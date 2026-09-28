"""
Logical labware.

A protocol plan never names a robot-specific labware definition. It declares
*logical* labware ("dilution_plate", a 96-well plate) and refers to wells on
it. A :class:`~lablibre.profile.RobotProfile` later resolves each logical
labware to the robot's own definition (an Opentrons load name, a Hamilton
labware file, ...).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

_WELL_PATTERN = re.compile(r"^([A-Z])([1-9][0-9]?)$")


def parse_well(well: str) -> Tuple[int, int]:
    """
    Convert a well name to zero-based ``(row, column)`` indices.

    Args:
        well: Well name such as ``'A1'`` or ``'H12'``.

    Returns:
        Tuple of ``(row_index, column_index)``; ``'B3'`` -> ``(1, 2)``.

    Raises:
        ValueError: If the name is not a single row letter followed by a column number.
    """
    match = _WELL_PATTERN.match(well)
    if not match:
        raise ValueError(f"Invalid well name '{well}'. Expected a row letter and column number, e.g. 'A1'.")
    return ord(match.group(1)) - ord("A"), int(match.group(2)) - 1


def well_name(row: int, column: int) -> str:
    """Convert zero-based ``(row, column)`` indices to a well name; ``(1, 2)`` -> ``'B3'``."""
    if not 0 <= row < 26 or column < 0:
        raise ValueError(f"Well indices out of range: row={row}, column={column}")
    return f"{chr(ord('A') + row)}{column + 1}"


@dataclass(frozen=True)
class LabwareFormat:
    """
    Grid geometry shared by all labware of one format.

    Robots disagree on labware definitions but agree on geometry, and geometry
    is what the channel planner needs: an 8-channel fixed-pitch head can only
    work on whole columns of a format with 8 rows.
    """
    name: str
    rows: int
    columns: int

    def __post_init__(self):
        if self.rows < 1 or self.columns < 1:
            raise ValueError(f"Labware format '{self.name}' must have at least one row and one column")
        if self.rows > 26:
            raise ValueError(f"Labware format '{self.name}' has more than 26 rows, which is not supported")

    @property
    def well_count(self) -> int:
        return self.rows * self.columns

    def contains(self, well: str) -> bool:
        """Return ``True`` if *well* exists in this format."""
        try:
            row, column = parse_well(well)
        except ValueError:
            return False
        return row < self.rows and column < self.columns

    def wells(self) -> List[str]:
        """All well names in column-major order (A1, B1, ... H1, A2, ...), matching Opentrons ordering."""
        return [well_name(r, c) for c in range(self.columns) for r in range(self.rows)]

    def column_wells(self) -> List[List[str]]:
        """Well names grouped by column, top to bottom."""
        return [[well_name(r, c) for r in range(self.rows)] for c in range(self.columns)]


PLATE_96 = LabwareFormat("plate_96", rows=8, columns=12)
PLATE_384 = LabwareFormat("plate_384", rows=16, columns=24)
TUBE_RACK_24 = LabwareFormat("tube_rack_24", rows=4, columns=6)
TUBE_RACK_15 = LabwareFormat("tube_rack_15", rows=3, columns=5)
TUBE_RACK_6 = LabwareFormat("tube_rack_6", rows=2, columns=3)
RESERVOIR_12 = LabwareFormat("reservoir_12", rows=1, columns=12)
RESERVOIR_1 = LabwareFormat("reservoir_1", rows=1, columns=1)

FORMATS: Dict[str, LabwareFormat] = {
    fmt.name: fmt
    for fmt in (PLATE_96, PLATE_384, TUBE_RACK_24, TUBE_RACK_15, TUBE_RACK_6, RESERVOIR_12, RESERVOIR_1)
}


@dataclass(frozen=True)
class WellRef:
    """A reference to one well on one logical labware, e.g. ``WellRef('dilution_plate', 'A1')``."""
    labware: str
    well: str

    def __post_init__(self):
        parse_well(self.well)

    @property
    def row(self) -> int:
        return parse_well(self.well)[0]

    @property
    def column(self) -> int:
        return parse_well(self.well)[1]

    def __str__(self) -> str:
        return f"{self.labware}:{self.well}"


@dataclass(frozen=True)
class Labware:
    """
    A logical piece of labware used by a plan.

    Args:
        name: Unique name within the plan, e.g. ``'dilution_plate'``. Profiles can map
            this name to a specific robot labware definition.
        format: A :class:`LabwareFormat` or the name of one in :data:`FORMATS`.
        module: Name of the plan module this labware sits on (see :class:`~lablibre.modules.Module`),
            or ``None`` if it sits directly on the deck.
        max_volume_ul: Working volume of each well in µL. Used to catch overfilled wells
            during plan validation; ``None`` skips that check.
        description: Human-readable description for setup sheets.
    """
    name: str
    format: LabwareFormat
    module: Optional[str] = None
    max_volume_ul: Optional[float] = None
    description: str = ""

    def __post_init__(self):
        fmt: Union[LabwareFormat, str] = self.format
        if isinstance(fmt, str):
            if fmt not in FORMATS:
                raise ValueError(f"Unknown labware format '{fmt}'. Known formats: {sorted(FORMATS)}")
            object.__setattr__(self, "format", FORMATS[fmt])
        if self.max_volume_ul is not None and self.max_volume_ul <= 0:
            raise ValueError(f"Labware '{self.name}' max_volume_ul must be positive")

    def __getitem__(self, well: str) -> WellRef:
        if not self.format.contains(well):
            raise KeyError(f"Well '{well}' does not exist on '{self.name}' ({self.format.name})")
        return WellRef(self.name, well)

    def wells(self) -> List[WellRef]:
        """All wells in column-major order."""
        return [WellRef(self.name, w) for w in self.format.wells()]

    def columns(self) -> List[List[WellRef]]:
        """Wells grouped by column, top to bottom."""
        return [[WellRef(self.name, w) for w in column] for column in self.format.column_wells()]
