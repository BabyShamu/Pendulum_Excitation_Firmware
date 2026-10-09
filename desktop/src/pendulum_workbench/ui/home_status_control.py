from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget
from PySide6.QtGui import QFont

from pendulum_workbench.domain.models import HomeStatus
from pendulum_workbench.ui.status_indicator import IndicatorTone, StatusChip


class HomeStatusControl(QWidget):
    _tones = {
        HomeStatus.UNKNOWN: IndicatorTone.UNKNOWN,
        HomeStatus.HOME_REQUIRED: IndicatorTone.WARNING,
        HomeStatus.HOMING: IndicatorTone.WARNING,
        HomeStatus.HOMED: IndicatorTone.GOOD,
        HomeStatus.HOMING_FAILED: IndicatorTone.ERROR,
    }

    def __init__(self, status: HomeStatus = HomeStatus.UNKNOWN) -> None:
        super().__init__()
        self.setObjectName("homeStatusControl")
        self.setMinimumHeight(56)
        self.setToolTip("Read-only homing status. Home commands are not available in this milestone.")
        self.setStyleSheet(
            "QWidget#homeStatusControl {"
            "background: #f1f6f3; border: 1px solid #c9d9d0; border-radius: 8px;"
            "}"
            "QLabel#sectionTitle { background: transparent; border: none; }"
        )
        self.title = QLabel("DEVICE HOME")
        self.title.setObjectName("sectionTitle")
        self.title.setFont(QFont("Bahnschrift", 9, QFont.Weight.Bold))
        self.indicator = StatusChip("Home", parent=self, show_name=False)
        self.indicator.setMinimumSize(150, 36)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(10)
        layout.addWidget(self.title)
        layout.addWidget(self.indicator)
        self.set_home_status(status)

    @property
    def status(self) -> HomeStatus:
        for status, tone in self._tones.items():
            if self.indicator.tone == tone and self.indicator.state_text == status.value:
                return status
        return HomeStatus.UNKNOWN

    def set_home_status(self, status: HomeStatus) -> None:
        self.indicator.set_status(
            status.value,
            self._tones[status],
            f"Device home status is {status.value}.",
        )
        self.setAccessibleName(f"Device home status: {status.value}")
        self.setAccessibleDescription(
            "Read-only homing status. This control does not send a Home command."
        )