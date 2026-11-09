"""Optional non-modal M15B offline analysis window; no control-layer imports."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import math
from pathlib import Path

from PySide6.QtCore import (
    QObject,
    QRectF,
    QRunnable,
    QSize,
    QSizeF,
    QThreadPool,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QImage,
    QPageSize,
    QPainter,
    QPainterPath,
    QPdfWriter,
    QPen,
)
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qpx_electrolyser.analysis.m15_results import (
    AnalysisReport,
    AxisSettings,
    CurveStyle,
    ExperimentSpec,
    FigureSettings,
    PlotConfiguration,
    analyse_m15_csv,
    exclusive_path,
    export_processed_csv,
    export_processing_metadata,
    load_plot_configuration,
    save_plot_configuration,
    validate_axis_settings,
)


class _TaskSignals(QObject):
    finished = Signal(str, object, object)


class _ImportTask(QRunnable):
    def __init__(self, key: str, spec: ExperimentSpec) -> None:
        super().__init__()
        self.key = key
        self.spec = spec
        self.signals = _TaskSignals()

    def run(self) -> None:
        try:
            result, error = analyse_m15_csv(self.spec), None
        except Exception as caught:
            result, error = None, caught
        self.signals.finished.emit(self.key, result, error)


def _line_style(value: str) -> Qt.PenStyle:
    return {
        "solid": Qt.PenStyle.SolidLine,
        "dash": Qt.PenStyle.DashLine,
        "dot": Qt.PenStyle.DotLine,
        "none": Qt.PenStyle.NoPen,
    }.get(value, Qt.PenStyle.SolidLine)


def _nice_tick(span: float) -> float:
    if not math.isfinite(span) or span <= 0:
        return 1.0
    raw = span / 5.0
    magnitude = 10 ** math.floor(math.log10(raw))
    fraction = raw / magnitude
    nice = 1.0 if fraction <= 1 else (2.0 if fraction <= 2 else (5.0 if fraction <= 5 else 10.0))
    return nice * magnitude


def _tick_values(minimum: float, maximum: float, interval: float) -> list[float]:
    start = math.ceil((minimum / interval) - 1e-12) * interval
    count = int(math.floor(((maximum - start) / interval) + 1e-9)) + 1
    if count < 0 or count > 201:
        raise ValueError("Axis major tick count is outside the supported range")
    return [start + index * interval for index in range(count)]


def choose_best_legend_position(
    normalized_points: list[tuple[float, float]],
) -> str:
    """Choose the corner containing the fewest plotted data points."""
    positions = ("upper_right", "upper_left", "lower_right", "lower_left")
    counts = {position: 0 for position in positions}
    for x, y in normalized_points:
        horizontal = "left" if x < 0.5 else "right"
        vertical = "upper" if y < 0.5 else "lower"
        counts[f"{vertical}_{horizontal}"] += 1
    return min(positions, key=lambda position: (counts[position], positions.index(position)))


class ResultsPlotCanvas(QWidget):
    """Qt-native scientific preview fed only by processed immutable results."""

    def __init__(self) -> None:
        super().__init__()
        self._reports: dict[str, AnalysisReport] = {}
        self._configuration = PlotConfiguration()
        self.setMinimumSize(640, 420)

    def set_data(
        self, reports: dict[str, AnalysisReport], configuration: PlotConfiguration
    ) -> None:
        self._reports = dict(reports)
        self._configuration = configuration
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            draw_scientific_plot(
                painter,
                QRectF(self.rect()),
                self._reports,
                self._configuration,
            )
        except Exception as error:
            painter.fillRect(self.rect(), QColor("white"))
            painter.setPen(QColor("#b42318"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                f"Offline preview unavailable: {error}",
            )
        finally:
            painter.end()


def draw_scientific_plot(
    painter: QPainter,
    outer: QRectF,
    reports: dict[str, AnalysisReport],
    configuration: PlotConfiguration,
) -> None:
    figure = configuration.figure
    painter.fillRect(outer, QColor("white"))
    outside_legend = figure.legend_visible and figure.legend_position == "outside_right"
    right_margin = 255 if outside_legend else 135
    area = QRectF(
        outer.left() + 90,
        outer.top() + 55,
        max(80, outer.width() - right_margin),
        max(80, outer.height() - 125),
    )
    curves: list[tuple[CurveStyle, list[tuple[float, float, float]]]] = []
    for key, report in reports.items():
        style = configuration.curves.get(key, CurveStyle())
        if not style.visible:
            continue
        points: list[tuple[float, float, float]] = []
        for result in report.accepted_points:
            x_value = (
                result.mean_current_density_a_cm2
                if figure.plot_mode == "density"
                else result.mean_current_a
            )
            if x_value is None or result.mean_voltage_v is None:
                continue
            points.append((
                float(x_value),
                float(result.mean_voltage_v),
                float(result.voltage_population_sd_v or Decimal(0)),
            ))
        curves.append((style, points))
    curves.sort(key=lambda item: item[0].display_order)
    all_points = [point for _style, points in curves for point in points]
    painter.setPen(QColor("#333333"))
    title_font = painter.font(); title_font.setBold(True); title_font.setPointSizeF(12)
    painter.setFont(title_font)
    painter.drawText(
        QRectF(outer.left(), outer.top() + 5, outer.width(), 28),
        Qt.AlignmentFlag.AlignCenter,
        figure.title,
    )
    normal = painter.font(); normal.setBold(False); normal.setPointSizeF(9)
    painter.setFont(normal)
    x_label = (
        figure.x_label_density if figure.plot_mode == "density"
        else figure.x_label_current
    )
    painter.drawText(
        QRectF(area.left(), area.bottom() + 38, area.width(), 24),
        Qt.AlignmentFlag.AlignCenter,
        x_label,
    )
    painter.save()
    painter.translate(24, area.center().y())
    painter.rotate(-90)
    painter.drawText(
        QRectF(-area.height() / 2, -20, area.height(), 24),
        Qt.AlignmentFlag.AlignCenter,
        figure.y_label,
    )
    painter.restore()
    if not all_points:
        painter.setPen(QPen(QColor("#777777"), 1))
        painter.drawRect(area)
        painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "No accepted 30-second points")
        return
    data_min_x, data_max_x = min(p[0] for p in all_points), max(p[0] for p in all_points)
    data_min_y = min(p[1] - p[2] for p in all_points)
    data_max_y = max(p[1] + p[2] for p in all_points)
    axis = figure.density_axis if figure.plot_mode == "density" else figure.current_axis
    validate_axis_settings(axis)
    if axis.mode == "manual":
        min_x, max_x = axis.x_min, axis.x_max
        min_y, max_y = axis.y_min, axis.y_max
        x_tick, y_tick = axis.x_major_tick, axis.y_major_tick
    else:
        x_pad = max((data_max_x - data_min_x) * 0.08, abs(data_max_x) * 0.02, 1e-12)
        y_pad = max((data_max_y - data_min_y) * 0.08, abs(data_max_y) * 0.02, 1e-12)
        min_x, max_x = data_min_x - x_pad, data_max_x + x_pad
        min_y, max_y = data_min_y - y_pad, data_max_y + y_pad
        x_tick, y_tick = _nice_tick(max_x - min_x), _nice_tick(max_y - min_y)
    x_ticks = _tick_values(min_x, max_x, x_tick)
    y_ticks = _tick_values(min_y, max_y, y_tick)

    def mapped(point: tuple[float, float, float]) -> tuple[float, float]:
        return (
            area.left() + area.width() * (point[0] - min_x) / (max_x - min_x),
            area.bottom() - area.height() * (point[1] - min_y) / (max_y - min_y),
        )

    if figure.grid_visible:
        painter.setPen(QPen(QColor("#dddddd"), 1, Qt.PenStyle.DotLine))
        for tick in x_ticks:
            x = mapped((tick, min_y, 0))[0]
            painter.drawLine(x, area.top(), x, area.bottom())
        for tick in y_ticks:
            y = mapped((min_x, tick, 0))[1]
            painter.drawLine(area.left(), y, area.right(), y)
    painter.setPen(QPen(QColor("#444444"), 1))
    painter.drawRect(area)
    for tick in x_ticks:
        x = mapped((tick, min_y, 0))[0]
        painter.drawText(QRectF(x - 42, area.bottom() + 3, 84, 18), Qt.AlignmentFlag.AlignHCenter, f"{tick:.6g}")
    for tick in y_ticks:
        y = mapped((min_x, tick, 0))[1]
        painter.drawText(QRectF(4, y - 9, area.left() - 10, 18), Qt.AlignmentFlag.AlignRight, f"{tick:.6g}")

    painter.save()
    painter.setClipRect(area)
    for style, points in curves:
        if not points:
            continue
        color = QColor(style.color)
        if not color.isValid():
            color = QColor("#1769aa")
        pen = QPen(color, max(0.1, style.line_width), _line_style(style.line_style))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        for index, point in enumerate(points):
            x, y = mapped(point)
            if index == 0 or style.line_style == "none": path.moveTo(x, y)
            else: path.lineTo(x, y)
        painter.drawPath(path)
        painter.setBrush(color)
        for point in points:
            x, y = mapped(point)
            size = max(2.0, style.marker_size)
            if figure.error_bars_visible and point[2] > 0:
                top = mapped((point[0], point[1] + point[2], 0))[1]
                bottom = mapped((point[0], point[1] - point[2], 0))[1]
                painter.drawLine(x, top, x, bottom)
                painter.drawLine(x - 4, top, x + 4, top)
                painter.drawLine(x - 4, bottom, x + 4, bottom)
            if style.marker == "none":
                continue
            if style.marker == "square":
                painter.drawRect(QRectF(x - size / 2, y - size / 2, size, size))
            elif style.marker == "triangle":
                marker = QPainterPath(); marker.moveTo(x, y - size / 2)
                marker.lineTo(x - size / 2, y + size / 2)
                marker.lineTo(x + size / 2, y + size / 2); marker.closeSubpath()
                painter.drawPath(marker)
            else:
                painter.drawEllipse(QRectF(x - size / 2, y - size / 2, size, size))
    painter.restore()

    entries = [(style, points) for style, points in curves if points]
    if figure.legend_visible and entries:
        normalized = [
            ((point[0] - min_x) / (max_x - min_x), (max_y - point[1]) / (max_y - min_y))
            for _style, points in entries for point in points
        ]
        position = figure.legend_position
        if position == "best":
            position = choose_best_legend_position(normalized)
        width = min(190.0, max(100.0, outer.width() * 0.28))
        row_height = max(14.0, min(20.0, (area.height() - 12) / len(entries)))
        height = row_height * len(entries) + 10
        margin = 8.0
        if position == "outside_right":
            x, y = area.right() + 12, area.top()
            width = max(8.0, outer.right() - x - 8)
        elif position.endswith("left"):
            x = area.left() + margin
            y = area.top() + margin if position.startswith("upper") else area.bottom() - height - margin
        else:
            x = area.right() - width - margin
            y = area.top() + margin if position.startswith("upper") else area.bottom() - height - margin
        legend = QRectF(x, max(outer.top() + 4, y), width, min(height, outer.height() - 8))
        painter.setPen(QPen(QColor("#888888"), 1))
        painter.setBrush(QColor(255, 255, 255, 235))
        painter.drawRect(legend)
        for index, (style, _points) in enumerate(entries):
            row_y = legend.top() + 5 + index * row_height
            color = QColor(style.color) if QColor(style.color).isValid() else QColor("#1769aa")
            painter.setPen(QPen(color, max(1.0, style.line_width), _line_style(style.line_style)))
            painter.drawLine(legend.left() + 8, row_y + row_height / 2, legend.left() + 30, row_y + row_height / 2)
            painter.setPen(QColor("#222222"))
            painter.drawText(
                QRectF(legend.left() + 35, row_y, legend.width() - 40, row_height),
                Qt.AlignmentFlag.AlignVCenter,
                style.legend_label or style.material_name,
            )


def export_plot_files(
    reports: dict[str, AnalysisReport], configuration: PlotConfiguration
) -> list[Path]:
    figure = configuration.figure
    directory = Path(figure.output_directory)
    width_px = max(200, round(figure.width_in * figure.dpi))
    height_px = max(160, round(figure.height_in * figure.dpi))
    outputs: list[Path] = []
    for format_name in figure.formats:
        suffix = f".{format_name.lower()}"
        while True:
            path = exclusive_path(directory, figure.output_filename, suffix)
            try:
                path.touch(exist_ok=False)
                break
            except FileExistsError:
                continue
        try:
            if format_name == "png":
                image = QImage(width_px, height_px, QImage.Format.Format_ARGB32)
                image.fill(QColor("white"))
                painter = QPainter(image)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                draw_scientific_plot(
                    painter, QRectF(0, 0, width_px, height_px), reports, configuration
                )
                painter.end()
                if not image.save(str(path), "PNG"):
                    raise OSError("Qt could not write PNG")
            elif format_name == "svg":
                generator = QSvgGenerator()
                generator.setFileName(str(path)); generator.setSize(QSize(width_px, height_px))
                generator.setViewBox(QRectF(0, 0, width_px, height_px).toRect())
                generator.setTitle(figure.title)
                painter = QPainter(generator)
                draw_scientific_plot(
                    painter, QRectF(0, 0, width_px, height_px), reports, configuration
                )
                painter.end()
            elif format_name == "pdf":
                writer = QPdfWriter(str(path)); writer.setResolution(figure.dpi)
                writer.setPageSize(QPageSize(
                    QSizeF(figure.width_in, figure.height_in),
                    QPageSize.Unit.Inch,
                    "M15B scientific figure",
                ))
                painter = QPainter(writer)
                draw_scientific_plot(
                    painter,
                    QRectF(painter.viewport()),
                    reports,
                    configuration,
                )
                painter.end()
            else:
                raise ValueError(f"Unsupported plot format: {format_name}")
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        outputs.append(path)
    return outputs


class ResultsPlotterWindow(QMainWindow):
    """Read-only, locally-contained offline analysis UI."""

    COLUMNS = (
        "Visible", "Material/sample", "Legend", "Colour", "Marker", "Size",
        "Line", "Width", "Order", "Area override (cm²)", "Source", "Status",
    )
    RESULT_COLUMNS = (
        "Stage status", "Source", "Run ID", "Channel", "Stage", "Type",
        "Commanded values", "Mean ± SD voltage (V)", "Mean ± SD current (A)",
        "Mean current density (A/cm²)", "Samples", "Final 30 s bounds", "Acceptance reason",
    )

    def __init__(self, default_directory: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("Results / Plotter — Offline analysis")
        self.resize(1250, 820)
        self._default_directory = Path(default_directory)
        self._reports: dict[str, AnalysisReport] = {}
        self._styles: dict[str, CurveStyle] = {}
        self._errors: dict[str, str] = {}
        self._tasks: set[_ImportTask] = set()
        self._pending_keys: set[str] = set()
        self._rerun_keys: set[str] = set()
        self._pool = QThreadPool(self); self._pool.setMaxThreadCount(2)
        self._updating_table = False
        self._updating_axes = False
        self._active_plot_mode = "current"
        self._axis_settings = {
            "current": AxisSettings(),
            "density": AxisSettings(),
        }
        root = QWidget(); layout = QVBoxLayout(root)
        banner = QLabel("Offline analysis — does not control the instrument")
        banner.setStyleSheet("font-weight: 800; padding: 8px; background: #e8f1fb;")
        layout.addWidget(banner)
        buttons = QHBoxLayout()
        self.import_button = QPushButton("Import M15 CSV...")
        self.remove_button = QPushButton("Remove selected")
        self.reset_button = QPushButton("Reset to scientific defaults")
        self.save_config_button = QPushButton("Save Plot Configuration")
        self.load_config_button = QPushButton("Load Plot Configuration")
        for button in (
            self.import_button, self.remove_button, self.reset_button,
            self.save_config_button, self.load_config_button,
        ): buttons.addWidget(button)
        layout.addLayout(buttons)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setMinimumHeight(190)
        layout.addWidget(self.table)
        result_tools = QHBoxLayout()
        result_tools.addWidget(QLabel("Results filter:"))
        self.results_filter = QLineEdit()
        self.results_filter.setPlaceholderText("Filter source, stage, status, channel…")
        self.include_partial_check = QCheckBox(
            "Include deliberately stopped stage with a complete valid 30-second window"
        )
        result_tools.addWidget(self.results_filter, 1)
        result_tools.addWidget(self.include_partial_check)
        layout.addLayout(result_tools)
        self.results_table = QTableWidget(0, len(self.RESULT_COLUMNS))
        self.results_table.setHorizontalHeaderLabels(self.RESULT_COLUMNS)
        self.results_table.setSortingEnabled(True)
        self.results_table.setMinimumHeight(190)
        layout.addWidget(self.results_table)
        self.canvas = ResultsPlotCanvas(); layout.addWidget(self.canvas, 1)
        controls = QGroupBox("Figure and exports"); grid = QGridLayout(controls)
        self.plot_mode = QComboBox(); self.plot_mode.addItem("Voltage vs current", "current")
        self.plot_mode.addItem("Voltage vs current density", "density")
        self.title_input = QLineEdit()
        self.x_label_input = QLineEdit(); self.y_label_input = QLineEdit()
        self.legend_check = QCheckBox("Legend"); self.grid_check = QCheckBox("Grid")
        self.legend_position = QComboBox()
        for label, value in (
            ("Best / Auto", "best"), ("Upper Left", "upper_left"),
            ("Upper Right", "upper_right"), ("Lower Left", "lower_left"),
            ("Lower Right", "lower_right"), ("Outside Right", "outside_right"),
        ):
            self.legend_position.addItem(label, value)
        self.error_check = QCheckBox("Voltage SD error bars")
        self.width_input = QDoubleSpinBox(); self.width_input.setRange(2, 30)
        self.height_input = QDoubleSpinBox(); self.height_input.setRange(2, 30)
        self.dpi_input = QSpinBox(); self.dpi_input.setRange(72, 1200)
        self.filename_input = QLineEdit("m15_results")
        self.directory_input = QLineEdit(str(self._default_directory))
        self.choose_directory = QPushButton("Choose...")
        self.png_check = QCheckBox("PNG"); self.svg_check = QCheckBox("SVG")
        self.pdf_check = QCheckBox("PDF")
        self.export_csv_button = QPushButton("Export processed CSV + metadata")
        self.export_plots_button = QPushButton("Export selected plots")
        entries = (
            ("Plot:", self.plot_mode), ("Title:", self.title_input),
            ("X label:", self.x_label_input), ("Y label:", self.y_label_input),
            ("Width (in):", self.width_input), ("Height (in):", self.height_input),
            ("PNG DPI:", self.dpi_input), ("Filename:", self.filename_input),
        )
        for row, (label, widget) in enumerate(entries):
            grid.addWidget(QLabel(label), row, 0); grid.addWidget(widget, row, 1)
        grid.addWidget(self.legend_check, 0, 2); grid.addWidget(self.grid_check, 1, 2)
        grid.addWidget(self.error_check, 2, 2)
        grid.addWidget(QLabel("Legend position:"), 3, 2)
        grid.addWidget(self.legend_position, 4, 2)
        grid.addWidget(QLabel("Output directory:"), 8, 0)
        grid.addWidget(self.directory_input, 8, 1); grid.addWidget(self.choose_directory, 8, 2)
        formats = QHBoxLayout()
        for item in (self.png_check, self.svg_check, self.pdf_check): formats.addWidget(item)
        grid.addWidget(QLabel("Formats:"), 9, 0); grid.addLayout(formats, 9, 1)
        exports = QHBoxLayout(); exports.addWidget(self.export_csv_button)
        exports.addWidget(self.export_plots_button); grid.addLayout(exports, 10, 1, 1, 2)
        layout.addWidget(controls)
        axis_box = QGroupBox("Axis controls for selected plot")
        axis_grid = QGridLayout(axis_box)
        self.axis_mode = QComboBox()
        self.axis_mode.addItem("Auto", "auto")
        self.axis_mode.addItem("Manual", "manual")
        self.x_min_input = QLineEdit("0")
        self.x_max_input = QLineEdit("1")
        self.y_min_input = QLineEdit("0")
        self.y_max_input = QLineEdit("1")
        self.x_tick_input = QLineEdit("0.2")
        self.y_tick_input = QLineEdit("0.2")
        self.auto_range_button = QPushButton("Auto Range")
        self.reset_axis_button = QPushButton("Reset Axis Defaults")
        for column, (label, widget) in enumerate((
            ("Mode:", self.axis_mode), ("X minimum:", self.x_min_input),
            ("X maximum:", self.x_max_input), ("Y minimum:", self.y_min_input),
            ("Y maximum:", self.y_max_input), ("X major tick:", self.x_tick_input),
            ("Y major tick:", self.y_tick_input),
        )):
            axis_grid.addWidget(QLabel(label), 0 if column < 4 else 2, (column % 4) * 2)
            axis_grid.addWidget(widget, 1 if column < 4 else 3, (column % 4) * 2)
        axis_grid.addWidget(self.auto_range_button, 4, 0, 1, 2)
        axis_grid.addWidget(self.reset_axis_button, 4, 2, 1, 2)
        layout.addWidget(axis_box)
        self.status_label = QLabel("Ready; no experiment-control connection is required.")
        self.status_label.setWordWrap(True); layout.addWidget(self.status_label)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(root)
        self.setCentralWidget(scroll)
        self.import_button.clicked.connect(self._choose_imports)
        self.remove_button.clicked.connect(self._remove_selected)
        self.reset_button.clicked.connect(self._reset_defaults)
        self.save_config_button.clicked.connect(self._save_configuration)
        self.load_config_button.clicked.connect(self._load_configuration)
        self.choose_directory.clicked.connect(self._choose_output_directory)
        self.export_csv_button.clicked.connect(self._export_csv)
        self.export_plots_button.clicked.connect(self._export_plots)
        self.table.cellChanged.connect(self._table_changed)
        self.results_filter.textChanged.connect(self._filter_results_table)
        self.include_partial_check.toggled.connect(self._reanalyze_all)
        self.plot_mode.currentIndexChanged.connect(self._plot_mode_changed)
        self.legend_position.currentIndexChanged.connect(self._refresh_preview)
        self.axis_mode.currentIndexChanged.connect(self._axis_controls_changed)
        for widget in (
            self.x_min_input, self.x_max_input, self.y_min_input,
            self.y_max_input, self.x_tick_input, self.y_tick_input,
        ):
            widget.textChanged.connect(self._axis_controls_changed)
        self.auto_range_button.clicked.connect(self._auto_range)
        self.reset_axis_button.clicked.connect(self._reset_axis_defaults)
        for widget in (
            self.title_input, self.x_label_input, self.y_label_input,
            self.filename_input, self.directory_input,
        ):
            widget.textChanged.connect(self._refresh_preview)
        for widget in (
            self.legend_check, self.grid_check, self.error_check,
            self.png_check, self.svg_check, self.pdf_check,
        ):
            widget.toggled.connect(self._refresh_preview)
        for widget in (self.width_input, self.height_input, self.dpi_input):
            widget.valueChanged.connect(self._refresh_preview)
        self._reset_defaults()

    @property
    def reports(self) -> dict[str, AnalysisReport]:
        return dict(self._reports)

    def import_paths(self, paths: list[Path]) -> None:
        for path in paths:
            key = str(Path(path).resolve())
            if key in self._styles:
                continue
            index = len(self._styles)
            self._styles[key] = CurveStyle(
                material_name=Path(path).stem,
                legend_label=Path(path).stem,
                color=("#1769aa", "#c62828", "#2e7d32", "#6a1b9a")[index % 4],
                display_order=index,
            )
            self._submit_analysis(key)
        self._rebuild_table()

    def _submit_analysis(self, key: str) -> None:
        if key in self._pending_keys:
            self._rerun_keys.add(key)
            return
        style = self._styles[key]
        try:
            override = style.active_area_override_cm2 or None
            spec = ExperimentSpec(
                Path(key), style.material_name, style.legend_label,
                None if override is None else Decimal(override),
                self.include_partial_check.isChecked(),
            )
        except Exception as error:
            self._show_local_error(f"Invalid active-area override: {error}")
            return
        task = _ImportTask(key, spec); self._tasks.add(task)
        self._pending_keys.add(key)
        task.signals.finished.connect(self._analysis_finished)
        self._pool.start(task)

    def _analysis_finished(self, key: str, report: object, error: object) -> None:
        self._tasks = {task for task in self._tasks if task.key != key}
        self._pending_keys.discard(key)
        if key not in self._styles:
            self._rerun_keys.discard(key)
            return
        if error is not None or not isinstance(report, AnalysisReport):
            self._reports.pop(key, None)
            self._errors[key] = str(error)
            self.status_label.setText(f"{Path(key).name}: invalid — {error}")
        else:
            self._errors.pop(key, None)
            self._reports[key] = report
            self.status_label.setText(f"{Path(key).name}: {report.validation_status}")
        self._rebuild_table(); self._rebuild_results_table(); self._refresh_preview()
        if key in self._rerun_keys:
            self._rerun_keys.discard(key)
            self._submit_analysis(key)

    def _choose_imports(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Import M15 raw CSV files", str(self._default_directory), "CSV files (*.csv)"
        )
        self.import_paths([Path(item) for item in files])

    def _rebuild_table(self) -> None:
        self._updating_table = True
        try:
            keys = sorted(self._styles, key=lambda key: self._styles[key].display_order)
            self.table.setRowCount(len(keys))
            for row, key in enumerate(keys):
                style = self._styles[key]
                values = (
                    "", style.material_name, style.legend_label, "", "", "", "", "", "",
                    style.active_area_override_cm2, Path(key).name,
                    self._reports[key].validation_status
                    if key in self._reports else self._errors.get(key, "Analysing"),
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, key)
                    if column in (0, 3, 4, 5, 6, 7, 8, 10, 11):
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(row, column, item)

                visible = QCheckBox()
                visible.setChecked(style.visible)
                visible.setToolTip("Show this experiment in the preview and exports")
                visible.toggled.connect(
                    lambda checked, selected=key: self._set_curve_style(
                        selected, "visible", checked
                    )
                )
                self.table.setCellWidget(row, 0, visible)

                colour = QPushButton(style.color)
                colour.setToolTip("Choose curve colour")
                colour.setStyleSheet(
                    f"QPushButton {{ background: {style.color}; color: "
                    f"{'#ffffff' if QColor(style.color).lightness() < 128 else '#000000'}; }}"
                )
                colour.clicked.connect(
                    lambda _checked=False, selected=key: self._choose_curve_color(selected)
                )
                self.table.setCellWidget(row, 3, colour)

                marker = QComboBox()
                for text, value in (
                    ("Circle", "circle"), ("Square", "square"),
                    ("Triangle", "triangle"), ("None", "none"),
                ):
                    marker.addItem(text, value)
                marker.setCurrentIndex(max(0, marker.findData(style.marker)))
                marker.currentIndexChanged.connect(
                    lambda _index, selected=key, control=marker: self._set_curve_style(
                        selected, "marker", str(control.currentData())
                    )
                )
                self.table.setCellWidget(row, 4, marker)

                marker_size = QDoubleSpinBox()
                marker_size.setRange(1.0, 40.0); marker_size.setDecimals(1)
                marker_size.setValue(style.marker_size)
                marker_size.valueChanged.connect(
                    lambda value, selected=key: self._set_curve_style(
                        selected, "marker_size", float(value)
                    )
                )
                self.table.setCellWidget(row, 5, marker_size)

                line = QComboBox()
                for text, value in (
                    ("Solid", "solid"), ("Dashed", "dash"),
                    ("Dotted", "dot"), ("None", "none"),
                ):
                    line.addItem(text, value)
                line.setCurrentIndex(max(0, line.findData(style.line_style)))
                line.currentIndexChanged.connect(
                    lambda _index, selected=key, control=line: self._set_curve_style(
                        selected, "line_style", str(control.currentData())
                    )
                )
                self.table.setCellWidget(row, 6, line)

                line_width = QDoubleSpinBox()
                line_width.setRange(0.1, 12.0); line_width.setDecimals(1)
                line_width.setValue(style.line_width)
                line_width.valueChanged.connect(
                    lambda value, selected=key: self._set_curve_style(
                        selected, "line_width", float(value)
                    )
                )
                self.table.setCellWidget(row, 7, line_width)

                order = QSpinBox()
                order.setRange(0, 999); order.setValue(style.display_order)
                order.valueChanged.connect(
                    lambda value, selected=key: self._set_curve_style(
                        selected, "display_order", int(value)
                    )
                )
                self.table.setCellWidget(row, 8, order)
            self.table.resizeColumnsToContents()
        finally:
            self._updating_table = False

    def _rebuild_results_table(self) -> None:
        self.results_table.setSortingEnabled(False)
        rows = [
            result
            for key in sorted(self._reports)
            for result in self._reports[key].results
        ]
        self.results_table.setRowCount(len(rows))
        for row, result in enumerate(rows):
            status = (
                "PARTIAL" if result.validation_status.startswith("PARTIAL")
                else ("ACCEPTED" if result.accepted else "EXCLUDED")
            )
            values = (
                status, result.source_file, result.run_id, f"CH{result.channel}",
                str(result.stage_index), result.stage_name, result.commanded_setpoints,
                self._mean_sd(result.mean_voltage_v, result.voltage_population_sd_v),
                self._mean_sd(result.mean_current_a, result.current_population_sd_a),
                "" if result.mean_current_density_a_cm2 is None else str(result.mean_current_density_a_cm2),
                str(result.sample_count),
                (
                    "" if result.analysis_start_elapsed_s is None
                    else f"{result.analysis_start_elapsed_s}–{result.analysis_end_elapsed_s} s"
                ),
                result.validation_status,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (4, 10):
                    item.setData(Qt.ItemDataRole.EditRole, int(value or 0))
                self.results_table.setItem(row, column, item)
        self.results_table.setSortingEnabled(True)
        self.results_table.resizeColumnsToContents()
        self._filter_results_table()

    @staticmethod
    def _mean_sd(mean_value: object, sd_value: object) -> str:
        return "" if mean_value is None else f"{mean_value} ± {sd_value}"

    def _filter_results_table(self, *_args) -> None:  # type: ignore[no-untyped-def]
        needle = self.results_filter.text().strip().lower()
        for row in range(self.results_table.rowCount()):
            haystack = " ".join(
                self.results_table.item(row, column).text()
                for column in range(self.results_table.columnCount())
                if self.results_table.item(row, column) is not None
            ).lower()
            self.results_table.setRowHidden(row, bool(needle and needle not in haystack))

    def _reanalyze_all(self, *_args) -> None:  # type: ignore[no-untyped-def]
        for key in tuple(self._styles):
            self._submit_analysis(key)

    def _set_curve_style(self, key: str, attribute: str, value: object) -> None:
        style = self._styles.get(key)
        if style is None:
            return
        setattr(style, attribute, value)
        self._refresh_preview()

    def _choose_curve_color(self, key: str) -> None:
        style = self._styles.get(key)
        if style is None:
            return
        selected = QColorDialog.getColor(QColor(style.color), self, "Select curve colour")
        if not selected.isValid():
            return
        style.color = selected.name()
        self._rebuild_table()
        self._refresh_preview()

    def _table_changed(self, row: int, _column: int) -> None:
        if self._updating_table:
            return
        item = self.table.item(row, 0)
        if item is None: return
        key = str(item.data(Qt.ItemDataRole.UserRole)); style = self._styles.get(key)
        if style is None: return
        old_material = style.material_name
        old_override = style.active_area_override_cm2
        try:
            style.material_name = self.table.item(row, 1).text().strip() or Path(key).stem
            style.legend_label = self.table.item(row, 2).text().strip() or style.material_name
            style.active_area_override_cm2 = self.table.item(row, 9).text().strip()
            if (
                old_override != style.active_area_override_cm2
                or old_material != style.material_name
            ):
                self._submit_analysis(key)
        except Exception as error:
            self._show_local_error(f"Curve setting rejected: {error}")
        self._refresh_preview()

    def _configuration(self) -> PlotConfiguration:
        self._capture_axis_controls(self._active_plot_mode)
        figure = FigureSettings(
            title=self.title_input.text(),
            x_label_current=(
                self.x_label_input.text() if self.plot_mode.currentData() == "current"
                else FigureSettings().x_label_current
            ),
            x_label_density=(
                self.x_label_input.text() if self.plot_mode.currentData() == "density"
                else FigureSettings().x_label_density
            ),
            y_label=self.y_label_input.text(), legend_visible=self.legend_check.isChecked(),
            legend_position=str(self.legend_position.currentData()),
            grid_visible=self.grid_check.isChecked(), error_bars_visible=self.error_check.isChecked(),
            width_in=self.width_input.value(), height_in=self.height_input.value(),
            dpi=self.dpi_input.value(), output_filename=self.filename_input.text(),
            output_directory=self.directory_input.text(),
            formats=[name for name, box in (("png", self.png_check), ("svg", self.svg_check), ("pdf", self.pdf_check)) if box.isChecked()],
            plot_mode=str(self.plot_mode.currentData()),
            current_axis=replace(self._axis_settings["current"]),
            density_axis=replace(self._axis_settings["density"]),
        )
        return PlotConfiguration(figure, dict(self._styles))

    def _apply_configuration(self, configuration: PlotConfiguration) -> None:
        figure = configuration.figure
        self._axis_settings = {
            "current": replace(figure.current_axis),
            "density": replace(figure.density_axis),
        }
        self.title_input.setText(figure.title)
        self._updating_axes = True
        try:
            self.plot_mode.setCurrentIndex(max(0, self.plot_mode.findData(figure.plot_mode)))
        finally:
            self._updating_axes = False
        self.x_label_input.setText(
            figure.x_label_density if figure.plot_mode == "density" else figure.x_label_current
        )
        self.y_label_input.setText(figure.y_label)
        self.legend_check.setChecked(figure.legend_visible); self.grid_check.setChecked(figure.grid_visible)
        self.legend_position.setCurrentIndex(
            max(0, self.legend_position.findData(figure.legend_position))
        )
        self.error_check.setChecked(figure.error_bars_visible)
        self.width_input.setValue(figure.width_in); self.height_input.setValue(figure.height_in)
        self.dpi_input.setValue(figure.dpi); self.filename_input.setText(figure.output_filename)
        self.directory_input.setText(figure.output_directory or str(self._default_directory))
        self.png_check.setChecked("png" in figure.formats); self.svg_check.setChecked("svg" in figure.formats)
        self.pdf_check.setChecked("pdf" in figure.formats)
        for key, style in configuration.curves.items():
            if key in self._styles: self._styles[key] = style
        self._active_plot_mode = str(self.plot_mode.currentData())
        self._load_axis_controls(self._active_plot_mode)
        self._rebuild_table(); self._rebuild_results_table(); self._refresh_preview()

    def _reset_defaults(self) -> None:
        defaults = FigureSettings(output_directory=str(self._default_directory))
        self._apply_configuration(PlotConfiguration(defaults, self._styles))

    def _plot_mode_changed(self, *_args) -> None:  # type: ignore[no-untyped-def]
        if self._updating_axes:
            return
        try:
            self._capture_axis_controls(self._active_plot_mode)
        except ValueError as error:
            self._show_local_error(f"Axis settings rejected: {error}")
        self._active_plot_mode = str(self.plot_mode.currentData())
        # Load the newly selected plot's independent axis state before changing
        # labels, whose textChanged signal also refreshes the preview.
        self._load_axis_controls(self._active_plot_mode)
        defaults = FigureSettings()
        self.x_label_input.setText(
            defaults.x_label_density
            if self.plot_mode.currentData() == "density"
            else defaults.x_label_current
        )
        self._refresh_preview()

    def _capture_axis_controls(self, mode: str) -> None:
        axis_mode = str(self.axis_mode.currentData())
        previous = self._axis_settings[mode]
        if axis_mode == "auto":
            self._axis_settings[mode] = replace(previous, mode="auto")
            return
        try:
            settings = AxisSettings(
                mode="manual",
                x_min=float(self.x_min_input.text()),
                x_max=float(self.x_max_input.text()),
                y_min=float(self.y_min_input.text()),
                y_max=float(self.y_max_input.text()),
                x_major_tick=float(self.x_tick_input.text()),
                y_major_tick=float(self.y_tick_input.text()),
            )
            validate_axis_settings(settings)
        except (TypeError, ValueError) as error:
            raise ValueError(str(error) or "Manual axis values must be numeric") from error
        self._axis_settings[mode] = settings

    def _load_axis_controls(self, mode: str) -> None:
        settings = self._axis_settings[mode]
        self._updating_axes = True
        try:
            self.axis_mode.setCurrentIndex(max(0, self.axis_mode.findData(settings.mode)))
            self.x_min_input.setText(f"{settings.x_min:.12g}")
            self.x_max_input.setText(f"{settings.x_max:.12g}")
            self.y_min_input.setText(f"{settings.y_min:.12g}")
            self.y_max_input.setText(f"{settings.y_max:.12g}")
            self.x_tick_input.setText(f"{settings.x_major_tick:.12g}")
            self.y_tick_input.setText(f"{settings.y_major_tick:.12g}")
            manual = settings.mode == "manual"
            for widget in (
                self.x_min_input, self.x_max_input, self.y_min_input,
                self.y_max_input, self.x_tick_input, self.y_tick_input,
            ):
                widget.setEnabled(manual)
        finally:
            self._updating_axes = False

    def _axis_controls_changed(self, *_args) -> None:  # type: ignore[no-untyped-def]
        if self._updating_axes:
            return
        manual = self.axis_mode.currentData() == "manual"
        for widget in (
            self.x_min_input, self.x_max_input, self.y_min_input,
            self.y_max_input, self.x_tick_input, self.y_tick_input,
        ):
            widget.setEnabled(manual)
        try:
            self._capture_axis_controls(self._active_plot_mode)
            self.status_label.setStyleSheet("")
            self.status_label.setText("Axis settings applied to the offline preview.")
            self._refresh_preview()
        except ValueError as error:
            self._show_local_error(f"Axis settings rejected: {error}")

    def _auto_range(self) -> None:
        self._axis_settings[self._active_plot_mode] = replace(
            self._axis_settings[self._active_plot_mode], mode="auto"
        )
        self._load_axis_controls(self._active_plot_mode)
        self._refresh_preview()

    def _reset_axis_defaults(self) -> None:
        self._axis_settings[self._active_plot_mode] = AxisSettings()
        self._load_axis_controls(self._active_plot_mode)
        self._refresh_preview()

    def _refresh_preview(self, *_args) -> None:  # type: ignore[no-untyped-def]
        try: self.canvas.set_data(self._reports, self._configuration())
        except Exception as error: self._show_local_error(f"Preview failed: {error}")

    def _remove_selected(self) -> None:
        rows = sorted({item.row() for item in self.table.selectedItems()}, reverse=True)
        for row in rows:
            item = self.table.item(row, 0)
            if item is None: continue
            key = str(item.data(Qt.ItemDataRole.UserRole))
            self._styles.pop(key, None); self._reports.pop(key, None)
            self._errors.pop(key, None)
            self._rerun_keys.discard(key)
        self._rebuild_table(); self._refresh_preview()

    def _choose_output_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select export directory", self.directory_input.text())
        if selected: self.directory_input.setText(selected)

    def _save_configuration(self) -> None:
        try:
            path = save_plot_configuration(
                self._configuration(), Path(self.directory_input.text()), self.filename_input.text()
            )
            self.status_label.setText(f"Saved plot configuration: {path}")
        except Exception as error: self._show_local_error(f"Configuration save failed: {error}")

    def _load_configuration(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Load plot configuration", self.directory_input.text(), "JSON (*.json)")
        if not selected: return
        try:
            self._apply_configuration(load_plot_configuration(Path(selected)))
            self.status_label.setText(f"Loaded plot configuration: {selected}")
        except Exception as error: self._show_local_error(f"Configuration load failed: {error}")

    def _export_csv(self) -> None:
        try:
            config = self._configuration(); directory = Path(config.figure.output_directory)
            csv_path = export_processed_csv(self._reports.values(), directory, config.figure.output_filename)
            metadata = export_processing_metadata(self._reports.values(), config, directory, config.figure.output_filename)
            self.status_label.setText(f"Exported {csv_path.name} and {metadata.name}")
        except Exception as error: self._show_local_error(f"Processed export failed: {error}")

    def _export_plots(self) -> None:
        try:
            configuration = self._configuration()
            if not configuration.figure.formats:
                raise ValueError("Select at least one plot format")
            paths = export_plot_files(self._reports, configuration)
            self.status_label.setText("Exported: " + ", ".join(path.name for path in paths))
        except Exception as error: self._show_local_error(f"Plot export failed: {error}")

    def _show_local_error(self, message: str) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #a61b1b;")

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._pool.clear(); self._pool.waitForDone()
        super().closeEvent(event)
