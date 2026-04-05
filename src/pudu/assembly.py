import xlsxwriter
from opentrons import protocol_api
from typing import List, Dict, Optional
from fnmatch import fnmatch
from itertools import product
import json
import re
from abc import ABC, abstractmethod
from pudu.utils import Camera, colors

class BaseAssembly(ABC):
    """
    Abstract base class for Loop Assembly protocols.
    Contains shared hardware setup, liquid handling, and tip management functionality.
    """

    def __init__(self,
                 json_params: Optional[Dict] = None,
                 volume_total_reaction: float = 20,
                 volume_part: float = 2,
                 volume_restriction_enzyme: float = 2,
                 volume_t4_dna_ligase: float = 4,
                 volume_t4_dna_ligase_buffer: float = 2,
                 replicates: int = 1,
                 thermocycler_starting_well: int = 0,
                 thermocycler_labware: str = 'nest_96_wellplate_100ul_pcr_full_skirt',
                 temperature_module_labware: str = 'opentrons_24_aluminumblock_nest_1.5ml_snapcap',
                 temperature_module_position: str = '1',
                 tiprack_labware: str = 'opentrons_96_tiprack_20ul',
                 tiprack_positions: Optional[List[str]] = None,
                 pipette: str = 'p20_single_gen2',
                 pipette_position: str = 'left',
                 initial_tip: Optional[str] = None,
                 aspiration_rate: float = 0.5,
                 dispense_rate: float = 1,
                 take_picture: bool = False,
                 take_video: bool = False,
                 water_testing: bool = False,
                 output_xlsx: bool = True,
                 protocol_name: str = ''):

        kwargs_params = {
            'volume_total_reaction': volume_total_reaction,
            'volume_part': volume_part,
            'volume_restriction_enzyme': volume_restriction_enzyme,
            'volume_t4_dna_ligase': volume_t4_dna_ligase,
            'volume_t4_dna_ligase_buffer': volume_t4_dna_ligase_buffer,
            'replicates': replicates,
            'thermocycler_starting_well': thermocycler_starting_well,
            'thermocycler_labware': thermocycler_labware,
            'temperature_module_labware': temperature_module_labware,
            'temperature_module_position': temperature_module_position,
            'tiprack_labware': tiprack_labware,
            'tiprack_positions': tiprack_positions,
            'pipette': pipette,
            'pipette_position': pipette_position,
            'initial_tip' : initial_tip,
            'aspiration_rate': aspiration_rate,
            'dispense_rate': dispense_rate,
            'take_picture': take_picture,
            'take_video': take_video,
            'water_testing': water_testing,
            'output_xlsx': output_xlsx,
            'protocol_name': protocol_name
        }

        params = self._merge_params(json_params, kwargs_params)

        self.volume_total_reaction = params['volume_total_reaction']
        self.volume_part = params['volume_part']
        self.volume_restriction_enzyme = params['volume_restriction_enzyme']
        self.volume_t4_dna_ligase = params['volume_t4_dna_ligase']
        self.volume_t4_dna_ligase_buffer = params['volume_t4_dna_ligase_buffer']
        self.replicates = params['replicates']
        self.thermocycler_starting_well = params['thermocycler_starting_well']
        self.thermocycler_labware = params['thermocycler_labware']
        self.temperature_module_labware = params['temperature_module_labware']
        self.temperature_module_position = params['temperature_module_position']
        self.tiprack_labware = params['tiprack_labware']
        if params['tiprack_positions'] is None:
            self.tiprack_positions = ['2', '3', '4', '5', '6', '9']
        else:
            self.tiprack_positions =  params['tiprack_positions']
        self.pipette = params['pipette']
        self.pipette_position = params['pipette_position']
        self.initial_tip = params['initial_tip']
        self.aspiration_rate = params['aspiration_rate']
        self.dispense_rate = params['dispense_rate']
        self.take_picture = params['take_picture']
        self.take_video = params['take_video']
        self.water_testing = params['water_testing']
        self.output_xlsx = params['output_xlsx']
        self.protocol_name = params['protocol_name']

        # Shared tracking dictionaries
        self.dict_of_parts_in_temp_mod_position = {}
        self.dict_of_parts_in_thermocycler = {}
        self.dna_list_for_transformation_protocol = []
        self.product_uri_to_wells = {}
        self.xlsx_output = None
        self.source_metadata_by_well = {}
        self.manual_protocol_events = []
        self.reaction_metadata = []
        self.deck_setup = {
            'modules': [],
            'labware': []
        }

        #Initialize Camera
        self.camera = Camera()
        # Tip management
        self.tip_management = {
            'all_racks': [],
            'on_deck_racks': [],
            'off_deck_racks': [],
            'available_slots': [],
            'tips_used': 0,
            'tips_per_batch': 0,
            'current_batch': 1,
            'total_batches': 1
        }

    def _merge_params(self, json_params: Dict, kwargs_params: Dict) -> Dict:
        """
        Merge parameters with precedence: defaults <- advanced_params <- kwargs

        Args:
            advanced_params: Dictionary of parameters from JSON/dict (optional)
            kwargs_params: Dictionary of parameters from function kwargs

        Returns:
            Merged parameter dictionary

        Raises:
            ValueError: If advanced_params contains unknown parameters
        """
        # Define all valid parameter names with their defaults
        valid_params = {
            'volume_total_reaction': 20,
            'volume_part': 2,
            'volume_restriction_enzyme': 2,
            'volume_t4_dna_ligase': 4,
            'volume_t4_dna_ligase_buffer': 2,
            'replicates': 1,
            'thermocycler_starting_well': 0,
            'thermocycler_labware': 'nest_96_wellplate_100ul_pcr_full_skirt',
            'temperature_module_labware': 'opentrons_24_aluminumblock_nest_1.5ml_snapcap',
            'temperature_module_position': '1',
            'tiprack_labware': 'opentrons_96_tiprack_20ul',
            'tiprack_positions': None,
            'pipette': 'p20_single_gen2',
            'pipette_position': 'left',
            'initial_tip': None,
            'aspiration_rate': 0.5,
            'dispense_rate': 1,
            'take_picture': False,
            'take_video': False,
            'water_testing': False,
            'output_xlsx': True,
            'protocol_name': ''
        }

        # Start with defaults
        merged = valid_params.copy()

        # Apply json_params if provided
        if json_params is not None:
            self._validate_param_structure(json_params, valid_params)
            merged.update(json_params)

        # Apply kwargs (checking against defaults to see what was explicitly passed)
        # Only update if the kwarg value differs from the default
        for key, value in kwargs_params.items():
            if key in valid_params:
                # Always use kwargs value, even if it matches default
                # This ensures explicit kwargs override advanced_params
                if json_params is None or key not in json_params or value != valid_params[key]:
                    merged[key] = value

        return merged

    def _validate_param_structure(self, advanced_params: Dict, valid_params: Dict):
        """
        Validate that advanced_params only contains known parameter names.

        Args:
            advanced_params: Dictionary to validate
            valid_params: Dictionary of valid parameter names

        Raises:
            ValueError: If unknown parameters are found
        """
        unknown_params = set(advanced_params.keys()) - set(valid_params.keys())
        if unknown_params:
            raise ValueError(
                f"Unknown parameters in advanced_params: {unknown_params}. "
                f"Valid parameters are: {set(valid_params.keys())}"
            )

    def _validate_reaction_volumes(self, num_parts: int):
        """
        Validate that reaction volumes are physically possible.

        Args:
            num_parts: Number of DNA parts (including backbone) in the assembly

        Raises:
            ValueError: If volumes exceed total reaction volume
        """
        volume_reagents = (self.volume_restriction_enzyme +
                          self.volume_t4_dna_ligase +
                          self.volume_t4_dna_ligase_buffer)

        total_parts_volume = self.volume_part * num_parts
        total_needed = volume_reagents + total_parts_volume

        if total_needed >= self.volume_total_reaction:
            water_volume = self.volume_total_reaction - total_needed
            raise ValueError(
                f"Reaction volume error: Cannot fit {num_parts} parts into {self.volume_total_reaction}µL reaction.\n"
                f"  Required volumes:\n"
                f"    - Reagents (enzyme + ligase + buffer): {volume_reagents}µL\n"
                f"    - Parts ({num_parts} × {self.volume_part}µL): {total_parts_volume}µL\n"
                f"    - Water: {water_volume}µL (NEGATIVE!)\n"
                f"  Total needed: {total_needed}µL\n"
                f"  Solutions:\n"
                f"    1. Increase 'volume_total_reaction' to at least {total_needed + 1}µL\n"
                f"    2. Decrease 'volume_part' to at most {(self.volume_total_reaction - volume_reagents - 1) / num_parts:.1f}µL\n"
                f"    3. Decrease reagent volumes"
            )

    def _well_to_index(self, well_name: str) -> int:
        """
        Convert well name (e.g., 'A1', 'H12') to 0-based index in 96-well plate.

        Args:
            well_name: Well position like 'A1', 'B3', 'H12'

        Returns:
            Zero-based index (0-95) for the well

        Raises:
            ValueError: If well_name format is invalid
        """
        if not well_name or len(well_name) < 2:
            raise ValueError(f"Invalid well name: '{well_name}'. Expected format like 'A1', 'B3', 'H12'")

        row = well_name[0]
        try:
            col = int(well_name[1:])
        except ValueError:
            raise ValueError(f"Invalid well name: '{well_name}'. Column must be a number (e.g., 'A1', 'H12')")

        if row not in 'ABCDEFGH':
            raise ValueError(f"Invalid well name: '{well_name}'. Row must be A-H")

        if col < 1 or col > 12:
            raise ValueError(f"Invalid well name: '{well_name}'. Column must be 1-12")

        row_index = ord(row) - ord('A')
        col_index = col - 1
        return row_index + (col_index * 8)

    def _tips_available_from_position(self, well_name: str) -> int:
        """
        Calculate how many tips are available starting from a given position.

        Args:
            well_name: Starting well position like 'A1', 'H12'

        Returns:
            Number of tips available from that position to H12
        """
        start_index = self._well_to_index(well_name)
        return 96 - start_index

    @abstractmethod
    def process_assemblies(self):
        """Process input assemblies - format-specific implementation"""
        pass

    @abstractmethod
    def _load_parts_and_enzymes(self, protocol, alum_block) -> int:
        """Load parts and enzymes onto temperature module - format-specific"""
        pass

    @abstractmethod
    def _process_assembly_combinations(self, protocol, pipette, thermo_plate, alum_block,
                                       dd_h2o, t4_dna_ligase_buffer, t4_dna_ligase,
                                       volume_reagents, thermocycler_well_counter) -> int:
        """Process all assembly combinations - format-specific"""
        pass

    @abstractmethod
    def _calculate_total_tips_needed(self) -> int:
        """Calculate total tips needed - format-specific implementation"""
        pass

    def setup_tip_management(self, protocol):
        """Setup batch tip management for high-throughput applications."""
        total_tips_needed = self._calculate_total_tips_needed()

        first_rack_tips = 96
        if self.initial_tip:
            try:
                first_rack_tips = self._tips_available_from_position(self.initial_tip)
                protocol.comment(f"Starting from tip {self.initial_tip} ({first_rack_tips} tips available on first rack)")
            except ValueError as e:
                raise ValueError(f"Error with initial_tip parameter: {e}")

        # Calculate racks needed, accounting for the partially used first rack
        if total_tips_needed <= first_rack_tips:
            tip_racks_needed = 1
        else:
            remaining_tips = total_tips_needed - first_rack_tips
            additional_racks = (remaining_tips + 95) // 96
            tip_racks_needed = 1 + additional_racks

        available_deck_slots = self.tiprack_positions
        max_racks_on_deck = len(available_deck_slots)

        protocol.comment(f"Protocol requires {total_tips_needed} tips ({tip_racks_needed} racks)")

        all_tip_racks = []
        on_deck_racks = []
        for i in range(min(tip_racks_needed, max_racks_on_deck)):
            rack = protocol.load_labware(self.tiprack_labware, available_deck_slots[i])
            all_tip_racks.append(rack)
            on_deck_racks.append(rack)
            self._record_deck_component(
                component_type='labware',
                name=self.tiprack_labware,
                location=available_deck_slots[i]
            )

        off_deck_racks = []
        for i in range(max_racks_on_deck, tip_racks_needed):
            rack = protocol.load_labware(self.tiprack_labware, protocol_api.OFF_DECK)
            all_tip_racks.append(rack)
            off_deck_racks.append(rack)

        self.tip_management.update({
            'all_racks': all_tip_racks,
            'on_deck_racks': on_deck_racks,
            'off_deck_racks': off_deck_racks,
            'available_slots': available_deck_slots,
            'tips_used': 0,
            'tips_per_batch': max_racks_on_deck * 96,
            'current_batch': 1,
            'total_batches': (tip_racks_needed + max_racks_on_deck - 1) // max_racks_on_deck
        })

        if len(off_deck_racks) > 0:
            protocol.comment(f"Will perform {self.tip_management['total_batches'] - 1} tip rack batch swaps")

        return all_tip_racks

    def liquid_transfer(self, protocol, pipette, volume, source, dest,
                        asp_rate: float = 0.5, disp_rate: float = 1.0,
                        blow_out: bool = True, touch_tip: bool = False,
                        mix_before: float = 0.0, mix_after: float = 0.0,
                        mix_reps: int = 3, new_tip: bool = True,
                        drop_tip: bool = True, record_event: bool = True,
                        reaction_label: Optional[str] = None):
        if new_tip:
            if self._check_if_swap_needed():
                self._perform_tip_rack_batch_swap(protocol)
            try:
                pipette.pick_up_tip()
                self._increment_tip_counter()
            except Exception as e:
                protocol.comment(f"Tip pickup failed with error: {e}")
                raise

        if mix_before > 0:
            pipette.mix(mix_reps, mix_before, source)

        pipette.aspirate(volume, source, rate=asp_rate)
        pipette.dispense(volume, dest, rate=disp_rate)

        if mix_after > 0:
            pipette.mix(mix_reps, mix_after, dest)

        if blow_out:
            pipette.blow_out()

        if touch_tip:
            pipette.touch_tip(radius=0.5, v_offset=-14, speed=20)

        if drop_tip:
            pipette.drop_tip()

        if record_event:
            self._record_transfer_event(
                source=source, dest=dest, volume=volume,
                mix_before=mix_before, mix_after=mix_after,
                mix_reps=mix_reps, reaction_label=reaction_label
            )

    def get_xlsx_output(self, name: str):
        workbook = xlsxwriter.Workbook(f"{name}.xlsx")
        worksheet = workbook.add_worksheet()
        row_num = 0
        col_num = 0
        worksheet.write(row_num, col_num, "Parts in temp_module")
        row_num += 2
        for key, value in self.dict_of_parts_in_temp_mod_position.items():
            worksheet.write(row_num, col_num, key)
            worksheet.write(row_num + 1, col_num, value)
            col_num += 1
        col_num = 0
        row_num += 4
        worksheet.write(row_num, col_num, "Parts in thermocycler_module")
        row_num += 2
        for key, value in self.dict_of_parts_in_thermocycler.items():
            key_str = " + ".join(key) if isinstance(key, tuple) else str(key)
            worksheet.write(row_num, col_num, key_str)
            worksheet.write(row_num + 1, col_num, value)
            col_num += 1
        workbook.close()
        self.xlsx_output = workbook
        return self.xlsx_output

    def run(self, protocol: protocol_api.ProtocolContext):
        """Main protocol execution - uses template method pattern"""
        # Process assemblies (format-specific)
        self.process_assemblies()

        # Load hardware (shared)
        temperature_module = protocol.load_module(module_name='temperature module',
                                                  location=self.temperature_module_position)
        alum_block = temperature_module.load_labware(self.temperature_module_labware)
        self._record_deck_component(
            component_type='module',
            name='temperature module',
            location=self.temperature_module_position
        )
        self._record_deck_component(
            component_type='labware',
            name=self.temperature_module_labware,
            location=self.temperature_module_position,
            parent='temperature module'
        )

        thermocycler_module = protocol.load_module('thermocycler module')
        thermo_plate = thermocycler_module.load_labware(name=self.thermocycler_labware)
        self._record_deck_component(component_type='module', name='thermocycler module', location='built-in')
        self._record_deck_component(
            component_type='labware',
            name=self.thermocycler_labware,
            location='thermocycler',
            parent='thermocycler module'
        )

        all_tip_racks = self.setup_tip_management(protocol)
        pipette = protocol.load_instrument(self.pipette, self.pipette_position, tip_racks=all_tip_racks)
        if self.initial_tip:
            pipette.starting_tip = self.tip_management['on_deck_racks'][0][self.initial_tip]
            protocol.comment(f"Pipette will start from tip {self.initial_tip}")

        # Load common reagents (shared)
        dd_h2o = self._load_reagent(protocol, module_labware=alum_block, well_position=0,
                                    name='Deionized Water')
        t4_dna_ligase_buffer = self._load_reagent(protocol, module_labware=alum_block, well_position=1,
                                                  name='T4 DNA Ligase Buffer')
        t4_dna_ligase = self._load_reagent(protocol, module_labware=alum_block, well_position=2,
                                           name="T4 DNA Ligase")

        # Load parts and enzymes (format-specific)
        self._load_parts_and_enzymes(protocol, alum_block)

        # Setup temperatures
        thermocycler_module.open_lid()
        if not self.water_testing:
            temperature_module.set_temperature(4)
            thermocycler_module.set_block_temperature(4)

        # Media capture start
        if self.take_picture:
            self.camera.capture_picture(protocol, when="start")
        if self.take_video:
            self.camera.start_video(protocol)

        # Process assemblies (format-specific)
        volume_reagents = self.volume_restriction_enzyme + self.volume_t4_dna_ligase + self.volume_t4_dna_ligase_buffer
        self._process_assembly_combinations(
            protocol, pipette, thermo_plate, alum_block, dd_h2o,
            t4_dna_ligase_buffer, t4_dna_ligase, volume_reagents,
            self.thermocycler_starting_well
        )

        protocol.comment('Take out the reagents since the temperature module will be turn off')

        # Thermocycling
        if not self.water_testing:
            thermocycler_module.close_lid()
            thermocycler_module.set_lid_temperature(42)
            temperature_module.deactivate()

        # Media capture end
        if self.take_video:
            self.camera.stop_video(protocol)
        if self.take_picture:
            self.camera.capture_picture(protocol, when="end")

        # Execute thermocycling profiles
        if not self.water_testing:
            profile = [
                {'temperature': 42, 'hold_time_minutes': 2},
                {'temperature': 16, 'hold_time_minutes': 5}
            ]
            denaturation = [
                {'temperature': 60, 'hold_time_minutes': 10},
                {'temperature': 80, 'hold_time_minutes': 10}
            ]
            thermocycler_module.execute_profile(steps=profile, repetitions=75, block_max_volume=30)
            thermocycler_module.execute_profile(steps=denaturation, repetitions=1, block_max_volume=30)
            thermocycler_module.set_block_temperature(4)

        if protocol.is_simulating():
            if self.output_xlsx:
                try:
                    if not self.protocol_name:
                        self.protocol_name = "Loop Assembly"
                    self.get_xlsx_output(self.protocol_name)
                except Exception as e:
                    protocol.comment(f"Could not create Excel file: {e}")
            # Export transformation input for next protocol (simulation only)
            try:
                self._export_transformation_input(protocol)
            except Exception as e:
                protocol.comment(f"Could not export transformation input: {e}")
            try:
                self.generate_manual_protocol(protocol)
            except Exception as e:
                protocol.comment(f"Could not create manual protocol markdown: {e}")

        # Output results
        print('Parts and reagents in temp_module')
        print(self.dict_of_parts_in_temp_mod_position)
        print('Assembled parts in thermocycler_module')
        print(self.dict_of_parts_in_thermocycler)
        print('DNA list for transformation protocol')
        print(self.dna_list_for_transformation_protocol)

    # Helper methods (shared)
    def _export_transformation_input(self, protocol):
        """
        Export plasmid location JSON during simulation for use by transformation protocol.
        Format: { "product_uri": ["well1", "well2", ...], ... }
        """
        output_path = 'transformation_input.json'
        with open(output_path, 'w') as f:
            json.dump(self.product_uri_to_wells, f, indent=2)

        protocol.comment("\n" + "="*70)
        protocol.comment(f"Generated {output_path} for transformation protocol")
        protocol.comment(f"  Products: {len(self.product_uri_to_wells)}")
        protocol.comment("="*70)

    def _load_reagent(self, protocol, module_labware, well_position, name, description=None,
                      volume=1000, color_index=None, uri: Optional[str] = None,
                      implementation_name: Optional[str] = None):
        """Load a reagent or DNA part onto the temperature module."""
        well = module_labware.wells()[well_position]
        well_name = well.well_name

        if description is None:
            description = name
        if color_index is None:
            color_index = len(self.dict_of_parts_in_temp_mod_position) % len(colors)

        liquid = protocol.define_liquid(name=name, description=description,
                                        display_color=colors[color_index])
        well.load_liquid(liquid, volume=volume)

        self.dict_of_parts_in_temp_mod_position[name] = well_name
        source_key = self._build_source_key_from_location(module_labware, well)
        self.source_metadata_by_well[source_key] = {
            'display_name': name,
            'implementation_name': implementation_name or name,
            'uri': uri,
            'well_name': well_name,
            'labware_name': getattr(module_labware, "load_name", str(module_labware)),
            'module_or_slot': self.temperature_module_position
        }
        self.manual_protocol_events.append({
            'event_type': 'load_reagent',
            'display_name': name,
            'implementation_name': implementation_name or name,
            'uri': uri,
            'well_name': well_name,
            'labware_name': getattr(module_labware, "load_name", str(module_labware)),
            'module_or_slot': self.temperature_module_position
        })
        protocol.comment(f"Loaded {name} at position {well_name}")

        return well

    def _record_deck_component(self, component_type: str, name: str, location: str, parent: Optional[str] = None):
        record = {'name': name, 'location': location}
        if parent is not None:
            record['parent'] = parent
        self.deck_setup[f'{component_type}s'].append(record)

    def _build_source_key_from_location(self, labware, well) -> str:
        labware_name = getattr(labware, 'load_name', str(labware))
        return f"{labware_name}:{well.well_name}"

    def _build_source_key(self, location) -> Optional[str]:
        parent = getattr(location, 'parent', None)
        if parent is None:
            return None
        well_name = getattr(parent, 'well_name', None)
        if well_name is None:
            return None
        labware = getattr(parent, 'parent', None)
        if labware is None:
            return None
        labware_name = getattr(labware, 'load_name', str(labware))
        return f"{labware_name}:{well_name}"

    def _format_location_label(self, location) -> str:
        parent = getattr(location, 'parent', None)
        well_name = getattr(parent, 'well_name', None)
        labware = getattr(parent, 'parent', None)
        labware_name = getattr(labware, 'load_name', 'labware')
        if well_name:
            return f"{labware_name} well {well_name}"
        return labware_name

    def _record_transfer_event(self, source, dest, volume: float, mix_before: float, mix_after: float,
                               mix_reps: int, reaction_label: Optional[str] = None):
        source_key = self._build_source_key(source)
        source_meta = self.source_metadata_by_well.get(source_key, {})

        self.manual_protocol_events.append({
            'event_type': 'transfer',
            'volume_ul': volume,
            'source': {
                'location': self._format_location_label(source),
                'well_name': getattr(getattr(source, 'parent', None), 'well_name', None),
                'labware_name': getattr(getattr(getattr(source, 'parent', None), 'parent', None), 'load_name', None),
            },
            'destination': {
                'location': self._format_location_label(dest),
                'well_name': getattr(getattr(dest, 'parent', None), 'well_name', None),
                'labware_name': getattr(getattr(getattr(dest, 'parent', None), 'parent', None), 'load_name', None),
            },
            'source_material': {
                'display_name': source_meta.get('display_name'),
                'implementation_name': source_meta.get('implementation_name'),
                'uri': source_meta.get('uri'),
            },
            'mix_before_ul': mix_before,
            'mix_after_ul': mix_after,
            'mix_reps': mix_reps,
            'reaction_label': reaction_label
        })

    def _record_mix_event(self, destination, cycles: int, mix_volume: float, reaction_label: Optional[str] = None):
        self.manual_protocol_events.append({
            'event_type': 'mix',
            'destination': {
                'location': self._format_location_label(destination),
                'well_name': getattr(destination, 'well_name', None),
                'labware_name': getattr(getattr(destination, 'parent', None), 'load_name', None),
            },
            'cycles': cycles,
            'mix_volume_ul': mix_volume,
            'reaction_label': reaction_label
        })

    def _record_reaction_destination(self, reaction_label: str, destination_well: str,
                                     product_uri: Optional[str] = None):
        self.reaction_metadata.append({
            'reaction_label': reaction_label,
            'destination_well': destination_well,
            'product_uri': product_uri
        })

    def _perform_reaction_mix(self, protocol, pipette, dest_well, reaction_label: Optional[str] = None):
        mix_volume = min(self.volume_total_reaction, pipette.max_volume)
        cycles = int(self.volume_total_reaction / 10)
        for _ in range(cycles):
            self.liquid_transfer(protocol=protocol, pipette=pipette, volume=mix_volume,
                                 source=dest_well.bottom(), dest=dest_well.bottom(8),
                                 asp_rate=1.0, disp_rate=1.0, new_tip=False, drop_tip=False,
                                 touch_tip=True, record_event=False)
        self._record_mix_event(dest_well, cycles=cycles, mix_volume=mix_volume, reaction_label=reaction_label)

    def _render_manual_protocol_markdown(self) -> str:
        protocol_name = self.protocol_name or "Loop Assembly"
        lines = [
            f"# {protocol_name} Manual Protocol",
            "",
            "## Overview",
            "This document is a human-readable version of the simulated OT-2 assembly workflow.",
            "Follow the steps in order to manually execute the same transfers performed in simulation.",
            "",
            "## Inputs / assumptions",
            "- Volumes are in µL.",
            "- Source materials are pre-loaded according to the setup below.",
            "- Reaction destination wells are in thermocycler plate order.",
            "",
            "## Deck and reagent setup",
            "### Loaded modules",
        ]

        for module in self.deck_setup['modules']:
            lines.append(f"- {module['name']} at {module['location']}")

        lines.extend(["", "### Loaded labware"])
        for labware in self.deck_setup['labware']:
            parent = f" on {labware['parent']}" if 'parent' in labware else ""
            lines.append(f"- {labware['name']} at {labware['location']}{parent}")

        lines.extend([
            "",
            "## Source materials table",
            "| Material | Implementation | URI | Location |",
            "|---|---|---|---|",
        ])
        for _, source in sorted(self.source_metadata_by_well.items(), key=lambda kv: kv[1]['well_name']):
            uri = source['uri'] if source['uri'] else "-"
            lines.append(
                f"| {source['display_name']} | {source.get('implementation_name', source['display_name'])} | "
                f"{uri} | {source['labware_name']} well {source['well_name']} |"
            )

        lines.extend([
            "",
            "## Reaction destination table",
            "| Reaction / construct | Destination well | URI |",
            "|---|---|---|",
        ])
        for reaction in sorted(self.reaction_metadata, key=lambda r: self._well_to_index(r['destination_well'])):
            uri = reaction['product_uri'] if reaction['product_uri'] else "-"
            lines.append(f"| {reaction['reaction_label']} | {reaction['destination_well']} | {uri} |")

        lines.extend(["", "## Step-by-step instructions"])
        step = 1
        for event in self.manual_protocol_events:
            if event['event_type'] == 'transfer':
                src_material = event['source_material'].get('display_name') or "material"
                implementation_name = event['source_material'].get('implementation_name')
                src_location = event['source']['location']
                dst_location = event['destination']['location']
                uri = event['source_material'].get('uri')
                if implementation_name and implementation_name != src_material:
                    material_text = f"{src_material} (implementation: {implementation_name})"
                else:
                    material_text = src_material
                if uri:
                    material_text = f"{material_text} (URI: {uri})"
                lines.append(
                    f"{step}. Add {event['volume_ul']} µL of {material_text} from {src_location} to {dst_location}."
                )
                step += 1
            elif event['event_type'] == 'mix':
                lines.append(
                    f"{step}. Mix the reaction in well {event['destination']['well_name']} by pipetting "
                    f"up and down {event['cycles']} times at {event['mix_volume_ul']} µL."
                )
                step += 1

        lines.extend([
            "",
            "## Notes / warnings",
            "- This manual protocol is generated during simulation and mirrors transfer event order.",
            "- If a URI is unavailable for a material, only the display name is shown.",
        ])

        return "\n".join(lines) + "\n"

    def generate_manual_protocol(self, protocol):
        protocol_name = self.protocol_name or "Loop Assembly"
        safe_protocol_name = re.sub(r'[^A-Za-z0-9_.-]+', "_", protocol_name).strip("_") or "Loop_Assembly"
        output_path = f"{safe_protocol_name}_manual_protocol.md"
        markdown = self._render_manual_protocol_markdown()
        with open(output_path, 'w') as f:
            f.write(markdown)
        protocol.comment(f"Generated manual protocol markdown: {output_path}")

    def _increment_tip_counter(self):
        """Increment tip usage counter"""
        self.tip_management['tips_used'] += 1

    def _check_if_swap_needed(self):
        current_batch_tips = self.tip_management['tips_used'] % self.tip_management['tips_per_batch']
        return (current_batch_tips == 0 and
                self.tip_management['tips_used'] > 0 and
                len(self.tip_management['off_deck_racks']) > 0)

    def _perform_tip_rack_batch_swap(self, protocol):
        """Perform tip rack batch swap when current batch is exhausted"""
        available_slots = self.tip_management['available_slots']

        for rack in self.tip_management['on_deck_racks']:
            protocol.move_labware(labware=rack, new_location=protocol_api.OFF_DECK)

        remaining_racks = len(self.tip_management['off_deck_racks'])
        racks_to_move = min(len(available_slots), remaining_racks)

        new_on_deck_racks = []
        for i in range(racks_to_move):
            rack = self.tip_management['off_deck_racks'].pop(0)
            protocol.move_labware(labware=rack, new_location=available_slots[i])
            new_on_deck_racks.append(rack)

        self.tip_management['on_deck_racks'] = new_on_deck_racks
        self.tip_management['current_batch'] += 1

        protocol.comment(f"Tip rack batch {self.tip_management['current_batch']} ready!")

class Domestication(BaseAssembly):
    """
    Domestication Assembly - inserts individual parts into universal acceptor backbone.
    Each part is assembled separately with the backbone to create domesticated parts.
    """

    def __init__(self,
                 assembly_data: Optional[Dict] = None,
                 json_params: Optional[str] = None,
                 assemblies: Optional[List[Dict]] = None,
                 *args, **kwargs):
        """
        Initialize Domestication Assembly protocol.

        Args:
            assembly_data: Dict containing 'assemblies' key (new standardized approach)
            advanced_params: Optional advanced parameters
            assemblies: List of assembly dicts (backward compatibility)
            *args, **kwargs: Passed to BaseAssembly
        """
        # Handle parameter precedence: assembly_data <- assemblies kwarg
        if assembly_data is not None:
            if 'assemblies' in assembly_data:
                assemblies = assembly_data['assemblies']
            else:
                # Allow passing assemblies directly in assembly_data for flexibility
                assemblies = assembly_data

        # Validate that assemblies were provided
        if assemblies is None:
            raise ValueError("Must provide assemblies either via assembly_data or assemblies parameter")

        super().__init__(json_params=json_params, *args, **kwargs)
        self.assemblies = assemblies
        self.parts_list = []
        self.backbone = ""
        self.restriction_enzyme = ""

    def process_assemblies(self):
        """Process domestication assembly input and validate format"""
        self._reset_assembly_state()
        # Domestication should have exactly one assembly
        if len(self.assemblies) != 1:
            raise ValueError(f"Domestication supports exactly one assembly, got {len(self.assemblies)}")

        assembly = self.assemblies[0]
        required_keys = {"parts", "backbone", "restriction_enzyme"}
        assembly_keys = set(assembly.keys())

        if not required_keys.issubset(assembly_keys):
            missing_keys = required_keys - assembly_keys
            raise ValueError(f"Domestication assembly missing required keys: {missing_keys}")

        # Extract and validate parts
        parts = assembly["parts"]
        if isinstance(parts, str):
            self.parts_list = [parts]
        elif isinstance(parts, list):
            self.parts_list = parts
        else:
            raise ValueError("Parts must be a string or list of strings")

        # Extract and validate backbone (must be single value)
        backbone = assembly["backbone"]
        if isinstance(backbone, list):
            if len(backbone) > 1:
                raise ValueError("Domestication supports only one backbone")
            self.backbone = backbone[0]
        else:
            self.backbone = backbone

        # Extract and validate restriction enzyme (must be single value)
        restriction_enzyme = assembly["restriction_enzyme"]
        if isinstance(restriction_enzyme, list):
            if len(restriction_enzyme) > 1:
                raise ValueError("Domestication supports only one restriction enzyme")
            self.restriction_enzyme = restriction_enzyme[0]
        else:
            self.restriction_enzyme = restriction_enzyme

        self._validate_assembly_requirements()

    def _load_parts_and_enzymes(self, protocol, alum_block) -> int:
        """Load restriction enzyme, backbone, and parts for domestication"""
        temp_module_well_counter = 3  # Starting after common reagents (water, ligase buffer, ligase)

        # Load restriction enzyme
        self._load_reagent(protocol, module_labware=alum_block,
                           well_position=temp_module_well_counter,
                           name=f"Restriction Enzyme {self.restriction_enzyme}")
        temp_module_well_counter += 1

        # Load backbone
        self._load_reagent(protocol, module_labware=alum_block,
                           well_position=temp_module_well_counter,
                           name=f"Backbone {self.backbone}")
        temp_module_well_counter += 1

        # Load individual parts
        for part in self.parts_list:
            self._load_reagent(protocol, module_labware=alum_block,
                               well_position=temp_module_well_counter,
                               name=f"Part {part}")
            temp_module_well_counter += 1

        return temp_module_well_counter

    def _process_assembly_combinations(self, protocol, pipette, thermo_plate, alum_block,
                                       dd_h2o, t4_dna_ligase_buffer, t4_dna_ligase,
                                       volume_reagents, thermocycler_well_counter) -> int:
        """Process domestication assemblies - each part with backbone separately"""

        # Get reagent sources
        restriction_enzyme = alum_block[
            self.dict_of_parts_in_temp_mod_position[f"Restriction Enzyme {self.restriction_enzyme}"]]
        backbone_source = alum_block[self.dict_of_parts_in_temp_mod_position[f"Backbone {self.backbone}"]]

        # Process each part
        for part in self.parts_list:
            part_source = alum_block[self.dict_of_parts_in_temp_mod_position[f"Part {part}"]]

            # Process replicates for this part
            for r in range(self.replicates):
                dest_well = thermo_plate.wells()[thermocycler_well_counter]
                dest_well_name = dest_well.well_name

                # Calculate water volume (total - reagents - 2 parts: backbone + part)
                volume_dd_h20 = self.volume_total_reaction - (volume_reagents + self.volume_part * 2)

                # Add reagents
                self.liquid_transfer(protocol=protocol, pipette=pipette, volume=volume_dd_h20,
                                     source=dd_h2o, dest=dest_well,
                                     asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                     touch_tip=True, reaction_label=f"{part}_rep{r + 1}")

                self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_t4_dna_ligase_buffer,
                                     source=t4_dna_ligase_buffer, dest=dest_well,
                                     asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                     mix_before=self.volume_t4_dna_ligase_buffer, touch_tip=True,
                                     reaction_label=f"{part}_rep{r + 1}")

                self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_t4_dna_ligase,
                                     source=t4_dna_ligase, dest=dest_well,
                                     asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                     mix_before=self.volume_t4_dna_ligase, touch_tip=True,
                                     reaction_label=f"{part}_rep{r + 1}")

                self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_restriction_enzyme,
                                     source=restriction_enzyme, dest=dest_well,
                                     asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                     mix_before=self.volume_restriction_enzyme, touch_tip=True,
                                     reaction_label=f"{part}_rep{r + 1}")

                # Add backbone
                self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_part,
                                     source=backbone_source, dest=dest_well,
                                     asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                     mix_before=self.volume_part, touch_tip=True,
                                     reaction_label=f"{part}_rep{r + 1}")

                # Add part (don't drop tip yet)
                self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_part,
                                     source=part_source, dest=dest_well,
                                     asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                     mix_before=self.volume_part, touch_tip=True, drop_tip=False,
                                     reaction_label=f"{part}_rep{r + 1}")

                # Remove air bubbles with mixing
                self._perform_reaction_mix(protocol=protocol, pipette=pipette, dest_well=dest_well,
                                           reaction_label=f"{part}_rep{r + 1}")
                pipette.drop_tip()

                # Track assembly
                assembly_name = f"Part: {part}, Replicate: {r + 1}"
                self.dict_of_parts_in_thermocycler[assembly_name] = dest_well_name
                self.dna_list_for_transformation_protocol.append(f"{part}_rep{r + 1}")
                self._record_reaction_destination(reaction_label=assembly_name, destination_well=dest_well_name)

                thermocycler_well_counter += 1

        return thermocycler_well_counter

    def _calculate_total_tips_needed(self, number_of_constant_reagents: int = 6) -> int:
        """Calculate total tips needed for domestication

        Args:
            number_of_constant_reagents: water + ligase buffer + ligase + enzyme + backbone + part = 6
        """
        total_assemblies = len(self.parts_list) * self.replicates
        return number_of_constant_reagents * total_assemblies

    def _validate_assembly_requirements(self):
        """Validate domestication assembly requirements"""
        if not self.parts_list:
            raise ValueError("No parts provided for domestication")

        if not self.backbone:
            raise ValueError("No backbone provided for domestication")

        if not self.restriction_enzyme:
            raise ValueError("No restriction enzyme provided for domestication")

        # Calculate reagent positions: water(1) + ligase buffer(1) + ligase(1) + enzyme(1) + backbone(1) = 5
        reagent_positions = 5
        max_parts = 24 - reagent_positions

        if len(self.parts_list) > max_parts:
            raise ValueError(
                f'This protocol only supports domestication with up to {max_parts} parts. '
                f'Number of parts provided is {len(self.parts_list)}. '
                f'Parts: {self.parts_list}. '
                f'Reagent positions used: {reagent_positions}/24'
            )

        # Validate thermocycler capacity
        available_wells = 96 - self.thermocycler_starting_well
        wells_needed = len(self.parts_list) * self.replicates

        if wells_needed > available_wells:
            raise ValueError(
                f'This protocol only supports assemblies with up to {available_wells} '
                f'wells. Number of assemblies needed is {wells_needed} '
                f'({len(self.parts_list)} parts × {self.replicates} replicates).'
            )

        self._validate_reaction_volumes(num_parts=2)

    def _reset_assembly_state(self):
        """Reset assembly processing state"""
        self.parts_list = []
        self.backbone = ""
        self.restriction_enzyme = ""


class ManualLoopAssembly(BaseAssembly):
    """
    Manual/Combinatorial Loop Assembly - generates combinations from roles.
    Supports Odd/Even pattern detection for automatic enzyme selection.
    """

    def __init__(self,
                 assembly_data: Optional[Dict] = None,
                 json_params: Optional[str] = None,
                 assemblies: Optional[List[Dict]] = None,
                 *args, **kwargs):
        """
        Initialize Manual Loop Assembly protocol.

        Args:
            assembly_data: Dict containing 'assemblies' key (new standardized approach)
            json_params: Optional advanced parameters
            assemblies: List of assembly dicts (backward compatibility)
            *args, **kwargs: Passed to BaseAssembly
        """
        # Handle parameter precedence: assembly_data <- assemblies kwarg
        if assembly_data is not None:
            if 'assemblies' in assembly_data:
                assemblies = assembly_data['assemblies']
            else:
                # Allow passing assemblies directly in assembly_data for flexibility
                assemblies = assembly_data

        # Validate that assemblies were provided
        if assemblies is None:
            raise ValueError("Must provide assemblies either via assembly_data or assemblies parameter")

        super().__init__(json_params=json_params, *args, **kwargs)

        self.assemblies = assemblies
        self.pattern_odd = 'Odd*'
        self.pattern_even = 'Even*'
        self.parts_set = set()
        self.has_odd = False
        self.has_even = False
        self.odd_combinations = []
        self.even_combinations = []

    def process_assemblies(self):
        """Process manual format assemblies and generate combinations"""
        self._reset_assembly_state()

        for assembly in self.assemblies:
            assembly_type = self._get_assembly_type(assembly['receiver'])
            if assembly_type == 'odd':
                self.has_odd = True
                combos = self._generate_combinations_for_assembly(assembly)
                self.odd_combinations.extend(combos)
            if assembly_type == 'even':
                self.has_even = True
                combos = self._generate_combinations_for_assembly(assembly)
                self.even_combinations.extend(combos)

        self._validate_assembly_requirements()

    def _load_parts_and_enzymes(self, protocol, alum_block) -> int:
        """Load enzymes and parts for manual format"""
        temp_module_well_counter = 3  # Starting after common reagents

        # Load enzymes based on Odd/Even detection
        if self.has_odd:
            self._load_reagent(protocol, module_labware=alum_block,
                               well_position=temp_module_well_counter,
                               name="Restriction Enzyme BSAI")
            temp_module_well_counter += 1

        if self.has_even:
            self._load_reagent(protocol, module_labware=alum_block,
                               well_position=temp_module_well_counter,
                               name="Restriction Enzyme SAPI")
            temp_module_well_counter += 1

        # Load parts
        for part in sorted(self.parts_set):
            self._load_reagent(protocol, module_labware=alum_block,
                               well_position=temp_module_well_counter,
                               name=f"{part}")
            temp_module_well_counter += 1

        return temp_module_well_counter

    def _process_assembly_combinations(self, protocol, pipette, thermo_plate, alum_block,
                                       dd_h2o, t4_dna_ligase_buffer, t4_dna_ligase,
                                       volume_reagents, thermocycler_well_counter) -> int:
        """Process manual format combinations with automatic enzyme selection"""

        if self.has_odd:
            restriction_enzyme_bsai = alum_block[self.dict_of_parts_in_temp_mod_position["Restriction Enzyme BSAI"]]
            thermocycler_well_counter = self._process_combinations(
                protocol=protocol, pipette=pipette,
                combinations=self.odd_combinations,
                restriction_enzyme=restriction_enzyme_bsai,
                thermo_plate=thermo_plate, alum_block=alum_block,
                dd_h2o=dd_h2o, t4_dna_ligase_buffer=t4_dna_ligase_buffer,
                t4_dna_ligase=t4_dna_ligase, volume_reagents=volume_reagents,
                thermocycler_well_counter=thermocycler_well_counter
            )

        if self.has_even:
            restriction_enzyme_sapi = alum_block[self.dict_of_parts_in_temp_mod_position["Restriction Enzyme SAPI"]]
            thermocycler_well_counter = self._process_combinations(
                protocol=protocol, pipette=pipette,
                combinations=self.even_combinations,
                restriction_enzyme=restriction_enzyme_sapi,
                thermo_plate=thermo_plate, alum_block=alum_block,
                dd_h2o=dd_h2o, t4_dna_ligase_buffer=t4_dna_ligase_buffer,
                t4_dna_ligase=t4_dna_ligase, volume_reagents=volume_reagents,
                thermocycler_well_counter=thermocycler_well_counter
            )

        return thermocycler_well_counter

    def _calculate_total_tips_needed(self, number_of_constant_reagents: int = 4) -> int:
        """Calculate total tips for manual format"""
        total_combinations = len(self.odd_combinations) + len(self.even_combinations)
        reagent_tips = number_of_constant_reagents
        total_reagent_tips = reagent_tips * total_combinations * self.replicates

        total_part_tips = 0
        for combination in self.odd_combinations + self.even_combinations:
            total_part_tips += len(combination) * self.replicates

        return total_reagent_tips + total_part_tips

    # Manual format helper methods
    def _reset_assembly_state(self):
        """Reset assembly processing state"""
        self.parts_set = set()
        self.has_odd = False
        self.has_even = False
        self.odd_combinations = []
        self.even_combinations = []

    def _get_assembly_type(self, receiver_name):
        """Determine if assembly is odd, even, or neither"""
        if fnmatch(receiver_name, self.pattern_odd):
            return 'odd'
        if fnmatch(receiver_name, self.pattern_even):
            return 'even'
        raise ValueError(
            f"Assembly receiver '{receiver_name}' does not match naming convention. "
            f"Must be odd pattern '{self.pattern_odd}' or even pattern '{self.pattern_even}'. "
            f"Check receiver naming."
        )

    def _generate_combinations_for_assembly(self, assembly):
        """Generate all possible part combinations for a single assembly"""
        parts_per_role = []
        for role, parts in assembly.items():
            if isinstance(parts, str):
                parts_list = [parts]
            else:
                parts_list = list(parts)

            self.parts_set.update(parts_list)
            parts_per_role.append(parts_list)

        return list(product(*parts_per_role))

    def _validate_assembly_requirements(self):
        """Validate manual assembly requirements"""
        if not (self.has_odd or self.has_even):
            raise ValueError(
                "Assembly does not have any Even or Odd receiver. "
                "Check assembly dictionaries for Odd and Even receivers."
            )

        reagent_positions = 3 + int(self.has_odd) + int(self.has_even)
        max_parts = 24 - reagent_positions

        if len(self.parts_set) > max_parts:
            raise ValueError(
                f'This protocol only supports assemblies with up to {max_parts} parts. '
                f'Number of parts in the protocol is {len(self.parts_set)}. '
                f'Parts: {self.parts_set}. '
                f'Reagent positions used: {reagent_positions}/24'
            )

        available_wells = 96 - self.thermocycler_starting_well
        total_combinations = len(self.odd_combinations) + len(self.even_combinations)
        wells_needed = total_combinations * self.replicates

        if wells_needed > available_wells:
            raise ValueError(
                f'This protocol only supports assemblies with up to {available_wells} '
                f'combinations. Number of combinations in the protocol are {wells_needed}.'
            )

        # Validate reaction volumes for all combinations
        for combination in self.odd_combinations + self.even_combinations:
            num_parts = len(combination)
            self._validate_reaction_volumes(num_parts)

    def _process_combinations(self, protocol, pipette, combinations, restriction_enzyme,
                              thermo_plate, alum_block, dd_h2o, t4_dna_ligase_buffer,
                              t4_dna_ligase, volume_reagents, thermocycler_well_counter):
        """Process combinations with specified restriction enzyme"""

        for combination in combinations:
            for r in range(self.replicates):
                dest_well = thermo_plate.wells()[thermocycler_well_counter]
                dest_well_name = dest_well.well_name

                volume_dd_h20 = self.volume_total_reaction - (volume_reagents + self.volume_part * len(combination))

                # Add reagents
                self.liquid_transfer(protocol=protocol, pipette=pipette, volume=volume_dd_h20,
                                     source=dd_h2o, dest=dest_well,
                                     asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                     touch_tip=True, reaction_label=str(combination))

                self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_t4_dna_ligase_buffer,
                                     source=t4_dna_ligase_buffer, dest=dest_well,
                                     asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                     mix_before=self.volume_t4_dna_ligase_buffer, touch_tip=True,
                                     reaction_label=str(combination))

                self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_t4_dna_ligase,
                                     source=t4_dna_ligase, dest=dest_well,
                                     asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                     mix_before=self.volume_t4_dna_ligase, touch_tip=True,
                                     reaction_label=str(combination))

                self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_restriction_enzyme,
                                     source=restriction_enzyme, dest=dest_well,
                                     asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                     mix_before=self.volume_restriction_enzyme, touch_tip=True,
                                     reaction_label=str(combination))

                # Add parts
                for i, part in enumerate(combination):
                    part_source = alum_block[self.dict_of_parts_in_temp_mod_position[part]]
                    if i == len(combination) - 1:  # Don't drop tip on last part
                        self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_part,
                                             source=part_source, dest=dest_well,
                                             asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                             mix_before=self.volume_part, touch_tip=True, drop_tip=False,
                                             reaction_label=str(combination))
                    else:
                        self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_part,
                                             source=part_source, dest=dest_well,
                                             asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                             mix_before=self.volume_part, touch_tip=True,
                                             reaction_label=str(combination))

                # Remove air bubbles
                self._perform_reaction_mix(protocol=protocol, pipette=pipette, dest_well=dest_well,
                                           reaction_label=str(combination))
                pipette.drop_tip()

                # Track combination
                self.dict_of_parts_in_thermocycler[f"Replicate: {r + 1}, Combination: {combination}"] = dest_well_name
                combination_name = "_".join(combination)
                self.dna_list_for_transformation_protocol.append(f"{combination_name}_rep{r + 1}")
                self._record_reaction_destination(
                    reaction_label=f"Replicate: {r + 1}, Combination: {combination}",
                    destination_well=dest_well_name
                )
                thermocycler_well_counter += 1

        return thermocycler_well_counter


class SBOLLoopAssembly(BaseAssembly):
    """
    SBOL Loop Assembly - handles explicit assembly dictionaries from SBOL format.
    Each assembly dictionary represents one specific construct to build.
    """

    def __init__(self,
                 assembly_data: Optional[Dict] = None,
                 json_params: Optional[str] = None,
                 assemblies: Optional[List[Dict]] = None,
                 *args, **kwargs):
        """
        Initialize SBOL Loop Assembly protocol.

        Args:
            assembly_data: Dict containing 'assemblies' key (new standardized approach)
            advanced_params: Optional advanced parameters
            assemblies: List of assembly dicts (backward compatibility)
            *args, **kwargs: Passed to BaseAssembly
        """
        # Handle parameter precedence: assembly_data <- assemblies kwarg
        if assembly_data is not None:
            if 'assemblies' in assembly_data:
                assemblies = assembly_data['assemblies']
            else:
                # Allow passing assemblies directly in assembly_data for flexibility
                assemblies = assembly_data

        # Validate that assemblies were provided
        if assemblies is None:
            raise ValueError("Must provide assemblies either via assembly_data or assemblies parameter")

        super().__init__(json_params=json_params, *args, **kwargs)
        self.assemblies = assemblies
        self.parts_set = set()
        self.backbone_set = set()
        self.restriction_enzyme_set = set()
        self.combined_set = set()
        self.assembly_combinations = []  # SBOL assemblies are explicit, not combinatorial
        self.part_name_to_uri = {}
        self.enzyme_name_to_uri = {}

    def process_assemblies(self):
        """Process SBOL format assemblies - each is explicit, no combinations needed"""
        self._reset_assembly_state()

        for assembly in self.assemblies:
            # Extract parts from PartsList
            part_names = []
            for part_uri in assembly["PartsList"]:
                part_name = self._extract_name_from_uri(part_uri)
                self.parts_set.add(part_name)
                self.part_name_to_uri[part_name] = part_uri
                part_names.append(part_name)

            # Extract backbone
            backbone_name = self._extract_name_from_uri(assembly["Backbone"])
            self.backbone_set.add(backbone_name)
            self.part_name_to_uri[backbone_name] = assembly["Backbone"]

            # Extract restriction enzyme
            enzyme_name = self._extract_name_from_uri(assembly["Restriction Enzyme"])
            self.restriction_enzyme_set.add(enzyme_name)
            self.enzyme_name_to_uri[enzyme_name] = assembly["Restriction Enzyme"]

            # Extract product name
            product_name = self._extract_name_from_uri(assembly["Product"])
            assembly_combo = {
                'parts': [backbone_name] + part_names,  # Include backbone as first part
                'enzyme': enzyme_name,
                'product': product_name,
                'product_uri': assembly["Product"]
            }
            self.assembly_combinations.append(assembly_combo)

        self.combined_set = self.parts_set.union(self.backbone_set)
        self._validate_assembly_requirements()

    def _load_parts_and_enzymes(self, protocol, alum_block) -> int:
        """Load enzymes and parts for SBOL format"""
        temp_module_well_counter = 3  # Starting after common reagents

        # Load all unique restriction enzymes
        for enzyme_name in sorted(self.restriction_enzyme_set):
            self._load_reagent(protocol, module_labware=alum_block,
                               well_position=temp_module_well_counter,
                               name=f"Restriction Enzyme {enzyme_name}",
                               uri=self.enzyme_name_to_uri.get(enzyme_name),
                               implementation_name=enzyme_name)
            temp_module_well_counter += 1

        # Load all unique parts (including backbones)
        for part in sorted(self.combined_set):
            self._load_reagent(protocol, module_labware=alum_block,
                               well_position=temp_module_well_counter,
                               name=f"{part}",
                               uri=self.part_name_to_uri.get(part),
                               implementation_name=part)
            temp_module_well_counter += 1

        return temp_module_well_counter

    def _process_assembly_combinations(self, protocol, pipette, thermo_plate, alum_block,
                                       dd_h2o, t4_dna_ligase_buffer, t4_dna_ligase,
                                       volume_reagents, thermocycler_well_counter) -> int:
        """Process SBOL assembly combinations with explicit enzyme selection"""

        for assembly_combo in self.assembly_combinations:
            for r in range(self.replicates):
                dest_well = thermo_plate.wells()[thermocycler_well_counter]
                dest_well_name = dest_well.well_name

                parts = assembly_combo['parts']
                enzyme_name = assembly_combo['enzyme']
                product_name = assembly_combo['product']

                volume_dd_h20 = self.volume_total_reaction - (volume_reagents + self.volume_part * len(parts))

                # Add reagents
                self.liquid_transfer(protocol=protocol, pipette=pipette, volume=volume_dd_h20,
                                     source=dd_h2o, dest=dest_well,
                                     asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                     touch_tip=True, reaction_label=product_name)

                self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_t4_dna_ligase_buffer,
                                     source=t4_dna_ligase_buffer, dest=dest_well,
                                     asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                     mix_before=self.volume_t4_dna_ligase_buffer, touch_tip=True,
                                     reaction_label=product_name)

                self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_t4_dna_ligase,
                                     source=t4_dna_ligase, dest=dest_well,
                                     asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                     mix_before=self.volume_t4_dna_ligase, touch_tip=True,
                                     reaction_label=product_name)

                # Add restriction enzyme (explicit from SBOL)
                restriction_enzyme = alum_block[
                    self.dict_of_parts_in_temp_mod_position[f"Restriction Enzyme {enzyme_name}"]]
                self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_restriction_enzyme,
                                     source=restriction_enzyme, dest=dest_well,
                                     asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                     mix_before=self.volume_restriction_enzyme, touch_tip=True,
                                     reaction_label=product_name)

                # Add parts (including backbone)
                for i, part in enumerate(parts):
                    part_source = alum_block[self.dict_of_parts_in_temp_mod_position[part]]
                    if i == len(parts) - 1:  # Don't drop tip on last part
                        self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_part,
                                             source=part_source, dest=dest_well,
                                             asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                             mix_before=self.volume_part, touch_tip=True, drop_tip=False,
                                             reaction_label=product_name)
                    else:
                        self.liquid_transfer(protocol=protocol, pipette=pipette, volume=self.volume_part,
                                             source=part_source, dest=dest_well,
                                             asp_rate=self.aspiration_rate, disp_rate=self.dispense_rate,
                                             mix_before=self.volume_part, touch_tip=True,
                                             reaction_label=product_name)

                # Remove air bubbles
                self._perform_reaction_mix(protocol=protocol, pipette=pipette, dest_well=dest_well,
                                           reaction_label=product_name)
                pipette.drop_tip()

                # Track assembly
                self.dict_of_parts_in_thermocycler[f"Replicate: {r + 1}, Product: {product_name}"] = dest_well_name
                self.dna_list_for_transformation_protocol.append(f"{product_name}_rep{r + 1}")

                # Track URI -> well locations for transformation export
                product_uri = assembly_combo['product_uri']
                if product_uri not in self.product_uri_to_wells:
                    self.product_uri_to_wells[product_uri] = []
                self.product_uri_to_wells[product_uri].append(dest_well_name)
                self._record_reaction_destination(
                    reaction_label=f"Replicate: {r + 1}, Product: {product_name}",
                    destination_well=dest_well_name,
                    product_uri=product_uri
                )

                thermocycler_well_counter += 1

        return thermocycler_well_counter

    def _calculate_total_tips_needed(self, number_of_constant_reagents: int = 4) -> int:
        """Calculate total tips for SBOL format"""
        total_assemblies = len(self.assembly_combinations)
        reagent_tips = number_of_constant_reagents
        total_reagent_tips = reagent_tips * total_assemblies * self.replicates

        total_part_tips = 0
        for assembly_combo in self.assembly_combinations:
            total_part_tips += len(assembly_combo['parts']) * self.replicates

        return total_reagent_tips + total_part_tips

    # SBOL format helper methods
    def _reset_assembly_state(self):
        """Reset assembly processing state"""
        self.parts_set = set()
        self.backbone_set = set()
        self.restriction_enzyme_set = set()
        self.combined_set = set()
        self.assembly_combinations = []
        self.part_name_to_uri = {}
        self.enzyme_name_to_uri = {}

    def _extract_name_from_uri(self, uri: str) -> str:
        """Extract part name from SBOL URI"""
        # Extract the last segment after the last '/'
        if '/' in uri:
            name_with_version = uri.split('/')[-2]
            # Remove version number if present (e.g., "GFP/1" -> "GFP")
            if '/' in name_with_version:
                return name_with_version.split('/')[0]
            return name_with_version
        return uri

    def _validate_assembly_requirements(self):
        """Validate SBOL assembly requirements"""
        if not self.assembly_combinations:
            raise ValueError("No valid SBOL assemblies found in input.")

        # Calculate reagent positions: water(1) + ligase(1) + buffer(1) + unique enzymes
        reagent_positions = 3 + len(self.restriction_enzyme_set)
        max_parts = 24 - reagent_positions

        if len(self.combined_set) > max_parts:
            raise ValueError(
                f'This protocol only supports assemblies with up to {max_parts} parts. '
                f'Number of parts in the protocol is {len(self.combined_set)}. '
                f'Parts: {self.combined_set}. '
                f'Reagent positions used: {reagent_positions}/24'
            )

        # Validate thermocycler capacity
        available_wells = 96 - self.thermocycler_starting_well
        wells_needed = len(self.assembly_combinations) * self.replicates

        if wells_needed > available_wells:
            raise ValueError(
                f'This protocol only supports assemblies with up to {available_wells} '
                f'combinations. Number of assemblies in the protocol are {wells_needed}.'
            )

        # Validate reaction volumes for each assembly
        for assembly_combo in self.assembly_combinations:
            num_parts = len(assembly_combo['parts'])
            self._validate_reaction_volumes(num_parts)


class LoopAssembly:
    """
    Factory class that auto-detects input format and returns appropriate subclass.
    Supports both manual/combinatorial and SBOL format assemblies.
    """

    def __new__(cls, assemblies: List[Dict], *args, **kwargs):
        """Factory method that detects format and returns appropriate instance"""
        if not assemblies:
            raise ValueError("No assemblies provided")

        # Detect format based on first assembly
        first_assembly = assemblies[0]

        if cls._is_sbol_format(first_assembly):
            print("Detected SBOL format - using SBOLLoopAssembly")
            return SBOLLoopAssembly(assemblies, *args, **kwargs)
        elif cls._is_manual_format(first_assembly):
            print("Detected Manual format - using ManualLoopAssembly")
            return ManualLoopAssembly(assemblies, *args, **kwargs)
        else:
            raise ValueError(
                f"Unknown assembly format. Assembly must contain either:\n"
                f"- SBOL format: 'Product', 'Backbone', 'PartsList', 'Restriction Enzyme'\n"
                f"- Manual format: 'receiver' and role keys like 'promoter', 'rbs', etc.\n"
                f"Found keys: {list(first_assembly.keys())}"
            )

    @staticmethod
    def _is_sbol_format(assembly: Dict) -> bool:
        """Check if assembly matches SBOL format"""
        sbol_keys = {'Product', 'Backbone', 'PartsList', 'Restriction Enzyme'}
        assembly_keys = set(assembly.keys())

        # Check for SBOL-specific keys
        has_sbol_keys = sbol_keys.issubset(assembly_keys)

        # Check for URI patterns (https://)
        has_uri_patterns = any(
            isinstance(v, str) and v.startswith('https://')
            for v in assembly.values()
        ) or any(
            isinstance(v, list) and any(
                isinstance(item, str) and item.startswith('https://')
                for item in v
            )
            for v in assembly.values()
        )

        return has_sbol_keys and has_uri_patterns

    @staticmethod
    def _is_manual_format(assembly: Dict) -> bool:
        """Check if assembly matches manual/combinatorial format"""
        # Must have 'receiver' key
        if 'receiver' not in assembly:
            return False

        # Should have role-based keys (not SBOL keys)
        sbol_keys = {'Product', 'Backbone', 'PartsList', 'Restriction Enzyme'}
        assembly_keys = set(assembly.keys())

        # Should not have SBOL keys
        has_sbol_keys = bool(sbol_keys.intersection(assembly_keys))

        # Should have role keys beyond 'receiver'
        role_keys = assembly_keys - {'receiver'}
        has_role_keys = len(role_keys) > 0

        return not has_sbol_keys and has_role_keys

# Default assemblies for testing
DEFAULT_DOMESTICATION_ASSEMBLY = [
    {"parts": ["part1", "part2"], "backbone": "acceptor", "restriction_enzyme": "BsaI"},
]

DEFAULT_MANUAL_ASSEMBLIES = [
    {"promoter": ["GVP0008"], "rbs": "B0034",
     "cds": "sfGFP", "terminator": "B0015", "receiver": "Odd_1"}
]

DEFAULT_SBOL_ASSEMBLIES = [
    {
        "Product": "https://SBOL2Build.org/composite_1/1",
        "Backbone": "https://sbolcanvas.org/Cir_qxow/1",
        "PartsList": [
            "https://sbolcanvas.org/GFP/1",
            "https://sbolcanvas.org/B0015/1",
            "https://sbolcanvas.org/J23101/1",
            "https://sbolcanvas.org/B0034/1"
        ],
        "Restriction Enzyme": "https://SBOL2Build.org/BsaI/1"
    }
]
