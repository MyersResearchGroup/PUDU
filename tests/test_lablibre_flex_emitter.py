import io
import json
import re

import pytest
from opentrons.simulate import simulate

from lablibre import (OFF_DECK, FileRole, Liquid, MoveLabware, PipetteSpec, Plan, RobotProfile, SetLid, TransferSet,
                      compile_plan, load_profile, transfers)
from lablibre.emitters import EmitError, FlexEmitter, OT2Emitter
from pudu.plans import PlatingProtocol, TransformationProtocol

_EVENT = re.compile(r"^(Aspirating|Dispensing) ([\d.]+) uL (?:from|into) ([A-P]\d+) of (\w+) on")


def _uri(name):
    return f"https://sbol.org/{name}/1"


STRAINS = [
    {"Strain": _uri("strain_GFP"), "Chassis": _uri("DH5alpha"), "Plasmids": [_uri("composite_1"), _uri("part_2")]},
    {"Strain": _uri("strain_RFP"), "Chassis": _uri("DH5alpha"), "Plasmids": [_uri("composite_2")]},
]


def _code(bundle):
    [protocol] = bundle.by_role(FileRole.PROTOCOL)
    return protocol.content


def _log(bundle):
    runlog, _ = simulate(io.StringIO(_code(bundle)), file_name="protocol.py")
    return [entry["payload"]["text"] for entry in runlog]


def _events(log):
    """Tips, aspirates and dispenses by labware label, so OT-2 and Flex runs can be compared."""
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
            verb, volume, well, labware = match.groups()
            events.append((verb, float(volume), labware, well))
    return events


def _eight_channel_profile(**options):
    base = load_profile("flex_default")
    multi = PipetteSpec("flex_8channel_1000", 8, 5, 200, mount="right",
                        tip_rack="opentrons_flex_96_filtertiprack_200ul", partial_pickup=True)
    return RobotProfile("flex", "Flex 8-channel", [multi], labware=dict(base.labware), deck_slots=("B2", "C2", "B3"),
                        has_gripper=True, liquid_classes=dict(base.liquid_classes),
                        options={"api_level": "2.21", "trash": "D3", **options})


@pytest.mark.parametrize("protocol", ["transformation", "plating"])
def test_flex_does_the_same_pipetting_as_the_ot2(protocol):
    transformation = TransformationProtocol(transformation_data=STRAINS)
    if protocol == "transformation":
        plan = transformation.build_plan()
    else:
        plan = PlatingProtocol(plating_data=transformation.plating_input(), replicates=2).build_plan()
    ot2 = _events(_log(compile_plan(plan, load_profile("ot2_default"))))
    flex = _events(_log(compile_plan(plan, load_profile("flex_default"))))
    assert {e[0] for e in flex} >= {"pick_up", "drop", "Aspirating", "Dispensing"}
    assert flex == ot2


def test_flex_protocol_loads_trash_modules_and_flex_hardware():
    bundle = compile_plan(TransformationProtocol(transformation_data=STRAINS).build_plan(), load_profile("flex_default"))
    code = _code(bundle)
    assert "requirements = {'robotType': 'Flex', 'apiLevel': '2.21'}" in code
    assert "trash = protocol.load_trash_bin('A3')" in code
    assert "thermocycler = protocol.load_module('thermocycler module gen2')" in code
    assert "cold_block = protocol.load_module('temperature module gen2', 'D1')" in code
    assert "protocol.load_instrument('flex_1channel_50', 'left'" in code
    assert "], trash, mix_before=(50, 3)" in code  # multi-dispense blows out into the loaded trash
    setup = bundle.by_role(FileRole.SETUP)[0].content
    assert "| A1, B1 | thermocycler module gen2 (`thermocycler`)" in setup and "| A3 | Trash bin |" in setup


def test_gripper_moves_labware_on_deck_but_not_off_it():
    plan = Plan("Moves")
    plan.add_module("tc", "thermocycler")
    plate = plan.add_labware("plate", "plate_96")
    plan.load(plate["A1"], Liquid("water"), 50)
    plan.add(TransferSet(transfers([(plate["A1"], plate["B1"], 10)])))
    plan.add(SetLid("tc", open=True))  # Opentrons won't move labware onto a closed thermocycler
    plan.add(MoveLabware("plate", "tc"))
    plan.add(MoveLabware("plate", OFF_DECK))
    bundle = compile_plan(plan, load_profile("flex_default"))
    code = _code(bundle)
    assert "protocol.move_labware(plate, tc, use_gripper=True)" in code
    assert "protocol.move_labware(plate, protocol_api.OFF_DECK, use_gripper=False)" in code
    log = _log(bundle)
    assert any("with gripper" in line for line in log)


def test_eight_channel_switches_to_one_nozzle_for_single_wells():
    plan = Plan("Partial")
    reservoir = plan.add_labware("reservoir", "reservoir_12")
    plate = plan.add_labware("plate", "plate_96", max_volume_ul=200)
    plan.load(reservoir["A1"], Liquid("media"), 15000)
    plan.add(TransferSet(transfers([(reservoir["A1"], w, 100) for w in plate.columns()[0]]), label="Column"))
    plan.add(TransferSet(transfers([(reservoir["A1"], plate["A2"], 50), (reservoir["A1"], plate["B2"], 50)])))
    plan.add(TransferSet(transfers([(reservoir["A1"], w, 100) for w in plate.columns()[2]]), label="Column again"))
    bundle = compile_plan(plan, _eight_channel_profile())
    code = _code(bundle)
    assert "from opentrons.protocol_api import ALL, SINGLE" in code
    assert code.count("configure_nozzle_layout(style=SINGLE, start='H1'") == 1
    assert code.count("configure_nozzle_layout(style=ALL") == 1
    pickups = [line for line in _log(bundle) if line.startswith("Picking up tip")]
    assert [line.split(" of ")[0] for line in pickups] == ["Picking up tip from A1", "Picking up tip from A2",
                                                           "Picking up tip from B2", "Picking up tip from A3"]


def test_partial_pickup_nozzle_is_configurable():
    plan = Plan("One well")
    plate = plan.add_labware("plate", "plate_96")
    plan.load(plate["A1"], Liquid("water"), 100)
    plan.add(TransferSet(transfers([(plate["A1"], plate["B1"], 10)])))
    code = _code(compile_plan(plan, _eight_channel_profile(partial_nozzle="A1")))
    assert "configure_nozzle_layout(style=SINGLE, start='A1'" in code


def test_ot2_still_rejects_partial_pickup():
    plan = Plan("One well")
    plate = plan.add_labware("plate", "plate_96")
    plan.load(plate["A1"], Liquid("water"), 100)
    plan.add(TransferSet(transfers([(plate["A1"], plate["B1"], 50)])))
    base = load_profile("ot2_default")
    multi = PipetteSpec("p300_multi_gen2", 8, 20, 300, mount="right", tip_rack="opentrons_96_tiprack_300ul",
                        partial_pickup=True)
    profile = RobotProfile("ot2", "OT-2 multi", [multi], labware=dict(base.labware), deck_slots=base.deck_slots)
    with pytest.raises(EmitError, match="picking up 1 of 8 tips .* not supported on the OT-2"):
        OT2Emitter().compile(plan, profile)


def test_flex_rejects_ot2_profiles_and_bad_mounts():
    plan = PlatingProtocol(plating_data={"bacterium_locations": {"A1": "GFP"}}).build_plan()
    with pytest.raises(EmitError, match="targets"):
        FlexEmitter().compile(plan, load_profile("ot2_default"))
    base = load_profile("flex_default")
    bad = RobotProfile("flex", "bad", [PipetteSpec("flex_1channel_50", 1, 1, 50, tip_rack="t")],
                       labware=dict(base.labware), deck_slots=base.deck_slots)
    with pytest.raises(EmitError, match="mount 'left' or 'right' on the Flex"):
        FlexEmitter().compile(plan, bad)


def test_bundle_chaining_output_is_unchanged():
    transformation = TransformationProtocol(transformation_data=STRAINS)
    flex = transformation.compile(load_profile("flex_default"))
    ot2 = transformation.compile(load_profile("ot2_default"))
    assert json.loads(flex.get("plating_input.json").content) == json.loads(ot2.get("plating_input.json").content)
