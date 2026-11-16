"""Isolated offline viewer for existing QPX600DP recorded CSV files."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from qpx_electrolyser.data.recorded_data import (
    RecordedDataError, RecordedDataFormat, RecordedDataset, RecordedPlotType,
    RecordedPoint, load_recorded_csv,
)
from qpx_electrolyser.gui.plots.interaction import DisplayPoint, InteractivePlotCanvas
from qpx_electrolyser.gui.plots.realtime import PlotPoint, PlotSnapshot, _PlotCanvas


MAX_DISPLAY_POINTS = 5_000


def bounded_display_points(
    points: tuple[RecordedPoint, ...], maximum: int = MAX_DISPLAY_POINTS
) -> tuple[RecordedPoint, ...]:
    """Bound rendering cost while preserving the imported model and both endpoints."""
    if len(points) <= maximum:
        return points
    stride = math.ceil((len(points) - 1) / (maximum - 1))
    selected = list(points[::stride])
    if selected[-1] is not points[-1]:
        selected.append(points[-1])
    return tuple(selected[: maximum - 1] + [points[-1]]) if len(selected) > maximum else tuple(selected)


class _RecordedXYCanvas(InteractivePlotCanvas):
    """Existing interaction layer with recorded-data X/Y scientific rendering."""

    MAJOR_TICK_COUNT = 6

    def __init__(self) -> None:
        super().__init__()
        self._dataset: RecordedDataset | None = None
        self._plot_type = RecordedPlotType.VOLTAGE_CURRENT
        self._trace_style = "dots"
        self.setMinimumHeight(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    @property
    def trace_style(self) -> str:
        return self._trace_style

    def set_data(self, dataset: RecordedDataset, plot_type: RecordedPlotType) -> None:
        self._dataset = dataset
        self._plot_type = plot_type
        self.reset_zoom()

    def set_trace_style(self, style: str) -> None:
        if style not in {"dots", "line_dots"}:
            raise ValueError("Recorded plot style must be 'dots' or 'line_dots'")
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
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Recorded plot unavailable")
        finally:
            painter.end()

    def _draw(self, painter: QPainter) -> None:
        dark = self.window().property("uiTheme") == "dark"
        background = QColor("#20242b" if dark else "#ffffff")
        axis = QColor("#d1d6de" if dark else "#4a4a4a")
        grid = QColor(174, 183, 194, 55) if dark else QColor("#e1e5ea")
        painter.fillRect(self.rect(), background)
        area = QRectF(70, 24, max(20, self.width() - 100), max(20, self.height() - 78))
        dataset = self._dataset
        points = bounded_display_points(dataset.points) if dataset is not None else ()
        if self._plot_type is RecordedPlotType.VOLTAGE_CURRENT:
            values = tuple((point.current_a, point.voltage_v, point) for point in points)
            x_label, y_label = "Measured current (A)", "Measured voltage (V)"
        else:
            area_value = dataset.active_area_cm2 if dataset is not None else None
            values = tuple(
                (point.voltage_v, point.current_a / area_value, point)
                for point in points
            ) if area_value is not None else ()
            x_label, y_label = "Measured voltage (V)", "Current density (A/cm²)"
        x_values = [item[0] for item in values]
        y_values = [item[1] for item in values]
        full_x0, full_x1 = self._bounds(x_values)
        full_y0, full_y1 = self._bounds(y_values)
        x0, x1, y0, y1 = self.visible_bounds(full_x0, full_x1, full_y0, full_y1)

        divisions = self.MAJOR_TICK_COUNT - 1
        painter.setPen(QPen(grid, 1))
        for index in range(self.MAJOR_TICK_COUNT):
            fraction = index / divisions
            x = area.left() + fraction * area.width()
            y = area.bottom() - fraction * area.height()
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        painter.setPen(QPen(axis, 1)); painter.drawRect(area)
        for index in range(self.MAJOR_TICK_COUNT):
            fraction = index / divisions
            x = area.left() + fraction * area.width()
            y = area.bottom() - fraction * area.height()
            painter.drawText(
                QRectF(x - 38, area.bottom() + 3, 76, 18),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                f"{x0 + fraction * (x1 - x0):.3g}",
            )
            painter.drawText(
                QRectF(0, y - 9, 64, 18),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{y0 + fraction * (y1 - y0):.3g}",
            )
        painter.drawText(
            QRectF(area.left(), area.bottom() + 23, area.width(), 20),
            Qt.AlignmentFlag.AlignCenter, x_label,
        )
        painter.save(); painter.translate(18, area.center().y()); painter.rotate(-90)
        painter.drawText(QRectF(-area.height() / 2, -10, area.height(), 20), Qt.AlignmentFlag.AlignCenter, y_label)
        painter.restore()

        positions: list[tuple[QPointF, RecordedPoint]] = []
        path = QPainterPath()
        for index, (x_value, y_value, point) in enumerate(values):
            position = QPointF(
                area.left() + area.width() * (x_value - x0) / max(x1 - x0, 1e-12),
                area.bottom() - area.height() * (y_value - y0) / max(y1 - y0, 1e-12),
            )
            positions.append((position, point))
            path.moveTo(position) if index == 0 else path.lineTo(position)
        colour = QColor("#0f6cbd")
        if self._trace_style == "line_dots":
            painter.setPen(QPen(colour, 2)); painter.drawPath(path)
        painter.setPen(QPen(colour, 1)); painter.setBrush(QBrush(colour))
        for position, _point in positions:
            painter.drawEllipse(position, 3, 3)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        label = dataset.channel_label if dataset is not None else "Recorded data"
        self.set_interaction_geometry(area, [
            DisplayPoint(position, (
                f"{label}\nElapsed: {point.elapsed_s:.6g} s\n"
                f"Measured voltage: {point.voltage_v:.6g} V\n"
                f"Measured current: {point.current_a:.6g} A"
            ))
            for position, point in positions
        ])
        self.draw_interaction_overlay(painter)

    @staticmethod
    def _bounds(values: list[float]) -> tuple[float, float]:
        if not values:
            return 0.0, 1.0
        lower, upper = min(values), max(values)
        if math.isclose(lower, upper, abs_tol=1e-12):
            padding = max(abs(lower) * 0.05, 0.1)
            return max(0.0, lower - padding), upper + padding
        padding = (upper - lower) * 0.05
        return max(0.0, lower - padding), upper + padding


class _ImportSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str, str)
    finished = Signal(object)


class _ImportTask(QRunnable):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = Path(path)
        self.signals = _ImportSignals()

    def run(self) -> None:
        try:
            self.signals.succeeded.emit(load_recorded_csv(self.path))
        except (RecordedDataError, OSError) as error:
            self.signals.failed.emit(self.path.name, str(error))
        finally:
            self.signals.finished.emit(self)


class RecordedResultsPage(QWidget):
    """Offline-only recorded-data page; owns no control or instrument object."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("recordedResultsPage")
        self._datasets: list[RecordedDataset] = []
        self._tasks: set[_ImportTask] = set()
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)

        root = QVBoxLayout(self)
        heading = QLabel("Recorded Results")
        heading.setProperty("role", "pageTitle")
        root.addWidget(heading)
        notice = QLabel(
            "Offline recorded-data viewer — does not connect to or control the instrument. "
            "Imported files are read-only and remain separate from live measurements."
        )
        notice.setWordWrap(True); notice.setObjectName("recordedResultsIsolationNotice")
        root.addWidget(notice)
        toolbar = QHBoxLayout()
        self.import_button = QPushButton("Import Recorded Data...")
        self.import_button.setObjectName("importRecordedData")
        self.import_button.clicked.connect(self._choose_files)
        self.remove_button = QPushButton("Remove from viewer")
        self.remove_button.setObjectName("removeRecordedData")
        self.remove_button.clicked.connect(self._remove_selected)
        toolbar.addWidget(self.import_button); toolbar.addWidget(self.remove_button)
        toolbar.addStretch(1)
        toolbar.addWidget(QLabel("Plot:"))
        self.plot_type_combo = QComboBox(); self.plot_type_combo.setObjectName("recordedPlotType")
        self.plot_type_combo.currentIndexChanged.connect(self._plot_type_changed)
        toolbar.addWidget(self.plot_type_combo)
        toolbar.addWidget(QLabel("Style:"))
        self.style_combo = QComboBox(); self.style_combo.setObjectName("recordedPlotStyle")
        self.style_combo.addItem("Dots", "dots"); self.style_combo.addItem("Line + Dots", "line_dots")
        self.style_combo.currentIndexChanged.connect(self._style_changed)
        toolbar.addWidget(self.style_combo)
        self.auto_range_button = QPushButton("Auto Range")
        self.reset_zoom_button = QPushButton("Reset Zoom")
        self.auto_range_button.clicked.connect(self._reset_zoom)
        self.reset_zoom_button.clicked.connect(self._reset_zoom)
        toolbar.addWidget(self.auto_range_button); toolbar.addWidget(self.reset_zoom_button)
        root.addLayout(toolbar)

        body = QHBoxLayout()
        self.file_list = QListWidget(); self.file_list.setObjectName("recordedDataFiles")
        self.file_list.setMinimumWidth(260)
        self.file_list.currentRowChanged.connect(self._selection_changed)
        body.addWidget(self.file_list)
        right = QVBoxLayout()
        self.validation_status = QLabel("Import a QPX600DP Raw or Readable CSV to begin.")
        self.validation_status.setObjectName("recordedDataValidation")
        self.validation_status.setWordWrap(True)
        right.addWidget(self.validation_status)
        self.error_message = QLabel(""); self.error_message.setObjectName("recordedDataError")
        self.error_message.setWordWrap(True); self.error_message.setProperty("severity", "error")
        right.addWidget(self.error_message)
        self.plot_stack = QStackedWidget()
        self.time_canvas = _PlotCanvas(0)
        self.xy_canvas = _RecordedXYCanvas()
        self.plot_stack.addWidget(self.time_canvas); self.plot_stack.addWidget(self.xy_canvas)
        right.addWidget(self.plot_stack, 1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)
        self._update_controls()

    @property
    def datasets(self) -> tuple[RecordedDataset, ...]:
        return tuple(self._datasets)

    @property
    def current_dataset(self) -> RecordedDataset | None:
        row = self.file_list.currentRow()
        return self._datasets[row] if 0 <= row < len(self._datasets) else None

    def import_paths(self, paths: list[Path]) -> None:
        for path in paths:
            task = _ImportTask(Path(path))
            self._tasks.add(task)
            task.signals.succeeded.connect(self.add_dataset)
            task.signals.failed.connect(self._import_failed)
            task.signals.finished.connect(self._task_finished)
            self._thread_pool.start(task)
        if paths:
            self.validation_status.setText(f"Importing {len(paths)} recorded file(s)...")

    @Slot(object)
    def add_dataset(self, value: object) -> None:
        if not isinstance(value, RecordedDataset):
            self._import_failed("recorded file", "Importer returned an invalid data model.")
            return
        self._datasets.append(value)
        item = QListWidgetItem(value.source_path.name)
        item.setToolTip(str(value.source_path))
        self.file_list.addItem(item)
        self.file_list.setCurrentRow(len(self._datasets) - 1)
        self.error_message.clear()

    def shutdown(self) -> None:
        self._thread_pool.clear()
        self._thread_pool.waitForDone(1000)

    @Slot()
    def _choose_files(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(
            self, "Import recorded QPX600DP CSV data", "", "CSV files (*.csv)"
        )
        self.import_paths([Path(path) for path in selected])

    @Slot(int)
    def _selection_changed(self, _row: int) -> None:
        self._render_current()

    @Slot(int)
    def _plot_type_changed(self, _index: int) -> None:
        self._render_plot()

    @Slot(int)
    def _style_changed(self, _index: int) -> None:
        style = str(self.style_combo.currentData() or "dots")
        self.time_canvas.set_trace_style(style)
        self.xy_canvas.set_trace_style(style)

    @Slot()
    def _reset_zoom(self) -> None:
        self.time_canvas.reset_zoom(); self.xy_canvas.reset_zoom()

    @Slot()
    def _remove_selected(self) -> None:
        row = self.file_list.currentRow()
        if not 0 <= row < len(self._datasets):
            return
        self._datasets.pop(row); self.file_list.takeItem(row)
        self._render_current()

    @Slot(str, str)
    def _import_failed(self, filename: str, message: str) -> None:
        self.error_message.setText(f"{filename}: {message}")
        self.validation_status.setText("Recorded-data import failed locally; live control is unchanged.")

    @Slot(object)
    def _task_finished(self, task: object) -> None:
        if isinstance(task, _ImportTask):
            self._tasks.discard(task)

    def _render_current(self) -> None:
        dataset = self.current_dataset
        self.plot_type_combo.blockSignals(True); self.plot_type_combo.clear()
        if dataset is not None:
            self.plot_type_combo.addItem("Voltage and Current vs elapsed time", RecordedPlotType.TIME.value)
            self.plot_type_combo.addItem("Voltage vs Current", RecordedPlotType.VOLTAGE_CURRENT.value)
            if dataset.supports_current_density:
                self.plot_type_combo.addItem(
                    "Current density vs Voltage", RecordedPlotType.CURRENT_DENSITY_VOLTAGE.value
                )
            target = self.plot_type_combo.findData(dataset.default_plot_type.value)
            self.plot_type_combo.setCurrentIndex(max(0, target))
            protocols = ", ".join(dataset.protocols) or "not recorded"
            stages = ", ".join(map(str, dataset.stage_indexes)) or "not recorded"
            area = (
                f"{dataset.active_area_cm2:g} cm²" if dataset.active_area_cm2 is not None
                else "not recorded"
            )
            self.validation_status.setText(
                f"Validated {dataset.data_format.value.title()} CSV · {dataset.channel_label} · "
                f"{len(dataset.points)} samples · protocol: {protocols} · stages: {stages} · area: {area}"
            )
        else:
            self.validation_status.setText("Import a QPX600DP Raw or Readable CSV to begin.")
        self.plot_type_combo.blockSignals(False)
        self._update_controls(); self._render_plot()

    def _render_plot(self) -> None:
        dataset = self.current_dataset
        if dataset is None or self.plot_type_combo.currentData() is None:
            self.time_canvas.apply_snapshot(PlotSnapshot(channel=0))
            return
        plot_type = RecordedPlotType(str(self.plot_type_combo.currentData()))
        points = bounded_display_points(dataset.points)
        style = str(self.style_combo.currentData() or "dots")
        if plot_type is RecordedPlotType.TIME:
            label = dataset.channel_label if dataset.channel is not None else dataset.source_path.name
            self.time_canvas.set_channel_label(label)
            self.time_canvas.set_trace_style(style)
            self.time_canvas.apply_snapshot(PlotSnapshot(
                channel=dataset.channel or 0,
                points=tuple(PlotPoint(p.elapsed_s, p.voltage_v, p.current_a, 0) for p in points),
                connected=False,
                latest_voltage_v=points[-1].voltage_v,
                latest_current_a=points[-1].current_a,
                total_samples=len(dataset.points),
                message="Offline recorded data",
            ))
            self.plot_stack.setCurrentWidget(self.time_canvas)
        else:
            if plot_type is RecordedPlotType.CURRENT_DENSITY_VOLTAGE and not dataset.supports_current_density:
                self.error_message.setText("Current-density plotting requires valid Raw CSV active-area metadata.")
                return
            self.xy_canvas.set_trace_style(style)
            self.xy_canvas.set_data(dataset, plot_type)
            self.plot_stack.setCurrentWidget(self.xy_canvas)
        self.error_message.clear()

    def _update_controls(self) -> None:
        available = self.current_dataset is not None
        for widget in (
            self.remove_button, self.plot_type_combo, self.style_combo,
            self.auto_range_button, self.reset_zoom_button,
        ):
            widget.setEnabled(available)
