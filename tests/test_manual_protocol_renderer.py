import unittest

from pudu.assembly import BaseAssembly


class DummyAssembly(BaseAssembly):
    def process_assemblies(self):
        return None

    def _load_parts_and_enzymes(self, protocol, alum_block):
        return 0

    def _process_assembly_combinations(self, protocol, pipette, thermo_plate, alum_block,
                                       dd_h2o, t4_dna_ligase_buffer, t4_dna_ligase,
                                       volume_reagents, thermocycler_well_counter):
        return thermocycler_well_counter

    def _calculate_total_tips_needed(self):
        return 0


class TestManualProtocolRenderer(unittest.TestCase):
    def test_markdown_contains_expected_sections_and_uri(self):
        assembly = DummyAssembly(protocol_name="Loop Test")

        class Parent:
            def __init__(self, name, parent=None):
                self.load_name = name
                self.parent = parent

        class Module:
            def __init__(self, name):
                self.name = name

        class Well:
            def __init__(self, well_name, parent):
                self.well_name = well_name
                self.parent = parent

        temp_parent = Parent("opentrons_24_aluminumblock_nest_1.5ml_snapcap", Module("temperature module"))
        thermo_parent = Parent("nest_96_wellplate_100ul_pcr_full_skirt", Module("thermocycler module"))

        assembly._register_source_metadata(
            display_name="GFP",
            source_well="A4",
            source_module="temperature module",
            source_labware="opentrons_24_aluminumblock_nest_1.5ml_snapcap",
            uri="https://example.org/GFP",
        )
        assembly._record_transfer_event(
            volume=2,
            source=Well('A4', temp_parent),
            dest=Well('A1', thermo_parent)
        )
        assembly._record_reaction_destination(
            destination_well="A1",
            reaction_label="Replicate: 1, Product: GFP_construct",
            implementation_name="GFP_construct",
            implementation_uri="https://example.org/GFP_construct",
        )

        markdown = assembly._render_manual_protocol_markdown()

        self.assertIn("# Loop Test Manual Protocol", markdown)
        self.assertIn("## Deck and reagent setup", markdown)
        self.assertIn("## Source materials table", markdown)
        self.assertIn("https://example.org/GFP", markdown)
        self.assertIn("## Reaction destination table", markdown)
        self.assertIn("https://example.org/GFP_construct", markdown)
        self.assertIn("## Step-by-step instructions", markdown)
        self.assertIn("Add 2 µL of GFP (URI: https://example.org/GFP)", markdown)


if __name__ == '__main__':
    unittest.main()
