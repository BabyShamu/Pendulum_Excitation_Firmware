import unittest

from PySide6.QtWidgets import QApplication

from pendulum_workbench.ui.status_indicator import IndicatorTone, StatusIndicator


class StatusIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_widget_updates_text_tone_and_accessible_description_for_each_state(self) -> None:
        indicator = StatusIndicator("Upper limit")
        states = (
            ("Clear", IndicatorTone.NORMAL),
            ("ACTIVE", IndicatorTone.WARNING),
            ("Unknown", IndicatorTone.UNKNOWN),
            ("No fault", IndicatorTone.GOOD),
            ("Fault 2", IndicatorTone.ERROR),
        )

        for text, tone in states:
            with self.subTest(text=text):
                description = f"Upper limit state: {text}."
                indicator.set_status(text, tone, description)
                self.assertEqual(indicator.state_text, text)
                self.assertEqual(indicator.tone, tone)
                self.assertEqual(indicator.led.tone, tone)
                self.assertEqual(indicator.accessibleName(), f"Upper limit: {text}")
                self.assertEqual(indicator.accessibleDescription(), description)
                self.assertGreaterEqual(indicator.led.width(), 28)
                self.assertGreaterEqual(indicator.led.height(), 28)

    def test_state_text_remains_available_independently_of_indicator_color(self) -> None:
        indicator = StatusIndicator("Lower limit")

        indicator.set_status("ACTIVE", IndicatorTone.WARNING)

        self.assertEqual(indicator.state_label.text(), "ACTIVE")
        self.assertNotEqual(indicator.led.color.name(), "#ffffff")
        self.assertIn("ACTIVE", indicator.accessibleDescription())


if __name__ == "__main__":
    unittest.main()