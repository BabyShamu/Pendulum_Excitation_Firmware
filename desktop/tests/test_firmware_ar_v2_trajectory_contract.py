import re
import unittest
from pathlib import Path


FIRMWARE_SOURCE = (
    Path(__file__).resolve().parents[2] / "src" / "main.c"
).read_text(encoding="utf-8")
ARV2_HEADER = (
    Path(__file__).resolve().parents[2] / "src" / "auto_resonance_v2.h"
).read_text(encoding="utf-8")
PLATFORMIO_CONFIG = (
    Path(__file__).resolve().parents[2] / "platformio.ini"
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
    def test_ar_v2_has_a_local_waveform_polarity_correction(self) -> None:
        self.assertRegex(ARV2_HEADER, r"#define ARV2_EXCITATION_SIGN\s+\(-1\.0f\)")
        self.assertIn("wave=ARV2_EXCITATION_SIGN*wave", ARV2_HEADER)
        self.assertIn("s->measured_phase_rad=alpha", ARV2_HEADER)

    def test_shared_motor_polarity_remains_unchanged(self) -> None:
        self.assertIn("#define CLOSED_LOOP_COMMAND_SIGN        (-1.0f)", FIRMWARE_SOURCE)
        self.assertNotIn("ARV2_EXCITATION_SIGN", FIRMWARE_SOURCE)

    def test_platformio_enables_float_input_scanning(self) -> None:
        self.assertIn("-Wl,-u,_scanf_float", PLATFORMIO_CONFIG)

    def test_ar_v2_parser_accepts_three_numeric_arguments(self) -> None:
        self.assertIn(
            'sscanf(line+12,"%f %f %u %c",&amp,&phase,&mode,&trailing)!=3',
            FIRMWARE_SOURCE,
        )
        self.assertIn("amp<=0.0f || amp>ARV2_MAX_AMPLITUDE_MM || mode<1U || mode>3U", FIRMWARE_SOURCE)
        self.assertRegex(FIRMWARE_SOURCE, r"#define ARV2_MAX_AMPLITUDE_MM\s+20\.0f")

    def test_ar_v2_travel_check_covers_both_quantized_extremes(self) -> None:
        start = FIRMWARE_SOURCE.split('if (strncmp(line,"ar_v2 start ",12U)==0', 1)[1]
        start = start.split('UartPrint("ar_v2 started', 1)[0]
        for required in (
            "amplitudeSteps=(int32_t)lroundf(amp*g_stepsPerMm)",
            "lowerTargetSteps=currentPositionSteps-amplitudeSteps",
            "upperTargetSteps=currentPositionSteps+amplitudeSteps",
            "lowerTargetSteps < -allowed",
            "upperTargetSteps > allowed",
            "requested amplitude does not fit available travel",
        ):
            self.assertIn(required, start)

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
        self.assertIn("g_tim6TrajectoryOwner=TIM6_TRAJECTORY_OWNER_PARAMETRIC", start)
        self.assertIn("g_parametricV2TargetSteps=g_positionSteps", start)

    def test_all_ar_v2_modes_claim_parametric_tim6_ownership(self) -> None:
        process_line = function_body("ProcessLine")
        ar_start = process_line.split('if (strncmp(line,"ar_v2 start ",12U)==0', 1)[1]
        ar_start = ar_start.split('UartPrint("ar_v2 started', 1)[0]
        self.assertIn("mode<1U || mode>3U", ar_start)
        self.assertIn("g_tim6TrajectoryOwner=TIM6_TRAJECTORY_OWNER_PARAMETRIC", ar_start)
        self.assertIn("g_parametricV2TargetSteps=g_positionSteps", ar_start)

    def test_parametric_enhanced_and_trajectory_test_claim_tim6_ownership(self) -> None:
        process_line = function_body("ProcessLine")
        self.assertIn(
            'g_tim6TrajectoryOwner = TIM6_TRAJECTORY_OWNER_PARAMETRIC',
            process_line,
        )
        self.assertIn(
            'g_tim6TrajectoryOwner=TIM6_TRAJECTORY_OWNER_PARAMETRIC',
            process_line,
        )
        self.assertIn("g_v2TestRunning = 1U", process_line)
        self.assertIn('parametric_v2 started (experimental)', process_line)

    def test_ar_v2_start_resets_estimator_and_preserves_sample_deadline(self) -> None:
        start = FIRMWARE_SOURCE.split(
            'if (strncmp(line,"ar_v2 start ",12U)==0', 1
        )[1].split('UartPrint("ar_v2 started', 1)[0]
        update = function_body("UpdateAutoResonanceV2")
        for required in (
            "ARV2_Reset(&g_arv2)",
            "g_arv2ReadFailures=0U",
            "g_arv2Samples=0U",
            "g_arv2Start=HAL_GetTick()",
            "g_arv2Next=g_arv2Start+PARAMETRIC_TEST_PERIOD_MS",
        ):
            self.assertIn(required, start)
        for required in (
            "if ((int32_t)(now-g_arv2Next)<0) return;",
            "g_arv2Next+=PARAMETRIC_TEST_PERIOD_MS",
            "AS5600_ReadAngle(&raw)",
            "ARV2_Step(&g_arv2",
        ):
            self.assertIn(required, update)
        self.assertRegex(FIRMWARE_SOURCE, r"#define PARAMETRIC_TEST_PERIOD_MS\s+5U")

    def test_healthy_sensor_path_does_not_wait_or_inhibit_tim6(self) -> None:
        timer = function_body("ParametricV2OnTimer")
        self.assertIn("g_closedLoopFault != 0U", timer)
        self.assertIn("g_pendulumAngleSafetyAvailable == 0U", timer)
        self.assertIn("SensorWaitingHasPendingTarget() == 0U", timer)
        waiting = function_body("SensorWaitingHasPendingTarget")
        self.assertIn("g_controllerWaitingForSensor == 0U", waiting)

    def test_sine_has_exclusive_tim6_ownership_without_parametric_flags(self) -> None:
        timer = function_body("ParametricV2OnTimer")
        active = function_body("ClosedLoopActuatorIsActive")
        update = function_body("UpdateSine")
        irq = FIRMWARE_SOURCE.split("void TIM6_DAC_IRQHandler(void)", 1)[1].split(
            "void TIM7_DAC_IRQHandler", 1
        )[0]
        self.assertIn("TIM6_TRAJECTORY_OWNER_SINE", irq)
        self.assertIn("TIM6_TRAJECTORY_OWNER_SINE", timer)
        self.assertIn("TIM6_TRAJECTORY_OWNER_NONE", active)
        self.assertIn("g_parametricV2TargetSteps = targetSteps", update)
        self.assertIn("ClosedLoopActuatorKick()", update)
        self.assertNotIn("StepOne(", update)

    def test_sine_stop_releases_tim6_before_return_to_zero(self) -> None:
        process_line = function_body("ProcessLine")
        stop = process_line.split('if (strncmp(line, "sine", 4) == 0', 1)[1]
        stop = stop.split("// TEMPORARY UART/PARSER DEBUG", 1)[0]
        self.assertIn("g_tim6TrajectoryOwner = TIM6_TRAJECTORY_OWNER_NONE", stop)
        self.assertIn("ClosedLoopActuatorStop()", stop)
        self.assertIn("ReturnToCenter()", stop)

    def test_ar_v2_modes_are_distinct_fourier_waveforms(self) -> None:
        self.assertIn("if (mode==1U) wave=h1", ARV2_HEADER)
        self.assertIn("else if (mode==2U)", ARV2_HEADER)
        self.assertIn("else wave=1.20713f*h1+0.28101f*h3+0.07389f*h5", ARV2_HEADER)

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

    def test_unified_parametric_stop_is_allowed_while_ar_v2_is_active(self) -> None:
        process_line = function_body("ProcessLine")
        guard = process_line.split("if (g_arv2Running != 0U", 1)[1].split(
            'UartPrint("ar_v2 active:', 1
        )[0]
        self.assertIn('strcmp(line,"parametric stop")!=0', guard)

        stop = process_line.split(
            'if (parsed == 1 && strcmp(variant, "stop") == 0)', 1
        )[1].split(
            'if (parsed != 3', 1
        )[0]
        self.assertIn("StopAutoResonanceV2()", stop)
        self.assertIn('RequestReturnToZero("experiment_stop")', stop)
        self.assertIn("g_arv2Running", stop)

    def test_ar_v2_stop_commands_preserve_legacy_and_global_paths(self) -> None:
        process_line = function_body("ProcessLine")
        self.assertIn('strcmp(line,"ar_v2 stop")==0', process_line)
        self.assertIn('strcmp(line, "stop") == 0', process_line)
        self.assertIn('strcmp(line,"parametric stop")!=0', process_line)

    def test_parametric_v2_stop_releases_parametric_owner(self) -> None:
        stop = function_body("StopParametricController")
        self.assertIn("g_tim6TrajectoryOwner == TIM6_TRAJECTORY_OWNER_PARAMETRIC", stop)
        self.assertIn("g_tim6TrajectoryOwner = TIM6_TRAJECTORY_OWNER_NONE", stop)

    def test_v2_trajectory_keeps_runtime_acceleration_configuration(self) -> None:
        trajectory = function_body("ParametricV2OnTimer")
        self.assertIn("g_parametricV2AccelHzS", trajectory)
        self.assertNotIn("PARAMETRIC_V2_ACCEL_MAX_HZ_S", trajectory)
        self.assertRegex(
            FIRMWARE_SOURCE,
            r"#define PARAMETRIC_V2_ACCEL_DEFAULT_HZ_S\s+100000\.0f",
        )
        self.assertRegex(FIRMWARE_SOURCE, r"CLOSED_LOOP_MAX_MM\s+7\.5f")

    def test_v2_acceleration_command_documents_unchanged_runtime_bounds(self) -> None:
        self.assertIn(
            "PARAMETRIC_V2_ACCEL_DEFAULT_HZ_S 100000.0f",
            FIRMWARE_SOURCE,
        )
        self.assertIn("6000..1000000", FIRMWARE_SOURCE)
        self.assertIn('if (strcmp(line, "accel") == 0)', function_body("ProcessLine"))
        self.assertIn('if (strncmp(line, "accel ", 6) == 0)', function_body("ProcessLine"))

    def test_status_and_diagnostics_report_tim6_ownership(self) -> None:
        self.assertIn("trajectory_owner=%s", function_body("PrintStatus"))
        self.assertIn("return_zero_state=%u", function_body("PrintStatus"))
        self.assertIn("target_steps=%ld", function_body("PrintDiagnostics"))
        self.assertIn("Tim6TrajectoryOwnerName()", FIRMWARE_SOURCE)

    def test_unified_parametric_names_delegate_without_changing_algorithms(self) -> None:
        process_line = function_body("ProcessLine")
        self.assertIn('strncmp(line, "parametric ", 11U)', process_line)
        self.assertIn('strcmp(variant, "classic")', process_line)
        self.assertIn('strcmp(variant, "enhanced")', process_line)
        self.assertIn('strcmp(variant, "ar1")', process_line)
        self.assertIn('strcmp(variant, "ar2")', process_line)
        self.assertIn('strcmp(variant, "ar3")', process_line)
        self.assertIn('StartParametricController(amplitude, phase)', process_line)
        self.assertIn('"parametric_v2 %.9g %.9g"', process_line)
        self.assertIn('"ar_v2 start %.9g %.9g %u"', process_line)
        self.assertIn(
            "value < PARAMETRIC_V2_ACCEL_MIN_HZ_S",
            function_body("ProcessLine"),
        )
        self.assertIn(
            "value > PARAMETRIC_V2_ACCEL_MAX_HZ_S",
            function_body("ProcessLine"),
        )

    def test_return_to_zero_uses_shared_runtime_acceleration(self) -> None:
        request = function_body("RequestReturnToZero")
        timer = function_body("ParametricV2OnTimer")
        self.assertIn("g_parametricV2AccelHzS", timer)
        self.assertNotIn("g_parametricV2AccelHzS =", request)
        self.assertIn("TIM6_TRAJECTORY_OWNER_RETURN_ZERO", request)
        self.assertNotIn("g_parametricV2Enabled = 1U", request)
        self.assertNotIn("g_parametricRunning = 1U", request)

    def test_faults_disable_ar_v2_and_parametric_v2_state(self) -> None:
        fault = function_body("ClosedLoopActuatorFault")
        sensor_fault = function_body("PendulumAngleSensorFaultTrip")
        for body in (fault, sensor_fault):
            self.assertIn("g_parametricV2Enabled = 0U", body)
            self.assertIn("g_parametricV2SpeedHz = 0.0f", body)
