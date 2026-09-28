import io
import json
import re

import pytest
from opentrons.simulate import simulate

from lablibre import FileRole, SetTemperature, ThermalProfile, load_profile
from lablibre.emitters import LabLibreWarning
from pudu.plans import PlatingProtocol, TransformationProtocol

_EVENT = re.compile(r"^(Aspirating|Dispensing) ([\d.]+) uL (?:from|into) ([A-P]\d+) of .* on slot (\d+)(?: at [\d.]+ uL/sec)?$")

# Deck slots of the original protocol (its defaults) and what each holds.
_PUDU_ROLES = {"1": "dna", "2": "dna", "3": "reagents", "7": "plate"}
_PORT_ROLES = {"dna_plate": "dna", "reagent_tubes": "reagents"}


def _uri(name):
    return f"https://sbol.org/{name}/1"


def _strain(name, chassis, *plasmids):
    return {"Strain": _uri(name), "Chassis": _uri(chassis), "Plasmids": [_uri(p) for p in plasmids]}


def _events(log, roles):
    """Tips, aspirates, dispenses and air gaps, with plate roles.

    Mix markers are left out: the original's "remove air" step is written as explicit
    aspirate/dispense pairs, the port as a mix; the pipetting underneath is the same.
    """
    events = []
    for line in log:
        line = line.strip()
        if line.startswith("Picking up tip"):
            events.append(("pick_up",))
        elif line.startswith("Dropping tip"):
            events.append(("drop",))
        elif line.startswith("Air gap"):
            events.append(("air_gap",))
        elif match := _EVENT.match(line):
            verb, volume, well, slot = match.groups()
            events.append((verb, float(volume), roles[slot], well))
    return events


def _run_log(source):
    runlog, _ = simulate(io.StringIO(source), file_name="protocol.py")
    return [entry["payload"]["text"] for entry in runlog]


def _pudu_log(data, locations, params):
    source = (f"from pudu.transformation import HeatShockTransformation\n"
              f"metadata = {{'apiLevel': '2.21'}}\n"
              f"def run(protocol):\n"
              f"    HeatShockTransformation(transformation_data={data!r}, plasmid_locations={locations!r},\n"
              f"                            **{params!r}).run(protocol)\n")
    return _run_log(source)


def _port_bundle(data, locations=None, params=None, profile="ot2_default"):
    return TransformationProtocol(transformation_data=data, plasmid_locations=locations,
                                  **(params or {})).compile(load_profile(profile))


def _port_events(bundle):
    code = bundle.get("transformation.py").content
    roles = {"1": "dna", "7": "plate"}  # cold block and thermocycler
    roles.update({slot: _PORT_ROLES[label]
                  for slot, label in re.findall(r"load_labware\('[^']+', '(\d+)', label='(\w+)'\)", code)})
    return _events(_run_log(code), roles)


CASES = {
    "two strains, shared chassis": ([_strain("strain_A", "DH5alpha", "pA", "pB"),
                                     _strain("strain_B", "DH5alpha", "pC")], None, {}),
    "two chassis, several cell tubes": ([_strain(f"s{i}", "DH5alpha" if i < 3 else "BL21", f"p{i}") for i in range(5)],
                                        None, {"replicates": 3}),
    "DNA from the assembly plate": ([_strain("strain_A", "DH5alpha", "pA", "pB"), _strain("strain_B", "DH5alpha", "pC")],
                                    {_uri("pA"): ["A1", "B1"], _uri("pB"): ["C1", "D1"], _uri("pC"): ["E1", "F1"]},
                                    {"replicates": 1}),
    "several media tubes": ([_strain(f"s{i}", "DH5alpha", f"p{i}") for i in range(12)], None, {}),
}


@pytest.mark.parametrize("case", CASES)
def test_port_matches_pudu_transformation_in_simulation(case, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the original writes plating_input.json into the working directory
    data, locations, params = CASES[case]
    expected = _events(_pudu_log(data, locations, params), _PUDU_ROLES)
    bundle = _port_bundle(data, locations, params)
    assert {e[0] for e in expected} == {"pick_up", "drop", "air_gap", "Aspirating", "Dispensing"}
    assert _port_events(bundle) == expected
    # The plating input the port ships is exactly what the original wrote in simulation.
    assert json.loads(bundle.get("plating_input.json").content) == json.loads((tmp_path / "plating_input.json").read_text())


def test_parity_check_detects_differences(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data, locations, params = CASES["two strains, shared chassis"]
    expected = _events(_pudu_log(data, locations, params), _PUDU_ROLES)
    assert _port_events(_port_bundle(data, params={"transfer_volume_dna": 3})) != expected


def test_bundle_contents_and_no_warnings(recwarn):
    bundle = _port_bundle(*CASES["two strains, shared chassis"][:2])
    assert [(f.path, f.role) for f in bundle.files] == [
        ("transformation.py", FileRole.PROTOCOL), ("transformation_setup.md", FileRole.SETUP),
        ("transformation_plan.json", FileRole.DATA), ("plating_input.json", FileRole.DATA),
    ]
    assert bundle.warnings == [] and not [w for w in recwarn if issubclass(w.category, LabLibreWarning)]


def test_heat_shock_and_recovery_profiles():
    plan = TransformationProtocol(transformation_data=CASES["two strains, shared chassis"][0]).build_plan()
    heat, recovery = [s for s in plan.steps if isinstance(s, ThermalProfile)]
    assert [(s.celsius, s.seconds) for s in heat.steps] == [(4, 1800), (42, 60), (4, 120)]
    assert [(s.celsius, s.seconds) for s in recovery.steps] == [(37, 3600)]
    assert heat.block_max_volume == 24 and recovery.block_max_volume == 84  # 20 cells + 2 x 2 DNA (+ 60 media)


def test_custom_incubations_and_water_testing():
    data = CASES["two strains, shared chassis"][0]
    plan = TransformationProtocol(transformation_data=data, heat_shock={"temperature": 42, "hold_time_minutes": 0.75}
                                  ).build_plan()
    heat = next(s for s in plan.steps if isinstance(s, ThermalProfile))
    assert heat.steps[1].seconds == 45
    dry = TransformationProtocol(transformation_data=data, water_testing=True).build_plan()
    assert not [s for s in dry.steps if isinstance(s, (ThermalProfile, SetTemperature))]


def test_transformation_output_feeds_plating():
    bundle = _port_bundle(*CASES["two strains, shared chassis"][:2])
    plating = PlatingProtocol(plating_data=json.loads(bundle.get("plating_input.json").content))
    assert plating.params.number_constructs == 4
    assert plating.compile(load_profile("ot2_default")).get("plating.py")


def test_bench_protocol():
    bundle = _port_bundle(*CASES["two strains, shared chassis"][:2], profile="bench_default")
    text = bundle.get("transformation_bench_protocol.md").content
    assert "mix 3 × 50 µL before aspirating; aspirate 80 µL once, then dispense into each well" in text
    assert "dispense 2 mm above the rim" in text
    assert "| P20 | `dna_tubes` A1 | `transformation_plate` A1 | 2 | mix 3 × 2 µL before; mix 2 × 20 µL after;" in text
    assert "Put `transformation_plate` in the thermocycler (`thermocycler`), close the lid and run this program:" in text


def test_hardware_parameters_are_rejected_with_guidance():
    data = CASES["two strains, shared chassis"][0]
    with pytest.raises(ValueError, match="robot profile"):
        TransformationProtocol(transformation_data=data, pipette_p20="p20_single_gen2")
    with pytest.raises(ValueError, match=r"\['tube_rack_position'\]"):
        TransformationProtocol(transformation_data=data, json_params={"tube_rack_position": "4"})


def test_original_validation_still_applies():
    with pytest.raises(ValueError, match="missing 'Chassis'"):
        TransformationProtocol(transformation_data=[{"Strain": _uri("s"), "Plasmids": [_uri("p")]}])
    too_many = [_strain(f"s{i}", f"chassis{i}", f"p{i}") for i in range(25)]
    with pytest.raises(ValueError, match="tube rack capacity"):
        TransformationProtocol(transformation_data=too_many)
