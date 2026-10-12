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


class FirmwareManualMotionContractTests(unittest.TestCase):
    def test_manual_speed_is_explicitly_the_homing_speed(self) -> None:
        self.assertRegex(FIRMWARE_SOURCE, r"#define HOMING_SPEED_HZ\s+1100U")
        self.assertRegex(
            FIRMWARE_SOURCE,
            r"#define MANUAL_MOTION_SPEED_HZ\s+HOMING_SPEED_HZ",
        )
        self.assertIn("500000U / MANUAL_MOTION_SPEED_HZ", function_body("ManualStepTimerStart"))

    def test_main_loop_manual_update_does_not_block_on_stepone(self) -> None:
        update = function_body("UpdateManualMotion")
        self.assertNotIn("StepOne(", update)
        self.assertIn("ManualStepTimerStart()", update)
        self.assertIn("ManualMotionSafetyStopReason()", update)

    def test_main_loop_keeps_uart_and_telemetry_service_around_manual_motion(self) -> None:
        main_loop = function_body("main")
        first_poll = main_loop.index("PollUart();")
        first_telemetry = main_loop.index("PrintLiveTelemetry();", first_poll)
        manual_update = main_loop.index("UpdateManualMotion();", first_telemetry)
        second_poll = main_loop.index("PollUart();", manual_update)
        second_telemetry = main_loop.index("PrintLiveTelemetry();", second_poll)
        self.assertLess(first_poll, first_telemetry)
        self.assertLess(first_telemetry, manual_update)
        self.assertLess(manual_update, second_poll)
        self.assertLess(second_poll, second_telemetry)

    def test_manual_timer_checks_deadman_interlocks_and_directional_limits(self) -> None:
        safety = function_body("ManualMotionSafetyStopReason")
        for required_check in (
            "MANUAL_MOTION_DEADMAN_TIMEOUT_MS",
            "g_closedLoopFault",
            "g_homed",
            "g_recording",
            "UpperLimitPressed()",
            "LowerLimitPressed()",
        ):
            self.assertIn(required_check, safety)
        self.assertIn("g_manualMotionDirection == 1U && UpperLimitPressed()", safety)
        self.assertIn("g_manualMotionDirection == 2U && LowerLimitPressed()", safety)

        timer = function_body("ManualStepTimerOnTimer")
        self.assertIn("ManualMotionSafetyStopReason()", timer)
        self.assertIn("ManualStepTimerStopFromIsr(reason)", timer)
        self.assertIn("g_positionSteps", timer)

    def test_explicit_and_safety_stops_disable_timer_and_force_step_low(self) -> None:
        explicit_stop = function_body("StopManualMotion")
        timer_stop = function_body("ManualStepTimerStop")
        timer_safety_stop = function_body("ManualStepTimerStopFromIsr")
        self.assertIn("ManualStepTimerStop()", explicit_stop)
        for stop_body in (timer_stop, timer_safety_stop):
            self.assertIn("TIM7->CR1 &= ~TIM_CR1_CEN", stop_body)
            self.assertIn("ActiveState(0U)", stop_body)

    def test_manual_timer_is_isolated_from_closed_loop_tim6(self) -> None:
        manual_timer = function_body("ManualStepTimerInit") + function_body("ManualStepTimerOnTimer")
        closed_loop_timer = function_body("ClosedLoopTimerScheduleUs")
        self.assertIn("TIM7_DAC_IRQn", function_body("ManualStepTimerInit"))
        self.assertIn("TIM7->", manual_timer)
        self.assertNotIn("TIM6->", manual_timer)
        self.assertIn("TIM6->", closed_loop_timer)

    def test_manual_heartbeats_only_refresh_the_active_direction(self) -> None:
        maintenance = function_body("ProcessMaintenanceCommand")
        one_way_branch = maintenance.split('if (requestId == 0UL)', 1)[1].split(
            'UartPrint("maintenance: id=0 type=error ok=0 error=invalid_request_id', 1
        )[0]
        self.assertIn('strcmp(end, "manual_keepalive_up") == 0 && g_manualMotionDirection == 1U', one_way_branch)
        self.assertIn('strcmp(end, "manual_keepalive_down") == 0 && g_manualMotionDirection == 2U', one_way_branch)
        self.assertEqual(one_way_branch.count("g_manualMotionLastKeepaliveMs = HAL_GetTick();"), 2)
        self.assertNotIn("UartPrint(", one_way_branch)

    def test_live_telemetry_keeps_20ms_schedule_and_sequential_indices(self) -> None:
        telemetry = function_body("PrintLiveTelemetry")
        self.assertRegex(FIRMWARE_SOURCE, r"#define LIVE_PERIOD_MS\s+20U")
        self.assertIn("(now - g_lastLiveMs) < LIVE_PERIOD_MS", telemetry)
        self.assertIn("g_lastLiveMs = now;", telemetry)
        self.assertEqual(telemetry.count("sampleIndex = ++g_liveSampleIndex;"), 1)
        self.assertNotIn("g_manualMotionDirection", telemetry)
        self.assertIn("UartPrint(msg);", telemetry)

    def test_uart_tx_ring_enqueues_complete_messages_and_reports_drops(self) -> None:
        enqueue = function_body("UartTryEnqueue")
        printer = function_body("UartPrint")
        report = function_body("ReportUartTxOverflow")
        self.assertLess(enqueue.index("if (length > available)"), enqueue.index("for (size_t i = 0U; i < length; i++)"))
        self.assertIn("if (UartTryEnqueue(text, length) == 0U)", printer)
        self.assertIn("g_uartTxDroppedMessages", printer)
        self.assertIn("g_uartTxDroppedBytes", printer)
        self.assertIn("type=uart_tx_overflow", report)

    def test_homing_still_uses_its_existing_blocking_stepper_path(self) -> None:
        homing = function_body("Home")
        self.assertEqual(homing.count("StepOne(HOMING_SPEED_HZ"), 3)
        self.assertNotIn("ManualStepTimer", homing)


if __name__ == "__main__":
    unittest.main()