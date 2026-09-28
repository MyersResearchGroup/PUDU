"""
LabLibre: compile robot-agnostic lab protocols into standalone files for specific robots.

Protocol logic builds a :class:`Plan` from logical labware, liquids and steps.
A :class:`RobotProfile` describes one robot's hardware. The
:class:`ChannelPlanner` turns the plan's transfers into pipetting operations
for that hardware, and emitters (added separately) write those operations into
a :class:`Bundle` of files that run on the robot with nothing extra installed on it.
"""
from lablibre.bundle import Bundle, BundleFile, FileRole
from lablibre.labware import (FORMATS, PLATE_96, PLATE_384, RESERVOIR_1, RESERVOIR_12, TUBE_RACK_6,
                              TUBE_RACK_15, TUBE_RACK_24, Labware, LabwareFormat, WellRef,
                              parse_well, well_name)
from lablibre.liquids import Liquid, LiquidClass
from lablibre.modules import Module, ModuleKind
from lablibre.ops import DropTips, HeadLayout, Op, PickUpTips, TransferPass, TransferPlan
from lablibre.plan import Issue, LoadLiquid, Plan, PlanError, Severity
from lablibre.planner import ChannelPlanner, PlanningError
from lablibre.profile import ModuleSpec, Pitch, PipetteSpec, RobotProfile, builtin_profiles, load_profile
from lablibre.steps import (OFF_DECK, Comment, Delay, Mix, MoveLabware, Ordering, Pause, SetLid,
                            SetTemperature, Step, ThermalProfile, ThermalStep, TipPolicy, Transfer,
                            TransferSet, transfers)
