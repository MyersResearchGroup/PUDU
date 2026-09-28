"""Liquids and liquid classes."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class LiquidClass(str, Enum):
    """
    How a liquid should be pipetted, independent of robot.

    Each robot profile maps these to its own handling: flow rates and touch-tip
    on Opentrons, a named liquid class on Hamilton.
    """
    AQUEOUS = "aqueous"      # water, buffers, media, diluted DNA
    GLYCEROL = "glycerol"    # enzyme stocks in ~50% glycerol (ligase, restriction enzymes)
    CELLS = "cells"          # competent cells and cultures; gentle handling


@dataclass(frozen=True)
class Liquid:
    """A named liquid loaded onto the deck at the start of a protocol."""
    name: str
    liquid_class: LiquidClass = LiquidClass.AQUEOUS
    description: str = ""
    color: Optional[str] = None  # hex display color, e.g. "#D2B48C"

    def __post_init__(self):
        object.__setattr__(self, "liquid_class", LiquidClass(self.liquid_class))
