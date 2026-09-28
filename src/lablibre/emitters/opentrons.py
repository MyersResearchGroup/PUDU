"""
OT-2 emitter: writes a standalone Opentrons protocol plus a setup sheet.

The protocol file imports only ``opentrons``, so it can be uploaded through
the Opentrons App without installing PUDU or LabLibre on the robot. All
decisions (deck layout, pipette choice, channel grouping, tips) are made here
on the laptop; the file itself is a straight-line list of commands that lab
members can read and edit.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from lablibre.bundle import Bundle, FileRole
from lablibre.emitters.base import CodeWriter, Emitter, EmitError, Namer, format_number, slugify
from lablibre.labware import Labware, WellRef
from lablibre.liquids import Liquid
from lablibre.modules import ModuleKind
from lablibre.ops import DistributePass, DropTips, PickUpTips, TransferPass, TransferPlan
from lablibre.plan import Plan
from lablibre.planner import ChannelPlanner
from lablibre.profile import ModuleSpec, Pitch, PipetteSpec, RobotProfile
from lablibre.steps import (OFF_DECK, Comment, Delay, MoveLabware, Pause, SetLid, SetTemperature, Step,
                            ThermalProfile, TransferSet)

DEFAULT_API_LEVEL = "2.21"
TIPS_PER_RACK = 96
HANDLING_KEYS = {"aspiration_rate", "dispense_rate"}

LIQUID_COLORS = ["#4040BF", "#BF4040", "#40BF40", "#A640BF", "#BFBF40", "#BF7340",
                 "#40BFBF", "#BF40A6", "#73BF40", "#4073BF", "#BF8C40", "#40BF73"]

# Helpers emitted into protocols that use them; the generated code needs nothing else.
_MOVE_HELPER = '''
def _move(pipette, source, dest, volume, aspiration_rate=1.0, dispense_rate=1.0,
          mix_before=None, mix_after=None, touch_tip=False, blow_out=True, dispense_below_top=None):
    """Aspirate from source and dispense into dest. Mixes are (volume, repetitions)."""
    if mix_before:
        pipette.mix(mix_before[1], mix_before[0], source)
    pipette.aspirate(volume, source, rate=aspiration_rate)
    target = dest if dispense_below_top is None else dest.top(-dispense_below_top)
    pipette.dispense(volume, target, rate=dispense_rate)
    if mix_after:
        pipette.mix(mix_after[1], mix_after[0], dest)
    if blow_out:
        pipette.blow_out()
    if touch_tip:
        pipette.touch_tip(dest)
'''

_DISTRIBUTE_HELPER = '''
def _distribute(pipette, source, dispenses, trash, disposal=0, aspiration_rate=1.0, dispense_rate=1.0,
                touch_tip=False, blow_out=True):
    """Aspirate once from source, dispense each (location, volume), then discard what is left in the trash."""
    pipette.aspirate(sum(volume for _, volume in dispenses) + disposal, source, rate=aspiration_rate)
    for dest, volume in dispenses:
        pipette.dispense(volume, dest, rate=dispense_rate)
        if touch_tip:
            pipette.touch_tip()
    if disposal or blow_out:
        pipette.blow_out(trash)
'''

_OT2_MODULE_KINDS = {ModuleKind.TEMPERATURE, ModuleKind.THERMOCYCLER}


@dataclass
class _LoadedPipette:
    spec: PipetteSpec
    var: str
    tips: int
    rack_vars: List[str] = field(default_factory=list)
    rack_slots: List[str] = field(default_factory=list)


@dataclass
class _Deck:
    """Where everything goes and what it is called in the generated code."""
    modules: Dict[str, Optional[ModuleSpec]] = field(default_factory=dict)  # plan module -> installed module
    module_vars: Dict[str, str] = field(default_factory=dict)
    labware_locations: Dict[str, str] = field(default_factory=dict)  # labware -> slot, or plan module name
    labware_defs: Dict[str, str] = field(default_factory=dict)
    labware_vars: Dict[str, str] = field(default_factory=dict)
    pipettes: Dict[str, _LoadedPipette] = field(default_factory=dict)  # pipette name -> loaded pipette
    liquid_vars: Dict[Liquid, str] = field(default_factory=dict)


class OT2Emitter(Emitter):
    """
    Compile a plan into an OT-2 protocol bundle.

    The bundle contains ``<name>.py`` (upload to the Opentrons App),
    ``<name>_setup.md`` (deck map, liquids to load, manual steps) and
    ``<name>_plan.json`` (the plan, for reference).

    Supports single-channel and fixed-pitch 8-channel pipettes, the temperature
    module and the thermocycler. Plan modules the profile doesn't have become
    pauses asking the user to do that step by hand.
    """

    robots = ("ot2",)
    robot_type = "OT-2"

    def compile(self, plan: Plan, profile: RobotProfile) -> Bundle:
        self._check_profile(profile)
        self._check_pipettes(profile)
        plan_warnings = plan.check()
        planned = ChannelPlanner(profile, plan).plan_all()
        self._check_supported(planned)
        handling = self._handling(profile)
        deck = self._layout(plan, profile, planned)

        slug = slugify(plan.name)
        bundle = Bundle(target=profile.name, warnings=self._warnings(plan, plan_warnings, planned))
        bundle.add(f"{slug}.py", self._render(plan, profile, planned, deck, handling), FileRole.PROTOCOL)
        bundle.add(f"{slug}_setup.md", self._setup_sheet(plan, profile, planned, deck, f"{slug}.py", bundle.warnings),
                   FileRole.SETUP)
        bundle.add(f"{slug}_plan.json", json.dumps(plan.to_dict(), indent=2) + "\n", FileRole.DATA)
        return bundle

    # --- checks ----------------------------------------------------------------------------

    def _check_pipettes(self, profile: RobotProfile) -> None:
        mounts: Dict[str, str] = {}
        for spec in profile.pipettes:
            if spec.pitch is Pitch.INDEPENDENT and spec.is_multichannel:
                raise EmitError(f"Pipette '{spec.name}' has independent channels, which the OT-2 lacks")
            if spec.channels not in (1, 8):
                raise EmitError(f"The OT-2 emitter supports 1- and 8-channel pipettes, "
                                f"not '{spec.name}' ({spec.channels} channels)")
            if spec.mount not in ("left", "right"):
                raise EmitError(f"Pipette '{spec.name}' needs mount 'left' or 'right' on the OT-2, got {spec.mount!r}")
            if spec.mount in mounts:
                raise EmitError(f"Pipettes '{mounts[spec.mount]}' and '{spec.name}' are both on the {spec.mount} mount")
            mounts[spec.mount] = spec.name

    def _check_supported(self, planned: Dict[int, TransferPlan]) -> None:
        for index, transfer_plan in planned.items():
            for op in transfer_plan.ops:
                if isinstance(op, PickUpTips) and op.channels != op.pipette.channels:
                    raise EmitError(f"Step {index}: partial tip pickup on '{op.pipette.name}' is not supported on the OT-2")

    def _handling(self, profile: RobotProfile) -> Dict[str, Dict[str, float]]:
        for liquid_class, settings in profile.liquid_classes.items():
            unknown = set(settings) - HANDLING_KEYS
            if unknown:
                raise EmitError(f"Profile '{profile.name}' liquid class '{liquid_class}' has settings the OT-2 "
                                f"emitter doesn't understand: {sorted(unknown)}. Supported: {sorted(HANDLING_KEYS)}")
        return {name: dict(settings) for name, settings in profile.liquid_classes.items()}

    # --- deck layout -----------------------------------------------------------------------

    def _layout(self, plan: Plan, profile: RobotProfile, planned: Dict[int, TransferPlan]) -> _Deck:
        deck = _Deck()
        namer = Namer(reserved={"protocol", "protocol_api", "run", "metadata", "requirements", "HANDLING", "_move"})

        taken: List[ModuleSpec] = []
        for module in plan.modules.values():
            installed = next((m for m in profile.modules if m.kind is module.kind and m not in taken), None)
            if installed is not None and installed.kind not in _OT2_MODULE_KINDS:
                raise EmitError(f"The OT-2 emitter doesn't support {installed.kind.value} modules yet")
            if installed is not None:
                taken.append(installed)
                deck.module_vars[module.name] = namer(module.name)
            deck.modules[module.name] = installed

        overrides: Dict[str, str] = dict(profile.options.get("locations", {}))
        free = [slot for slot in profile.deck_slots if slot not in overrides.values()]
        occupied_modules: Dict[str, str] = {}

        def next_slot(what: str) -> str:
            if not free:
                raise EmitError(f"Out of deck slots on '{profile.name}' while placing {what}. "
                                f"Free slots were {list(profile.deck_slots)}.")
            return free.pop(0)

        for labware in plan.labware.values():
            if labware.module is not None and deck.modules.get(labware.module) is not None:
                if labware.module in occupied_modules:
                    raise EmitError(f"'{labware.name}' and '{occupied_modules[labware.module]}' "
                                    f"are both placed on module '{labware.module}'")
                occupied_modules[labware.module] = labware.name
                location = labware.module
            elif labware.name in overrides:
                location = overrides[labware.name]
            else:
                location = next_slot(f"'{labware.name}'")
            deck.labware_locations[labware.name] = location
            installed = deck.modules.get(labware.module) if labware.module else None
            deck.labware_defs[labware.name] = profile.resolve_labware(labware, installed.kind if installed else None)
            deck.labware_vars[labware.name] = namer(labware.name)

        tips: Dict[str, int] = {}
        for transfer_plan in planned.values():
            for name, count in transfer_plan.tips_used().items():
                tips[name] = tips.get(name, 0) + count
        for spec in profile.pipettes:
            if spec.name not in tips:
                continue
            if not spec.tip_rack:
                raise EmitError(f"Pipette '{spec.name}' has no tip_rack in profile '{profile.name}'")
            loaded = _LoadedPipette(spec, namer(spec.name), tips[spec.name])
            for number in range(1, math.ceil(tips[spec.name] / TIPS_PER_RACK) + 1):
                loaded.rack_slots.append(next_slot(f"tip rack {number} for '{spec.name}'"))
                loaded.rack_vars.append(namer(f"tips_{spec.name}_{number}"))
            deck.pipettes[spec.name] = loaded

        for loaded_liquid in plan.contents:
            if loaded_liquid.liquid not in deck.liquid_vars:
                deck.liquid_vars[loaded_liquid.liquid] = namer(loaded_liquid.liquid.name, prefix="liquid_")
        return deck

    # --- protocol file ---------------------------------------------------------------------

    def _render(self, plan: Plan, profile: RobotProfile, planned: Dict[int, TransferPlan], deck: _Deck,
                handling: Dict[str, Dict[str, float]]) -> str:
        api_level = str(profile.options.get("api_level", DEFAULT_API_LEVEL))
        out = CodeWriter()
        out.line('"""')
        out.line(f"{plan.name}")
        out.line("")
        out.line(f"Generated by LabLibre for profile '{profile.name}'.")
        out.line("Standalone: needs only the Opentrons software already on the robot.")
        out.line('"""')
        out.line("from opentrons import protocol_api")
        out.line()
        out.line(f"metadata = {repr(self._metadata(plan))}")
        out.line(f"requirements = {repr({'robotType': self.robot_type, 'apiLevel': api_level})}")
        out.line()
        out.line("# Pipetting settings per liquid class (from the robot profile)")
        out.line(f"HANDLING = {repr(handling)}")
        out.line()
        used = {type(op) for transfer_plan in planned.values() for op in transfer_plan.ops}
        for op_type, helper in ((TransferPass, _MOVE_HELPER), (DistributePass, _DISTRIBUTE_HELPER)):
            if op_type in used:
                out.lines(helper)
                out.line()
        out.line()
        out.line("def run(protocol: protocol_api.ProtocolContext):")
        with out.indented():
            self._render_deck(out, plan, deck)
            for index, step in enumerate(plan.steps):
                out.line()
                self._render_step(out, index, step, planned.get(index), deck, handling)
        return out.text()

    def _render_deck(self, out: CodeWriter, plan: Plan, deck: _Deck) -> None:
        if deck.module_vars:
            out.line("# Modules")
            for name, var in deck.module_vars.items():
                spec = deck.modules[name]
                if spec.kind is ModuleKind.THERMOCYCLER:
                    out.line(f"{var} = protocol.load_module({spec.model!r})")
                else:
                    out.line(f"{var} = protocol.load_module({spec.model!r}, {spec.location!r})")
            out.line()

        out.line("# Labware")
        for name, labware in plan.labware.items():
            location = deck.labware_locations[name]
            var, definition = deck.labware_vars[name], deck.labware_defs[name]
            if location in deck.module_vars:
                out.line(f"{var} = {deck.module_vars[location]}.load_labware({definition!r}, label={name!r})")
            else:
                out.line(f"{var} = protocol.load_labware({definition!r}, {location!r}, label={name!r})")

        if deck.pipettes:
            out.line()
            out.line("# Tip racks and pipettes")
            for loaded in deck.pipettes.values():
                for var, slot in zip(loaded.rack_vars, loaded.rack_slots):
                    out.line(f"{var} = protocol.load_labware({loaded.spec.tip_rack!r}, {slot!r})")
                racks = ", ".join(loaded.rack_vars)
                out.line(f"{loaded.var} = protocol.load_instrument({loaded.spec.name!r}, "
                         f"{loaded.spec.mount!r}, tip_racks=[{racks}])")

        if plan.contents:
            out.line()
            out.line("# Liquids")
            for index, (liquid, var) in enumerate(deck.liquid_vars.items()):
                color = liquid.color or LIQUID_COLORS[index % len(LIQUID_COLORS)]
                description = liquid.description or liquid.name
                out.line(f"{var} = protocol.define_liquid(name={liquid.name!r}, "
                         f"description={description!r}, display_color={color!r})")
            for loaded_liquid in plan.contents:
                out.line(f"{self._well(loaded_liquid.well, deck)}.load_liquid("
                         f"liquid={deck.liquid_vars[loaded_liquid.liquid]}, volume={format_number(loaded_liquid.volume)})")

    def _render_step(self, out: CodeWriter, index: int, step: Step, transfer_plan: Optional[TransferPlan],
                     deck: _Deck, handling: Dict[str, Dict[str, float]]) -> None:
        title = f"Step {index + 1}: {self._describe(step)}"
        out.line(f"# {title}")
        out.line(f"protocol.comment({title!r})")

        if isinstance(step, TransferSet):
            for warning in transfer_plan.warnings:
                out.line(f"# Warning: {warning}")
            for op in transfer_plan.ops:
                pipette = deck.pipettes[op.pipette.name].var
                if isinstance(op, PickUpTips):
                    out.line(f"{pipette}.pick_up_tip()")
                elif isinstance(op, DropTips):
                    out.line(f"{pipette}.drop_tip()")
                elif isinstance(op, TransferPass):
                    out.line(self._render_pass(op, pipette, deck, handling))
                elif isinstance(op, DistributePass):
                    for line in self._render_distribute(op, pipette, deck, handling):
                        out.line(line)
        elif isinstance(step, SetTemperature):
            self._render_set_temperature(out, step, deck)
        elif isinstance(step, SetLid):
            var = deck.module_vars.get(step.module)
            if var is None:
                out.line(f"protocol.comment({f'No thermocycler on deck; skipping lid {self._lid_word(step)}'!r})")
            else:
                out.line(f"{var}.{'open_lid' if step.open else 'close_lid'}()")
        elif isinstance(step, ThermalProfile):
            self._render_thermal_profile(out, step, deck)
        elif isinstance(step, MoveLabware):
            labware = deck.labware_vars[step.labware]
            if step.destination == OFF_DECK:
                destination = "protocol_api.OFF_DECK"
            elif step.destination in deck.module_vars:
                destination = deck.module_vars[step.destination]
            else:
                raise EmitError(f"Step {index}: cannot move '{step.labware}' onto '{step.destination}', "
                                f"which is not on the deck of this robot")
            out.line(f"protocol.move_labware({labware}, {destination}, use_gripper=False)")
        elif isinstance(step, Delay):
            out.line(f"protocol.delay(seconds={format_number(step.seconds)}, msg={step.message!r})")
        elif isinstance(step, Pause):
            out.line(f"protocol.pause({step.message!r})")
        elif isinstance(step, Comment):
            pass  # the step title comment above already carries the text
        else:
            raise EmitError(f"Step {index}: the OT-2 emitter doesn't know how to emit {type(step).__name__}")

    def _render_pass(self, op: TransferPass, pipette: str, deck: _Deck,
                     handling: Dict[str, Dict[str, float]]) -> str:
        # Single-channel passes have one transfer; 8-channel passes address the column by its top well.
        transfer = op.transfers[0]
        args = [pipette, self._well(transfer.source, deck), self._well(transfer.dest, deck),
                format_number(transfer.volume)]
        if op.liquid_class.value in handling:
            args.append(f"**HANDLING[{op.liquid_class.value!r}]")
        if transfer.mix_before:
            args.append(f"mix_before=({format_number(transfer.mix_before.volume)}, {transfer.mix_before.repetitions})")
        if transfer.mix_after:
            args.append(f"mix_after=({format_number(transfer.mix_after.volume)}, {transfer.mix_after.repetitions})")
        if transfer.dispense_below_top is not None:
            args.append(f"dispense_below_top={format_number(transfer.dispense_below_top)}")
        if op.touch_tip:
            args.append("touch_tip=True")
        if not op.blow_out:
            args.append("blow_out=False")
        return f"_move({', '.join(args)})"

    def _render_distribute(self, op: DistributePass, pipette: str, deck: _Deck,
                           handling: Dict[str, Dict[str, float]]) -> List[str]:
        """The ``_distribute`` call, one dispense per line; 8-channel dispenses address each column by its top well."""
        lines = [f"_distribute({pipette}, {self._well(op.dispenses[0][0].source, deck)}, ["]
        lines += [f"    ({self._location(dispense[0], deck)}, {format_number(dispense[0].volume)}),"
                  for dispense in op.dispenses]
        args = ["protocol.fixed_trash"]
        if op.disposal_volume:
            args.append(f"disposal={format_number(op.disposal_volume)}")
        if op.liquid_class.value in handling:
            args.append(f"**HANDLING[{op.liquid_class.value!r}]")
        if op.touch_tip:
            args.append("touch_tip=True")
        if not op.blow_out:
            args.append("blow_out=False")
        lines.append(f"], {', '.join(args)})")
        return lines

    def _render_set_temperature(self, out: CodeWriter, step: SetTemperature, deck: _Deck) -> None:
        var = deck.module_vars.get(step.module)
        if var is None:
            action = f"to {step.celsius:g} °C" if step.celsius is not None else "off"
            out.line(f"protocol.pause({f'Set {step.module} {action} by hand, then resume.'!r})")
            return
        kind = deck.modules[step.module].kind
        if kind is ModuleKind.THERMOCYCLER:
            if step.celsius is None:
                out.line(f"{var}.deactivate_block()")
            else:
                out.line(f"{var}.set_block_temperature({step.celsius:g})")
        elif step.celsius is None:
            out.line(f"{var}.deactivate()")
        else:
            out.line(f"{var}.set_temperature({step.celsius:g})")

    def _render_thermal_profile(self, out: CodeWriter, step: ThermalProfile, deck: _Deck) -> None:
        var = deck.module_vars.get(step.module)
        if var is None:
            out.line(f"protocol.pause({f'Run on an external thermocycler: {self._profile_text(step)}. Resume when done.'!r})")
            return
        if step.lid_celsius is not None:
            out.line(f"{var}.set_lid_temperature({step.lid_celsius:g})")
        steps = [{"temperature": s.celsius, "hold_time_seconds": s.seconds} for s in step.steps]
        volume = f", block_max_volume={format_number(step.block_max_volume)}" if step.block_max_volume else ""
        out.line(f"{var}.execute_profile(steps={repr(steps)}, repetitions={step.repetitions}{volume})")
        if step.hold_celsius is not None:
            out.line(f"{var}.set_block_temperature({step.hold_celsius:g})")

    # --- setup sheet -----------------------------------------------------------------------

    def _setup_sheet(self, plan: Plan, profile: RobotProfile, planned: Dict[int, TransferPlan], deck: _Deck,
                     protocol_file: str, warnings: List[str]) -> str:
        lines = [f"# {plan.name}: OT-2 setup", "",
                 f"Generated by LabLibre for profile **{profile.name}**. "
                 f"Upload `{protocol_file}` in the Opentrons App; nothing else needs to be installed on the robot.", ""]
        if plan.description:
            lines += [plan.description, ""]

        rows: List[Tuple[str, str]] = []
        for name, spec in deck.modules.items():
            if spec is not None:
                on_it = next((l for l, loc in deck.labware_locations.items() if loc == name), None)
                item = f"{spec.model} (`{name}`)" + (f" holding `{on_it}` ({deck.labware_defs[on_it]})" if on_it else "")
                rows.append((spec.location if spec.kind is not ModuleKind.THERMOCYCLER else "7, 8, 10, 11", item))
        for name, location in deck.labware_locations.items():
            if location not in deck.module_vars:
                rows.append((location, f"`{name}` ({deck.labware_defs[name]})"))
        for loaded in deck.pipettes.values():
            for number, slot in enumerate(loaded.rack_slots, 1):
                rows.append((slot, f"Tip rack {number} for {loaded.spec.name} ({loaded.spec.tip_rack})"))
        rows.sort(key=lambda row: _slot_sort_key(row[0]))
        lines += ["## Deck", "", "| Slot | Item |", "|---|---|"] + [f"| {slot} | {item} |" for slot, item in rows] + [""]

        if deck.pipettes:
            lines += ["## Pipettes", "", "| Mount | Pipette | Tips used |", "|---|---|---|"]
            lines += [f"| {p.spec.mount} | {p.spec.name} | {p.tips} |" for p in deck.pipettes.values()] + [""]

        if plan.contents:
            lines += ["## Liquids to load", "", "| Labware | Well | Liquid | Volume (µL) |", "|---|---|---|---|"]
            lines += [f"| `{c.well.labware}` | {c.well.well} | {c.liquid.name} | {format_number(c.volume)} |"
                      for c in plan.contents] + [""]

        missing = [name for name, spec in deck.modules.items() if spec is None]
        if missing:
            lines += ["## Manual steps", "",
                      f"This robot has no module for: {', '.join(f'`{m}`' for m in missing)}. "
                      "The protocol pauses at those steps so you can do them by hand.", ""]

        if warnings:
            lines += ["## Warnings", "", "Check these before running:", ""] + [f"- {w}" for w in warnings] + [""]

        lines += ["## Steps", ""]
        for index, step in enumerate(plan.steps):
            detail = ""
            if isinstance(step, TransferSet):
                transfer_plan = planned[index]
                tips = sum(transfer_plan.tips_used().values())
                detail = (f": {_count(len(step.transfers), 'transfer')} in "
                          f"{_count(len(transfer_plan.passes()), 'pass', 'passes')}, {_count(tips, 'tip')}")
            lines.append(f"{index + 1}. {self._describe(step)}{detail}")
        return "\n".join(lines) + "\n"

    # --- helpers ---------------------------------------------------------------------------

    @staticmethod
    def _metadata(plan: Plan) -> Dict[str, str]:
        metadata = {"protocolName": plan.name}
        if plan.description:
            metadata["description"] = plan.description
        metadata.update({k: str(v) for k, v in plan.metadata.items() if k in ("protocolName", "author", "description")})
        return metadata

    @staticmethod
    def _well(ref: WellRef, deck: _Deck) -> str:
        return f"{deck.labware_vars[ref.labware]}[{ref.well!r}]"

    def _location(self, transfer, deck: _Deck) -> str:
        """Where *transfer* dispenses: the destination well, or a point below its top."""
        well = self._well(transfer.dest, deck)
        if transfer.dispense_below_top is None:
            return well
        return f"{well}.top(-{format_number(transfer.dispense_below_top)})"

    @staticmethod
    def _lid_word(step: SetLid) -> str:
        return "open" if step.open else "close"

    @staticmethod
    def _profile_text(step: ThermalProfile) -> str:
        cycle = ", ".join(f"{s.celsius:g} °C for {s.seconds:g} s" for s in step.steps)
        text = f"{step.repetitions} x ({cycle})"
        if step.hold_celsius is not None:
            text += f", then hold at {step.hold_celsius:g} °C"
        return text

    def _describe(self, step: Step) -> str:
        if isinstance(step, TransferSet):
            return step.label or f"Transfer ({len(step.transfers)} transfers)"
        if isinstance(step, SetTemperature):
            return f"Set {step.module} to {step.celsius:g} °C" if step.celsius is not None else f"Turn off {step.module}"
        if isinstance(step, SetLid):
            return f"{self._lid_word(step).capitalize()} {step.module} lid"
        if isinstance(step, ThermalProfile):
            return f"{step.label or 'Thermal profile'}: {self._profile_text(step)}"
        if isinstance(step, MoveLabware):
            return f"Move {step.labware} to {step.destination}"
        if isinstance(step, Delay):
            return step.message or f"Wait {step.seconds:g} s"
        if isinstance(step, Pause):
            return f"Pause: {step.message}"
        if isinstance(step, Comment):
            return step.text
        return type(step).__name__


def _count(number: int, singular: str, plural: str = "") -> str:
    return f"{number} {singular if number == 1 else plural or singular + 's'}"


def _slot_sort_key(slot: str):
    first = slot.split(",")[0].strip()
    return (0, int(first), "") if first.isdigit() else (1, 0, first)
