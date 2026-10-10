"""Regression coverage for lid control during holds and repeated profiles."""

import unittest
from unittest.mock import MagicMock, call

from pudu.utils import execute_thermocycler_profile, set_thermocycler_temperature


class TestThermocyclerTemperature(unittest.TestCase):
    def test_cold_hold_turns_off_lid_and_preserves_block_arguments(self):
        module = MagicMock()
        set_thermocycler_temperature(module, 4, block_max_volume=95)
        self.assertEqual(module.method_calls, [
            call.deactivate_lid(),
            call.set_block_temperature(4, block_max_volume=95),
        ])

    def test_warm_hold_matches_lid_including_minimum_target(self):
        for temperature in (37, 42, 60, 80):
            with self.subTest(temperature=temperature):
                module = MagicMock()
                set_thermocycler_temperature(module, temperature)
                self.assertEqual(module.method_calls, [
                    call.set_lid_temperature(temperature),
                    call.set_block_temperature(temperature),
                ])

    def test_profile_updates_lid_for_every_hold_and_repetition(self):
        module = MagicMock()
        warm = {'temperature': 42, 'hold_time_minutes': 2}
        cold = {'temperature': 16, 'hold_time_seconds': 300}
        execute_thermocycler_profile(module, [warm, cold], repetitions=2, block_max_volume=95)
        cycle = [
            call.set_lid_temperature(42),
            call.execute_profile(steps=[warm], repetitions=1, block_max_volume=95),
            call.deactivate_lid(),
            call.execute_profile(steps=[cold], repetitions=1, block_max_volume=95),
        ]
        self.assertEqual(module.method_calls, cycle * 2)

    def test_successful_recovery_keeps_lid_and_block_control_active(self):
        module = MagicMock()
        recovery = {'temperature': 37, 'hold_time_minutes': 60}
        execute_thermocycler_profile(module, [recovery])
        self.assertEqual(module.method_calls, [
            call.set_lid_temperature(37),
            call.execute_profile(steps=[recovery], repetitions=1, block_max_volume=30),
        ])


if __name__ == '__main__':
    unittest.main()
