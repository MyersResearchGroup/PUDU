from pudu.assembly import ManualLoopAssembly


def test_manual_protocol_markdown_contains_expected_sections_and_steps():
    assembly = {"promoter": "j23101", "rbs": "B0034", "receiver": "Odd_1"}
    protocol = ManualLoopAssembly(assemblies=[assembly], protocol_name="Example Loop")

    protocol.deck_setup = {
        "modules": [{"name": "temperature module", "location": "1"}],
        "labware": [{"name": "opentrons_24_aluminumblock_nest_1.5ml_snapcap", "location": "1"}],
    }
    protocol.source_metadata_by_well = {
        "temp:A1": {
            "display_name": "Part GFP",
            "implementation_name": "GFP",
            "uri": "https://example.org/GFP",
            "well_name": "A1",
            "labware_name": "opentrons_24_aluminumblock_nest_1.5ml_snapcap",
            "module_or_slot": "1",
        }
    }
    protocol.reaction_metadata = [
        {"reaction_label": "Replicate: 1, Combination: ('Part GFP',)", "destination_well": "A1", "product_uri": None}
    ]
    protocol.manual_protocol_events = [
        {
            "event_type": "transfer",
            "volume_ul": 2,
            "source": {"location": "temperature module well A1"},
            "destination": {"location": "thermocycler well A1"},
            "source_material": {
                "display_name": "Part GFP",
                "implementation_name": "GFP",
                "uri": "https://example.org/GFP",
            },
        },
        {
            "event_type": "mix",
            "destination": {"well_name": "A1"},
            "cycles": 2,
            "mix_volume_ul": 20,
        },
    ]

    markdown = protocol._render_manual_protocol_markdown()

    assert "# Example Loop Manual Protocol" in markdown
    assert "## Deck and reagent setup" in markdown
    assert "## Source materials table" in markdown
    assert "## Reaction destination table" in markdown
    assert "1. Add 2 µL of Part GFP (implementation: GFP) (URI: https://example.org/GFP)" in markdown
    assert "2. Mix the reaction in well A1" in markdown
