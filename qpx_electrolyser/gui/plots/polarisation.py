"""Bounded mean-voltage/current-density plot fed by M14 summary points."""

from __future__ import annotations

from dataclasses import dataclass
import math

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGroupBox, QLabel, QPushButton, QVBoxLayout, QWidget

from qpx_electrolyser.engine.polarisation import PolarisationPoint
from qpx_electrolyser.gui.plots.interaction import DisplayPoint, InteractivePlotCanvas


@dataclass(frozen=True, slots=True)
class PolarisationPlotPoint:
    current_density_a_cm2: float
    mean_voltage_v: float
    step_index: int
    mean_current_a: float | None = None
    active_end_s: float | None = None


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
            point.commanded_current_density_a_cm2,
            point.mean_voltage_v,
            point.step_index,
            point.mean_current_a,
            point.active_end_s,
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


class _Canvas(InteractivePlotCanvas):
    def __init__(self, channel: int) -> None:
        super().__init__()
        self._channel = channel
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
        full_min_x, full_max_x = min(xs), max(xs)
        full_min_y, full_max_y = min(ys), max(ys)
        min_x, max_x, min_y, max_y = self.visible_bounds(
            full_min_x, full_max_x, full_min_y, full_max_y
        )
        span_x = max(max_x-min_x, 1e-12); span_y = max(max_y-min_y, 1e-12)
        path = QPainterPath()
        display_points: list[DisplayPoint] = []
        painter.save(); painter.setClipRect(area)
        for index, point in enumerate(self.points):
            position = QPointF(
                area.left() + area.width()*(point.current_density_a_cm2-min_x)/span_x,
                area.bottom() - area.height()*(point.mean_voltage_v-min_y)/span_y,
            )
            path.lineTo(position) if index else path.moveTo(position)
            elapsed = (
                "unavailable" if point.active_end_s is None
                else f"{point.active_end_s:.6g} s"
            )
            current = (
                "unavailable" if point.mean_current_a is None
                else f"{point.mean_current_a:.6g} A"
            )
            display_points.append(DisplayPoint(
                position,
                f"CH{self._channel} | Step {point.step_index}\n"
                f"Elapsed: {elapsed}\nMeasured voltage: {point.mean_voltage_v:.6g} V\n"
                f"Measured current: {current}",
            ))
        painter.setPen(QPen(QColor("#1769aa"), 2)); painter.drawPath(path)
        painter.restore()
        self.set_interaction_geometry(area, display_points)
        self.draw_interaction_overlay(painter)


class PolarisationPlotPanel(QGroupBox):
    clear_requested = Signal()

    def __init__(self, channel: int) -> None:
        super().__init__(f"Channel {channel} Polarisation: mean V vs j")
        self.channel = channel
        self.canvas = _Canvas(channel)
        self.status = QLabel("Waiting for completed polarisation steps")
        clear = QPushButton("Clear polarisation plot")
        clear.clicked.connect(self.clear_requested)
        self.reset_zoom_button = QPushButton("Auto Range / Reset Zoom")
        self.reset_zoom_button.clicked.connect(self.canvas.reset_zoom)
        layout = QVBoxLayout(self); layout.addWidget(self.canvas); layout.addWidget(self.status)
        layout.addWidget(self.reset_zoom_button); layout.addWidget(clear)

    @Slot(object)
    def apply_snapshot(self, snapshot: object) -> None:
        if not isinstance(snapshot, PolarisationPlotSnapshot) or snapshot.channel != self.channel:
            return
        self.canvas.set_points(snapshot.points)
        self.status.setText(
            f"{len(snapshot.points)} displayed / {snapshot.total_points} completed steps"
        )
