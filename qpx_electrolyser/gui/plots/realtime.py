"""Bounded Qt-native real-time plots fed by existing measurement signals."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from qpx_electrolyser.app.connection_controller import (
    ConnectionController,
    ConnectionSnapshot,
    ConnectionState,
)
from qpx_electrolyser.instrument.channel_readback import ChannelReadback
from qpx_electrolyser.gui.plots.interaction import DisplayPoint, InteractivePlotCanvas


@dataclass(frozen=True, slots=True)
class PlotPoint:
    elapsed_s: float
    voltage_v: float
    current_a: float
    session_id: int


class BoundedPlotSeries:
    """Retain a representative bounded display series and the newest point."""

    def __init__(self, max_points: int = 2_000) -> None:
        if type(max_points) is not int or max_points < 16:
            raise ValueError("max_points must be an integer of at least 16")
        self._max_points = max_points
        self._points: list[PlotPoint] = []
        self._stride = 1
        self._total_samples = 0

    @property
    def points(self) -> tuple[PlotPoint, ...]:
        return tuple(self._points)

    @property
    def total_samples(self) -> int:
        return self._total_samples

    @property
    def stride(self) -> int:
        return self._stride

    def clear(self) -> None:
        self._points.clear()
        self._stride = 1
        self._total_samples = 0

    def append(self, point: PlotPoint) -> None:
        if not all(math.isfinite(value) for value in (
            point.elapsed_s, point.voltage_v, point.current_a
        )):
            raise ValueError("plot values must be finite")
        if point.elapsed_s < 0 or point.voltage_v < 0 or point.current_a < 0:
            raise ValueError("plot values must be nonnegative")
        self._total_samples += 1
        if len(self._points) < self._max_points:
            self._points.append(point)
            return
        if self._total_samples % self._stride == 0:
            self._points.append(point)
        else:
            self._points[-1] = point
        if len(self._points) <= self._max_points:
            return
        first = self._points[0]
        latest = self._points[-1]
        interior = self._points[1:-1:2]
        self._points = [first, *interior, latest]
        self._stride *= 2


@dataclass(frozen=True, slots=True)
class PlotSnapshot:
    channel: int
    points: tuple[PlotPoint, ...] = ()
    connected: bool = False
    session_id: int = 0
    latest_voltage_v: float | None = None
    latest_current_a: float | None = None
    total_samples: int = 0
    display_stride: int = 1
    message: str = "Waiting for measurement data"
    error: bool = False


class ChannelPlotController(QObject):
    """Build a bounded display model without polling or backend access."""

    snapshot_changed = Signal(object)

    def __init__(
        self,
        channel: int,
        connection: ConnectionController,
        parent: QObject | None = None,
        *,
        max_points: int = 2_000,
        refresh_interval_ms: int = 100,
        monotonic=time.monotonic,
    ) -> None:
        super().__init__(parent)
        if channel not in (1, 2):
            raise ValueError("channel must be 1 or 2")
        if type(refresh_interval_ms) is not int or refresh_interval_ms < 0:
            raise ValueError("refresh_interval_ms must be a nonnegative integer")
        self._channel = channel
        self._connection = connection
        self._series = BoundedPlotSeries(max_points)
        self._monotonic = monotonic
        self._origin_s: float | None = None
        self._last_elapsed_s = 0.0
        connection_snapshot = connection.snapshot
        self._snapshot = PlotSnapshot(
            channel=channel,
            connected=connection_snapshot.state is ConnectionState.CONNECTED,
            session_id=connection_snapshot.session_id,
        )
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(refresh_interval_ms)
        self._refresh_timer.timeout.connect(self.flush)
        connection.snapshot_changed.connect(self.connection_changed)

    @property
    def snapshot(self) -> PlotSnapshot:
        return self._snapshot

    @Slot(object)
    def accept_sample(self, value: object) -> None:
        try:
            if not isinstance(value, ChannelReadback) or value.channel != self._channel:
                raise ValueError("Plot received malformed or wrong-channel data.")
            connection = self._connection.snapshot
            if connection.state is not ConnectionState.CONNECTED:
                return
            now = self._monotonic()
            if not math.isfinite(now):
                raise ValueError("Plot monotonic clock returned a non-finite value.")
            if self._origin_s is None:
                self._origin_s = now
            elapsed = max(self._last_elapsed_s, max(0.0, now - self._origin_s))
            self._last_elapsed_s = elapsed
            point = PlotPoint(
                elapsed_s=elapsed,
                voltage_v=value.measured_voltage_v,
                current_a=value.measured_current_a,
                session_id=connection.session_id,
            )
            self._series.append(point)
            self._snapshot = PlotSnapshot(
                channel=self._channel,
                points=self._series.points,
                connected=True,
                session_id=connection.session_id,
                latest_voltage_v=point.voltage_v,
                latest_current_a=point.current_a,
                total_samples=self._series.total_samples,
                display_stride=self._series.stride,
                message=(
                    f"Displaying {len(self._series.points)} of "
                    f"{self._series.total_samples} received samples"
                ),
            )
        except Exception as error:
            self._snapshot = replace(
                self._snapshot,
                message=f"Plot update failed: {error}",
                error=True,
            )
        self._schedule_refresh()

    @Slot(object)
    def connection_changed(self, snapshot: object) -> None:
        if not isinstance(snapshot, ConnectionSnapshot):
            return
        connected = snapshot.state is ConnectionState.CONNECTED
        message = self._snapshot.message
        if connected and snapshot.session_id != self._snapshot.session_id:
            message = "Connected; new session will be drawn as a separate segment"
        elif not connected:
            message = "Disconnected; displayed history retained"
        self._snapshot = replace(
            self._snapshot,
            connected=connected,
            session_id=snapshot.session_id,
            message=message,
            error=False,
        )
        self._schedule_refresh()

    @Slot()
    def reset(self) -> None:
        self._series.clear()
        self._origin_s = None
        self._last_elapsed_s = 0.0
        connection = self._connection.snapshot
        self._snapshot = PlotSnapshot(
            channel=self._channel,
            connected=connection.state is ConnectionState.CONNECTED,
            session_id=connection.session_id,
            message="Plot cleared; CSV logging is unchanged",
        )
        self._refresh_timer.stop()
        self.snapshot_changed.emit(self._snapshot)

    @Slot()
    def flush(self) -> None:
        self.snapshot_changed.emit(self._snapshot)

    def _schedule_refresh(self) -> None:
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()


class _PlotCanvas(InteractivePlotCanvas):
    MAJOR_TICK_COUNT = 6

    def __init__(self, channel: int) -> None:
        super().__init__()
        self._channel = channel
        self._snapshot = PlotSnapshot(channel=channel)
        self.last_major_tick_counts = (0, 0, 0)
        self.last_grid_style = ("light", "#e1e5ea", 255)
        self._trace_style = "dots"
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def apply_snapshot(self, snapshot: PlotSnapshot) -> None:
        self._snapshot = snapshot
        self.update()

    @property
    def trace_style(self) -> str:
        return self._trace_style

    def set_trace_style(self, style: str) -> None:
        if style not in {"dots", "line_dots"}:
            raise ValueError("CP plot style must be 'dots' or 'line_dots'")
        self._trace_style = style
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            self._draw(painter)
        except Exception:
            painter.fillRect(self.rect(), QColor("#ffffff"))
            painter.setPen(QColor("#b42318"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Plot rendering unavailable")
        finally:
            painter.end()

    def _draw(self, painter: QPainter) -> None:
        dark = self.window().property("uiTheme") == "dark"
        background = QColor("#20242b" if dark else "#ffffff")
        axis_colour = QColor("#d1d6de" if dark else "#4a4a4a")
        grid_colour = QColor(174, 183, 194, 55) if dark else QColor("#e1e5ea")
        self.last_grid_style = (
            "dark" if dark else "light", grid_colour.name(), grid_colour.alpha()
        )
        painter.fillRect(self.rect(), background)
        area = QRectF(62, 20, max(20, self.width() - 124), max(20, self.height() - 66))
        points = self._snapshot.points
        full_max_time = max((point.elapsed_s for point in points), default=1.0)
        full_max_time = max(1.0, full_max_time)
        max_voltage = max((point.voltage_v for point in points), default=1.0)
        max_current = max((point.current_a for point in points), default=1.0)
        max_voltage = max(1e-9, max_voltage * 1.1)
        max_current = max(1e-9, max_current * 1.1)
        min_time, max_time, min_voltage, max_voltage = self.visible_bounds(
            0.0, full_max_time, 0.0, max_voltage
        )
        _unused_x0, _unused_x1, min_current, max_current = self.visible_bounds(
            0.0, full_max_time, 0.0, max_current
        )

        # One sparse major grid serves time plus both independently scaled Y axes.
        painter.setPen(QPen(grid_colour, 1))
        divisions = self.MAJOR_TICK_COUNT - 1
        for index in range(self.MAJOR_TICK_COUNT):
            fraction = index / divisions
            x = area.left() + fraction * area.width()
            y = area.bottom() - fraction * area.height()
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        painter.setPen(QPen(axis_colour, 1))
        painter.drawRect(area)
        painter.setPen(axis_colour)
        for index in range(self.MAJOR_TICK_COUNT):
            fraction = index / divisions
            x = area.left() + fraction * area.width()
            y = area.bottom() - fraction * area.height()
            elapsed = min_time + fraction * (max_time - min_time)
            voltage = min_voltage + fraction * (max_voltage - min_voltage)
            current = min_current + fraction * (max_current - min_current)
            painter.drawText(
                QRectF(x - 36, area.bottom() + 2, 72, 17),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                self._format_tick(elapsed, "s"),
            )
            painter.drawText(
                QRectF(0, y - 9, 57, 18),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self._format_tick(voltage, "V"),
            )
            painter.drawText(
                QRectF(area.right() + 6, y - 9, 57, 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._format_tick(current, "A"),
            )
        self.last_major_tick_counts = (
            self.MAJOR_TICK_COUNT, self.MAJOR_TICK_COUNT, self.MAJOR_TICK_COUNT
        )
        painter.drawText(
            QRectF(area.left(), area.bottom() + 19, area.width(), 20),
            Qt.AlignmentFlag.AlignCenter,
            "Elapsed time (s)",
        )

        painter.save()
        painter.setClipRect(area)
        self._draw_trace(
            painter, area, points, min_time, max_time, min_voltage, max_voltage,
            "voltage", self._trace_style,
        )
        self._draw_trace(
            painter, area, points, min_time, max_time, min_current, max_current,
            "current", self._trace_style,
        )
        painter.restore()

        display_points: list[DisplayPoint] = []
        for point in points:
            x = area.left() + area.width() * (point.elapsed_s - min_time) / max(
                max_time - min_time, 1e-12
            )
            tooltip = (
                f"CH{self._channel}\nElapsed: {point.elapsed_s:.6g} s\n"
                f"Measured voltage: {point.voltage_v:.6g} V\n"
                f"Measured current: {point.current_a:.6g} A"
            )
            for value, lower, upper in (
                (point.voltage_v, min_voltage, max_voltage),
                (point.current_a, min_current, max_current),
            ):
                y = area.bottom() - area.height() * (value - lower) / max(
                    upper - lower, 1e-12
                )
                display_points.append(DisplayPoint(QPointF(x, y), tooltip))
        self.set_interaction_geometry(area, display_points)
        self.draw_interaction_overlay(painter)

        legend_y = 5.0
        painter.setPen(QPen(QColor("#0f6cbd"), 3))
        painter.drawLine(QPointF(area.left(), legend_y + 6), QPointF(area.left() + 18, legend_y + 6))
        painter.setPen(axis_colour)
        painter.drawText(QRectF(area.left() + 23, legend_y, 100, 18), "Voltage (V)")
        painter.setPen(QPen(QColor("#ca5010"), 3))
        painter.drawLine(QPointF(area.left() + 120, legend_y + 6), QPointF(area.left() + 138, legend_y + 6))
        painter.setPen(axis_colour)
        painter.drawText(QRectF(area.left() + 143, legend_y, 100, 18), "Current (A)")

    @staticmethod
    def _format_tick(value: float, unit: str) -> str:
        if abs(value) < 5e-13:
            value = 0.0
        return f"{value:.3g} {unit}"

    @staticmethod
    def _draw_trace(
        painter: QPainter,
        area: QRectF,
        points: tuple[PlotPoint, ...],
        min_time: float,
        max_time: float,
        min_value: float,
        max_value: float,
        field: str,
        style: str,
    ) -> None:
        colour = QColor("#0f6cbd" if field == "voltage" else "#ca5010")
        pen = QPen(colour, 2)
        if field == "current":
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        path = QPainterPath()
        prior_session: int | None = None
        positions: list[QPointF] = []
        for point in points:
            value = point.voltage_v if field == "voltage" else point.current_a
            x = area.left() + area.width() * (point.elapsed_s - min_time) / max(
                max_time - min_time, 1e-12
            )
            y = area.bottom() - area.height() * (value - min_value) / max(
                max_value - min_value, 1e-12
            )
            position = QPointF(x, y)
            positions.append(position)
            if prior_session != point.session_id:
                path.moveTo(position)
            else:
                path.lineTo(position)
            prior_session = point.session_id
        if style == "line_dots":
            painter.drawPath(path)
        painter.setPen(QPen(colour, 1))
        painter.setBrush(QBrush(colour))
        radius = 2.6
        for position in positions:
            painter.drawEllipse(position, radius, radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)


class ChannelPlotPanel(QGroupBox):
    """Per-channel voltage/current plot with status and a display-only reset."""

    clear_requested = Signal()

    def __init__(self, channel: int) -> None:
        super().__init__(f"Channel {channel} live measurements")
        self.channel = channel
        self.canvas = _PlotCanvas(channel)
        self.latest_voltage = QLabel("Voltage: unavailable")
        self.latest_current = QLabel("Current: unavailable")
        self.status = QLabel("Waiting for measurement data")
        self.status.setWordWrap(True)
        self.clear_button = QPushButton("Clear plot")
        self.clear_button.setObjectName(f"channel{channel}ClearPlot")
        self.clear_button.clicked.connect(self.clear_requested)
        self.reset_zoom_button = QPushButton("Auto Range / Reset Zoom")
        self.reset_zoom_button.clicked.connect(self.canvas.reset_zoom)
        self.plot_style_label = QLabel("Plot style")
        self.plot_style_combo = QComboBox()
        self.plot_style_combo.setObjectName(f"channel{channel}CpPlotStyle")
        self.plot_style_combo.addItem("Dots", "dots")
        self.plot_style_combo.addItem("Line + Dots", "line_dots")
        self.plot_style_combo.setToolTip(
            "Display-only CP curve style; sampling and CSV data are unchanged."
        )
        self.plot_style_combo.currentIndexChanged.connect(
            lambda _index: self.canvas.set_trace_style(
                str(self.plot_style_combo.currentData())
            )
        )

        latest = QHBoxLayout()
        latest.addWidget(self.latest_voltage)
        latest.addWidget(self.latest_current)
        latest.addStretch(1)
        latest.addWidget(self.plot_style_label)
        latest.addWidget(self.plot_style_combo)
        latest.addWidget(self.reset_zoom_button)
        latest.addWidget(self.clear_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.canvas, 1)
        layout.addLayout(latest)
        layout.addWidget(self.status)

    @Slot(object)
    def apply_snapshot(self, value: object) -> None:
        if not isinstance(value, PlotSnapshot) or value.channel != self.channel:
            return
        self.latest_voltage.setText(
            "Voltage: unavailable" if value.latest_voltage_v is None
            else f"Voltage: {value.latest_voltage_v:g} V"
        )
        self.latest_current.setText(
            "Current: unavailable" if value.latest_current_a is None
            else f"Current: {value.latest_current_a:g} A"
        )
        state = "connected" if value.connected else "disconnected"
        self.status.setText(f"CH{self.channel} | {state} | {value.message}")
        self.status.setStyleSheet("color: #a61b1b;" if value.error else "")
        self.canvas.apply_snapshot(value)

    def set_cp_plot_style(self, style: str) -> None:
        index = self.plot_style_combo.findData(style)
        self.plot_style_combo.setCurrentIndex(index if index >= 0 else 0)
        self.canvas.set_trace_style(str(self.plot_style_combo.currentData()))
