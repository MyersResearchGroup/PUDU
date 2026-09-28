import csv
import io
import json
import warnings

import pytest

from lablibre import (Liquid, LiquidClass, Mix, Pause, PipetteSpec, Plan, RobotProfile, TipPolicy, Transfer,
                      TransferSet, load_profile, transfers)
from lablibre.emitters import EmitError, HamiltonEmitter
from lablibre.emitters.hamilton import COLUMNS
from pudu.plans import PlatingProtocol, TransformationProtocol


def _uri(name):
    return f"https://sbol.org/{name}/1"


STRAINS = [
    {"Strain": _uri("strain_GFP"), "Chassis": _uri("DH5alpha"), "Plasmids": [_uri("composite_1")]},
    {"Strain": _uri("strain_RFP"), "Chassis": _uri("DH5alpha"), "Plasmids": [_uri("composite_2")]},
]


def _compile(plan, profile=None):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return HamiltonEmitter().compile(plan, profile or load_profile("hamilton_star_default"))


def _rows(bundle, path):
    return list(csv.DictReader(io.StringIO(bundle.get(path).content)))


def _profile(**changes):
    base = load_profile("hamilton_star_default")
    data = {"robot": base.robot, "name": base.name, "pipettes": list(base.pipettes), "labware": dict(base.labware),
            "supports_multi_dispense": False, "liquid_classes": dict(base.liquid_classes),
            "options": dict(base.options)}
    data.update(changes)
    return RobotProfile(**data)


def test_transformation_splits_into_worklists_around_thermocycling():
    bundle = _compile(TransformationProtocol(transformation_data=STRAINS, replicates=1).build_plan())
    assert [f.path for f in bundle.by_role("worklist")] == ["transformation_worklist_1.csv",
                                                           "transformation_worklist_2.csv"]
    sheet = bundle.by_role("setup")[0].content
    order = sheet[sheet.index("## Run order"):sheet.index("## Worklist columns")]
    assert order.index("Set the thermocycler") < order.index("transformation_worklist_1.csv") \
        < order.index("Heat shock") < order.index("transformation_worklist_2.csv") < order.index("Recovery")
    assert "When it finishes, put `transformation_plate` back at Plate_1." in order
    assert "set_lid" not in order.lower() and "temperature block" not in order  # no lid steps, no on-deck cold block


def test_worklist_rows_use_channels_tip_sizes_and_positions():
    bundle = _compile(TransformationProtocol(transformation_data=STRAINS, replicates=1).build_plan())
    first = _rows(bundle, "transformation_worklist_1.csv")
    assert list(first[0]) == [name for name, _ in COLUMNS]
    cells = [r for r in first if r["Step"] == "4"]
    assert [(r["Batch"], r["Channel"], r["NewTip"], r["DropTip"]) for r in cells] == [("1", "1", "1", "0"),
                                                                                     ("2", "1", "0", "1")]
    assert cells[0]["AspMixCycles"] == "3" and cells[0]["AspMixVolume_uL"] == "50"
    assert cells[0]["LiquidClass"] == "LabLibre_Cells" and cells[0]["TipType"] == "50uL_Filter"
    dna = [r for r in first if r["Step"] == "5"]
    assert {r["Batch"] for r in dna} == {"3"} and [r["Channel"] for r in dna] == ["1", "2"]  # two channels at once
    assert [(r["SourceLabware"], r["SourceWell"], r["SourcePosition"]) for r in dna] == [("Tubes_1", "A1", "1"),
                                                                                         ("Tubes_1", "B1", "2")]
    assert all(r["DispMixCycles"] == "2" and r["DispMixVolume_uL"] == "20" and r["TouchTip"] == "1" for r in dna)
    media = _rows(bundle, "transformation_worklist_2.csv")
    assert {r["TipType"] for r in media} == {"300uL_Filter"} and {r["Volume_uL"] for r in media} == {"60"}
    assert {r["DispenseBelowTop_mm"] for r in media} == {"-2"}
    assert [r["Batch"] for r in media] == ["1", "2"]  # batches restart in each file


def test_plating_positions_and_dispense_heights():
    transformation = TransformationProtocol(transformation_data=STRAINS, replicates=1)
    plating_input = transformation.plating_input()
    bundle = _compile(PlatingProtocol(plating_data=plating_input).build_plan())
    rows = _rows(bundle, "plating_worklist_1.csv")
    assert rows[2]["DestWell"] == "A7" and rows[2]["DestPosition"] == "49"
    spots = [r for r in rows if r["DestLabware"] == "Plate_3"]
    assert spots and {r["DispenseBelowTop_mm"] for r in spots} == {"8"}


def test_switching_tip_size_drops_the_other_tips_first():
    plan = Plan("Two sizes")
    plate = plan.add_labware("plate", "plate_96")
    plan.load(plate["A1"], Liquid("water"), 1000)
    plan.add(TransferSet(transfers([(plate["A1"], plate["B1"], 5), (plate["A1"], plate["C1"], 200),
                                    (plate["A1"], plate["D1"], 5)]), tips=TipPolicy.ONE, ordering="sequential"))
    rows = _rows(_compile(plan), "two_sizes_worklist_1.csv")
    assert [(r["TipType"], r["NewTip"], r["DropTip"]) for r in rows] == [
        ("50uL_Filter", "1", "1"), ("300uL_Filter", "1", "1"), ("50uL_Filter", "1", "1")]


def test_run_sheet_lists_layout_tips_liquids_and_warnings():
    bundle = _compile(TransformationProtocol(transformation_data=STRAINS, replicates=1).build_plan())
    sheet = bundle.by_role("setup")[0].content
    assert "| Tubes_1 | `dna_tubes` | 1.5 mL tubes (24-position tube carrier) | DNA plasmids |" in sheet
    assert "| 50uL_Filter | 3 |" in sheet and "| 300uL_Filter | 1 |" in sheet
    assert "| Tubes_2 | `reagent_tubes` | A1 | Competent cells DH5alpha | 100 |" in sheet
    assert "`dna_tubes` sits on the deck at Tubes_1 without temperature control" in sheet
    assert bundle.warnings and "Profile note:" in sheet
    assert "| `DispenseBelowTop_mm` |" in sheet


def test_pauses_split_worklists_and_locations_can_be_pinned():
    plan = Plan("Pinned")
    plate = plan.add_labware("plate", "plate_96")
    plan.load(plate["A1"], Liquid("water"), 100)
    plan.add(TransferSet(transfers([(plate["A1"], plate["B1"], 5)])))
    plan.add(Pause("Seal the plate"))
    plan.add(TransferSet(transfers([(plate["A1"], plate["C1"], 5)])))
    bundle = _compile(plan, _profile(options={**load_profile("hamilton_star_default").options,
                                              "locations": {"plate": "Plate_5"}}))
    assert len(bundle.by_role("worklist")) == 2
    assert _rows(bundle, "pinned_worklist_1.csv")[0]["SourceLabware"] == "Plate_5"
    assert "**Seal the plate**" in bundle.by_role("setup")[0].content


def test_compile_plan_routes_hamilton_profiles():
    from lablibre import compile_plan
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bundle = compile_plan(PlatingProtocol(plating_data={"bacterium_locations": {"A1": "GFP"}}).build_plan(),
                              load_profile("hamilton_star_default"))
    assert bundle.get("plating_worklist_1.csv") is not None


@pytest.mark.parametrize("changes, message", [
    ({"robot": "ot2"}, "targets"),
    ({"supports_multi_dispense": True}, "supports_multi_dispense to false"),
    ({"liquid_classes": {"aqueous": {"aspiration_rate": 1}}}, "doesn't understand"),
    ({"pipettes": [PipetteSpec("CO-RE 96", 96, 1, 1000, tip_rack="t")]}, "fixed-pitch head"),
    ({"pipettes": [PipetteSpec("channels", 8, 1, 1000, pitch="independent")]}, "needs a tip_rack"),
    ({"options": {"positions": {**load_profile("hamilton_star_default").options["positions"], "plate_96": ["Plate_1"]}}},
     "No free deck position for 'dilution_plate_1'"),
])
def test_unsupported_profiles_raise(changes, message):
    plan = PlatingProtocol(plating_data={"bacterium_locations": {"A1": "GFP"}}).build_plan()
    with pytest.raises(EmitError, match=message):
        _compile(plan, _profile(**changes))
