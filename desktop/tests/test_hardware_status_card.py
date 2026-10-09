import unittest

from PySide6.QtWidgets import QApplication

from pendulum_workbench.ui.hardware_status_card import HardwareStatusCard
from pendulum_workbench.ui.status_indicator import IndicatorTone


class HardwareStatusCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_card_separates_title_and_state_and_updates_style(self) -> None:
        card = HardwareStatusCard("Upper Limit")
        card.set_state("Clear", IndicatorTone.NORMAL)
        self.assertEqual(card.title_label.text(), "UPPER LIMIT")
        self.assertEqual(card.state_text, "CLEAR")

        card.set_state("Active", IndicatorTone.WARNING)
        self.assertEqual(card.title_label.text(), "UPPER LIMIT")
        self.assertEqual(card.state_text, "ACTIVE")
        self.assertIn("#fff0d6", card.styleSheet())
        self.assertIn("border-radius: 8px", card.styleSheet())
        self.assertEqual(card.accessibleName(), "Upper Limit: Active")
        self.assertGreaterEqual(card.minimumWidth(), 112)
        self.assertGreaterEqual(card.minimumHeight(), 60)

    def test_long_fault_state_is_a_separate_unclipped_state_label(self) -> None:
        card = HardwareStatusCard("Fault", "No fault")

        self.assertEqual(card.title_label.text(), "FAULT")
        self.assertEqual(card.state_text, "NO FAULT")
        self.assertTrue(card.state_label.wordWrap())


if __name__ == "__main__":
    unittest.main()