"""Generate a manual Golden Gate protocol Markdown document from SBOL-like JSON input."""

import json
from pathlib import Path

from pudu.assembly import ManualAssembly


def main():
    input_path = Path("examples/manual_assembly_input.json")
    output_path = Path("documentation/manual_assembly_example.md")

    assemblies = json.loads(input_path.read_text(encoding="utf-8"))
    manual_protocol = ManualAssembly(assemblies=assemblies, output_xlsx=False)
    manual_protocol.write_markdown(str(output_path))

    print(f"Wrote manual protocol to: {output_path}")


if __name__ == "__main__":
    main()
