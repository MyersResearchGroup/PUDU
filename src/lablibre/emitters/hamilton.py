"""
Hamilton emitter: writes CSV worklists for a fixed VENUS method, plus a run sheet.

A Hamilton STAR is driven by VENUS on an attached PC. Instead of generating
VENUS methods, LabLibre targets one method built once on the instrument that
reads a worklist and executes it row by row. That keeps VENUS, its deck
layout, calibrations and liquid classes in charge, and nothing needs to be
installed on the Hamilton's PC.

Each row is one channel's aspirate-and-dispense. Rows that share a ``Batch``
run at the same time, one per channel. Steps the worklist method can't do
(thermocycling, temperature control, pauses) split the protocol into several
worklists; the run sheet says what to do between them.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Dict, List, Optional, Tuple

from lablibre.bundle import Bundle, FileRole
from lablibre.emitters.base import Emitter, EmitError, format_number, slugify
from lablibre.emitters.manual import ManualEmitter
from lablibre.labware import WellRef
from lablibre.modules import ModuleKind
from lablibre.ops import DistributePass, DropTips, HeadLayout, PickUpTips, TransferPass, TransferPlan
from lablibre.plan import Plan
from lablibre.planner import ChannelPlanner
from lablibre.profile import Pitch, RobotProfile
from lablibre.steps import SetTemperature, Step, ThermalProfile, TransferSet

HANDLING_KEYS = {"liquid_class"}

# Worklist columns and what the VENUS method should do with each.
COLUMNS: List[Tuple[str, str]] = [
    ("Step", "Plan step number, as in the run sheet."),
    ("Batch", "Rows with the same batch run at the same time, one per channel. Numbered from 1 in each file."),
    ("Channel", "Channel to use, from 1."),
    ("TipType", "Tip type to pick up (from the profile's pipettes)."),
    ("NewTip", "1: pick up a fresh tip on this channel before aspirating."),
    ("DropTip", "1: drop this channel's tip after dispensing."),
    ("SourceLabware", "Labware ID in the deck layout."),
    ("SourceWell", "Well name, e.g. A1."),
    ("SourcePosition", "Same well as a number, counted down each column (A1 = 1, B1 = 2, …)."),
    ("DestLabware", "Labware ID in the deck layout."),
    ("DestWell", "Well name."),
    ("DestPosition", "Same well as a number."),
    ("Volume_uL", "Volume to aspirate and dispense."),
    ("LiquidClass", "VENUS liquid class (from the profile's liquid classes)."),
    ("AspMixCycles", "Mix cycles in the source before aspirating (0: no mix)."),
    ("AspMixVolume_uL", "Mix volume in the source."),
    ("DispMixCycles", "Mix cycles in the destination after dispensing (0: no mix)."),
    ("DispMixVolume_uL", "Mix volume in the destination."),
    ("DispenseBelowTop_mm", "Blank: the liquid class decides. Otherwise dispense this far below the rim "
                            "(negative: above it)."),
    ("TouchTip", "1: touch the tip to the well wall after dispensing."),
]


class HamiltonEmitter(Emitter):
    """
    Compile a plan into Hamilton worklists and a run sheet.

    The bundle contains ``<name>_worklist_<n>.csv`` (one per stretch of
    pipetting), ``<name>_run_sheet.md`` (deck layout, tips, liquids, run order,
    worklist format) and ``<name>_plan.json``.

    Profile requirements: ``robot: "hamilton"``; pipettes with independent
    channels (one entry per tip type); ``supports_multi_dispense: false``;
    ``liquid_classes`` giving each class's VENUS ``liquid_class`` name; and
    ``options.positions`` listing the deck layout's labware IDs per format.
    ``options.locations`` pins labware to IDs; ``options.method`` names the
    VENUS method.
    """

    robots = ("hamilton",)

    def compile(self, plan: Plan, profile: RobotProfile) -> Bundle:
        self._check_profile(profile)
        self._check_hardware(profile)
        plan_warnings = plan.check()
        planned = ChannelPlanner(profile, plan).plan_all()
        positions = self._positions(plan, profile)
        handling = {name: settings.get("liquid_class", "") for name, settings in profile.liquid_classes.items()}

        warnings = self._warnings(plan, plan_warnings, planned) + self._module_warnings(plan, positions)
        worklists, run_order = self._segments(plan, profile, planned, positions, handling)

        slug = slugify(plan.name)
        bundle = Bundle(target=profile.name, warnings=warnings)
        files = []
        for number, rows in enumerate(worklists, 1):
            path = f"{slug}_worklist_{number}.csv"
            bundle.add(path, _csv(rows), FileRole.WORKLIST)
            files.append(path)
        bundle.add(f"{slug}_run_sheet.md",
                   self._run_sheet(plan, profile, positions, worklists, files, run_order, warnings), FileRole.SETUP)
        bundle.add(f"{slug}_plan.json", json.dumps(plan.to_dict(), indent=2) + "\n", FileRole.DATA)
        return bundle

    # --- checks and layout -----------------------------------------------------------------

    @staticmethod
    def _check_hardware(profile: RobotProfile) -> None:
        for spec in profile.pipettes:
            if spec.is_multichannel and spec.pitch is Pitch.FIXED:
                raise EmitError(f"'{spec.name}' is a fixed-pitch head (e.g. a CO-RE 96 head), which the Hamilton "
                                f"worklist doesn't support yet; use independent channels")
            if not spec.tip_rack:
                raise EmitError(f"Pipette '{spec.name}' needs a tip_rack: the tip type written to the worklist")
        if profile.supports_multi_dispense:
            raise EmitError(f"Profile '{profile.name}' must set supports_multi_dispense to false: "
                            f"each worklist row is one aspirate and one dispense")
        for liquid_class, settings in profile.liquid_classes.items():
            unknown = set(settings) - HANDLING_KEYS
            if unknown:
                raise EmitError(f"Profile '{profile.name}' liquid class '{liquid_class}' has settings the Hamilton "
                                f"emitter doesn't understand: {sorted(unknown)}. Supported: {sorted(HANDLING_KEYS)}")

    @staticmethod
    def _positions(plan: Plan, profile: RobotProfile) -> Dict[str, str]:
        """The deck layout's labware ID for every plan labware."""
        overrides: Dict[str, str] = dict(profile.options.get("locations", {}))
        pools = {fmt: [p for p in ids if p not in overrides.values()]
                 for fmt, ids in profile.options.get("positions", {}).items()}
        positions = {}
        for labware in plan.labware.values():
            if labware.name in overrides:
                positions[labware.name] = overrides[labware.name]
                continue
            pool = pools.get(labware.format.name, [])
            if not pool:
                raise EmitError(f"No free deck position for '{labware.name}' ({labware.format.name}) in profile "
                                f"'{profile.name}'. Add labware IDs under options.positions.{labware.format.name}, "
                                f"or pin it with options.locations.")
            positions[labware.name] = pool.pop(0)
        return positions

    @staticmethod
    def _module_warnings(plan: Plan, positions: Dict[str, str]) -> List[str]:
        """The worklist method controls no modules, so labware the plan keeps on one sits on the deck."""
        return [f"`{labware.name}` sits on the deck at {positions[labware.name]} without temperature control; "
                f"the plan keeps it on `{labware.module}` ({plan.modules[labware.module].kind.value})"
                for labware in plan.labware.values() if labware.module is not None]

    # --- worklists -------------------------------------------------------------------------

    def _segments(self, plan: Plan, profile: RobotProfile, planned: Dict[int, TransferPlan],
                  positions: Dict[str, str], handling: Dict[str, str]):
        """Worklists (lists of rows) and the run order: ("worklist", index, step numbers) or ("manual", lines)."""
        worklists: List[List[Dict]] = []
        run_order: List[Tuple] = []
        current: List[Dict] = []
        steps_in_current: List[int] = []
        manual = ManualEmitter()

        def close():
            if current:
                worklists.append(list(current))
                run_order.append(("worklist", len(worklists) - 1, list(steps_in_current)))
                current.clear()
                steps_in_current.clear()

        for index, step in enumerate(plan.steps):
            if isinstance(step, TransferSet):
                batch = max((row["Batch"] for row in current), default=0)
                current.extend(self._rows(plan, index + 1, planned[index], positions, handling, batch))
                steps_in_current.append(index + 1)
                continue
            lines = self._manual_lines(manual, plan, profile, step, positions, index)
            if lines is None:
                continue
            close()
            run_order.append(("manual", lines))
        close()
        return worklists, run_order

    def _rows(self, plan: Plan, number: int, transfer_plan: TransferPlan, positions: Dict[str, str],
              handling: Dict[str, str], batch: int) -> List[Dict]:
        """Worklist rows for one transfer set, with tip flags that respect the shared channels."""
        rows: List[Dict] = []
        last_batch: List[Dict] = []
        holding: Optional[str] = None  # tip type on the channels
        pending = set()                # pipettes the planner picked up tips for, not yet used

        def drop():
            for row in last_batch:
                row["DropTip"] = 1

        for op in transfer_plan.ops:
            if isinstance(op, PickUpTips):
                pending.add(op.pipette.name)
            elif isinstance(op, DropTips):
                if holding == op.pipette.name:
                    drop()
                    holding = None
            elif isinstance(op, TransferPass):
                name = op.pipette.name
                if holding not in (None, name):
                    # Every tip type uses the same channels, so switching types means dropping the others first.
                    drop()
                    holding = None
                new_tip = name in pending or holding != name
                pending.discard(name)
                batch += 1
                channels = range(1, len(op.transfers) + 1) if op.layout is HeadLayout.INDEPENDENT else [1]
                last_batch = [self._row(plan, number, batch, channel, op, transfer, new_tip, positions, handling)
                              for channel, transfer in zip(channels, op.transfers)]
                rows += last_batch
                holding = name
            elif isinstance(op, DistributePass):
                raise EmitError("The Hamilton worklist has one dispense per row; set supports_multi_dispense "
                                "to false in the profile")
        if holding is not None:
            drop()
        return rows

    @staticmethod
    def _row(plan: Plan, number: int, batch: int, channel: int, op: TransferPass, transfer, new_tip: bool,
             positions: Dict[str, str], handling: Dict[str, str]) -> Dict:
        mix_before, mix_after = transfer.mix_before, transfer.mix_after
        below = transfer.dispense_below_top
        return {
            "Step": number, "Batch": batch, "Channel": channel, "TipType": op.pipette.tip_rack,
            "NewTip": int(new_tip), "DropTip": 0,
            "SourceLabware": positions[transfer.source.labware], "SourceWell": transfer.source.well,
            "SourcePosition": _position(transfer.source, plan),
            "DestLabware": positions[transfer.dest.labware], "DestWell": transfer.dest.well,
            "DestPosition": _position(transfer.dest, plan),
            "Volume_uL": format_number(transfer.volume),
            "LiquidClass": handling.get(op.liquid_class.value, ""),
            "AspMixCycles": mix_before.repetitions if mix_before else 0,
            "AspMixVolume_uL": format_number(mix_before.volume) if mix_before else 0,
            "DispMixCycles": mix_after.repetitions if mix_after else 0,
            "DispMixVolume_uL": format_number(mix_after.volume) if mix_after else 0,
            "DispenseBelowTop_mm": "" if below is None else format_number(below),
            "TouchTip": int(op.touch_tip),
        }

    # --- run sheet -------------------------------------------------------------------------

    def _manual_lines(self, manual: ManualEmitter, plan: Plan, profile: RobotProfile, step: Step,
                      positions: Dict[str, str], index: int) -> Optional[List[str]]:
        """What the operator does for a non-pipetting step, or ``None`` if nothing."""
        if isinstance(step, SetTemperature):
            module = plan.modules.get(step.module)
            if module is not None and module.kind is ModuleKind.TEMPERATURE:
                return None  # there is no temperature block on the deck; see the warnings
        lines = manual._step(plan, profile, step, None)
        if lines is None:
            return None
        if isinstance(step, ThermalProfile):
            plate = next((l.name for l in plan.labware.values() if l.module == step.module), None)
            if plate and self._used_later(plan, plate, index):
                lines = lines + ["", f"When it finishes, put `{plate}` back at {positions[plate]}."]
        return lines

    @staticmethod
    def _used_later(plan: Plan, labware: str, index: int) -> bool:
        return any(isinstance(step, TransferSet) and any(labware in (t.source.labware, t.dest.labware)
                                                         for t in step.transfers)
                   for step in plan.steps[index + 1:])

    def _run_sheet(self, plan: Plan, profile: RobotProfile, positions: Dict[str, str], worklists: List[List[Dict]],
                   files: List[str], run_order: List[Tuple], warnings: List[str]) -> str:
        method = profile.options.get("method", "the worklist method")
        lines = [f"# {plan.name}: Hamilton run sheet", ""]
        if plan.description:
            lines += [plan.description, ""]
        lines += [f"_Generated by LabLibre for **{profile.name}**._ Each worklist runs with the VENUS method "
                  f"`{method}`. Nothing else needs to be installed on the instrument's PC.", ""]
        if profile.options.get("note"):
            lines += [f"> Profile note: {profile.options['note']}", ""]

        lines += ["## Deck layout", "", "| Position | Labware | Type | Notes |", "|---|---|---|---|"]
        for labware in plan.labware.values():
            try:
                kind = profile.resolve_labware(labware)
            except KeyError:
                kind = labware.format.name.replace("_", " ")
            lines.append(f"| {positions[labware.name]} | `{labware.name}` | {kind} | {labware.description} |")
        lines.append("")

        tips: Dict[str, int] = {}
        for rows in worklists:
            for row in rows:
                tips[row["TipType"]] = tips.get(row["TipType"], 0) + row["NewTip"]
        if tips:
            lines += ["## Tips", "", "| Tip type | Tips used |", "|---|---|"]
            lines += [f"| {tip} | {count} |" for tip, count in tips.items()] + [""]

        if plan.contents:
            lines += ["## Liquids to load", "", "| Position | Labware | Well | Liquid | Volume (µL) |",
                      "|---|---|---|---|---|"]
            lines += [f"| {positions[c.well.labware]} | `{c.well.labware}` | {c.well.well} | {c.liquid.name} "
                      f"| {format_number(c.volume)} |" for c in plan.contents] + [""]

        if warnings:
            lines += ["## Warnings", "", "Check these before running:", ""] + [f"- {w}" for w in warnings] + [""]

        lines += ["## Run order", ""]
        for number, entry in enumerate(run_order, 1):
            if entry[0] == "worklist":
                _, index, steps = entry
                rows = worklists[index]
                labels = ", ".join(plan.steps[s - 1].label or f"step {s}" for s in steps)
                tips = sum(r['NewTip'] for r in rows)
                lines.append(f"{number}. **Run `{files[index]}`** in `{method}`: {labels} "
                             f"({len(rows)} row{'s' * (len(rows) != 1)}, {tips} tip{'s' * (tips != 1)}).")
            else:
                title, *body = entry[1]
                lines.append(f"{number}. {title}.")
                lines += [f"   {line}" if line else "" for line in body]
        lines.append("")

        lines += ["## Worklist columns", "",
                  "For whoever builds or maintains the VENUS method. Air gaps and blow-outs come from the "
                  "liquid class.", "", "| Column | Meaning |", "|---|---|"]
        lines += [f"| `{name}` | {meaning} |" for name, meaning in COLUMNS]
        return "\n".join(lines) + "\n"


def _position(ref: WellRef, plan: Plan) -> int:
    """1-based well number counted down each column, as Hamilton sequences count."""
    return ref.column * plan.labware[ref.labware].format.rows + ref.row + 1


def _csv(rows: List[Dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=[name for name, _ in COLUMNS], lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()
