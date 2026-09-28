"""
Channel planner: turns transfer sets into pipetting operations for one robot.

For each :class:`~lablibre.steps.TransferSet` the planner decides which head
does each transfer, which transfers run together on a multichannel head, how
many passes large volumes need, and when tips are picked up and dropped.

Heads are handled by capability, not by robot brand:

* **Fixed-pitch multichannel** (Opentrons 8-channel, 96-channel heads): channels
  cover a block of 8 rows x ``channels / 8`` columns. A group needs one transfer
  per channel, lined up row-for-row between source and destination (or drawing
  from a single-row reservoir), all with the same volume and mixing.
* **Independent-pitch multichannel** (Hamilton STAR): any wells, up to
  ``channels`` at once, as long as no two channels share a destination or a
  non-reservoir source. Channels are assigned in destination row order; exact
  reachability is left to the robot's method.
* **Single channel**, or a multichannel head that can work one tip at a time:
  everything else.

With ``Ordering.ANY`` the planner may regroup transfers freely, and assumes the
transfers in the set don't depend on each other. With ``Ordering.SEQUENTIAL``
it only groups consecutive transfers, and never groups a transfer with one
whose destination it draws from.

Sets with ``multi_dispense`` are grouped by geometry first (single channel or
fixed-pitch columns), then consecutive groups drawing from the same sources
are chained into :class:`~lablibre.ops.DistributePass` operations. The head is
chosen for the whole chain, preferring the fewest aspirations, so a large
pipette may serve dispenses below its own minimum volume. Chains no head can
multi-dispense fall back to ordinary transfers.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from typing import Callable, Dict, Hashable, List, Optional, Sequence, Tuple

from lablibre.labware import LabwareFormat, WellRef
from lablibre.ops import DistributePass, DropTips, HeadLayout, MixReduced, Op, PickUpTips, TransferPass, TransferPlan
from lablibre.plan import Plan
from lablibre.profile import Pitch, PipetteSpec, RobotProfile
from lablibre.steps import Mix, Ordering, TipPolicy, Transfer, TransferSet

FIXED_HEAD_ROWS = 8

_Indexed = Tuple[int, Transfer]
_Lane = Tuple[int, int]


class PlanningError(ValueError):
    """Raised when a transfer set cannot be carried out with a profile's hardware."""


@dataclass
class _Group:
    head: Optional[PipetteSpec]  # None until chosen, when grouping by geometry only
    layout: HeadLayout
    transfers: List[Transfer]    # in channel order
    first_index: int             # position of the earliest member in the transfer set

    @property
    def sources(self) -> Tuple[WellRef, ...]:
        return tuple(t.source for t in self.transfers)


@dataclass
class _Unit:
    """Work done with one set of tips: a head, how many tips it holds, where they draw from, and its passes."""
    head: PipetteSpec
    channels: int
    sources: Tuple[WellRef, ...]
    passes: List[Op]


class ChannelPlanner:
    """
    Plans transfer sets from *plan* for the hardware in *profile*.

    Example::

        planner = ChannelPlanner(load_profile("ot2_default"), plan)
        for index, transfer_plan in planner.plan_all().items():
            print(index, transfer_plan.tips_used())
    """

    def __init__(self, profile: RobotProfile, plan: Plan):
        self.profile = profile
        self.plan = plan
        self._fixed_heads = [h for h in profile.pipettes
                             if h.is_multichannel and h.pitch is Pitch.FIXED and h.channels % FIXED_HEAD_ROWS == 0]
        self._independent_heads = sorted((h for h in profile.pipettes if h.is_multichannel and h.pitch is Pitch.INDEPENDENT),
                                         key=lambda h: -h.channels)
        self._fixed_channel_counts = sorted({h.channels for h in self._fixed_heads}, reverse=True)

    def plan_all(self) -> Dict[int, TransferPlan]:
        """Plan every transfer set in the plan, keyed by step index."""
        return {index: self.plan_transfer_set(step)
                for index, step in enumerate(self.plan.steps) if isinstance(step, TransferSet)}

    def plan_transfer_set(self, step: TransferSet) -> TransferPlan:
        """Plan one transfer set."""
        items = list(enumerate(step.transfers))
        self._warnings: List[MixReduced] = []
        if step.multi_dispense and self.profile.supports_multi_dispense:
            units = self._plan_multi_dispense(step, items)
        else:
            groups = self._group_any(items) if step.ordering is Ordering.ANY else self._group_sequential(items)
            groups.sort(key=lambda g: g.first_index)
            units = [self._unit(step, group) for group in groups]
        return TransferPlan(step, tuple(self._with_tips(step, units)), tuple(self._warnings))

    # --- grouping ------------------------------------------------------------------------

    def _group_any(self, items: List[_Indexed]) -> List[_Group]:
        groups: List[_Group] = []
        remaining = items
        for channels in self._fixed_channel_counts:
            found, remaining = self._extract_fixed_groups(remaining, channels)
            groups += found
        if self._independent_heads:
            # Each transfer goes to its best independent head first (e.g. the right tip size), then each
            # head's transfers are packed onto its channels.
            by_head: Dict[str, List[_Indexed]] = defaultdict(list)
            leftover = []
            for item in remaining:
                head = self._best_independent_head(item[1])
                (by_head[head.name] if head else leftover).append(item)
            for head in self._independent_heads:
                eligible = sorted(by_head[head.name],
                                  key=lambda item: (item[1].dest.labware, item[1].dest.column, item[1].dest.row, item[0]))
                groups += self._pack_independent(eligible, head)
            remaining = leftover
        groups += [self._single(item) for item in remaining]
        return groups

    def _group_sequential(self, items: List[_Indexed]) -> List[_Group]:
        groups: List[_Group] = []
        position = 0
        while position < len(items):
            group = None
            for channels in self._fixed_channel_counts:
                group = self._fixed_window(items[position:position + channels], channels)
                if group:
                    break
            if group is None:
                head = self._best_independent_head(items[position][1])
                if head is not None:
                    group = self._pack_independent(items[position:], head, stop_at_first_misfit=True)[0]
            if group is None:
                group = self._single(items[position])
            groups.append(group)
            position += len(group.transfers)
        return groups

    def _extract_fixed_groups(self, items: List[_Indexed], channels: int,
                              by_geometry: bool = False) -> Tuple[List[_Group], List[_Indexed]]:
        """
        Pull every complete fixed-pitch group out of *items*; return the groups and the leftovers.

        With *by_geometry*, volumes are ignored and no head is chosen yet.
        """
        min_volume = 0 if by_geometry else min(h.min_volume for h in self._fixed_heads if h.channels == channels)
        lanes = self._fixed_lanes(channels)
        buckets: Dict[Hashable, Dict[_Lane, deque]] = defaultdict(lambda: defaultdict(deque))
        leftover: List[_Indexed] = []
        for index, transfer in items:
            slot = self._fixed_slot(transfer, channels) if transfer.volume >= min_volume else None
            if slot is None:
                leftover.append((index, transfer))
            else:
                key, lane = slot
                buckets[key][lane].append((index, transfer))

        groups = []
        for by_lane in buckets.values():
            while all(by_lane[lane] for lane in lanes):
                members = [by_lane[lane].popleft() for lane in lanes]
                groups.append(self._fixed_group(members, channels, choose_head=not by_geometry))
            for queue in by_lane.values():
                leftover.extend(queue)
        leftover.sort(key=lambda item: item[0])
        return groups, leftover

    def _fixed_window(self, window: Sequence[_Indexed], channels: int, by_geometry: bool = False) -> Optional[_Group]:
        """A fixed-pitch group made of exactly the transfers in *window*, or ``None``."""
        if len(window) != channels:
            return None
        min_volume = 0 if by_geometry else min(h.min_volume for h in self._fixed_heads if h.channels == channels)
        if any(t.volume < min_volume for _, t in window):
            return None
        slots = [self._fixed_slot(t, channels) for _, t in window]
        if any(slot is None for slot in slots) or len({key for key, _ in slots}) != 1:
            return None
        by_lane = {lane: item for (_, lane), item in zip(slots, window)}
        lanes = self._fixed_lanes(channels)
        if set(by_lane) != set(lanes) or _has_dependency([t for _, t in window]):
            return None
        return self._fixed_group([by_lane[lane] for lane in lanes], channels, choose_head=not by_geometry)

    def _fixed_group(self, members: List[_Indexed], channels: int, choose_head: bool = True) -> _Group:
        transfers = [t for _, t in members]
        if not choose_head:
            return _Group(None, HeadLayout.FIXED, transfers, min(i for i, _ in members))
        head = self._choose_head(transfers, self._fits_layout(HeadLayout.FIXED, channels))
        if head is None:  # eligibility is checked before grouping, so this means a bug
            raise PlanningError(f"No {channels}-channel head for {transfers[0].source} -> {transfers[0].dest}")
        return _Group(head, HeadLayout.FIXED, transfers, min(i for i, _ in members))

    def _pack_independent(self, items: List[_Indexed], head: PipetteSpec,
                          stop_at_first_misfit: bool = False) -> List[_Group]:
        """Pack *items* in order into groups of up to ``head.channels``."""
        groups: List[_Group] = []
        current: List[_Indexed] = []

        def flush():
            if current:
                ordered = sorted(current, key=lambda item: (item[1].dest.row, item[1].dest.column))
                groups.append(_Group(head, HeadLayout.INDEPENDENT, [t for _, t in ordered],
                                     min(i for i, _ in current)))

        for item in items:
            if current and not self._fits_independent([t for _, t in current], item[1], head):
                flush()
                current = []
                if stop_at_first_misfit:
                    return groups
            current.append(item)
        flush()
        return groups

    def _best_independent_head(self, transfer: Transfer) -> Optional[PipetteSpec]:
        return self._choose_head([transfer], lambda h: h.pitch is Pitch.INDEPENDENT and h.is_multichannel)

    def _fits_independent(self, group: List[Transfer], transfer: Transfer, head: PipetteSpec) -> bool:
        if len(group) >= head.channels or self._best_independent_head(transfer) is not head:
            return False
        sources = {t.source for t in group}
        dests = {t.dest for t in group}
        if transfer.dest in dests or transfer.source in dests or transfer.dest in sources:
            return False
        if transfer.source in sources and self._format(transfer.source).rows != 1:
            return False
        if not head.per_channel_volume and transfer.volume != group[0].volume:
            return False
        return _pass_count(transfer.volume, head) == _pass_count(group[0].volume, head)

    def _single(self, item: _Indexed) -> _Group:
        index, transfer = item
        head = self._choose_head([transfer], lambda h: h.can_work_single)
        if head is None:
            usable = [f"{h.name} ({h.min_volume:g}-{h.max_volume:g} µL, {h.channels}ch)" for h in self.profile.pipettes]
            raise PlanningError(f"No pipette in profile '{self.profile.name}' can transfer {transfer.volume:g} µL "
                                f"from {transfer.source} to {transfer.dest} on its own. Available: {', '.join(usable)}")
        return _Group(head, HeadLayout.SINGLE, [transfer], index)

    @staticmethod
    def _fits_layout(layout: HeadLayout, channels: int) -> Callable[[PipetteSpec], bool]:
        if layout is HeadLayout.FIXED:
            return lambda h: h.pitch is Pitch.FIXED and h.channels == channels
        if layout is HeadLayout.INDEPENDENT:
            return lambda h: h.pitch is Pitch.INDEPENDENT and h.channels >= channels
        return lambda h: h.can_work_single

    def _choose_head(self, transfers: List[Transfer], predicate: Callable[[PipetteSpec], bool]) -> Optional[PipetteSpec]:
        """
        The best head satisfying *predicate* that can handle every transfer's volume.

        Among those, prefer one whose capacity also covers the largest mix, so a
        2 µL transfer followed by a 19 µL mix goes to a P20 rather than a P2.
        """
        volumes = [t.volume for t in transfers]
        low, high = min(volumes), max(volumes)
        mix_volume = max((m.volume for t in transfers for m in (t.mix_before, t.mix_after) if m), default=0)
        candidates = [h for h in self.profile.pipettes_for(high) if predicate(h) and h.min_volume <= low]
        return next((h for h in candidates if h.max_volume >= mix_volume), candidates[0] if candidates else None)

    # --- fixed-pitch geometry --------------------------------------------------------------

    @staticmethod
    def _fixed_lanes(channels: int) -> List[_Lane]:
        """Channel positions as ``(row, column)`` offsets, column-major so the anchor comes first."""
        return [(row, column) for column in range(channels // FIXED_HEAD_ROWS) for row in range(FIXED_HEAD_ROWS)]

    def _fixed_slot(self, transfer: Transfer, channels: int) -> Optional[Tuple[Hashable, _Lane]]:
        """
        Where *transfer* sits under a fixed-pitch head: a grouping key and a lane, or ``None``.

        Transfers with the same key and distinct lanes can run together.
        """
        head_columns = channels // FIXED_HEAD_ROWS
        source = self._footprint(transfer.source, head_columns)
        dest = self._footprint(transfer.dest, head_columns)
        if source is None or dest is None:
            return None
        (source_anchor, source_lane), (dest_anchor, dest_lane) = source, dest
        if source_lane is None and dest_lane is None:
            return None
        if source_lane is not None and dest_lane is not None and source_lane != dest_lane:
            return None
        key = (transfer.source.labware, source_anchor, transfer.dest.labware, dest_anchor,
               transfer.volume, transfer.mix_before, transfer.mix_after, transfer.dispense_below_top)
        return key, dest_lane if dest_lane is not None else source_lane

    def _footprint(self, ref: WellRef, head_columns: int) -> Optional[Tuple[Hashable, Optional[_Lane]]]:
        """
        ``(anchor, lane)`` for *ref* under a head *head_columns* wide.

        A single-row reservoir well is shared by all channels, so its lane is ``None``.
        """
        fmt = self._format(ref)
        if fmt.rows == 1 and (head_columns == 1 or fmt.columns == 1):
            return ref.well, None
        if fmt.rows == FIXED_HEAD_ROWS and fmt.columns % head_columns == 0:
            anchor = ref.column - ref.column % head_columns
            return anchor, (ref.row, ref.column - anchor)
        return None

    def _format(self, ref: WellRef) -> LabwareFormat:
        labware = self.plan.labware.get(ref.labware)
        if labware is None:
            raise PlanningError(f"{ref} refers to labware '{ref.labware}', which the plan does not declare")
        return labware.format

    # --- multi-dispense --------------------------------------------------------------------

    def _plan_multi_dispense(self, step: TransferSet, items: List[_Indexed]) -> List[_Unit]:
        groups = self._group_by_geometry(step, items)
        if step.ordering is Ordering.ANY:
            # Bring together everything drawn from the same sources so it can share aspirations.
            first_use: Dict[Tuple, int] = {}
            for group in groups:
                first_use.setdefault(self._run_key(group), group.first_index)
            groups.sort(key=lambda g: (first_use[self._run_key(g)], g.first_index))
        units: List[_Unit] = []
        run: List[_Group] = []
        for group in groups:
            if run and self._run_key(group) != self._run_key(run[0]):
                units += self._distribute_run(step, run)
                run = []
            run.append(group)
        if run:
            units += self._distribute_run(step, run)
        return units

    def _group_by_geometry(self, step: TransferSet, items: List[_Indexed]) -> List[_Group]:
        """Group into fixed-pitch columns where possible and single transfers otherwise, choosing no heads."""
        groups: List[_Group] = []
        if step.ordering is Ordering.ANY:
            remaining = items
            for channels in self._fixed_channel_counts:
                found, remaining = self._extract_fixed_groups(remaining, channels, by_geometry=True)
                groups += found
            groups += [_Group(None, HeadLayout.SINGLE, [transfer], index) for index, transfer in remaining]
        else:
            position = 0
            while position < len(items):
                group = next((g for g in (self._fixed_window(items[position:position + channels], channels,
                                                             by_geometry=True)
                                          for channels in self._fixed_channel_counts) if g), None)
                if group is None:
                    index, transfer = items[position]
                    group = _Group(None, HeadLayout.SINGLE, [transfer], index)
                groups.append(group)
                position += len(group.transfers)
        groups.sort(key=lambda g: g.first_index)
        return groups

    @staticmethod
    def _run_key(group: _Group) -> Tuple:
        return group.layout, len(group.transfers), group.sources, group.transfers[0].mix_before

    def _distribute_run(self, step: TransferSet, run: List[_Group]) -> List[_Unit]:
        """Chain *run* (groups sharing layout and sources) into as few aspirations as the best head allows."""
        layout, channels = run[0].layout, len(run[0].transfers)
        volumes = [max(t.volume for t in group.transfers) for group in run]
        mix = run[0].transfers[0].mix_before
        best = None
        for head in filter(self._fits_layout(layout, channels), self.profile.pipettes):
            chunks = _pack(volumes, head.max_volume - step.disposal_volume - step.air_gap)
            if chunks is None or any(sum(volumes[i] for i in chunk) + step.disposal_volume < head.min_volume
                                     for chunk in chunks):
                continue
            # Like _choose_head: a head that can do the mix first, then fewest aspirations, then smallest.
            mix_too_big = mix is not None and mix.volume > head.max_volume
            score = (mix_too_big, len(chunks), head.max_volume)
            if best is None or score < best[0]:
                best = (score, head, chunks)
        if best is None:
            return self._without_multi_dispense(step, run)
        _, head, chunks = best
        first = run[0].transfers[0]
        mix_before = self._fit_mix(first.mix_before, head, first.source, "before")
        return [_Unit(head, channels, run[0].sources,
                      [DistributePass(head, layout, tuple(tuple(run[i].transfers) for i in chunk),
                                      step.disposal_volume, step.liquid_class, step.touch_tip, step.blow_out,
                                      mix_before=mix_before, air_gap=step.air_gap)])
                for chunk in chunks]

    def _without_multi_dispense(self, step: TransferSet, run: List[_Group]) -> List[_Unit]:
        """Ordinary transfers for a run no head can multi-dispense; columns no head fits become single transfers."""
        units = []
        for group in run:
            head = self._choose_head(group.transfers, self._fits_layout(group.layout, len(group.transfers)))
            if head is None:
                units += [self._unit(step, self._single((group.first_index, t))) for t in group.transfers]
            else:
                group.head = head
                units.append(self._unit(step, group))
        return units

    # --- tips and passes -------------------------------------------------------------------

    def _unit(self, step: TransferSet, group: _Group) -> _Unit:
        return _Unit(group.head, len(group.transfers), group.sources, self._passes(step, group))

    def _with_tips(self, step: TransferSet, units: List[_Unit]) -> List[Op]:
        """Wrap each unit's passes in tip pickups and drops according to the step's tip policy."""
        ops: List[Op] = []
        held: Dict[str, _Unit] = {}  # head name -> unit whose tips it holds
        for unit in units:
            head = unit.head
            current = held.get(head.name)
            reuse = current is not None and current.channels == unit.channels and (
                step.tips is TipPolicy.ONE or (step.tips is TipPolicy.PER_SOURCE and current.sources == unit.sources))
            if not reuse:
                if current is not None:
                    ops.append(DropTips(head))
                ops.append(PickUpTips(head, unit.channels))
                held[head.name] = unit
            ops += unit.passes
            if step.tips is TipPolicy.NEW_EACH:
                ops.append(DropTips(head))
                del held[head.name]
        ops += [DropTips(unit.head) for unit in held.values()]
        return ops

    def _fit_mix(self, mix: Optional[Mix], head: PipetteSpec, well: WellRef, when: str) -> Optional[Mix]:
        """*mix*, reduced to what *head* holds if needed; each reduction is recorded as a warning."""
        if mix is None or mix.volume <= head.max_volume:
            return mix
        self._warnings.append(MixReduced(well, when, mix.volume, head.max_volume, head.name))
        return Mix(head.max_volume, mix.repetitions)

    def _passes(self, step: TransferSet, group: _Group) -> List[TransferPass]:
        """Split *group* into passes that fit the head; mix before the first pass and after the last."""
        head = group.head
        if step.air_gap >= head.max_volume:
            raise PlanningError(f"Air gap of {step.air_gap:g} µL leaves no room in {head.name} "
                                f"({head.max_volume:g} µL)")
        count = max(_pass_count(t.volume, head, step.air_gap) for t in group.transfers)
        mixes = {id(t): (self._fit_mix(t.mix_before, head, t.source, "before"),
                         self._fit_mix(t.mix_after, head, t.dest, "after")) for t in group.transfers}
        passes = []
        for number in range(count):
            chunk = tuple(replace(t, volume=t.volume / count,
                                  mix_before=mixes[id(t)][0] if number == 0 else None,
                                  mix_after=mixes[id(t)][1] if number == count - 1 else None)
                          for t in group.transfers)
            passes.append(TransferPass(head, group.layout, chunk, step.liquid_class, step.touch_tip, step.blow_out,
                                       air_gap=step.air_gap))
        return passes


def _pack(volumes: List[float], capacity: float) -> Optional[List[List[int]]]:
    """Split consecutive *volumes* into chunks totalling at most *capacity*; ``None`` if one alone won't fit."""
    if capacity <= 0 or max(volumes) > capacity + 1e-9:
        return None
    chunks: List[List[int]] = []
    current: List[int] = []
    total = 0.0
    for index, volume in enumerate(volumes):
        if current and total + volume > capacity + 1e-9:
            chunks.append(current)
            current, total = [], 0.0
        current.append(index)
        total += volume
    chunks.append(current)
    return chunks


def _pass_count(volume: float, head: PipetteSpec, air_gap: float = 0) -> int:
    return max(1, math.ceil(volume / (head.max_volume - air_gap) - 1e-9))




def _has_dependency(transfers: List[Transfer]) -> bool:
    """``True`` if any transfer draws from a well another transfer in the list dispenses into."""
    dests = {t.dest for t in transfers}
    return any(t.source in dests for t in transfers)
