"""Guarded real-mode integration using injected fake serial ports only."""

from collections import deque
from collections.abc import Callable, Iterator
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
import pytest

from qpx_electrolyser.app.channel_controller import ChannelSnapshot, ManualChannelController
from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.gui.channel_panel import ChannelPanel
from qpx_electrolyser.instrument.qpx600dp import QPX600DPDriver
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


def initial_responses(
    *, identity: bytes = b"THURLBY THANDAR,QPX600DP,FAKE,1.00\r\n",
) -> list[bytes]:
    return [identity, *channel_responses(1), *channel_responses(2)]


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
