"""
The protocol plan: labware, modules, starting liquids and an ordered list of steps.

A plan is what protocol logic (e.g. a PUDU plating protocol) produces and what
planners and emitters consume. It is fully robot-agnostic, so it can be
validated on a laptop before any robot file is written.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from lablibre.labware import Labware, LabwareFormat, WellRef
from lablibre.liquids import Liquid
from lablibre.modules import Module, ModuleKind
from lablibre.steps import (OFF_DECK, MoveLabware, SetLid, SetTemperature, Step,
                            ThermalProfile, TransferSet)

_VOLUME_TOLERANCE = 1e-6

# Which module kinds each module-targeting step may address.
_STEP_MODULE_KINDS = {
    SetTemperature: {ModuleKind.TEMPERATURE, ModuleKind.THERMOCYCLER, ModuleKind.HEATER_SHAKER},
    SetLid: {ModuleKind.THERMOCYCLER},
    ThermalProfile: {ModuleKind.THERMOCYCLER},
}


@dataclass(frozen=True)
class LoadLiquid:
    """*volume* µL of *liquid* placed in *well* before the protocol starts."""
    well: WellRef
    liquid: Liquid
    volume: float

    def __post_init__(self):
        if self.volume <= 0:
            raise ValueError(f"Loaded volume must be positive, got {self.volume} for {self.well}")


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    """A validation finding. ``step_index`` is ``None`` for problems outside the step list."""
    severity: Severity
    message: str
    step_index: Optional[int] = None

    def __str__(self) -> str:
        where = f"step {self.step_index}: " if self.step_index is not None else ""
        return f"[{self.severity.value}] {where}{self.message}"


class PlanError(ValueError):
    """Raised by :meth:`Plan.check` when a plan has errors."""

    def __init__(self, issues: List[Issue]):
        self.issues = issues
        super().__init__("Plan is invalid:\n" + "\n".join(f"  {issue}" for issue in issues))


@dataclass
class Plan:
    """
    A robot-agnostic protocol.

    Build a plan with :meth:`add_module`, :meth:`add_labware`, :meth:`load` and
    :meth:`add`, then call :meth:`check` before handing it to an emitter.

    Example::

        plan = Plan("plating")
        tubes = plan.add_labware("tubes", "tube_rack_15")
        dilution = plan.add_labware("dilution_plate", "plate_96", max_volume_ul=100)
        plan.load(tubes["A1"], Liquid("LB"), 10000)
        plan.add(TransferSet(transfers([(tubes["A1"], w, 18) for w in dilution.columns()[0]]),
                             tips=TipPolicy.ONE, label="Distribute LB"))
        plan.check()
    """
    name: str
    description: str = ""
    modules: Dict[str, Module] = field(default_factory=dict)
    labware: Dict[str, Labware] = field(default_factory=dict)
    contents: List[LoadLiquid] = field(default_factory=list)
    steps: List[Step] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_module(self, name: str, kind: Union[ModuleKind, str]) -> Module:
        """Declare a module the protocol needs and return it."""
        if name in self.modules:
            raise ValueError(f"Module '{name}' is already declared")
        module = Module(name, ModuleKind(kind))
        self.modules[name] = module
        return module

    def add_labware(self, name: str, format: Union[LabwareFormat, str], module: Optional[str] = None,
                    max_volume_ul: Optional[float] = None, description: str = "") -> Labware:
        """Declare a logical labware and return it; index the result by well name to get :class:`WellRef` objects."""
        if name in self.labware:
            raise ValueError(f"Labware '{name}' is already declared")
        if module is not None and module not in self.modules:
            raise ValueError(f"Labware '{name}' is placed on undeclared module '{module}'")
        labware = Labware(name, format, module=module, max_volume_ul=max_volume_ul, description=description)
        self.labware[name] = labware
        return labware

    def load(self, well: WellRef, liquid: Liquid, volume: float) -> LoadLiquid:
        """Record that *volume* µL of *liquid* is in *well* at the start of the protocol."""
        loaded = LoadLiquid(well, liquid, volume)
        self.contents.append(loaded)
        return loaded

    def add(self, step: Step) -> Step:
        """Append *step* to the plan and return it."""
        if not isinstance(step, Step):
            raise TypeError(f"Expected a Step, got {type(step).__name__}")
        self.steps.append(step)
        return step

    def validate(self) -> List[Issue]:
        """
        Check references, module kinds and liquid volumes without raising.

        Volumes are tracked through every transfer in order: a source that runs
        dry, a well used as a source before anything was put in it, or a
        destination that exceeds its labware's ``max_volume_ul`` are all errors.
        """
        issues: List[Issue] = []

        def error(message: str, index: Optional[int] = None):
            issues.append(Issue(Severity.ERROR, message, index))

        def warning(message: str, index: Optional[int] = None):
            issues.append(Issue(Severity.WARNING, message, index))

        def check_well(ref: WellRef, index: Optional[int]) -> bool:
            labware = self.labware.get(ref.labware)
            if labware is None:
                error(f"{ref} refers to undeclared labware '{ref.labware}'", index)
                return False
            if not labware.format.contains(ref.well):
                error(f"{ref} does not exist on {labware.format.name}", index)
                return False
            return True

        for labware in self.labware.values():
            if labware.module is not None and labware.module not in self.modules:
                error(f"Labware '{labware.name}' is placed on undeclared module '{labware.module}'")

        volumes: Dict[WellRef, float] = {}
        for loaded in self.contents:
            if check_well(loaded.well, None):
                volumes[loaded.well] = volumes.get(loaded.well, 0.0) + loaded.volume
                self._check_capacity(loaded.well, volumes[loaded.well], None, error)

        if not self.steps:
            warning("Plan has no steps")

        for index, step in enumerate(self.steps):
            allowed_kinds = _STEP_MODULE_KINDS.get(type(step))
            if allowed_kinds is not None:
                module = self.modules.get(step.module)
                if module is None:
                    error(f"{type(step).__name__} refers to undeclared module '{step.module}'", index)
                elif module.kind not in allowed_kinds:
                    error(f"{type(step).__name__} cannot target '{module.name}' ({module.kind.value})", index)

            if isinstance(step, MoveLabware):
                if step.labware not in self.labware:
                    error(f"MoveLabware refers to undeclared labware '{step.labware}'", index)
                if step.destination != OFF_DECK and step.destination not in self.modules:
                    error(f"MoveLabware destination '{step.destination}' is neither a declared module nor OFF_DECK", index)

            if isinstance(step, TransferSet):
                mixed_sources = set()
                for transfer in step.transfers:
                    source_ok = check_well(transfer.source, index)
                    dest_ok = check_well(transfer.dest, index)
                    if not (source_ok and dest_ok):
                        continue
                    available = volumes.get(transfer.source)
                    if available is None:
                        error(f"{transfer.source} is used as a source but nothing was loaded or transferred into it", index)
                        available = 0.0
                    elif available + _VOLUME_TOLERANCE < transfer.volume:
                        error(f"{transfer.source} needs {transfer.volume:g} µL but only {available:g} µL remain", index)
                    # When multi-dispensing, the source is mixed before each aspiration, not each dispense;
                    # checking its first use is the closest the plan can get without knowing the robot.
                    check_mix = not (step.multi_dispense and transfer.source in mixed_sources)
                    mixed_sources.add(transfer.source)
                    if check_mix and transfer.mix_before and transfer.mix_before.volume > available + _VOLUME_TOLERANCE:
                        warning(f"mix_before of {transfer.mix_before.volume:g} µL at {transfer.source} exceeds its {available:g} µL", index)
                    volumes[transfer.source] = max(available - transfer.volume, 0.0)
                    volumes[transfer.dest] = volumes.get(transfer.dest, 0.0) + transfer.volume
                    self._check_capacity(transfer.dest, volumes[transfer.dest], index, error)
                    if transfer.mix_after and transfer.mix_after.volume > volumes[transfer.dest] + _VOLUME_TOLERANCE:
                        warning(f"mix_after of {transfer.mix_after.volume:g} µL at {transfer.dest} exceeds its {volumes[transfer.dest]:g} µL", index)

        return issues

    def check(self) -> List[Issue]:
        """Validate the plan, raising :class:`PlanError` on errors; returns any warnings."""
        issues = self.validate()
        errors = [issue for issue in issues if issue.severity is Severity.ERROR]
        if errors:
            raise PlanError(errors)
        return issues

    def to_dict(self) -> Dict[str, Any]:
        """A JSON-serializable representation, for debugging and for inclusion in bundles."""
        return {
            "name": self.name,
            "description": self.description,
            "metadata": _to_jsonable(self.metadata),
            "modules": [_to_jsonable(m) for m in self.modules.values()],
            "labware": [_to_jsonable(l) for l in self.labware.values()],
            "contents": [_to_jsonable(c) for c in self.contents],
            "steps": [{"kind": step.kind, **_to_jsonable(step)} for step in self.steps],
        }

    def _check_capacity(self, well: WellRef, volume: float, index: Optional[int], error) -> None:
        capacity = self.labware[well.labware].max_volume_ul
        if capacity is not None and volume > capacity + _VOLUME_TOLERANCE:
            error(f"{well} would hold {volume:g} µL, above its {capacity:g} µL capacity", index)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, LabwareFormat):
        return value.name
    if isinstance(value, WellRef):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value
