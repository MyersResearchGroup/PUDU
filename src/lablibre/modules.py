"""Modules a plan needs (temperature block, thermocycler, ...), independent of robot model or position."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ModuleKind(str, Enum):
    TEMPERATURE = "temperature"
    THERMOCYCLER = "thermocycler"
    HEATER_SHAKER = "heater_shaker"
    MAGNETIC = "magnetic"


@dataclass(frozen=True)
class Module:
    """
    A module the plan uses, e.g. ``Module('cold_block', ModuleKind.TEMPERATURE)``.

    Whether the target robot actually has this module is decided by its profile.
    Emitters fall back to manual instructions when it doesn't (for example, a
    Hamilton without an on-deck thermocycler turns a thermal profile into a pause
    telling the user to move the plate).
    """
    name: str
    kind: ModuleKind

    def __post_init__(self):
        object.__setattr__(self, "kind", ModuleKind(self.kind))
