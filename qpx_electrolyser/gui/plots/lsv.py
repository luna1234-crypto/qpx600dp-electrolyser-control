"""Bounded I-V and optional j-V plots fed only by Staircase LSV results."""

from __future__ import annotations

from dataclasses import dataclass
import math

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGroupBox, QLabel, QPushButton, QVBoxLayout, QWidget

from qpx_electrolyser.engine.stepped_scan import StaircaseLsvPoint
from qpx_electrolyser.gui.plots.interaction import DisplayPoint, InteractivePlotCanvas


@dataclass(frozen=True, slots=True)
class LsvPlotPoint:
    voltage_v: float
    current_a: float
    current_density_a_cm2: float | None
    point_index: int


class BoundedLsvSeries:
    """Bound display memory while always retaining the newest representative point."""

    def __init__(self, max_points: int = 2_000) -> None:
        if type(max_points) is not int or max_points < 16:
            raise ValueError("max_points must be an integer of at least 16")
        self._max_points = max_points
        self._points: list[LsvPlotPoint] = []
        self._stride = 1
        self._total = 0

    @property
    def points(self) -> tuple[LsvPlotPoint, ...]:
        return tuple(self._points)

    @property
    def total_points(self) -> int:
        return self._total

    def clear(self) -> None:
        self._points.clear()
        self._stride = 1
        self._total = 0

    def append(self, point: LsvPlotPoint) -> None:
        values = [point.voltage_v, point.current_a]
        if point.current_density_a_cm2 is not None:
            values.append(point.current_density_a_cm2)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("LSV plot values must be finite")
        self._total += 1
        if len(self._points) < self._max_points:
            self._points.append(point)
            return
        if self._total % self._stride == 0:
            self._points.append(point)
        else:
            self._points[-1] = point
        if len(self._points) > self._max_points:
            self._points = [self._points[0], *self._points[1:-1:2], self._points[-1]]
            self._stride *= 2


@dataclass(frozen=True, slots=True)
class LsvPlotSnapshot:
    channel: int
    points: tuple[LsvPlotPoint, ...] = ()
    total_points: int = 0
    message: str = "Waiting for Staircase LSV point results"


class LsvPlotController(QObject):
    snapshot_changed = Signal(object)

    def __init__(self, channel: int, parent: QObject | None = None, *, max_points: int = 2_000) -> None:
        super().__init__(parent)
        self._channel = channel
        self._series = BoundedLsvSeries(max_points)
        self._snapshot = LsvPlotSnapshot(channel=channel)

    @property
    def snapshot(self) -> LsvPlotSnapshot:
        return self._snapshot

    @Slot(object)
    def accept_point(self, value: object) -> None:
        if not isinstance(value, StaircaseLsvPoint) or value.channel != self._channel:
            return
        if not value.valid or value.mean_voltage_v is None or value.mean_current_a is None:
            return
        self._series.append(LsvPlotPoint(
            voltage_v=value.mean_voltage_v,
            current_a=value.mean_current_a,
            current_density_a_cm2=value.mean_current_density_a_cm2,
            point_index=value.point_index,
        ))
        points = self._series.points
        self._snapshot = LsvPlotSnapshot(
            channel=self._channel,
            points=points,
            total_points=self._series.total_points,
            message=(
                f"I–V: {len(points)} displayed / {self._series.total_points} results; "
                + ("j–V available" if points[-1].current_density_a_cm2 is not None else "enter active area for j–V")
            ),
        )
        self.snapshot_changed.emit(self._snapshot)

    @Slot()
    def reset(self) -> None:
        self._series.clear()
        self._snapshot = LsvPlotSnapshot(channel=self._channel, message="LSV plot cleared; CSV unchanged")
        self.snapshot_changed.emit(self._snapshot)


class _LsvCanvas(InteractivePlotCanvas):
    def __init__(self, channel: int) -> None:
        super().__init__()
        self._channel = channel
        self._points: tuple[LsvPlotPoint, ...] = ()
        self.setMinimumHeight(150)

    def set_points(self, points: tuple[LsvPlotPoint, ...]) -> None:
        self._points = points
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        area = QRectF(52, 22, max(1, self.width() - 78), max(1, self.height() - 58))
        painter.setPen(QPen(QColor("#555555"), 1))
        painter.drawRect(area)
        painter.drawText(QRectF(area.left(), area.bottom() + 5, area.width(), 20), Qt.AlignmentFlag.AlignCenter, "Measured voltage (V)")
        painter.save()
        painter.translate(14, area.center().y())
        painter.rotate(-90)
        painter.drawText(QRectF(-area.height() / 2, -10, area.height(), 20), Qt.AlignmentFlag.AlignCenter, "Current (A) / density (A/cm²)")
        painter.restore()
        if not self._points:
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "No representative points")
            return
        full_min_v = min(point.voltage_v for point in self._points)
        full_max_v = max(point.voltage_v for point in self._points)
        full_max_y = max(
            max(point.current_a, point.current_density_a_cm2 or 0.0)
            for point in self._points
        ) or 1.0
        min_v, max_v, min_y, max_y = self.visible_bounds(
            full_min_v, full_max_v, 0.0, full_max_y
        )
        span_v = max(max_v - min_v, 1e-12)
        display_points: list[DisplayPoint] = []
        painter.save()
        painter.setClipRect(area)
        for field, color, dashed in (
            ("current_a", "#1769aa", False),
            ("current_density_a_cm2", "#c62828", True),
        ):
            path = QPainterPath()
            started = False
            for point in self._points:
                value = getattr(point, field)
                if value is None:
                    continue
                pos = QPointF(
                    area.left() + area.width() * (point.voltage_v - min_v) / span_v,
                    area.bottom() - area.height() * (value - min_y) / max(
                        max_y - min_y, 1e-12
                    ),
                )
                path.lineTo(pos) if started else path.moveTo(pos)
                started = True
                display_points.append(DisplayPoint(
                    pos,
                    f"CH{self._channel} | Step {point.point_index}\n"
                    "Elapsed: unavailable for completed-point data\n"
                    f"Measured voltage: {point.voltage_v:.6g} V\n"
                    f"Measured current: {point.current_a:.6g} A",
                ))
            pen = QPen(QColor(color), 2)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawPath(path)
        painter.restore()
        self.set_interaction_geometry(area, display_points)
        self.draw_interaction_overlay(painter)


class LsvPlotPanel(QGroupBox):
    clear_requested = Signal()

    def __init__(self, channel: int) -> None:
        super().__init__(f"Channel {channel} Staircase LSV I–V / j–V")
        self._channel = channel
        self.canvas = _LsvCanvas(channel)
        self.status = QLabel("Waiting for Staircase LSV point results")
        self.clear_button = QPushButton("Clear LSV plot")
        self.clear_button.clicked.connect(self.clear_requested)
        self.reset_zoom_button = QPushButton("Auto Range / Reset Zoom")
        self.reset_zoom_button.clicked.connect(self.canvas.reset_zoom)
        layout = QVBoxLayout(self)
        layout.addWidget(self.canvas)
        layout.addWidget(self.status)
        layout.addWidget(self.reset_zoom_button)
        layout.addWidget(self.clear_button)

    @Slot(object)
    def apply_snapshot(self, value: object) -> None:
        if not isinstance(value, LsvPlotSnapshot) or value.channel != self._channel:
            return
        self.canvas.set_points(value.points)
        self.status.setText(value.message)
