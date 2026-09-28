import pytest

from lablibre import (OFF_DECK, Comment, Delay, FileRole, Liquid, LiquidClass, Mix, MoveLabware, Pause,
                      PipetteSpec, Plan, RobotProfile, SetLid, SetTemperature, ThermalProfile, ThermalStep,
                      TipPolicy, TransferSet, compile_plan, load_profile, transfers)
from lablibre.emitters import EmitError, LabLibreWarning, ManualEmitter
from lablibre.emitters.manual import _duration
from pudu.plans import PlatingProtocol


def _document(plan, profile=None):
    bundle = ManualEmitter().compile(plan, profile or load_profile("bench_default"))
    [protocol] = bundle.by_role(FileRole.PROTOCOL)
    return protocol.content


def _bench(*pipettes):
    return RobotProfile("manual", "Test bench", pipettes, labware={"plate_96": "96-well plate"})


def _assembly_like_plan():
    plan = Plan("Assembly", description="Golden Gate test")
    plan.add_module("cold", "temperature")
    plan.add_module("tc", "thermocycler")
    tubes = plan.add_labware("reagents", "tube_rack_24", module="cold")
    plate = plan.add_labware("reactions", "plate_96", module="tc")
    plan.load(tubes["A1"], Liquid("water"), 1000)
    plan.load(tubes["A2"], Liquid("T4 ligase", LiquidClass.GLYCEROL), 50)
    plan.add(SetTemperature("cold", 4))
    plan.add(SetLid("tc", open=True))
    plan.add(TransferSet(transfers([(tubes["A1"], plate["A1"], 12), (tubes["A1"], plate["B1"], 12)]),
                         tips=TipPolicy.ONE, multi_dispense=True, label="Water"))
    plan.add(TransferSet(transfers([(tubes["A2"], plate["A1"], 1), (tubes["A2"], plate["B1"], 1)], mix_after=Mix(8)),
                         liquid_class=LiquidClass.GLYCEROL, label="Ligase"))
    plan.add(SetLid("tc", open=False))
    plan.add(ThermalProfile("tc", [ThermalStep(42, 120), ThermalStep(16, 300)], repetitions=75, lid_celsius=42,
                            hold_celsius=4, label="Golden Gate cycling"))
    plan.add(SetTemperature("cold", None))
    plan.add(Delay(90, "Let the plate cool"))
    plan.add(Pause("Spin the plate down"))
    plan.add(MoveLabware("reactions", OFF_DECK))
    plan.add(Comment("Store reactions at -20 °C"))
    return plan


def test_plating_bench_protocol():
    protocol = PlatingProtocol(plating_data={"bacterium_locations": {"A1": "GFP", "B1": "RFP"}}, replicates=2)
    bundle = protocol.compile(load_profile("bench_default"))
    assert [f.path for f in bundle.files] == ["plating_bench_protocol.md", "plating_plan.json",
                                              "plating_layout.json", "plating_layout.xlsx"]
    text = bundle.get("plating_bench_protocol.md").content
    assert "- P20 (2–20 µL): 4 tips" in text and "- P200 (20–200 µL): 1 tip" in text
    assert "### 2. Distribute LB to dilution wells" in text  # the lid step is not numbered
    assert "`dilution_plate_1` A1, B1, A7, B7 | 18 each | aspirate 76 µL once, including 4 µL extra" in text
    assert "| 1 | new | P20 | `culture_plate` A1 | `dilution_plate_1` A1 | 2 | mix 5 × 19 µL after |" in text
    assert "| `dilution_plate_1` A1 | `agar_plate_1` A1 | 4 | dispense 8 mm below the rim |" in text
    assert "Cells: pipette gently." in text and "Do the transfers in the order listed." in text


def test_non_transfer_steps_read_as_instructions():
    text = _document(_assembly_like_plan())
    assert "### 1. Set the temperature block (`cold`) to 4 °C" in text
    assert "Put `reactions` in the thermocycler (`tc`), close the lid and run this program with the lid at 42 °C:" in text
    assert "| 42 °C | 2 min |" in text and "| 16 °C | 5 min |" in text
    assert "Repeat 75 times (about 8 h 45 min in total)." in text and "Then hold at 4 °C." in text
    assert "### 5. Turn off the temperature block (`cold`)" in text
    assert "### 6. Wait 1 min 30 s" in text and "Let the plate cool" in text
    assert "**Spin the plate down**" in text
    assert "### 8. Set `reactions` aside" in text
    assert "Store reactions at -20 °C" in text
    assert "| `reagents` | 1.5 mL tubes |" in text
    assert "- Cold block or ice (`cold`)" in text


def test_transfer_details():
    text = _document(_assembly_like_plan())
    assert "Viscous glycerol stock: pipette slowly" in text
    assert "`reactions` A1, B1 | 12 each | aspirate 24 µL once, then dispense into each well |" in text
    assert "Use a fresh tip for every transfer." in text


def test_reduced_mixes_are_flagged():
    # Only the P2 can pipette 1 µL, so the 8 µL mix is reduced to what the P2 holds.
    with pytest.warns(LabLibreWarning, match="reduced from 8 µL to 2 µL") as caught:
        bundle = compile_plan(_assembly_like_plan(), load_profile("bench_default"))
    assert len(caught) == 2
    assert bundle.warnings == [
        "Step 4: Mix after at reactions:A1 reduced from 8 µL to 2 µL: P2 holds at most 2 µL",
        "Step 4: Mix after at reactions:B1 reduced from 8 µL to 2 µL: P2 holds at most 2 µL",
    ]
    text = bundle.get("assembly_bench_protocol.md").content
    assert ("| 1 | new | P2 | `reagents` A2 | `reactions` A1 | 1 | "
            "mix 3 × 2 µL after (reduced from 8 µL: the P2 holds 2 µL) |") in text
    assert text.index("## Warnings") < text.index("## Procedure")
    assert "- Step 4: Mix after at reactions:B1 reduced from 8 µL to 2 µL" in text


def test_split_volumes_are_one_row():
    plan = Plan("Split")
    plate = plan.add_labware("plate", "plate_96")
    plan.load(plate["A1"], Liquid("water"), 100)
    plan.add(TransferSet(transfers([(plate["A1"], plate["B1"], 90)], mix_after=Mix(10))))
    text = _document(plan, _bench(PipetteSpec("P20", 1, 2, 20)))
    assert "| 1 | new | P20 | `plate` A1 | `plate` B1 | 90 (5 × 18) | mix 3 × 10 µL after |" in text


def test_multichannel_rows_name_columns():
    plan = Plan("Columns")
    reservoir = plan.add_labware("reservoir", "reservoir_12")
    plate = plan.add_labware("plate", "plate_96")
    plan.load(reservoir["A1"], Liquid("media"), 10000)
    plan.add(TransferSet(transfers([(reservoir["A1"], w, 50) for w in plate.wells()[:16]])))
    text = _document(plan, _bench(PipetteSpec("P200 multi", 8, 20, 200)))
    assert "| 1 | new | P200 multi | `reservoir` A1 | `plate` column 1 | 50 | 8-channel |" in text
    assert "| 2 | new | P200 multi | `reservoir` A1 | `plate` column 2 | 50 | 8-channel |" in text


def test_rejects_robot_profiles():
    with pytest.raises(EmitError, match="targets"):
        ManualEmitter().compile(_assembly_like_plan(), load_profile("ot2_default"))


@pytest.mark.parametrize("seconds, text", [(0, "0 s"), (45, "45 s"), (90, "1 min 30 s"), (3600, "1 h"),
                                           (3725, "1 h 2 min 5 s")])
def test_duration(seconds, text):
    assert _duration(seconds) == text
