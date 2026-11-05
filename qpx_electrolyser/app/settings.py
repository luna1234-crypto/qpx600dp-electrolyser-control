"""Versioned, atomic per-user configuration persistence for Milestone 17."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from qpx_electrolyser.app.paths import default_paths
from qpx_electrolyser.safety.model import (
    PROJECT_SAFETY_LIMITS,
    SafetyLimits,
    SafetyValidationError,
    SafetyValidator,
)


SETTINGS_SCHEMA_VERSION = 1
_PORT_PATTERN = re.compile(r"COM[1-9]\d*", re.IGNORECASE)


class SettingsError(RuntimeError):
    """A configuration could not be written atomically."""


@dataclass(frozen=True, slots=True)
class WindowGeometry:
    x: int = 80
    y: int = 60
    width: int = 1280
    height: int = 960


@dataclass(frozen=True, slots=True)
class PlotDisplayPreferences:
    show_time_plots: bool = True
    show_lsv_plots: bool = True
    show_polarisation_plots: bool = True


@dataclass(frozen=True, slots=True)
class ApplicationSettings:
    schema_version: int = SETTINGS_SCHEMA_VERSION
    manual_port: str = ""
    data_export_directory: str = field(
        default_factory=lambda: str(default_paths().data_directory.resolve())
    )
    safety_limits: dict[int, SafetyLimits] = field(
        default_factory=lambda: {
            1: PROJECT_SAFETY_LIMITS,
            2: PROJECT_SAFETY_LIMITS,
        }
    )
    default_active_area_cm2: float = 1.0
    default_sampling_interval_s: float = 1.0
    simulation_mode: bool = True
    window_geometry: WindowGeometry = field(default_factory=WindowGeometry)
    plot_display: PlotDisplayPreferences = field(default_factory=PlotDisplayPreferences)

    @classmethod
    def defaults(cls, data_directory: Path | None = None) -> "ApplicationSettings":
        return cls(
            data_export_directory=str(
                Path(data_directory or default_paths().data_directory).resolve()
            )
        )

    def to_json_object(self) -> dict[str, Any]:
        return {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "manual_port": self.manual_port,
            "data_export_directory": self.data_export_directory,
            "safety_limits": {
                str(channel): asdict(limits)
                for channel, limits in sorted(self.safety_limits.items())
            },
            "default_active_area_cm2": self.default_active_area_cm2,
            "default_sampling_interval_s": self.default_sampling_interval_s,
            "simulation_mode": self.simulation_mode,
            "window_geometry": asdict(self.window_geometry),
            "plot_display": asdict(self.plot_display),
        }


@dataclass(frozen=True, slots=True)
class SettingsLoadResult:
    settings: ApplicationSettings
    warnings: tuple[str, ...] = ()


def default_settings_path() -> Path:
    """Return an OS-appropriate per-user location without touching the filesystem."""
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "QPX600DP Lab" / "QPX600DP Electrolyser" / "settings.json"


def normalize_window_geometry(
    geometry: WindowGeometry,
    available_screens: tuple[tuple[int, int, int, int], ...],
) -> WindowGeometry:
    """Keep a restored window usable when monitors have moved or disappeared."""
    width = min(max(int(geometry.width), 760), 7680)
    height = min(max(int(geometry.height), 540), 4320)
    if not available_screens:
        return WindowGeometry(geometry.x, geometry.y, width, height)
    for left, top, screen_width, screen_height in available_screens:
        right = left + screen_width
        bottom = top + screen_height
        if (
            geometry.x + min(width, 100) > left
            and geometry.x < right
            and geometry.y + min(height, 60) > top
            and geometry.y < bottom
        ):
            return WindowGeometry(geometry.x, geometry.y, width, height)
    left, top, screen_width, screen_height = available_screens[0]
    width = min(width, screen_width)
    height = min(height, screen_height)
    return WindowGeometry(
        left + max(0, (screen_width - width) // 2),
        top + max(0, (screen_height - height) // 2),
        width,
        height,
    )


class SettingsStore:
    """Load tolerant settings and replace the file atomically on save."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or default_settings_path())

    def load(self, *, default_data_directory: Path | None = None) -> SettingsLoadResult:
        defaults = ApplicationSettings.defaults(default_data_directory)
        if not self.path.exists():
            return SettingsLoadResult(defaults)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("root must be a JSON object")
            migrated = self._migrate(raw)
            return self._parse(migrated, defaults)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            return SettingsLoadResult(
                defaults,
                (f"Settings were unreadable and documented defaults were used: {error}",),
            )

    def save(self, settings: ApplicationSettings) -> None:
        payload = json.dumps(
            settings.to_json_object(), indent=2, sort_keys=True, allow_nan=False
        ) + "\n"
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except (OSError, TypeError, ValueError) as error:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise SettingsError(f"Settings were not saved atomically: {error}") from error

    def reset(self, *, default_data_directory: Path | None = None) -> ApplicationSettings:
        settings = ApplicationSettings.defaults(default_data_directory)
        self.save(settings)
        return settings

    @staticmethod
    def _migrate(raw: Mapping[str, Any]) -> dict[str, Any]:
        version = raw.get("schema_version", 0)
        if version == SETTINGS_SCHEMA_VERSION:
            return dict(raw)
        if version == 0:
            return {
                "schema_version": SETTINGS_SCHEMA_VERSION,
                "manual_port": raw.get("port", raw.get("manual_port", "")),
                "data_export_directory": raw.get(
                    "output_directory", raw.get("data_export_directory", "")
                ),
                "safety_limits": raw.get("safety_limits", {}),
                "default_active_area_cm2": raw.get("active_area_cm2", 1.0),
                "default_sampling_interval_s": raw.get("sampling_interval_s", 1.0),
                "simulation_mode": raw.get("simulation_mode", True),
                "window_geometry": raw.get("window_geometry", {}),
                "plot_display": raw.get("plot_display", {}),
            }
        raise ValueError(f"unsupported settings schema version {version!r}")

    @staticmethod
    def _parse(
        raw: Mapping[str, Any], defaults: ApplicationSettings
    ) -> SettingsLoadResult:
        warnings: list[str] = []

        port_value = raw.get("manual_port", "")
        port = port_value.strip().upper() if isinstance(port_value, str) else ""
        if port and not _PORT_PATTERN.fullmatch(port):
            warnings.append("Invalid persisted COM port was discarded.")
            port = ""

        directory_value = raw.get("data_export_directory")
        directory = defaults.data_export_directory
        if isinstance(directory_value, str) and directory_value.strip():
            candidate = Path(directory_value).expanduser()
            if candidate.is_dir():
                directory = str(candidate.resolve())
            else:
                warnings.append("Missing persisted data/export directory was replaced by default.")

        limits: dict[int, SafetyLimits] = {}
        raw_limits = raw.get("safety_limits", {})
        if not isinstance(raw_limits, Mapping):
            raw_limits = {}
        validator = SafetyValidator()
        for channel in (1, 2):
            fallback = defaults.safety_limits[channel]
            item = raw_limits.get(str(channel), raw_limits.get(channel))
            try:
                if not isinstance(item, Mapping):
                    raise ValueError("missing channel limits")
                candidate = SafetyLimits(
                    float(item["max_voltage_v"]),
                    float(item["max_current_a"]),
                    float(item["max_power_w"]),
                    float(item["max_current_density_a_cm2"]),
                )
                validator.set_user_limits(channel, candidate)
                limits[channel] = validator.user_limits(channel)
            except (KeyError, TypeError, ValueError, SafetyValidationError) as error:
                limits[channel] = fallback
                if item is not None:
                    warnings.append(f"CH{channel} persisted safety limits were rejected: {error}")

        area = SettingsStore._finite_range(
            raw.get("default_active_area_cm2"),
            defaults.default_active_area_cm2,
            0.0,
            1_000_000.0,
            "active area",
            warnings,
            lower_inclusive=False,
        )
        interval = SettingsStore._finite_range(
            raw.get("default_sampling_interval_s"),
            defaults.default_sampling_interval_s,
            0.1,
            3600.0,
            "sampling interval",
            warnings,
        )
        simulation_mode = raw.get("simulation_mode", True)
        if type(simulation_mode) is not bool:
            warnings.append("Invalid persisted mode preference was replaced by Simulation Mode.")
            simulation_mode = True

        geometry = SettingsStore._geometry(raw.get("window_geometry"), defaults, warnings)
        plots = SettingsStore._plots(raw.get("plot_display"), warnings)
        return SettingsLoadResult(ApplicationSettings(
            manual_port=port,
            data_export_directory=directory,
            safety_limits=limits,
            default_active_area_cm2=area,
            default_sampling_interval_s=interval,
            simulation_mode=simulation_mode,
            window_geometry=geometry,
            plot_display=plots,
        ), tuple(warnings))

    @staticmethod
    def _finite_range(
        value: Any,
        fallback: float,
        lower: float,
        upper: float,
        label: str,
        warnings: list[str],
        *,
        lower_inclusive: bool = True,
    ) -> float:
        try:
            number = float(value)
            valid_lower = number >= lower if lower_inclusive else number > lower
            if not math.isfinite(number) or not valid_lower or number > upper:
                raise ValueError
            return number
        except (TypeError, ValueError):
            warnings.append(f"Invalid persisted {label} was replaced by default.")
            return fallback

    @staticmethod
    def _geometry(
        value: Any, defaults: ApplicationSettings, warnings: list[str]
    ) -> WindowGeometry:
        try:
            if not isinstance(value, Mapping):
                raise ValueError
            geometry = WindowGeometry(
                int(value["x"]), int(value["y"]),
                int(value["width"]), int(value["height"]),
            )
            if geometry.width <= 0 or geometry.height <= 0:
                raise ValueError
            return geometry
        except (KeyError, TypeError, ValueError):
            if value not in ({}, None):
                warnings.append("Invalid persisted window geometry was replaced by default.")
            return defaults.window_geometry

    @staticmethod
    def _plots(value: Any, warnings: list[str]) -> PlotDisplayPreferences:
        if not isinstance(value, Mapping):
            if value is not None:
                warnings.append("Invalid persisted plot preferences were replaced by default.")
            return PlotDisplayPreferences()
        fields: dict[str, bool] = {}
        for name in (
            "show_time_plots", "show_lsv_plots", "show_polarisation_plots"
        ):
            item = value.get(name, True)
            if type(item) is not bool:
                warnings.append(f"Invalid persisted plot preference {name} was reset.")
                item = True
            fields[name] = item
        return PlotDisplayPreferences(**fields)
