"""Focused M18B layout, templates, CSV naming and Plotter regressions."""

from __future__ import annotations

from decimal import Decimal
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QFileDialog
import pytest

from qpx_electrolyser.analysis.m15_results import (
    AnalysisError, AnalysisReport, AxisSettings, CurveStyle, FigureSettings,
    PlotConfiguration, StageResult, load_plot_configuration,
    save_plot_configuration, validate_axis_settings,
)
from qpx_electrolyser.data.csv_logging import CsvLogFile
from qpx_electrolyser.data.sequence_templates import (
    SequenceTemplate, load_sequence_template, save_sequence_template,
)
from qpx_electrolyser.engine.custom_sequence import SequenceStage, SequenceStageType
from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.gui.results_plotter import (
    ResultsPlotterWindow, choose_best_legend_position, draw_scientific_plot,
    export_plot_files,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_explicit_csv_path_is_exclusive_and_locked(tmp_path: Path) -> None:
    requested = tmp_path / "operator_selected.csv"
    writer = CsvLogFile(requested, 1)
    assert writer.path == requested
    writer.close()
    with pytest.raises(FileExistsError):
        CsvLogFile(requested, 1)


def test_csv_dialog_cancel_precedes_protocol_mutation(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = SimulatedPowerSupply()
    connection = ConnectionController(lambda: backend)
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    window._force_native_csv_dialog = True
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    connection.request_connect()
    deadline = __import__("time").monotonic() + 2
    while __import__("time").monotonic() < deadline:
        qapp.processEvents()
        if connection.snapshot.state is ConnectionState.CONNECTED:
            break
    try:
        before = window.channel_controllers[1].snapshot
        window._start_voltage_protocol(1, "1", "0.1", "2", "0.25")
        qapp.processEvents()
        assert not window.voltage_protocol_controllers[1].snapshot.owns_channel
        assert window.channel_controllers[1].snapshot.output_enabled is before.output_enabled is False
        assert not list(tmp_path.glob("*.csv"))
    finally:
        window.close(); connection.shutdown(2)


def test_sequence_template_round_trip_preserves_offline_fields(tmp_path: Path) -> None:
    stage = SequenceStage.create(
        SequenceStageType.CONSTANT_CURRENT,
        current="0.1", voltage="2", duration="30", interval="1",
        current_input_mode="current_density", active_area_cm2="4",
    )
    template = SequenceTemplate(
        "CP then CA", "100-ohm supervised template", "continuous", "1.0", (stage,)
    )
    path = tmp_path / "example.qpxseq.txt"
    save_sequence_template(path, template)
    assert load_sequence_template(path) == template


def _point(source: str, stage: int, current: str, voltage: str) -> StageResult:
    return StageResult(
        source, source, "run", 1, stage, "constant_voltage", "constant_voltage", "[]",
        Decimal("1"), "recorded", Decimal(voltage), Decimal("0"), Decimal(current),
        Decimal("0"), Decimal(current), 31, Decimal("0"), Decimal("30"), "", "",
        "Accepted",
    )


def test_two_curves_are_drawn_without_polygon_fill(qapp: QApplication) -> None:
    first = AnalysisReport(Path("a.csv"), (
        _point("a.csv", 1, "0", "0"), _point("a.csv", 2, "1", "2"),
        _point("a.csv", 3, "2", "0"),
    ), "accepted")
    second = AnalysisReport(Path("b.csv"), (
        _point("b.csv", 1, "0", "5"), _point("b.csv", 2, "1", "4"),
        _point("b.csv", 3, "2", "5"),
    ), "accepted")
    config = PlotConfiguration(
        FigureSettings(grid_visible=False, error_bars_visible=False),
        {
            "a": CurveStyle(color="#1769aa", marker="none"),
            "b": CurveStyle(color="#c62828", marker="none"),
        },
    )
    image = QImage(800, 600, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    draw_scientific_plot(
        painter, QRectF(0, 0, 800, 600), {"a": first, "b": second}, config
    )
    painter.end()
    # Inside the old implicitly-closed blue V-shaped polygon, away from its line.
    assert image.pixelColor(422, 433) == QColor("white")


def test_results_table_is_sortable_filterable_and_reports_stage_reason(
    qapp: QApplication, tmp_path: Path,
) -> None:
    report = AnalysisReport(Path("a.csv"), (_point("a.csv", 2, "0.1", "1"),), "1/1")
    window = ResultsPlotterWindow(tmp_path)
    try:
        window._reports = {"a": report}
        window._rebuild_results_table()
        assert window.results_table.isSortingEnabled()
        assert window.results_table.rowCount() == 1
        assert window.results_table.item(0, 0).text() == "ACCEPTED"
        assert window.results_table.item(0, 12).text() == "Accepted"
        window.results_filter.setText("CH1")
        assert not window.results_table.isRowHidden(0)
        window.results_filter.setText("not-present")
        assert window.results_table.isRowHidden(0)
    finally:
        window.close()


@pytest.mark.parametrize(
    "axis, message",
    (
        (AxisSettings("manual", float("nan"), 1, 0, 1, 0.1, 0.1), "finite"),
        (AxisSettings("manual", 1, 1, 0, 1, 0.1, 0.1), "X minimum"),
        (AxisSettings("manual", 0, 1, 2, 1, 0.1, 0.1), "Y minimum"),
        (AxisSettings("manual", 0, 1, 0, 1, 0, 0.1), "positive"),
        (AxisSettings("manual", 0, 1, 0, 1, 0.001, 0.1), "200-major-tick"),
    ),
)
def test_manual_axis_validation_rejects_unsafe_definitions(
    axis: AxisSettings, message: str,
) -> None:
    with pytest.raises(AnalysisError, match=message):
        validate_axis_settings(axis)
    validate_axis_settings(AxisSettings("auto", float("nan")))


def test_axis_and_legend_configuration_round_trip_is_plot_specific(tmp_path: Path) -> None:
    current_axis = AxisSettings("manual", -0.1, 0.5, 0.8, 2.2, 0.1, 0.2)
    density_axis = AxisSettings("manual", 0, 1.5, 1, 4, 0.25, 0.5)
    configuration = PlotConfiguration(
        FigureSettings(
            legend_position="outside_right",
            current_axis=current_axis,
            density_axis=density_axis,
        ),
        {},
    )
    saved = save_plot_configuration(configuration, tmp_path, "axis-settings")
    loaded = load_plot_configuration(saved)
    assert loaded == configuration
    assert loaded.figure.current_axis != loaded.figure.density_axis


def test_plotter_preserves_separate_axis_controls_and_rejects_invalid_local_input(
    qapp: QApplication, tmp_path: Path,
) -> None:
    window = ResultsPlotterWindow(tmp_path)
    try:
        window.axis_mode.setCurrentIndex(window.axis_mode.findData("manual"))
        for widget, value in (
            (window.x_min_input, "-0.1"), (window.x_max_input, "0.5"),
            (window.y_min_input, "0.8"), (window.y_max_input, "2.2"),
            (window.x_tick_input, "0.1"), (window.y_tick_input, "0.2"),
        ):
            widget.setText(value)
        current = window._configuration().figure.current_axis
        assert current == AxisSettings("manual", -0.1, 0.5, 0.8, 2.2, 0.1, 0.2)

        window.plot_mode.setCurrentIndex(window.plot_mode.findData("density"))
        assert window.axis_mode.currentData() == "auto"
        window.axis_mode.setCurrentIndex(window.axis_mode.findData("manual"))
        window.x_max_input.setText("4")
        density = window._configuration().figure.density_axis
        assert density.mode == "manual" and density.x_max == 4

        window.plot_mode.setCurrentIndex(window.plot_mode.findData("current"))
        assert window.x_min_input.text() == "-0.1"
        before = window._axis_settings["current"]
        window.x_max_input.setText("-0.2")
        qapp.processEvents()
        assert window._axis_settings["current"] == before
        assert "Axis settings rejected" in window.status_label.text()
        assert window.canvas._configuration.figure.current_axis == before
    finally:
        window.close()


def test_auto_range_reset_and_legend_controls_update_preview_immediately(
    qapp: QApplication, tmp_path: Path,
) -> None:
    window = ResultsPlotterWindow(tmp_path)
    try:
        window.axis_mode.setCurrentIndex(window.axis_mode.findData("manual"))
        window.x_max_input.setText("5")
        window.auto_range_button.click()
        assert window.canvas._configuration.figure.current_axis.mode == "auto"
        window.axis_mode.setCurrentIndex(window.axis_mode.findData("manual"))
        window.x_max_input.setText("8")
        window.reset_axis_button.click()
        assert window._axis_settings["current"] == AxisSettings()

        window.legend_position.setCurrentIndex(
            window.legend_position.findData("outside_right")
        )
        qapp.processEvents()
        assert window.canvas._configuration.figure.legend_position == "outside_right"
        window.legend_check.setChecked(False)
        assert not window._configuration().figure.legend_visible
        assert window._configuration().figure.legend_position == "outside_right"
    finally:
        window.close()


def test_best_legend_position_chooses_least_occupied_corner() -> None:
    assert choose_best_legend_position([]) == "upper_right"
    assert choose_best_legend_position([(0.8, 0.1), (0.9, 0.2)]) == "upper_left"
    assert choose_best_legend_position([(0.1, 0.1), (0.2, 0.2)]) == "upper_right"


def test_exports_use_visible_manual_axes_ticks_and_outside_legend(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "a"
    reports = {key: AnalysisReport(Path("a.csv"), (_point("a.csv", 1, "0.1", "1.2"),), "accepted")}
    axis = AxisSettings("manual", 0, 0.2, 1, 1.4, 0.05, 0.1)
    configuration = PlotConfiguration(
        FigureSettings(
            output_directory=str(tmp_path), output_filename="manual-axis",
            formats=["png", "svg", "pdf"], legend_position="outside_right",
            current_axis=axis, width_in=4, height_in=3,
        ),
        {key: CurveStyle(legend_label="Curve A")},
    )
    import qpx_electrolyser.gui.results_plotter as plotter_module
    original = plotter_module.draw_scientific_plot
    observed: list[tuple[AxisSettings, str]] = []

    def recording_draw(*args, **kwargs):  # type: ignore[no-untyped-def]
        config = args[3]
        observed.append((config.figure.current_axis, config.figure.legend_position))
        return original(*args, **kwargs)

    monkeypatch.setattr(plotter_module, "draw_scientific_plot", recording_draw)
    outputs = export_plot_files(reports, configuration)
    assert {path.suffix for path in outputs} == {".png", ".svg", ".pdf"}
    assert all(path.stat().st_size > 0 for path in outputs)
    assert observed == [(axis, "outside_right")] * 3

