"""M15B offline processing, export and control-isolation regressions."""

from __future__ import annotations

import csv
import builtins
from decimal import Decimal
import json
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
import pytest

from qpx_electrolyser.analysis.m15_results import (
    AnalysisError,
    CurveStyle,
    ExperimentSpec,
    FigureSettings,
    INSUFFICIENT_WINDOW,
    PlotConfiguration,
    PROCESSED_COLUMNS,
    analyse_m15_csv,
    export_processed_csv,
    export_processing_metadata,
    load_plot_configuration,
    save_plot_configuration,
)
from qpx_electrolyser.app.connection_controller import ConnectionController
from qpx_electrolyser.data.csv_logging import CSV_COLUMNS
from qpx_electrolyser.engine.constant_current import ProtocolState
from qpx_electrolyser.engine.custom_sequence import (
    CustomSequenceController,
    SequenceStage,
    SequenceStageType,
)
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.gui.results_plotter import (
    ResultsPlotterWindow,
    export_plot_files,
)
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.instrument.simulator_config import SimulatorConfig


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _row(
    elapsed: float,
    *,
    run: str = "run-a",
    channel: int = 1,
    stage: int = 1,
    stage_type: str = "constant_voltage",
    voltage: str = "2",
    current: str = "0.02",
    status: str = "ok",
    state: str = "RUNNING",
    output: str = "ON",
    area: str = "2",
) -> dict[str, object]:
    result: dict[str, object] = {name: "" for name in CSV_COLUMNS}
    result.update({
        "timestamp_iso": f"2026-01-01T00:00:{elapsed:06.3f}Z",
        "elapsed_seconds": str(elapsed),
        "channel": channel,
        "programmed_voltage_v": "2",
        "programmed_current_limit_a": "0.1",
        "measured_voltage_v": voltage,
        "measured_current_a": current,
        "output_state": output,
        "sample_status": status,
        "protocol_name": "custom_sequence",
        "protocol_state": state,
        "active_area_cm2": area,
        "sequence_name": "acceptance",
        "sequence_run_id": run,
        "sequence_stage_index": stage,
        "sequence_stage_total": 2,
        "sequence_stage_type": stage_type,
        "sequence_stage_state": state,
    })
    return result


def _write(path: Path, rows: list[dict[str, object]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _completed(rows: list[dict[str, object]], **kwargs: object) -> None:
    elapsed = float(rows[-1]["elapsed_seconds"]) if rows else 0.0
    rows.append(_row(elapsed, status="stage_completed", state="COMPLETED", output="OFF", **kwargs))


def _wait(qapp: QApplication, predicate, timeout: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for offline analysis")


def test_exact_final_thirty_seconds_and_known_population_statistics(tmp_path: Path) -> None:
    rows = [_row(0, voltage="1", current="0.01"), _row(30, voltage="3", current="0.03")]
    _completed(rows)
    report = analyse_m15_csv(ExperimentSpec(_write(tmp_path / "raw.csv", rows), "Nickel"))
    point = report.accepted_points[0]
    assert point.analysis_start_elapsed_s == Decimal("0")
    assert point.analysis_end_elapsed_s == Decimal("30")
    assert point.mean_voltage_v == Decimal("2")
    assert point.voltage_population_sd_v == Decimal("1")
    assert point.mean_current_a == Decimal("0.02")
    assert point.current_population_sd_a == Decimal("0.01")
    assert point.mean_current_density_a_cm2 == Decimal("0.01")
    assert point.sample_count == 2


def test_excluded_rows_break_continuity_and_short_final_segment_is_rejected(tmp_path: Path) -> None:
    rows = [_row(float(value)) for value in range(21)]
    rows.append(_row(20, status="paused", state="PAUSED", output="OFF"))
    rows.extend(_row(float(value)) for value in range(21, 51))
    _completed(rows)
    result = analyse_m15_csv(ExperimentSpec(_write(tmp_path / "pause.csv", rows))).results[0]
    assert not result.accepted
    assert result.validation_status == INSUFFICIENT_WINDOW
    report = analyse_m15_csv(ExperimentSpec(tmp_path / "pause.csv"))
    assert INSUFFICIENT_WINDOW in report.validation_status


def test_only_completed_stages_and_each_run_channel_stage_are_independent(tmp_path: Path) -> None:
    rows: list[dict[str, object]] = []
    for run, channel, stage in (("r1", 1, 1), ("r1", 2, 1), ("r2", 1, 2)):
        rows.extend([
            _row(0, run=run, channel=channel, stage=stage),
            _row(30, run=run, channel=channel, stage=stage),
        ])
        _completed(rows, run=run, channel=channel, stage=stage)
    rows.extend([_row(0, run="partial", stage=3), _row(40, run="partial", stage=3)])
    report = analyse_m15_csv(ExperimentSpec(_write(tmp_path / "mixed.csv", rows)))
    assert len(report.results) == 4
    assert len(report.accepted_points) == 3
    assert {(item.run_id, item.channel, item.stage_index) for item in report.accepted_points} == {
        ("r1", 1, 1), ("r1", 2, 1), ("r2", 1, 2),
    }


def test_ascending_descending_and_repeated_setpoints_do_not_change_one_point_per_stage(
    tmp_path: Path,
) -> None:
    rows: list[dict[str, object]] = []
    for elapsed, setpoint in ((0, "1"), (10, "2"), (20, "2"), (30, "1")):
        row = _row(float(elapsed), stage_type="voltage_scan")
        row["scan_commanded_setpoint"] = setpoint
        rows.append(row)
    _completed(rows, stage_type="voltage_scan")
    report = analyse_m15_csv(ExperimentSpec(_write(tmp_path / "scan.csv", rows)))
    assert len(report.accepted_points) == 1
    commands = json.loads(report.accepted_points[0].commanded_setpoints)
    assert [item["scan_commanded_setpoint"] for item in commands] == ["1", "2"]


def test_area_override_is_explicit_and_source_file_is_never_modified(tmp_path: Path) -> None:
    rows = [_row(0, area=""), _row(30, area="")]
    _completed(rows)
    source = _write(tmp_path / "override.csv", rows)
    original = source.read_bytes()
    report = analyse_m15_csv(ExperimentSpec(source, active_area_override_cm2=Decimal("4")))
    point = report.accepted_points[0]
    assert point.active_area_source == "overridden"
    assert point.mean_current_density_a_cm2 == Decimal("0.005")
    assert source.read_bytes() == original
    with pytest.raises(AnalysisError, match="positive"):
        analyse_m15_csv(ExperimentSpec(source, active_area_override_cm2=Decimal("0")))


@pytest.mark.parametrize("contents", ["", "a,b\n1,2\n"])
def test_empty_and_incompatible_files_are_locally_rejected(tmp_path: Path, contents: str) -> None:
    source = tmp_path / "bad.csv"
    source.write_text(contents, encoding="utf-8")
    with pytest.raises(AnalysisError):
        analyse_m15_csv(ExperimentSpec(source))


def test_exports_are_precise_exclusive_and_configuration_round_trips(tmp_path: Path) -> None:
    rows = [_row(0, voltage="1.123456789", current="0.0123456789"),
            _row(30, voltage="1.123456791", current="0.0123456791")]
    _completed(rows)
    report = analyse_m15_csv(ExperimentSpec(_write(tmp_path / "precision.csv", rows), "Pt"))
    first = export_processed_csv([report], tmp_path, "unsafe:name")
    second = export_processed_csv([report], tmp_path, "unsafe:name")
    assert first != second and first.exists() and second.exists()
    exported = list(csv.DictReader(first.open(encoding="utf-8")))
    assert tuple(exported[0]) == PROCESSED_COLUMNS
    assert Decimal(exported[0]["mean_measured_voltage_v"]) == Decimal("1.123456790")
    config = PlotConfiguration(
        FigureSettings(output_directory=str(tmp_path), plot_mode="density"),
        {str(report.source_file): CurveStyle("Pt", "Sample A", "#112233", "square", 8, "dash", 2, False, 4, "3")},
    )
    saved = save_plot_configuration(config, tmp_path, "settings")
    loaded = load_plot_configuration(saved)
    assert loaded == config
    metadata = export_processing_metadata([report], config, tmp_path, "settings")
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    assert payload["stable_window_seconds"] == "30"
    assert payload["source_filenames"] == ["precision.csv"]


def test_png_svg_pdf_export_and_collision_handling(qapp: QApplication, tmp_path: Path) -> None:
    rows = [_row(0), _row(30)]
    _completed(rows)
    source = _write(tmp_path / "plot.csv", rows)
    report = analyse_m15_csv(ExperimentSpec(source))
    key = str(source.resolve())
    config = PlotConfiguration(
        FigureSettings(output_directory=str(tmp_path), output_filename="figure"),
        {key: CurveStyle(legend_label="Curve")},
    )
    first = export_plot_files({key: report}, config)
    second = export_plot_files({key: report}, config)
    assert {item.suffix for item in first} == {".png", ".svg", ".pdf"}
    assert all(item.exists() and item.stat().st_size > 0 for item in first + second)
    assert set(first).isdisjoint(second)


def test_window_import_failure_is_contained_and_control_layer_receives_zero_operations(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = ConnectionController()
    submitted: list[object] = []
    monkeypatch.setattr(controller, "submit_command", lambda *args, **kwargs: submitted.append((args, kwargs)))
    window = MainWindow(tmp_path / "app.log", controller, data_directory=tmp_path)
    try:
        before = controller.snapshot
        window._open_results_plotter()
        plotter = window._results_plotter_window
        assert isinstance(plotter, ResultsPlotterWindow)
        bad = tmp_path / "malformed.csv"
        bad.write_text("bad,data\n", encoding="utf-8")
        plotter.import_paths([bad])
        _wait(qapp, lambda: "invalid" in plotter.status_label.text())
        assert controller.snapshot == before
        assert submitted == []
        assert any(
            "does not control the instrument" in label.text()
            for label in plotter.findChildren(type(plotter.status_label))
        )
        plotter.close()
        qapp.processEvents()
    finally:
        window.close()
        controller.shutdown()


def test_plotter_module_has_no_control_or_instrument_imports() -> None:
    source = Path("qpx_electrolyser/gui/results_plotter.py").read_text(encoding="utf-8")
    forbidden = (
        "SerialTransport", "QPX600DPDriver", "ConnectionController",
        "ChannelEngine", "submit_command", "hardware_check",
    )
    assert not any(token in source for token in forbidden)


def test_optional_extension_load_and_export_failures_leave_main_window_usable(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = ConnectionController()
    window = MainWindow(tmp_path / "app.log", controller, data_directory=tmp_path)
    warnings: list[str] = []
    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "qpx_electrolyser.gui.results_plotter":
            raise ImportError("contained test failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(str(message)),
    )
    try:
        before = controller.snapshot
        window._open_results_plotter()
        assert window._results_plotter_window is None
        assert warnings and "Experiment control is unaffected" in warnings[0]
        assert controller.snapshot == before
    finally:
        window.close()
        assert controller.shutdown(2)


def test_plotter_during_active_paused_sequence_submits_no_backend_operation(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class RecordingSimulator(SimulatedPowerSupply):
        def __init__(self) -> None:
            super().__init__(SimulatorConfig(communication_delay_s=0.001))
            self.calls: list[str] = []

        def output_on(self, channel: int) -> None:
            self.calls.append(f"on-{channel}")
            super().output_on(channel)

        def output_off(self, channel: int) -> None:
            self.calls.append(f"off-{channel}")
            super().output_off(channel)

    backend = RecordingSimulator()
    connection = ConnectionController(lambda: backend)
    connection.request_connect()
    _wait(qapp, lambda: connection.snapshot.state.value == "connected")
    sequence = CustomSequenceController(1, connection)
    stage = SequenceStage.create(
        SequenceStageType.CONSTANT_VOLTAGE,
        voltage="1", current="0.1", duration="60", interval="1",
    )
    plotter: ResultsPlotterWindow | None = None
    try:
        assert sequence.start("active", (stage,), "60 V", tmp_path)
        _wait(qapp, lambda: sequence.snapshot.state is ProtocolState.RUNNING)
        sequence.pause()
        _wait(qapp, lambda: sequence.snapshot.state is ProtocolState.PAUSED)
        before = list(backend.calls)
        plotter = ResultsPlotterWindow(tmp_path)
        plotter.show()
        qapp.processEvents()
        plotter.close()
        qapp.processEvents()
        plotter = None
        assert backend.calls == before
        assert sequence.snapshot.state is ProtocolState.PAUSED
    finally:
        if plotter is not None:
            plotter.close()
        sequence.stop("test cleanup")
        _wait(qapp, lambda: not sequence.snapshot.owns_channel)
        sequence.shutdown()
        assert connection.shutdown(2)
