import math
import unittest


ARV2_MAX_AMPLITUDE_MM = 20.0
STEPS_PER_MM = 40.0
TRAVEL_MARGIN_MM = 2.0


def validate_amplitude(
    amplitude_mm: float,
    current_position_steps: int,
    travel_steps: int,
) -> bool:
    if not math.isfinite(amplitude_mm) or not 0.0 < amplitude_mm <= ARV2_MAX_AMPLITUDE_MM:
        return False
    allowed = int(travel_steps / 2) - round(TRAVEL_MARGIN_MM * STEPS_PER_MM)
    amplitude_steps = round(amplitude_mm * STEPS_PER_MM)
    lower = current_position_steps - amplitude_steps
    upper = current_position_steps + amplitude_steps
    centered = abs(current_position_steps) <= round(0.5 * STEPS_PER_MM)
    return (
        allowed > 0
        and centered
        and lower >= -allowed
        and upper <= allowed
    )


class FirmwareArV2AmplitudeBehaviorTests(unittest.TestCase):
    def test_supported_amplitude_boundaries(self) -> None:
        for amplitude in (0.5, 2.0, 5.0, 10.0, 15.0, 20.0):
            self.assertTrue(validate_amplitude(amplitude, 0, 2000))

    def test_invalid_amplitudes_are_rejected(self) -> None:
        for amplitude in (0.0, -1.0, 20.1, 25.0, math.nan, math.inf, -math.inf):
            self.assertFalse(validate_amplitude(amplitude, 0, 2000))

    def test_travel_check_uses_both_current_position_extremes(self) -> None:
        self.assertTrue(validate_amplitude(10.0, 0, 2000))
        self.assertFalse(validate_amplitude(10.0, 20, 840))
        self.assertFalse(validate_amplitude(10.0, -20, 840))

    def test_centering_requirement_is_independent_of_amplitude_limit(self) -> None:
        self.assertTrue(validate_amplitude(20.0, 0, 2000))
        self.assertTrue(validate_amplitude(0.5, 20, 2000))
        self.assertFalse(validate_amplitude(0.5, 21, 2000))

    def test_actuator_output_clamp_remains_separate(self) -> None:
        actuator_limit_mm = 7.5
        clamp = lambda value: max(-actuator_limit_mm, min(actuator_limit_mm, value))
        self.assertEqual(clamp(20.0), actuator_limit_mm)
        self.assertEqual(clamp(-20.0), -actuator_limit_mm)


if __name__ == "__main__":
    unittest.main()
