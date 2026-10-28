"""Bounded mean-voltage/current-density plot fed by M14 summary points."""

from __future__ import annotations

from dataclasses import dataclass
import math

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGroupBox, QLabel, QPushButton, QVBoxLayout, QWidget

from qpx_electrolyser.engine.polarisation import PolarisationPoint


@dataclass(frozen=True, slots=True)
class PolarisationPlotPoint:
    current_density_a_cm2: float
    mean_voltage_v: float
    step_index: int


class BoundedPolarisationSeries:
    def __init__(self, max_points: int = 2000) -> None:
        if type(max_points) is not int or max_points < 16:
            raise ValueError("max_points must be an integer of at least 16")
        self.max_points = max_points
        self.points: list[PolarisationPlotPoint] = []
        self.total = 0

    def append(self, point: PolarisationPlotPoint) -> None:
        if not all(math.isfinite(value) for value in (
            point.current_density_a_cm2, point.mean_voltage_v
        )):
            raise ValueError("polarisation plot values must be finite")
        self.total += 1
        self.points.append(point)
        if len(self.points) > self.max_points:
            self.points = [self.points[0], *self.points[2::2]]

    def clear(self) -> None:
        self.points.clear()
        self.total = 0


@dataclass(frozen=True, slots=True)
class PolarisationPlotSnapshot:
    channel: int
    points: tuple[PolarisationPlotPoint, ...] = ()
    total_points: int = 0


class PolarisationPlotController(QObject):
    snapshot_changed = Signal(object)

    def __init__(self, channel: int, parent=None, *, max_points: int = 2000) -> None:
        super().__init__(parent)
        self.channel = channel
        self.series = BoundedPolarisationSeries(max_points)
        self.snapshot = PolarisationPlotSnapshot(channel)

    @Slot(object)
    def accept_point(self, point: object) -> None:
        if not isinstance(point, PolarisationPoint) or point.channel != self.channel:
            return
        self.series.append(PolarisationPlotPoint(
            point.commanded_current_density_a_cm2, point.mean_voltage_v, point.step_index
        ))
        self.snapshot = PolarisationPlotSnapshot(
            self.channel, tuple(self.series.points), self.series.total
        )
        self.snapshot_changed.emit(self.snapshot)

    @Slot()
    def reset(self) -> None:
        self.series.clear()
        self.snapshot = PolarisationPlotSnapshot(self.channel)
        self.snapshot_changed.emit(self.snapshot)


class _Canvas(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.points: tuple[PolarisationPlotPoint, ...] = ()
        self.setMinimumHeight(150)

    def set_points(self, points: tuple[PolarisationPlotPoint, ...]) -> None:
        self.points = points
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        area = QRectF(52, 22, max(1, self.width() - 78), max(1, self.height() - 58))
        painter.setPen(QPen(QColor("#555555"), 1))
        painter.drawRect(area)
        painter.drawText(QRectF(area.left(), area.bottom() + 5, area.width(), 20), Qt.AlignmentFlag.AlignCenter, "Current density (A/cm²)")
        painter.save(); painter.translate(14, area.center().y()); painter.rotate(-90)
        painter.drawText(QRectF(-area.height()/2, -10, area.height(), 20), Qt.AlignmentFlag.AlignCenter, "Mean voltage (V)")
        painter.restore()
        if not self.points:
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "No completed steps")
            return
        xs = [p.current_density_a_cm2 for p in self.points]
        ys = [p.mean_voltage_v for p in self.points]
        min_x, max_x = min(xs), max(xs); min_y, max_y = min(ys), max(ys)
        span_x = max(max_x-min_x, 1e-12); span_y = max(max_y-min_y, 1e-12)
        path = QPainterPath()
        for index, point in enumerate(self.points):
            position = QPointF(
                area.left() + area.width()*(point.current_density_a_cm2-min_x)/span_x,
                area.bottom() - area.height()*(point.mean_voltage_v-min_y)/span_y,
            )
            path.lineTo(position) if index else path.moveTo(position)
        painter.setPen(QPen(QColor("#1769aa"), 2)); painter.drawPath(path)


class PolarisationPlotPanel(QGroupBox):
    clear_requested = Signal()

    def __init__(self, channel: int) -> None:
        super().__init__(f"Channel {channel} Polarisation: mean V vs j")
        self.channel = channel
        self.canvas = _Canvas()
        self.status = QLabel("Waiting for completed polarisation steps")
        clear = QPushButton("Clear polarisation plot")
        clear.clicked.connect(self.clear_requested)
        layout = QVBoxLayout(self); layout.addWidget(self.canvas); layout.addWidget(self.status); layout.addWidget(clear)

    @Slot(object)
    def apply_snapshot(self, snapshot: object) -> None:
        if not isinstance(snapshot, PolarisationPlotSnapshot) or snapshot.channel != self.channel:
            return
        self.canvas.set_points(snapshot.points)
        self.status.setText(
            f"{len(snapshot.points)} displayed / {snapshot.total_points} completed steps"
        )
