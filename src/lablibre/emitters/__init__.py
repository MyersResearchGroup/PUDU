"""Emitters compile a plan for one robot family into a bundle of files."""
from typing import Dict, Type

from lablibre.bundle import Bundle
from lablibre.emitters.base import Emitter, EmitError
from lablibre.emitters.manual import ManualEmitter
from lablibre.emitters.opentrons import OT2Emitter
from lablibre.plan import Plan
from lablibre.profile import RobotProfile

EMITTERS: Dict[str, Type[Emitter]] = {robot: cls for cls in (OT2Emitter, ManualEmitter) for robot in cls.robots}


def compile_plan(plan: Plan, profile: RobotProfile) -> Bundle:
    """Compile *plan* with the emitter for *profile*'s robot."""
    emitter = EMITTERS.get(profile.robot)
    if emitter is None:
        raise EmitError(f"No emitter for robot '{profile.robot}'. Available: {sorted(EMITTERS)}")
    return emitter().compile(plan, profile)
