"""Emitters compile a plan for one robot family into a bundle of files."""
import warnings
from typing import Dict, Type

from lablibre.bundle import Bundle
from lablibre.emitters.base import Emitter, EmitError
from lablibre.emitters.manual import ManualEmitter
from lablibre.emitters.opentrons import OT2Emitter
from lablibre.plan import Plan
from lablibre.profile import RobotProfile

class LabLibreWarning(UserWarning):
    """Something in a compiled protocol worth checking before a run, e.g. a mix reduced to fit a pipette."""


EMITTERS: Dict[str, Type[Emitter]] = {robot: cls for cls in (OT2Emitter, ManualEmitter) for robot in cls.robots}


def compile_plan(plan: Plan, profile: RobotProfile) -> Bundle:
    """
    Compile *plan* with the emitter for *profile*'s robot.

    Each of the bundle's warnings is also raised as a :class:`LabLibreWarning`, so
    scripts show them in the terminal.
    """
    emitter = EMITTERS.get(profile.robot)
    if emitter is None:
        raise EmitError(f"No emitter for robot '{profile.robot}'. Available: {sorted(EMITTERS)}")
    bundle = emitter().compile(plan, profile)
    for message in bundle.warnings:
        warnings.warn(message, LabLibreWarning, stacklevel=2)
    return bundle
