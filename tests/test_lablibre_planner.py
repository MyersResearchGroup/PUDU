import pytest

from lablibre import (Liquid, Mix, Ordering, Plan, PipetteSpec, RobotProfile, TipPolicy, TransferSet,
                      load_profile, transfers)
from lablibre.ops import DropTips, HeadLayout, PickUpTips, TransferPass
from lablibre.planner import ChannelPlanner, PlanningError

P20 = PipetteSpec("p20_single", channels=1, min_volume=1, max_volume=20)
P300_MULTI = PipetteSpec("p300_multi", channels=8, min_volume=20, max_volume=200)
P20_MULTI = PipetteSpec("p20_multi", channels=8, min_volume=1, max_volume=20)
HEAD_96 = PipetteSpec("head_96", channels=96, min_volume=5, max_volume=1000)
STAR = PipetteSpec("star_1000", channels=8, min_volume=0.5, max_volume=1000, pitch="independent",
                   per_channel_volume=True)


def _profile(*pipettes):
    return RobotProfile("test", "test", pipettes)


def _plan():
    plan = Plan("p")
    plan.add_labware("plate", "plate_96")
    plan.add_labware("plate2", "plate_96")
    plan.add_labware("reservoir", "reservoir_12")
    plan.add_labware("tubes", "tube_rack_24")
    for name in ("plate", "plate2", "reservoir", "tubes"):
        for well in plan.labware[name].wells():
            plan.load(well, Liquid(name), 1)
    return plan


def _plan_step(profile, plan, step):
    return ChannelPlanner(profile, plan).plan_transfer_set(step)


def _kinds(transfer_plan):
    return [op.kind for op in transfer_plan.ops]


# --- single channel ------------------------------------------------------------------------

def test_single_channel_new_tip_each():
    plan = _plan()
    plate = plan.labware["plate"]
    step = TransferSet(transfers([(plate["A1"], plate["B1"], 5), (plate["A1"], plate["C1"], 5)]))
    result = _plan_step(_profile(P20), plan, step)
    assert _kinds(result) == ["pick_up_tips", "transfer_pass", "drop_tips"] * 2
    assert all(p.layout is HeadLayout.SINGLE for p in result.passes())
    assert result.tips_used() == {"p20_single": 2}


def test_large_volumes_are_split_and_mixes_placed_on_first_and_last_pass():
    plan = _plan()
    plate = plan.labware["plate"]
    step = TransferSet(transfers([(plate["A1"], plate["B1"], 90)], mix_before=Mix(50), mix_after=Mix(10)))
    passes = _plan_step(_profile(P20), plan, step).passes()
    assert [p.transfers[0].volume for p in passes] == [18] * 5
    assert passes[0].transfers[0].mix_before == Mix(20)  # clamped to the head
    assert all(p.transfers[0].mix_before is None for p in passes[1:])
    assert passes[-1].transfers[0].mix_after == Mix(10)
    assert all(p.transfers[0].mix_after is None for p in passes[:-1])


def test_tip_policies():
    plan = _plan()
    plate, tubes = plan.labware["plate"], plan.labware["tubes"]
    pairs = [(tubes["A1"], plate["A1"], 5), (tubes["A1"], plate["B1"], 5),
             (tubes["B1"], plate["C1"], 5), (tubes["A1"], plate["D1"], 5)]
    profile = _profile(P20)
    one = _plan_step(profile, plan, TransferSet(transfers(pairs), tips=TipPolicy.ONE))
    assert one.tips_used() == {"p20_single": 1}
    assert _kinds(one)[0] == "pick_up_tips" and _kinds(one)[-1] == "drop_tips"
    per_source = _plan_step(profile, plan, TransferSet(transfers(pairs), tips=TipPolicy.PER_SOURCE))
    assert per_source.tips_used() == {"p20_single": 3}  # A1, B1, then back to A1


def test_picks_smallest_head_that_fits_and_errors_when_none_can():
    plan = _plan()
    plate = plan.labware["plate"]
    p300 = PipetteSpec("p300_single", channels=1, min_volume=20, max_volume=300)
    step = TransferSet(transfers([(plate["A1"], plate["B1"], 15), (plate["A1"], plate["C1"], 150)]))
    heads = [p.pipette.name for p in _plan_step(_profile(p300, P20), plan, step).passes()]
    assert heads == ["p20_single", "p300_single"]
    with pytest.raises(PlanningError, match="0.5 µL"):
        _plan_step(_profile(P20), plan, TransferSet(transfers([(plate["A1"], plate["B1"], 0.5)])))


# --- fixed-pitch 8 channel -----------------------------------------------------------------

def test_reservoir_to_full_plate_uses_8_channel_columns():
    plan = _plan()
    reservoir, plate = plan.labware["reservoir"], plan.labware["plate"]
    step = TransferSet(transfers([(reservoir["A1"], w, 90) for w in plate.wells()]), tips=TipPolicy.ONE)
    result = _plan_step(_profile(P300_MULTI, P20), plan, step)
    passes = result.passes()
    assert len(passes) == 12
    assert all(p.layout is HeadLayout.FIXED and p.pipette.name == "p300_multi" for p in passes)
    assert [p.transfers[0].dest.well for p in passes] == [f"A{c}" for c in range(1, 13)]
    assert [t.dest.well for t in passes[0].transfers] == [f"{r}1" for r in "ABCDEFGH"]
    assert result.tips_used() == {"p300_multi": 8}


def test_column_to_column_copy_groups_only_when_rows_line_up():
    plan = _plan()
    plate, plate2 = plan.labware["plate"], plan.labware["plate2"]
    aligned = transfers([(plate[f"{r}1"], plate2[f"{r}3"], 30) for r in "ABCDEFGH"])
    result = _plan_step(_profile(P300_MULTI, P20), plan, TransferSet(aligned))
    assert [p.layout for p in result.passes()] == [HeadLayout.FIXED]

    shifted = transfers([(plate[f"{r}1"], plate2[f"{s}3"], 15) for r, s in zip("ABCDEFGH", "BCDEFGHA")])
    result = _plan_step(_profile(P20_MULTI, P20), plan, TransferSet(shifted))
    assert [p.layout for p in result.passes()] == [HeadLayout.SINGLE] * 8


def test_incomplete_column_falls_back_to_single_channel():
    plan = _plan()
    reservoir, plate = plan.labware["reservoir"], plan.labware["plate"]
    wells = plate.columns()[0] + plate.columns()[1][:3]
    step = TransferSet(transfers([(reservoir["A1"], w, 10) for w in wells]))
    layouts = [p.layout for p in _plan_step(_profile(P20_MULTI, P20), plan, step).passes()]
    assert layouts == [HeadLayout.FIXED] + [HeadLayout.SINGLE] * 3


def test_mixed_volumes_in_a_column_are_not_grouped_on_fixed_heads():
    plan = _plan()
    reservoir, plate = plan.labware["reservoir"], plan.labware["plate"]
    step = TransferSet(transfers([(reservoir["A1"], w, 5 + i) for i, w in enumerate(plate.columns()[0])]))
    layouts = {p.layout for p in _plan_step(_profile(P20_MULTI, P20), plan, step).passes()}
    assert layouts == {HeadLayout.SINGLE}


def test_multichannel_only_profile_needs_partial_pickup_for_leftovers():
    plan = _plan()
    reservoir, plate = plan.labware["reservoir"], plan.labware["plate"]
    step = TransferSet(transfers([(reservoir["A1"], plate["A1"], 10)]))
    with pytest.raises(PlanningError, match="on its own"):
        _plan_step(_profile(P20_MULTI), plan, step)
    partial = PipetteSpec("flex_8", channels=8, min_volume=1, max_volume=50, partial_pickup=True)
    result = _plan_step(_profile(partial), plan, step)
    assert result.ops[0] == PickUpTips(partial, 1)
    assert result.passes()[0].layout is HeadLayout.SINGLE


# --- fixed-pitch 96 channel ----------------------------------------------------------------

def test_96_head_stamps_whole_plate():
    plan = _plan()
    plate, plate2 = plan.labware["plate"], plan.labware["plate2"]
    step = TransferSet(transfers([(w, plate2[w.well], 10) for w in plate.wells()]))
    result = _plan_step(_profile(HEAD_96, P20), plan, step)
    passes = result.passes()
    assert len(passes) == 1 and len(passes[0].transfers) == 96
    assert passes[0].transfers[0].source.well == "A1"
    assert result.tips_used() == {"head_96": 96}


# --- independent pitch ---------------------------------------------------------------------

def test_independent_head_packs_up_to_8_with_per_channel_volumes():
    plan = _plan()
    reservoir, plate = plan.labware["reservoir"], plan.labware["plate"]
    step = TransferSet(transfers([(reservoir["A1"], w, 5 + i) for i, w in enumerate(plate.wells()[:10])]))
    passes = _plan_step(_profile(STAR), plan, step).passes()
    assert [len(p.transfers) for p in passes] == [8, 2]
    assert all(p.layout is HeadLayout.INDEPENDENT for p in passes)
    assert [t.volume for t in passes[0].transfers] == [5 + i for i in range(8)]


def test_independent_channels_cannot_share_a_tube():
    plan = _plan()
    tubes, plate = plan.labware["tubes"], plan.labware["plate"]
    same_tube = TransferSet(transfers([(tubes["A1"], w, 5) for w in plate.wells()[:3]]))
    assert [len(p.transfers) for p in _plan_step(_profile(STAR), plan, same_tube).passes()] == [1, 1, 1]
    different_tubes = TransferSet(transfers([(t, w, 5) for t, w in zip(tubes.wells(), plate.wells()[:3])]))
    assert [len(p.transfers) for p in _plan_step(_profile(STAR), plan, different_tubes).passes()] == [3]


# --- ordering ------------------------------------------------------------------------------

def test_sequential_never_groups_dependent_transfers():
    plan = _plan()
    plate = plan.labware["plate"]
    chain = transfers([(plate["A1"], plate["A2"], 5), (plate["A2"], plate["A3"], 5), (plate["A3"], plate["A4"], 5)])
    step = TransferSet(chain, ordering=Ordering.SEQUENTIAL)
    passes = _plan_step(_profile(STAR), plan, step).passes()
    assert [[t.dest.well for t in p.transfers] for p in passes] == [["A2"], ["A3"], ["A4"]]


def test_sequential_groups_consecutive_columns_in_order():
    plan = _plan()
    plate = plan.labware["plate"]
    pairs = [(plate[f"{r}{c}"], plate[f"{r}{c + 1}"], 10) for c in (1, 2) for r in "ABCDEFGH"]
    step = TransferSet(transfers(pairs), ordering=Ordering.SEQUENTIAL)
    passes = _plan_step(_profile(P20_MULTI, P20), plan, step).passes()
    assert [(p.layout, p.transfers[0].dest.well) for p in passes] == [(HeadLayout.FIXED, "A2"), (HeadLayout.FIXED, "A3")]


def test_any_ordering_keeps_groups_in_first_use_order():
    plan = _plan()
    reservoir, plate = plan.labware["reservoir"], plan.labware["plate"]
    pairs = [(reservoir["A1"], plate["A12"], 10)] + [(reservoir["A1"], w, 10) for w in plate.columns()[0]]
    passes = _plan_step(_profile(P20_MULTI, P20), plan, TransferSet(transfers(pairs))).passes()
    assert [p.layout for p in passes] == [HeadLayout.SINGLE, HeadLayout.FIXED]


# --- whole plan ----------------------------------------------------------------------------

def test_plan_all_on_ot2_default_profile():
    plan = Plan("plating")
    tubes = plan.add_labware("tubes", "tube_rack_15")
    dilution = plan.add_labware("dilution_plate", "plate_96")
    plan.load(tubes["A1"], Liquid("LB"), 10000)
    plan.add(TransferSet(transfers([(tubes["A1"], w, 18) for w in dilution.wells()[:3]]), tips=TipPolicy.ONE))
    plan.add(TransferSet(transfers([(tubes["A1"], w, 90) for w in dilution.wells()[3:5]]), tips=TipPolicy.ONE))
    planned = ChannelPlanner(load_profile("ot2_default"), plan).plan_all()
    assert list(planned) == [0, 1]
    assert planned[0].tips_used() == {"p20_single_gen2": 1}
    assert planned[1].tips_used() == {"p300_single_gen2": 1}
    assert isinstance(planned[1].ops[0], PickUpTips) and isinstance(planned[1].ops[-1], DropTips)
    assert all(isinstance(op, TransferPass) for op in planned[1].ops[1:-1])
