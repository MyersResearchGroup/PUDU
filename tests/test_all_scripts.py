import unittest
import subprocess
import pathlib

SCRIPTS_DIR = pathlib.Path("scripts")

class TestAllScripts(unittest.TestCase):
    def test_all_scripts_with_simulator(self):
        # Make sure we found the folder
        self.assertTrue(SCRIPTS_DIR.exists(), f"Scripts dir not found: {SCRIPTS_DIR}")
        script_files = sorted(SCRIPTS_DIR.glob("*.py"), key=lambda p: p.name.lower())

        for script_path in script_files:
            with self.subTest(script=script_path.name):
                print(f"\n=== Simulating: {script_path.name} ===")
                result = subprocess.run(
                    ["opentrons_simulate", str(script_path)],
                    capture_output=True,
                    text=True
                )
                # Ensure simulator exits successfully
                self.assertEqual(
                    result.returncode, 0,
                    msg=f"Simulation failed for {script_path}:\n{result.stderr}"
                )
    #TODO: Create edge case tests that fail appropriately
    #Some example edge cases:
    #Too many reagents for the labware
    #Not enough available wells for the thermocycler
    #Too many colonies for the plating


if __name__ == '__main__':
    unittest.main()
