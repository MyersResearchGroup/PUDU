"""
Robot-agnostic protocol steps.

Steps describe *what* should happen, never *how* a particular robot does it.
There are no pipettes, channels, tips or deck positions here: those are chosen
later by a planner (from the robot profile) and written out by an emitter.

Steps in a plan run in order. Within a :class:`TransferSet`, the ``ordering``
field says whether the individual transfers must also run in order or may be
regrouped (for example, eight at a time on an 8-channel head).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Dict, List, Optional, Sequence, Tuple

from lablibre.labware import WellRef
from lablibre.liquids import LiquidClass

OFF_DECK = "off_deck"


class Ordering(str, Enum):
    SEQUENTIAL = "sequential"  # transfers run exactly in the listed order
    ANY = "any"                # the planner may reorder and parallelize


class TipPolicy(str, Enum):
    NEW_EACH = "new_each"      # fresh tip for every transfer
    PER_SOURCE = "per_source"  # reuse a tip while drawing from the same source (distributing)
    ONE = "one"                # a single tip for the whole set; any carryover is accepted


@dataclass(frozen=True)
class Mix:
    """Pipette-mix *volume* µL, *repetitions* times."""
    volume: float
    repetitions: int = 3

    def __post_init__(self):
        if self.volume <= 0:
            raise ValueError(f"Mix volume must be positive, got {self.volume}")
        if self.repetitions < 1:
            raise ValueError(f"Mix repetitions must be at least 1, got {self.repetitions}")


@dataclass(frozen=True)
class Transfer:
    """
    Move *volume* µL from *source* to *dest*, optionally mixing before aspirating or after dispensing.

    Args:
        dispense_below_top: Dispense this many mm below the top of the destination well
            instead of at the robot's default height, e.g. ``8`` to spot onto agar without
            touching it. ``None`` uses the default.
    """
    source: WellRef
    dest: WellRef
    volume: float
    mix_before: Optional[Mix] = None
    mix_after: Optional[Mix] = None
    dispense_below_top: Optional[float] = None

    def __post_init__(self):
        if self.volume <= 0:
            raise ValueError(f"Transfer volume must be positive, got {self.volume} ({self.source} -> {self.dest})")
        if self.dispense_below_top is not None and self.dispense_below_top < 0:
            raise ValueError(f"dispense_below_top must be zero or positive, got {self.dispense_below_top}")


class Step:
    """Base class for all plan steps. ``kind`` is a stable identifier emitters dispatch on."""
    kind: ClassVar[str] = "step"


@dataclass(frozen=True)
class TransferSet(Step):
    """
    A group of transfers that share handling.

    This is the unit the channel planner works on: a single transfer can never
    use an 8- or 96-channel head, a set can.

    Args:
        transfers: The transfers in this set.
        liquid_class: How the transferred liquid should be handled.
        ordering: Whether the transfers must run in the listed order.
        tips: When tips are replaced.
        touch_tip: Touch the tip to the destination well wall after dispensing.
        blow_out: Blow out after each dispense (after the last one when multi-dispensing).
        label: Short description for comments and setup sheets, e.g. ``'Distribute LB'``.
        multi_dispense: Let one aspiration serve several dispenses from the same source.
            Needs a tip policy that reuses tips and no mixing. Dispenses may be smaller
            than the pipette's minimum volume as long as each aspiration is not.
        disposal_volume: Extra µL aspirated with each multi-dispense aspiration and
            discarded afterwards, which evens out the dispensed volumes.
    """
    kind: ClassVar[str] = "transfer_set"

    transfers: Tuple[Transfer, ...]
    liquid_class: LiquidClass = LiquidClass.AQUEOUS
    ordering: Ordering = Ordering.ANY
    tips: TipPolicy = TipPolicy.NEW_EACH
    touch_tip: bool = False
    blow_out: bool = True
    label: str = ""
    multi_dispense: bool = False
    disposal_volume: float = 0

    def __post_init__(self):
        object.__setattr__(self, "transfers", tuple(self.transfers))
        object.__setattr__(self, "liquid_class", LiquidClass(self.liquid_class))
        object.__setattr__(self, "ordering", Ordering(self.ordering))
        object.__setattr__(self, "tips", TipPolicy(self.tips))
        if not self.transfers:
            raise ValueError("TransferSet must contain at least one transfer")
        if self.tips is TipPolicy.PER_SOURCE and any(t.mix_after for t in self.transfers):
            # Mixing in the destination wets the tip with that well's contents, and
            # PER_SOURCE would silently carry it back into the source. TipPolicy.ONE
            # is allowed: it is an explicit choice to use one tip for the whole set.
            raise ValueError(f"TransferSet '{self.label}': mix_after cannot be combined with tips=TipPolicy.PER_SOURCE")
        if self.disposal_volume < 0:
            raise ValueError(f"TransferSet '{self.label}': disposal_volume must be zero or positive")
        if self.disposal_volume and not self.multi_dispense:
            raise ValueError(f"TransferSet '{self.label}': disposal_volume only applies with multi_dispense=True")
        if self.multi_dispense:
            if self.tips is TipPolicy.NEW_EACH:
                raise ValueError(f"TransferSet '{self.label}': multi_dispense needs a tip policy that reuses tips")
            if any(t.mix_before or t.mix_after for t in self.transfers):
                raise ValueError(f"TransferSet '{self.label}': multi_dispense cannot be combined with mixing")

    def sources(self) -> List[WellRef]:
        """Distinct source wells, in first-use order."""
        return list(dict.fromkeys(t.source for t in self.transfers))

    def destinations(self) -> List[WellRef]:
        """Distinct destination wells, in first-use order."""
        return list(dict.fromkeys(t.dest for t in self.transfers))

    def volume_by_source(self) -> Dict[WellRef, float]:
        """Total volume drawn from each source well."""
        totals: Dict[WellRef, float] = {}
        for t in self.transfers:
            totals[t.source] = totals.get(t.source, 0.0) + t.volume
        return totals


@dataclass(frozen=True)
class SetTemperature(Step):
    """Set a temperature or thermocycler block to *celsius*, or turn it off when *celsius* is ``None``."""
    kind: ClassVar[str] = "set_temperature"

    module: str
    celsius: Optional[float]


@dataclass(frozen=True)
class SetLid(Step):
    """Open or close a thermocycler lid. Robots without an on-deck thermocycler ignore this."""
    kind: ClassVar[str] = "set_lid"

    module: str
    open: bool


@dataclass(frozen=True)
class ThermalStep:
    """Hold at *celsius* for *seconds*."""
    celsius: float
    seconds: float

    def __post_init__(self):
        if self.seconds <= 0:
            raise ValueError(f"Thermal step hold time must be positive, got {self.seconds}")


@dataclass(frozen=True)
class ThermalProfile(Step):
    """
    Run a cycling profile on the plate held by a thermocycler.

    Args:
        module: Name of the thermocycler module in the plan.
        steps: The steps of one cycle.
        repetitions: Number of cycles.
        lid_celsius: Lid temperature during the profile; ``None`` leaves it unchanged.
        block_max_volume: Largest well volume in µL, used by some thermocyclers to
            compensate for thermal mass.
        hold_celsius: Block temperature to hold after the profile; ``None`` for no hold.
        label: Short description, e.g. ``'Golden Gate cycling'``.
    """
    kind: ClassVar[str] = "thermal_profile"

    module: str
    steps: Tuple[ThermalStep, ...]
    repetitions: int = 1
    lid_celsius: Optional[float] = None
    block_max_volume: Optional[float] = None
    hold_celsius: Optional[float] = None
    label: str = ""

    def __post_init__(self):
        object.__setattr__(self, "steps", tuple(self.steps))
        if not self.steps:
            raise ValueError("ThermalProfile must contain at least one step")
        if self.repetitions < 1:
            raise ValueError(f"ThermalProfile repetitions must be at least 1, got {self.repetitions}")

    @property
    def duration_seconds(self) -> float:
        return sum(s.seconds for s in self.steps) * self.repetitions


@dataclass(frozen=True)
class MoveLabware(Step):
    """Move a labware onto a plan module, or off the deck with :data:`OFF_DECK`."""
    kind: ClassVar[str] = "move_labware"

    labware: str
    destination: str


@dataclass(frozen=True)
class Delay(Step):
    """Wait *seconds* without user interaction."""
    kind: ClassVar[str] = "delay"

    seconds: float
    message: str = ""

    def __post_init__(self):
        if self.seconds <= 0:
            raise ValueError(f"Delay must be positive, got {self.seconds}")


@dataclass(frozen=True)
class Pause(Step):
    """Stop until the user resumes, showing *message*."""
    kind: ClassVar[str] = "pause"

    message: str


@dataclass(frozen=True)
class Comment(Step):
    """A note in the run log; no physical action."""
    kind: ClassVar[str] = "comment"

    text: str


def transfers(pairs: Sequence[Tuple[WellRef, WellRef, float]], **kwargs) -> Tuple[Transfer, ...]:
    """Build transfers from ``(source, dest, volume)`` tuples; *kwargs* (e.g. ``mix_after``) apply to all."""
    return tuple(Transfer(source, dest, volume, **kwargs) for source, dest, volume in pairs)
