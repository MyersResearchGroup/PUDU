import json

import pytest

from lablibre import (OFF_DECK, Bundle, FileRole, Liquid, LiquidClass, Mix, ModuleKind, MoveLabware,
                      Pitch, PipetteSpec, Plan, PlanError, RobotProfile, SetLid, SetTemperature,
                      Severity, ThermalProfile, ThermalStep, TipPolicy, Transfer, TransferSet,
                      builtin_profiles, load_profile, parse_well, transfers, well_name)


def _errors(plan):
    return [i for i in plan.validate() if i.severity is Severity.ERROR]


def _plating_plan(number_constructs=3, replicates=2):
    """A reduced version of PUDU plating: distribute LB, dilute bacteria 1:10, spot onto agar."""
    plan = Plan("plating")
    plan.add_module("thermocycler", ModuleKind.THERMOCYCLER)
    source = plan.add_labware("source_plate", "plate_96", module="thermocycler", max_volume_ul=100)
    tubes = plan.add_labware("tubes", "tube_rack_15")
    dilution = plan.add_labware("dilution_plate", "plate_96", max_volume_ul=100)
    agar = plan.add_labware("agar_plate", "plate_96")

    plan.load(tubes["A1"], Liquid("LB"), 10000)
    for i in range(number_constructs):
        plan.load(source.wells()[i], Liquid(f"construct_{i}", LiquidClass.CELLS), 20)

    dilution_wells = dilution.wells()[:number_constructs]
    plan.add(TransferSet(transfers([(tubes["A1"], w, 18) for w in dilution_wells]),
                         tips=TipPolicy.ONE, label="Distribute LB"))
    plan.add(TransferSet(transfers([(source.wells()[i], dilution_wells[i], 2) for i in range(number_constructs)],
                                   mix_after=Mix(10)),
                         liquid_class=LiquidClass.CELLS, label="Dilute 1:10"))
    agar_wells = iter(agar.wells())
    plan.add(TransferSet(transfers([(w, next(agar_wells), 4) for w in dilution_wells for _ in range(replicates)]),
                         liquid_class=LiquidClass.CELLS, tips=TipPolicy.PER_SOURCE, label="Spot"))
    return plan


# --- labware -------------------------------------------------------------------------------

def test_well_parsing_round_trips():
    assert parse_well("A1") == (0, 0)
    assert parse_well("H12") == (7, 11)
    assert well_name(*parse_well("P24")) == "P24"
    for bad in ["a1", "A0", "1A", "AA1", ""]:
        with pytest.raises(ValueError):
            parse_well(bad)


def test_labware_wells_are_column_major_and_validated():
    plan = Plan("p")
    plate = plan.add_labware("plate", "plate_96")
    assert [w.well for w in plate.wells()[:3]] == ["A1", "B1", "C1"]
    assert [w.well for w in plate.columns()[1]] == ["A2", "B2", "C2", "D2", "E2", "F2", "G2", "H2"]
    with pytest.raises(KeyError):
        plate["I1"]
    with pytest.raises(ValueError):
        plan.add_labware("bad", "plate_97")


# --- steps ---------------------------------------------------------------------------------

def test_transfer_set_rejects_implicit_tip_reuse_with_mix_after():
    plan = Plan("p")
    plate = plan.add_labware("plate", "plate_96")
    with pytest.raises(ValueError, match="mix_after"):
        TransferSet([Transfer(plate["A1"], plate["B1"], 5, mix_after=Mix(5))], tips=TipPolicy.PER_SOURCE)
    TransferSet([Transfer(plate["A1"], plate["B1"], 5, mix_after=Mix(5))], tips=TipPolicy.ONE)  # explicit choice
    with pytest.raises(ValueError, match="dispense_below_top"):
        Transfer(plate["A1"], plate["B1"], 5, dispense_below_top=-1)
    with pytest.raises(ValueError):
        TransferSet([])
    with pytest.raises(ValueError):
        Transfer(plate["A1"], plate["B1"], 0)


def test_transfer_set_summaries():
    plan = Plan("p")
    plate = plan.add_labware("plate", "plate_96")
    step = TransferSet(transfers([(plate["A1"], plate["B1"], 5), (plate["A1"], plate["C1"], 7),
                                  (plate["A2"], plate["B1"], 1)]))
    assert step.sources() == [plate["A1"], plate["A2"]]
    assert step.destinations() == [plate["B1"], plate["C1"]]
    assert step.volume_by_source() == {plate["A1"]: 12, plate["A2"]: 1}


# --- plan validation -----------------------------------------------------------------------

def test_plating_plan_is_valid_and_serializable():
    plan = _plating_plan()
    assert plan.check() == []
    data = json.loads(json.dumps(plan.to_dict()))
    assert [s["kind"] for s in data["steps"]] == ["transfer_set"] * 3
    assert data["steps"][0]["transfers"][0]["source"] == "tubes:A1"


def test_source_running_dry_is_an_error():
    plan = Plan("p")
    plate = plan.add_labware("plate", "plate_96")
    plan.load(plate["A1"], Liquid("water"), 10)
    plan.add(TransferSet(transfers([(plate["A1"], plate["B1"], 6), (plate["A1"], plate["C1"], 6)])))
    with pytest.raises(PlanError, match="only 4 µL remain"):
        plan.check()


def test_empty_source_and_overfilled_destination_are_errors():
    plan = Plan("p")
    plate = plan.add_labware("plate", "plate_96", max_volume_ul=100)
    plan.load(plate["A1"], Liquid("water"), 500)
    plan.add(TransferSet(transfers([(plate["B1"], plate["C1"], 5), (plate["A1"], plate["D1"], 150)])))
    messages = [i.message for i in _errors(plan)]
    assert any("nothing was loaded" in m for m in messages)
    assert any("above its 100 µL capacity" in m for m in messages)


def test_transferred_liquid_can_be_used_as_a_source():
    plan = Plan("p")
    plate = plan.add_labware("plate", "plate_96")
    plan.load(plate["A1"], Liquid("water"), 50)
    plan.add(TransferSet(transfers([(plate["A1"], plate["B1"], 20)])))
    plan.add(TransferSet(transfers([(plate["B1"], plate["C1"], 20)])))
    assert _errors(plan) == []


def test_module_references_and_kinds_are_checked():
    plan = Plan("p")
    plan.add_module("cold", ModuleKind.TEMPERATURE)
    plan.add_labware("plate", "plate_96")
    plan.add(SetTemperature("cold", 4))
    plan.add(ThermalProfile("cold", [ThermalStep(42, 120)]))
    plan.add(SetLid("missing", open=True))
    plan.add(MoveLabware("plate", "nowhere"))
    plan.add(MoveLabware("plate", OFF_DECK))
    errors = _errors(plan)
    assert [e.step_index for e in errors] == [1, 2, 3]


def test_unknown_labware_in_well_ref_is_an_error():
    plan = Plan("p")
    plate = plan.add_labware("plate", "plate_96")
    other = Plan("other").add_labware("ghost", "plate_96")
    plan.load(plate["A1"], Liquid("water"), 50)
    plan.add(TransferSet(transfers([(plate["A1"], other["A1"], 5)])))
    assert "undeclared labware 'ghost'" in _errors(plan)[0].message


def test_thermal_profile_duration():
    profile = ThermalProfile("tc", [ThermalStep(42, 120), ThermalStep(16, 300)], repetitions=75)
    assert profile.duration_seconds == 75 * 420


# --- profiles ------------------------------------------------------------------------------

def test_builtin_ot2_profile_loads():
    assert "ot2_default" in builtin_profiles()
    profile = load_profile("ot2_default")
    assert profile.robot == "ot2"
    assert profile.has_module(ModuleKind.THERMOCYCLER)
    assert profile.module("temperature").location == "1"
    assert profile.liquid_handling(LiquidClass.GLYCEROL)["aspiration_rate"] == 0.3


def test_profile_resolves_labware_by_name_before_format():
    profile = load_profile("ot2_default")
    plan = Plan("p")
    agar = plan.add_labware("agar_plate", "plate_96")
    dilution = plan.add_labware("dilution_plate", "plate_96")
    custom = RobotProfile.from_dict({**_profile_dict(profile), "labware": {"agar_plate": "nunc_omnitray_96grid",
                                                                           "plate_96": "generic_96"}})
    assert custom.resolve_labware(agar) == "nunc_omnitray_96grid"
    assert custom.resolve_labware(dilution) == "generic_96"
    with pytest.raises(KeyError, match="no labware definition"):
        custom.resolve_labware(plan.add_labware("tubes", "tube_rack_24"))


def test_pipettes_for_prefers_fewest_aspirations_then_smallest():
    profile = load_profile("ot2_default")
    assert [p.name for p in profile.pipettes_for(5)] == ["p20_single_gen2"]
    assert [p.name for p in profile.pipettes_for(18)] == ["p20_single_gen2"]
    assert [p.name for p in profile.pipettes_for(90)] == ["p300_single_gen2", "p20_single_gen2"]


def test_pipette_and_profile_validation():
    with pytest.raises(ValueError):
        PipetteSpec("bad", channels=8, min_volume=10, max_volume=5)
    head = PipetteSpec("star_1000", channels=8, min_volume=0.5, max_volume=1000, pitch="independent",
                       per_channel_volume=True)
    assert head.pitch is Pitch.INDEPENDENT and head.is_multichannel
    with pytest.raises(ValueError, match="unknown liquid classes"):
        RobotProfile("hamilton_star", "x", [head], liquid_classes={"honey": {}})


def _profile_dict(profile):
    return {
        "robot": profile.robot, "name": profile.name,
        "pipettes": [vars(p) | {"pitch": p.pitch.value} for p in profile.pipettes],
        "modules": [vars(m) | {"kind": m.kind.value} for m in profile.modules],
    }


# --- bundles -------------------------------------------------------------------------------

def test_bundle_writes_files_and_rejects_escaping_paths(tmp_path):
    bundle = Bundle("ot2")
    bundle.add("plating.py", "print('hi')\n", FileRole.PROTOCOL)
    bundle.add("data/plating_layout.json", "{}", FileRole.DATA)
    written = bundle.write(tmp_path)
    assert (tmp_path / "data" / "plating_layout.json").read_text() == "{}"
    assert len(written) == 2
    assert [f.path for f in bundle.by_role("protocol")] == ["plating.py"]
    with pytest.raises(FileExistsError):
        bundle.write(tmp_path, overwrite=False)
    with pytest.raises(ValueError):
        bundle.add("plating.py", "", FileRole.PROTOCOL)
    for bad in ["../escape.py", "/abs.py"]:
        with pytest.raises(ValueError):
            bundle.add(bad, "", FileRole.DATA)


def test_profile_resolves_labware_on_modules_before_plain_format():
    profile = load_profile("ot2_default")
    plan = Plan("p")
    plan.add_module("cold", ModuleKind.TEMPERATURE)
    block = plan.add_labware("reagents", "tube_rack_24", module="cold")
    rack = plan.add_labware("rack", "tube_rack_24")
    assert profile.resolve_labware(block, ModuleKind.TEMPERATURE) == "opentrons_24_aluminumblock_nest_1.5ml_snapcap"
    assert profile.resolve_labware(rack) == "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap"
