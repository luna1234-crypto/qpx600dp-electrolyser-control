"""Milestone 7 dual-channel manual controls using simulator/fake backends only."""

from collections.abc import Callable
import os
from pathlib import Path
from threading import Event, current_thread, main_thread
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
import pytest

from qpx_electrolyser.app.channel_controller import ManualChannelController
from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.instrument.simulator_config import SimulatorConfig


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def wait_until(qapp: QApplication, predicate: Callable[[], bool], timeout_s: float = 2) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("Timed out waiting for Qt state change")


class RecordingSimulator(SimulatedPowerSupply):
    def __init__(self, *, delay_s: float = 0) -> None:
        super().__init__(SimulatorConfig(communication_delay_s=delay_s))
        self.mutations: list[tuple[str, int, float | None, str]] = []

    def set_voltage(self, channel: int, voltage_v: float) -> None:
        self.mutations.append(("voltage", channel, voltage_v, current_thread().name))
        super().set_voltage(channel, voltage_v)

    def set_current(self, channel: int, current_a: float) -> None:
        self.mutations.append(("current", channel, current_a, current_thread().name))
        super().set_current(channel, current_a)

    def output_on(self, channel: int) -> None:
        self.mutations.append(("on", channel, None, current_thread().name))
        super().output_on(channel)

    def output_off(self, channel: int) -> None:
        self.mutations.append(("off", channel, None, current_thread().name))
        super().output_off(channel)


def connected_window(
    qapp: QApplication, tmp_path: Path, backend: PowerSupplyInterface,
) -> tuple[ConnectionController, MainWindow]:
    controller = ConnectionController(lambda: backend)
    window = MainWindow(tmp_path / "application.log", controller)
    window.show()
    controller.request_connect()
    wait_until(
        qapp,
        lambda: controller.snapshot.state is ConnectionState.CONNECTED
        and all(not item.snapshot.pending and item.snapshot.voltage_range_name
                for item in window.channel_controllers.values()),
    )
    return controller, window


def close_window(controller: ConnectionController, window: MainWindow) -> None:
    window.close()
    assert controller.shutdown(timeout_s=1)


def test_both_channels_read_back_and_target_only_the_selected_channel(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator()
    controller, window = connected_window(qapp, tmp_path, backend)
    try:
        ch1, ch2 = window.channel_panels[1], window.channel_panels[2]
        ch1.voltage_input.setText("5")
        ch1.set_voltage_button.click()
        ch2.current_input.setText("2")
        ch2.set_current_button.click()
        wait_until(qapp, lambda: not window.channel_controllers[1].snapshot.pending
                   and not window.channel_controllers[2].snapshot.pending)

        assert window.channel_controllers[1].snapshot.programmed_voltage_v == 5
        assert window.channel_controllers[1].snapshot.current_limit_a == 0
        assert window.channel_controllers[2].snapshot.programmed_voltage_v == 0
        assert window.channel_controllers[2].snapshot.current_limit_a == 2
        assert [(kind, channel, value) for kind, channel, value, _thread in backend.mutations] == [
            ("voltage", 1, 5), ("current", 2, 2)
        ]
        assert {thread for *_rest, thread in backend.mutations} == {"InstrumentCommandWorker"}
        assert ch1.output_state_value.text() == "OUTPUT OFF"
        assert ch2.output_state_value.text() == "OUTPUT OFF"
    finally:
        close_window(controller, window)


def test_output_on_requires_confirmation_and_state_is_unambiguous(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingSimulator()
    controller, window = connected_window(qapp, tmp_path, backend)
    panel = window.channel_panels[1]
    try:
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
        panel.output_on_button.click()
        qapp.processEvents()
        assert not backend.mutations
        assert panel.output_state_value.text() == "OUTPUT OFF"

        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
        panel.output_on_button.click()
        wait_until(qapp, lambda: not window.channel_controllers[1].snapshot.pending)
        assert backend.mutations == [("on", 1, None, "InstrumentCommandWorker")]
        assert panel.output_state_value.text() == "OUTPUT ON"

        panel.output_off_button.click()
        wait_until(qapp, lambda: not window.channel_controllers[1].snapshot.pending)
        assert panel.output_state_value.text() == "OUTPUT OFF"
    finally:
        close_window(controller, window)


@pytest.mark.parametrize("text", ["", "not-a-number", "-1", "nan", "inf", "60.001", "2.0001"])
def test_invalid_voltage_is_rejected_before_queue_submission(
    qapp: QApplication, tmp_path: Path, text: str,
) -> None:
    backend = RecordingSimulator()
    controller, window = connected_window(qapp, tmp_path, backend)
    try:
        channel = window.channel_controllers[1]
        channel.request_set_voltage(text)
        qapp.processEvents()
        assert not backend.mutations
        assert channel.snapshot.error
        assert not channel.snapshot.pending
    finally:
        close_window(controller, window)


@pytest.mark.parametrize("text", ["", "bad", "-0.01", "0", "nan", "inf", "50.01", "1.001"])
def test_invalid_current_is_rejected_before_queue_submission(
    qapp: QApplication, tmp_path: Path, text: str,
) -> None:
    backend = RecordingSimulator()
    controller, window = connected_window(qapp, tmp_path, backend)
    try:
        channel = window.channel_controllers[2]
        channel.request_set_current(text)
        qapp.processEvents()
        assert not backend.mutations
        assert channel.snapshot.error
    finally:
        close_window(controller, window)


def test_repeated_clicks_do_not_submit_duplicate_commands(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator(delay_s=0.01)
    controller, window = connected_window(qapp, tmp_path, backend)
    panel = window.channel_panels[1]
    try:
        panel.voltage_input.setText("3")
        panel.set_voltage_button.click()
        panel.set_voltage_button.click()
        window.channel_controllers[1].request_set_voltage("3")
        wait_until(qapp, lambda: not window.channel_controllers[1].snapshot.pending)
        assert [item[:3] for item in backend.mutations] == [("voltage", 1, 3)]
    finally:
        close_window(controller, window)


def test_backend_error_is_channel_local_and_preserves_other_display_state(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class FailingChannelOne(RecordingSimulator):
        def set_voltage(self, channel: int, voltage_v: float) -> None:
            if channel == 1:
                raise RuntimeError("injected CH1 failure")
            super().set_voltage(channel, voltage_v)

    backend = FailingChannelOne()
    controller, window = connected_window(qapp, tmp_path, backend)
    ch2_before = window.channel_controllers[2].snapshot
    try:
        window.channel_controllers[1].request_set_voltage("2")
        wait_until(qapp, lambda: window.channel_controllers[1].snapshot.error)
        assert "injected CH1 failure" in window.channel_controllers[1].snapshot.message
        assert window.channel_controllers[1].snapshot.programmed_voltage_v == 0
        assert window.channel_controllers[2].snapshot == ch2_before
        assert not window.channel_panels[1].set_voltage_button.isEnabled()
        assert window.channel_panels[2].refresh_button.isEnabled()
        window.channel_panels[1].refresh_button.click()
        wait_until(qapp, lambda: not window.channel_controllers[1].snapshot.faulted)
        assert window.channel_panels[1].set_voltage_button.isEnabled()
    finally:
        close_window(controller, window)


def test_cancellation_and_disconnect_return_controls_to_safe_disabled_state(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator()
    controller, window = connected_window(qapp, tmp_path, backend)
    started, release = Event(), Event()
    try:
        blocker = controller.submit_command(
            "block", lambda _supply: started.set() or release.wait(timeout=1)
        )
        assert blocker is not None and started.wait(timeout=1)
        channel = window.channel_controllers[1]
        channel.request_refresh()
        assert channel.snapshot.pending
        assert channel.cancel() == 1
        assert not channel.snapshot.pending
        controller.request_disconnect()
        release.set()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.DISCONNECTED)
        assert all(not panel.refresh_button.isEnabled() for panel in window.channel_panels.values())
        assert all(panel.output_state_value.text() == "UNKNOWN"
                   for panel in window.channel_panels.values())
    finally:
        release.set()
        close_window(controller, window)


def test_concurrent_channel_submissions_share_one_worker_and_complete_on_gui_thread(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator(delay_s=0.005)
    controller, window = connected_window(qapp, tmp_path, backend)
    callback_threads: list[bool] = []
    window.channel_controllers[1].snapshot_changed.connect(
        lambda _snapshot: callback_threads.append(current_thread() is main_thread())
    )
    window.channel_controllers[2].snapshot_changed.connect(
        lambda _snapshot: callback_threads.append(current_thread() is main_thread())
    )
    try:
        window.channel_controllers[1].request_set_voltage("4")
        window.channel_controllers[2].request_set_voltage("6")
        wait_until(qapp, lambda: all(not item.snapshot.pending
                                     for item in window.channel_controllers.values()))
        assert [item[:3] for item in backend.mutations] == [
            ("voltage", 1, 4), ("voltage", 2, 6)
        ]
        assert all(callback_threads)
        assert {item[3] for item in backend.mutations} == {"InstrumentCommandWorker"}
    finally:
        close_window(controller, window)


def test_controller_rejects_output_on_without_explicit_confirmation(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend = RecordingSimulator()
    controller, window = connected_window(qapp, tmp_path, backend)
    try:
        channel: ManualChannelController = window.channel_controllers[1]
        channel.request_output_on(False)
        qapp.processEvents()
        assert not backend.mutations
        assert channel.snapshot.error
        assert channel.snapshot.output_enabled is False
    finally:
        close_window(controller, window)
