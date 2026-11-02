"""Independent Simulation Mode constant-voltage protocol controllers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject

from qpx_electrolyser.app.connection_controller import ConnectionController
from qpx_electrolyser.data.csv_logging import CsvLogFile, ProtocolLogSink
from qpx_electrolyser.engine.constant_current import (
    DEFAULT_CONSTANT_CURRENT_LIMITS,
    ConstantCurrentConfig,
    ConstantCurrentController,
    ConstantCurrentSafetyLimits,
)


REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION = "RUN-QPX600DP-CONSTANT-VOLTAGE"


@dataclass(frozen=True, slots=True)
class ConstantVoltageConfig:
    """Validated constant-voltage settings with controller compatibility aliases."""

    target_voltage_v: float
    current_limit_a: float
    duration_s: float
    interval_s: float
    voltage_range_name: str

    @property
    def voltage_compliance_v(self) -> float:
        return self.target_voltage_v

    @property
    def target_current_a(self) -> float:
        return self.current_limit_a


class ConstantVoltageController(ConstantCurrentController):
    """Run CV using the Milestone 9 queue, timing, logging and cleanup state machine."""

    def __init__(
        self,
        channel: int,
        connection: ConnectionController,
        parent: QObject | None = None,
        *,
        safety_limits: ConstantCurrentSafetyLimits = DEFAULT_CONSTANT_CURRENT_LIMITS,
        monotonic=None,
        sleep=None,
        utc_now=None,
        writer_factory=CsvLogFile,
    ) -> None:
        kwargs = {
            "safety_limits": safety_limits,
            "real_hardware_safety_limits": replace(
                safety_limits,
                min_interval_s=max(1.0, safety_limits.min_interval_s),
            ),
            "writer_factory": writer_factory,
            "protocol_name": "constant_voltage",
            "display_name": "Constant Voltage",
            "command_slug": "constant-voltage",
            "current_label": "Current limit",
            "voltage_label": "Target voltage",
            "real_hardware_confirmation_token": (
                REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION
            ),
        }
        if monotonic is not None:
            kwargs["monotonic"] = monotonic
        if sleep is not None:
            kwargs["sleep"] = sleep
        if utc_now is not None:
            kwargs["utc_now"] = utc_now
        super().__init__(channel, connection, parent, **kwargs)

    def validate_inputs(
        self,
        target_voltage: str,
        current_limit: str,
        duration: str,
        interval: str,
        voltage_range_name: str | None,
    ) -> str | None:
        return self._validate_controller_inputs(
            current_limit,
            target_voltage,
            duration,
            interval,
            voltage_range_name,
        )

    def start(
        self,
        target_voltage: str,
        current_limit: str,
        duration: str,
        interval: str,
        voltage_range_name: str | None,
        directory: Path,
        *,
        logging_sink: ProtocolLogSink | None = None,
        real_hardware_confirmation: str | None = None,
        retain_output_on_completion: bool = False,
    ) -> bool:
        return super().start(
            current_limit,
            target_voltage,
            duration,
            interval,
            voltage_range_name,
            directory,
            logging_sink=logging_sink,
            real_hardware_confirmation=real_hardware_confirmation,
            retain_output_on_completion=retain_output_on_completion,
        )

    def _validate_config(
        self,
        current_limit: str,
        target_voltage: str,
        duration: str,
        interval: str,
        voltage_range_name: str,
        *,
        current_input_mode: str = "current",
        active_area_cm2: str = "",
    ) -> ConstantVoltageConfig:
        validated: ConstantCurrentConfig = super()._validate_config(
            current_limit,
            target_voltage,
            duration,
            interval,
            voltage_range_name,
            current_input_mode=current_input_mode,
            active_area_cm2=active_area_cm2,
        )
        return ConstantVoltageConfig(
            target_voltage_v=validated.voltage_compliance_v,
            current_limit_a=validated.target_current_a,
            duration_s=validated.duration_s,
            interval_s=validated.interval_s,
            voltage_range_name=validated.voltage_range_name,
        )
