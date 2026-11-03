import re
import unittest
from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[2] / "src" / "main.c"
).read_text(encoding="utf-8")


def function_body(name: str) -> str:
    signature = re.search(
        rf"(?m)^(?:static\s+|int\s+)[^;\n]*\b{re.escape(name)}\s*\([^;]*?\)\s*\{{",
        SOURCE,
        re.DOTALL,
    )
    if signature is None:
        raise AssertionError(f"{name} not found")
    opening = SOURCE.index("{", signature.start())
    depth = 0
    for index in range(opening, len(SOURCE)):
        if SOURCE[index] == "{":
            depth += 1
        elif SOURCE[index] == "}":
            depth -= 1
            if depth == 0:
                return SOURCE[opening + 1:index]
    raise AssertionError(f"{name} is unterminated")


class FirmwareSensorFaultPolicyTests(unittest.TestCase):
    def test_session_statistics_have_required_fields(self) -> None:
        for field in (
            "sensor_failed_reads",
            "sensor_outages",
            "sensor_recovery_attempts",
            "sensor_recovery_successes",
            "sensor_recovery_failures",
            "sensor_total_outage_ms",
            "sensor_max_outage_ms",
            "sensor_last_outage_ms",
            "angle_samples_rejected",
            "max_validated_angle_deg",
            "max_rejected_angle_deg",
        ):
            self.assertIn(field, SOURCE)

    def test_session_lifecycle_emits_start_and_exactly_once_summary(self) -> None:
        start = function_body("SensorSessionStart")
        summary = function_body("SensorSessionSummary")
        observe = function_body("SensorSessionObserve")
        self.assertIn("sensor_session_start", start)
        self.assertIn("summary_emitted", summary)
        self.assertIn("sensor_session_summary", summary)
        self.assertIn("SensorSessionStart", observe)
        self.assertIn("SensorSessionSummary", observe)

    def test_failed_reads_count_distinct_outages(self) -> None:
        failure = function_body("AS5600_RecordReadFailure")
        self.assertIn("sensor_failed_reads++", failure)
        self.assertIn("sensor_outages++", failure)
        self.assertIn("outage_active == 0U", failure)

    def test_recovery_attempts_are_counted_once(self) -> None:
        read = function_body("AS5600_ReadAngle")
        self.assertIn("recoveryAttempted = 1U", read)
        self.assertIn("sensor_recovery_attempts++", read)
        self.assertIn("sensor_recovery_successes++", read)
        self.assertIn("sensor_recovery_failures++", read)

    def test_safety_allows_only_bounded_transient_outage(self) -> None:
        safety = function_body("PendulumAngleSafetyPoll")
        self.assertIn("SensorOutageWithinGrace()", safety)
        self.assertIn("PendulumAngleSensorFaultTrip()", safety)
        self.assertRegex(SOURCE, r"#define AS5600_TRANSIENT_OUTAGE_GRACE_MS\s+250U")

    def test_feedback_outage_enters_waiting_and_keeps_recovering(self) -> None:
        safety = function_body("PendulumAngleSafetyPoll")
        self.assertIn("FeedbackControllerIsActive()", safety)
        self.assertIn("SetControllerWaitingForSensor()", safety)
        self.assertIn("AS5600_RECOVERY_RETRY_INTERVAL_MS", safety)
        self.assertIn("SensorOutageWithinGrace()", safety)
        self.assertLess(
            safety.index("SensorOutageWithinGrace()"),
            safety.index("SetControllerWaitingForSensor()"),
        )
        self.assertIn("return 1U", safety)

    def test_waiting_and_resume_events_are_transition_events(self) -> None:
        waiting = function_body("SetControllerWaitingForSensor")
        resumed = function_body("SetControllerResumed")
        self.assertIn("g_controllerWaitingForSensor == 0U", waiting)
        self.assertIn("controller_waiting_for_sensor", waiting)
        self.assertIn("g_controllerWaitingForSensor != 0U", resumed)
        self.assertIn("controller_resumed", resumed)

    def test_waiting_only_allows_last_target_to_finish(self) -> None:
        pending = function_body("SensorWaitingHasPendingTarget")
        for target in ("g_parametricV2TargetSteps", "g_parametricClosedLoopTargetSteps"):
            self.assertIn(target, pending)
        for name in ("ParametricV2OnTimer", "ClosedLoopActuatorOnTimer"):
            timer = function_body(name)
            self.assertIn("SensorWaitingHasPendingTarget()", timer)

    def test_closed_loop_holds_target_on_read_failure(self) -> None:
        for name in ("UpdateAutoResonanceV2", "UpdateParametricClosedLoop"):
            body = function_body(name)
            self.assertIn("AS5600_ReadAngle", body)
            self.assertIn("return;", body)
            self.assertNotIn("ClosedLoopActuatorFault(3U)", body)

    def test_phase_tracked_controller_does_not_command_from_stale_angle(self) -> None:
        body = function_body("UpdatePhaseTrackedSine")
        self.assertIn("if (angleValid == 0U)", body)
        self.assertIn("Hold the last validated target", body)

    def test_existing_angle_target_stop_remains_protective(self) -> None:
        trip = function_body("PendulumAngleTargetReached")
        for required in (
            "ManualStepTimerStop()",
            "ClosedLoopActuatorStop()",
        ):
            self.assertIn(required, trip)
        self.assertNotIn("g_closedLoopFault = 4U", trip)

    def test_plausibility_rejects_spike_before_validation_and_statistics(self) -> None:
        read_once = function_body("AS5600_ReadAngleOnce")
        self.assertIn("AS5600_AngleIsPlausible", read_once)
        self.assertIn("AS5600_ReportRejectedAngle", read_once)
        self.assertIn("return HAL_ERROR", read_once)
        validator = function_body("AS5600_AngleIsPlausible")
        self.assertIn("g_latestAngleRaw", validator)
        self.assertIn("AS5600_MAX_PLAUSIBLE_STEP_DEG", validator)
        rejected = function_body("AS5600_ReportRejectedAngle")
        for field in (
            "timestamp_ms",
            "raw_angle",
            "previous_validated_angle_deg",
            "reason",
            "max_rejected_angle_deg",
        ):
            self.assertIn(field, rejected)
        valid = function_body("AS5600_RecordValidAngleSample")
        self.assertIn("max_validated_angle_deg", valid)

    def test_rejected_samples_are_missing_from_stream_and_not_target_events(self) -> None:
        read_once = function_body("AS5600_ReadAngleOnce")
        self.assertLess(
            read_once.index("AS5600_AngleIsPlausible"),
            read_once.index("AS5600_RecordValidAngleSample"),
        )
        telemetry = function_body("PrintLiveTelemetry")
        self.assertIn("angle_deg=NaN", telemetry)

    def test_high_angle_safety_remains_after_plausibility_validation(self) -> None:
        valid = function_body("AS5600_RecordValidAngleSample")
        self.assertIn("PENDULUM_ANGLE_LIMIT_DEG", valid)
        self.assertIn("PendulumAngleTargetReached", valid)

    def test_timer_still_requires_safety_gate_and_target_owner(self) -> None:
        timer = function_body("ParametricV2OnTimer")
        self.assertIn("g_closedLoopFault != 0U", timer)
        self.assertIn("g_pendulumAngleSafetyAvailable == 0U", timer)
        self.assertIn("g_parametricV2TargetSteps", timer)


if __name__ == "__main__":
    unittest.main