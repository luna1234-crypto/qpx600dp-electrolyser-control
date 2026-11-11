"""Regression coverage for the current-session authoritative channel state."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
import pytest

from qpx_electrolyser.app.channel_controller import ManualChannelController
from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.engine.constant_current import ConstantCurrentController
from qpx_electrolyser.engine.constant_current import ProtocolState
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.instrument.channel_readback import ChannelReadback
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def wait_until(
    qapp: QApplication, predicate: Callable[[], bool], timeout_s: float = 4.0
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("Timed out waiting for shared channel state")


class RecordingSimulator(SimulatedPowerSupply):
    def __init__(self) -> None:
        super().__init__()
        self.output_off_calls: list[int] = []

    def output_off(self, channel: int) -> None:
        self.output_off_calls.append(channel)
        super().output_off(channel)


def connected_window(
    qapp: QApplication, tmp_path: Path, backend: SimulatedPowerSupply
) -> tuple[ConnectionController, MainWindow]:
    connection = ConnectionController(lambda: backend)
    window = MainWindow(tmp_path / "application.log", connection)
    connection.request_connect()
    wait_until(
        qapp,
        lambda: connection.snapshot.state is ConnectionState.CONNECTED
        and all(item.snapshot.connected for item in window.channel_controllers.values()),
    )
    return connection, window


def add_sequence_stage(window: MainWindow, channel: int, qapp: QApplication) -> None:
    window.sequence_panels[channel].add_button.click()
    qapp.processEvents()


def close_window(connection: ConnectionController, window: MainWindow) -> None:
    window.close()
    assert connection.shutdown(1)


def readback(connection: ConnectionController, channel: int) -> ChannelReadback:
    return next(
        item for item in connection.snapshot.channel_readbacks if item.channel == channel
    )


def test_manual_on_off_updates_shared_state_and_enables_sequence_without_reconnect(
    qapp: QApplication, tmp_path: Path
) -> None:
    backend = RecordingSimulator()
    connection, window = connected_window(qapp, tmp_path, backend)
    try:
        add_sequence_stage(window, 1, qapp)
        ch1_sequence = window.sequence_panels[1]
        ch2_before = readback(connection, 2)
        assert ch1_sequence.start_button.isEnabled()

        window.channel_controllers[1].request_output_on(True)
        wait_until(qapp, lambda: readback(connection, 1).output_enabled)
        assert not ch1_sequence.start_button.isEnabled()

        window.channel_controllers[1].request_output_off()
        wait_until(
            qapp,
            lambda: not window.channel_controllers[1].snapshot.pending
            and readback(connection, 1).output_enabled is False
            and ch1_sequence.start_button.isEnabled(),
        )
        assert window.channel_controllers[1].snapshot.output_enabled is False
        assert window.channel_workspaces[1].output_value.text() == "Output: OFF"
        assert readback(connection, 2) == ch2_before
        assert backend.output_off_calls.count(1) == 1
        prepared = window.sequence_controllers[1].validate_sequence(
            "authoritative off",
            ch1_sequence.stages,
            window.channel_controllers[1].snapshot.voltage_range_name,
        )
        assert len(prepared) == 1
    finally:
        close_window(connection, window)


def test_refresh_off_replaces_on_state_and_recomputes_availability(
    qapp: QApplication, tmp_path: Path
) -> None:
    backend = RecordingSimulator()
    connection, window = connected_window(qapp, tmp_path, backend)
    try:
        add_sequence_stage(window, 1, qapp)
        window.channel_controllers[1].request_output_on(True)
        wait_until(qapp, lambda: readback(connection, 1).output_enabled)
        assert not window.sequence_panels[1].start_button.isEnabled()

        backend.output_off(1)  # external/front-panel state represented by the fake backend
        window.channel_controllers[1].request_refresh()
        wait_until(
            qapp,
            lambda: readback(connection, 1).output_enabled is False
            and window.sequence_panels[1].start_button.isEnabled(),
        )
    finally:
        close_window(connection, window)


def test_pause_stop_and_natural_completion_publish_authoritative_off(
    qapp: QApplication, tmp_path: Path
) -> None:
    backend = RecordingSimulator()
    connection = ConnectionController(lambda: backend)
    manual = ManualChannelController(1, connection)
    protocol = ConstantCurrentController(1, connection)
    try:
        connection.request_connect()
        wait_until(qapp, lambda: manual.snapshot.connected)
        assert protocol.start("0.1", "2", "2", "0.1", "60 V", tmp_path)
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        protocol.pause()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.PAUSED)
        assert readback(connection, 1).output_enabled is False
        assert manual.snapshot.output_enabled is False

        protocol.stop("test stop")
        wait_until(qapp, lambda: not protocol.snapshot.owns_channel)
        assert readback(connection, 1).output_enabled is False

        assert protocol.start("0.1", "2", "0.25", "0.1", "60 V", tmp_path)
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)
        assert readback(connection, 1).output_enabled is False
        assert manual.snapshot.output_enabled is False
    finally:
        protocol.shutdown()
        assert connection.shutdown(1)


def test_emergency_all_off_updates_both_independent_shared_readbacks(
    qapp: QApplication, tmp_path: Path
) -> None:
    backend = RecordingSimulator()
    connection, window = connected_window(qapp, tmp_path, backend)
    try:
        window.channel_controllers[1].request_output_on(True)
        window.channel_controllers[2].request_output_on(True)
        wait_until(qapp, lambda: all(item.output_enabled for item in connection.snapshot.channel_readbacks))
        assert window.emergency_controller.request("shared-state regression")
        wait_until(qapp, lambda: not window.emergency_controller.snapshot.active)
        assert all(not item.output_enabled for item in connection.snapshot.channel_readbacks)
        assert all(
            item.snapshot.output_enabled is False
            for item in window.channel_controllers.values()
        )
    finally:
        close_window(connection, window)


def test_unconfirmed_and_old_session_results_cannot_enable_sequence(
    qapp: QApplication, tmp_path: Path
) -> None:
    backend = RecordingSimulator()
    connection, window = connected_window(qapp, tmp_path, backend)
    try:
        add_sequence_stage(window, 1, qapp)
        window.channel_controllers[1].request_output_on(True)
        wait_until(qapp, lambda: readback(connection, 1).output_enabled)
        assert not window.sequence_panels[1].start_button.isEnabled()

        old_session = connection.snapshot.session_id - 1
        stale = ChannelReadback(1, 0, 0, 0, 0, "60 V", False)
        connection._command_sessions[999_999] = old_session
        connection._handle_command_completed(999_999, "stale-old-session", stale)
        qapp.processEvents()
        assert readback(connection, 1).output_enabled is True
        assert not window.sequence_panels[1].start_button.isEnabled()
    finally:
        close_window(connection, window)


def test_failed_output_off_keeps_shared_state_on_and_sequence_disabled(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingOffSimulator(RecordingSimulator):
        def output_off(self, channel: int) -> None:
            self.output_off_calls.append(channel)
            raise RuntimeError("injected unconfirmed Output Off")

    backend = FailingOffSimulator()
    connection, window = connected_window(qapp, tmp_path, backend)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Ok)
    try:
        add_sequence_stage(window, 1, qapp)
        window.channel_controllers[1].request_output_on(True)
        wait_until(qapp, lambda: readback(connection, 1).output_enabled)
        window.channel_controllers[1].request_output_off()
        wait_until(qapp, lambda: window.channel_controllers[1].snapshot.faulted)
        assert readback(connection, 1).output_enabled is True
        assert not window.sequence_panels[1].start_button.isEnabled()
        assert backend.output_off_calls.count(1) == 1
    finally:
        close_window(connection, window)
