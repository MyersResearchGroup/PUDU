import ast
import io
import json
import re

import pytest
from opentrons.simulate import simulate

from lablibre import FileRole, load_profile
from pudu.plans import PlatingProtocol
from pudu.plating import Plating

# Flow rates are left out on purpose: the port takes them from the profile's liquid classes.
_EVENT = re.compile(r"^(Aspirating|Dispensing) ([\d.]+) uL (?:from|into) ([A-P]\d+) of .* on slot (\d+)(?: at [\d.]+ uL/sec)?$")
_MIX = re.compile(r"^Mixing (\d+) times with a volume of ([\d.]+)")

# Deck slots of the original Plating protocol (its defaults) and what each holds.
_PUDU_ROLES = {"7": "culture", "4": "lb", "2": "dilution_1", "3": "dilution_2", "5": "agar_1", "6": "agar_2"}
_PORT_ROLES = {"culture_plate": "culture", "lb_tubes": "lb", "dilution_plate_1": "dilution_1",
               "dilution_plate_2": "dilution_2", "agar_plate_1": "agar_1", "agar_plate_2": "agar_2"}


def _events(log, roles, start, end):
    """Tip, aspirate, dispense and mix events between the *start* and *end* markers, with plate roles."""
    first = next(i for i, line in enumerate(log) if start in line)
    last = next(i for i, line in enumerate(log) if end in line)
    events = []
    for line in log[first:last]:
        line = line.strip()
        if line.startswith("Picking up tip"):
            events.append(("pick_up",))
        elif line.startswith("Dropping tip"):
            events.append(("drop",))
        elif match := _MIX.match(line):
            events.append(("mix", int(match.group(1)), float(match.group(2))))
        elif match := _EVENT.match(line):
            verb, volume, well, slot = match.groups()
            events.append((verb, float(volume), roles[slot], well))
    return events


def _run_log(source):
    runlog, _ = simulate(io.StringIO(source), file_name="protocol.py")
    return [entry["payload"]["text"] for entry in runlog]


def _pudu_events(plating_data, params):
    source = (f"from pudu.plating import Plating\n"
              f"metadata = {{'apiLevel': '2.21'}}\n"
              f"def run(protocol):\n"
              f"    Plating(plating_data={plating_data!r}, **{params!r}).run(protocol)\n")
    return _events(_run_log(source), _PUDU_ROLES, "=== Step 2", "=== Plating protocol complete")


def _port_bundle(plating_data, params):
    return PlatingProtocol(plating_data=plating_data, **params).compile(load_profile("ot2_default"))


def _port_events(bundle):
    code = bundle.get("plating.py").content
    roles = {slot: _PORT_ROLES[label]
             for slot, label in re.findall(r"load_labware\('[^']+', '(\d+)', label='(\w+)'\)", code)}
    roles["7"] = "culture"  # thermocycler
    return _events(_run_log(code), roles, "dilute and plate dilution 1", "Plated ")


CASES = {
    "3 constructs, 2 replicates, 2 dilutions": ({"A1": "GFP", "B1": "RFP", "C1": ["mCherry", "v2"]},
                                                {"replicates": 2}),
    "1 dilution, 3 replicates": ({"A1": "GFP", "D4": "RFP"}, {"number_dilutions": 1, "replicates": 3}),
    "agar split across two plates": ({f"{r}{c}": f"c{r}{c}" for c in (1, 2, 3, 4) for r in "ABCDEFG"[:(8 if c < 4 else 6)]},
                                     {"replicates": 2}),
}


@pytest.mark.parametrize("case", CASES)
def test_port_matches_pudu_plating_in_simulation(case, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the original Plating writes its layout files into the working directory
    plating_data, params = CASES[case]
    plating_data = {"bacterium_locations": plating_data}
    expected = _pudu_events(plating_data, params)
    actual = _port_events(_port_bundle(plating_data, params))
    assert {event[0] for event in expected} == {"pick_up", "drop", "mix", "Aspirating", "Dispensing"}
    assert actual == expected


def test_parity_check_detects_differences(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plating_data = {"bacterium_locations": {"A1": "GFP", "B1": "RFP"}}
    expected = _pudu_events(plating_data, {"replicates": 2})
    assert _port_events(_port_bundle(plating_data, {"replicates": 2, "volume_colony": 5})) != expected


def test_bundle_carries_agar_layout_matching_pudu():
    plating_data = {"bacterium_locations": {"A1": "GFP", "B1": "RFP"}}
    bundle = _port_bundle(plating_data, {"replicates": 2})
    assert [(f.path, f.role) for f in bundle.files] == [
        ("plating.py", FileRole.PROTOCOL), ("plating_setup.md", FileRole.SETUP), ("plating_plan.json", FileRole.DATA),
        ("plating_layout.json", FileRole.DATA), ("plating_layout.xlsx", FileRole.DATA),
    ]
    layout = json.loads(bundle.get("plating_layout.json").content)
    assert layout == Plating(plating_data=plating_data, replicates=2).get_plates_json()
    assert bundle.get("plating_layout.xlsx").content[:2] == b"PK"  # xlsx is a zip archive


def test_protocol_is_standalone():
    code = _port_bundle({"bacterium_locations": {"A1": "GFP"}}, {}).get("plating.py").content
    imported = {node.module if isinstance(node, ast.ImportFrom) else alias.name
                for node in ast.walk(ast.parse(code)) if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names}
    assert imported == {"opentrons"}


def test_one_tip_for_lb_and_two_per_construct():
    code = _port_bundle({"bacterium_locations": {"A1": "GFP", "B1": "RFP", "C1": "YFP"}}, {}).get("plating.py").content
    assert code.count(".pick_up_tip()") == 1 + 2 * 3


def test_agar_spots_dispense_above_the_agar():
    code = _port_bundle({"bacterium_locations": {"A1": "GFP"}}, {"replicates": 3}).get("plating.py").content
    spots = [line for line in code.splitlines() if "agar_plate_1[" in line]
    assert len(spots) == 6 and all("dispense_below_top=8" in line for line in spots)


def test_hardware_parameters_are_rejected_with_guidance():
    with pytest.raises(ValueError, match="robot profile"):
        PlatingProtocol(plating_data={"bacterium_locations": {"A1": "GFP"}}, small_pipette="p20_single_gen2")
    with pytest.raises(ValueError, match=r"\['agar_plate'\]"):
        PlatingProtocol(plating_data={"bacterium_locations": {"A1": "GFP"}}, json_params={"agar_plate": "x"})


def test_plating_validation_still_applies():
    with pytest.raises(ValueError, match="max of 2 dilutions"):
        PlatingProtocol(plating_data={"bacterium_locations": {"A1": "GFP"}}, number_dilutions=3)
    with pytest.raises(ValueError, match="Must input bacterium_locations"):
        PlatingProtocol()


def test_lb_is_multi_dispensed_with_the_large_pipette(tmp_path, monkeypatch):
    bundle = _port_bundle({"bacterium_locations": {"A1": "GFP", "B1": "RFP", "C1": "YFP"}}, {})
    log = _run_log(bundle.get("plating.py").content)
    first = next(i for i, line in enumerate(log) if "Distribute LB" in line)
    last = next(i for i, line in enumerate(log) if "dilute and plate dilution 1" in line)
    lb_step = log[first:last]
    assert [line.split(" at ")[0] for line in lb_step if line.startswith("Aspirating")] == [
        "Aspirating 112.0 uL from A1 of lb_tubes on slot 2"]  # 6 wells x 18 µL + 4 µL disposal
    assert sum("Filter Tip Rack 200" in line for line in lb_step if line.startswith("Picking up tip")) == 1
