import unittest

from pendulum_workbench.domain.experiment_run import (
    AutoResonanceDefinition,
    ExperimentMode,
    ExperimentRunState,
    PhaseTrackedSineDefinition,
    VerticalSineDefinition,
)


class VerticalSineDefinitionTests(unittest.TestCase):
    def test_mode_and_definition_have_no_editable_initial_angle(self) -> None:
        definition = VerticalSineDefinition(5.0, 1.8, 30.0)

        self.assertEqual(definition.mode, ExperimentMode.VERTICAL_SINUSOIDAL_EXCITATION)
        self.assertFalse(hasattr(definition, "target_initial_angle_deg"))
        self.assertEqual(ExperimentRunState.READY.value, "READY")

    def test_zero_amplitude_is_a_valid_stationary_baseline(self) -> None:
        definition = VerticalSineDefinition(0.0, 1.8, 30.0)

        definition.validate()
        self.assertEqual(
            definition.final_name(18.2),
            "VS_A0mm_F1.8Hz_Thetap18.2deg_T30s",
        )

    def test_rejects_negative_amplitude_and_nonpositive_frequency_or_duration(self) -> None:
        for values in ((-0.1, 1.8, 30.0), (5.0, 0.0, 30.0), (5.0, 1.8, 0.0)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                VerticalSineDefinition(*values).validate()
        with self.assertRaisesRegex(ValueError, "finite"):
            VerticalSineDefinition(float("nan"), 1.8, 30.0).validate()

    def test_name_generation_is_deterministic_safe_and_preserves_angle_sign(self) -> None:
        definition = VerticalSineDefinition(5.0, 1.8, 30.0, label="My Lab/Run")

        self.assertEqual(
            definition.partial_name(),
            "My_Lab_Run_VS_A5mm_F1.8Hz_T30s",
        )
        self.assertEqual(
            definition.final_name(14.8),
            "My_Lab_Run_VS_A5mm_F1.8Hz_Thetap14.8deg_T30s",
        )
        self.assertEqual(
            definition.final_name(-14.8),
            "My_Lab_Run_VS_A5mm_F1.8Hz_Thetam14.8deg_T30s",
        )
        self.assertEqual(
            definition.final_name(0.0),
            "My_Lab_Run_VS_A5mm_F1.8Hz_Theta0deg_T30s",
        )

    def test_manifest_uses_semantic_commanded_and_measured_fields(self) -> None:
        definition = VerticalSineDefinition(5.0, 1.8, 30.0, label="Trial", notes="note")
        manifest = definition.manifest_values(
            measured_initial_angle_deg=-14.8,
        )

        self.assertEqual(manifest["experiment_mode"], "VERTICAL_SINUSOIDAL_EXCITATION")
        self.assertEqual(manifest["commanded_amplitude_mm"], 5.0)
        self.assertEqual(manifest["commanded_frequency_hz"], 1.8)
        self.assertEqual(manifest["requested_duration_s"], 30.0)
        self.assertEqual(manifest["measured_initial_angle_deg"], -14.8)
        self.assertEqual(manifest["generated_run_name"], definition.final_name(-14.8))
        self.assertIn("actual_elapsed_duration_s", manifest)
        self.assertIn("completion_reason", manifest)
        self.assertEqual(manifest["optional_label"], "Trial")
        self.assertEqual(manifest["notes"], "note")
        self.assertNotIn("target_initial_angle_deg", manifest)


class AutoResonanceDefinitionTests(unittest.TestCase):
    def test_mode_defaults_and_manifest_preserve_user_phase_and_amplitude(self) -> None:
        definition = AutoResonanceDefinition(6.0, -12.5, 20.0, label="Trial", notes="note")

        definition.validate()
        self.assertEqual(definition.mode, ExperimentMode.AUTO_RESONANCE_PARAMETRIC_CLOSED_LOOP)
        self.assertEqual(
            definition.final_name(18.2),
            "Trial_AR_A6mm_Phim12.5deg_Thetap18.2deg_T20s",
        )
        manifest = definition.manifest_values(measured_initial_angle_deg=18.2)
        self.assertEqual(manifest["experiment_mode"], "AUTO_RESONANCE_PARAMETRIC_CLOSED_LOOP")
        self.assertEqual(manifest["commanded_amplitude_mm"], 6.0)
        self.assertEqual(manifest["commanded_phase_deg"], -12.5)
        self.assertEqual(manifest["requested_duration_s"], 20.0)
        self.assertEqual(manifest["optional_label"], "Trial")
        self.assertEqual(manifest["notes"], "note")

    def test_rejects_nonpositive_amplitude_duration_and_nonfinite_phase(self) -> None:
        for values in (
            (0.0, 0.0, 20.0),
            (-1.0, 0.0, 20.0),
            (5.0, 0.0, 0.0),
            (5.0, float("nan"), 20.0),
            (5.0, float("inf"), 20.0),
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                AutoResonanceDefinition(*values).validate()

    def test_rejects_cross_mode_definition_override(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires Auto-Resonance"):
            AutoResonanceDefinition(
                6.0,
                0.0,
                20.0,
                mode=ExperimentMode.VERTICAL_SINUSOIDAL_EXCITATION,
            ).validate()


class PhaseTrackedSineDefinitionTests(unittest.TestCase):
    def test_phase_tracked_mode_has_distinct_name_and_preserves_metadata(self) -> None:
        definition = PhaseTrackedSineDefinition(5.0, -20.0, 12.0, label="Trace", notes="replay")

        definition.validate()
        self.assertEqual(definition.mode, ExperimentMode.PHASE_TRACKED_SINE_AUTO_RESONANCE)
        self.assertEqual(
            definition.final_name(17.31),
            "Trace_PTAR_A5mm_Phim20deg_Thetap17.31deg_T12s",
        )
        manifest = definition.manifest_values(measured_initial_angle_deg=17.31)
        self.assertEqual(manifest["experiment_mode"], "PHASE_TRACKED_SINE_AUTO_RESONANCE")
        self.assertEqual(manifest["commanded_amplitude_mm"], 5.0)
        self.assertEqual(manifest["commanded_phase_deg"], -20.0)
        self.assertEqual(manifest["requested_duration_s"], 12.0)
        self.assertNotIn("commanded_frequency_hz", manifest)

    def test_rejects_wrong_mode_invalid_amplitude_phase_or_duration(self) -> None:
        for values in (
            (0.0, 0.0, 12.0),
            (-1.0, 0.0, 12.0),
            (5.0, float("nan"), 12.0),
            (5.0, float("inf"), 12.0),
            (5.0, 0.0, 0.0),
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                PhaseTrackedSineDefinition(*values).validate()
        with self.assertRaisesRegex(ValueError, "requires Phase-Tracked"):
            PhaseTrackedSineDefinition(
                5.0,
                0.0,
                12.0,
                mode=ExperimentMode.AUTO_RESONANCE_PARAMETRIC_CLOSED_LOOP,
            ).validate()


if __name__ == "__main__":
    unittest.main()