import ast
import io

import pytest
from opentrons.simulate import simulate

from lablibre import (OFF_DECK, FileRole, Liquid, LiquidClass, Mix, MoveLabware, Pause, PipetteSpec, Plan,
                      RobotProfile, SetLid, SetTemperature, ThermalProfile, ThermalStep, TipPolicy, Transfer, TransferSet,
                      compile_plan, load_profile, transfers)
from lablibre.emitters import EmitError, LabLibreWarning, OT2Emitter


def _plating_plan():
    plan = Plan("GFP Plating", description="Distribute LB, dilute, spot, incubate")
    plan.add_module("cold_block", "temperature")
    plan.add_module("thermocycler", "thermocycler")
    reagents = plan.add_labware("reagents", "tube_rack_24", module="cold_block")
    source = plan.add_labware("source_plate", "plate_96", module="thermocycler", max_volume_ul=100)
    tubes = plan.add_labware("tubes", "tube_rack_15")
    dilution = plan.add_labware("dilution_plate", "plate_96", max_volume_ul=100)
    agar = plan.add_labware("agar_plate", "plate_96")

    plan.load(tubes["A1"], Liquid("LB"), 10000)
    plan.load(reagents["A1"], Liquid("ligase", LiquidClass.GLYCEROL), 100)
    for i in range(3):
        plan.load(source.wells()[i], Liquid(f"construct_{i}", LiquidClass.CELLS), 20)

    wells = dilution.wells()[:3]
    plan.add(SetLid("thermocycler", open=True))
    plan.add(SetTemperature("cold_block", 4))
    plan.add(TransferSet(transfers([(tubes["A1"], w, 18) for w in wells]), tips=TipPolicy.ONE, label="Distribute LB"))
    plan.add(TransferSet(transfers([(source.wells()[i], wells[i], 2) for i in range(3)], mix_after=Mix(10)),
                         liquid_class=LiquidClass.CELLS, label="Dilute 1:10"))
    plan.add(TransferSet(transfers([(reagents["A1"], source.wells()[5], 2)]), liquid_class="glycerol",
                         touch_tip=True, label="Ligase"))
    agar_wells = iter(agar.wells())
    plan.add(TransferSet(transfers([(w, next(agar_wells), 4) for w in wells for _ in range(2)]),
                         liquid_class=LiquidClass.CELLS, tips=TipPolicy.PER_SOURCE, label="Spot"))
    plan.add(SetLid("thermocycler", open=False))
    plan.add(ThermalProfile("thermocycler", [ThermalStep(42, 120), ThermalStep(16, 300)], repetitions=2,
                            lid_celsius=42, block_max_volume=30, hold_celsius=4, label="Cycling"))
    plan.add(SetTemperature("cold_block", None))
    plan.add(Pause("Remove plates"))
    plan.add(MoveLabware("agar_plate", OFF_DECK))
    return plan


def _protocol(bundle):
    [protocol] = bundle.by_role(FileRole.PROTOCOL)
    return protocol


def _simulate(bundle):
    protocol = _protocol(bundle)
    runlog, _ = simulate(io.StringIO(protocol.content), file_name=protocol.path)
    return [entry["payload"]["text"] for entry in runlog]


def _profile(**changes):
    base = load_profile("ot2_default")
    data = {"robot": base.robot, "name": base.name, "pipettes": list(base.pipettes), "modules": list(base.modules),
            "labware": dict(base.labware), "deck_slots": base.deck_slots, "liquid_classes": dict(base.liquid_classes),
            "options": dict(base.options)}
    data.update(changes)
    return RobotProfile(**data)


def test_bundle_contains_protocol_setup_sheet_and_plan():
    bundle = compile_plan(_plating_plan(), load_profile("ot2_default"))
    assert [(f.path, f.role) for f in bundle.files] == [
        ("gfp_plating.py", FileRole.PROTOCOL),
        ("gfp_plating_setup.md", FileRole.SETUP),
        ("gfp_plating_plan.json", FileRole.DATA),
    ]


def test_protocol_imports_only_opentrons():
    tree = ast.parse(_protocol(compile_plan(_plating_plan(), load_profile("ot2_default"))).content)
    imported = {node.module if isinstance(node, ast.ImportFrom) else alias.name
                for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names}
    assert imported == {"opentrons"}


def test_plating_protocol_simulates():
    log = _simulate(compile_plan(_plating_plan(), load_profile("ot2_default")))
    assert log[0] == "Step 1: Open thermocycler lid"
    assert sum("Picking up tip" in line for line in log) == 1 + 3 + 1 + 3
    assert any("Aspirating 18.0 uL from A1 of tubes on slot 2" in line for line in log)
    assert any("Mixing 3 times with a volume of 10.0" in line for line in log)
    assert any("Touching tip" in line for line in log)
    assert "Pausing robot operation: Remove plates" in log


def test_labware_on_modules_uses_module_specific_definitions():
    code = _protocol(compile_plan(_plating_plan(), load_profile("ot2_default"))).content
    assert "cold_block.load_labware('opentrons_24_aluminumblock_nest_1.5ml_snapcap', label='reagents')" in code
    assert "protocol.load_labware('opentrons_15_tuberack_falcon_15ml_conical', '2', label='tubes')" in code


def test_eight_channel_pipette_fills_a_plate_by_column():
    plan = Plan("Fill plate")
    reservoir = plan.add_labware("reservoir", "reservoir_12")
    plate = plan.add_labware("plate", "plate_96", max_volume_ul=200)
    plan.load(reservoir["A1"], Liquid("media"), 15000)
    plan.add(TransferSet(transfers([(reservoir["A1"], w, 100) for w in plate.wells()]), tips=TipPolicy.ONE))
    multi = PipetteSpec("p300_multi_gen2", channels=8, min_volume=20, max_volume=300, mount="right",
                        tip_rack="opentrons_96_tiprack_300ul")
    bundle = compile_plan(plan, _profile(pipettes=[multi], modules=[]))
    code = _protocol(bundle).content
    assert code.count("_move(p300_multi_gen2, reservoir['A1'], plate[") == 12
    assert "plate['A12'], 100" in code
    log = _simulate(bundle)
    assert sum("Dispensing 100.0 uL" in line for line in log) == 12


def test_missing_modules_become_manual_pauses():
    bundle = compile_plan(_plating_plan(), _profile(modules=[]))
    log = _simulate(bundle)
    assert any("Set cold_block to 4 °C by hand" in line for line in log)
    assert any("Run on an external thermocycler: 2 x (42 °C for 120 s, 16 °C for 300 s)" in line for line in log)
    setup = bundle.by_role(FileRole.SETUP)[0].content
    assert "## Manual steps" in setup and "`thermocycler`" in setup


def test_setup_sheet_lists_deck_liquids_and_steps():
    setup = compile_plan(_plating_plan(), load_profile("ot2_default")).by_role(FileRole.SETUP)[0].content
    assert "| 7, 8, 10, 11 | thermocycler module (`thermocycler`) holding `source_plate`" in setup
    assert "| left | p20_single_gen2 | 8 |" in setup
    assert "| `tubes` | A1 | LB | 10000 |" in setup
    assert "3. Distribute LB: 3 transfers in 3 passes, 1 tip" in setup
    assert "5. Ligase: 1 transfer in 1 pass, 1 tip" in setup


def test_location_overrides_are_respected():
    code = _protocol(compile_plan(_plating_plan(), _profile(options={"locations": {"agar_plate": "9"}}))).content
    assert "protocol.load_labware('nest_96_wellplate_100ul_pcr_full_skirt', '9', label='agar_plate')" in code


def test_many_tips_load_extra_racks():
    plan = Plan("Many tips")
    tubes = plan.add_labware("tubes", "tube_rack_15")
    plates = [plan.add_labware(f"plate{i}", "plate_96") for i in range(2)]
    plan.load(tubes["A1"], Liquid("water"), 15000)
    plan.add(TransferSet(transfers([(tubes["A1"], w, 5) for p in plates for w in p.wells()])))
    code = _protocol(compile_plan(plan, load_profile("ot2_default"))).content
    assert "tip_racks=[tips_p20_single_gen2_1, tips_p20_single_gen2_2]" in code


@pytest.mark.parametrize("changes, message", [
    ({"robot": "flex"}, "targets"),
    ({"deck_slots": ("2",)}, "Out of deck slots"),
    ({"liquid_classes": {"aqueous": {"flow": 1}}}, "doesn't understand"),
    ({"pipettes": [PipetteSpec("p20_single_gen2", 1, 1, 20, mount="left")]}, "no tip_rack"),
    ({"pipettes": [PipetteSpec("p20_single_gen2", 1, 1, 20, mount="left", tip_rack="t"),
                   PipetteSpec("p300_single_gen2", 1, 20, 200, mount="left", tip_rack="t")]}, "both on the left"),
    ({"pipettes": [PipetteSpec("star", 8, 0.5, 1000, mount="left", pitch="independent", per_channel_volume=True,
                               tip_rack="t")]}, "independent channels"),
])
def test_unsupported_profiles_raise(changes, message):
    with pytest.raises(EmitError, match=message):
        OT2Emitter().compile(_plating_plan(), _profile(**changes))


def _lb_plan(wells=6, **kwargs):
    plan = Plan("LB")
    tubes = plan.add_labware("tubes", "tube_rack_15")
    plate = plan.add_labware("plate", "plate_96", max_volume_ul=100)
    plan.load(tubes["A1"], Liquid("LB"), 10000)
    plan.add(TransferSet(transfers([(tubes["A1"], w, 18) for w in plate.wells()[:wells]]), tips=TipPolicy.ONE,
                         multi_dispense=True, disposal_volume=4, **kwargs))
    return plan


def test_multi_dispense_simulates_with_disposal_to_trash():
    bundle = compile_plan(_lb_plan(), load_profile("ot2_default"))
    code = _protocol(bundle).content
    assert "def _distribute(" in code and "def _move(" not in code  # only helpers that are used
    log = _simulate(bundle)
    assert sum(line.startswith("Aspirating") for line in log) == 1
    assert "Aspirating 112.0 uL from A1 of tubes on slot 2" in " ".join(log)
    assert sum(line.startswith("Dispensing 18.0 uL") for line in log) == 6
    assert any(line.startswith("Blowing out into Trash Bin") for line in log)
    assert "1. Transfer (6 transfers): 6 transfers in 1 pass, 1 tip" in bundle.by_role(FileRole.SETUP)[0].content


def test_multi_dispense_with_touch_tip_and_dispense_height_simulates():
    plan = Plan("Spots")
    tubes = plan.add_labware("tubes", "tube_rack_15")
    agar = plan.add_labware("agar", "plate_96")
    plan.load(tubes["A1"], Liquid("cells", LiquidClass.CELLS), 1000)
    plan.add(TransferSet([Transfer(tubes["A1"], w, 4, dispense_below_top=8) for w in agar.wells()[:3]],
                         tips=TipPolicy.ONE, multi_dispense=True, touch_tip=True))
    bundle = compile_plan(plan, load_profile("ot2_default"))
    assert "(agar['A1'].top(-8), 4)," in _protocol(bundle).content
    log = _simulate(bundle)
    assert sum(line.startswith("Touching tip") for line in log) == 3


def test_multi_dispense_eight_channel_simulates():
    plan = Plan("Fill plate")
    reservoir = plan.add_labware("reservoir", "reservoir_12")
    plate = plan.add_labware("plate", "plate_96", max_volume_ul=200)
    plan.load(reservoir["A1"], Liquid("media"), 15000)
    plan.add(TransferSet(transfers([(reservoir["A1"], w, 50) for w in plate.wells()]), tips=TipPolicy.ONE,
                         multi_dispense=True, disposal_volume=10))
    multi = PipetteSpec("p300_multi_gen2", channels=8, min_volume=20, max_volume=300, mount="right",
                        tip_rack="opentrons_96_tiprack_300ul")
    log = _simulate(compile_plan(plan, _profile(pipettes=[multi], modules=[])))
    aspirations = [line for line in log if line.startswith("Aspirating")]
    assert len(aspirations) == 3 and all("260.0 uL" in line for line in aspirations[:2])
    assert sum(line.startswith("Dispensing 50.0 uL") for line in log) == 12


def test_reduced_mixes_are_flagged_in_code_setup_sheet_and_terminal():
    plan = Plan("Big mix")
    plate = plan.add_labware("plate", "plate_96", max_volume_ul=200)
    plan.load(plate["A1"], Liquid("water"), 150)
    plan.load(plate["B1"], Liquid("buffer"), 90)
    plan.add(TransferSet(transfers([(plate["A1"], plate["B1"], 10)], mix_after=Mix(50)), label="Mix hard"))
    with pytest.warns(LabLibreWarning, match="reduced from 50 µL to 20 µL") as caught:
        bundle = compile_plan(plan, _profile(modules=[]))
    assert len(caught) == 1
    code = _protocol(bundle).content
    assert "    # Warning: Mix after at plate:B1 reduced from 50 µL to 20 µL: p20_single_gen2 holds at most 20 µL" in code
    setup = bundle.by_role(FileRole.SETUP)[0].content
    assert "## Warnings" in setup and "- Step 1: Mix after at plate:B1 reduced from 50 µL to 20 µL" in setup
    _simulate(bundle)


def test_plan_validation_warnings_reach_the_bundle():
    plan = Plan("Small well")
    plate = plan.add_labware("plate", "plate_96")
    plan.load(plate["A1"], Liquid("water"), 5)
    plan.add(TransferSet(transfers([(plate["A1"], plate["B1"], 2)], mix_before=Mix(10))))
    with pytest.warns(LabLibreWarning, match="mix_before of 10 µL at plate:A1 exceeds its 5 µL"):
        bundle = compile_plan(plan, load_profile("ot2_default"))
    assert bundle.warnings == ["Step 1: mix_before of 10 µL at plate:A1 exceeds its 5 µL"]


def test_clean_plans_have_no_warnings(recwarn):
    bundle = compile_plan(_plating_plan(), load_profile("ot2_default"))
    assert bundle.warnings == [] and not [w for w in recwarn if issubclass(w.category, LabLibreWarning)]
    assert "## Warnings" not in bundle.by_role(FileRole.SETUP)[0].content
