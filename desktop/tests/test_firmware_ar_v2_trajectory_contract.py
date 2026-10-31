import re
import unittest
from pathlib import Path


FIRMWARE_SOURCE = (
    Path(__file__).resolve().parents[2] / "src" / "main.c"
).read_text(encoding="utf-8")


def function_body(name: str) -> str:
    signature = re.search(
        rf"(?m)^(?:static\s+|int\s+)[^;\n]*\b{re.escape(name)}\s*\([^;]*?\)\s*\{{",
        FIRMWARE_SOURCE,
        re.DOTALL,
    )
    if signature is None:
        raise AssertionError(f"Firmware function {name} was not found")

    opening_brace = FIRMWARE_SOURCE.index("{", signature.start())
    depth = 0
    for index in range(opening_brace, len(FIRMWARE_SOURCE)):
        if FIRMWARE_SOURCE[index] == "{":
            depth += 1
        elif FIRMWARE_SOURCE[index] == "}":
            depth -= 1
            if depth == 0:
                return FIRMWARE_SOURCE[opening_brace + 1:index]
    raise AssertionError(f"Firmware function {name} has an unterminated body")


class FirmwareAutoResonanceV2TrajectoryTests(unittest.TestCase):
    def test_ar_v2_updates_the_parametric_v2_target(self) -> None:
        update = function_body("UpdateAutoResonanceV2")
        self.assertIn("g_parametricV2TargetSteps=steps", update)
        self.assertNotIn("g_parametricClosedLoopTargetSteps=steps", update)
        self.assertIn("ClosedLoopActuatorKick()", update)

    def test_ar_v2_start_claims_parametric_v2_trajectory_ownership(self) -> None:
        start = FIRMWARE_SOURCE.split('if (strncmp(line,"ar_v2 start ",12U)==0', 1)[1]
        start = start.split('UartPrint("ar_v2 started', 1)[0]
        self.assertIn("g_parametricV2Enabled=1U", start)
        self.assertIn("g_parametricRunning=1U", start)
        self.assertIn("g_parametricV2TargetSteps=g_positionSteps", start)

    def test_ar_v2_stop_clears_trajectory_state_and_timer(self) -> None:
        stop = function_body("StopAutoResonanceV2")
        for required in (
            "g_arv2Running = 0U",
            "g_parametricRunning = 0U",
            "g_parametricV2Enabled = 0U",
            "g_parametricV2TargetSteps = g_positionSteps",
            "ClosedLoopActuatorStop()",
        ):
            self.assertIn(required, stop)

    def test_v2_trajectory_keeps_runtime_acceleration_configuration(self) -> None:
        trajectory = function_body("ParametricV2OnTimer")
        self.assertIn("g_parametricV2AccelHzS", trajectory)
        self.assertNotIn("PARAMETRIC_V2_ACCEL_MAX_HZ_S", trajectory)
        self.assertRegex(
            FIRMWARE_SOURCE,
            r"#define PARAMETRIC_V2_ACCEL_DEFAULT_HZ_S\s+18000\.0f",
        )

    def test_faults_disable_ar_v2_and_parametric_v2_state(self) -> None:
        fault = function_body("ClosedLoopActuatorFault")
        sensor_fault = function_body("PendulumAngleSensorFaultTrip")
        for body in (fault, sensor_fault):
            self.assertIn("g_parametricV2Enabled = 0U", body)
            self.assertIn("g_parametricV2SpeedHz = 0.0f", body)
