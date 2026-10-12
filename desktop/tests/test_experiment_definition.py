import unittest

from pendulum_workbench.domain.experiment_run import (
    ExperimentMode,
    ExperimentRunState,
    VerticalSineDefinition,
)


class VerticalSineDefinitionTests(unittest.TestCase):
    def test_mode_and_definition_have_no_editable_initial_angle(self) -> None:
        definition = VerticalSineDefinition(5.0, 1.8, 30.0)

        self.assertEqual(definition.mode, ExperimentMode.VERTICAL_SINUSOIDAL_EXCITATION)
        self.assertFalse(hasattr(definition, "target_initial_angle_deg"))
        self.assertEqual(ExperimentRunState.READY.value, "READY")

    def test_rejects_nonfinite_or_nonpositive_setup_values(self) -> None:
        for values in ((0.0, 1.8, 30.0), (5.0, 0.0, 30.0), (5.0, 1.8, 0.0)):
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


if __name__ == "__main__":
    unittest.main()