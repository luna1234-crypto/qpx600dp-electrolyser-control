"""Optional non-modal M15B offline analysis window; no control-layer imports."""

from __future__ import annotations

from decimal import Decimal
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
    area = QRectF(
        outer.left() + 90,
        outer.top() + 55,
        max(80, outer.width() - 135),
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
    min_x, max_x = min(p[0] for p in all_points), max(p[0] for p in all_points)
    min_y = min(p[1] - p[2] for p in all_points)
    max_y = max(p[1] + p[2] for p in all_points)
    x_pad = max((max_x - min_x) * 0.08, abs(max_x) * 0.02, 1e-12)
    y_pad = max((max_y - min_y) * 0.08, abs(max_y) * 0.02, 1e-12)
    min_x -= x_pad; max_x += x_pad; min_y -= y_pad; max_y += y_pad
    if figure.grid_visible:
        painter.setPen(QPen(QColor("#dddddd"), 1, Qt.PenStyle.DotLine))
        for index in range(1, 5):
            x = area.left() + area.width() * index / 5
            y = area.top() + area.height() * index / 5
            painter.drawLine(x, area.top(), x, area.bottom())
            painter.drawLine(area.left(), y, area.right(), y)
    painter.setPen(QPen(QColor("#444444"), 1))
    painter.drawRect(area)
    painter.drawText(area.left(), area.bottom() + 20, f"{min_x:.6g}")
    painter.drawText(area.right() - 80, area.bottom() + 20, f"{max_x:.6g}")
    painter.drawText(45, area.bottom(), f"{min_y:.6g}")
    painter.drawText(45, area.top() + 10, f"{max_y:.6g}")

    def mapped(point: tuple[float, float, float]) -> tuple[float, float]:
        return (
            area.left() + area.width() * (point[0] - min_x) / (max_x - min_x),
            area.bottom() - area.height() * (point[1] - min_y) / (max_y - min_y),
        )

    legend_y = area.top() + 6
    for style, points in curves:
        if not points:
            continue
        color = QColor(style.color)
        if not color.isValid():
            color = QColor("#1769aa")
        pen = QPen(color, max(0.1, style.line_width), _line_style(style.line_style))
        painter.setPen(pen)
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
        if figure.legend_visible:
            painter.setPen(QPen(color, 2))
            painter.drawLine(area.right() - 170, legend_y + 6, area.right() - 145, legend_y + 6)
            painter.setPen(QColor("#222222"))
            painter.drawText(
                QRectF(area.right() - 140, legend_y - 4, 135, 20),
                style.legend_label or style.material_name,
            )
            legend_y += 20


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
        self.canvas = ResultsPlotCanvas(); layout.addWidget(self.canvas, 1)
        controls = QGroupBox("Figure and exports"); grid = QGridLayout(controls)
        self.plot_mode = QComboBox(); self.plot_mode.addItem("Voltage vs current", "current")
        self.plot_mode.addItem("Voltage vs current density", "density")
        self.title_input = QLineEdit()
        self.x_label_input = QLineEdit(); self.y_label_input = QLineEdit()
        self.legend_check = QCheckBox("Legend"); self.grid_check = QCheckBox("Grid")
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
        grid.addWidget(QLabel("Output directory:"), 8, 0)
        grid.addWidget(self.directory_input, 8, 1); grid.addWidget(self.choose_directory, 8, 2)
        formats = QHBoxLayout()
        for item in (self.png_check, self.svg_check, self.pdf_check): formats.addWidget(item)
        grid.addWidget(QLabel("Formats:"), 9, 0); grid.addLayout(formats, 9, 1)
        exports = QHBoxLayout(); exports.addWidget(self.export_csv_button)
        exports.addWidget(self.export_plots_button); grid.addLayout(exports, 10, 1, 1, 2)
        layout.addWidget(controls)
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
        self.plot_mode.currentIndexChanged.connect(self._plot_mode_changed)
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
        self._rebuild_table(); self._refresh_preview()
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
            grid_visible=self.grid_check.isChecked(), error_bars_visible=self.error_check.isChecked(),
            width_in=self.width_input.value(), height_in=self.height_input.value(),
            dpi=self.dpi_input.value(), output_filename=self.filename_input.text(),
            output_directory=self.directory_input.text(),
            formats=[name for name, box in (("png", self.png_check), ("svg", self.svg_check), ("pdf", self.pdf_check)) if box.isChecked()],
            plot_mode=str(self.plot_mode.currentData()),
        )
        return PlotConfiguration(figure, dict(self._styles))

    def _apply_configuration(self, configuration: PlotConfiguration) -> None:
        figure = configuration.figure
        self.title_input.setText(figure.title)
        self.plot_mode.setCurrentIndex(max(0, self.plot_mode.findData(figure.plot_mode)))
        self.x_label_input.setText(
            figure.x_label_density if figure.plot_mode == "density" else figure.x_label_current
        )
        self.y_label_input.setText(figure.y_label)
        self.legend_check.setChecked(figure.legend_visible); self.grid_check.setChecked(figure.grid_visible)
        self.error_check.setChecked(figure.error_bars_visible)
        self.width_input.setValue(figure.width_in); self.height_input.setValue(figure.height_in)
        self.dpi_input.setValue(figure.dpi); self.filename_input.setText(figure.output_filename)
        self.directory_input.setText(figure.output_directory or str(self._default_directory))
        self.png_check.setChecked("png" in figure.formats); self.svg_check.setChecked("svg" in figure.formats)
        self.pdf_check.setChecked("pdf" in figure.formats)
        for key, style in configuration.curves.items():
            if key in self._styles: self._styles[key] = style
        self._rebuild_table(); self._refresh_preview()

    def _reset_defaults(self) -> None:
        defaults = FigureSettings(output_directory=str(self._default_directory))
        self._apply_configuration(PlotConfiguration(defaults, self._styles))

    def _plot_mode_changed(self, *_args) -> None:  # type: ignore[no-untyped-def]
        defaults = FigureSettings()
        self.x_label_input.setText(
            defaults.x_label_density
            if self.plot_mode.currentData() == "density"
            else defaults.x_label_current
        )
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
