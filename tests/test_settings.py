"""Milestone 17 versioned configuration persistence and safe GUI restoration."""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
import pytest

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.app.settings import (
    ApplicationSettings,
    PlotDisplayPreferences,
    SettingsError,
    SettingsStore,
    WindowGeometry,
    normalize_window_geometry,
)
from qpx_electrolyser.engine.constant_current import ProtocolState
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.safety.model import SafetyLimits


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def settings_for(tmp_path: Path, **changes: object) -> ApplicationSettings:
    values = {
        "manual_port": "COM7",
        "data_export_directory": str(tmp_path),
        "safety_limits": {
            1: SafetyLimits(2.0, 1.0, 2.0, 0.5),
            2: SafetyLimits(4.0, 2.0, 8.0, 1.5),
        },
        "default_active_area_cm2": 2.5,
        "default_sampling_interval_s": 0.75,
        "simulation_mode": False,
        "window_geometry": WindowGeometry(120, 90, 1000, 700),
        "plot_display": PlotDisplayPreferences(True, False, True),
    }
    values.update(changes)
    return ApplicationSettings(**values)


def test_versioned_save_restart_restore_and_fixed_schema(tmp_path: Path) -> None:
    path = tmp_path / "user" / "settings.json"
    store = SettingsStore(path)
    expected = settings_for(tmp_path)
    store.save(expected)
    loaded = store.load(default_data_directory=tmp_path)
    assert loaded.warnings == ()
    assert loaded.settings == expected
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    assert "instrument_serial" not in raw
    assert "confirmation_token" not in raw
    assert set(raw) == {
        "schema_version", "manual_port", "data_export_directory",
        "safety_limits", "default_active_area_cm2",
        "default_sampling_interval_s", "simulation_mode",
        "window_geometry", "plot_display", "theme", "safety_splitter_sizes",
    }


def test_missing_corrupt_and_old_settings_fall_back_or_migrate(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    missing = SettingsStore(path).load(default_data_directory=tmp_path)
    assert missing.settings.simulation_mode is True
    assert missing.warnings == ()

    path.write_text("{broken", encoding="utf-8")
    corrupt = SettingsStore(path).load(default_data_directory=tmp_path)
    assert corrupt.settings == ApplicationSettings.defaults(tmp_path)
    assert "unreadable" in corrupt.warnings[0]

    path.write_text(json.dumps({
        "port": "com9",
        "output_directory": str(tmp_path),
        "active_area_cm2": 3,
        "sampling_interval_s": 2,
        "simulation_mode": False,
    }), encoding="utf-8")
    migrated = SettingsStore(path).load(default_data_directory=tmp_path)
    assert migrated.settings.schema_version == 2
    assert migrated.settings.manual_port == "COM9"
    assert migrated.settings.default_active_area_cm2 == 3
    assert migrated.settings.default_sampling_interval_s == 2
    assert migrated.settings.simulation_mode is False


def test_invalid_values_and_out_of_capability_limits_are_rejected_independently(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    raw = settings_for(tmp_path).to_json_object()
    raw["manual_port"] = "not-a-port"
    raw["default_active_area_cm2"] = float("inf")
    raw["default_sampling_interval_s"] = -1
    raw["safety_limits"]["1"]["max_voltage_v"] = 999
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = SettingsStore(path).load(default_data_directory=tmp_path)
    assert loaded.settings.manual_port == ""
    assert loaded.settings.default_active_area_cm2 == 1
    assert loaded.settings.default_sampling_interval_s == 1
    assert loaded.settings.safety_limits[1].max_voltage_v == 10
    assert loaded.settings.safety_limits[2] == settings_for(tmp_path).safety_limits[2]
    assert any("CH1" in warning and "rejected" in warning for warning in loaded.warnings)


def test_atomic_write_failure_preserves_prior_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "settings.json"
    path.write_text("prior\n", encoding="utf-8")
    store = SettingsStore(path)
    monkeypatch.setattr(
        "qpx_electrolyser.app.settings.os.replace",
        lambda *_args: (_ for _ in ()).throw(OSError("injected replace failure")),
    )
    with pytest.raises(SettingsError, match="atomically"):
        store.save(settings_for(tmp_path))
    assert path.read_text(encoding="utf-8") == "prior\n"
    assert not list(tmp_path.glob(".settings.json.*.tmp"))


def test_off_screen_geometry_is_recentered_and_size_is_bounded() -> None:
    assert normalize_window_geometry(
        WindowGeometry(100, 80, 1000, 700), ((0, 0, 1920, 1080),)
    ) == WindowGeometry(100, 80, 1000, 700)
    assert normalize_window_geometry(
        WindowGeometry(9000, 9000, 3000, 2000), ((0, 0, 1920, 1080),)
    ) == WindowGeometry(0, 0, 1920, 1080)


def test_gui_restores_preferences_but_never_connects_or_restores_runtime_state(
    qapp: QApplication, tmp_path: Path,
) -> None:
    backend_calls: list[str] = []

    def forbidden_backend(port: str):  # type: ignore[no-untyped-def]
        backend_calls.append(port)
        raise AssertionError("restoring settings must not construct a physical backend")

    store = SettingsStore(tmp_path / "settings.json")
    settings = settings_for(tmp_path)
    controller = ConnectionController(real_backend_factory=forbidden_backend)
    window = MainWindow(
        tmp_path / "application.log",
        controller,
        settings_store=store,
        settings=settings,
    )
    try:
        snapshot = controller.snapshot
        assert snapshot.state is ConnectionState.DISCONNECTED
        assert snapshot.simulation_mode is False
        assert snapshot.port_name == "COM7"
        assert backend_calls == []
        assert all(
            protocol.snapshot.state is ProtocolState.IDLE
            for protocol in window._all_protocol_controllers()
        )
        assert all(
            channel.snapshot.output_enabled is None
            for channel in window.channel_controllers.values()
        )
        assert window.safety_validator.user_limits(1).max_voltage_v == 2
        assert window.safety_validator.user_limits(2).max_voltage_v == 4
        assert window.default_active_area.value() == 2.5
        assert window.logging_interval.value() == 0.75
        assert window.protocol_panels[1].active_area_input.text() == "2.5"
        assert window.show_lsv_plots_action.isChecked() is False
    finally:
        window.close()
        qapp.processEvents()
        assert controller.shutdown(1)


def test_gui_close_saves_and_restart_restores_channel_independence(
    qapp: QApplication, tmp_path: Path,
) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    first_controller = ConnectionController()
    first = MainWindow(
        tmp_path / "first.log",
        first_controller,
        settings_store=store,
        settings=ApplicationSettings.defaults(tmp_path),
    )
    first.safety_validator.set_user_limits(1, SafetyLimits(1, 1, 1, 1))
    first.safety_validator.set_user_limits(2, SafetyLimits(2, 2, 4, 2))
    first.default_active_area.setValue(4.5)
    first.logging_interval.setValue(2.5)
    first.show_time_plots_action.setChecked(False)
    first._apply_theme("dark")
    first.main_vertical_splitter.setSizes([240, 660])
    first_controller.set_port_name("COM12")
    first_controller.set_simulation_mode(False)
    first.close(); qapp.processEvents()
    assert first_controller.shutdown(1)

    loaded = store.load(default_data_directory=tmp_path)
    second_controller = ConnectionController()
    second = MainWindow(
        tmp_path / "second.log",
        second_controller,
        settings_store=store,
        settings=loaded.settings,
        settings_warnings=loaded.warnings,
    )
    try:
        assert second_controller.snapshot.state is ConnectionState.DISCONNECTED
        assert second_controller.snapshot.port_name == "COM12"
        assert second.safety_validator.user_limits(1).max_voltage_v == 1
        assert second.safety_validator.user_limits(2).max_voltage_v == 2
        assert second.default_active_area.value() == 4.5
        assert second.logging_interval.value() == 2.5
        assert not second.show_time_plots_action.isChecked()
        assert second.dark_theme_action.isChecked()
        assert second.main_vertical_splitter.sizes()[0] > 0
    finally:
        second.close(); qapp.processEvents()
        assert second_controller.shutdown(1)


def test_reset_defaults_is_confirmed_and_does_not_touch_plotter_configs(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    plotter_config = tmp_path / "m15_plot_configuration.json"
    plotter_config.write_text("keep", encoding="utf-8")
    controller = ConnectionController()
    window = MainWindow(
        tmp_path / "application.log", controller,
        settings_store=store, settings=settings_for(tmp_path),
    )
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args: QMessageBox.StandardButton.Reset,
    )
    try:
        window._reset_settings_to_defaults()
        assert controller.snapshot.simulation_mode is True
        assert controller.snapshot.port_name == ""
        assert window.safety_validator.user_limits(1).max_voltage_v == 10
        assert window.safety_validator.user_limits(2).max_voltage_v == 10
        assert plotter_config.read_text(encoding="utf-8") == "keep"
        assert store.load(default_data_directory=tmp_path).settings.simulation_mode is True
    finally:
        window.close(); qapp.processEvents()
        assert controller.shutdown(1)
