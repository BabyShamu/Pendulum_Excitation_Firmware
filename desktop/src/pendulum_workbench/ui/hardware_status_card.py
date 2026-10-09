from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from pendulum_workbench.ui.status_indicator import IndicatorTone, StatusChip


class HardwareStatusCard(QFrame):
    _styles = {
        IndicatorTone.NORMAL: ("#eef1ef", "#c9d2ce", "#34423e"),
        IndicatorTone.GOOD: ("#e5f3eb", "#8fc3a3", "#205b42"),
        IndicatorTone.WARNING: ("#fff0d6", "#e0ad5c", "#754707"),
        IndicatorTone.ERROR: ("#fbe8e6", "#d58a83", "#812f28"),
        IndicatorTone.UNKNOWN: ("#eef0ef", "#c8cecb", "#59635f"),
    }

    def __init__(self, title: str, state: str = "Unknown", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = title
        self.setObjectName("hardwareStatusCard")
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("hardwareStatusTitle")
        self.state_label = QLabel(state.upper())
        self.state_label.setObjectName("hardwareStatusValue")
        self.state_label.setWordWrap(True)
        self.title_label.setFont(QFont("Bahnschrift", 8, QFont.Weight.DemiBold))
        self.state_label.setFont(QFont("Bahnschrift", 10, QFont.Weight.Bold))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)
        layout.addWidget(self.title_label)
        layout.addWidget(self.state_label, 1)
        self.setMinimumSize(112, 60)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.set_state(state, IndicatorTone.UNKNOWN)

    @property
    def state_text(self) -> str:
        return self.state_label.text()

    def set_state(self, state: str, tone: IndicatorTone) -> None:
        background, border, foreground = self._styles[tone]
        self.state_label.setText(state.upper())
        self.state_label.setStyleSheet(f"color: {foreground}; background: transparent; border: none;")
        self.title_label.setStyleSheet("color: #66746e; background: transparent; border: none;")
        self.setStyleSheet(
            "QFrame#hardwareStatusCard {"
            f"background: {background}; border: 1px solid {border}; border-radius: 8px;"
            "}"
        )
        self.setAccessibleName(f"{self.title}: {state}")
        self.setAccessibleDescription(f"Hardware {self.title} status: {state}.")