"""Guarded real-mode integration using injected fake serial ports only."""

from collections import deque
from collections.abc import Callable, Iterator
import json
import logging
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
import pytest

from qpx_electrolyser.app.channel_controller import ChannelSnapshot, ManualChannelController
from qpx_electrolyser.app.connection_controller import (
    ConnectionController,
    ConnectionSnapshot,
    ConnectionState,
)
from qpx_electrolyser.data.csv_logging import ChannelLoggingController, CsvLogFile
from qpx_electrolyser.engine.constant_current import (
    ConstantCurrentConfig,
    ConstantCurrentController,
    ProtocolSnapshot,
    ProtocolState,
    REAL_HARDWARE_PROTOCOL_CONFIRMATION,
)
from qpx_electrolyser.engine.constant_voltage import (
    ConstantVoltageController,
    REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
)
from qpx_electrolyser.gui.channel_panel import ChannelPanel
from qpx_electrolyser.gui.constant_current_panel import ConstantCurrentPanel
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.instrument.qpx600dp import QPX600DPDriver
from qpx_electrolyser.instrument.connection_worker import CommandPriority
from qpx_electrolyser.instrument.serial_transport import SerialTransport


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


class FakeSerial:
    def __init__(self, responses: list[bytes | Exception]) -> None:
        self.responses = deque(responses)
        self.writes: list[bytes] = []
        self.settings: list[dict[str, object]] = []
        self.is_open = True
        self.close_calls = 0

    def open_with(self, **settings: object) -> "FakeSerial":
        self.settings.append(settings)
        return self

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        if not self.responses:
            return b""
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    def close(self) -> None:
        self.close_calls += 1
        self.is_open = False


def channel_responses(
    channel: int,
    *,
    voltage: float = 5,
    current: float = 0.5,
    output: bool = False,
    voltage_range: int = 1,
) -> list[bytes]:
    return [
        f"{voltage_range}\r\n".encode(),
        f"V{channel} {voltage:.3f}\r\n".encode(),
        f"I{channel} {current:.2f}\r\n".encode(),
        (f"{voltage:.3f}V\r\n" if output else "0.000V\r\n").encode(),
        (f"{current:.2f}A\r\n" if output else "0.00A\r\n").encode(),
        ("1\r\n" if output else "0\r\n").encode(),
    ]


def output_off_responses(
    channel: int,
    *,
    voltage: float = 2,
    current: float = 0.1,
    confirmation_states: tuple[bool, ...] = (False, False),
    operation_complete: bytes = b"1\r\n",
    authoritative_output: bool = False,
) -> list[bytes]:
    """Script *OPC?, stable OP? polling, then one authoritative readback."""
    if len(confirmation_states) < 2 or any(confirmation_states[-2:]):
        raise ValueError("confirmation_states must end with two OFF responses")
    return [
        operation_complete,
        *((b"1\r\n" if state else b"0\r\n") for state in confirmation_states),
        *channel_responses(
            channel,
            voltage=voltage,
            current=current,
            output=authoritative_output,
        ),
    ]


def initial_responses(
    *, identity: bytes = b"THURLBY THANDAR,QPX600DP,FAKE,1.00\r\n",
) -> list[bytes]:
    return [identity, *channel_responses(1), *channel_responses(2)]


def protocol_start_responses(channel: int, *, mismatch: bool = False) -> list[bytes]:
    voltage = 3.000 if mismatch else 2.000
    return [
        b"0\r\n",  # prepare: selected output OFF
        b"1\r\n",  # range query performed by set_voltage
        f"V{channel} {voltage:.3f}\r\n".encode(),
        f"I{channel} 0.10\r\n".encode(),
        b"0\r\n",  # final preflight: still OFF
        f"V{channel} 2.000\r\n".encode(),
        f"I{channel} 0.10\r\n".encode(),
        b"1\r\n",  # *OPC? after the single Output On write
        b"1\r\n",  # first stable-ON confirmation
        b"1\r\n",  # second stable-ON confirmation
        *channel_responses(channel, voltage=2, current=0.1, output=True),
    ]


def protocol_resume_responses(channel: int) -> list[bytes]:
    """Script resume preflight plus the shared stable-ON transaction."""
    return [
        b"0\r\n",
        f"V{channel} 2.000\r\n".encode(),
        f"I{channel} 0.10\r\n".encode(),
        b"0\r\n",
        f"V{channel} 2.000\r\n".encode(),
        f"I{channel} 0.10\r\n".encode(),
        b"1\r\n",
        b"1\r\n",
        b"1\r\n",
        *channel_responses(channel, voltage=2, current=0.1, output=True),
    ]


class FakeRealBackendFactory:
    def __init__(self, fakes: Iterator[FakeSerial] | FakeSerial) -> None:
        self._fakes = fakes if isinstance(fakes, Iterator) else iter((fakes,))
        self.ports: list[str] = []
        self.created: list[FakeSerial] = []

    def __call__(self, port_name: str) -> QPX600DPDriver:
        fake = next(self._fakes)
        self.ports.append(port_name)
        self.created.append(fake)
        return QPX600DPDriver(
            SerialTransport(port_name, serial_factory=fake.open_with)
        )


def arm_real(controller: ConnectionController, port: str = "COM11") -> None:
    controller.set_simulation_mode(False)
    controller.set_port_name(port)


def test_mode_and_manual_port_selection_never_construct_or_open_backend() -> None:
    fake = FakeSerial(initial_responses())
    factory = FakeRealBackendFactory(fake)
    controller = ConnectionController(real_backend_factory=factory)
    try:
        assert controller.snapshot.simulation_mode
        arm_real(controller)
        assert controller.snapshot.port_name == "COM11"
        assert controller.snapshot.real_port_valid
        assert factory.ports == []
        assert fake.settings == []
        assert fake.writes == []
    finally:
        assert controller.shutdown(timeout_s=1)


def test_real_connect_exact_identify_and_read_order_has_no_mutating_commands(
    qapp: QApplication,
) -> None:
    fake = FakeSerial(initial_responses())
    factory = FakeRealBackendFactory(fake)
    controller = ConnectionController(real_backend_factory=factory)
    try:
        arm_real(controller)
        controller.request_connect()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.CONNECTED)
        assert factory.ports == ["COM11"]
        assert len(fake.settings) == 1
        assert fake.writes == [
            b"*IDN?\n",
            b"VRANGE1?\n", b"V1?\n", b"I1?\n", b"V1O?\n", b"I1O?\n", b"OP1?\n",
            b"VRANGE2?\n", b"V2?\n", b"I2?\n", b"V2O?\n", b"I2O?\n", b"OP2?\n",
        ]
        assert all(command.rstrip(b"\n").endswith(b"?") for command in fake.writes)
        assert [item.channel for item in controller.snapshot.initial_readbacks] == [1, 2]
        assert all(not item.output_enabled for item in controller.snapshot.initial_readbacks)
        controller.set_simulation_mode(True)
        controller.set_port_name("COM99")
        assert not controller.snapshot.simulation_mode
        assert controller.snapshot.port_name == "COM11"

        before = list(fake.writes)
        controller.request_disconnect()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.DISCONNECTED)
        assert fake.writes == before
        assert fake.close_calls == 1
    finally:
        assert controller.shutdown(timeout_s=1)


@pytest.mark.parametrize("identity", [
    b"THURLBY THANDAR,CPX400DP,FAKE,1.00\r\n",
    b"malformed\r\n",
    b"",
])
def test_wrong_identity_malformed_response_and_timeout_close_failed_session(
    qapp: QApplication, identity: bytes,
) -> None:
    fake = FakeSerial([identity])
    controller = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    try:
        arm_real(controller)
        controller.request_connect()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.ERROR)
        assert fake.close_calls == 1
        assert not fake.is_open
        assert controller.snapshot.instrument_id is None
        assert controller.snapshot.initial_readbacks == ()
    finally:
        assert controller.shutdown(timeout_s=1)


def test_busy_port_failure_does_not_retry_automatically(qapp: QApplication) -> None:
    attempts: list[str] = []

    def busy_factory(port_name: str) -> QPX600DPDriver:
        attempts.append(port_name)

        def fail_open(**_settings: object) -> FakeSerial:
            raise OSError("port busy")

        return QPX600DPDriver(SerialTransport(port_name, serial_factory=fail_open))

    controller = ConnectionController(real_backend_factory=busy_factory)
    try:
        arm_real(controller, "COM12")
        controller.request_connect()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.ERROR)
        qapp.processEvents()
        assert attempts == ["COM12"]
    finally:
        assert controller.shutdown(timeout_s=1)


def test_channel_mapping_writes_setpoints_while_outputs_stay_off(
    qapp: QApplication,
) -> None:
    responses = initial_responses()
    responses += [b"1\r\n", *channel_responses(1, voltage=6, current=0.5)]
    responses += channel_responses(2, voltage=5, current=0.7)
    fake = FakeSerial(responses)
    controller = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    ch1 = ManualChannelController(1, controller)
    ch2 = ManualChannelController(2, controller)
    try:
        arm_real(controller)
        controller.request_connect()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.CONNECTED)
        ch2_before = ch2.snapshot

        ch1.request_set_voltage("6")
        ch1.request_set_voltage("6")
        wait_until(qapp, lambda: not ch1.snapshot.pending)
        assert ch1.snapshot.programmed_voltage_v == 6
        assert ch1.snapshot.output_enabled is False
        assert ch2.snapshot == ch2_before

        ch2.request_set_current("0.7")
        wait_until(qapp, lambda: not ch2.snapshot.pending)
        assert ch2.snapshot.current_limit_a == pytest.approx(0.7)
        assert ch2.snapshot.output_enabled is False
        assert ch1.snapshot.programmed_voltage_v == 6

        mutations = [command for command in fake.writes if not command.rstrip(b"\n").endswith(b"?")]
        assert mutations == [b"V1 6.000\n", b"I2 0.70\n"]
        assert not any(command.startswith(b"OP1 ") or command.startswith(b"OP2 ")
                       for command in fake.writes)
    finally:
        assert controller.shutdown(timeout_s=1)


def test_real_output_on_requires_detailed_confirmation_and_cancel_sends_nothing(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = initial_responses()
    responses += channel_responses(1, voltage=5, current=0.5, output=True)
    fake = FakeSerial(responses)
    controller = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    channel = ManualChannelController(1, controller)
    panel = ChannelPanel(1)
    channel.snapshot_changed.connect(panel.apply_snapshot)
    panel.output_on_requested.connect(channel.request_output_on)
    panel.set_real_hardware_mode(True)
    captured: list[str] = []
    try:
        arm_real(controller)
        controller.request_connect()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.CONNECTED)
        panel.apply_snapshot(channel.snapshot)
        baseline = list(fake.writes)

        def cancel(*args: object) -> QMessageBox.StandardButton:
            captured.append(str(args[2]))
            return QMessageBox.StandardButton.Cancel

        monkeypatch.setattr(QMessageBox, "warning", cancel)
        panel.output_on_button.click()
        qapp.processEvents()
        assert fake.writes == baseline
        assert "Channel: 1" in captured[-1]
        assert "Programmed voltage: 5 V" in captured[-1]
        assert "Current limit: 0.5 A" in captured[-1]

        monkeypatch.setattr(
            QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Yes
        )
        panel.output_on_button.click()
        wait_until(qapp, lambda: not channel.snapshot.pending)
        assert b"OP1 1\n" in fake.writes
        assert channel.snapshot.output_enabled is True
    finally:
        assert controller.shutdown(timeout_s=1)


def test_real_command_error_closes_session_and_recovery_is_manual(
    qapp: QApplication,
) -> None:
    first = FakeSerial([*initial_responses(), b"invalid\r\n"])
    second = FakeSerial(initial_responses())
    factory = FakeRealBackendFactory(iter((first, second)))
    controller = ConnectionController(real_backend_factory=factory)
    channel = ManualChannelController(1, controller)
    try:
        arm_real(controller)
        controller.request_connect()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.CONNECTED)
        channel.request_refresh()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.ERROR)
        assert first.close_calls == 1
        assert factory.ports == ["COM11"]
        assert not channel.snapshot.connected

        controller.request_connect()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.CONNECTED)
        assert factory.ports == ["COM11", "COM11"]
        assert channel.snapshot.connected
    finally:
        assert controller.shutdown(timeout_s=1)


def test_simulation_connect_never_calls_real_factory(qapp: QApplication) -> None:
    def forbidden(_port_name: str) -> QPX600DPDriver:
        raise AssertionError("real backend factory must not run in Simulation Mode")

    controller = ConnectionController(real_backend_factory=forbidden)
    try:
        controller.request_connect()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.CONNECTED)
        assert controller.snapshot.simulation_mode
    finally:
        assert controller.shutdown(timeout_s=1)


def test_real_protocol_requires_guard_token_before_any_protocol_command(
    qapp: QApplication, tmp_path: Path,
) -> None:
    fake = FakeSerial(initial_responses())
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantCurrentController(1, connection)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        baseline = list(fake.writes)

        assert not protocol.start("0.1", "2", "2", "1", "60 V", tmp_path)
        assert "not confirmed" in protocol.snapshot.message
        assert fake.writes == baseline
        assert not list(tmp_path.glob("*.csv"))
    finally:
        assert connection.shutdown(timeout_s=1)


def test_real_protocol_confirmation_exact_order_single_on_and_confirmed_off_cleanup(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(1),
        *output_off_responses(1),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    window.show()
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        panel = window.protocol_panels[1]
        panel.duration_input.setText("2")
        panel.interval_input.setText("1")
        baseline = list(fake.writes)
        prompts: list[str] = []

        def cancel(*args: object) -> QMessageBox.StandardButton:
            prompts.append(str(args[2]))
            return QMessageBox.StandardButton.Cancel

        monkeypatch.setattr(QMessageBox, "warning", cancel)
        panel.start_button.click()
        qapp.processEvents()
        assert fake.writes == baseline
        assert "REAL HARDWARE OUTPUT-ON AUTHORIZATION" in prompts[-1]
        assert "Channel: CH1" in prompts[-1]
        assert "CSV destination:" in prompts[-1]

        monkeypatch.setattr(
            QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Yes
        )
        panel.start_button.click()
        panel.start_button.click()  # stale/double-click guard
        wait_until(qapp, lambda: window.protocol_controllers[1].snapshot.state is ProtocolState.RUNNING)
        window.protocol_controllers[1].stop("fake-serial test cleanup")
        wait_until(qapp, lambda: window.protocol_controllers[1].snapshot.state is ProtocolState.COMPLETED)

        protocol_writes = fake.writes[len(baseline):]
        assert protocol_writes == [
            b"OP1?\n", b"VRANGE1?\n", b"V1 2.000\n", b"I1 0.10\n",
            b"V1?\n", b"I1?\n",
            b"OP1?\n", b"V1?\n", b"I1?\n", b"OP1 1\n", b"*OPC?\n",
            b"OP1?\n", b"OP1?\n",
            b"VRANGE1?\n", b"V1?\n", b"I1?\n",
            b"V1O?\n", b"I1O?\n", b"OP1?\n",
            b"OP1 0\n", b"*OPC?\n", b"OP1?\n", b"OP1?\n",
            b"VRANGE1?\n", b"V1?\n", b"I1?\n",
            b"V1O?\n", b"I1O?\n", b"OP1?\n",
        ]
        assert protocol_writes.count(b"OP1 1\n") == 1
        assert window.protocol_controllers[1].snapshot.output_enabled is False
        assert not any(command.startswith((b"V2 ", b"I2 ", b"OP2 ")) for command in protocol_writes)
    finally:
        window.close()
        qapp.processEvents()
        assert connection.shutdown(timeout_s=1)


def test_real_protocol_readback_mismatch_never_turns_on_and_uses_priority_cleanup(
    qapp: QApplication, tmp_path: Path,
) -> None:
    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(1, mismatch=True)[:4],
        *output_off_responses(1, voltage=3),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantCurrentController(1, connection)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "0.1", "2", "2", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_PROTOCOL_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        mutations = [item for item in fake.writes if not item.rstrip(b"\n").endswith(b"?")]
        assert mutations == [b"V1 2.000\n", b"I1 0.10\n", b"OP1 0\n"]
        assert b"OP1 1\n" not in fake.writes
        assert protocol.snapshot.output_enabled is False
        assert not any(command.startswith((b"V2 ", b"I2 ", b"OP2 ")) for command in fake.writes)
    finally:
        assert connection.shutdown(timeout_s=1)


def test_stop_while_real_start_is_pending_cannot_submit_stale_output_on(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class SlowFakeSerial(FakeSerial):
        def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
            if len(self.writes) > len(initial_responses()) and self.writes[-1] == b"OP1?\n":
                time.sleep(0.03)
            return super().read_until(expected, size)

    fake = SlowFakeSerial([
        *initial_responses(),
        *protocol_start_responses(1)[:4],
        *output_off_responses(1),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantCurrentController(1, connection)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "0.1", "2", "2", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_PROTOCOL_CONFIRMATION,
        )
        wait_until(qapp, lambda: len(fake.writes) > len(initial_responses()))
        protocol.stop("cancelled during guarded preflight")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)
        assert b"OP1 1\n" not in fake.writes
        assert fake.writes.count(b"OP1 0\n") == 1
        assert protocol.snapshot.output_enabled is False
    finally:
        assert connection.shutdown(timeout_s=1)


def test_real_protocol_resume_requires_fresh_confirmation_and_reuses_csv(
    qapp: QApplication, tmp_path: Path,
) -> None:
    fake = FakeSerial([
        *initial_responses(),
        *channel_responses(1),  # active logger's initial read-only sample
        *protocol_start_responses(1),
        *output_off_responses(1),  # pause
        *protocol_resume_responses(1),
        *output_off_responses(1),  # stop
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    logger = ChannelLoggingController(1, connection)
    protocol = ConstantCurrentController(1, connection)
    logger.snapshot_changed.connect(protocol.logging_snapshot_changed)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert logger.start(tmp_path, 10)
        wait_until(qapp, lambda: logger.snapshot.sample_count == 1)
        shared_path = logger.snapshot.file_path
        assert protocol.start(
            "0.1", "2", "4", "1", "60 V", tmp_path,
            logging_sink=logger,
            real_hardware_confirmation=REAL_HARDWARE_PROTOCOL_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        protocol.pause()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.PAUSED)
        before_cancelled_resume = list(fake.writes)

        assert not protocol.resume()
        assert fake.writes == before_cancelled_resume
        wait_until(qapp, lambda: protocol.snapshot.resume_ready, timeout_s=1.5)
        assert protocol.resume(REAL_HARDWARE_PROTOCOL_CONFIRMATION)
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        protocol.stop("fake-serial resume test cleanup")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)

        assert logger.snapshot.active
        assert protocol.snapshot.file_path == shared_path
        assert len(list(tmp_path.glob("qpx600dp_ch1_*.csv"))) == 1
        assert fake.writes.count(b"OP1 1\n") == 2
        assert fake.writes.count(b"OP1 0\n") == 2
        assert not any(command.startswith((b"V2 ", b"I2 ", b"OP2 ")) for command in fake.writes)
        logger.stop("operator-owned logger stopped by test")
    finally:
        logger.shutdown()
        assert connection.shutdown(timeout_s=1)


def test_real_resume_waits_for_confirmed_off_quiescence_without_side_effects(
    qapp: QApplication, tmp_path: Path,
) -> None:
    now = [100.0]

    def advance(delay_s: float) -> None:
        now[0] += delay_s

    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(1),
        *output_off_responses(1),
        *protocol_resume_responses(1),
        *output_off_responses(1),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantCurrentController(
        1, connection, monotonic=lambda: now[0], sleep=advance,
    )
    panel = ConstantCurrentPanel(1)
    panel.set_context(available=True)
    protocol.snapshot_changed.connect(panel.apply_snapshot)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "0.1", "2", "5", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_PROTOCOL_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        csv_path = protocol.snapshot.file_path

        protocol.pause()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.PAUSED)
        writes_at_pause = list(fake.writes)
        assert protocol.snapshot.output_enabled is False
        assert not protocol.snapshot.resume_ready
        assert not panel.resume_button.isEnabled()
        assert protocol.snapshot.message == "Output OFF confirmed; waiting before Resume."

        assert not protocol.resume(REAL_HARDWARE_PROTOCOL_CONFIRMATION)
        assert not protocol.resume(REAL_HARDWARE_PROTOCOL_CONFIRMATION)
        assert fake.writes == writes_at_pause
        assert protocol.snapshot.file_path == csv_path

        now[0] += 0.999
        protocol._complete_resume_quiescence()
        assert not protocol.snapshot.resume_ready
        assert not panel.resume_button.isEnabled()
        now[0] += 0.002
        protocol._complete_resume_quiescence()
        assert protocol.snapshot.resume_ready
        assert panel.resume_button.isEnabled()

        assert protocol.resume(REAL_HARDWARE_PROTOCOL_CONFIRMATION)
        assert not protocol.resume(REAL_HARDWARE_PROTOCOL_CONFIRMATION)
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        assert fake.writes.count(b"OP1 1\n") == 2
        assert protocol.snapshot.file_path == csv_path
        assert len(list(tmp_path.glob("qpx600dp_ch1_*.csv"))) == 1
        assert not any(command.startswith((b"V2 ", b"I2 ", b"OP2 ")) for command in fake.writes)

        protocol.stop("resume gate regression cleanup")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)
    finally:
        panel.deleteLater()
        assert connection.shutdown(timeout_s=1)


@pytest.mark.parametrize("event", ["stop", "disconnect", "failure", "session-change"])
def test_real_resume_quiescence_is_cancelled_by_terminal_or_session_event(
    qapp: QApplication, event: str,
) -> None:
    now = [100.0]
    connection = ConnectionController()
    protocol = ConstantCurrentController(1, connection, monotonic=lambda: now[0])
    protocol._real_hardware_run = True
    protocol._session_id = 7
    protocol._snapshot = ProtocolSnapshot(
        channel=1,
        state=ProtocolState.PAUSED,
        config=ConstantCurrentConfig(0.1, 2.0, 5.0, 1.0, "60 V"),
        remaining_active_s=5.0,
        output_enabled=False,
    )
    protocol._start_resume_quiescence()
    assert protocol._resume_enable_timer.isActive()

    if event == "stop":
        protocol.stop()
    elif event == "failure":
        protocol.force_shutdown("test failure")
    else:
        protocol._connection_changed(ConnectionSnapshot(
            state=(
                ConnectionState.DISCONNECTED
                if event == "disconnect"
                else ConnectionState.CONNECTED
            ),
            simulation_mode=False,
            port_name="REDACTED",
            instrument_id=None,
            initial_readbacks=(),
            message="test event",
            session_id=7 if event == "disconnect" else 8,
        ))

    now[0] += 2.0
    protocol._complete_resume_quiescence()
    qapp.processEvents()
    assert not protocol._resume_enable_timer.isActive()
    assert not protocol.snapshot.resume_ready
    assert protocol.snapshot.state is not ProtocolState.PAUSED


def test_ambiguous_real_output_on_never_retries_and_requests_priority_off(
    qapp: QApplication, tmp_path: Path,
) -> None:
    responses = protocol_start_responses(1)
    responses[-1] = b"malformed\r\n"
    fake = FakeSerial([*initial_responses(), *responses])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantCurrentController(1, connection)
    submissions: list[tuple[str, CommandPriority | None]] = []
    original_submit = connection.submit_command

    def recording_submit(name: str, operation, **kwargs):  # type: ignore[no-untyped-def]
        submissions.append((name, kwargs.get("priority")))
        return original_submit(name, operation, **kwargs)

    connection.submit_command = recording_submit  # type: ignore[method-assign]
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "0.1", "2", "2", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_PROTOCOL_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.ERROR)

        assert fake.writes.count(b"OP1 1\n") == 1
        assert ("ch1-constant-current-stop", CommandPriority.SAFETY) in submissions
        assert protocol.snapshot.operator_warning
        assert "could not be confirmed" in protocol.snapshot.message
        assert fake.close_calls == 1
    finally:
        assert connection.shutdown(timeout_s=1)


def test_real_protocol_fresh_initially_on_readback_refuses_every_mutation(
    qapp: QApplication, tmp_path: Path,
) -> None:
    fake = FakeSerial([*initial_responses(), b"1\r\n"])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantCurrentController(1, connection)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        baseline = len(fake.writes)
        assert protocol.start(
            "0.1", "2", "2", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_PROTOCOL_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        assert fake.writes[baseline:] == [b"OP1?\n"]
        assert protocol.snapshot.operator_warning
        assert connection.snapshot.state is ConnectionState.CONNECTED
    finally:
        assert connection.shutdown(timeout_s=1)


def test_real_protocol_ch2_mapping_is_isolated(
    qapp: QApplication, tmp_path: Path,
) -> None:
    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(2),
        *output_off_responses(2),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantCurrentController(2, connection)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        baseline = len(fake.writes)
        assert protocol.start(
            "0.1", "2", "2", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_PROTOCOL_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        protocol.stop("fake CH2 mapping cleanup")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)
        commands = fake.writes[baseline:]
        assert commands.count(b"OP2 1\n") == 1
        assert commands.count(b"OP2 0\n") == 1
        assert b"V2 2.000\n" in commands
        assert b"I2 0.10\n" in commands
        assert not any(command.startswith((b"V1 ", b"I1 ", b"OP1 ")) for command in commands)
    finally:
        assert connection.shutdown(timeout_s=1)


def test_invalid_real_protocol_input_prevents_confirmation_file_and_queue(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSerial(initial_responses())
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        baseline = list(fake.writes)
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda *args: pytest.fail("invalid input must not reach confirmation"),
        )
        window._start_protocol(1, "nan", "2", "2", "1")
        assert fake.writes == baseline
        assert not list(tmp_path.glob("*.csv"))
        assert "finite" in window.statusBar().currentMessage()
    finally:
        window.close()
        qapp.processEvents()
        assert connection.shutdown(timeout_s=1)


def test_real_cv_confirmation_cancel_exact_order_single_on_and_isolation(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(1),
        *output_off_responses(1),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        panel = window.voltage_protocol_panels[1]
        panel.duration_input.setText("2")
        panel.interval_input.setText("1")
        baseline = list(fake.writes)
        prompts: list[str] = []

        def cancel(*args: object) -> QMessageBox.StandardButton:
            prompts.append(str(args[2]))
            return QMessageBox.StandardButton.Cancel

        monkeypatch.setattr(QMessageBox, "warning", cancel)
        panel.start_button.click()
        qapp.processEvents()
        assert fake.writes == baseline
        assert "REAL HARDWARE OUTPUT-ON AUTHORIZATION" in prompts[-1]
        assert "Target voltage: 2 V" in prompts[-1]
        assert "Current limit: 0.1 A" in prompts[-1]

        monkeypatch.setattr(
            QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Yes
        )
        panel.start_button.click()
        panel.start_button.click()
        protocol = window.voltage_protocol_controllers[1]
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        protocol.stop("fake CV command-order cleanup")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)

        commands = fake.writes[len(baseline):]
        assert commands == [
            b"OP1?\n", b"VRANGE1?\n", b"V1 2.000\n", b"I1 0.10\n",
            b"V1?\n", b"I1?\n",
            b"OP1?\n", b"V1?\n", b"I1?\n", b"OP1 1\n", b"*OPC?\n",
            b"OP1?\n", b"OP1?\n",
            b"VRANGE1?\n", b"V1?\n", b"I1?\n",
            b"V1O?\n", b"I1O?\n", b"OP1?\n",
            b"OP1 0\n", b"*OPC?\n", b"OP1?\n", b"OP1?\n",
            b"VRANGE1?\n", b"V1?\n", b"I1?\n",
            b"V1O?\n", b"I1O?\n", b"OP1?\n",
        ]
        assert commands.count(b"OP1 1\n") == 1
        assert protocol.snapshot.output_enabled is False
        assert not any(command.startswith((b"V2 ", b"I2 ", b"OP2 ")) for command in commands)
    finally:
        window.close()
        qapp.processEvents()
        assert connection.shutdown(timeout_s=1)


def test_real_cv_requires_guard_and_rejects_invalid_or_initially_on_before_writes(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        QMessageBox, "critical", lambda *args: QMessageBox.StandardButton.Ok
    )
    fake = FakeSerial([*initial_responses(), b"1\r\n"])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        protocol = window.voltage_protocol_controllers[1]
        baseline = list(fake.writes)

        assert not protocol.start("2", "0.1", "2", "1", "60 V", tmp_path)
        assert fake.writes == baseline
        assert not list(tmp_path.glob("*.csv"))

        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda *args: pytest.fail("invalid CV input must not reach confirmation"),
        )
        window._start_voltage_protocol(1, "61", "0.1", "2", "1")
        assert fake.writes == baseline
        assert not list(tmp_path.glob("*.csv"))

        assert protocol.start(
            "2", "0.1", "2", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        assert fake.writes[len(baseline):] == [b"OP1?\n"]
        assert protocol.snapshot.operator_warning
    finally:
        window.close()
        qapp.processEvents()
        assert connection.shutdown(timeout_s=1)


def test_real_cv_readback_mismatch_never_turns_on_and_confirms_off(
    qapp: QApplication, tmp_path: Path,
) -> None:
    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(1, mismatch=True)[:4],
        *output_off_responses(1, voltage=3),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantVoltageController(1, connection)
    submissions: list[tuple[str, CommandPriority | None]] = []
    original_submit = connection.submit_command

    def recording_submit(name: str, operation, **kwargs):  # type: ignore[no-untyped-def]
        submissions.append((name, kwargs.get("priority")))
        return original_submit(name, operation, **kwargs)

    connection.submit_command = recording_submit  # type: ignore[method-assign]
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "2", "0.1", "2", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        mutations = [item for item in fake.writes if not item.rstrip(b"\n").endswith(b"?")]
        assert mutations == [b"V1 2.000\n", b"I1 0.10\n", b"OP1 0\n"]
        assert b"OP1 1\n" not in fake.writes
        assert protocol.snapshot.output_enabled is False
        assert ("ch1-constant-voltage-stop", CommandPriority.SAFETY) in submissions
    finally:
        assert connection.shutdown(timeout_s=1)


def test_real_cv_ambiguous_on_and_cleanup_failure_warn_without_retry(
    qapp: QApplication, tmp_path: Path,
) -> None:
    failed_on = protocol_start_responses(1)
    failed_on[-1] = b"malformed\r\n"
    fake = FakeSerial([*initial_responses(), *failed_on])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantVoltageController(1, connection)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "2", "0.1", "2", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        assert fake.writes.count(b"OP1 1\n") == 1
        assert protocol.snapshot.operator_warning
        assert "could not be confirmed" in protocol.snapshot.message
    finally:
        assert connection.shutdown(timeout_s=1)

    fake2 = FakeSerial([*initial_responses(), *protocol_start_responses(1), b"bad\r\n"])
    connection2 = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake2))
    protocol2 = ConstantVoltageController(1, connection2)
    try:
        arm_real(connection2)
        connection2.request_connect()
        wait_until(qapp, lambda: connection2.snapshot.state is ConnectionState.CONNECTED)
        assert protocol2.start(
            "2", "0.1", "2", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol2.snapshot.state is ProtocolState.RUNNING)
        protocol2.stop("exercise failed OFF readback")
        wait_until(qapp, lambda: protocol2.snapshot.state is ProtocolState.FAILED)
        assert fake2.writes.count(b"OP1 0\n") == 1
        assert protocol2.snapshot.operator_warning
        assert "could not be confirmed" in protocol2.snapshot.message
    finally:
        assert connection2.shutdown(timeout_s=1)


def test_real_cv_pause_resume_reconfirms_and_reuses_existing_csv(
    qapp: QApplication, tmp_path: Path,
) -> None:
    fake = FakeSerial([
        *initial_responses(),
        *channel_responses(1),
        *protocol_start_responses(1),
        *output_off_responses(1),
        *protocol_resume_responses(1),
        *output_off_responses(1),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    logger = ChannelLoggingController(1, connection)
    protocol = ConstantVoltageController(1, connection)
    logger.snapshot_changed.connect(protocol.logging_snapshot_changed)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert logger.start(tmp_path, 10)
        wait_until(qapp, lambda: logger.snapshot.sample_count == 1)
        shared_path = logger.snapshot.file_path
        assert protocol.start(
            "2", "0.1", "4", "1", "60 V", tmp_path,
            logging_sink=logger,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        protocol.pause()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.PAUSED)
        before_cancel = list(fake.writes)
        assert not protocol.resume()
        assert fake.writes == before_cancel
        wait_until(qapp, lambda: protocol.snapshot.resume_ready, timeout_s=1.5)
        assert protocol.resume(REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION)
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        protocol.stop("CV resume test cleanup")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)
        assert logger.snapshot.active
        assert protocol.snapshot.file_path == shared_path
        assert len(list(tmp_path.glob("qpx600dp_ch1_*.csv"))) == 1
        assert fake.writes.count(b"OP1 1\n") == 2
        assert fake.writes.count(b"OP1 0\n") == 2
        logger.stop("operator-owned logger test cleanup")
    finally:
        logger.shutdown()
        assert connection.shutdown(timeout_s=1)


def test_real_cv_completion_and_coordinated_disconnect_confirm_off(
    qapp: QApplication, tmp_path: Path,
) -> None:
    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(1),
        *channel_responses(1, voltage=2, current=0.1, output=True),
        *output_off_responses(1),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    now = [100.0]
    protocol = ConstantVoltageController(1, connection, monotonic=lambda: now[0])
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "2", "0.1", "1.5", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.sample_count >= 1)
        now[0] += 2.0
        protocol._update_clock()
        wait_until(qapp, lambda: protocol.snapshot.state in {
            ProtocolState.COMPLETED, ProtocolState.FAILED,
        })
        assert protocol.snapshot.state is ProtocolState.COMPLETED, (
            protocol.snapshot.message, fake.writes, list(fake.responses)
        )
        assert protocol.snapshot.output_enabled is False
        assert fake.writes.count(b"OP1 1\n") == 1
        assert fake.writes.count(b"OP1 0\n") == 1
    finally:
        assert connection.shutdown(timeout_s=1)

    fake2 = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(2),
        *output_off_responses(2),
    ])
    connection2 = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake2))
    window = MainWindow(tmp_path / "disconnect.log", connection2, data_directory=tmp_path)
    try:
        arm_real(connection2)
        connection2.request_connect()
        wait_until(qapp, lambda: connection2.snapshot.state is ConnectionState.CONNECTED)
        protocol2 = window.voltage_protocol_controllers[2]
        assert protocol2.start(
            "2", "0.1", "2", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol2.snapshot.state is ProtocolState.RUNNING)
        window._request_disconnect()
        wait_until(qapp, lambda: connection2.snapshot.state is ConnectionState.DISCONNECTED)
        assert fake2.writes.count(b"OP2 0\n") == 1
        assert protocol2.snapshot.output_enabled is False
    finally:
        window.close()
        qapp.processEvents()
        assert connection2.shutdown(timeout_s=1)


def test_real_cv_logging_failure_stops_only_selected_channel_and_closes_writer(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class FailingProtocolWriter:
        def __init__(self, directory: Path, channel: int) -> None:
            self._inner = CsvLogFile(directory, channel)
            self.path = self._inner.path

        def append(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            if kwargs.get("protocol_name") == "constant_voltage":
                raise OSError("injected CV disk failure")
            self._inner.append(**kwargs)

        def close(self) -> None:
            self._inner.close()

    fake = FakeSerial([
        *initial_responses(),
        *channel_responses(1),
        *protocol_start_responses(1),
        *channel_responses(1, voltage=2, current=0.1, output=True),
        *output_off_responses(1),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    logger = ChannelLoggingController(1, connection, writer_factory=FailingProtocolWriter)
    protocol = ConstantVoltageController(1, connection)
    logger.snapshot_changed.connect(protocol.logging_snapshot_changed)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert logger.start(tmp_path, 10)
        wait_until(qapp, lambda: logger.snapshot.sample_count == 1)
        assert protocol.start(
            "2", "0.1", "2", "1", "60 V", tmp_path,
            logging_sink=logger,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        assert not logger.snapshot.active
        assert fake.writes.count(b"OP1 1\n") == 1
        assert fake.writes.count(b"OP1 0\n") == 1
        assert not any(command.startswith((b"V2 ", b"I2 ", b"OP2 ")) for command in fake.writes)
    finally:
        logger.shutdown()
        assert connection.shutdown(timeout_s=1)


def test_real_cv_stale_start_cannot_turn_on_and_two_channels_are_serialized(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class SlowFakeSerial(FakeSerial):
        def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
            if self.writes[-1] == b"OP1?\n" and len(self.writes) > len(initial_responses()):
                time.sleep(0.03)
            return super().read_until(expected, size)

    slow = SlowFakeSerial([
        *initial_responses(),
        *protocol_start_responses(1)[:4],
        *output_off_responses(1),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(slow))
    protocol = ConstantVoltageController(1, connection)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "2", "0.1", "2", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: len(slow.writes) > len(initial_responses()))
        protocol.stop("cancel stale CV start")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)
        for _ in range(10):
            qapp.processEvents()
            time.sleep(0.002)
        assert b"OP1 1\n" not in slow.writes
        assert slow.writes.count(b"OP1 0\n") == 1
    finally:
        assert connection.shutdown(timeout_s=1)

    start1 = protocol_start_responses(1)
    start2 = protocol_start_responses(2)
    fake = FakeSerial([
        *initial_responses(),
        *start1[:4], *start2[:4], *start1[4:], *start2[4:],
        *channel_responses(1, voltage=2, current=0.1, output=True),
        *channel_responses(2, voltage=2, current=0.1, output=True),
        *output_off_responses(1),
        *output_off_responses(2),
    ])
    connection2 = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocols = {
        channel: ConstantVoltageController(channel, connection2) for channel in (1, 2)
    }
    try:
        arm_real(connection2)
        connection2.request_connect()
        wait_until(qapp, lambda: connection2.snapshot.state is ConnectionState.CONNECTED)
        for item in protocols.values():
            assert item.start(
                "2", "0.1", "2", "1", "60 V", tmp_path,
                real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
            )
        wait_until(qapp, lambda: all(
            item.snapshot.state is ProtocolState.RUNNING for item in protocols.values()
        ))
        wait_until(qapp, lambda: all(
            item.snapshot.sample_count >= 1 for item in protocols.values()
        ))
        for item in protocols.values():
            item.stop("simultaneous fake-serial cleanup")
        wait_until(qapp, lambda: all(
            item.snapshot.state in {ProtocolState.COMPLETED, ProtocolState.FAILED}
            for item in protocols.values()
        ))
        assert all(
            item.snapshot.state is ProtocolState.COMPLETED for item in protocols.values()
        ), ([(item.snapshot.state, item.snapshot.message) for item in protocols.values()],
            fake.writes, list(fake.responses))
        assert fake.writes.count(b"OP1 1\n") == 1
        assert fake.writes.count(b"OP2 1\n") == 1
        assert fake.writes.count(b"OP1 0\n") == 1
        assert fake.writes.count(b"OP2 0\n") == 1
    finally:
        assert connection2.shutdown(timeout_s=1)


def test_real_cv_second_sample_off_logs_aligned_trace_then_one_safety_cleanup(
    qapp: QApplication,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(1),
        *channel_responses(1, voltage=2, current=0.1, output=True),
        *channel_responses(1, voltage=2, current=0.1, output=False),
        *output_off_responses(1),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantVoltageController(1, connection)
    caplog.set_level(
        logging.INFO, logger="qpx_electrolyser.instrument.serial_transport"
    )
    diagnostic_logger = logging.getLogger("qpx_electrolyser.instrument.serial_transport")
    previous_propagate = diagnostic_logger.propagate
    diagnostic_logger.propagate = False
    diagnostic_logger.addHandler(caplog.handler)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        baseline = len(fake.writes)
        assert protocol.start(
            "2", "0.1", "3", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(
            qapp,
            lambda: protocol.snapshot.state is ProtocolState.FAILED,
            timeout_s=4,
        )

        commands = fake.writes[baseline:]
        sample_tail = [
            b"VRANGE1?\n", b"V1?\n", b"I1?\n", b"V1O?\n", b"I1O?\n", b"OP1?\n",
        ]
        first_sample_start = 19
        assert commands[first_sample_start:first_sample_start + 6] == sample_tail
        assert commands[first_sample_start + 6:first_sample_start + 12] == sample_tail
        second_output_query = first_sample_start + 11
        cleanup_off = commands.index(b"OP1 0\n")
        assert cleanup_off > second_output_query
        assert commands.count(b"OP1 1\n") == 1
        assert commands.count(b"OP1 0\n") == 1
        assert protocol.snapshot.message == "Output unexpectedly reported OFF."

        records = [
            json.loads(record.message.removeprefix("hardware_transaction "))
            for record in caplog.records
            if record.message.startswith("hardware_transaction ")
        ]
        protocol_records = [
            record for record in records
            if str(record["operation"]).startswith("ch1-constant-voltage-")
        ]
        tx_commands = [
            record["command"] for record in protocol_records
            if record["event"] == "serial_tx"
        ]
        assert tx_commands == [item.decode().rstrip("\n") for item in commands]
        rx_records = [
            record for record in protocol_records if record["event"] == "serial_rx"
        ]
        assert len(rx_records) == sum(command.endswith(b"?\n") for command in commands)
        off_intent = [
            record for record in protocol_records
            if record["event"] == "output_off_intent"
        ]
        assert len(off_intent) == 1
        assert off_intent[0]["priority"] == "SAFETY"
        assert off_intent[0]["reason"] == "Output unexpectedly reported OFF."
        assert all(record["session_id"] == connection.snapshot.session_id for record in protocol_records)
        assert all(record["request_id"] > 0 for record in protocol_records)
    finally:
        diagnostic_logger.removeHandler(caplog.handler)
        diagnostic_logger.propagate = previous_propagate
        assert connection.shutdown(timeout_s=1)


def test_active_real_logger_is_strictly_read_only(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    fake = FakeSerial([*initial_responses(), *channel_responses(1)])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    logger = ChannelLoggingController(1, connection)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        baseline = len(fake.writes)
        assert logger.start(tmp_path, 10)
        wait_until(qapp, lambda: logger.snapshot.sample_count == 1)
        logger.stop("diagnostic read-only regression complete")
        logging_commands = fake.writes[baseline:]
        assert logging_commands
        assert all(command.rstrip(b"\n").endswith(b"?") for command in logging_commands)
        assert not any(command.startswith(b"OP1 ") for command in logging_commands)
        assert not any(command.startswith(b"OP2 ") for command in logging_commands)
    finally:
        logger.shutdown()
        assert connection.shutdown(timeout_s=1)


@pytest.mark.parametrize(
    "confirmation_states",
    [
        pytest.param((False, False), id="immediate-stable-off"),
        pytest.param((False, True, False, False), id="observed-off-on-rebound"),
        pytest.param(
            (False, True, False, True, False, False),
            id="multiple-rebounds-then-stable-off",
        ),
    ],
)
def test_real_cv_bounded_off_confirmation_writes_off_exactly_once(
    qapp: QApplication,
    tmp_path: Path,
    confirmation_states: tuple[bool, ...],
) -> None:
    now = [100.0]

    def advance(delay_s: float) -> None:
        now[0] += delay_s

    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(1),
        *channel_responses(1, voltage=2, current=0.1, output=True),
        *output_off_responses(1, confirmation_states=confirmation_states),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantVoltageController(
        1,
        connection,
        monotonic=lambda: now[0],
        sleep=advance,
    )
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "2", "0.1", "5", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.sample_count == 1)
        baseline = len(fake.writes)
        protocol.stop("bounded OFF confirmation regression")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)

        cleanup = fake.writes[baseline:]
        assert cleanup.count(b"OP1 0\n") == 1
        assert cleanup[:2 + len(confirmation_states)] == [
            b"OP1 0\n",
            b"*OPC?\n",
            *([b"OP1?\n"] * len(confirmation_states)),
        ]
        assert cleanup[-6:] == [
            b"VRANGE1?\n", b"V1?\n", b"I1?\n",
            b"V1O?\n", b"I1O?\n", b"OP1?\n",
        ]
        assert now[0] == pytest.approx(
            100.05 + 0.05 * (len(confirmation_states) - 1)
        )
        assert protocol.snapshot.output_enabled is False
    finally:
        assert connection.shutdown(timeout_s=1)


def test_real_cv_off_confirmation_timeout_writes_once_and_requires_front_panel(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    now = [100.0]

    def advance(delay_s: float) -> None:
        now[0] += delay_s

    confirmation_attempts = 20  # Immediate, then every 0.05 s through t=0.95 s.
    rebound_responses = [
        b"0\r\n" if index % 2 == 0 else b"1\r\n"
        for index in range(confirmation_attempts)
    ]
    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(1),
        *channel_responses(1, voltage=2, current=0.1, output=True),
        b"1\r\n",  # *OPC?
        *rebound_responses,
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantVoltageController(
        1,
        connection,
        monotonic=lambda: now[0],
        sleep=advance,
    )
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "2", "0.1", "5", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.sample_count == 1)
        baseline = len(fake.writes)
        protocol.pause()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.ERROR)

        cleanup = fake.writes[baseline:]
        assert cleanup.count(b"OP1 0\n") == 1
        assert cleanup.count(b"*OPC?\n") == 1
        assert cleanup.count(b"OP1?\n") == confirmation_attempts
        assert not any(command.startswith(b"VRANGE1?") for command in cleanup)
        assert now[0] == pytest.approx(101.05)
        assert protocol.snapshot.operator_warning
        assert "front-panel intervention" in protocol.snapshot.message
        assert fake.close_calls == 1
    finally:
        assert connection.shutdown(timeout_s=1)


def test_real_cv_pause_near_completion_uses_one_bounded_off_transaction(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    now = [200.0]

    def advance(delay_s: float) -> None:
        now[0] += delay_s

    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(1),
        *channel_responses(1, voltage=2, current=0.1, output=True),
        *output_off_responses(
            1, confirmation_states=(False, True, False, False)
        ),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantVoltageController(
        1,
        connection,
        monotonic=lambda: now[0],
        sleep=advance,
    )
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "2", "0.1", "2", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.sample_count == 1)
        now[0] = protocol._run_started_at + 1.99
        baseline = len(fake.writes)
        protocol.pause()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)

        cleanup = fake.writes[baseline:]
        assert cleanup.count(b"OP1 0\n") == 1
        assert cleanup.count(b"*OPC?\n") == 1
        assert cleanup.count(b"OP1?\n") == 5  # four confirmations plus readback
        assert protocol.snapshot.output_enabled is False
    finally:
        assert connection.shutdown(timeout_s=1)


def test_real_cv_shutdown_uses_bounded_off_confirmation_once(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(1),
        *channel_responses(1, voltage=2, current=0.1, output=True),
        *output_off_responses(
            1, confirmation_states=(False, True, False, False)
        ),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantVoltageController(1, connection, sleep=lambda _delay: None)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "2", "0.1", "5", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.sample_count == 1)
        baseline = len(fake.writes)
        protocol.shutdown()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)

        cleanup = fake.writes[baseline:]
        assert cleanup.count(b"OP1 0\n") == 1
        assert cleanup.count(b"*OPC?\n") == 1
        assert cleanup.count(b"OP1?\n") == 5
        assert protocol.snapshot.output_enabled is False
    finally:
        assert connection.shutdown(timeout_s=1)


def test_real_cv_authoritative_readback_rebound_never_repeats_output_off(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    now = [100.0]
    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(1),
        *channel_responses(1, voltage=2, current=0.1, output=True),
        *output_off_responses(1, authoritative_output=True),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantVoltageController(
        1,
        connection,
        monotonic=lambda: now[0],
        sleep=lambda delay_s: now.__setitem__(0, now[0] + delay_s),
    )
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "2", "0.1", "5", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.sample_count == 1)
        baseline = len(fake.writes)
        protocol.pause()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.ERROR)

        cleanup = fake.writes[baseline:]
        assert cleanup.count(b"OP1 0\n") == 1
        assert cleanup.count(b"*OPC?\n") == 1
        assert cleanup.count(b"OP1?\n") == 3
        assert protocol.snapshot.operator_warning
        assert "physical OFF could not be confirmed" in protocol.snapshot.message
    finally:
        assert connection.shutdown(timeout_s=1)


@pytest.mark.parametrize(
    "operation_complete_response",
    [
        pytest.param(b"malformed\r\n", id="malformed-opc"),
        pytest.param(b"", id="opc-timeout"),
    ],
)
def test_real_cv_operation_complete_failure_closes_once_without_off_retry(
    qapp: QApplication,
    tmp_path: Path,
    operation_complete_response: bytes,
) -> None:
    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(2),
        *channel_responses(2, voltage=2, current=0.1, output=True),
        operation_complete_response,
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantVoltageController(2, connection)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "2", "0.1", "5", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.sample_count == 1)
        baseline = len(fake.writes)
        protocol.pause()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.ERROR)

        cleanup = fake.writes[baseline:]
        assert cleanup == [b"OP2 0\n", b"*OPC?\n"]
        assert protocol.snapshot.operator_warning
        assert fake.close_calls == 1
    finally:
        assert connection.shutdown(timeout_s=1)


@pytest.mark.parametrize(
    ("failure_point", "expected_message", "expected_off_writes"),
    [
        pytest.param(
            "second-on-poll-off",
            "unexpectedly reported OFF during Output-On stabilization",
            1,
            id="on-then-off-within-50ms",
        ),
        pytest.param("malformed-opc", "Malformed *OPC? response", 1, id="malformed-opc"),
        pytest.param(
            "timeout-opc",
            "physical OFF could not be confirmed",
            0,
            id="timeout-opc-invalidates-session",
        ),
        pytest.param(
            "authoritative-off",
            "did not remain ON during authoritative read-back",
            1,
            id="authoritative-readback-off",
        ),
    ],
)
def test_real_cv_stable_on_failures_attempt_on_and_off_exactly_once(
    qapp: QApplication,
    tmp_path: Path,
    failure_point: str,
    expected_message: str,
    expected_off_writes: int,
) -> None:
    start = protocol_start_responses(1)
    if failure_point == "second-on-poll-off":
        start[9] = b"0\r\n"
        start = start[:10]
    elif failure_point == "malformed-opc":
        start[7] = b"malformed\r\n"
        start = start[:8]
    elif failure_point == "timeout-opc":
        start[7] = b""
        start = start[:8]
    elif failure_point == "authoritative-off":
        start[-1] = b"0\r\n"

    fake = FakeSerial([
        *initial_responses(),
        *start,
        *output_off_responses(1),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantVoltageController(1, connection, sleep=lambda _delay: None)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "2", "0.1", "3", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.FAILED)

        assert expected_message in protocol.snapshot.message
        assert fake.writes.count(b"OP1 1\n") == 1
        assert fake.writes.count(b"OP1 0\n") == expected_off_writes
        assert protocol._output_off_attempted
        assert not any(command.startswith((b"V2 ", b"I2 ", b"OP2 ")) for command in fake.writes)
    finally:
        assert connection.shutdown(timeout_s=1)


def test_real_protocol_minimum_interval_rejected_before_any_mutation(
    qapp: QApplication, tmp_path: Path,
) -> None:
    fake = FakeSerial(initial_responses())
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    current = ConstantCurrentController(1, connection)
    voltage = ConstantVoltageController(2, connection)
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        baseline = list(fake.writes)

        assert not current.start(
            "0.1", "2", "3", "0.999", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_PROTOCOL_CONFIRMATION,
        )
        assert "between 1 and 3600 seconds" in current.snapshot.message
        assert not voltage.start(
            "2", "0.1", "3", "0.999", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        assert "between 1 and 3600 seconds" in voltage.snapshot.message
        assert fake.writes == baseline
        assert not list(tmp_path.glob("*.csv"))
    finally:
        assert connection.shutdown(timeout_s=1)


def test_real_cv_resume_rebases_first_sample_without_skipping_active_slot(
    qapp: QApplication, tmp_path: Path,
) -> None:
    now = [100.0]

    def advance(delay_s: float) -> None:
        now[0] += delay_s

    fake = FakeSerial([
        *initial_responses(),
        *protocol_start_responses(1),
        *output_off_responses(1),
        *protocol_resume_responses(1),
        *channel_responses(1, voltage=2, current=0.1, output=True),
        *output_off_responses(1),
    ])
    connection = ConnectionController(real_backend_factory=FakeRealBackendFactory(fake))
    protocol = ConstantVoltageController(
        1, connection, monotonic=lambda: now[0], sleep=advance,
    )
    try:
        arm_real(connection)
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        assert protocol.start(
            "2", "0.1", "5", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
        )
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        assert protocol.snapshot.sample_count == 0
        assert protocol._sample_timer.remainingTime() >= 900

        now[0] += 0.4
        protocol.pause()
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.PAUSED)
        paused_elapsed = protocol.snapshot.elapsed_active_s
        now[0] += 10.0
        protocol._complete_resume_quiescence()
        assert protocol.snapshot.resume_ready
        writes_before_resume = len(fake.writes)

        assert protocol.resume(REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION)
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.RUNNING)
        writes_after_stabilization = len(fake.writes)
        qapp.processEvents()
        time.sleep(0.05)
        qapp.processEvents()

        assert len(fake.writes) == writes_after_stabilization
        assert writes_after_stabilization > writes_before_resume
        assert protocol.snapshot.sample_count == 0
        assert protocol.snapshot.elapsed_active_s == pytest.approx(paused_elapsed)
        assert protocol._sample_epoch_active_s == pytest.approx(paused_elapsed)
        assert protocol._next_sample_slot == 1
        assert protocol._sample_timer.remainingTime() >= 850

        protocol._sample_timer.stop()
        now[0] += 1.0
        protocol._submit_sample()
        wait_until(qapp, lambda: protocol.snapshot.sample_count == 1)
        assert protocol.snapshot.missed_samples == 0
        assert protocol._elapsed_active() == pytest.approx(paused_elapsed + 1.0)
        assert fake.writes.count(b"OP1 1\n") == 2

        protocol.stop("resume scheduling regression cleanup")
        wait_until(qapp, lambda: protocol.snapshot.state is ProtocolState.COMPLETED)
        assert fake.writes.count(b"OP1 0\n") == 2
    finally:
        assert connection.shutdown(timeout_s=1)
