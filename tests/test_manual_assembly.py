import unittest

from pudu.assembly import ManualAssembly


EXAMPLE_ASSEMBLIES = [
    {
        "Product": "https://SBOL2Build.org/composite_1/1",
        "Backbone": "https://sbolcanvas.org/pSB1C3/1",
        "PartsList": [
            "https://sbolcanvas.org/J23101/1",
            "https://sbolcanvas.org/B0034/1",
            "https://sbolcanvas.org/GFP/1",
            "https://sbolcanvas.org/B0015/1",
        ],
        "Restriction Enzyme": "https://SBOL2Build.org/BsaI/1",
    },
    {
        "Product": "https://SBOL2Build.org/composite_2/1",
        "Backbone": "https://sbolcanvas.org/pSB1C3/1",
        "PartsList": [
            "https://sbolcanvas.org/J23106/1",
            "https://sbolcanvas.org/B0034/1",
            "https://sbolcanvas.org/RFP/1",
            "https://sbolcanvas.org/B0015/1",
        ],
        "Restriction Enzyme": "https://SBOL2Build.org/BsaI/1",
    },
]


class TestManualAssembly(unittest.TestCase):
    def test_extract_name_from_uri(self):
        protocol = ManualAssembly(assemblies=EXAMPLE_ASSEMBLIES, output_xlsx=False)
        self.assertEqual(protocol._extract_name_from_uri("https://sbolcanvas.org/GFP/1"), "GFP")

    def test_volume_calculation(self):
        protocol = ManualAssembly(assemblies=EXAMPLE_ASSEMBLIES, output_xlsx=False)
        records = protocol.process_assemblies()

        first = records[0]
        self.assertEqual(first.number_of_dna_components, 5)
        self.assertEqual(first.water_volume, 2)
        self.assertEqual(first.total_reaction_volume, 20)

    def test_invalid_overfilled_reaction(self):
        overfilled = [{
            "Product": "https://SBOL2Build.org/composite_1/1",
            "Backbone": "https://sbolcanvas.org/pSB1C3/1",
            "PartsList": [f"https://example.org/part_{i}/1" for i in range(12)],
            "Restriction Enzyme": "https://SBOL2Build.org/BsaI/1",
        }]

        protocol = ManualAssembly(assemblies=overfilled, output_xlsx=False)
        with self.assertRaises(ValueError):
            protocol.process_assemblies()

    def test_markdown_rendering_contains_sections(self):
        protocol = ManualAssembly(assemblies=EXAMPLE_ASSEMBLIES, output_xlsx=False)
        markdown = protocol.render_markdown()

        self.assertIn("# Golden Gate Manual Assembly Protocol", markdown)
        self.assertIn("## Per-reaction instructions", markdown)
        self.assertIn("### Product: composite_1", markdown)
        self.assertIn("Add 2 µL nuclease-free water", markdown)


if __name__ == "__main__":
    unittest.main()
