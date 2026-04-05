import tempfile
import unittest
from pathlib import Path

from pudu.assembly import ManualLoopAssembly, ManualProtocolEvent


class TestManualProtocolGeneration(unittest.TestCase):
    def test_generate_manual_protocol_markdown(self):
        assembly = ManualLoopAssembly(
            assemblies=[{"receiver": "Odd_1", "promoter": "j23101"}],
            protocol_name="Loop Assembly Test"
        )

        assembly.deck_setup_records = [
            {"kind": "Module", "name": "temperature module", "location": "1", "details": ""},
            {"kind": "Labware", "name": "nest_96_wellplate_100ul_pcr_full_skirt", "location": "thermocycler", "details": "on thermocycler module"},
        ]
        assembly.source_metadata_by_well = {
            ("temperature module", "opentrons_24_aluminumblock_nest_1.5ml_snapcap", "A1"): {
                "display_name": "Part GFP",
                "uri": "https://example.org/GFP",
                "module_name": "temperature module",
                "labware_name": "opentrons_24_aluminumblock_nest_1.5ml_snapcap",
                "well": "A1",
            }
        }
        assembly.reaction_destination_records = [
            {"reaction_label": "Replicate: 1, Product: GFP_construct", "reaction_uri": "https://example.org/GFP_construct", "destination_well": "A1"}
        ]
        assembly.manual_protocol_events = [
            ManualProtocolEvent(
                event_type="transfer",
                volume_ul=2,
                material_name="Part GFP",
                material_uri="https://example.org/GFP",
                source_module="temperature module",
                source_labware="opentrons_24_aluminumblock_nest_1.5ml_snapcap",
                source_well="A1",
                dest_module="thermocycler module",
                dest_labware="nest_96_wellplate_100ul_pcr_full_skirt",
                dest_well="A1",
            ),
            ManualProtocolEvent(
                event_type="mix",
                volume_ul=20,
                material_name="reaction in A1",
                material_uri=None,
                source_module="thermocycler module",
                source_labware="nest_96_wellplate_100ul_pcr_full_skirt",
                source_well="A1",
                dest_module="thermocycler module",
                dest_labware="nest_96_wellplate_100ul_pcr_full_skirt",
                dest_well="A1",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "loop_assembly_test_manual_protocol.md"
            generated_path = assembly.generate_manual_protocol(str(output_path))
            content = Path(generated_path).read_text(encoding="utf-8")

        self.assertIn("# Loop Assembly Test Manual Protocol", content)
        self.assertIn("## Deck and reagent setup", content)
        self.assertIn("## Source materials table", content)
        self.assertIn("## Reaction destination table", content)
        self.assertIn("Add 2 µL of Part GFP (URI: https://example.org/GFP)", content)
        self.assertIn("Mix the reaction in thermocycler module well A1", content)


if __name__ == '__main__':
    unittest.main()
