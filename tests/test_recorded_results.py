"""Offline recorded-data parsing, rendering and isolation regressions."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import inspect
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
import pytest

from qpx_electrolyser.data.csv_logging import (
    CSV_COLUMNS, READABLE_CSV_COLUMNS, CsvLogFile,
)
from qpx_electrolyser.data.recorded_data import (
    RecordedDataError, RecordedDataFormat, RecordedPlotType, load_recorded_csv,
)
from qpx_electrolyser.gui.recorded_results import RecordedResultsPage
from qpx_electrolyser.engine.polarisation import (
    PolarisationPoint, PolarisationSummaryFile,
)
from qpx_electrolyser.instrument.channel_readback import ChannelReadback
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


def _readback(channel: int, voltage: float, current: float) -> ChannelReadback:
    return ChannelReadback(channel, voltage, 0.5, voltage, current, "80 V", True)


def _export_protocol_raw(tmp_path: Path, protocol: str, *, channel: int = 1) -> Path:
    """Exercise the application's real Raw exporter before feeding the import model."""
    path = tmp_path / f"{protocol}_ch{channel}_raw.csv"
    log = CsvLogFile(path, channel)
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    try:
        if protocol == "custom_sequence":
            for stage_index, stage_type in ((1, "constant_voltage"), (2, "constant_current")):
                for offset in (0.0, 0.5):
                    log.append(
                        timestamp=now, elapsed_s=stage_index + offset, channel=channel,
                        readback=_readback(channel, stage_index, 0.01 * stage_index),
                        connection_state="CONNECTED", session_id=17, status="ok",
                        protocol_name=stage_type, protocol_state="RUNNING",
                        sequence_name="Round trip", sequence_run_id="sequence-17",
                        sequence_stage_index=stage_index, sequence_stage_total=2,
                        sequence_stage_type=stage_type, sequence_stage_state="RUNNING",
                        active_area_cm2=2.0,
                    )
                log.append(
                    timestamp=now, elapsed_s=stage_index + 0.5, channel=channel,
                    readback=None, connection_state="CONNECTED", session_id=17,
                    status="stage_completed", protocol_name=stage_type,
                    protocol_state="COMPLETED", sequence_name="Round trip",
                    sequence_run_id="sequence-17", sequence_stage_index=stage_index,
                    sequence_stage_total=2, sequence_stage_type=stage_type,
                    sequence_stage_state="COMPLETED", active_area_cm2=2.0,
                )
        else:
            for index in range(3):
                scan = protocol in {"staircase_lsv", "voltage_scan", "current_scan"}
                log.append(
                    timestamp=now, elapsed_s=float(index), channel=channel,
                    readback=_readback(channel, 1.0 + index * 0.5, 0.01 + index * 0.005),
                    connection_state="CONNECTED", session_id=17,
                    status="lsv_point" if protocol == "staircase_lsv" else "ok",
                    protocol_name=protocol, protocol_state="RUNNING",
                    scan_name=protocol if scan else "",
                    scan_step_index=index + 1 if scan else None,
                    scan_total_steps=3 if scan else None,
                    scan_commanded_setpoint=1.0 + index * 0.5 if scan else None,
                    scan_point_valid=True if protocol == "staircase_lsv" else None,
                    active_area_cm2=2.0,
                )
            log.append(
                timestamp=now, elapsed_s=3.0, channel=channel, readback=None,
                connection_state="CONNECTED", session_id=17, status="completed",
                protocol_name=protocol, protocol_state="COMPLETED",
                active_area_cm2=2.0,
            )
    finally:
        log.close()
    if protocol == "steady_state_polarisation":
        summary = PolarisationSummaryFile(path)
        try:
            summary.append(PolarisationPoint(
                channel=channel, step_index=1, total_steps=1,
                commanded_current_a=0.02,
                commanded_current_density_a_cm2=0.01,
                mean_voltage_v=1.99, population_voltage_sd_v=0.012,
                mean_current_a=0.0198,
                mean_current_density_a_cm2=0.0099,
                sample_count=12, active_start_s=0.25, active_end_s=2.0,
            ))
        finally:
            summary.close()
    return path


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


@pytest.mark.parametrize(
    ("protocol", "expected_plot"),
    [
        ("constant_current", RecordedPlotType.TIME),
        ("constant_voltage", RecordedPlotType.TIME),
        ("staircase_lsv", RecordedPlotType.VOLTAGE_CURRENT),
        ("voltage_scan", RecordedPlotType.VOLTAGE_CURRENT),
        ("current_scan", RecordedPlotType.VOLTAGE_CURRENT),
        ("steady_state_polarisation", RecordedPlotType.POLARISATION),
        ("custom_sequence", RecordedPlotType.TIME),
    ],
)
def test_application_export_to_manual_import_round_trip_for_every_protocol(
    tmp_path: Path, protocol: str, expected_plot: RecordedPlotType,
) -> None:
    raw_path = _export_protocol_raw(tmp_path, protocol)
    dataset = load_recorded_csv(raw_path)
    assert dataset.channel == 1
    assert dataset.completed
    assert dataset.default_plot_type is expected_plot
    assert dataset.supports_current_density
    assert dataset.points
    if protocol == "staircase_lsv":
        assert len(dataset.xy_points) == 3
        assert all(point.scan_point_valid for point in dataset.xy_points)
    if protocol == "steady_state_polarisation":
        assert dataset.companion_path is not None
        assert len(dataset.polarisation_summary) == 1
        point = dataset.polarisation_summary[0]
        assert point.mean_voltage_v == pytest.approx(1.99)
        assert point.population_voltage_sd_v == pytest.approx(0.012)
    if protocol == "custom_sequence":
        assert [stage.index for stage in dataset.stages] == [1, 2]
        assert all(stage.completed for stage in dataset.stages)


def test_missing_or_incompatible_polarisation_companion_keeps_basic_plots(
    tmp_path: Path,
) -> None:
    path = _export_protocol_raw(tmp_path, "steady_state_polarisation")
    companion = next(tmp_path.glob(f"{path.stem}*_polarisation_summary.csv"))
    companion.unlink()
    dataset = load_recorded_csv(path)
    assert dataset.points
    assert not dataset.polarisation_summary
    assert dataset.default_plot_type is RecordedPlotType.CURRENT_DENSITY_VOLTAGE
    assert any("companion is missing" in note for note in dataset.advisories)

    companion.write_text("wrong,header\n1,2\n", encoding="utf-8")
    incompatible = load_recorded_csv(path)
    assert incompatible.points
    assert any("incompatible" in note for note in incompatible.advisories)


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


def test_page_exposes_polarisation_summary_error_bars_and_sequence_boundaries(
    qapp: QApplication, tmp_path: Path,
) -> None:
    polarisation = _export_protocol_raw(tmp_path, "steady_state_polarisation")
    sequence = _export_protocol_raw(tmp_path, "custom_sequence", channel=2)
    page = RecordedResultsPage(); page.resize(1000, 700); page.show()
    try:
        page.add_dataset(load_recorded_csv(polarisation)); qapp.processEvents()
        assert page.plot_type_combo.currentData() == RecordedPlotType.POLARISATION.value
        assert page.plot_stack.currentWidget() is page.xy_canvas
        assert page.xy_canvas.last_error_bar_count == 1
        assert "Companion:" in page.availability_message.text()
        point = page.xy_canvas._display_points[0].position
        page.xy_canvas.mousePressEvent(_mouse(
            QMouseEvent.Type.MouseButtonPress, point,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        ))
        assert "Voltage SD:" in page.xy_canvas.last_tooltip_text

        page.add_dataset(load_recorded_csv(sequence)); qapp.processEvents()
        assert page.current_dataset is not None and page.current_dataset.channel == 2
        assert page.plot_type_combo.currentData() == RecordedPlotType.TIME.value
        assert len(page.time_canvas._stages) == 2
        assert [stage.name for stage in page.time_canvas._stages] == [
            "constant_voltage", "constant_current",
        ]
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
