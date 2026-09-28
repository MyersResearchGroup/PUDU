"""
Robot profiles: the hardware a specific robot has.

The same plan compiles differently against different profiles. An OT-2 with a
p300 single-channel and one with a p300 8-channel get different (and each
correct) protocol files. Profiles are plain JSON so lab members can edit them
without touching code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from lablibre.labware import Labware
from lablibre.liquids import LiquidClass
from lablibre.modules import ModuleKind


class Pitch(str, Enum):
    """How the channels of a multichannel head are spaced."""
    FIXED = "fixed"              # channels locked to 9 mm spacing (Opentrons multi, 96-heads)
    INDEPENDENT = "independent"  # channels move independently (Hamilton STAR)


@dataclass(frozen=True)
class PipetteSpec:
    """
    One pipetting head on the robot.

    Args:
        name: Robot-specific model identifier, e.g. ``'p20_single_gen2'``.
        channels: Number of channels (1, 8, 96, ...).
        min_volume: Smallest volume the head can transfer in µL.
        max_volume: Largest single aspiration in µL (limited by pipette or tip).
        mount: Where the head is mounted (``'left'``/``'right'`` on Opentrons), if applicable.
        pitch: Channel spacing; irrelevant for single-channel heads.
        per_channel_volume: ``True`` if channels can aspirate different volumes in one pass.
        partial_pickup: ``True`` if a fixed-pitch multichannel head can pick up a single tip
            and work like a single-channel pipette.
        tip_rack: Robot-specific tip rack definition this head uses.
    """
    name: str
    channels: int
    min_volume: float
    max_volume: float
    mount: Optional[str] = None
    pitch: Pitch = Pitch.FIXED
    per_channel_volume: bool = False
    partial_pickup: bool = False
    tip_rack: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(self, "pitch", Pitch(self.pitch))
        if self.channels < 1:
            raise ValueError(f"Pipette '{self.name}' must have at least one channel")
        if not 0 < self.min_volume <= self.max_volume:
            raise ValueError(f"Pipette '{self.name}' needs 0 < min_volume <= max_volume, "
                             f"got {self.min_volume} and {self.max_volume}")

    @property
    def is_multichannel(self) -> bool:
        return self.channels > 1

    @property
    def can_work_single(self) -> bool:
        """``True`` if the head can transfer to one well at a time."""
        return self.channels == 1 or self.pitch is Pitch.INDEPENDENT or self.partial_pickup

    def can_transfer(self, volume: float) -> bool:
        """``True`` if *volume* is within range, allowing multiple aspirations above ``max_volume``."""
        return volume >= self.min_volume


@dataclass(frozen=True)
class ModuleSpec:
    """A module physically on the robot: its kind, robot-specific model, and deck location."""
    kind: ModuleKind
    model: str
    location: str

    def __post_init__(self):
        object.__setattr__(self, "kind", ModuleKind(self.kind))


@dataclass(frozen=True)
class RobotProfile:
    """
    Everything an emitter needs to know about one robot.

    Args:
        robot: Robot family the emitter targets, e.g. ``'ot2'``, ``'flex'``, ``'hamilton_star'``.
        name: Human-readable profile name, e.g. ``'Myers lab OT-2'``.
        pipettes: The heads installed on the robot.
        modules: The modules installed on the robot.
        labware: Maps a logical labware name (preferred) or a format name to a
            robot-specific labware definition.
        deck_slots: Free deck positions for labware and tip racks, in order of preference.
        has_gripper: ``True`` if the robot can move labware itself.
        liquid_classes: Robot-specific handling per :class:`~lablibre.liquids.LiquidClass`
            value, e.g. ``{"glycerol": {"aspiration_rate": 0.3}}``.
        options: Emitter-specific settings, e.g. ``{"api_level": "2.21"}``.
    """
    robot: str
    name: str
    pipettes: Tuple[PipetteSpec, ...]
    modules: Tuple[ModuleSpec, ...] = ()
    labware: Mapping[str, str] = field(default_factory=dict)
    deck_slots: Tuple[str, ...] = ()
    has_gripper: bool = False
    liquid_classes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "pipettes", tuple(self.pipettes))
        object.__setattr__(self, "modules", tuple(self.modules))
        object.__setattr__(self, "deck_slots", tuple(self.deck_slots))
        if not self.pipettes:
            raise ValueError(f"Profile '{self.name}' must define at least one pipette")
        unknown = set(self.liquid_classes) - {lc.value for lc in LiquidClass}
        if unknown:
            raise ValueError(f"Profile '{self.name}' has unknown liquid classes: {sorted(unknown)}")

    def module(self, kind: Union[ModuleKind, str]) -> Optional[ModuleSpec]:
        """The first installed module of *kind*, or ``None``."""
        kind = ModuleKind(kind)
        return next((m for m in self.modules if m.kind is kind), None)

    def has_module(self, kind: Union[ModuleKind, str]) -> bool:
        return self.module(kind) is not None

    def resolve_labware(self, labware: Labware, module_kind: Union[ModuleKind, str, None] = None) -> str:
        """
        The robot definition for *labware*.

        Looks up, in order: the labware's name, ``'<format>@<module kind>'`` when it sits on
        a module (e.g. ``'tube_rack_24@temperature'`` for an aluminum block), then the format.
        """
        keys = [labware.name]
        if module_kind is not None:
            keys.append(f"{labware.format.name}@{ModuleKind(module_kind).value}")
        keys.append(labware.format.name)
        for key in keys:
            if key in self.labware:
                return self.labware[key]
        raise KeyError(f"Profile '{self.name}' has no labware definition for any of {keys}. "
                       f"Add one to the profile's 'labware' map.")

    def pipettes_for(self, volume: float) -> List[PipetteSpec]:
        """Heads able to transfer *volume*, preferring the fewest aspirations, then the smallest head."""
        usable = [p for p in self.pipettes if p.can_transfer(volume)]
        return sorted(usable, key=lambda p: (-(-volume // p.max_volume), p.max_volume))

    def liquid_handling(self, liquid_class: Union[LiquidClass, str]) -> Mapping[str, Any]:
        """Robot-specific handling for *liquid_class*, or an empty mapping for robot defaults."""
        return self.liquid_classes.get(LiquidClass(liquid_class).value, {})

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RobotProfile":
        data = dict(data)
        data["pipettes"] = [PipetteSpec(**p) for p in data.get("pipettes", [])]
        data["modules"] = [ModuleSpec(**m) for m in data.get("modules", [])]
        return cls(**data)

    @classmethod
    def from_json(cls, path: Union[str, Path]) -> "RobotProfile":
        with open(path) as f:
            return cls.from_dict(json.load(f))


def builtin_profiles() -> List[str]:
    """Names of the profiles shipped with LabLibre."""
    return sorted(p.name[:-len(".json")] for p in resources.files("lablibre.profiles").iterdir()
                  if p.name.endswith(".json"))


def load_profile(name: str) -> RobotProfile:
    """Load a built-in profile by name (see :func:`builtin_profiles`)."""
    resource = resources.files("lablibre.profiles") / f"{name}.json"
    if not resource.is_file():
        raise KeyError(f"No built-in profile '{name}'. Available: {builtin_profiles()}")
    return RobotProfile.from_dict(json.loads(resource.read_text()))
