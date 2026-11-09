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
    AnalysisReport, CurveStyle, FigureSettings, PlotConfiguration, StageResult,
)
from qpx_electrolyser.data.csv_logging import CsvLogFile
from qpx_electrolyser.data.sequence_templates import (
    SequenceTemplate, load_sequence_template, save_sequence_template,
)
from qpx_electrolyser.engine.custom_sequence import SequenceStage, SequenceStageType
from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.gui.results_plotter import ResultsPlotterWindow, draw_scientific_plot


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

