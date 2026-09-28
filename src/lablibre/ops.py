"""
Pipetting operations: the planner's output and the emitters' input.

Where a :class:`~lablibre.steps.TransferSet` says *what* to move, operations
say *how* on a specific robot: which head, which channels act together, how
many passes, and when tips are picked up and dropped. Emitters translate
operations one-to-one into robot code or worklist rows and make no decisions
of their own.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Dict, List, Tuple

from lablibre.liquids import LiquidClass
from lablibre.profile import PipetteSpec
from lablibre.steps import Transfer, TransferSet


class HeadLayout(str, Enum):
    """How the channels in a pass are arranged."""
    SINGLE = "single"            # one channel, one well
    FIXED = "fixed"              # fixed-pitch head over a block of wells; transfers[0] is the top-left (anchor) well
    INDEPENDENT = "independent"  # independently spaced channels; transfers are in channel order


class Op:
    """Base class for pipetting operations. ``kind`` is a stable identifier emitters dispatch on."""
    kind: ClassVar[str] = "op"


@dataclass(frozen=True)
class PickUpTips(Op):
    """Pick up *channels* tips on *pipette*."""
    kind: ClassVar[str] = "pick_up_tips"

    pipette: PipetteSpec
    channels: int


@dataclass(frozen=True)
class DropTips(Op):
    """Drop the tips currently on *pipette*."""
    kind: ClassVar[str] = "drop_tips"

    pipette: PipetteSpec


@dataclass(frozen=True)
class TransferPass(Op):
    """
    One aspirate-and-dispense pass in which every channel acts at once.

    ``transfers[i]`` is the work of channel ``i``. Volumes are already split to
    fit the head, so every volume here is at most ``pipette.max_volume``.
    """
    kind: ClassVar[str] = "transfer_pass"

    pipette: PipetteSpec
    layout: HeadLayout
    transfers: Tuple[Transfer, ...]
    liquid_class: LiquidClass
    touch_tip: bool
    blow_out: bool


@dataclass(frozen=True)
class TransferPlan:
    """The operations that carry out one :class:`~lablibre.steps.TransferSet` on one robot."""
    step: TransferSet
    ops: Tuple[Op, ...]

    def passes(self) -> List[TransferPass]:
        return [op for op in self.ops if isinstance(op, TransferPass)]

    def tips_used(self) -> Dict[str, int]:
        """Number of tips consumed per pipette name."""
        used: Dict[str, int] = {}
        for op in self.ops:
            if isinstance(op, PickUpTips):
                used[op.pipette.name] = used.get(op.pipette.name, 0) + op.channels
        return used
