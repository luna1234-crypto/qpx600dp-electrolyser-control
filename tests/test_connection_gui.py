"""Qt integration tests for the simulation-only connection controls."""

from collections.abc import Callable, Iterator
import os
from pathlib import Path
from threading import current_thread, main_thread
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
import pytest

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.instrument.simulator_config import SimulatorConfig


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
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
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
        assert panel.identification_value.text().startswith("SIMULATOR,QPX600DP")

        panel.disconnect_button.click()
        assert controller.snapshot.state is ConnectionState.DISCONNECTING
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.DISCONNECTED)
        assert panel.connect_button.isEnabled()
        assert panel.identification_value.text() == "Unavailable"

        monkeypatch.setattr(
            QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Yes
        )
        panel.real_hardware_radio.setChecked(True)
        assert not controller.snapshot.simulation_mode
        assert not panel.connect_button.isEnabled()
        assert "REAL HARDWARE" in panel.detail_label.text()
        assert panel.port_input.isEnabled()
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


def test_real_mode_warning_cancel_keeps_simulation_selected(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = ConnectionController()
    window = MainWindow(tmp_path / "application.log", controller)
    panel = window.connection_panel
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Cancel
    )
    try:
        panel.real_hardware_radio.click()
        qapp.processEvents()
        assert controller.snapshot.simulation_mode
        assert panel.simulation_checkbox.isChecked()
        assert not panel.real_hardware_radio.isChecked()
        assert panel.connect_button.text() == "Connect Simulator"
    finally:
        window.close()
        assert controller.shutdown(timeout_s=1)


def test_mode_cards_are_unmistakable_accessible_and_theme_safe(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = ConnectionController()
    window = MainWindow(tmp_path / "application.log", controller)
    window.show(); qapp.processEvents()
    panel = window.connection_panel
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Yes
    )
    try:
        simulation = panel.simulation_checkbox
        hardware = panel.real_hardware_radio
        for theme in ("light", "dark"):
            window._apply_theme(theme); qapp.processEvents()
            assert not simulation.grab().isNull()
            assert simulation.last_paint_state == {
                "theme": theme,
                "selected": True,
                "accent": "#0f6cbd",
                "selected_fill": "#e6f2fb" if theme == "light" else "#173a54",
                "warning_badge": False,
                "enabled": True,
            }
            assert simulation.property("selectedState") == "selected"
            assert "is selected" in simulation.accessibleDescription()
            assert hardware.property("selectedState") == "unselected"
            assert "not selected" in hardware.accessibleDescription()

        # Click the card's far edge, not its circle or label.
        QTest.mouseClick(
            hardware, Qt.MouseButton.LeftButton,
            pos=QPoint(hardware.width() - 4, hardware.height() // 2),
        )
        qapp.processEvents()
        assert hardware.isChecked()
        assert hardware.property("selectedState") == "selected"
        assert "Hardware warning" in hardware.accessibleDescription()
        assert hardware.last_paint_state["accent"] == "#ca5010"
        assert hardware.last_paint_state["warning_badge"] is True
        assert "#b42318" not in panel.mode_indicator.styleSheet().lower()
        assert controller.snapshot.state is ConnectionState.DISCONNECTED
        assert controller.snapshot.session_id == 0

        hardware.setEnabled(False); qapp.processEvents(); hardware.grab()
        assert hardware.last_paint_state["enabled"] is False
        hardware.setEnabled(True)
        hardware.setFocus(); qapp.processEvents()
        assert hardware.hasFocus()
    finally:
        window.close()
        assert controller.shutdown(timeout_s=1)


def test_command_results_and_errors_return_to_qt_without_blocking_gui(
    qapp: QApplication,
) -> None:
    controller = ConnectionController(
        lambda: SimulatedPowerSupply(SimulatorConfig(communication_delay_s=0.05))
    )
    completed: list[tuple[int, str, object, bool]] = []
    failed: list[tuple[int, str, object, bool]] = []
    controller.command_completed.connect(
        lambda request_id, name, result: completed.append(
            (request_id, name, result, current_thread() is main_thread())
        )
    )
    controller.command_failed.connect(
        lambda request_id, name, error: failed.append(
            (request_id, name, error, current_thread() is main_thread())
        )
    )

    def injected_failure(_supply: PowerSupplyInterface) -> object:
        raise RuntimeError("injected queued failure")

    try:
        controller.request_connect()
        wait_until(qapp, lambda: controller.snapshot.state is ConnectionState.CONNECTED)

        before = time.monotonic()
        ticket = controller.submit_command("identify", lambda supply: supply.identify())
        assert ticket is not None
        assert time.monotonic() - before < 0.05
        wait_until(qapp, lambda: bool(completed))
        assert completed == [
            (ticket.request_id, "identify", ticket.result(timeout=0), True)
        ]

        failed_ticket = controller.submit_command("fails", injected_failure)
        assert failed_ticket is not None
        wait_until(qapp, lambda: bool(failed))
        assert failed[0][0:2] == (failed_ticket.request_id, "fails")
        assert isinstance(failed[0][2], RuntimeError)
        assert failed[0][3]
    finally:
        assert controller.shutdown(timeout_s=1.0)
