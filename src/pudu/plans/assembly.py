"""
Golden Gate / Loop assembly as a LabLibre plan.

Builds the same protocol as PUDU's assembly classes (:class:`~pudu.assembly.SBOLLoopAssembly`,
:class:`~pudu.assembly.ManualLoopAssembly` and :class:`~pudu.assembly.Domestication`) as a
robot-agnostic :class:`~lablibre.plan.Plan`: water, T4 ligase buffer, T4 ligase, restriction
enzyme and DNA parts into each reaction well, then Golden Gate cycling. The compiled bundle
includes ``transformation_input.json`` (product wells) for the transformation protocol, so
the whole assembly → transformation → plating chain runs without simulating anything.

Only the biology is configured here. Hardware (pipettes, tip racks, labware definitions,
deck slots, flow rates) comes from the robot profile.

Example::

    from lablibre import load_profile
    from pudu.plans import AssemblyProtocol

    protocol = AssemblyProtocol([{
        "Product": "https://SBOL2Build.org/composite_1/1",
        "Backbone": "https://sbolcanvas.org/pSB1C3/1",
        "PartsList": ["https://sbolcanvas.org/J23101/1", "https://sbolcanvas.org/GFP/1"],
        "Restriction Enzyme": "https://SBOL2Build.org/BsaI/1",
    }])
    protocol.compile(load_profile("ot2_default")).write("assembly_out/")
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from lablibre import (Bundle, Comment, FileRole, Liquid, LiquidClass, Mix, ModuleKind, Ordering, Plan,
                      RobotProfile, SetLid, SetTemperature, ThermalProfile, ThermalStep, TipPolicy, Transfer,
                      TransferSet, WellRef, compile_plan)
from pudu.assembly import BaseAssembly, Domestication, ManualLoopAssembly, SBOLLoopAssembly

MIX_REPETITIONS = 3              # pre-aspiration mix of every reagent and part, as the original does
REAGENT_VOLUME = 1000            # µL loaded per tube; the original's default
CYCLING = [ThermalStep(42, 120), ThermalStep(16, 300)]
CYCLES = 75
DENATURATION = [ThermalStep(60, 600), ThermalStep(80, 600)]
LID_CELSIUS = 42
HOLD_CELSIUS = 4

# Assembly parameters that describe hardware (or robot-side features). They now live in the robot profile.
HARDWARE_PARAMS = frozenset({
    'thermocycler_labware', 'temperature_module_labware', 'temperature_module_position',
    'tiprack_labware', 'tiprack_positions', 'pipette', 'pipette_position', 'initial_tip',
    'aspiration_rate', 'dispense_rate', 'take_picture', 'take_video',
})

FORMATS = {"SBOL": SBOLLoopAssembly, "Manual": ManualLoopAssembly, "Domestication": Domestication}


@dataclass
class _Reaction:
    """One reaction well: what goes in it, and how the original tracks it."""
    parts: List[str]          # reagent names of the DNA parts, in the order they're added
    enzyme: str               # reagent name of the restriction enzyme
    product_key: str          # key in transformation_input.json
    thermocycler_label: str   # key in the original's thermocycler dictionary (for the Excel output)
    name: str                 # short product name for step labels


class AssemblyProtocol:
    """
    Golden Gate / Loop assembly, compiled through LabLibre.

    Accepts the same inputs as PUDU's assembly classes: a list of assemblies (or
    ``{"assemblies": [...]}``) in SBOL, Manual (combinatorial, Odd/Even receivers) or
    Domestication format, plus ``json_params`` and keyword parameters with the same
    precedence and validation. The format is detected like ``pudu.generate_protocol``
    does, or set with ``assembly_type``. Hardware parameters and the camera options in
    :data:`HARDWARE_PARAMS` raise ``ValueError``.

    Each reaction gets water, T4 ligase buffer, T4 ligase, the restriction enzyme and its
    DNA parts in that order, each with a fresh tip and a pre-aspiration mix; the tip used
    for the last part mixes the reaction. ``water_testing=True`` leaves out every
    temperature step for dry runs.
    """

    def __init__(self, assembly_data: Union[List[Dict], Dict, None] = None, json_params: Optional[Dict] = None,
                 assembly_type: Optional[str] = None, **params):
        hardware = sorted((set(params) | set(json_params or {})) & HARDWARE_PARAMS)
        if hardware:
            raise ValueError(f"{hardware} describe robot hardware or robot-side features, which compiled "
                             f"protocols take from the robot profile (see lablibre.load_profile). "
                             f"Remove them from the assembly parameters.")
        assemblies = assembly_data.get("assemblies") if isinstance(assembly_data, dict) else assembly_data
        if not assemblies:
            raise ValueError("Must provide assemblies, as a list or as {'assemblies': [...]}")
        self.assembly_type = assembly_type or detect_format(assemblies[0])
        if self.assembly_type not in FORMATS:
            raise ValueError(f"Unknown assembly_type '{self.assembly_type}'. Choose from {sorted(FORMATS)}")
        # The original class does the parameter merging, parsing and validation; nothing here touches a robot.
        self.params: BaseAssembly = FORMATS[self.assembly_type](assemblies=assemblies, json_params=json_params,
                                                                **params)
        self.params.process_assemblies()
        self._reagents, self._reactions = self._layout()

    def build_plan(self) -> Plan:
        """The robot-agnostic plan for this assembly run."""
        p = self.params
        plan = Plan("Assembly",
                    description=f"Golden Gate assembly ({self.assembly_type} format) of "
                                f"{len(self._reactions)} reaction(s)",
                    metadata={"protocolName": "PUDU Assembly"})
        cold = plan.add_module("cold_block", ModuleKind.TEMPERATURE).name
        thermocycler = plan.add_module("thermocycler", ModuleKind.THERMOCYCLER).name
        tubes = plan.add_labware("reagent_tubes", "tube_rack_24", module=cold,
                                 description="Water, T4 ligase buffer, T4 ligase, enzymes and DNA parts")
        plate = plan.add_labware("reaction_plate", "plate_96", module=thermocycler,
                                 description="Assembly reactions")

        source: Dict[str, WellRef] = {}
        for index, (name, liquid_class) in enumerate(self._reagents):
            source[name] = tubes.wells()[index]
            plan.load(source[name], Liquid(name, liquid_class), REAGENT_VOLUME)

        wells = plate.wells()[p.thermocycler_starting_well:p.thermocycler_starting_well + len(self._reactions)]

        plan.add(SetLid(thermocycler, open=True))
        if not p.water_testing:
            plan.add(SetTemperature(cold, 4))
            plan.add(SetTemperature(thermocycler, 4))

        reagents_volume = p.volume_restriction_enzyme + p.volume_t4_dna_ligase + p.volume_t4_dna_ligase_buffer
        remove_air = Mix(p.volume_total_reaction, int(p.volume_total_reaction / 10))
        for well, reaction in zip(wells, self._reactions):
            name = f"{well.well} ({reaction.name})"
            water = p.volume_total_reaction - (reagents_volume + p.volume_part * len(reaction.parts))
            plan.add(TransferSet([
                Transfer(source["Deionized Water"], well, water),
                Transfer(source["T4 DNA Ligase Buffer"], well, p.volume_t4_dna_ligase_buffer,
                         mix_before=Mix(p.volume_t4_dna_ligase_buffer, MIX_REPETITIONS)),
            ], ordering=Ordering.SEQUENTIAL, touch_tip=True, label=f"{name}: water and ligase buffer"))
            plan.add(TransferSet([
                Transfer(source["T4 DNA Ligase"], well, p.volume_t4_dna_ligase,
                         mix_before=Mix(p.volume_t4_dna_ligase, MIX_REPETITIONS)),
                Transfer(source[reaction.enzyme], well, p.volume_restriction_enzyme,
                         mix_before=Mix(p.volume_restriction_enzyme, MIX_REPETITIONS)),
            ], liquid_class=LiquidClass.GLYCEROL, ordering=Ordering.SEQUENTIAL, touch_tip=True,
                label=f"{name}: ligase and restriction enzyme"))
            parts = [Transfer(source[part], well, p.volume_part, mix_before=Mix(p.volume_part, MIX_REPETITIONS))
                     for part in reaction.parts]
            if remove_air.repetitions:
                parts[-1] = Transfer(parts[-1].source, well, p.volume_part, mix_before=parts[-1].mix_before,
                                     mix_after=remove_air)
            plan.add(TransferSet(parts, ordering=Ordering.SEQUENTIAL, touch_tip=True, label=f"{name}: DNA parts"))

        plan.add(Comment("Take out the reagents: the temperature module turns off next"))
        if not p.water_testing:
            plan.add(SetLid(thermocycler, open=False))
            plan.add(SetTemperature(cold, None))
            plan.add(ThermalProfile(thermocycler, CYCLING, repetitions=CYCLES, lid_celsius=LID_CELSIUS,
                                    block_max_volume=p.volume_total_reaction, label="Golden Gate cycling"))
            plan.add(ThermalProfile(thermocycler, DENATURATION, block_max_volume=p.volume_total_reaction,
                                    hold_celsius=HOLD_CELSIUS, label="Enzyme inactivation"))
        return plan

    def transformation_input(self) -> Dict[str, List[str]]:
        """Product wells for the transformation protocol, in the format the original writes."""
        start = self.params.thermocycler_starting_well
        names = [f"{row}{column}" for column in range(1, 13) for row in "ABCDEFGH"]
        wells: Dict[str, List[str]] = {}
        for well, reaction in zip(names[start:], self._reactions):
            wells.setdefault(reaction.product_key, []).append(well)
        return wells

    def compile(self, profile: RobotProfile) -> Bundle:
        """Compile for *profile*, adding ``transformation_input.json`` and, if enabled, the Excel summary."""
        bundle = compile_plan(self.build_plan(), profile)
        bundle.add("transformation_input.json", json.dumps(self.transformation_input(), indent=2) + "\n",
                   FileRole.DATA)
        if self.params.output_xlsx:
            name = self.params.protocol_name or "Loop Assembly"
            bundle.add(f"{name}.xlsx", self._excel(name), FileRole.DATA)
        return bundle

    # --- helpers ---------------------------------------------------------------------------

    def _layout(self) -> Tuple[List[Tuple[str, LiquidClass]], List[_Reaction]]:
        """Reagent tubes in the original's order, and the reactions in the original's well order."""
        p = self.params
        reagents = [("Deionized Water", LiquidClass.AQUEOUS), ("T4 DNA Ligase Buffer", LiquidClass.AQUEOUS),
                    ("T4 DNA Ligase", LiquidClass.GLYCEROL)]
        enzymes: List[str] = []
        parts: List[str] = []
        reactions: List[_Reaction] = []

        if isinstance(p, SBOLLoopAssembly):
            enzymes = [f"Restriction Enzyme {name}" for name in sorted(p.restriction_enzyme_set)]
            parts = sorted(p.combined_set)
            for combo in p.assembly_combinations:
                for r in range(p.replicates):
                    reactions.append(_Reaction(list(combo['parts']), f"Restriction Enzyme {combo['enzyme']}",
                                               combo['product_uri'],
                                               f"Replicate: {r + 1}, Product: {combo['product']}", combo['product']))
        elif isinstance(p, ManualLoopAssembly):
            groups = [("Restriction Enzyme BSAI", p.odd_combinations) if p.has_odd else None,
                      ("Restriction Enzyme SAPI", p.even_combinations) if p.has_even else None]
            enzymes = [enzyme for enzyme, _ in filter(None, groups)]
            parts = sorted(p.parts_set)
            for enzyme, combinations in filter(None, groups):
                for combination in combinations:
                    for r in range(p.replicates):
                        reactions.append(_Reaction(list(combination), enzyme, "_".join(combination),
                                                   f"Replicate: {r + 1}, Combination: {combination}",
                                                   "_".join(combination)))
        else:  # Domestication
            enzymes = [f"Restriction Enzyme {p.restriction_enzyme}"]
            parts = [f"Backbone {p.backbone}"] + [f"Part {part}" for part in p.parts_list]
            for part in p.parts_list:
                for r in range(p.replicates):
                    reactions.append(_Reaction([f"Backbone {p.backbone}", f"Part {part}"], enzymes[0], part,
                                               f"Part: {part}, Replicate: {r + 1}", part))

        reagents += [(name, LiquidClass.GLYCEROL) for name in enzymes]
        reagents += [(name, LiquidClass.AQUEOUS) for name in parts]
        return reagents, reactions

    def _excel(self, name: str) -> bytes:
        """The original's Excel summary of tube and reaction positions."""
        p = self.params
        p.dict_of_parts_in_temp_mod_position = {reagent: _tube_well(index)
                                                for index, (reagent, _) in enumerate(self._reagents)}
        start = p.thermocycler_starting_well
        names = [f"{row}{column}" for column in range(1, 13) for row in "ABCDEFGH"]
        p.dict_of_parts_in_thermocycler = {reaction.thermocycler_label: well
                                           for well, reaction in zip(names[start:], self._reactions)}
        with tempfile.TemporaryDirectory() as tmp:
            p.get_xlsx_output(str(Path(tmp) / name))
            return (Path(tmp) / f"{name}.xlsx").read_bytes()


def detect_format(assembly: Dict) -> str:
    """``'SBOL'``, ``'Manual'`` or ``'Domestication'``, detected the way ``pudu.generate_protocol`` does."""
    if {'Product', 'Backbone', 'PartsList', 'Restriction Enzyme'} <= set(assembly):
        return "SBOL"
    if 'receiver' in assembly:
        return "Manual"
    if {'parts', 'backbone', 'restriction_enzyme'} <= set(assembly):
        return "Domestication"
    raise ValueError(f"Can't tell the assembly format from keys {sorted(assembly)}. Pass assembly_type.")


def _tube_well(index: int) -> str:
    """Well name of the *index*-th tube on a 24-tube block (4 rows, filled down each column)."""
    return f"{'ABCD'[index % 4]}{index // 4 + 1}"
