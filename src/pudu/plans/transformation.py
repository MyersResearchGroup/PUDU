"""
Heat-shock transformation as a LabLibre plan.

Builds the same protocol as :class:`pudu.transformation.HeatShockTransformation`
as a robot-agnostic :class:`~lablibre.plan.Plan`: competent cells into a
thermocycler plate, DNA into each well, heat shock, recovery media, recovery
incubation. The compiled bundle includes ``plating_input.json`` for the plating
protocol, so the two chain without simulating anything.

Only the biology is configured here. Hardware (pipettes, tip racks, labware
definitions, deck slots, flow rates) comes from the robot profile.

Example::

    from lablibre import load_profile
    from pudu.plans import TransformationProtocol

    protocol = TransformationProtocol(transformation_data=[
        {"Strain": "https://x.org/strain_GFP/1", "Chassis": "https://x.org/DH5alpha/1",
         "Plasmids": ["https://x.org/composite_1/1"]},
    ])
    protocol.compile(load_profile("ot2_default")).write("transformation_out/")
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from lablibre import (Bundle, FileRole, Liquid, LiquidClass, Mix, ModuleKind, Plan, RobotProfile, SetLid,
                      SetTemperature, ThermalProfile, ThermalStep, TipPolicy, Transfer, TransferSet, WellRef,
                      compile_plan)
from pudu.transformation import HeatShockTransformation

CELL_MIX = Mix(50, 3)            # resuspend competent cells before each aspiration, as the original does
DNA_MIX = 3                      # repetitions of the pre-aspiration DNA mix (volume = DNA transfer volume)
REMOVE_AIR_MIX = Mix(20, 2)      # the original's "remove air" step: aspirate/dispense 20 µL twice in the well
MEDIA_AIR_GAP = 10               # µL, as in the original's media distribute
MEDIA_DISPENSE_ABOVE_RIM = 2     # mm; media is dispensed from above the well to keep the tip clean
TUBE_RACK_WELLS = 24
MODULE_WELLS = 24

# HeatShockTransformation parameters that describe hardware. They now live in the robot profile.
HARDWARE_PARAMS = frozenset({
    'thermocycler_labware', 'temperature_module_labware', 'temperature_module_position',
    'dna_plate', 'dna_plate_position',
    'tiprack_p20_labware', 'tiprack_p20_position', 'tiprack_p200_labware', 'tiprack_p200_position',
    'pipette_p20', 'pipette_p20_position', 'pipette_p300', 'pipette_p300_position',
    'aspiration_rate', 'dispense_rate', 'initial_tip_p20', 'initial_tip_p300',
    'tube_rack_labware', 'tube_rack_position',
})


class _Wells:
    """Stands in for a labware object where HeatShockTransformation only needs to count wells."""

    def __init__(self, count: int):
        self._count = count

    def wells(self) -> List[None]:
        return [None] * self._count


class TransformationProtocol:
    """
    Heat-shock transformation, compiled through LabLibre.

    Accepts the same inputs as :class:`pudu.transformation.HeatShockTransformation`
    (``transformation_data``, optional ``plasmid_locations`` from an assembly run,
    ``json_params`` and keyword parameters, with the same precedence and
    validation) except the hardware parameters in :data:`HARDWARE_PARAMS`, which
    raise ``ValueError`` pointing to the robot profile.

    DNA comes from sequential tubes on a cold block, or, when
    ``plasmid_locations`` is given, from the assembly plate at those wells.
    ``water_testing=True`` leaves out every temperature step for dry runs.
    """

    def __init__(self, transformation_data: Optional[List] = None, plasmid_locations: Optional[Dict] = None,
                 json_params: Optional[Dict] = None, **params):
        hardware = sorted((set(params) | set(json_params or {})) & HARDWARE_PARAMS)
        if hardware:
            raise ValueError(f"{hardware} describe robot hardware, which now comes from the robot profile "
                             f"(see lablibre.load_profile). Remove them from the transformation parameters.")
        # HeatShockTransformation does the parameter merging, parsing and validation; nothing here touches a robot.
        self.params = HeatShockTransformation(transformation_data=transformation_data,
                                              plasmid_locations=plasmid_locations, json_params=json_params, **params)
        # Its capacity checks and tube counts only need well counts, not labware.
        self.params._validate_protocol(None, _Wells(MODULE_WELLS), _Wells(TUBE_RACK_WELLS))

    def build_plan(self) -> Plan:
        """The robot-agnostic plan for this transformation run."""
        p = self.params
        plan = Plan("Transformation",
                    description=(f"Heat-shock transformation of {len(p.transformations)} strain(s), "
                                 f"{p.total_transformations} reaction(s)"),
                    metadata={"protocolName": "PUDU Heat Shock Transformation"})
        cold = plan.add_module("cold_block", ModuleKind.TEMPERATURE).name
        thermocycler = plan.add_module("thermocycler", ModuleKind.THERMOCYCLER).name
        plate = plan.add_labware("transformation_plate", "plate_96", module=thermocycler,
                                 description="Transformation reactions")
        dna = self._load_dna(plan, cold)
        cells, media = self._load_reagents(plan)

        wells = plate.wells()[p.thermocycler_starting_well:p.thermocycler_starting_well + p.total_transformations]
        if len(wells) < p.total_transformations:
            raise ValueError(f"{p.total_transformations} reactions starting at well "
                             f"{p.thermocycler_starting_well} don't fit on a 96-well plate")
        reactions = self._reactions(wells)

        plan.add(SetLid(thermocycler, open=True))
        if not p.water_testing:
            plan.add(SetTemperature(cold, 4))
            plan.add(SetTemperature(thermocycler, 4))

        cell_moves = []
        count_by_chassis = {chassis: 0 for chassis in p.all_chassis}
        for well, transformation, _ in reactions:
            chassis = transformation['chassis']
            tube = cells[chassis][count_by_chassis[chassis] // p.transformations_per_cell_tube]
            count_by_chassis[chassis] += 1
            cell_moves.append(Transfer(tube, well, p.transfer_volume_competent_cell, mix_before=CELL_MIX))
        plan.add(TransferSet(cell_moves, liquid_class=LiquidClass.CELLS, tips=TipPolicy.PER_SOURCE,
                             multi_dispense=True, blow_out=False, label="Competent cells"))

        dna_moves = [Transfer(dna[name][location], well, p.transfer_volume_dna,
                              mix_before=Mix(p.transfer_volume_dna, DNA_MIX), mix_after=REMOVE_AIR_MIX)
                     for well, transformation, location in reactions for name in transformation['plasmids']]
        plan.add(TransferSet(dna_moves, touch_tip=True, label="DNA"))

        plan.add(SetLid(thermocycler, open=False))
        heat_volume = self._well_volume(0)
        if not p.water_testing:
            plan.add(ThermalProfile(thermocycler, [_step(p.cold_incubation1), _step(p.heat_shock),
                                                   _step(p.cold_incubation2)],
                                    block_max_volume=heat_volume, label="Heat shock"))
        plan.add(SetLid(thermocycler, open=True))

        media_moves = [Transfer(media[index // p.transformations_per_media_tube], well,
                                p.transfer_volume_recovery_media, dispense_below_top=-MEDIA_DISPENSE_ABOVE_RIM)
                       for index, (well, _, _) in enumerate(reactions)]
        plan.add(TransferSet(media_moves, tips=TipPolicy.PER_SOURCE, multi_dispense=True, blow_out=False,
                             air_gap=MEDIA_AIR_GAP, label="Recovery media"))

        plan.add(SetLid(thermocycler, open=False))
        if not p.water_testing:
            plan.add(ThermalProfile(thermocycler, [_step(p.recovery_incubation)],
                                    block_max_volume=self._well_volume(p.transfer_volume_recovery_media),
                                    label="Recovery"))
        return plan

    def plating_input(self) -> Dict:
        """``{"bacterium_locations": ...}`` for the plating protocol, in the same format the original writes."""
        p = self.params
        start = p.thermocycler_starting_well
        names = [f"{row}{column}" for column in range(1, 13) for row in "ABCDEFGH"]
        locations: Dict[str, List[str]] = {}
        wells = names[start:start + p.total_transformations]
        for well, transformation, _ in self._reactions(wells):
            locations[well] = [transformation['strain'], f"Competent_Cell_{transformation['chassis']}"]
        for well, transformation, _ in self._reactions(wells):
            locations[well] += transformation['plasmids']
        for index, well in enumerate(wells):
            locations[well].append(f"Media_{index // p.transformations_per_media_tube + 1}")
        return {"bacterium_locations": locations}

    def compile(self, profile: RobotProfile) -> Bundle:
        """Compile for *profile*, adding ``plating_input.json`` for the next protocol."""
        bundle = compile_plan(self.build_plan(), profile)
        bundle.add("plating_input.json", json.dumps(self.plating_input(), indent=2) + "\n", FileRole.DATA)
        return bundle

    # --- helpers ---------------------------------------------------------------------------

    def _reactions(self, wells) -> List[Tuple]:
        """``(well, transformation, location_index)`` per reaction, in the original's well order."""
        p = self.params
        reactions = []
        position = iter(wells)
        for transformation in p.transformations:
            for location in range(p.location_replicates):
                for _ in range(p.replicates):
                    reactions.append((next(position), transformation, location))
        return reactions

    def _load_dna(self, plan: Plan, cold: str) -> Dict[str, List[WellRef]]:
        """DNA source wells per plasmid name, loaded where the original loads them."""
        p = self.params
        if p.plasmid_locations is not None:
            dna_plate = plan.add_labware("dna_plate", "plate_96", description="Assembly output plate")
            name_to_uri = {p._extract_name_from_uri(uri): uri for uri in p.plasmid_locations}
            sources = {name: [dna_plate[well] for well in p.plasmid_locations[name_to_uri[name]]]
                       for name in p.all_plasmids}
        else:
            tubes = plan.add_labware("dna_tubes", "tube_rack_24", module=cold, description="DNA plasmids")
            sources = {name: [tubes.wells()[p.initial_dna_well + index]] for index, name in enumerate(p.all_plasmids)}
        for name, wells in sources.items():
            liquid = Liquid(name, description=f"{name} DNA construct")
            for well in wells:
                plan.load(well, liquid, p.volume_dna)
        return sources

    def _load_reagents(self, plan: Plan) -> Tuple[Dict[str, List[WellRef]], List[WellRef]]:
        """Competent cell tubes per chassis, then recovery media tubes, on one tube rack."""
        p = self.params
        rack = plan.add_labware("reagent_tubes", "tube_rack_24", description="Competent cells and recovery media")
        tubes = iter(rack.wells())
        cells: Dict[str, List[WellRef]] = {}
        for chassis in p.all_chassis:
            liquid = Liquid(f"Competent cells {chassis}", LiquidClass.CELLS)
            cells[chassis] = [next(tubes) for _ in range(p.competent_cell_tubes_by_chassis[chassis])]
            for tube in cells[chassis]:
                plan.load(tube, liquid, p.tube_volume_competent_cell)
        media_liquid = Liquid("Recovery media")
        media = [next(tubes) for _ in range(p.media_tubes_needed)]
        for tube in media:
            plan.load(tube, media_liquid, p.tube_volume_recovery_media)
        return cells, media

    def _well_volume(self, media: float) -> float:
        """Largest reaction volume in µL: cells, every plasmid's DNA, plus *media*."""
        p = self.params
        most_plasmids = max(len(t['plasmids']) for t in p.transformations)
        return p.transfer_volume_competent_cell + most_plasmids * p.transfer_volume_dna + media


def _step(incubation: Dict) -> ThermalStep:
    return ThermalStep(incubation['temperature'], incubation['hold_time_minutes'] * 60)
