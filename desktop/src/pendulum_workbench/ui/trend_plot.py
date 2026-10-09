from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class TrendPlot(QWidget):
    def __init__(self, title: str, unit: str, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._unit = unit
        self._color = QColor(color)
        self._samples: Sequence[tuple[float, float]] = ()
        self.setMinimumHeight(150)
        self.setAccessibleName(f"{title} live trend")

    def set_samples(self, samples: Sequence[tuple[float, float]]) -> None:
        self._samples = samples
        self.update()

    @staticmethod
    def map_elapsed_to_x(
        elapsed_s: float,
        start_s: float,
        end_s: float,
        left: float,
        width: float,
    ) -> float:
        if end_s <= start_s:
            return left + width / 2
        return left + (elapsed_s - start_s) / (end_s - start_s) * width

    def paintEvent(self, event: object) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))

        margin_left = 52.0
        margin_right = 16.0
        margin_top = 28.0
        margin_bottom = 27.0
        plot = QRectF(
            margin_left,
            margin_top,
            max(1.0, self.width() - margin_left - margin_right),
            max(1.0, self.height() - margin_top - margin_bottom),
        )

        painter.setPen(QColor("#23353a"))
        painter.setFont(QFont("Bahnschrift", 10, QFont.Weight.DemiBold))
        painter.drawText(14, 20, self._title)
        painter.setPen(QColor("#6d7a79"))
        painter.setFont(QFont("Bahnschrift", 8))
        painter.drawText(14, int(plot.center().y()), self._unit)

        grid_pen = QPen(QColor("#e7ece9"), 1)
        painter.setPen(grid_pen)
        for index in range(5):
            y = plot.top() + plot.height() * index / 4
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        for index in range(6):
            x = plot.left() + plot.width() * index / 5
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        painter.setPen(QColor("#74817e"))
        painter.setFont(QFont("Bahnschrift", 8))
        first_time = self._samples[0][0] if self._samples else 0.0
        painter.drawText(
            QPointF(plot.left(), self.height() - 7),
            f"{first_time:.1f} s" if self._samples else "time",
        )
        painter.drawText(
            QPointF(plot.right() - 36, self.height() - 7),
            f"{self._samples[-1][0]:.1f} s" if self._samples else "time",
        )
        if self._samples:
            values = [value for _, value in self._samples]
            painter.drawText(QPointF(8, plot.top() + 4), f"{max(values):.1f}")
            painter.drawText(QPointF(8, plot.bottom()), f"{min(values):.1f}")

        if not self._samples:
            painter.setPen(QColor("#84918e"))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "Waiting for mock samples")
            return

        values = [value for _, value in self._samples]
        low = min(values)
        high = max(values)
        span = high - low
        if span < 1e-9:
            span = max(1.0, abs(high) * 0.2)
            low -= span / 2
            high += span / 2
        else:
            padding = span * 0.12
            low -= padding
            high += padding

        path = QPainterPath()
        start_s = self._samples[0][0]
        end_s = self._samples[-1][0]
        for index, (elapsed_s, value) in enumerate(self._samples):
            x = self.map_elapsed_to_x(
                elapsed_s,
                start_s,
                end_s,
                plot.left(),
                plot.width(),
            )
            y = plot.bottom() - (value - low) / (high - low) * plot.height()
            point = QPointF(x, y)
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)

        painter.setPen(QPen(self._color, 2))
        painter.drawPath(path)