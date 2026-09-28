"""Shared pieces for emitters: the :class:`Emitter` interface, errors, and code-writing helpers."""
from __future__ import annotations

import keyword
import re
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import ClassVar, Dict, Iterator, List, Set, Tuple

from lablibre.bundle import Bundle
from lablibre.ops import TransferPlan
from lablibre.plan import Plan
from lablibre.profile import RobotProfile


class EmitError(ValueError):
    """Raised when a plan cannot be written for a robot, e.g. it needs hardware the emitter doesn't support."""


class Emitter(ABC):
    """Compiles a plan for one robot family into a :class:`~lablibre.bundle.Bundle`."""

    robots: ClassVar[Tuple[str, ...]] = ()

    @abstractmethod
    def compile(self, plan: Plan, profile: RobotProfile) -> Bundle:
        """Validate *plan*, plan its transfers for *profile*, and return the files to run it."""

    @staticmethod
    def _warnings(plan: Plan, plan_warnings, planned: Dict[int, TransferPlan]) -> List[str]:
        """Plan-validation warnings and planner warnings, each prefixed with its step number when it has one."""
        messages = [f"Step {w.step_index + 1}: {w.message}" if w.step_index is not None else w.message
                    for w in plan_warnings]
        for index, transfer_plan in sorted(planned.items()):
            messages += [f"Step {index + 1}: {w}" for w in transfer_plan.warnings]
        return messages

    def _check_profile(self, profile: RobotProfile) -> None:
        if profile.robot not in self.robots:
            raise EmitError(f"{type(self).__name__} targets {list(self.robots)}, "
                            f"but profile '{profile.name}' is for '{profile.robot}'")


class CodeWriter:
    """Accumulates indented lines of source code."""

    def __init__(self, indent: str = "    "):
        self._lines: List[str] = []
        self._indent = indent
        self._level = 0

    def line(self, text: str = "") -> None:
        self._lines.append(f"{self._indent * self._level}{text}" if text else "")

    def lines(self, text: str) -> None:
        """Add a multi-line block, keeping its relative indentation."""
        for line in text.strip("\n").splitlines():
            self.line(line)

    @contextmanager
    def indented(self) -> Iterator[None]:
        self._level += 1
        try:
            yield
        finally:
            self._level -= 1

    def text(self) -> str:
        return "\n".join(self._lines) + "\n"


class Namer:
    """Turns names like ``'Dilution Plate #1'`` into unique Python identifiers."""

    def __init__(self, reserved: Set[str] = frozenset()):
        self._used = set(reserved)

    def __call__(self, name: str, prefix: str = "") -> str:
        base = re.sub(r"\W+", "_", f"{prefix}{name}".strip().lower()).strip("_") or "item"
        if base[0].isdigit() or keyword.iskeyword(base):
            base = f"_{base}"
        candidate, counter = base, 2
        while candidate in self._used:
            candidate = f"{base}_{counter}"
            counter += 1
        self._used.add(candidate)
        return candidate


def slugify(name: str) -> str:
    """A filesystem-friendly version of *name*: ``'GFP Plating'`` -> ``'gfp_plating'``."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "protocol"


def format_number(value: float) -> str:
    """Numbers as short, exact-enough literals: ``18.0`` -> ``'18'``, ``100/6`` -> ``'16.667'``."""
    rounded = round(value, 3)
    return str(int(rounded)) if rounded == int(rounded) else repr(rounded)
