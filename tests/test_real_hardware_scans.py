"""Guarded M12/M13 Real Hardware scan tests using fake serial only."""

from collections import defaultdict, deque
from collections.abc import Callable
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
import pytest

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.engine.constant_current import ProtocolState
from qpx_electrolyser.engine.stepped_scan import (
    CurrentScanController,
    REAL_HARDWARE_SCAN_CONFIRMATION,
    VoltageScanController,
)
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.instrument.qpx600dp import QPX600DPDriver
from qpx_electrolyser.instrument.serial_transport import SerialTransport


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def wait_until(qapp: QApplication, predicate: Callable[[], bool], timeout_s: float = 8) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("Timed out waiting for guarded scan state")


class StatefulFakeSerial:
    """Minimal command-aware QPX600DP serial double; never touches a port."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.settings: list[dict[str, object]] = []
        self.is_open = True
        self.close_calls = 0
        self.voltage = {1: 0.0, 2: 0.0}
        self.current = {1: 0.1, 2: 0.1}
        self.output = {1: False, 2: False}
        self.range_code = {1: 1, 2: 1}
        self.fail_next: dict[str, deque[bytes | Exception]] = defaultdict(deque)
        self._last_query = ""

    def open_with(self, **settings: object) -> "StatefulFakeSerial":
        self.settings.append(settings)
        self.is_open = True
        return self

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        command = data.decode("ascii").strip()
        self._last_query = command if command.endswith("?") else ""
        if command.startswith("V") and not command.endswith("?"):
            channel, value = command[1:].split()
            self.voltage[int(channel)] = float(value)
        elif command.startswith("I") and not command.endswith("?"):
            channel, value = command[1:].split()
            self.current[int(channel)] = float(value)
        elif command.startswith("OP") and not command.endswith("?"):
            channel, value = command[2:].split()
            self.output[int(channel)] = value == "1"
        return len(data)

    def flush(self) -> None:
        pass

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        command = self._last_query
        if self.fail_next[command]:
            result = self.fail_next[command].popleft()
            if isinstance(result, Exception):
                raise result
            return result
        if command == "*IDN?":
            return b"THURLBY THANDAR,QPX600DP,REDACTED,1.00\r\n"
        if command == "*OPC?":
            return b"1\r\n"
        if command.startswith("VRANGE"):
            channel = int(command[6])
            return f"{self.range_code[channel]}\r\n".encode()
        if command.startswith("OP"):
            channel = int(command[2])
            return ("1\r\n" if self.output[channel] else "0\r\n").encode()
        if command.startswith("V") and command.endswith("O?"):
            channel = int(command[1])
            value = self.voltage[channel] if self.output[channel] else 0.0
            return f"{value:.3f}V\r\n".encode()
        if command.startswith("I") and command.endswith("O?"):
            channel = int(command[1])
            value = min(self.current[channel], self.voltage[channel] / 100.0) if self.output[channel] else 0.0
            return f"{value:.2f}A\r\n".encode()
        if command.startswith("V"):
            channel = int(command[1])
            return f"V{channel} {self.voltage[channel]:.3f}\r\n".encode()
        if command.startswith("I"):
            channel = int(command[1])
            return f"I{channel} {self.current[channel]:.2f}\r\n".encode()
        return b""

    def close(self) -> None:
        self.close_calls += 1
        self.is_open = False


def connected_real(qapp: QApplication) -> tuple[ConnectionController, StatefulFakeSerial]:
    fake = StatefulFakeSerial()

    def factory(port: str) -> QPX600DPDriver:
        assert port == "COM11"
        return QPX600DPDriver(SerialTransport(port, serial_factory=fake.open_with))

    connection = ConnectionController(real_backend_factory=factory)
    connection.set_simulation_mode(False)
    connection.set_port_name("COM11")
    connection.request_connect()
    wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    fake.writes.clear()
    return connection, fake


def start_voltage(
    scan: VoltageScanController,
    directory: Path,
    *,
    stop: str = "1",
    dwell: str = "4",
    confirmation: str | None = REAL_HARDWARE_SCAN_CONFIRMATION,
) -> bool:
    return scan.start(
        "1", stop, "0.5", "0.1", dwell, "1", "60 V", directory,
        real_hardware_confirmation=confirmation,
    )


def start_current(
    scan: CurrentScanController,
    directory: Path,
    *,
    dwell: str = "4",
    confirmation: str | None = REAL_HARDWARE_SCAN_CONFIRMATION,
) -> bool:
    return scan.start(
        "0.1", "0.1", "0.1", "2", dwell, "1", "60 V", directory,
        real_hardware_confirmation=confirmation,
    )


def test_real_voltage_scan_exact_start_order_and_exactly_once_cleanup(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, fake = connected_real(qapp)
    scan = VoltageScanController(1, connection)
    try:
        assert start_voltage(scan, tmp_path)
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.RUNNING)
        assert fake.writes[:21] == [
            b"OP1?\n", b"VRANGE1?\n", b"VRANGE1?\n", b"V1 1.000\n",
            b"I1 0.10\n", b"V1?\n", b"I1?\n", b"VRANGE1?\n",
            b"VRANGE1?\n", b"OP1?\n", b"V1?\n", b"I1?\n", b"OP1 1\n",
            b"*OPC?\n", b"OP1?\n", b"OP1?\n", b"VRANGE1?\n",
            b"V1?\n", b"I1?\n", b"V1O?\n", b"I1O?\n",
        ]
        assert fake.writes[21] == b"OP1?\n"
        scan.stop("test cleanup")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.COMPLETED)
        assert fake.writes.count(b"OP1 1\n") == 1
        assert fake.writes.count(b"OP1 0\n") == 1
        assert fake.writes.count(b"*OPC?\n") == 2
        assert not fake.output[1]
        assert all(b"2" not in item.split(b" ")[0] for item in fake.writes)
    finally:
        assert connection.shutdown(2)


def test_real_scan_confirmation_cancel_has_no_file_or_command(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, fake = connected_real(qapp)
    scan = CurrentScanController(1, connection)
    try:
        assert not start_current(scan, tmp_path, confirmation=None)
        assert "not confirmed" in scan.snapshot.message
        assert fake.writes == []
        assert not list(tmp_path.glob("*.csv"))
    finally:
        assert connection.shutdown(2)


def test_real_scan_readback_mismatch_fails_with_one_safety_cleanup(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, fake = connected_real(qapp)
    scan = VoltageScanController(1, connection)
    fake.fail_next["V1?"].append(b"V1 9.000\r\n")
    try:
        assert start_voltage(scan, tmp_path)
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.FAILED)
        assert "mismatch" in scan.snapshot.message
        assert fake.writes.count(b"OP1 1\n") == 0
        assert fake.writes.count(b"OP1 0\n") == 1
        assert not fake.output[1]
    finally:
        assert connection.shutdown(2)


def test_real_scan_range_change_is_rejected_before_any_setting_write(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, fake = connected_real(qapp)
    scan = CurrentScanController(1, connection)
    fake.range_code[1] = 2
    try:
        assert start_current(scan, tmp_path)
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.FAILED)
        assert "Voltage range changed" in scan.snapshot.message
        assert not any(item.startswith((b"V1 ", b"I1 ", b"OP1 ")) for item in fake.writes)
        assert not fake.output[1]
    finally:
        assert connection.shutdown(2)


def test_real_current_scan_pause_resume_reuses_csv_and_blocks_duplicate_resume(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, fake = connected_real(qapp)
    scan = CurrentScanController(1, connection)
    try:
        assert start_current(scan, tmp_path, dwell="12")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.RUNNING)
        path = scan.snapshot.file_path
        scan.pause()
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.PAUSED)
        assert not scan.resume(REAL_HARDWARE_SCAN_CONFIRMATION)
        wait_until(qapp, lambda: scan.snapshot.resume_ready, 3)
        assert scan.resume(REAL_HARDWARE_SCAN_CONFIRMATION)
        assert not scan.resume(REAL_HARDWARE_SCAN_CONFIRMATION)
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.RUNNING)
        assert scan.snapshot.file_path == path
        scan.stop("pause/resume test complete")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.COMPLETED)
        assert fake.writes.count(b"OP1 1\n") == 2
        assert fake.writes.count(b"OP1 0\n") == 2
        assert not fake.output[1]
    finally:
        assert connection.shutdown(2)


def test_real_scan_step_communication_failure_cleans_once_and_never_retries_on(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, fake = connected_real(qapp)
    scan = VoltageScanController(1, connection)
    try:
        assert start_voltage(scan, tmp_path, stop="1.5", dwell="1")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.RUNNING)
        fake.fail_next["VRANGE1?"].append(b"malformed\r\n")
        wait_until(qapp, lambda: scan.snapshot.state is ProtocolState.FAILED, 6)
        assert fake.writes.count(b"OP1 1\n") == 1
        assert fake.writes.count(b"OP1 0\n") == 1
        assert not fake.output[1]
    finally:
        assert connection.shutdown(2)


def test_real_scans_run_simultaneously_with_channel_isolation(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, fake = connected_real(qapp)
    ch1 = VoltageScanController(1, connection)
    ch2 = CurrentScanController(2, connection)
    try:
        assert start_voltage(ch1, tmp_path)
        assert ch2.start(
            "0.1", "0.1", "0.1", "2", "4", "1", "60 V", tmp_path,
            real_hardware_confirmation=REAL_HARDWARE_SCAN_CONFIRMATION,
        )
        wait_until(qapp, lambda: all(
            item.snapshot.state is ProtocolState.RUNNING for item in (ch1, ch2)
        ))
        ch1.stop("simultaneous test")
        ch2.stop("simultaneous test")
        wait_until(qapp, lambda: all(
            item.snapshot.state is ProtocolState.COMPLETED for item in (ch1, ch2)
        ))
        for channel in (1, 2):
            assert fake.writes.count(f"OP{channel} 1\n".encode()) == 1
            assert fake.writes.count(f"OP{channel} 0\n".encode()) == 1
            assert not fake.output[channel]
        assert ch1.snapshot.file_path != ch2.snapshot.file_path
    finally:
        assert connection.shutdown(2)


def test_real_gui_default_cancel_submits_no_scan_command(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection, fake = connected_real(qapp)
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    try:
        connection.snapshot_changed.emit(connection.snapshot)
        qapp.processEvents()
        fake.writes.clear()
        messages: list[str] = []

        def cancel(*args, **kwargs):  # type: ignore[no-untyped-def]
            messages.append(args[2])
            return QMessageBox.StandardButton.Cancel

        monkeypatch.setattr(QMessageBox, "warning", cancel)
        panel = window.voltage_scan_panels[1]
        panel.start_voltage_input.setText("1")
        panel.stop_voltage_input.setText("1.5")
        panel.step_input.setText("0.5")
        panel.current_limit_input.setText("0.1")
        panel.dwell_input.setText("1")
        panel.interval_input.setText("1")
        assert panel.start_button.isEnabled()
        panel.start_button.click()
        qapp.processEvents()
        assert fake.writes == []
        assert window.voltage_scan_controllers[1].snapshot.state is ProtocolState.IDLE
        assert messages and all(text in messages[0] for text in (
            "Channel: CH1", "Scan type: Voltage Scan", "Start / stop / step",
            "Current limit", "Dwell / interval", "Total scan steps: 2",
            "Output-On writes for this Start: exactly one", "CSV destination",
        ))
    finally:
        window.close()
        qapp.processEvents()
        assert connection.shutdown(2)
