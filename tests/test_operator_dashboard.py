"""Final compact operator-dashboard presentation regressions."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QApplication
import pytest

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.gui.operator_dashboard import EXPERIMENT_ITEMS, NAVIGATION_ITEMS
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _window(tmp_path: Path) -> tuple[ConnectionController, MainWindow]:
    connection = ConnectionController(lambda: SimulatedPowerSupply())
    window = MainWindow(tmp_path / "app.log", connection, data_directory=tmp_path)
    window.show()
    return connection, window


def test_navigation_summary_cards_and_scaling_safe_geometry(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, window = _window(tmp_path)
    try:
        assert tuple(window.dashboard.navigation.item(i).text() for i in range(6)) == NAVIGATION_ITEMS
        assert window.experiment_selector.experiment_selector.count() == len(EXPERIMENT_ITEMS)
        assert window.channel_workspaces[1].isVisibleTo(window)
        assert window.channel_workspaces[2].isVisibleTo(window)
        assert window.always_visible_header.isVisibleTo(window)
        assert window.safety_panel.emergency_button.isVisibleTo(window)
        for size in ((1280, 960), (1024, 768), (760, 540)):
            window.resize(*size); qapp.processEvents()
            assert window.dashboard.navigation.isVisibleTo(window)
            assert window.always_visible_header.isVisibleTo(window)
    finally:
        window.close(); connection.shutdown(2)


def test_one_experiment_editor_and_channel_routing(qapp: QApplication, tmp_path: Path) -> None:
    connection, window = _window(tmp_path)
    try:
        for channel in (1, 2):
            for index in range(len(EXPERIMENT_ITEMS)):
                window._show_experiment(channel, index)
                qapp.processEvents()
                stack = window.experiment_selector.editor_stack
                assert stack.currentIndex() == (channel - 1) * len(EXPERIMENT_ITEMS) + index
                visible_pages = sum(stack.widget(i).isVisibleTo(window) for i in range(stack.count()))
                assert visible_pages == 1
    finally:
        window.close(); connection.shutdown(2)


def test_vertical_live_graphs_synchronise_time_zoom_and_maximise(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, window = _window(tmp_path)
    try:
        window.dashboard.show_page("Live Data"); qapp.processEvents()
        assert window.live_data_page.splitter.orientation() is Qt.Orientation.Vertical
        first = window.plot_panels[1].canvas
        second = window.plot_panels[2].canvas
        first._zoom_view = (0.2, 0.8, 0.1, 0.9)
        first.zoom_changed.emit(0.2, 0.8)
        assert second.zoom_view[:2] == (0.2, 0.8)
        assert second.zoom_view[2:] == (0.0, 1.0)
        window.live_data_page.toggle_maximise(1); qapp.processEvents()
        assert window.live_data_page._containers[1].isVisibleTo(window)
        assert not window.live_data_page._containers[2].isVisibleTo(window)
        window.live_data_page.toggle_maximise(1); qapp.processEvents()
        assert window.live_data_page._containers[2].isVisibleTo(window)
    finally:
        window.close(); connection.shutdown(2)


def test_offline_plotter_absent_and_old_keys_do_not_create_entry(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, window = _window(tmp_path)
    try:
        all_menu_text = [action.text() for action in window.menuBar().actions()]
        for menu_action in window.menuBar().actions():
            menu = menu_action.menu()
            if menu is not None:
                all_menu_text.extend(action.text() for action in menu.actions())
        assert not any("Plotter" in text or "Scientific" in text for text in all_menu_text)
        assert not hasattr(window, "results_plotter")
    finally:
        window.close(); connection.shutdown(2)
