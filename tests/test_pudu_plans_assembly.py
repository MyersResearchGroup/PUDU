import io
import json
import re

import pytest
from opentrons.simulate import simulate

from lablibre import FileRole, SetTemperature, ThermalProfile, load_profile
from lablibre.emitters import LabLibreWarning
from pudu.plans import AssemblyProtocol, PlatingProtocol, TransformationProtocol
from pudu.plans.assembly import detect_format

_EVENT = re.compile(r"^(Aspirating|Dispensing) ([\d.]+) uL (?:from|into) ([A-P]\d+) of .* on slot (\d+)(?: at [\d.]+ uL/sec)?$")
_ROLES = {"1": "tubes", "7": "plate"}  # temperature module and thermocycler, in both versions


def _uri(name):
    return f"https://sbolcanvas.org/{name}/1"


SBOL = [
    {"Product": "https://SBOL2Build.org/composite_1/1", "Backbone": _uri("pSB1C3"),
     "PartsList": [_uri("J23101"), _uri("B0034"), _uri("GFP"), _uri("B0015")],
     "Restriction Enzyme": "https://SBOL2Build.org/BsaI/1"},
    {"Product": "https://SBOL2Build.org/composite_2/1", "Backbone": _uri("pSB1C3"),
     "PartsList": [_uri("J23101"), _uri("B0034"), _uri("RFP"), _uri("B0015")],
     "Restriction Enzyme": "https://SBOL2Build.org/SapI/1"},
]
MANUAL = [
    {"promoter": ["GVP0008", "GVP0010"], "rbs": "B0034", "cds": "sfGFP", "terminator": "B0015", "receiver": "Odd_1"},
    {"promoter": "GVP0011", "cds": "mCherry", "receiver": "Even_2"},
]
DOMESTICATION = [{"parts": ["part1", "part2", "part3"], "backbone": "UA", "restriction_enzyme": "BsmBI"}]

CASES = {
    "SBOL, two enzymes, replicates": ("SBOLLoopAssembly", SBOL, {"replicates": 2}),
    "SBOL, later starting well": ("SBOLLoopAssembly", SBOL, {"thermocycler_starting_well": 5}),
    "Manual, odd and even receivers": ("ManualLoopAssembly", MANUAL, {}),
    "Domestication": ("Domestication", DOMESTICATION, {"replicates": 2}),
    "SBOL, custom volumes": ("SBOLLoopAssembly", SBOL[:1], {"volume_total_reaction": 30, "volume_part": 3}),
}


def _events(log):
    """Tips, aspirates and dispenses with plate roles.

    Mix markers are left out: the original writes its air-removal step as explicit
    aspirate/dispense pairs, the port as a mix; the pipetting underneath is the same.
    """
    events = []
    for line in log:
        line = line.strip()
        if line.startswith("Picking up tip"):
            events.append(("pick_up",))
        elif line.startswith("Dropping tip"):
            events.append(("drop",))
        elif match := _EVENT.match(line):
            verb, volume, well, slot = match.groups()
            events.append((verb, float(volume), _ROLES[slot], well))
    return events


def _run_log(source):
    runlog, _ = simulate(io.StringIO(source), file_name="protocol.py")
    return [entry["payload"]["text"] for entry in runlog]


def _pudu_log(cls, data, params):
    source = (f"from pudu.assembly import {cls}\n"
              f"metadata = {{'apiLevel': '2.21'}}\n"
              f"def run(protocol):\n"
              f"    {cls}(assembly_data={{'assemblies': {data!r}}}, **{params!r}).run(protocol)\n")
    return _run_log(source)


@pytest.mark.parametrize("case", CASES)
def test_port_matches_pudu_assembly_in_simulation(case, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the original writes transformation_input.json and an Excel file here
    cls, data, params = CASES[case]
    expected = _events(_pudu_log(cls, data, params))
    protocol = AssemblyProtocol(data, **params)
    if params.get("volume_total_reaction", 20) > 20:
        # The original quietly caps the air-removal mix at the p20's 20 µL; the port says so.
        with pytest.warns(LabLibreWarning, match="reduced from 30 µL to 20 µL"):
            bundle = protocol.compile(load_profile("ot2_default"))
    else:
        bundle = protocol.compile(load_profile("ot2_default"))
    assert {e[0] for e in expected} == {"pick_up", "drop", "Aspirating", "Dispensing"}
    assert _events(_run_log(bundle.get("assembly.py").content)) == expected
    # The product wells the port ships are exactly what the original wrote in simulation.
    assert json.loads(bundle.get("transformation_input.json").content) == \
        json.loads((tmp_path / "transformation_input.json").read_text())


def test_parity_check_detects_differences(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    expected = _events(_pudu_log("SBOLLoopAssembly", SBOL, {}))
    bundle = AssemblyProtocol(SBOL, volume_t4_dna_ligase=3).compile(load_profile("ot2_default"))
    assert _events(_run_log(bundle.get("assembly.py").content)) != expected


def test_format_detection_and_override():
    assert [detect_format(a[0]) for a in (SBOL, MANUAL, DOMESTICATION)] == ["SBOL", "Manual", "Domestication"]
    assert AssemblyProtocol({"assemblies": MANUAL}).assembly_type == "Manual"
    with pytest.raises(ValueError, match="Can't tell the assembly format"):
        AssemblyProtocol([{"unknown": "x"}])
    with pytest.raises(ValueError, match="Unknown assembly_type"):
        AssemblyProtocol(SBOL, assembly_type="Gibson")


def test_bundle_contents_and_cycling():
    protocol = AssemblyProtocol(SBOL)
    bundle = protocol.compile(load_profile("ot2_default"))
    assert [(f.path, f.role) for f in bundle.files] == [
        ("assembly.py", FileRole.PROTOCOL), ("assembly_setup.md", FileRole.SETUP), ("assembly_plan.json", FileRole.DATA),
        ("transformation_input.json", FileRole.DATA), ("Loop Assembly.xlsx", FileRole.DATA),
    ]
    assert bundle.warnings == []
    assert bundle.get("Loop Assembly.xlsx").content[:2] == b"PK"
    cycling, inactivation = [s for s in protocol.build_plan().steps if isinstance(s, ThermalProfile)]
    assert (cycling.repetitions, [(s.celsius, s.seconds) for s in cycling.steps], cycling.lid_celsius) == \
        (75, [(42, 120), (16, 300)], 42)
    assert [(s.celsius, s.seconds) for s in inactivation.steps] == [(60, 600), (80, 600)]
    assert inactivation.hold_celsius == 4


def test_enzymes_use_the_glycerol_liquid_class():
    code = AssemblyProtocol(SBOL[:1]).compile(load_profile("ot2_default")).get("assembly.py").content
    ligase_line = next(line for line in code.splitlines() if "_move(" in line and ", 4, " in line)
    assert "**HANDLING['glycerol']" in ligase_line


def test_water_testing_and_no_xlsx():
    protocol = AssemblyProtocol(SBOL, water_testing=True, output_xlsx=False)
    assert not [s for s in protocol.build_plan().steps if isinstance(s, (ThermalProfile, SetTemperature))]
    assert not [f for f in protocol.compile(load_profile("ot2_default")).files if f.path.endswith(".xlsx")]


def test_full_chain_assembly_transformation_plating():
    assembly = AssemblyProtocol(SBOL, replicates=2)
    transformation = TransformationProtocol(
        transformation_data=[{"Strain": f"https://sbol.org/strain_{i}/1", "Chassis": "https://sbol.org/DH5alpha/1",
                              "Plasmids": [a["Product"]]} for i, a in enumerate(SBOL)],
        plasmid_locations=assembly.transformation_input(), replicates=1)
    plating = PlatingProtocol(plating_data=transformation.plating_input())
    for profile in ("ot2_default", "flex_default"):
        for protocol in (assembly, transformation, plating):
            bundle = protocol.compile(load_profile(profile))
            [code] = bundle.by_role(FileRole.PROTOCOL)
            _run_log(code.content)  # runs in the simulator without errors
    assert plating.params.number_constructs == 4  # 2 products x 2 assembly replicates


@pytest.mark.parametrize("profile", ["bench_default", "hamilton_star_default"])
def test_other_targets_compile(profile, recwarn):
    bundle = AssemblyProtocol(MANUAL).compile(load_profile(profile))
    assert bundle.get("transformation_input.json") is not None
    if profile == "bench_default":
        text = bundle.get("assembly_bench_protocol.md").content
        assert "Viscous glycerol stock" in text and "Golden Gate cycling" in text


def test_hardware_and_camera_parameters_are_rejected():
    with pytest.raises(ValueError, match="robot profile"):
        AssemblyProtocol(SBOL, pipette="p20_single_gen2")
    with pytest.raises(ValueError, match=r"\['take_picture'\]"):
        AssemblyProtocol(SBOL, json_params={"take_picture": True})


def test_original_validation_still_applies():
    with pytest.raises(ValueError, match="Reaction volume error"):
        AssemblyProtocol(SBOL, volume_part=5)
    with pytest.raises(ValueError, match="does not match naming convention"):
        AssemblyProtocol([{"promoter": "p1", "receiver": "Other_1"}])
