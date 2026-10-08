"""Bounded Qt-native real-time plots fed by existing measurement signals."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
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


class _PlotCanvas(QWidget):
    def __init__(self, channel: int) -> None:
        super().__init__()
        self._channel = channel
        self._snapshot = PlotSnapshot(channel=channel)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def apply_snapshot(self, snapshot: PlotSnapshot) -> None:
        self._snapshot = snapshot
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
        painter.fillRect(self.rect(), QColor("#ffffff"))
        area = QRectF(62, 20, max(20, self.width() - 124), max(20, self.height() - 66))
        painter.setPen(QPen(QColor("#777777"), 1))
        painter.drawRect(area)
        points = self._snapshot.points
        max_time = max((point.elapsed_s for point in points), default=1.0)
        max_time = max(1.0, max_time)
        max_voltage = max((point.voltage_v for point in points), default=1.0)
        max_current = max((point.current_a for point in points), default=1.0)
        max_voltage = max(1e-9, max_voltage * 1.1)
        max_current = max(1e-9, max_current * 1.1)

        painter.setPen(QColor("#444444"))
        painter.drawText(QRectF(area.left(), area.bottom() + 8, area.width(), 22),
                         Qt.AlignmentFlag.AlignCenter, "Elapsed time (s)")
        painter.drawText(QRectF(2, area.top(), 56, 22),
                         Qt.AlignmentFlag.AlignRight, f"{max_voltage:.3g} V")
        painter.drawText(QRectF(area.right() + 6, area.top(), 56, 22),
                         Qt.AlignmentFlag.AlignLeft, f"{max_current:.3g} A")
        painter.drawText(QRectF(2, area.bottom() - 18, 56, 22),
                         Qt.AlignmentFlag.AlignRight, "0 V")
        painter.drawText(QRectF(area.right() + 6, area.bottom() - 18, 56, 22),
                         Qt.AlignmentFlag.AlignLeft, "0 A")
        painter.drawText(QRectF(area.left(), area.bottom() - 2, 80, 20),
                         Qt.AlignmentFlag.AlignLeft, "0")
        painter.drawText(QRectF(area.right() - 80, area.bottom() - 2, 80, 20),
                         Qt.AlignmentFlag.AlignRight, f"{max_time:.3g}")

        self._draw_trace(painter, area, points, max_time, max_voltage, "voltage")
        self._draw_trace(painter, area, points, max_time, max_current, "current")

        legend_y = 5.0
        painter.setPen(QPen(QColor("#1769aa"), 3))
        painter.drawLine(QPointF(area.left(), legend_y + 6), QPointF(area.left() + 18, legend_y + 6))
        painter.setPen(QColor("#222222"))
        painter.drawText(QRectF(area.left() + 23, legend_y, 100, 18), "Voltage (V)")
        painter.setPen(QPen(QColor("#c62828"), 3))
        painter.drawLine(QPointF(area.left() + 120, legend_y + 6), QPointF(area.left() + 138, legend_y + 6))
        painter.setPen(QColor("#222222"))
        painter.drawText(QRectF(area.left() + 143, legend_y, 100, 18), "Current (A)")

    @staticmethod
    def _draw_trace(
        painter: QPainter,
        area: QRectF,
        points: tuple[PlotPoint, ...],
        max_time: float,
        max_value: float,
        field: str,
    ) -> None:
        pen = QPen(QColor("#1769aa" if field == "voltage" else "#c62828"), 2)
        if field == "current":
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        path = QPainterPath()
        prior_session: int | None = None
        for point in points:
            value = point.voltage_v if field == "voltage" else point.current_a
            x = area.left() + area.width() * point.elapsed_s / max_time
            y = area.bottom() - area.height() * value / max_value
            position = QPointF(x, y)
            if prior_session != point.session_id:
                path.moveTo(position)
            else:
                path.lineTo(position)
            prior_session = point.session_id
        painter.drawPath(path)


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

        latest = QHBoxLayout()
        latest.addWidget(self.latest_voltage)
        latest.addWidget(self.latest_current)
        latest.addStretch(1)
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
