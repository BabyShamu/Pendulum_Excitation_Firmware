import unittest

from PySide6.QtWidgets import QApplication

from pendulum_workbench.ui.status_indicator import IndicatorTone, StatusChip


class StatusChipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_compact_chip_updates_explicit_text_accessibility_and_tone(self) -> None:
        chip = StatusChip("Upper limit")
        self.assertEqual(chip.state_text, "UPPER LIMIT: UNKNOWN")
        self.assertFalse(hasattr(chip, "led"))

        chip.set_status("ACTIVE", IndicatorTone.WARNING)

        self.assertEqual(chip.state_text, "UPPER LIMIT: ACTIVE")
        self.assertEqual(chip.tone, IndicatorTone.WARNING)
        self.assertEqual(chip.background_color, "#fff0d6")
        self.assertEqual(chip.border_color, "#e0ad5c")
        self.assertIn("QWidget#statusChip", chip.styleSheet())
        self.assertIn("background-color: #fff0d6", chip.styleSheet())
        self.assertIn("border: 1px solid #e0ad5c", chip.styleSheet())
        self.assertIn("border-radius: 7px", chip.styleSheet())
        self.assertEqual(chip.accessibleName(), "UPPER LIMIT: ACTIVE")
        self.assertIn("Upper limit state is ACTIVE", chip.accessibleDescription())


if __name__ == "__main__":
    unittest.main()