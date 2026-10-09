from enum import Enum

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


class IndicatorTone(str, Enum):
    NORMAL = "normal"
    GOOD = "good"
    WARNING = "warning"
    ERROR = "error"
    UNKNOWN = "unknown"


class LedIndicator(QWidget):
    _colors = {
        IndicatorTone.NORMAL: QColor("#73817c"),
        IndicatorTone.GOOD: QColor("#27936a"),
        IndicatorTone.WARNING: QColor("#e18a22"),
        IndicatorTone.ERROR: QColor("#c53b32"),
        IndicatorTone.UNKNOWN: QColor("#9ba5a1"),
    }

    def __init__(
        self,
        tone: IndicatorTone = IndicatorTone.UNKNOWN,
        diameter: int = 32,
    ) -> None:
        super().__init__()
        self._tone = tone
        self._diameter = diameter
        self.setFixedSize(diameter, diameter)
        self.setAccessibleName("Status indicator light")
        self.setAccessibleDescription("A graphical status light; the adjacent text states the condition.")

    @property
    def tone(self) -> IndicatorTone:
        return self._tone

    @property
    def color(self) -> QColor:
        return QColor(self._colors[self._tone])

    def set_tone(self, tone: IndicatorTone) -> None:
        if tone == self._tone:
            return
        self._tone = tone
        self.update()

    def paintEvent(self, event: object) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        painter.setPen(QPen(QColor("#273934"), 1.5))
        painter.setBrush(QColor("#465650"))
        painter.drawEllipse(bounds)

        lens = bounds.adjusted(4, 4, -4, -4)
        tone_color = self._colors[self._tone]
        painter.setPen(QPen(tone_color.darker(145), 1))
        painter.setBrush(tone_color)
        painter.drawEllipse(lens)

        highlight = QRectF(lens.left() + 4, lens.top() + 3, 7, 4)
        painter.setPen(QPen(QColor(255, 255, 255, 150), 0.5))
        painter.setBrush(QColor(255, 255, 255, 115))
        painter.drawEllipse(highlight)


class StatusChip(QWidget):
    _styles = {
        IndicatorTone.NORMAL: ("#eef1ef", "#c9d2ce", "#34423e"),
        IndicatorTone.GOOD: ("#e5f3eb", "#8fc3a3", "#205b42"),
        IndicatorTone.WARNING: ("#fff0d6", "#e0ad5c", "#754707"),
        IndicatorTone.ERROR: ("#fbe8e6", "#d58a83", "#812f28"),
        IndicatorTone.UNKNOWN: ("#eef0ef", "#c8cecb", "#59635f"),
    }

    def __init__(
        self,
        name: str,
        text: str = "Unknown",
        tone: IndicatorTone = IndicatorTone.UNKNOWN,
        parent: QWidget | None = None,
        *,
        show_name: bool = True,
    ) -> None:
        super().__init__(parent)
        self.name = name
        self.show_name = show_name
        self.setObjectName("statusChip")
        self.state_label = QLabel()
        self.state_label.setObjectName("statusChipText")
        self.state_label.setFont(QFont("Bahnschrift", 9, QFont.Weight.DemiBold))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 4, 9, 4)
        layout.setSpacing(0)
        layout.addWidget(self.state_label)
        self.setMinimumSize(92, 27)
        self.set_status(text, tone)

    @property
    def state_text(self) -> str:
        return self.state_label.text()

    @property
    def tone(self) -> IndicatorTone:
        return self._tone

    @property
    def background_color(self) -> str:
        return self._styles[self._tone][0]

    @property
    def border_color(self) -> str:
        return self._styles[self._tone][1]

    def set_status(
        self,
        text: str,
        tone: IndicatorTone,
        accessible_description: str | None = None,
    ) -> None:
        display_text = f"{self.name.upper()}: {text.upper()}" if self.show_name else text
        self._tone = tone
        self.state_label.setText(display_text)
        background, border, foreground = self._styles[tone]
        self.setStyleSheet(
            "QWidget#statusChip {"
            f"background-color: {background}; border: 1px solid {border}; "
            "border-radius: 7px;"
            "}"
            "QLabel#statusChipText {"
            f"background: transparent; border: none; color: {foreground}; "
            "font-weight: 700;"
            "}"
        )
        self.setAccessibleName(display_text)
        self.setAccessibleDescription(
            accessible_description or f"{self.name} state is {text}."
        )


class StatusIndicator(QWidget):
    def __init__(
        self,
        name: str,
        text: str = "Unknown",
        tone: IndicatorTone = IndicatorTone.UNKNOWN,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.name = name
        self.led = LedIndicator(tone)
        self.state_label = QLabel(text)
        self.state_label.setObjectName("indicatorStateText")
        self.state_label.setFont(QFont("Bahnschrift", 11, QFont.Weight.DemiBold))
        self.state_label.setMinimumWidth(52)
        self.state_label.setWordWrap(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        layout.addWidget(self.led, 0)
        layout.addWidget(self.state_label, 1)
        self.setMinimumHeight(34)
        self.set_status(text, tone)

    @property
    def state_text(self) -> str:
        return self.state_label.text()

    @property
    def tone(self) -> IndicatorTone:
        return self.led.tone

    def set_status(
        self,
        text: str,
        tone: IndicatorTone,
        accessible_description: str | None = None,
    ) -> None:
        self.state_label.setText(text)
        self.state_label.setStyleSheet(
            f"color: {self._text_color(tone)}; font-weight: 600;"
        )
        self.led.set_tone(tone)
        self.setAccessibleName(f"{self.name}: {text}")
        self.setAccessibleDescription(
            accessible_description or f"{self.name} status is {text}."
        )

    @staticmethod
    def _text_color(tone: IndicatorTone) -> str:
        return {
            IndicatorTone.NORMAL: "#52615d",
            IndicatorTone.GOOD: "#205f49",
            IndicatorTone.WARNING: "#8a4f0d",
            IndicatorTone.ERROR: "#9f2d27",
            IndicatorTone.UNKNOWN: "#596a66",
        }[tone]