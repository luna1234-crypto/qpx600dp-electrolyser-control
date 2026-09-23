"""Qt integration tests for the simulation-only connection controls."""

from collections.abc import Callable, Iterator
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import pytest

from cpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from cpx_electrolyser.gui.main_window import MainWindow
from cpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from cpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from cpx_electrolyser.instrument.simulator_config import SimulatorConfig


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def wait_until(qapp: QApplication, predicate: Callable[[], bool], timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("Timed out waiting for Qt state change")


def test_connection_panel_is_responsive_and_tracks_simulator_lifecycle(
    qapp: QApplication, tmp_path: Path,
) -> None:
    controller = ConnectionController(
        lambda: SimulatedPowerSupply(SimulatorConfig(communication_delay_s=0.05))
    )
    window = MainWindow(tmp_path / "application.log", controller)
    window.show()
    panel = window.connection_panel
    try:
        assert panel.simulation_checkbox.isChecked()
        assert panel.connect_button.isEnabled()
        assert not panel.disconnect_button.isEnabled()
        assert panel.identification_value.text() == "Unavailable"

        heartbeat: list[bool] = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        before = time.monotonic()
        panel.connect_button.click()
        assert time.monotonic() - before < 0.05
        assert controller.snapshot.state is ConnectionState.CONNECTING
        assert not panel.connect_button.isEnabled()
        wait_until(
            qapp,
            lambda: bool(heartbeat) and controller.snapshot.state is ConnectionState.CONNECTED,
        )
        assert panel.disconnect_button.isEnabled()
        assert not panel.simulation_checkbox.isEnabled()
        assert panel.identification_value.text().startswith("SIMULATOR,CPX400DP")

        panel.disconnect_button.click()
        assert controller.snapshot.state is ConnectionState.DISCONNECTING
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.DISCONNECTED)
        assert panel.connect_button.isEnabled()
        assert panel.identification_value.text() == "Unavailable"

        panel.simulation_checkbox.setChecked(False)
        assert not controller.snapshot.simulation_mode
        assert not panel.connect_button.isEnabled()
        assert "not available" in panel.detail_label.text()
    finally:
        window.close()
        assert controller.shutdown(timeout_s=1.0)


def test_controller_reports_failure_cleans_backend_and_allows_retry(
    qapp: QApplication,
) -> None:
    class FailingIdentificationSimulator(SimulatedPowerSupply):
        def __init__(self) -> None:
            super().__init__(SimulatorConfig(communication_delay_s=0))
            self.disconnect_calls = 0

        def identify(self) -> str:
            raise RuntimeError("injected failure")

        def disconnect(self) -> None:
            self.disconnect_calls += 1
            super().disconnect()

    failing = FailingIdentificationSimulator()
    healthy = SimulatedPowerSupply(SimulatorConfig(communication_delay_s=0))
    backends: Iterator[PowerSupplyInterface] = iter((failing, healthy))
    controller = ConnectionController(lambda: next(backends))
    errors: list[str] = []
    controller.error_reported.connect(errors.append)
    try:
        controller.request_connect()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.ERROR)
        assert failing.disconnect_calls == 1
        assert not failing.is_connected()
        assert errors and "retry" in errors[-1]

        controller.request_connect()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.CONNECTED)
        assert controller.snapshot.instrument_id is not None
        controller.request_disconnect()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.DISCONNECTED)
    finally:
        assert controller.shutdown(timeout_s=1.0)
