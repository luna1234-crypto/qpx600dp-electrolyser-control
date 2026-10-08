"""Milestone 10 bounded plots fed only by existing Qt measurement signals."""

from collections.abc import Callable
import csv
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
import pytest

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.engine.constant_current import ProtocolState
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.gui.plots.realtime import (
    BoundedPlotSeries,
    ChannelPlotController,
    ChannelPlotPanel,
    PlotPoint,
)
from qpx_electrolyser.instrument.channel_readback import ChannelReadback


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def wait_until(qapp: QApplication, predicate: Callable[[], bool], timeout_s: float = 4) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("Timed out waiting for plotting state")


def reading(channel: int, voltage: float, current: float) -> ChannelReadback:
    return ChannelReadback(channel, 2, 0.1, voltage, current, "60 V", current > 0)


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_bounded_series_downsamples_long_runs_and_keeps_latest() -> None:
    series = BoundedPlotSeries(max_points=64)
    for index in range(20_000):
        series.append(PlotPoint(index * 0.1, index % 11, index % 7, 1))
    assert len(series.points) <= 64
    assert series.total_samples == 20_000
    assert series.stride > 1
    assert series.points[0].elapsed_s == 0
    assert series.points[-1].elapsed_s == pytest.approx(1_999.9)


def test_two_channel_models_are_independent_monotonic_and_renderable(
    qapp: QApplication,
) -> None:
    connection = ConnectionController()
    now = [100.0]
    ch1 = ChannelPlotController(1, connection, max_points=32, refresh_interval_ms=0,
                                monotonic=lambda: now[0])
    ch2 = ChannelPlotController(2, connection, max_points=32, refresh_interval_ms=0,
                                monotonic=lambda: now[0])
    panel = ChannelPlotPanel(1)
    ch1.snapshot_changed.connect(panel.apply_snapshot)
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        ch1.accept_sample(reading(1, 1.1, 0.05))
        now[0] += 1.25
        ch1.accept_sample(reading(1, 1.2, 0.06))
        ch2.accept_sample(reading(2, 2.1, 0.07))
        ch1.flush()
        ch2.flush()

        assert [point.elapsed_s for point in ch1.snapshot.points] == [0, 1.25]
        assert ch1.snapshot.latest_voltage_v == 1.2
        assert ch2.snapshot.total_samples == 1
        assert all(point.session_id == connection.snapshot.session_id
                   for point in ch1.snapshot.points)
        assert "Voltage: 1.2 V" == panel.latest_voltage.text()
        assert "Current: 0.06 A" == panel.latest_current.text()
        assert not panel.grab().isNull()
    finally:
        assert connection.shutdown(1)


def test_simultaneous_logging_and_protocols_feed_plots_without_extra_pollers(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection = ConnectionController()
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        for logger in window.logging_controllers.values():
            assert logger.start(tmp_path, 0.1)
        wait_until(qapp, lambda: all(
            plot.snapshot.total_samples >= 2 for plot in window.plot_controllers.values()
        ))
        for panel in window.protocol_panels.values():
            panel.duration_input.setText("0.35")
            panel.interval_input.setText("0.1")
            panel.start_button.click()
        wait_until(qapp, lambda: all(
            protocol.snapshot.state is ProtocolState.COMPLETED
            for protocol in window.protocol_controllers.values()
        ))

        for channel in (1, 2):
            plot = window.plot_controllers[channel].snapshot
            logger = window.logging_controllers[channel].snapshot
            assert plot.total_samples >= 3
            assert plot.latest_voltage_v is not None
            assert plot.latest_current_a is not None
            assert logger.active
            assert logger.file_path == window.protocol_controllers[channel].snapshot.file_path
            window.logging_controllers[channel].stop("plot integration test complete")
            assert csv_rows(logger.file_path)  # type: ignore[arg-type]
    finally:
        window.close()
        qapp.processEvents()
        assert connection.shutdown(1)


def test_pause_resume_stop_and_clear_do_not_change_csv_or_duplicate_data(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection = ConnectionController()
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        panel = window.protocol_panels[1]
        panel.duration_input.setText("2")
        panel.interval_input.setText("0.1")
        panel.start_button.click()
        wait_until(qapp, lambda: window.plot_controllers[1].snapshot.total_samples >= 1)
        panel.pause_button.click()
        wait_until(qapp, lambda: window.protocol_controllers[1].snapshot.state is ProtocolState.PAUSED)
        paused_count = window.plot_controllers[1].snapshot.total_samples
        pause_deadline = time.monotonic() + 0.2
        while time.monotonic() < pause_deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert window.plot_controllers[1].snapshot.total_samples == paused_count

        panel.resume_button.click()
        wait_until(qapp, lambda: window.plot_controllers[1].snapshot.total_samples > paused_count)
        panel.stop_button.click()
        wait_until(qapp, lambda: window.protocol_controllers[1].snapshot.state is ProtocolState.COMPLETED)
        file_path = window.protocol_controllers[1].snapshot.file_path
        rows_before = csv_rows(file_path)  # type: ignore[arg-type]
        assert {row["protocol_state"] for row in rows_before} >= {"RUNNING", "PAUSED"}

        window.plot_panels[1].clear_button.click()
        assert window.plot_controllers[1].snapshot.total_samples == 0
        assert window.plot_controllers[1].snapshot.points == ()
        assert csv_rows(file_path) == rows_before  # type: ignore[arg-type]
    finally:
        window.close()
        qapp.processEvents()
        assert connection.shutdown(1)


def test_disconnect_reconnect_preserves_history_with_session_break(
    qapp: QApplication,
) -> None:
    connection = ConnectionController()
    now = [10.0]
    plot = ChannelPlotController(1, connection, refresh_interval_ms=0,
                                 monotonic=lambda: now[0])
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        first_session = connection.snapshot.session_id
        plot.accept_sample(reading(1, 1, 0.1))
        connection.request_disconnect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.DISCONNECTED)
        assert not plot.snapshot.connected
        assert len(plot.snapshot.points) == 1

        now[0] += 5
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        plot.accept_sample(reading(1, 2, 0.2))
        sessions = [point.session_id for point in plot.snapshot.points]
        assert sessions == [first_session, connection.snapshot.session_id]
        assert sessions[0] != sessions[1]
        assert plot.snapshot.points[1].elapsed_s == 5
    finally:
        assert connection.shutdown(1)


def test_plot_failure_is_isolated_from_active_logger(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection = ConnectionController()
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        logger = window.logging_controllers[1]
        plot = window.plot_controllers[1]
        assert logger.start(tmp_path, 0.1)
        wait_until(qapp, lambda: logger.snapshot.sample_count >= 1)
        before = logger.snapshot.sample_count

        plot.accept_sample(object())
        plot.flush()
        assert plot.snapshot.error
        assert logger.snapshot.active
        assert connection.snapshot.state is ConnectionState.CONNECTED
        wait_until(qapp, lambda: logger.snapshot.sample_count > before)
        logger.stop("plot failure isolation test complete")
        assert len(csv_rows(logger.snapshot.file_path)) >= 2  # type: ignore[arg-type]
    finally:
        window.close()
        qapp.processEvents()
        assert connection.shutdown(1)
