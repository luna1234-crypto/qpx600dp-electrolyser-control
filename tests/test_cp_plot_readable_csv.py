"""Focused display-only CP styling and paired readable CSV regressions."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog
import pytest

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.app.settings import ApplicationSettings, SettingsStore
from qpx_electrolyser.data.csv_logging import (
    CSV_COLUMNS,
    READABLE_CSV_COLUMNS,
    ChannelLoggingController,
    CsvLogFile,
)
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.gui.plots.realtime import ChannelPlotPanel, PlotPoint, PlotSnapshot
from qpx_electrolyser.engine.constant_current import ConstantCurrentController, ProtocolState
from qpx_electrolyser.instrument.channel_readback import ChannelReadback
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _readback(channel: int, voltage: float, current: float) -> ChannelReadback:
    return ChannelReadback(channel, 4.0, 0.08, voltage, current, "80 V", True)


def _wait(qapp: QApplication, predicate, timeout_s: float = 3) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("Timed out waiting for Qt state")


def test_paired_csv_preserves_raw_schema_and_writes_numeric_readable_samples(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "experiment_raw.csv"
    readable_path = tmp_path / "experiment_readable.csv"
    writer = CsvLogFile(raw_path, 1)
    now = datetime.now(timezone.utc)
    writer.append(
        timestamp=now, elapsed_s=100, readable_elapsed_s=1.25, channel=1,
        readback=_readback(1, 3.99, 0.04), connection_state="connected",
        session_id=7, status="ok", protocol_name="constant_current",
        protocol_state="RUNNING",
    )
    # Transition/error evidence remains Raw-only.
    writer.append(
        timestamp=now, elapsed_s=101, channel=1, readback=_readback(1, 0, 0),
        connection_state="connected", session_id=7, status="paused",
        protocol_name="constant_current", protocol_state="PAUSED",
    )
    writer.append(
        timestamp=now, elapsed_s=200, readable_elapsed_s=2.5, channel=1,
        readback=_readback(1, 4.01, 0.041), connection_state="connected",
        session_id=7, status="ok", protocol_name="constant_current",
        protocol_state="RUNNING",
    )
    writer.close()

    assert raw_path.read_text(encoding="utf-8").splitlines()[0].split(",") == list(CSV_COLUMNS)
    with readable_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
        assert stream.closed is False
    assert tuple(rows[0]) == READABLE_CSV_COLUMNS
    assert rows == [
        {"Elapsed time (s)": "1.25", "Voltage (V)": "3.99", "Current (A)": "0.04"},
        {"Elapsed time (s)": "2.5", "Voltage (V)": "4.01", "Current (A)": "0.041"},
    ]
    for row in rows:
        for value in row.values():
            assert float(value) == float(value)


def test_pair_creation_is_exclusive_if_either_name_exists(tmp_path: Path) -> None:
    (tmp_path / "occupied_readable.csv").write_text("existing\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Readable CSV already exists"):
        CsvLogFile(tmp_path / "occupied_raw.csv", 1)
    assert not (tmp_path / "occupied_raw.csv").exists()


def test_readable_write_failure_uses_existing_safe_protocol_cleanup(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class FailingReadable:
        def writerow(self, _row: object) -> None:
            raise OSError("readable disk failure")

    created: list[CsvLogFile] = []

    def factory(path: Path, channel: int) -> CsvLogFile:
        writer = CsvLogFile(path, channel)
        writer._readable_writer = FailingReadable()  # type: ignore[assignment]
        created.append(writer)
        return writer

    backend = SimulatedPowerSupply()
    connection = ConnectionController(lambda: backend)
    protocol = ConstantCurrentController(1, connection, writer_factory=factory)
    connection.request_connect()
    _wait(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    try:
        assert protocol.start(
            "0.1", "2", "1", "0.1", "80 V", tmp_path / "failure_raw.csv"
        )
        _wait(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        assert "CSV write failed" in protocol.snapshot.message
        assert backend.read_output_state(1) is False
        assert created and created[0]._stream is None
        assert created[0]._readable_stream is None
    finally:
        connection.shutdown(2)


def test_active_protocol_clock_excludes_paused_wall_time_in_shared_logger(
    qapp: QApplication, tmp_path: Path,
) -> None:
    clock = [10.0]
    connection = ConnectionController(lambda: SimulatedPowerSupply())
    logger = ChannelLoggingController(1, connection, monotonic=lambda: clock[0])
    connection.request_connect()
    _wait(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    try:
        assert logger.start(tmp_path / "paused_raw.csv", 3600)
        logger.append_protocol(
            _readback(1, 3.9, 0.039), status="ok", protocol_state="RUNNING",
            readable_elapsed_s=1.0,
        )
        clock[0] += 120.0  # Paused wall time must not appear in readable elapsed.
        logger.append_protocol(
            _readback(1, 4.0, 0.04), status="ok", protocol_state="RUNNING",
            readable_elapsed_s=2.0,
        )
        logger.append_protocol(
            None, status="completed", protocol_state="COMPLETED",
            readable_elapsed_s=2.0,
        )
        logger.stop()
        with (tmp_path / "paused_readable.csv").open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        assert [float(row["Elapsed time (s)"]) for row in rows] == [1.0, 2.0]
    finally:
        connection.shutdown(2)


def test_cp_plot_styles_are_independent_persisted_and_applied(
    qapp: QApplication, tmp_path: Path,
) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    settings = ApplicationSettings.defaults(tmp_path)
    settings = __import__("dataclasses").replace(
        settings, cp_plot_styles={1: "line_dots", 2: "dots"}
    )
    store.save(settings)
    loaded = store.load(default_data_directory=tmp_path).settings
    connection = ConnectionController(lambda: SimulatedPowerSupply())
    window = MainWindow(
        tmp_path / "application.log", connection, data_directory=tmp_path,
        settings_store=store, settings=loaded,
    )
    try:
        assert window.protocol_panels[1].plot_style.currentData() == "line_dots"
        assert window.protocol_panels[2].plot_style.currentData() == "dots"
        assert window.plot_panels[1].canvas.trace_style == "line_dots"
        assert window.plot_panels[2].canvas.trace_style == "dots"
        window.protocol_panels[1].plot_style.setCurrentIndex(0)
        qapp.processEvents()
        assert window.plot_panels[1].canvas.trace_style == "dots"
        assert window.plot_panels[2].canvas.trace_style == "dots"
        persisted = window._current_settings()
        assert persisted.cp_plot_styles == {1: "dots", 2: "dots"}
    finally:
        window.close()
        connection.shutdown(2)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("style", ["dots", "line_dots"])
def test_cp_plot_styles_render_in_both_themes(
    qapp: QApplication, theme: str, style: str,
) -> None:
    panel = ChannelPlotPanel(1)
    panel.setProperty("uiTheme", theme)
    panel.resize(720, 360)
    panel.set_cp_plot_style(style)
    panel.apply_snapshot(PlotSnapshot(
        channel=1,
        points=(
            PlotPoint(0.0, 1.0, 0.01, 1),
            PlotPoint(1.0, 1.1, 0.011, 1),
        ),
        connected=True,
    ))
    panel.show()
    qapp.processEvents()
    try:
        assert panel.canvas.trace_style == style
        assert not panel.canvas.grab().isNull()
    finally:
        panel.close()


def test_gui_base_filename_maps_to_pair_and_cancel_is_side_effect_free(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = ConnectionController(lambda: SimulatedPowerSupply())
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    window._force_native_csv_dialog = True
    try:
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            lambda *args: (str(tmp_path / "chosen.csv"), "CSV files (*.csv)"),
        )
        assert window._choose_run_csv(1, "cp") == tmp_path / "chosen_raw.csv"
        writer = CsvLogFile(tmp_path / "chosen_raw.csv", 1)
        writer.close()
        assert (tmp_path / "chosen_readable.csv").exists()

        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
        assert window._choose_run_csv(2, "cp") is None
        assert not list(tmp_path.glob("*ch2*csv"))
    finally:
        window.close()
        connection.shutdown(2)


def test_old_settings_without_cp_style_migrate_to_dots(tmp_path: Path) -> None:
    payload = ApplicationSettings.defaults(tmp_path).to_json_object()
    payload["schema_version"] = 2
    payload.pop("cp_plot_styles", None)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = SettingsStore(path).load(default_data_directory=tmp_path)
    assert loaded.settings.cp_plot_styles == {1: "dots", 2: "dots"}

