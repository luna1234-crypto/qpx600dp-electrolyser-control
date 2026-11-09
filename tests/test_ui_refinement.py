"""Milestone 18 channel workspace, focus and routing regressions."""

from __future__ import annotations

import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
import pytest

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.instrument.simulator_config import SimulatorConfig


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait(qapp: QApplication, predicate, timeout: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("Timed out waiting for GUI state")


def _window(qapp: QApplication, tmp_path: Path) -> tuple[ConnectionController, MainWindow]:
    connection = ConnectionController(
        lambda: SimulatedPowerSupply(SimulatorConfig(communication_delay_s=0.001))
    )
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    window.show()
    connection.request_connect()
    _wait(
        qapp,
        lambda: connection.snapshot.state is ConnectionState.CONNECTED
        and all(item.snapshot.voltage_range_name for item in window.channel_controllers.values()),
    )
    return connection, window


def test_always_visible_header_channel_tabs_and_collapsible_plots(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, window = _window(qapp, tmp_path)
    try:
        assert window.safety_panel.parentWidget() is window.main_vertical_splitter
        assert window.safety_panel.emergency_button.parentWidget() is window.always_visible_header
        assert window.connection_panel.parentWidget() is window.always_visible_header
        assert window.always_visible_header.parentWidget() is window.centralWidget()
        assert window.content_scroll.parentWidget() is window.main_vertical_splitter
        for channel in (1, 2):
            workspace = window.channel_workspaces[channel]
            assert workspace.tabs.count() == 7
            assert [workspace.tabs.tabText(index) for index in range(7)] == [
                "Manual", "Chronopotentiometry (CP)", "Chronoamperometry (CA)", "Voltage Scan", "Current Scan",
                "Polarisation", "Custom Sequence",
            ]
        original = window.main_vertical_splitter.sizes()
        window._collapse_safety_panel()
        assert window.main_vertical_splitter.sizes()[0] == 0
        window._restore_safety_panel()
        assert window.main_vertical_splitter.sizes()[0] > 0
        window._apply_theme("dark")
        assert window.dark_theme_action.isChecked()
        assert original[1] > 0
        section = window.plot_sections["time"]
        section.setChecked(False)
        assert not section.splitter.isVisible()
        section.setChecked(True)
        assert section.splitter.isVisible()
    finally:
        window.close(); connection.shutdown(2)


def test_ch2_cv_start_routes_to_ch2_and_keeps_tab_focus_and_scroll(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection, window = _window(qapp, tmp_path)
    calls: list[tuple[object, ...]] = []
    try:
        workspace = window.channel_workspaces[2]
        workspace.tabs.setCurrentIndex(2)
        panel = window.voltage_protocol_panels[2]
        panel.target_voltage_input.setFocus()
        before_scroll = window.content_scroll.verticalScrollBar().value()
        monkeypatch.setattr(
            window,
            "_start_voltage_protocol",
            lambda *args: calls.append(args),
        )
        assert panel.start_button.isEnabled()
        panel.start_button.click()
        qapp.processEvents()
        assert calls and calls[0][0] == 2
        assert workspace.tabs.currentIndex() == 2
        assert window.content_scroll.verticalScrollBar().value() == before_scroll
        focus = QApplication.focusWidget()
        assert focus is not None
        assert panel is focus or panel.isAncestorOf(focus)
        assert not window.sequence_panels[2].isAncestorOf(focus)
        assert window.channel_workspaces[1].tabs.currentIndex() == 0
    finally:
        window.close(); connection.shutdown(2)


def test_channel_status_is_independent_and_reports_atomic_power(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, window = _window(qapp, tmp_path)
    try:
        ch1 = window.channel_workspaces[1]
        ch2 = window.channel_workspaces[2]
        assert "Output: OFF" in ch1.output_value.text()
        assert "Output: OFF" in ch2.output_value.text()
        assert "P 0 W" in ch1.measurements_value.text()
        window.channel_controllers[1].request_set_voltage("2")
        _wait(qapp, lambda: not window.channel_controllers[1].snapshot.pending)
        assert ch2.protocol_value.text() == "Protocol: IDLE"
    finally:
        window.close(); connection.shutdown(2)

