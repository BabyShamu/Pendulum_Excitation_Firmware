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

    def test_latch_pwm_is_lazy_and_pc7_has_no_boot_actuation(self) -> None:
        startup = function_body("main")
        gpio_init = function_body("GPIO_Init")
        pwm_init = function_body("ServoLatchPwmInitialize")
        pwm_command = function_body("ServoLatchCommand")
        self.assertIn("GPIO_InitStruct.Pin = GPIO_PIN_7", gpio_init)
        self.assertIn("GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP", gpio_init)
        self.assertIn("HAL_GPIO_WritePin(GPIOC, GPIO_PIN_7, GPIO_PIN_RESET)", gpio_init)
        self.assertNotIn("ServoLatchPwmInitialize", startup)
        self.assertNotIn("ServoLatchCommand", startup)
        self.assertNotIn("HAL_TIM_PWM_Start", startup)
        self.assertIn("channelConfig.Pulse = 0U", pwm_init)
        self.assertNotIn("HAL_TIM_PWM_Start", pwm_init)
        self.assertIn("HAL_TIM_PWM_Start(&htim8, TIM_CHANNEL_2)", pwm_command)

    def test_latch_uses_pc7_tim8_channel_two_at_50_hz_with_calibrated_pulses(self) -> None:
        self.assertRegex(FIRMWARE_SOURCE, r"#define SERVO_LATCH_PWM_PERIOD_US\s+20000U")
        self.assertRegex(FIRMWARE_SOURCE, r"#define SERVO_LATCH_HOLD_PULSE_US\s+700U")
        self.assertRegex(FIRMWARE_SOURCE, r"#define SERVO_LATCH_OPEN_PULSE_US\s+1700U")
        self.assertIn("pulse widths, not servo angles", FIRMWARE_SOURCE)
        self.assertIn("GPIO_AF4_TIM8", function_body("ServoLatchCommand"))
        self.assertIn("TIM_CHANNEL_2", function_body("ServoLatchCommand"))
        self.assertIn("SERVO_LATCH_HOLD_PULSE_US", function_body("ProcessMaintenanceCommand"))
        self.assertIn("SERVO_LATCH_OPEN_PULSE_US", function_body("ProcessMaintenanceCommand"))
        self.assertNotIn("TIM6->", function_body("ServoLatchPwmInitialize"))
        self.assertNotIn("TIM7->", function_body("ServoLatchPwmInitialize"))

    def test_as5600_read_failures_preserve_hal_status_error_and_failure_totals(self) -> None:
        read_once = function_body("AS5600_ReadAngleOnce")
        record_failure = function_body("AS5600_RecordReadFailure")
        record_hal_failure = function_body("AS5600_RecordHalFailure")
        read_angle = function_body("AS5600_ReadAngle")

        self.assertEqual(read_once.count("AS5600_RecordReadFailure("), 3)
        self.assertIn("HAL_I2C_GetError(&hi2c1)", read_once)
        self.assertIn("HAL_I2C_ERROR_TIMEOUT", read_once)
        self.assertIn("g_as5600LastHalStatus = status", record_hal_failure)
        self.assertIn("g_as5600LastHalError = error", record_hal_failure)
        self.assertIn("g_as5600LastFailureUptimeMs = HAL_GetTick()", record_hal_failure)
        self.assertIn("g_as5600TotalReadFailures++", record_failure)
        self.assertIn("g_angleSensorConsecutiveFailures++", read_angle)
        self.assertIn("g_as5600LongestFailureStreak", read_angle)
        self.assertIn("g_i2cRecoveryCount++", read_angle)
        self.assertIn("g_as5600RecoverySuccessCount++", read_angle)
        self.assertIn("g_as5600RecoveryFailureCount++", read_angle)

    def test_as5600_hal_error_classification_preserves_raw_categories(self) -> None:
        classify = function_body("AS5600_HalErrorName")
        for raw_error, readable_name in (
            ("HAL_I2C_ERROR_NONE", '"NONE"'),
            ("HAL_I2C_ERROR_BERR", '"BERR"'),
            ("HAL_I2C_ERROR_ARLO", '"ARLO"'),
            ("HAL_I2C_ERROR_AF", '"AF_NACK"'),
            ("HAL_I2C_ERROR_OVR", '"OVR"'),
            ("HAL_I2C_ERROR_DMA", '"DMA"'),
            ("HAL_I2C_ERROR_TIMEOUT", '"TIMEOUT"'),
            ("HAL_I2C_ERROR_SIZE", '"SIZE"'),
            ("HAL_I2C_ERROR_DMA_PARAM", '"DMA_PARAM"'),
        ):
            self.assertIn(raw_error, classify)
            self.assertIn(readable_name, classify)
        self.assertIn('"MULTIPLE"', classify)
        self.assertIn('"UNKNOWN"', classify)

    def test_as5600_failure_recovery_restored_events_and_diagnostics_are_structured(self) -> None:
        unavailable = function_body("AS5600_ReportUnavailable")
        recovery_event = function_body("AS5600_ReportRecovery")
        restored = function_body("AS5600_ReportRestored")
        diagnostics = function_body("PrintDiagnostics")

        for field in (
            "type=angle_sensor_unavailable",
            "failures=%u",
            "total_failures=%lu",
            "hal_status=%u",
            "hal_error=0x%08lX",
            "hal_error_name=%s",
            "recovery_attempted=%u",
            "recovery_count=%lu",
            "sda=%u scl=%u",
            "uptime_ms=%lu",
        ):
            self.assertIn(field, unavailable)
        self.assertNotIn("configured_sda", unavailable)
        self.assertNotIn("PB7", unavailable)
        self.assertNotIn("PB6", unavailable)
        for field in (
            "type=angle_sensor_recovery",
            "attempt=%lu",
            "result=%s",
            "sda_before=%u",
            "sda_after_pulses=%u",
            "sda_after_bus_clear=%u",
            "sda_after_reinit=%u",
            "sda_pin=PB9 scl_pin=PB8",
            "uptime_ms=%lu",
        ):
            self.assertIn(field, recovery_event)
        self.assertIn('"success"', recovery_event)
        self.assertIn('"failed"', recovery_event)
        self.assertNotIn("configured_sda", recovery_event)
        self.assertNotIn("PB7", recovery_event)
        self.assertNotIn("PB6", recovery_event)
        self.assertIn("type=angle_sensor_restored", restored)
        self.assertIn("outage_ms=%lu", restored)
        for field in (
            "as5600_available=%s",
            "consecutive_failures=%u",
            "total_failures=%lu",
            "recovery_attempts=%lu",
            "recovery_successes=%lu",
            "recovery_failures=%lu",
            "longest_failure_streak=%lu",
            "last_hal_status=%u",
            "last_hal_error=0x%08lX",
            "last_hal_error_name=%s",
            "sda=%u scl=%u",
            "uptime_ms=%lu",
        ):
            self.assertIn(field, diagnostics)
        self.assertIn("sda_pin=PB9 scl_pin=PB8", diagnostics)
        self.assertNotIn("PB7", diagnostics)
        self.assertNotIn("PB6", diagnostics)

    def test_as5600_line_sampling_uses_only_configured_i2c1_pins_without_reconfiguration(self) -> None:
        sample = function_body("AS5600_SampleLineStates")
        recover = function_body("AS5600_RecoverI2c")

        for pin in ("GPIO_PIN_9", "GPIO_PIN_8"):
            self.assertIn(pin, sample)
        self.assertNotIn("GPIO_PIN_7", sample)
        self.assertNotIn("GPIO_PIN_6", sample)
        self.assertIn("PB9 for SDA", sample)
        self.assertIn("PB8 for SCL", sample)
        self.assertNotIn("HAL_GPIO_Init", sample)
        for stage in (
            "g_as5600RecoveryLineStates.before",
            "g_as5600RecoveryLineStates.afterPulses",
            "g_as5600RecoveryLineStates.afterBusClear",
            "g_as5600RecoveryLineStates.afterReinit",
        ):
            self.assertIn(stage, recover)
        self.assertLess(
            recover.index("g_as5600RecoveryLineStates.before"),
            recover.index("HAL_I2C_DeInit"),
        )
        self.assertLess(
            recover.index("for (uint8_t pulse"),
            recover.index("g_as5600RecoveryLineStates.afterPulses"),
        )
        self.assertLess(
            recover.index("g_as5600RecoveryLineStates.afterReinit"),
            recover.index("HAL_I2C_IsDeviceReady"),
        )

    def test_as5600_recovery_behavior_constants_and_sequence_remain_unchanged(self) -> None:
        for definition in (
            "#define AS5600_STARTUP_PROBE_ATTEMPTS 3U",
            "#define AS5600_STARTUP_RETRY_DELAY_MS 25U",
            "#define AS5600_READ_FAILURE_RECOVERY_THRESHOLD 3U",
            "#define AS5600_RECOVERY_RETRY_INTERVAL_MS 1000U",
            "#define AS5600_I2C_BUS_CLEAR_CLOCK_PULSES 9U",
        ):
            self.assertIn(definition, FIRMWARE_SOURCE)

        recover = function_body("AS5600_RecoverI2c")
        self.assertLess(recover.index("HAL_I2C_DeInit"), recover.index("GPIO_MODE_OUTPUT_OD"))
        self.assertIn("HAL_Delay(1U)", recover)
        self.assertIn("I2C1_Init()", recover)
        self.assertIn("HAL_I2C_IsDeviceReady", recover)
        self.assertIn("0x36U << 1", recover)

    def test_fault_clear_command_validates_latched_condition_and_does_not_move_or_recalibrate(self) -> None:
        maintenance = function_body("ProcessMaintenanceCommand")
        clear_command = maintenance.split('if (strcmp(end, "fault_clear") == 0)', 1)[1].split(
            'if (strncmp(end, "sine_start ", 11U) == 0)', 1
        )[0]
        for required_check in (
            "g_homingActive != 0U",
            "g_manualMotionDirection != 0U",
            "g_run != 0U",
            "g_sineRunning != 0U",
            "g_parametricRunning != 0U",
            "g_parametricTestRunning != 0U",
            "g_parametricClosedLoopRunning != 0U",
            "g_recording != 0U",
            "previousFault == 1U && UpperLimitPressed() != 0U",
            "upper_limit_still_active",
            "previousFault == 2U && LowerLimitPressed() != 0U",
            "lower_limit_still_active",
            "previousFault == 3U && AS5600_ReadAngle(&angle) != HAL_OK",
            "angle_sensor_unavailable",
            "previousFault == 0U",
            "cleared=0",
            "g_closedLoopFault = 0U",
            "g_closedLoopFaultPending = 0U",
            "g_parametricClosedLoopAngleFailureCount = 0U",
            "type=fault_clear ok=0 fault=%u error=%s",
            "type=fault_clear ok=1 fault=0 cleared=1 previous_fault=%u",
            "event: type=fault_cleared previous_fault=%u",
        ):
            self.assertIn(required_check, clear_command)
        for forbidden_side_effect in (
            "Home()",
            "StepOne(",
            "ClosedLoopActuatorKick()",
            "ClosedLoopActuatorStop()",
            "ServoLatchCommand(",
            "g_homed =",
            "g_travelSteps =",
            "g_positionSteps =",
            "g_stepsPerMm =",
            "g_angleZeroRaw =",
            "g_angleZeroValid =",
            "g_servoLatchCommandedState =",
        ):
            self.assertNotIn(forbidden_side_effect, clear_command)

        legacy_closed_loop = function_body("ProcessLine").split(
            'if (strncmp(line, "parametric_closed_loop", 22) == 0', 1
        )[1].split('if (strncmp(line, "scale ", 6) == 0', 1)[0]
        self.assertIn("g_closedLoopFault != 0U", legacy_closed_loop)
        self.assertNotIn("g_closedLoopFault = 0U", legacy_closed_loop)
        self.assertNotIn("type=fault_cleared", legacy_closed_loop)

    def test_latch_firmware_interlocks_and_commanded_status_are_explicit(self) -> None:
        interlock = function_body("ServoLatchInterlockReason")
        for required_check in (
            "g_homingActive",
            "g_manualMotionDirection",
            "g_run",
            "g_sineRunning",
            "g_parametricRunning",
            "g_parametricTestRunning",
            "ClosedLoopActuatorIsActive()",
            "g_recording",
            "g_closedLoopFault",
        ):
            self.assertIn(required_check, interlock)
        actuator_activity = function_body("ClosedLoopActuatorIsActive")
        self.assertIn("g_parametricClosedLoopRunning", actuator_activity)
        self.assertIn("g_phaseTrackedSineRunning", actuator_activity)
        handler = function_body("ProcessMaintenanceCommand")
        for protocol_term in ("latch_status", "latch_hold", "latch_open", "commanded=1"):
            self.assertIn(protocol_term, handler)
        self.assertRegex(
            FIRMWARE_SOURCE,
            r"g_servoLatchCommandedState\s*=\s*SERVO_LATCH_STATE_UNKNOWN",
        )

    def test_home_latch_guard_precedes_all_homing_side_effects(self) -> None:
        home = function_body("Home")
        latch_guard = home.index(
            "if (g_servoLatchCommandedState != SERVO_LATCH_STATE_OPEN)"
        )
        for side_effect in (
            "g_homed = 0U;",
            "g_homingActive = 1U;",
            "ClosedLoopActuatorStop();",
            "SetDirection(DIR_DOWN);",
            "StepOne(HOMING_SPEED_HZ",
        ):
            self.assertLess(latch_guard, home.index(side_effect))
        self.assertNotIn("ServoLatchCommand", home)
        self.assertNotIn("latch_open", home)
        self.assertIn('"homing rejected: latch_not_open', home)

        handler = function_body("ProcessMaintenanceCommand")
        home_command = handler.split('if (strcmp(end, "home") == 0)', 1)[1].split(
            'if (strcmp(end, "zero") == 0)', 1
        )[0]
        correlated_guard = home_command.index(
            "g_servoLatchCommandedState != SERVO_LATCH_STATE_OPEN"
        )
        self.assertIn("error=latch_not_open", home_command)
        self.assertLess(correlated_guard, home_command.index("Home();"))

    def test_typed_sine_commands_keep_existing_latch_motion_fault_and_recording_guards(self) -> None:
        maintenance = function_body("ProcessMaintenanceCommand")
        sine_start = maintenance.split('if (strncmp(end, "sine_start ", 11U) == 0)', 1)[1].split(
            'if (strcmp(end, "sine_stop") == 0)', 1
        )[0]
        for required_check in (
            "isfinite(amplitudeMm)",
            "isfinite(frequencyHz)",
            "g_homed == 0U",
            "g_servoLatchCommandedState != SERVO_LATCH_STATE_OPEN",
            "g_closedLoopFault != 0U",
            "g_manualMotionDirection != 0U",
            "g_parametricClosedLoopRunning != 0U",
            "g_recording != 0U",
            "MAX_SINE_FREQUENCY_HZ",
            "amplitude_out_of_travel",
            "type=sine_start ok=1 active=1",
        ):
            self.assertIn(required_check, sine_start)
        self.assertIn("amplitudeMm < 0.0f", sine_start)
        self.assertIn("amplitudeMm > 0.0f", sine_start)
        self.assertNotIn("amplitudeMm <= 0.0f", sine_start)

        sine_stop = maintenance.split('if (strcmp(end, "sine_stop") == 0)', 1)[1].split(
            'if (strcmp(end, "manual_up") == 0', 1
        )[0]
        self.assertIn("g_sineRunning = 0U", sine_stop)
        self.assertIn("ReturnToCenter()", sine_stop)
        self.assertIn("type=sine_stop ok=1 active=0", sine_stop)

        legacy_sine_start = function_body("ProcessLine").split(
            'if (strncmp(line, "sine", 4) == 0', 1
        )[1].split('if (strncmp(line, "parametric", 10) == 0', 1)[0]
        self.assertIn("g_servoLatchCommandedState != SERVO_LATCH_STATE_OPEN", legacy_sine_start)

        sine_update = function_body("UpdateSine")
        self.assertIn("g_positionSteps = 0;", sine_start)
        self.assertIn("g_sineAmplitudeMm * sinf(phase)", sine_update)
        self.assertIn("lroundf(targetMm * g_stepsPerMm)", sine_update)
        self.assertIn("while (g_positionSteps != targetSteps)", sine_update)
        self.assertIn("StepOne(speedHz, 1U, 1U) == 0U", sine_update)
        self.assertIn("event: type=sine_motion_stopped reason=limit", sine_update)

    def test_autores_start_stop_apply_user_values_without_weakening_existing_limits(self) -> None:
        def assert_assigned_before_read(body: str, variable: str) -> None:
            assigned = False
            declaration = re.compile(rf"^(?:const\s+)?(?:float|double)\s+{variable}\s*;$")
            assignment = re.compile(rf"^{variable}\s*=")
            reference = re.compile(rf"\b{variable}\b")
            for line in body.splitlines():
                code = line.split("//", 1)[0].strip()
                if not code or declaration.match(code):
                    continue
                if assignment.match(code):
                    assigned = True
                    continue
                if reference.search(code) and not assigned:
                    self.fail(f"{variable} is read before assignment in: {code}")

        maintenance = function_body("ProcessMaintenanceCommand")
        start = maintenance.split('if (strncmp(end, "autores_start ", 14U) == 0)', 1)[1].split(
            'if (strcmp(end, "autores_stop") == 0)', 1
        )[0]
        for required_guard in (
            "!isfinite(amplitudeMm)",
            "!isfinite(phaseDeg)",
            "amplitudeMm <= 0.0f",
            "g_homed == 0U",
            "g_servoLatchCommandedState != SERVO_LATCH_STATE_OPEN",
            "g_closedLoopFault != 0U",
            "g_homingActive != 0U",
            "g_manualMotionDirection != 0U",
            "g_sineRunning != 0U",
            "g_parametricRunning != 0U",
            "g_parametricTestRunning != 0U",
            "g_parametricClosedLoopRunning != 0U",
            "amplitudeMm > CLOSED_LOOP_MAX_MM",
            "CLOSED_LOOP_TRAVEL_MARGIN_MM",
        ):
            self.assertIn(required_guard, start)
        self.assertNotIn("g_recording != 0U", start)
        self.assertNotIn("g_closedLoopFault = 0U", start)
        for field in (
            "phaseDeg * (PI_F / 180.0f)",
            "type=autores_start ok=1 active=1 active_mode=parametric_closed_loop",
            "amplitude_mm=%.6f phase_deg=%.6f",
        ):
            self.assertIn(field, start)

        legacy_test = function_body("UpdateParametricTest")
        legacy_guard_match = re.search(
            r"if \(elapsedMs < PARAMETRIC_TEST_PERIOD_MS\)\s*\{([^}]*)\}",
            legacy_test,
        )
        self.assertIsNotNone(legacy_guard_match)
        legacy_guard = legacy_guard_match.group(1)
        self.assertRegex(legacy_guard, r"^\s*return;\s*$")
        for variable in (
            "directAgc", "quadAgc", "directSat", "quadSat", "combined",
            "outputSat", "masterOut", "rateDelta",
        ):
            self.assertNotIn(variable, legacy_guard)
        self.assertIn("PARAMETRIC_TEST_PHASE_COS", legacy_test)
        self.assertIn("PARAMETRIC_TEST_PHASE_SIN", legacy_test)
        self.assertNotIn("g_parametricClosedLoopPhaseCos", legacy_test)
        self.assertNotIn("g_parametricClosedLoopPhaseSin", legacy_test)
        self.assertLess(
            legacy_test.index("if (elapsedMs < PARAMETRIC_TEST_PERIOD_MS)"),
            legacy_test.index("g_parametricTestLastSampleMs = now;"),
        )

        update = function_body("UpdateParametricClosedLoop")
        closed_guard_match = re.search(
            r"if \(\(int32_t\)\(now - g_parametricClosedLoopNextSampleMs\) < 0\)\s*\{([^}]*)\}",
            update,
        )
        self.assertIsNotNone(closed_guard_match)
        closed_guard = closed_guard_match.group(1)
        self.assertRegex(closed_guard, r"^\s*return;\s*$")
        for variable in (
            "directAgc", "quadAgc", "directSat", "quadSat", "combined",
            "outputSat", "masterOut", "rateDelta",
        ):
            self.assertNotIn(variable, closed_guard)
        self.assertIn("g_parametricClosedLoopNextSampleMs += PARAMETRIC_TEST_PERIOD_MS", update)
        self.assertIn("while ((int32_t)(now - g_parametricClosedLoopNextSampleMs) >= 0)", update)
        self.assertIn("phase=0 selects quadrature only", update)
        self.assertIn("quadSat * g_parametricClosedLoopPhaseCos", update)
        self.assertIn("directSat * g_parametricClosedLoopPhaseSin", update)
        self.assertIn("g_parametricClosedLoopAmplitudeMm *", update)
        self.assertIn(
            "g_parametricClosedLoopRateLimitedRad /\n                           "
            "(PARAMETRIC_TEST_OUTPUT_SATURATION * PARAMETRIC_TEST_MASTER_GAIN)",
            update,
        )
        saturation = float(re.search(r"#define PARAMETRIC_TEST_OUTPUT_SATURATION\s+([0-9.]+)f", FIRMWARE_SOURCE).group(1))
        master_gain = float(re.search(r"#define PARAMETRIC_TEST_MASTER_GAIN\s+([0-9.]+)f", FIRMWARE_SOURCE).group(1))
        controller_output_max = saturation * master_gain
        self.assertEqual(controller_output_max, 4.0)
        for rate_limited_output in (-controller_output_max, controller_output_max):
            normalized_output = rate_limited_output / controller_output_max
            pre_safety_command_mm = 5.0 * normalized_output
            self.assertGreaterEqual(normalized_output, -1.0)
            self.assertLessEqual(normalized_output, 1.0)
            self.assertLessEqual(abs(pre_safety_command_mm), 5.0)
        self.assertRegex(FIRMWARE_SOURCE, r"#define CLOSED_LOOP_MAX_MM\s+7\.5f")
        self.assertRegex(FIRMWARE_SOURCE, r"#define CLOSED_LOOP_TRAVEL_MARGIN_MM\s+2\.0f")
        self.assertRegex(FIRMWARE_SOURCE, r"#define CLOSED_LOOP_MAX_STEP_HZ\s+6000U")
        self.assertLess(update.index("zSimscapeMm = g_parametricClosedLoopAmplitudeMm"),
                        update.index("zMotorTargetMm = CLOSED_LOOP_COMMAND_SIGN"))
        self.assertIn("zMotorTargetMm > CLOSED_LOOP_MAX_MM", update)
        self.assertIn("CLOSED_LOOP_MAX_STEP_HZ", function_body("ClosedLoopActuatorOnTimer"))
        checked_signals = (
            "directAgc",
            "quadAgc",
            "directSat",
            "quadSat",
            "combined",
            "outputSat",
            "masterOut",
            "rateDelta",
        )
        for body in (legacy_test, update):
            for signal in checked_signals:
                with self.subTest(function="controller", signal=signal):
                    assert_assigned_before_read(body, signal)

        legacy_start = function_body("ProcessLine").split(
            'if (strncmp(line, "parametric_closed_loop", 22) == 0', 1
        )[1].split('if (strncmp(line, "scale ", 6) == 0', 1)[0]
        self.assertIn("g_parametricClosedLoopExperimentMode = 0U", legacy_start)
        self.assertIn("g_parametricClosedLoopPhaseCos = PARAMETRIC_TEST_PHASE_COS", legacy_start)
        self.assertIn("g_parametricClosedLoopPhaseSin = PARAMETRIC_TEST_PHASE_SIN", legacy_start)

        stop = maintenance.split('if (strcmp(end, "autores_stop") == 0)', 1)[1].split(
            'if (strcmp(end, "manual_up") == 0', 1
        )[0]
        self.assertIn("g_parametricClosedLoopRunning = 0U", stop)
        self.assertIn("ClosedLoopActuatorStop()", stop)
        self.assertIn("type=autores_stop ok=1 active=0 active_mode=%s", stop)

    def test_fault_clear_is_correlated_condition_validated_and_non_motion_mutating(self) -> None:
        maintenance = function_body("ProcessMaintenanceCommand")
        fault_clear = maintenance.split('if (strcmp(end, "fault_clear") == 0)', 1)[1].split(
            'if (strncmp(end, "sine_start ", 11U) == 0)', 1
        )[0]
        for required in (
            "g_homingActive != 0U",
            "g_manualMotionDirection != 0U",
            "g_run != 0U",
            "g_sineRunning != 0U",
            "g_parametricRunning != 0U",
            "g_parametricTestRunning != 0U",
            "g_parametricClosedLoopRunning != 0U",
            "g_recording != 0U",
            "previousFault == 1U && UpperLimitPressed() != 0U",
            "upper_limit_still_active",
            "previousFault == 2U && LowerLimitPressed() != 0U",
            "lower_limit_still_active",
            "previousFault == 3U && AS5600_ReadAngle(&angle) != HAL_OK",
            "angle_sensor_unavailable",
            "previousFault == 0U",
            "cleared=0",
            "g_closedLoopFault = 0U",
            "g_closedLoopFaultPending = 0U",
            "event: type=fault_cleared previous_fault=%u",
            "type=fault_clear ok=1 fault=0 cleared=1 previous_fault=%u",
            "type=fault_clear ok=0 fault=%u error=%s",
        ):
            self.assertIn(required, fault_clear)
        for forbidden_side_effect in (
            "Home()",
            "StepOne(",
            "ClosedLoopActuatorKick()",
            "ServoLatchCommand(",
            "g_homed =",
            "g_travelSteps =",
            "g_positionSteps =",
            "g_angleZeroRaw =",
            "g_servoLatchCommandedState =",
        ):
            self.assertNotIn(forbidden_side_effect, fault_clear)
        self.assertNotIn('"fault_clear"', function_body("Home"))

        legacy_start = function_body("ProcessLine").split(
            'if (strncmp(line, "parametric_closed_loop", 22) == 0', 1
        )[1].split('if (strncmp(line, "scale ", 6) == 0', 1)[0]
        self.assertIn("g_closedLoopFault != 0U", legacy_start)
        self.assertNotIn("g_closedLoopFault = 0U", legacy_start)
        self.assertNotIn("type=fault_cleared", legacy_start)

    def test_phase_tracked_sine_reuses_phase_basis_without_mutating_existing_autores_controller(self) -> None:
        phase_sine = function_body("UpdatePhaseTrackedSine")
        for required in (
            "atan2f(g_phaseTrackedSine.quadratureFiltered, -dcBlocked)",
            "hypotf(dcBlocked, g_phaseTrackedSine.quadratureFiltered)",
            "WrapRadiansToPi",
            "measuredOmega",
            "PHASE_TRACKED_OMEGA_FILTER_TAU_S",
            "PHASE_TRACKED_PHASE_CORRECTION_GAIN",
            "PHASE_TRACKED_MAX_PHASE_RATE_RAD_S",
            "PHASE_TRACKED_MIN_VECTOR_MAGNITUDE",
            "PHASE_TRACKED_MAX_UNCONFIDENT_MS",
            "g_phaseTrackedSine.omega2xRadS * dt",
            "g_phaseTrackedSine.trackedPhaseRad -",
            "sinf(g_phaseTrackedSine.trackedPhaseRad -",
            "g_phaseTrackedSine.amplitudeMm * normalizedSine",
            "PHASE_TRACKED_START_RAMP_MS",
            "g_parametricClosedLoopTargetSteps = targetSteps",
            "CLOSED_LOOP_MAX_MM",
            "CLOSED_LOOP_TRAVEL_MARGIN_MM",
            "g_phaseTrackedSine.phaseHistoryValid",
            "PHASE_TRACKED_ACQUISITION_SAMPLES",
            "g_phaseTrackedSine.acquisitionCount",
            "g_phaseTrackedSine.phaseAcquiredMs = now",
        ):
            self.assertIn(required, phase_sine)
        self.assertNotIn("ParametricTestAgc(", phase_sine)
        first_vector = phase_sine.split(
            "if (g_phaseTrackedSine.phaseHistoryValid == 0U)", 1
        )[1].split("else", 1)[0]
        self.assertIn("g_phaseTrackedSine.acquisitionCount = 1U", first_vector)
        self.assertNotIn("g_phaseTrackedSine.phaseValid = 1U", first_vector)
        self.assertNotIn("g_phaseTrackedSine.omegaValid = 1U", first_vector)
        self.assertIn(
            "g_phaseTrackedSine.acquisitionCount = 0U;\n                        g_phaseTrackedSine.omegaValid = 0U;",
            phase_sine,
        )

        rate_rejection = phase_sine.split(
            "fabsf(measuredOmega) > PHASE_TRACKED_MAX_PHASE_RATE_RAD_S)", 1
        )[1].split("\n                }\n                else", 1)[0]
        for history_update in (
            "previousRawPhaseRad =",
            "lastPhaseMeasurementMs =",
            "measuredUnwrappedPhaseRad +=",
            "trackedPhaseRad =",
        ):
            self.assertNotIn(history_update, rate_rejection)

        locked_loss_timer = phase_sine.split(
            "if (g_phaseTrackedSine.phaseValid != 0U &&\n        g_phaseTrackedSine.invalidSinceActive != 0U)",
            1,
        )
        self.assertEqual(len(locked_loss_timer), 2)
        maintenance = function_body("ProcessMaintenanceCommand")
        self.assertIn('strncmp(end, "phase_sine_start ", 17U)', maintenance)
        self.assertIn('strcmp(end, "phase_sine_stop") == 0', maintenance)
        phase_start = maintenance.split(
            'if (strncmp(end, "phase_sine_start ", 17U) == 0)', 1
        )[1].split('if (strcmp(end, "phase_sine_stop") == 0)', 1)[0]
        self.assertIn("currentPositionMm = (float)g_positionSteps / g_stepsPerMm", phase_start)
        self.assertIn(
            "g_phaseTrackedSine.startPositionMm =\n                CLOSED_LOOP_COMMAND_SIGN * currentPositionMm",
            phase_start,
        )
        self.assertIn("fabsf(currentPositionMm) > amplitudeMm", phase_start)
        self.assertIn("g_phaseTrackedSine.status = PHASE_TRACKED_STATUS_ACQUIRING", phase_start)
        self.assertIn("g_phaseTrackedSine.invalidSinceActive = 0U", phase_start)
        self.assertNotIn("g_phaseTrackedSine.invalidSinceMs = now", phase_start)
        for diagnostic_field in (
            "phase2_raw_rad",
            "phase2_tracked_rad",
            "omega_2x_rad_s",
            "phase_magnitude",
            "normalized_sine",
            "target_mm",
            "actual_mm",
        ):
            self.assertIn(diagnostic_field, phase_start)

        existing_autores = function_body("UpdateParametricClosedLoop")
        self.assertIn("g_parametricClosedLoopPhaseCos", existing_autores)
        self.assertIn("g_parametricClosedLoopPhaseSin", existing_autores)
        self.assertIn("ParametricTestAgc(", existing_autores)
        self.assertNotIn("g_phaseTrackedSine.", existing_autores)
        self.assertNotIn("g_phaseTrackedSine", function_body("UpdateParametricTest"))

        for setting in (
            "#define PHASE_TRACKED_MIN_VECTOR_MAGNITUDE 0.05f",
            "#define PHASE_TRACKED_ACQUISITION_SAMPLES 3U",
            "#define PHASE_TRACKED_MAX_PHASE_RATE_RAD_S 40.0f",
            "#define PHASE_TRACKED_OMEGA_FILTER_TAU_S   0.05f",
            "#define PHASE_TRACKED_PHASE_CORRECTION_GAIN 0.15f",
            "#define PHASE_TRACKED_MAX_UNCONFIDENT_MS  250U",
            "#define PHASE_TRACKED_START_RAMP_MS       250U",
        ):
            self.assertIn(setting, FIRMWARE_SOURCE)

        self.assertRegex(FIRMWARE_SOURCE, r"#define CLOSED_LOOP_MAX_MM\s+7\.5f")
        self.assertRegex(FIRMWARE_SOURCE, r"#define CLOSED_LOOP_TRAVEL_MARGIN_MM\s+2\.0f")
        self.assertRegex(FIRMWARE_SOURCE, r"#define CLOSED_LOOP_MAX_STEP_HZ\s+6000U")


if __name__ == "__main__":
    unittest.main()