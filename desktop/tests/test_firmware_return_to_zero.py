import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "src" / "main.c").read_text(encoding="utf-8")


def function_body(name: str) -> str:
    match = re.search(
        rf"(?m)^(?:static\s+)?[^;\n]*\b{re.escape(name)}\s*\([^;]*\)\s*\{{",
        SOURCE,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing {name}")
    opening = SOURCE.index("{", match.start())
    depth = 0
    for index in range(opening, len(SOURCE)):
        if SOURCE[index] == "{":
            depth += 1
        elif SOURCE[index] == "}":
            depth -= 1
            if depth == 0:
                return SOURCE[opening + 1:index]
    raise AssertionError(f"unterminated {name}")


class FirmwareReturnToZeroTests(unittest.TestCase):
    def test_return_uses_shared_nonblocking_tim6_trajectory(self) -> None:
        request = function_body("RequestReturnToZero")
        update = function_body("UpdateReturnToZero")
        self.assertIn("g_parametricV2TargetSteps = 0", request)
        self.assertIn("g_parametricV2Enabled = 1U", request)
        self.assertIn("g_parametricRunning = 1U", request)
        self.assertIn("ClosedLoopActuatorKick()", request)
        self.assertNotIn("StepOne(", request)
        self.assertIn("g_positionSteps == 0", update)
        self.assertIn("ClosedLoopActuatorStop()", update)
        self.assertIn("RETURN_TO_ZERO_STALL_TIMEOUT_MS", update)
        self.assertIn("trajectory_inactive", update)
        self.assertIn("position_stalled", update)

    def test_return_reports_structured_start_completion_and_abort_events(self) -> None:
        source = function_body("RequestReturnToZero") + function_body("UpdateReturnToZero")
        for event in (
            "return_to_zero_started",
            "return_to_zero_completed",
            "return_to_zero_aborted",
            "position_steps",
        ):
            self.assertIn(event, source)

    def test_return_preserves_safety_interlocks(self) -> None:
        request = function_body("RequestReturnToZero")
        update = function_body("UpdateReturnToZero")
        for check in ("g_homed", "g_closedLoopFault", "PendulumAngleSafetyReadyForMotion"):
            self.assertIn(check, request)
        for check in ("g_closedLoopFault", "g_homed", "g_pendulumAngleSafetyAvailable"):
            self.assertIn(check, update)
        self.assertIn("AbortReturnToZero", function_body("ClosedLoopActuatorFault"))
        self.assertIn("AbortReturnToZero", function_body("PendulumAngleLimitTrip"))

    def test_go_zero_is_distinct_from_angle_zero(self) -> None:
        process_line = function_body("ProcessLine")
        self.assertIn('strcmp(line, "go_zero") == 0', process_line)
        self.assertIn('strcmp(line, "zero") == 0', process_line)
        self.assertIn('RequestReturnToZero("manual")', process_line)
        self.assertIn('UartPrint("go_zero rejected: run home first', process_line)
        zero_branch = process_line.split('if (strcmp(line, "zero") == 0)', 1)[1]
        zero_branch = zero_branch.split('if (strcmp(line, "go_zero") == 0)', 1)[0]
        self.assertNotIn('strcmp(line, "go_zero") == 0', zero_branch)

    def test_return_is_prioritized_and_does_not_start_sensor_session(self) -> None:
        main = function_body("main")
        self.assertIn("if (ReturnToZeroIsActive() != 0U)", main)
        self.assertLess(
            main.index("UpdateReturnToZero();"),
            main.index("UpdatePhaseTrackedSine();"),
        )
        self.assertIn("ExperimentControllerIsActive()", function_body("SensorSessionObserve"))

    def test_normal_stop_paths_request_return(self) -> None:
        source = SOURCE
        stop = function_body("ProcessLine").split(
            'if (strcmp(line, "stop") == 0)', 1
        )[1].split('if (strncmp(line,"ar_v2",5U)==0', 1)[0]
        self.assertLess(stop.index("g_parametricV2TargetSteps = g_positionSteps"),
                        stop.index("RequestReturnToZero("))
        self.assertNotIn(
            "g_parametricV2TargetSteps = g_positionSteps",
            stop[stop.index("RequestReturnToZero("):],
        )
        self.assertNotIn("ClosedLoopActuatorStop()", stop[stop.index("RequestReturnToZero("):])
        self.assertNotIn("RequestReturnToZero(", function_body("StopAutoResonanceV2"))
        self.assertIn("ReturnToCenter()", function_body("StopParametricController"))
        self.assertIn("ReturnToCenter()", function_body("ProcessMaintenanceCommand"))
        self.assertNotIn("RequestReturnToZero(", function_body("StopAutoResonanceV2"))
        self.assertIn('RequestReturnToZero("operator_stop")', source)
        self.assertIn('RequestReturnToZero("target_angle_reached")', source)

    def test_ar_v2_stop_cleans_up_before_arming_return(self) -> None:
        process_line = function_body("ProcessLine")
        ar_stop = process_line.split('strcmp(line,"ar_v2 stop")==0', 1)[1]
        ar_stop = ar_stop.split('if (strncmp(line,"ar_v2 start ', 1)[0]
        self.assertLess(ar_stop.index("StopAutoResonanceV2()"),
                        ar_stop.index("RequestReturnToZero("))
        self.assertNotIn(
            "ClosedLoopActuatorStop()",
            ar_stop[ar_stop.index("RequestReturnToZero("):],
        )

    def test_stop_during_return_aborts_without_rearming_experiment(self) -> None:
        process_line = function_body("ProcessLine")
        active_stop = process_line.split(
            'if (ReturnToZeroIsActive() != 0U && strcmp(line, "stop") == 0)',
            1,
        )[1].split('if (ReturnToZeroIsActive() != 0U &&', 1)[0]
        self.assertIn('AbortReturnToZero("operator_stop")', active_stop)
        self.assertIn("return;", active_stop)

    def test_desktop_command_service_exposes_go_zero(self) -> None:
        commands = (
            ROOT / "desktop" / "src" / "pendulum_workbench" /
            "application" / "firmware_commands.py"
        ).read_text(encoding="utf-8")
        self.assertIn('GO_ZERO = "go_zero"', commands)
        self.assertIn("def go_zero", commands)
