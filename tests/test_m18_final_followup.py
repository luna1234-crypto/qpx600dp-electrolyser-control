"""Final M18 removal, input-safety, live-plot and persistence regressions."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import (
    QApplication, QComboBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)
import pytest

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.app.settings import ApplicationSettings, SettingsStore
from qpx_electrolyser.data.csv_logging import CSV_COLUMNS
from qpx_electrolyser.gui.input_guard import WheelValueGuard, protect_wheel_value_widgets
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.gui.plots.lsv import LsvPlotPoint, _LsvCanvas
from qpx_electrolyser.gui.plots.polarisation import PolarisationPlotPoint, _Canvas
from qpx_electrolyser.gui.plots.realtime import PlotPoint, PlotSnapshot, _PlotCanvas
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.safety.model import PROJECT_SAFETY_LIMITS, SafetyLimits


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wheel(position: QPointF, angle_y: int) -> QWheelEvent:
    return QWheelEvent(
        position, position, QPoint(), QPoint(0, angle_y),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate, False,
    )


def _mouse(
    event_type: QMouseEvent.Type,
    position: QPointF,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> QMouseEvent:
    return QMouseEvent(
        event_type, position, position, position, button, buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def test_m15b_modules_and_menu_entry_are_removed(
    qapp: QApplication, tmp_path: Path,
) -> None:
    assert importlib.util.find_spec("qpx_electrolyser.analysis.m15_results") is None
    assert importlib.util.find_spec("qpx_electrolyser.gui.results_plotter") is None
    connection = ConnectionController(lambda: SimulatedPowerSupply())
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    try:
        menu_text = [action.text() for action in window.menuBar().actions()]
        nested_text = [
            item.text()
            for action in window.menuBar().actions()
            if action.menu() is not None
            for item in action.menu().actions()
        ]
        assert not any("Plotter" in text or "Results" in text for text in menu_text + nested_text)
        assert not hasattr(window, "_results_plotter_window")
    finally:
        window.close(); connection.shutdown(2)


def test_old_settings_with_m15b_keys_are_ignored_safely(tmp_path: Path) -> None:
    data = tmp_path / "data"; data.mkdir()
    settings_path = tmp_path / "settings.json"
    payload = ApplicationSettings.defaults(data).to_json_object()
    payload["m15b_plotter"] = {
        "last_configuration": "preserve-existing-file.json",
        "axis": {"unsafe_unknown_future_key": object.__name__},
    }
    payload["results_plotter_configuration"] = "legacy.json"
    settings_path.write_text(json.dumps(payload), encoding="utf-8")
    result = SettingsStore(settings_path).load(default_data_directory=data)
    assert result.settings.data_export_directory == str(data.resolve())
    assert result.settings.safety_limits[1] == PROJECT_SAFETY_LIMITS
    assert not result.warnings


def test_raw_csv_schema_remains_complete_and_unchanged() -> None:
    assert CSV_COLUMNS == (
        "timestamp_iso", "elapsed_seconds", "channel", "programmed_voltage_v",
        "programmed_current_limit_a", "measured_voltage_v", "measured_current_a",
        "voltage_range", "output_state", "connection_state", "session_id",
        "sample_status", "error", "protocol_name", "protocol_state", "scan_name",
        "scan_step_index", "scan_total_steps", "scan_commanded_setpoint",
        "measured_power_w", "current_input_mode", "active_area_cm2",
        "commanded_current_density_a_cm2", "scan_rate_mv_per_s", "scan_step_mv",
        "scan_step_duration_s", "scan_settling_time_s", "scan_measurements_per_point",
        "measured_current_density_a_cm2", "scan_point_valid", "scan_point_result_index",
        "polarisation_averaging_window_s", "polarisation_voltage_sd_v",
        "polarisation_sample_count", "polarisation_active_start_s",
        "polarisation_active_end_s", "sequence_name", "sequence_run_id",
        "sequence_stage_index", "sequence_stage_total", "sequence_stage_type",
        "sequence_stage_state", "sequence_transition_policy", "sequence_handoff_state",
    )


def test_wheel_guard_scrolls_page_without_changing_closed_values(qapp: QApplication) -> None:
    scroll = QScrollArea(); scroll.resize(300, 160)
    content = QWidget(); layout = QVBoxLayout(content)
    spin = QSpinBox(); spin.setRange(0, 20); spin.setValue(7)
    combo = QComboBox(); combo.addItems(("one", "two", "three")); combo.setCurrentIndex(1)
    layout.addWidget(spin); layout.addWidget(combo)
    spacer = QWidget(); spacer.setMinimumHeight(700); layout.addWidget(spacer)
    scroll.setWidget(content); scroll.setWidgetResizable(True); scroll.show(); qapp.processEvents()
    guard = WheelValueGuard(scroll); protect_wheel_value_widgets(scroll, guard)
    scroll.verticalScrollBar().setValue(200)
    before_scroll = scroll.verticalScrollBar().value()
    QApplication.sendEvent(spin, _wheel(QPointF(5, 5), -120))
    QApplication.sendEvent(combo, _wheel(QPointF(5, 5), -120))
    assert spin.value() == 7
    assert combo.currentIndex() == 1
    assert scroll.verticalScrollBar().value() > before_scroll
    combo.showPopup(); qapp.processEvents()
    assert guard.eventFilter(combo, _wheel(QPointF(5, 5), -120)) is False
    combo.hidePopup(); scroll.close()


def _render(canvas: QWidget, qapp: QApplication) -> None:
    canvas.resize(640, 300); canvas.show(); canvas.repaint(); qapp.processEvents()


def _exercise_zoom_and_tooltip(canvas, qapp: QApplication) -> None:  # type: ignore[no-untyped-def]
    assert canvas._display_points
    point = canvas._display_points[0].position
    canvas.mousePressEvent(_mouse(
        QMouseEvent.Type.MouseButtonPress, point,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
    ))
    assert "Measured voltage" in canvas.last_tooltip_text
    assert "Measured current" in canvas.last_tooltip_text
    area = canvas._plot_area
    start = QPointF(area.left() + area.width() * 0.2, area.top() + area.height() * 0.2)
    end = QPointF(area.left() + area.width() * 0.8, area.top() + area.height() * 0.8)
    canvas.mousePressEvent(_mouse(
        QMouseEvent.Type.MouseButtonPress, start,
        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
    ))
    assert canvas._rubber_band.isVisible()
    canvas.mouseMoveEvent(_mouse(
        QMouseEvent.Type.MouseMove, end,
        Qt.MouseButton.NoButton, Qt.MouseButton.RightButton,
    ))
    canvas.mouseReleaseEvent(_mouse(
        QMouseEvent.Type.MouseButtonRelease, end,
        Qt.MouseButton.RightButton, Qt.MouseButton.NoButton,
    ))
    assert canvas.zoom_view != (0.0, 1.0, 0.0, 1.0)
    assert not canvas._rubber_band.isVisible()
    canvas.reset_zoom()
    assert canvas.zoom_view == (0.0, 1.0, 0.0, 1.0)


def test_all_live_plot_canvases_support_zoom_and_point_tooltips(qapp: QApplication) -> None:
    time_canvas = _PlotCanvas(1)
    time_canvas.apply_snapshot(PlotSnapshot(
        channel=1,
        points=(PlotPoint(0, 1, 0.01, 1), PlotPoint(10, 2, 0.02, 1)),
    ))
    _render(time_canvas, qapp); _exercise_zoom_and_tooltip(time_canvas, qapp)
    assert "CH1" in time_canvas.last_tooltip_text

    lsv_canvas = _LsvCanvas(2)
    lsv_canvas.set_points((
        LsvPlotPoint(1, 0.01, 0.01, 1), LsvPlotPoint(2, 0.02, 0.02, 2),
    ))
    _render(lsv_canvas, qapp); _exercise_zoom_and_tooltip(lsv_canvas, qapp)
    assert "CH2 | Step" in lsv_canvas.last_tooltip_text

    polar_canvas = _Canvas(1)
    polar_canvas.set_points((
        PolarisationPlotPoint(0.01, 1, 1, 0.01, 30),
        PolarisationPlotPoint(0.02, 2, 2, 0.02, 60),
    ))
    _render(polar_canvas, qapp); _exercise_zoom_and_tooltip(polar_canvas, qapp)
    assert "Elapsed: 30" in polar_canvas.last_tooltip_text
    time_canvas.close(); lsv_canvas.close(); polar_canvas.close()


def test_safety_limits_restore_independently_and_editor_starts_hidden(
    qapp: QApplication, tmp_path: Path,
) -> None:
    data = tmp_path / "data"; data.mkdir()
    settings = ApplicationSettings(
        data_export_directory=str(data),
        safety_limits={
            1: SafetyLimits(2, 0.3, 0.5, 0.2),
            2: SafetyLimits(3, 0.4, 0.8, 0.3),
        },
        safety_splitter_sizes=(0, 900),
    )
    connection = ConnectionController(lambda: SimulatedPowerSupply())
    window = MainWindow(
        tmp_path / "application.log", connection, data_directory=data, settings=settings,
    )
    try:
        assert connection.snapshot.state is ConnectionState.DISCONNECTED
        assert window.safety_validator.user_limits(1) == settings.safety_limits[1]
        assert window.safety_validator.user_limits(2) == settings.safety_limits[2]
        assert window.main_vertical_splitter.sizes()[0] == 0
        assert not window.safety_panel.isVisibleTo(window)
        persisted = window._current_settings()
        assert persisted.safety_splitter_sizes[0] > 0
        assert persisted.safety_limits == settings.safety_limits
        window._restore_safety_panel()
        assert window.main_vertical_splitter.sizes()[0] > 0
    finally:
        window.close(); connection.shutdown(2)
