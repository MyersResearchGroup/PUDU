"""
Plating as a LabLibre plan.

Builds the same serial-dilution and spot-plating protocol as
:class:`pudu.plating.Plating`, but as a robot-agnostic
:class:`~lablibre.plan.Plan` that compiles to a standalone file for any
robot LabLibre supports. Nothing needs to be installed on the robot.

Only the biology is configured here (volumes, dilutions, replicates, which
wells hold which constructs). Hardware (pipettes, tip racks, labware
definitions, deck slots, flow rates) comes from the robot profile.

Example::

    from lablibre import load_profile
    from pudu.plans import PlatingProtocol

    protocol = PlatingProtocol(plating_data={"bacterium_locations": {"A1": "GFP", "B1": "RFP"}},
                               replicates=2)
    protocol.compile(load_profile("ot2_default")).write("plating_out/")
"""
from __future__ import annotations

import json
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from lablibre import (Bundle, Comment, FileRole, Liquid, LiquidClass, Mix, ModuleKind, Ordering, Plan,
                      RobotProfile, SetLid, SetTemperature, TipPolicy, Transfer, TransferSet, WellRef,
                      compile_plan, transfers)
from pudu.plating import Plating

MIX_REPETITIONS = 5              # same as pudu.plating.Plating
AGAR_DISPENSE_BELOW_TOP = 8      # mm; spot above the agar surface without touching it
LB_DISPOSAL_VOLUME = 4           # µL; same as the distribute call in pudu.plating.Plating
MAX_CONSTRUCTS = 96

# Plating parameters that describe hardware. They now live in the robot profile.
HARDWARE_PARAMS = frozenset({
    'thermocycler_starting_well', 'thermocycler_labware',
    'small_tiprack', 'small_tiprack_position', 'initial_small_tip',
    'large_tiprack', 'large_tiprack_position', 'initial_large_tip',
    'small_pipette', 'small_pipette_position', 'large_pipette', 'large_pipette_position',
    'dilution_plate', 'dilution_plate_position1', 'dilution_plate_position2',
    'agar_plate', 'agar_plate_position1', 'agar_plate_position2',
    'tube_rack', 'tube_rack_position', 'aspiration_rate', 'dispense_rate',
})


class PlatingProtocol:
    """
    Serial dilution and spot plating, compiled through LabLibre.

    Accepts the same inputs as :class:`pudu.plating.Plating` (``plating_data``,
    ``json_params`` and keyword parameters, with the same precedence and
    validation) except the hardware parameters in :data:`HARDWARE_PARAMS`,
    which raise ``ValueError`` pointing to the robot profile.

    For each construct, one tip carries bacteria into dilution 1, seeds
    dilution 2 and spots every dilution-1 replicate; a second tip spots the
    dilution-2 replicates. LB is first multi-dispensed to all dilution wells
    with a single tip.
    """

    def __init__(self, plating_data: Optional[Dict] = None, json_params: Optional[Dict] = None, **params):
        supplied = set(params) | set(plating_data or {}) | set(json_params or {})
        hardware = sorted(supplied & HARDWARE_PARAMS)
        if hardware:
            raise ValueError(f"{hardware} describe robot hardware, which now comes from the robot profile "
                             f"(see lablibre.load_profile). Remove them from the plating parameters.")
        # Plating does the parameter merging, validation and agar layout; nothing here touches a robot.
        self.params = Plating(plating_data=plating_data, json_params=json_params, **params)
        if self.params.number_constructs > MAX_CONSTRUCTS:
            raise ValueError(f"At most {MAX_CONSTRUCTS} constructs are supported, got {self.params.number_constructs}")

    def agar_plate_map(self) -> Dict:
        """Agar plate layout in the same format as ``Plating.get_plates_json()``."""
        return self.params.get_plates_json()

    def build_plan(self) -> Plan:
        """The robot-agnostic plan for this plating run."""
        p = self.params
        constructs = list(p.bacterium_locations.items())

        plan = Plan("Plating",
                    description=(f"Serial dilution and spot plating of {p.number_constructs} constructs, "
                                 f"{p.number_dilutions} dilution(s) of 1:{p.dilution_factor:g}, "
                                 f"{p.replicates} replicate(s) each"),
                    metadata={"protocolName": "PUDU Plating"})
        plan.add_module("thermocycler", ModuleKind.THERMOCYCLER)
        cultures = plan.add_labware("culture_plate", "plate_96", module="thermocycler",
                                    description="Transformed cultures")
        tubes = plan.add_labware("lb_tubes", "tube_rack_15", description="LB stock")
        lb_tube = tubes.wells()[p.lb_tube_position]

        plan.load(lb_tube, Liquid("LB", description="Liquid broth for dilutions", color="#D2B48C"), p.volume_lb)
        for well, names in constructs:
            plan.load(cultures[well], Liquid(Plating._format_construct_name(names), LiquidClass.CELLS),
                      p.volume_total_reaction)

        dilution_1, dilution_2 = self._dilution_wells(plan)
        spots = self._agar_spots(plan)

        plan.add(SetTemperature("thermocycler", 4))
        plan.add(SetLid("thermocycler", open=True))
        plan.add(TransferSet(transfers([(lb_tube, w, p.volume_lb_transfer) for w in dilution_1 + dilution_2]),
                             tips=TipPolicy.ONE, multi_dispense=True, disposal_volume=LB_DISPOSAL_VOLUME,
                             label="Distribute LB to dilution wells"))

        mix = Mix(p.volume_lb_transfer + p.volume_bacteria_transfer - 1, MIX_REPETITIONS)
        for index, (well, names) in enumerate(constructs):
            name = Plating._format_construct_name(names)
            first = dilution_1[index]
            moves = [Transfer(cultures[well], first, p.volume_bacteria_transfer, mix_after=mix)]
            if dilution_2:
                moves.append(Transfer(first, dilution_2[index], p.volume_bacteria_transfer, mix_after=mix))
            moves += [Transfer(first, spot, p.volume_colony, dispense_below_top=AGAR_DISPENSE_BELOW_TOP)
                      for spot in spots[(well, 1)]]
            plan.add(TransferSet(moves, liquid_class=LiquidClass.CELLS, ordering=Ordering.SEQUENTIAL,
                                 tips=TipPolicy.ONE, label=f"{name}: dilute and plate dilution 1"))
            if dilution_2:
                plan.add(TransferSet([Transfer(dilution_2[index], spot, p.volume_colony,
                                               dispense_below_top=AGAR_DISPENSE_BELOW_TOP)
                                      for spot in spots[(well, 2)]],
                                     liquid_class=LiquidClass.CELLS, tips=TipPolicy.ONE,
                                     label=f"{name}: plate dilution 2"))

        plan.add(Comment(f"Plated {p.number_constructs} constructs with {p.replicates} replicate(s): "
                         f"{p.total_colonies} spots"))
        return plan

    def compile(self, profile: RobotProfile) -> Bundle:
        """Compile for *profile*, adding the agar layout as JSON and Excel next to the protocol."""
        bundle = compile_plan(self.build_plan(), profile)
        name = self.params.protocol_name
        bundle.add(f"{name}.json", json.dumps(self.agar_plate_map(), indent=2) + "\n", FileRole.DATA)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{name}.xlsx"
            self.params.write_plates_excel(str(path))
            bundle.add(f"{name}.xlsx", path.read_bytes(), FileRole.DATA)
        return bundle

    def _dilution_wells(self, plan: Plan) -> Tuple[List[WellRef], List[WellRef]]:
        """Dilution 1 and 2 wells, per construct; same placement as ``Plating.calculate_plate_layout``."""
        p = self.params
        count = p.number_constructs
        plate_1 = plan.add_labware("dilution_plate_1", "plate_96", description="Dilutions")
        first = plate_1.wells()[:count]
        if p.number_dilutions < 2:
            return first, []
        if count <= 48:  # both dilutions share one plate, one half each
            return first, plate_1.wells()[48:48 + count]
        plate_2 = plan.add_labware("dilution_plate_2", "plate_96", description="Second dilutions")
        return first, plate_2.wells()[:count]

    def _agar_spots(self, plan: Plan) -> Dict[Tuple[str, int], List[WellRef]]:
        """Agar wells keyed by (source well, dilution step), in replicate order, from the Plating agar map."""
        agar_map = self.params.build_agar_plate_map()
        plates = {key: plan.add_labware(f"agar_{key}", "plate_96", description="Agar plate") for key in sorted(agar_map)}
        spots: Dict[Tuple[str, int], List[Tuple[int, WellRef]]] = defaultdict(list)
        for plate_key, dilutions in agar_map.items():
            for dilution_key, data in dilutions.items():
                step = int(dilution_key.split("_")[1])
                for well, info in data["wells"].items():
                    spots[(info["source_well"], step)].append((info["replicate"], plates[plate_key][well]))
        return {key: [ref for _, ref in sorted(wells)] for key, wells in spots.items()}
