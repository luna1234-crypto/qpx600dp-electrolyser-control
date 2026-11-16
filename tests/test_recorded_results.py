"""Offline recorded-data parsing, rendering and isolation regressions."""

from __future__ import annotations

import csv
import inspect
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
import pytest

from qpx_electrolyser.data.csv_logging import CSV_COLUMNS, READABLE_CSV_COLUMNS
from qpx_electrolyser.data.recorded_data import (
    RecordedDataError, RecordedDataFormat, RecordedPlotType, load_recorded_csv,
)
from qpx_electrolyser.gui.recorded_results import RecordedResultsPage
import qpx_electrolyser.gui.recorded_results as recorded_results_module


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _raw_row(
    *, channel: int = 1, elapsed: float = 0.0, voltage: float = 1.0,
    current: float = 0.01, protocol: str = "constant_current", stage: int | None = 1,
    area: float | None = 2.0,
) -> dict[str, str]:
    row = {name: "" for name in CSV_COLUMNS}
    row.update({
        "timestamp_iso": "2026-11-16T10:00:00+00:00",
        "elapsed_seconds": str(elapsed),
        "channel": str(channel),
        "measured_voltage_v": str(voltage),
        "measured_current_a": str(current),
        "sample_status": "ok",
        "protocol_name": protocol,
        "protocol_state": "RUNNING",
        "active_area_cm2": "" if area is None else str(area),
        "sequence_stage_index": "" if stage is None else str(stage),
    })
    return row


def _write_raw(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader(); writer.writerows(rows)


def _write_readable(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(READABLE_CSV_COLUMNS)
        writer.writerow((0, 1.0, 0.01)); writer.writerow((1, 1.2, 0.012))


def _mouse(
    event_type: QMouseEvent.Type, position: QPointF,
    button: Qt.MouseButton, buttons: Qt.MouseButton,
) -> QMouseEvent:
    return QMouseEvent(
        event_type, position, position, position, button, buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def test_raw_and_readable_detection_with_protocol_channel_stage_and_area(tmp_path: Path) -> None:
    raw = tmp_path / "ch2_raw.csv"
    _write_raw(raw, [
        _raw_row(channel=2, elapsed=0, protocol="voltage_scan", stage=1, area=2.5),
        _raw_row(channel=2, elapsed=1, voltage=1.5, current=0.02,
                 protocol="voltage_scan", stage=2, area=2.5),
    ])
    dataset = load_recorded_csv(raw)
    assert dataset.data_format is RecordedDataFormat.RAW
    assert dataset.channel == 2
    assert dataset.protocols == ("voltage_scan",)
    assert dataset.stage_indexes == (1, 2)
    assert dataset.active_area_cm2 == 2.5
    assert dataset.default_plot_type is RecordedPlotType.VOLTAGE_CURRENT

    readable = tmp_path / "run_readable.csv"; _write_readable(readable)
    plain = load_recorded_csv(readable)
    assert plain.data_format is RecordedDataFormat.READABLE
    assert plain.channel is None and not plain.protocols and not plain.stage_indexes
    assert plain.active_area_cm2 is None
    assert plain.default_plot_type is RecordedPlotType.TIME


def test_custom_sequence_and_polarisation_default_plot_selection(tmp_path: Path) -> None:
    sequence = tmp_path / "sequence.csv"
    rows = [
        _raw_row(elapsed=0, protocol="constant_voltage", stage=1),
        _raw_row(elapsed=1, protocol="constant_current", stage=2),
    ]
    rows[0]["sequence_run_id"] = rows[1]["sequence_run_id"] = "run-1"
    _write_raw(sequence, rows)
    assert load_recorded_csv(sequence).default_plot_type is RecordedPlotType.TIME

    polarisation = tmp_path / "polarisation.csv"
    _write_raw(polarisation, [
        _raw_row(protocol="polarisation", area=1.5),
        _raw_row(elapsed=1, voltage=2, current=0.02, protocol="polarisation", area=1.5),
    ])
    assert load_recorded_csv(polarisation).default_plot_type is RecordedPlotType.CURRENT_DENSITY_VOLTAGE


@pytest.mark.parametrize("kind", ["bad-number", "mixed-channel", "conflicting-area", "wrong-header"])
def test_malformed_and_ambiguous_files_are_rejected(tmp_path: Path, kind: str) -> None:
    path = tmp_path / f"{kind}.csv"
    if kind == "wrong-header":
        path.write_text("time,voltage,current\n0,1,2\n", encoding="utf-8")
    else:
        rows = [_raw_row(), _raw_row(elapsed=1)]
        if kind == "bad-number":
            rows[1]["measured_voltage_v"] = "not-a-number"
        elif kind == "mixed-channel":
            rows[1]["channel"] = "2"
        else:
            rows[1]["active_area_cm2"] = "3"
        _write_raw(path, rows)
    with pytest.raises(RecordedDataError):
        load_recorded_csv(path)


def test_page_renders_time_xy_density_and_switches_style_and_interactions(
    qapp: QApplication, tmp_path: Path,
) -> None:
    path = tmp_path / "ch1_polarisation_raw.csv"
    _write_raw(path, [
        _raw_row(elapsed=index, voltage=1 + index * 0.2, current=0.01 + index * 0.002,
                 protocol="polarisation", area=2.0)
        for index in range(4)
    ])
    page = RecordedResultsPage(); page.resize(1000, 700); page.show()
    try:
        page.add_dataset(load_recorded_csv(path)); qapp.processEvents()
        assert page.plot_type_combo.currentData() == RecordedPlotType.CURRENT_DENSITY_VOLTAGE.value
        assert page.plot_stack.currentWidget() is page.xy_canvas
        assert page.xy_canvas._display_points
        page.style_combo.setCurrentIndex(1); qapp.processEvents()
        assert page.xy_canvas.trace_style == "line_dots"
        point = page.xy_canvas._display_points[0].position
        page.xy_canvas.mousePressEvent(_mouse(
            QMouseEvent.Type.MouseButtonPress, point,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        ))
        assert "Elapsed:" in page.xy_canvas.last_tooltip_text
        area = page.xy_canvas._plot_area
        start = QPointF(area.left() + 10, area.top() + 10)
        end = QPointF(area.right() - 10, area.bottom() - 10)
        page.xy_canvas.mousePressEvent(_mouse(
            QMouseEvent.Type.MouseButtonPress, start,
            Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
        ))
        page.xy_canvas.mouseReleaseEvent(_mouse(
            QMouseEvent.Type.MouseButtonRelease, end,
            Qt.MouseButton.RightButton, Qt.MouseButton.NoButton,
        ))
        assert page.xy_canvas.zoom_view != (0.0, 1.0, 0.0, 1.0)
        page.reset_zoom_button.click()
        assert page.xy_canvas.zoom_view == (0.0, 1.0, 0.0, 1.0)

        page.plot_type_combo.setCurrentIndex(
            page.plot_type_combo.findData(RecordedPlotType.TIME.value)
        ); qapp.processEvents()
        assert page.plot_stack.currentWidget() is page.time_canvas
        assert page.time_canvas._display_points
        page.auto_range_button.click()
        assert page.time_canvas.zoom_view == (0.0, 1.0, 0.0, 1.0)
    finally:
        page.shutdown(); page.close()


def test_readable_omits_density_and_ch1_ch2_imports_remain_independent(
    qapp: QApplication, tmp_path: Path,
) -> None:
    readable = tmp_path / "readable.csv"; _write_readable(readable)
    ch1 = tmp_path / "ch1.csv"; ch2 = tmp_path / "ch2.csv"
    _write_raw(ch1, [_raw_row(channel=1)])
    _write_raw(ch2, [_raw_row(channel=2, voltage=2)])
    page = RecordedResultsPage()
    try:
        page.add_dataset(load_recorded_csv(ch1)); page.add_dataset(load_recorded_csv(ch2))
        page.add_dataset(load_recorded_csv(readable)); qapp.processEvents()
        assert [item.channel for item in page.datasets] == [1, 2, None]
        assert page.plot_type_combo.findData(RecordedPlotType.CURRENT_DENSITY_VOLTAGE.value) == -1
        page.file_list.setCurrentRow(0); qapp.processEvents()
        assert page.current_dataset is not None and page.current_dataset.channel == 1
        page.file_list.setCurrentRow(1); qapp.processEvents()
        assert page.current_dataset is not None and page.current_dataset.channel == 2
    finally:
        page.shutdown()


def test_background_import_errors_are_local_and_no_control_dependency_exists(
    qapp: QApplication, tmp_path: Path,
) -> None:
    good = tmp_path / "good.csv"; _write_readable(good)
    bad = tmp_path / "bad.csv"; bad.write_text("wrong,header\n1,2\n", encoding="utf-8")
    page = RecordedResultsPage(); page.import_paths([good, bad])
    deadline = time.monotonic() + 3
    while page._tasks and time.monotonic() < deadline:
        qapp.processEvents(); time.sleep(0.002)
    try:
        assert len(page.datasets) == 1
        assert "Unsupported CSV header" in page.error_message.text()
        assert not hasattr(page, "connection_controller")
        source = inspect.getsource(recorded_results_module)
        for forbidden in (
            "SerialTransport", "QPX600DPDriver", "submit_command(",
            "request_output_on", "request_output_off", "hardware_check",
        ):
            assert forbidden not in source
    finally:
        page.shutdown()
