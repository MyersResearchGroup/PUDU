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
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from typing import Callable, Dict, Hashable, List, Optional, Sequence, Tuple

from lablibre.labware import LabwareFormat, WellRef
from lablibre.ops import DropTips, HeadLayout, Op, PickUpTips, TransferPass, TransferPlan
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
    head: PipetteSpec
    layout: HeadLayout
    transfers: List[Transfer]  # in channel order
    first_index: int           # position of the earliest member in the transfer set


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
        if step.ordering is Ordering.ANY:
            groups = self._group_any(items)
        else:
            groups = self._group_sequential(items)
        groups.sort(key=lambda g: g.first_index)
        return TransferPlan(step, tuple(self._to_ops(step, groups)))

    # --- grouping ------------------------------------------------------------------------

    def _group_any(self, items: List[_Indexed]) -> List[_Group]:
        groups: List[_Group] = []
        remaining = items
        for channels in self._fixed_channel_counts:
            found, remaining = self._extract_fixed_groups(remaining, channels)
            groups += found
        for head in self._independent_heads:
            eligible = [item for item in remaining if item[1].volume >= head.min_volume]
            remaining = [item for item in remaining if item[1].volume < head.min_volume]
            eligible.sort(key=lambda item: (item[1].dest.labware, item[1].dest.column, item[1].dest.row, item[0]))
            groups += self._pack_independent(eligible, head)
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
                first = items[position][1]
                head = next((h for h in self._independent_heads if first.volume >= h.min_volume), None)
                if head is not None:
                    group = self._pack_independent(items[position:], head, stop_at_first_misfit=True)[0]
            if group is None:
                group = self._single(items[position])
            groups.append(group)
            position += len(group.transfers)
        return groups

    def _extract_fixed_groups(self, items: List[_Indexed], channels: int) -> Tuple[List[_Group], List[_Indexed]]:
        """Pull every complete fixed-pitch group out of *items*; return the groups and the leftovers."""
        min_volume = min(h.min_volume for h in self._fixed_heads if h.channels == channels)
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
                groups.append(self._fixed_group(members, channels))
            for queue in by_lane.values():
                leftover.extend(queue)
        leftover.sort(key=lambda item: item[0])
        return groups, leftover

    def _fixed_window(self, window: Sequence[_Indexed], channels: int) -> Optional[_Group]:
        """A fixed-pitch group made of exactly the transfers in *window*, or ``None``."""
        if len(window) != channels:
            return None
        if any(t.volume < min(h.min_volume for h in self._fixed_heads if h.channels == channels) for _, t in window):
            return None
        slots = [self._fixed_slot(t, channels) for _, t in window]
        if any(slot is None for slot in slots) or len({key for key, _ in slots}) != 1:
            return None
        by_lane = {lane: item for (_, lane), item in zip(slots, window)}
        lanes = self._fixed_lanes(channels)
        if set(by_lane) != set(lanes) or _has_dependency([t for _, t in window]):
            return None
        return self._fixed_group([by_lane[lane] for lane in lanes], channels)

    def _fixed_group(self, members: List[_Indexed], channels: int) -> _Group:
        transfers = [t for _, t in members]
        head = self._choose_head([t.volume for t in transfers],
                                 lambda h: h.channels == channels and h.pitch is Pitch.FIXED)
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

    def _fits_independent(self, group: List[Transfer], transfer: Transfer, head: PipetteSpec) -> bool:
        if len(group) >= head.channels or transfer.volume < head.min_volume:
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
        head = self._choose_head([transfer.volume], lambda h: h.can_work_single)
        if head is None:
            usable = [f"{h.name} ({h.min_volume:g}-{h.max_volume:g} µL, {h.channels}ch)" for h in self.profile.pipettes]
            raise PlanningError(f"No pipette in profile '{self.profile.name}' can transfer {transfer.volume:g} µL "
                                f"from {transfer.source} to {transfer.dest} on its own. Available: {', '.join(usable)}")
        return _Group(head, HeadLayout.SINGLE, [transfer], index)

    def _choose_head(self, volumes: List[float], predicate: Callable[[PipetteSpec], bool]) -> Optional[PipetteSpec]:
        """The best head satisfying *predicate* that can handle every volume in *volumes*."""
        low, high = min(volumes), max(volumes)
        return next((h for h in self.profile.pipettes_for(high) if predicate(h) and h.min_volume <= low), None)

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
               transfer.volume, transfer.mix_before, transfer.mix_after)
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

    # --- tips and passes -------------------------------------------------------------------

    def _to_ops(self, step: TransferSet, groups: List[_Group]) -> List[Op]:
        ops: List[Op] = []
        held: Dict[str, Tuple[PipetteSpec, int, Tuple[WellRef, ...]]] = {}  # head name -> (head, tips, sources)
        for group in groups:
            head = group.head
            channels = len(group.transfers)
            sources = tuple(t.source for t in group.transfers)
            current = held.get(head.name)
            reuse = current is not None and current[1] == channels and (
                step.tips is TipPolicy.ONE or (step.tips is TipPolicy.PER_SOURCE and current[2] == sources))
            if not reuse:
                if current is not None:
                    ops.append(DropTips(head))
                ops.append(PickUpTips(head, channels))
                held[head.name] = (head, channels, sources)
            ops += self._passes(step, group)
            if step.tips is TipPolicy.NEW_EACH:
                ops.append(DropTips(head))
                del held[head.name]
        ops += [DropTips(head) for head, _, _ in held.values()]
        return ops

    def _passes(self, step: TransferSet, group: _Group) -> List[TransferPass]:
        """Split *group* into passes that fit the head; mix before the first pass and after the last."""
        head = group.head
        count = max(_pass_count(t.volume, head) for t in group.transfers)
        passes = []
        for number in range(count):
            chunk = tuple(replace(t, volume=t.volume / count,
                                  mix_before=_clamp_mix(t.mix_before, head) if number == 0 else None,
                                  mix_after=_clamp_mix(t.mix_after, head) if number == count - 1 else None)
                          for t in group.transfers)
            passes.append(TransferPass(head, group.layout, chunk, step.liquid_class, step.touch_tip, step.blow_out))
        return passes


def _pass_count(volume: float, head: PipetteSpec) -> int:
    return max(1, math.ceil(volume / head.max_volume - 1e-9))


def _clamp_mix(mix: Optional[Mix], head: PipetteSpec) -> Optional[Mix]:
    if mix is None or mix.volume <= head.max_volume:
        return mix
    return Mix(head.max_volume, mix.repetitions)


def _has_dependency(transfers: List[Transfer]) -> bool:
    """``True`` if any transfer draws from a well another transfer in the list dispenses into."""
    dests = {t.dest for t in transfers}
    return any(t.source in dests for t in transfers)
