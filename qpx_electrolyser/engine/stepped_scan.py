"""Reusable queued stepped-scan state machine for voltage and current scans."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import math
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.data.csv_logging import CsvLogFile, ProtocolLogSink
from qpx_electrolyser.engine.constant_current import (
    DEFAULT_CONSTANT_CURRENT_LIMITS,
    ConstantCurrentConfig,
    ConstantCurrentController,
    ConstantCurrentSafetyLimits,
    ProtocolSnapshot,
    ProtocolState,
    _GuardFailure,
    _OutputTransitionArmed,
    _PreparedResult,
    _StartResult,
    _matches,
)
from qpx_electrolyser.engine.current_input import CurrentInputMode, CurrentInputSelection
from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.capabilities import QPX600DP_CAPABILITIES
from qpx_electrolyser.instrument.channel_readback import ChannelReadback, read_channel
from qpx_electrolyser.instrument.connection_worker import CommandPriority
from qpx_electrolyser.instrument.exceptions import InstrumentValueError
from qpx_electrolyser.safety.model import SafetyValidator


REAL_HARDWARE_SCAN_CONFIRMATION = "RUN-QPX600DP-STEPPED-SCAN"


def _decimal(text: str, label: str) -> Decimal:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{label} is required.")
    try:
        value = Decimal(text.strip())
    except InvalidOperation as error:
        raise ValueError(f"{label} must be numeric.") from error
    if not value.is_finite():
        raise ValueError(f"{label} must be finite.")
    return value


def generate_inclusive_setpoints(
    start: str | Decimal,
    stop: str | Decimal,
    step: str | Decimal,
    *,
    axis_label: str = "Voltage",
) -> tuple[Decimal, ...]:
    """Return exact inclusive steps, adding a non-divisible endpoint once."""
    first = (
        start if isinstance(start, Decimal) else _decimal(start, f"Start {axis_label.lower()}")
    )
    last = stop if isinstance(stop, Decimal) else _decimal(
        stop, f"Stop {axis_label.lower()}"
    )
    increment = step if isinstance(step, Decimal) else _decimal(step, f"{axis_label} step")
    if increment == 0:
        raise ValueError(f"{axis_label} step must not be zero.")
    if first < last and increment < 0:
        raise ValueError(f"{axis_label} step must be positive for an ascending scan.")
    if first > last and increment > 0:
        raise ValueError(f"{axis_label} step must be negative for a descending scan.")
    if first == last:
        return (first,)

    values = [first]
    current = first
    while True:
        candidate = current + increment
        reached = candidate >= last if increment > 0 else candidate <= last
        if reached:
            if values[-1] != last:
                values.append(last)
            break
        values.append(candidate)
        current = candidate
    return tuple(values)


@dataclass(frozen=True, slots=True)
class SteppedScanConfig:
    """Axis-neutral settings consumed by the shared stepped-scan state machine."""

    setpoints: tuple[float, ...]
    fixed_voltage_v: float
    fixed_current_a: float
    dwell_s: float
    interval_s: float
    voltage_range_name: str
    scan_name: str
    current_input_mode: str = ""
    active_area_cm2: float | None = None
    current_density_setpoints_a_cm2: tuple[float, ...] = ()
    scan_step_mv: float | None = None
    scan_rate_mv_per_s: float | None = None
    settling_time_s: float = 0.0
    measurements_per_point: int = 0
    averaging_window_s: float = 0.0

    @property
    def duration_s(self) -> float:
        return len(self.setpoints) * self.dwell_s

    # Compatibility aliases used by the proven M9-M11 transition machinery.
    @property
    def voltage_compliance_v(self) -> float:
        return self.fixed_voltage_v

    @property
    def target_current_a(self) -> float:
        return self.fixed_current_a


class SteppedScanController(ConstantCurrentController):
    """Common step timing layered on the existing protocol safety state machine.

    A future current scan supplies a different axis name and validation adapter;
    queue ownership, dwell scheduling, Pause/Resume and CSV behavior remain here.
    """

    def __init__(
        self,
        channel: int,
        connection: ConnectionController,
        parent: QObject | None = None,
        *,
        axis_name: str,
        safety_limits: ConstantCurrentSafetyLimits = DEFAULT_CONSTANT_CURRENT_LIMITS,
        monotonic=None,
        utc_now=None,
        writer_factory=CsvLogFile,
        safety_validator: SafetyValidator | None = None,
    ) -> None:
        if axis_name not in {"voltage", "current"}:
            raise ValueError("scan axis must be voltage or current")
        kwargs = {
            "safety_limits": safety_limits,
            "writer_factory": writer_factory,
            "protocol_name": f"{axis_name}_scan",
            "display_name": f"{axis_name.title()} Scan",
            "command_slug": f"{axis_name}-scan",
            "current_label": "Current limit" if axis_name == "voltage" else "Scan current",
            "voltage_label": "Scan voltage" if axis_name == "voltage" else "Voltage compliance",
            "real_hardware_confirmation_token": REAL_HARDWARE_SCAN_CONFIRMATION,
            "safety_validator": safety_validator,
        }
        if monotonic is not None:
            kwargs["monotonic"] = monotonic
        if utc_now is not None:
            kwargs["utc_now"] = utc_now
        super().__init__(channel, connection, parent, **kwargs)
        self._axis_name = axis_name
        self._scan_step_index = 0
        self._pending_step_index: int | None = None
        self._next_scan_event_active_s: float | None = None

    def _validate_scan_session(self, voltage_range_name: str | None) -> str | None:
        if self._connection.snapshot.state is not ConnectionState.CONNECTED:
            return "Connect and initialize the selected instrument before starting."
        if voltage_range_name is None:
            return f"Refresh the channel before starting a {self._axis_name} scan."
        if not self._connection.snapshot.simulation_mode:
            parts = [
                part.strip()
                for part in (self._connection.snapshot.instrument_id or "").split(",")
            ]
            if len(parts) != 4 or parts[1].upper() != "QPX600DP":
                return "Real Hardware scan requires an identified QPX600DP session."
        return None

    def _require_selected_range(
        self, supply: PowerSupplyInterface, config: SteppedScanConfig
    ) -> None:
        actual = supply.read_voltage_range(self._channel)
        if actual.name != config.voltage_range_name:
            raise RuntimeError(
                f"Voltage range changed: expected {config.voltage_range_name}, "
                f"read back {actual.name}."
            )

    def _start_prepare_operation(
        self, supply: PowerSupplyInterface, config: ConstantCurrentConfig
    ) -> _PreparedResult | _GuardFailure:
        if not self._real_hardware_run:
            return super()._start_prepare_operation(supply, config)
        scan = config
        assert isinstance(scan, SteppedScanConfig)
        writes_started = False
        try:
            if supply.read_output_state(self._channel):
                return _GuardFailure(
                    "Selected output is already ON; scan start refused.",
                    cleanup_required=False,
                )
            self._require_selected_range(supply, scan)
            writes_started = True
            supply.set_voltage(self._channel, self._requested_voltage(scan))
            supply.set_current(self._channel, self._requested_current(scan))
            voltage = supply.read_set_voltage(self._channel)
            current = supply.read_set_current(self._channel)
            self._require_selected_range(supply, scan)
            self._verify_settings(voltage, current, scan)
            return _PreparedResult(voltage, current)
        except Exception as error:
            return _GuardFailure(
                str(error) or type(error).__name__, cleanup_required=writes_started
            )

    def _resume_prepare_operation(
        self, supply: PowerSupplyInterface, config: ConstantCurrentConfig
    ) -> _PreparedResult | _GuardFailure:
        if not self._real_hardware_run:
            return super()._resume_prepare_operation(supply, config)
        scan = config
        assert isinstance(scan, SteppedScanConfig)
        try:
            if supply.read_output_state(self._channel):
                return _GuardFailure(
                    "Selected output was not OFF before scan resume.",
                    cleanup_required=True,
                )
            self._require_selected_range(supply, scan)
            voltage = supply.read_set_voltage(self._channel)
            current = supply.read_set_current(self._channel)
            self._verify_settings(voltage, current, scan)
            return _PreparedResult(voltage, current)
        except Exception as error:
            return _GuardFailure(
                str(error) or type(error).__name__, cleanup_required=True
            )

    def _output_on_operation(
        self, supply: PowerSupplyInterface, config: ConstantCurrentConfig
    ) -> _StartResult | _OutputTransitionArmed | _GuardFailure:
        if self._real_hardware_run:
            try:
                scan = config
                assert isinstance(scan, SteppedScanConfig)
                self._require_selected_range(supply, scan)
            except Exception as error:
                return _GuardFailure(
                    str(error) or type(error).__name__, cleanup_required=True
                )
        return super()._output_on_operation(supply, config)

    def _requested_voltage(self, config: ConstantCurrentConfig) -> float:
        scan = config
        assert isinstance(scan, SteppedScanConfig)
        return (
            scan.setpoints[self._scan_step_index]
            if self._axis_name == "voltage"
            else scan.fixed_voltage_v
        )

    def _requested_current(self, config: ConstantCurrentConfig) -> float:
        scan = config
        assert isinstance(scan, SteppedScanConfig)
        return (
            scan.fixed_current_a
            if self._axis_name == "voltage"
            else scan.setpoints[self._scan_step_index]
        )

    def _initial_snapshot_fields(
        self, config: ConstantCurrentConfig
    ) -> dict[str, object]:
        scan = config
        assert isinstance(scan, SteppedScanConfig)
        self._next_scan_event_active_s = None
        return {
            "scan_step_index": 1,
            "scan_total_steps": len(scan.setpoints),
            "scan_commanded_setpoint": scan.setpoints[0],
        }

    def _protocol_csv_metadata(self) -> dict[str, object]:
        config = self._snapshot.config
        if not isinstance(config, SteppedScanConfig):
            return {}
        metadata: dict[str, object] = {
            "scan_name": config.scan_name,
            "scan_step_index": self._scan_step_index + 1,
            "scan_total_steps": len(config.setpoints),
            "scan_commanded_setpoint": config.setpoints[self._scan_step_index],
        }
        if config.current_input_mode:
            metadata.update({
                "current_input_mode": config.current_input_mode,
                "active_area_cm2": config.active_area_cm2,
                "commanded_current_density_a_cm2": (
                    config.current_density_setpoints_a_cm2[self._scan_step_index]
                    if config.current_density_setpoints_a_cm2 else None
                ),
            })
        return metadata

    def _step_change_operation(
        self,
        supply: PowerSupplyInterface,
        config: SteppedScanConfig,
        next_index: int,
    ) -> ChannelReadback | _GuardFailure:
        try:
            setpoint = config.setpoints[next_index]
            if self._axis_name == "voltage":
                supply.set_voltage(self._channel, setpoint)
            else:
                supply.set_current(self._channel, setpoint)
            readback = read_channel(supply, self._channel)
            voltage_resolution = QPX600DP_CAPABILITIES.voltage_range(
                config.voltage_range_name
            ).programming_resolution_v
            expected_voltage = setpoint if self._axis_name == "voltage" else config.fixed_voltage_v
            expected_current = config.fixed_current_a if self._axis_name == "voltage" else setpoint
            if not _matches(
                readback.programmed_voltage_v, expected_voltage, voltage_resolution
            ) or not _matches(
                readback.current_limit_a,
                expected_current,
                QPX600DP_CAPABILITIES.current_programming_resolution_a,
            ):
                raise RuntimeError("Scan step setpoint read-back mismatch.")
            if readback.voltage_range_name != config.voltage_range_name:
                raise RuntimeError("Scan step voltage-range read-back mismatch.")
            if not readback.output_enabled:
                raise RuntimeError("Output unexpectedly reported OFF during scan step change.")
            return readback
        except Exception as error:
            return _GuardFailure(
                f"Scan step change failed: {str(error) or type(error).__name__}",
                cleanup_required=True,
            )

    def pause(self) -> None:
        # A step update is one atomic queue operation; do not replace its ticket.
        if self._pending_request_id is not None:
            return
        super().pause()

    def resume(self, real_hardware_confirmation: str | None = None) -> bool:
        return super().resume(real_hardware_confirmation)

    @Slot()
    def _submit_sample(self) -> None:
        config = self._snapshot.config
        if self._snapshot.state is not ProtocolState.RUNNING or not isinstance(
            config, SteppedScanConfig
        ):
            return
        elapsed = self._elapsed_active()
        # A stopped QTimer may already have queued its timeout event when Pause
        # wins the race.  Resume installs a new active-time deadline before the
        # event loop can deliver that stale callback.  Never let such a callback
        # poll early or advance a step before the rebuilt deadline.
        not_before = self._next_scan_event_active_s
        if not_before is not None and elapsed + 1e-12 < not_before:
            self._schedule_next_sample()
            return
        self._next_scan_event_active_s = None
        step_end = (self._scan_step_index + 1) * config.dwell_s
        if elapsed + 1e-12 >= step_end:
            self._advance_or_complete(config)
            return
        step_start = self._scan_step_index * config.dwell_s
        step_elapsed = max(0.0, elapsed - step_start)
        current_slot = math.floor(step_elapsed / config.interval_s)
        if current_slot > self._next_sample_slot:
            self._publish(replace(
                self._snapshot,
                missed_samples=(
                    self._snapshot.missed_samples
                    + current_slot
                    - self._next_sample_slot
                ),
            ))
            self._next_sample_slot = current_slot
        ticket = self._connection.submit_command(
            f"ch{self._channel}-{self._command_slug}-sample",
            self._sample_operation,
            priority=CommandPriority.BACKGROUND,
            cancellation_key=self._sample_key,
        )
        if ticket is None:
            self._begin_cleanup(ProtocolState.FAILED, "Shared queue rejected a scan sample.")
            return
        self._pending_request_id = ticket.request_id
        self._pending_action = "sample"
        self._next_sample_slot += 1

    def _advance_or_complete(self, config: SteppedScanConfig) -> None:
        self._next_scan_event_active_s = None
        slots_per_step = math.ceil(config.dwell_s / config.interval_s - 1e-12)
        if self._next_sample_slot < slots_per_step:
            self._publish(replace(
                self._snapshot,
                missed_samples=(
                    self._snapshot.missed_samples
                    + slots_per_step
                    - self._next_sample_slot
                ),
            ))
        if self._scan_step_index + 1 >= len(config.setpoints):
            self._active_before_run_s = config.duration_s
            self._run_started_at = 0.0
            self._begin_cleanup(ProtocolState.COMPLETED, f"{self._display_name} completed")
            return
        self._active_before_run_s = (self._scan_step_index + 1) * config.dwell_s
        self._run_started_at = 0.0
        next_setpoint = config.setpoints[self._scan_step_index + 1]
        next_index = self._scan_step_index + 1
        self._pending_step_index = next_index
        self._publish(replace(
            self._snapshot,
            elapsed_active_s=self._active_before_run_s,
            remaining_active_s=max(0.0, config.duration_s - self._active_before_run_s),
            message=f"Applying scan step {self._scan_step_index + 2}/{len(config.setpoints)}: "
            f"{next_setpoint:g}",
        ))
        self._submit_action(
            "step-transition",
            lambda supply: self._step_change_operation(supply, config, next_index),
        )

    def _command_completed(self, request_id: int, name: str, result: object) -> None:
        if request_id == self._pending_request_id and self._pending_action == "step-transition":
            self._pending_request_id = None
            self._pending_action = ""
            if isinstance(result, _GuardFailure):
                self._pending_step_index = None
                self._begin_cleanup(ProtocolState.FAILED, result.message)
                return
            config = self._snapshot.config
            if (
                not isinstance(config, SteppedScanConfig)
                or not isinstance(result, ChannelReadback)
                or result.channel != self._channel
                or not result.output_enabled
            ):
                self._begin_cleanup(ProtocolState.FAILED, "Malformed scan step read-back.")
                return
            if self._pending_step_index is None:
                self._begin_cleanup(ProtocolState.FAILED, "Missing scan step transition state.")
                return
            self._scan_step_index = self._pending_step_index
            self._pending_step_index = None
            try:
                self._append(result, "step_start", ProtocolState.RUNNING)
            except (OSError, ValueError) as error:
                self._begin_cleanup(ProtocolState.FAILED, f"Protocol CSV write failed: {error}")
                return
            self.sample_received.emit(result)
            self._run_started_at = self._monotonic()
            self._sample_epoch_active_s = self._active_before_run_s
            self._next_sample_slot = 1
            self._publish(replace(
                self._snapshot,
                scan_step_index=self._scan_step_index + 1,
                scan_commanded_setpoint=config.setpoints[self._scan_step_index],
                measured_voltage_v=result.measured_voltage_v,
                measured_current_a=result.measured_current_a,
                output_enabled=True,
                sample_count=self._snapshot.sample_count + 1,
                message=f"{self._display_name} step {self._scan_step_index + 1}/"
                f"{len(config.setpoints)} running",
            ))
            self._schedule_next_sample()
            return
        super()._command_completed(request_id, name, result)

    def _schedule_next_sample(self) -> None:
        config = self._snapshot.config
        if self._snapshot.state is not ProtocolState.RUNNING or not isinstance(
            config, SteppedScanConfig
        ):
            return
        elapsed = self._elapsed_active()
        step_start = self._scan_step_index * config.dwell_s
        step_end = step_start + config.dwell_s
        if elapsed + 1e-12 >= step_end:
            self._sample_timer.start(0)
            return
        due_slot = math.floor((elapsed - step_start) / config.interval_s) + 1
        if due_slot > self._next_sample_slot:
            self._publish(replace(
                self._snapshot,
                missed_samples=(
                    self._snapshot.missed_samples
                    + due_slot
                    - self._next_sample_slot
                ),
            ))
            self._next_sample_slot = due_slot
        next_sample = step_start + self._next_sample_slot * config.interval_s
        next_event = min(next_sample, step_end)
        self._next_scan_event_active_s = next_event
        delay_s = max(0.0, next_event - elapsed)
        self._sample_timer.start(max(0, math.ceil(delay_s * 1000)))

    def _rebase_sample_schedule(self) -> None:
        config = self._snapshot.config
        if not isinstance(config, SteppedScanConfig):
            super()._rebase_sample_schedule()
            return
        elapsed = self._elapsed_active()
        step_start = self._scan_step_index * config.dwell_s
        self._sample_epoch_active_s = step_start
        step_elapsed = max(0.0, elapsed - step_start)
        # Preserve every already-consumed slot while retaining a sample that is
        # exactly due at the frozen Pause instant.  The old floor(...)+1 rule
        # skipped that boundary slot after Resume.
        due_slot = max(
            1,
            math.ceil((step_elapsed - 1e-12) / config.interval_s),
        )
        self._next_sample_slot = max(self._next_sample_slot, due_slot)
        self._next_scan_event_active_s = None


@dataclass(frozen=True, slots=True)
class StaircaseLsvPoint:
    """One representative, queue-free result derived from scheduled samples."""

    channel: int
    point_index: int
    total_points: int
    commanded_voltage_v: float
    mean_voltage_v: float | None
    mean_current_a: float | None
    mean_current_density_a_cm2: float | None
    valid: bool


class VoltageScanController(SteppedScanController):
    """Milestone 12 Voltage Sweep / Staircase LSV controller."""

    point_result_received = Signal(object)

    def __init__(
        self,
        channel: int,
        connection: ConnectionController,
        parent: QObject | None = None,
        *,
        safety_limits: ConstantCurrentSafetyLimits = DEFAULT_CONSTANT_CURRENT_LIMITS,
        monotonic=None,
        utc_now=None,
        writer_factory=CsvLogFile,
        safety_validator: SafetyValidator | None = None,
    ) -> None:
        super().__init__(
            channel,
            connection,
            parent,
            axis_name="voltage",
            safety_limits=safety_limits,
            monotonic=monotonic,
            utc_now=utc_now,
            writer_factory=writer_factory,
            safety_validator=safety_validator,
        )
        self._point_samples: list[ChannelReadback] = []
        self._point_metadata: dict[str, object] | None = None

    def validate_inputs(
        self,
        start_voltage: str,
        stop_voltage: str,
        voltage_step_mv: str,
        scan_rate_mv_per_s: str,
        current_limit: str,
        settling_time: str,
        measurements_per_point: str,
        interval: str,
        voltage_range_name: str | None,
        active_area_cm2: str = "",
    ) -> str | None:
        session_error = self._validate_scan_session(voltage_range_name)
        if session_error is not None:
            return session_error
        try:
            self._validate_scan_config(
                start_voltage,
                stop_voltage,
                voltage_step_mv,
                scan_rate_mv_per_s,
                current_limit,
                settling_time,
                measurements_per_point,
                interval,
                voltage_range_name,
                active_area_cm2,
            )
        except (ValueError, InstrumentValueError) as error:
            return str(error)
        return None

    def start(
        self,
        start_voltage: str,
        stop_voltage: str,
        voltage_step_mv: str,
        scan_rate_mv_per_s: str,
        current_limit: str,
        settling_time: str,
        measurements_per_point: str,
        interval: str,
        voltage_range_name: str | None,
        directory: Path,
        *,
        logging_sink: ProtocolLogSink | None = None,
        real_hardware_confirmation: str | None = None,
        active_area_cm2: str = "",
    ) -> bool:
        if self._snapshot.owns_channel:
            return False
        validation_error = self.validate_inputs(
            start_voltage,
            stop_voltage,
            voltage_step_mv,
            scan_rate_mv_per_s,
            current_limit,
            settling_time,
            measurements_per_point,
            interval,
            voltage_range_name,
            active_area_cm2,
        )
        if validation_error is not None:
            return self._reject(validation_error)
        if (
            not self._connection.snapshot.simulation_mode
            and real_hardware_confirmation != REAL_HARDWARE_SCAN_CONFIRMATION
        ):
            return self._reject("Real Hardware Voltage Scan start was not confirmed.")
        assert voltage_range_name is not None
        config = self._validate_scan_config(
            start_voltage,
            stop_voltage,
            voltage_step_mv,
            scan_rate_mv_per_s,
            current_limit,
            settling_time,
            measurements_per_point,
            interval,
            voltage_range_name,
            active_area_cm2,
        )
        self._scan_step_index = 0
        self._point_samples.clear()
        self._point_metadata = None
        return self._begin_validated_start(config, directory, logging_sink=logging_sink)

    def _validate_scan_config(
        self,
        start_voltage: str,
        stop_voltage: str,
        voltage_step_mv: str,
        scan_rate_mv_per_s: str,
        current_limit: str,
        settling_time: str,
        measurements_per_point: str,
        interval: str,
        voltage_range_name: str,
        active_area_cm2: str = "",
    ) -> SteppedScanConfig:
        exact_start = _decimal(start_voltage, "Start voltage")
        exact_stop = _decimal(stop_voltage, "Stop voltage")
        exact_step_mv = _decimal(voltage_step_mv, "Voltage step")
        exact_rate = _decimal(scan_rate_mv_per_s, "Scan rate")
        exact_step = exact_step_mv / Decimal("1000")
        # Reject unsupported endpoints/step grids before constructing the plan;
        # this also bounds plan size to the selected range's documented grid.
        QPX600DP_CAPABILITIES.validate_voltage(float(exact_start), voltage_range_name)
        QPX600DP_CAPABILITIES.validate_voltage(float(exact_stop), voltage_range_name)
        if exact_step_mv == 0:
            raise ValueError("Voltage step must not be zero.")
        if exact_rate <= 0:
            raise ValueError("Scan rate must be positive.")
        QPX600DP_CAPABILITIES.validate_voltage(abs(float(exact_step)), voltage_range_name)
        exact_setpoints = generate_inclusive_setpoints(
            exact_start, exact_stop, exact_step
        )
        current = QPX600DP_CAPABILITIES.validate_current(
            self._number(current_limit, "Current limit")
        )
        exact_duration = abs(exact_step_mv) / exact_rate
        if not exact_duration.is_finite() or exact_duration <= 0:
            raise ValueError("Derived step duration must be positive and finite.")
        dwell_s = float(exact_duration)
        settling_s = self._number(settling_time, "Settling time")
        if settling_s < 0:
            raise ValueError("Settling time must be non-negative.")
        if settling_s > dwell_s:
            raise ValueError("Settling time must fit inside the derived step duration.")
        try:
            measurements = int(measurements_per_point.strip())
        except (AttributeError, ValueError) as error:
            raise ValueError("Measurements per point must be a positive integer.") from error
        if str(measurements) != measurements_per_point.strip() or measurements <= 0:
            raise ValueError("Measurements per point must be a positive integer.")
        interval_s = self._number(interval, "Sampling interval")
        limits = (
            self._limits
            if self._connection.snapshot.simulation_mode
            else self._real_hardware_limits
        )
        if current > limits.max_current_a:
            raise ValueError(
                f"Current limit exceeds the {limits.max_current_a:g} A project limit."
            )
        if not 0 < dwell_s <= limits.max_duration_s:
            raise ValueError(
                f"Derived step duration must be positive and no greater than "
                f"{limits.max_duration_s:g} seconds."
            )
        if not limits.min_interval_s <= interval_s <= limits.max_interval_s:
            raise ValueError(
                f"Sampling interval must be between {limits.min_interval_s:g} and "
                f"{limits.max_interval_s:g} seconds."
            )
        if interval_s > dwell_s:
            raise ValueError("Sampling interval cannot exceed the derived step duration.")
        exact_interval = _decimal(interval, "Sampling interval")
        exact_settling = _decimal(settling_time, "Settling time")
        available = 0
        sample_time = exact_interval
        while sample_time < exact_duration:
            if sample_time >= exact_settling:
                available += 1
            sample_time += exact_interval
        if available < measurements:
            raise ValueError(
                "Step duration, settling time and sampling interval provide only "
                f"{available} post-settling sample(s), fewer than the requested "
                f"{measurements}."
            )

        area: float | None = None
        if active_area_cm2.strip():
            exact_area = _decimal(active_area_cm2, "Active area")
            if exact_area <= 0:
                raise ValueError("Active area must be positive when supplied.")
            area = float(exact_area)

        setpoints: list[float] = []
        for exact in exact_setpoints:
            voltage = float(exact)
            # Validate the exact decimal against the hardware grid, but retain
            # the decimal-derived float instead of accumulating binary steps.
            QPX600DP_CAPABILITIES.validate_voltage(voltage, voltage_range_name)
            if voltage > limits.max_voltage_v:
                raise ValueError(
                    f"Scan voltage exceeds the {limits.max_voltage_v:g} V project limit."
                )
            if voltage * current > limits.max_power_w:
                raise ValueError(
                    f"Requested V*I exceeds the {limits.max_power_w:g} W project limit."
                )
            if not QPX600DP_CAPABILITIES.power_envelope.contains(voltage, current):
                raise ValueError(
                    "Requested scan point is outside the conservative power envelope."
                )
            self._safety_validator.validate_operating_point(
                self._channel,
                voltage,
                current,
                voltage_range_name,
                active_area_cm2=area,
                voltage_label="Scan voltage",
                current_label="Current limit",
            )
            setpoints.append(voltage)
        duration_s = len(setpoints) * dwell_s
        if duration_s > limits.max_duration_s:
            raise ValueError(
                f"Total scan duration exceeds the {limits.max_duration_s:g} second "
                "project limit."
            )
        return SteppedScanConfig(
            setpoints=tuple(setpoints),
            fixed_voltage_v=setpoints[0],
            fixed_current_a=current,
            dwell_s=dwell_s,
            interval_s=interval_s,
            voltage_range_name=voltage_range_name,
            scan_name="staircase_lsv",
            active_area_cm2=area,
            scan_step_mv=float(exact_step_mv),
            scan_rate_mv_per_s=float(exact_rate),
            settling_time_s=settling_s,
            measurements_per_point=measurements,
        )

    def _protocol_csv_metadata(self) -> dict[str, object]:
        metadata = super()._protocol_csv_metadata()
        config = self._snapshot.config
        if not isinstance(config, SteppedScanConfig) or config.scan_name != "staircase_lsv":
            return metadata
        metadata.update({
            "scan_rate_mv_per_s": config.scan_rate_mv_per_s,
            "scan_step_mv": config.scan_step_mv,
            "scan_step_duration_s": config.dwell_s,
            "scan_settling_time_s": config.settling_time_s,
            "scan_measurements_per_point": config.measurements_per_point,
            "active_area_cm2": config.active_area_cm2,
        })
        if self._point_metadata is not None:
            metadata.update(self._point_metadata)
        return metadata

    def _command_completed(self, request_id: int, name: str, result: object) -> None:
        is_sample = (
            request_id == self._pending_request_id
            and self._pending_action == "sample"
            and isinstance(result, ChannelReadback)
        )
        sample_step_elapsed = 0.0
        if is_sample:
            config = self._snapshot.config
            if isinstance(config, SteppedScanConfig):
                sample_step_elapsed = max(
                    0.0,
                    self._elapsed_active() - self._scan_step_index * config.dwell_s,
                )
        super()._command_completed(request_id, name, result)
        if is_sample and isinstance(result, ChannelReadback):
            config = self._snapshot.config
            if (
                isinstance(config, SteppedScanConfig)
                and config.scan_name == "staircase_lsv"
                and sample_step_elapsed + 1e-12 >= config.settling_time_s
            ):
                self._point_samples.append(result)

    @Slot()
    def _update_clock(self) -> None:
        """Let the scan boundary finalize the last LSV point before cleanup."""
        if self._snapshot.state not in {ProtocolState.RUNNING, ProtocolState.PAUSING}:
            return
        elapsed = self._elapsed_active()
        self._publish(replace(
            self._snapshot,
            elapsed_active_s=elapsed,
            remaining_active_s=self._remaining_active(elapsed),
        ))
        config = self._snapshot.config
        if (
            self._snapshot.state is ProtocolState.RUNNING
            and isinstance(config, SteppedScanConfig)
            and elapsed >= config.duration_s
            and self._pending_request_id is None
        ):
            self._sample_timer.start(0)

    def _advance_or_complete(self, config: SteppedScanConfig) -> None:
        if config.scan_name == "staircase_lsv":
            try:
                valid = self._finalize_lsv_point(config)
            except (OSError, ValueError) as error:
                self._begin_cleanup(
                    ProtocolState.FAILED,
                    f"Protocol CSV write failed: {error}",
                )
                return
            if not valid:
                self._begin_cleanup(
                    ProtocolState.FAILED,
                    "Staircase LSV point had insufficient valid post-settling samples.",
                )
                return
        super()._advance_or_complete(config)

    def _finalize_lsv_point(self, config: SteppedScanConfig) -> bool:
        selected = self._point_samples[-config.measurements_per_point :]
        valid = len(selected) == config.measurements_per_point
        mean_voltage: float | None = None
        mean_current: float | None = None
        density: float | None = None
        summary: ChannelReadback | None = None
        if valid:
            mean_voltage = sum(item.measured_voltage_v for item in selected) / len(selected)
            mean_current = sum(item.measured_current_a for item in selected) / len(selected)
            if config.active_area_cm2 is not None:
                density = mean_current / config.active_area_cm2
            last = selected[-1]
            summary = ChannelReadback(
                channel=self._channel,
                programmed_voltage_v=config.setpoints[self._scan_step_index],
                current_limit_a=config.fixed_current_a,
                measured_voltage_v=mean_voltage,
                measured_current_a=mean_current,
                voltage_range_name=last.voltage_range_name,
                output_enabled=last.output_enabled,
            )
        point = StaircaseLsvPoint(
            channel=self._channel,
            point_index=self._scan_step_index + 1,
            total_points=len(config.setpoints),
            commanded_voltage_v=config.setpoints[self._scan_step_index],
            mean_voltage_v=mean_voltage,
            mean_current_a=mean_current,
            mean_current_density_a_cm2=density,
            valid=valid,
        )
        self._point_metadata = {
            "scan_point_result_index": point.point_index,
            "scan_point_valid": valid,
            "measured_current_density_a_cm2": density,
        }
        try:
            self._append(
                summary,
                "lsv_point" if valid else "lsv_point_invalid",
                ProtocolState.RUNNING,
            )
        except (OSError, ValueError):
            self._point_metadata = None
            self._point_samples.clear()
            raise
        self._point_metadata = None
        self._point_samples.clear()
        self.point_result_received.emit(point)
        return valid


class CurrentScanController(SteppedScanController):
    """Milestone 13 Simulation Mode stepped-current scan."""

    def __init__(
        self,
        channel: int,
        connection: ConnectionController,
        parent: QObject | None = None,
        *,
        safety_limits: ConstantCurrentSafetyLimits = DEFAULT_CONSTANT_CURRENT_LIMITS,
        monotonic=None,
        utc_now=None,
        writer_factory=CsvLogFile,
        safety_validator: SafetyValidator | None = None,
    ) -> None:
        super().__init__(
            channel,
            connection,
            parent,
            axis_name="current",
            safety_limits=safety_limits,
            monotonic=monotonic,
            utc_now=utc_now,
            writer_factory=writer_factory,
            safety_validator=safety_validator,
        )

    def validate_inputs(
        self,
        start_current: str,
        stop_current: str,
        current_step: str,
        voltage_compliance: str,
        dwell: str,
        interval: str,
        voltage_range_name: str | None,
        *,
        current_input_mode: str = CurrentInputMode.CURRENT.value,
        active_area_cm2: str = "",
    ) -> str | None:
        session_error = self._validate_scan_session(voltage_range_name)
        if session_error is not None:
            return session_error
        try:
            self._validate_scan_config(
                start_current,
                stop_current,
                current_step,
                voltage_compliance,
                dwell,
                interval,
                voltage_range_name,
                current_input_mode=current_input_mode,
                active_area_cm2=active_area_cm2,
            )
        except (ValueError, InstrumentValueError) as error:
            return str(error)
        return None

    def start(
        self,
        start_current: str,
        stop_current: str,
        current_step: str,
        voltage_compliance: str,
        dwell: str,
        interval: str,
        voltage_range_name: str | None,
        directory: Path,
        *,
        current_input_mode: str = CurrentInputMode.CURRENT.value,
        active_area_cm2: str = "",
        logging_sink: ProtocolLogSink | None = None,
        real_hardware_confirmation: str | None = None,
    ) -> bool:
        if self._snapshot.owns_channel:
            return False
        validation_error = self.validate_inputs(
            start_current,
            stop_current,
            current_step,
            voltage_compliance,
            dwell,
            interval,
            voltage_range_name,
            current_input_mode=current_input_mode,
            active_area_cm2=active_area_cm2,
        )
        if validation_error is not None:
            return self._reject(validation_error)
        if (
            not self._connection.snapshot.simulation_mode
            and real_hardware_confirmation != REAL_HARDWARE_SCAN_CONFIRMATION
        ):
            return self._reject("Real Hardware Current Scan start was not confirmed.")
        assert voltage_range_name is not None
        config = self._validate_scan_config(
            start_current,
            stop_current,
            current_step,
            voltage_compliance,
            dwell,
            interval,
            voltage_range_name,
            current_input_mode=current_input_mode,
            active_area_cm2=active_area_cm2,
        )
        self._scan_step_index = 0
        return self._begin_validated_start(config, directory, logging_sink=logging_sink)

    def _validate_scan_config(
        self,
        start_current: str,
        stop_current: str,
        current_step: str,
        voltage_compliance: str,
        dwell: str,
        interval: str,
        voltage_range_name: str,
        *,
        current_input_mode: str = CurrentInputMode.CURRENT.value,
        active_area_cm2: str = "",
    ) -> SteppedScanConfig:
        selection = CurrentInputSelection.parse(current_input_mode, active_area_cm2)
        input_label = (
            "current density"
            if selection.mode is CurrentInputMode.CURRENT_DENSITY else "current"
        )
        exact_start_input = _decimal(start_current, f"Start {input_label}")
        exact_stop_input = _decimal(stop_current, f"Stop {input_label}")
        exact_step_input = _decimal(current_step, f"{input_label.title()} step")
        exact_start = selection.resolve(
            exact_start_input, f"Start {input_label}"
        ).current_a
        exact_stop = selection.resolve(
            exact_stop_input, f"Stop {input_label}"
        ).current_a
        exact_step = selection.resolve(
            exact_step_input, f"{input_label.title()} step"
        ).current_a
        QPX600DP_CAPABILITIES.validate_current(float(exact_start))
        QPX600DP_CAPABILITIES.validate_current(float(exact_stop))
        if exact_step == 0:
            raise ValueError("Current step must not be zero.")
        QPX600DP_CAPABILITIES.validate_current(abs(float(exact_step)))
        exact_setpoints = generate_inclusive_setpoints(
            exact_start,
            exact_stop,
            exact_step,
            axis_label="Current",
        )
        density_setpoints = (
            generate_inclusive_setpoints(
                exact_start_input,
                exact_stop_input,
                exact_step_input,
                axis_label="Current density",
            )
            if selection.mode is CurrentInputMode.CURRENT_DENSITY else ()
        )
        voltage = QPX600DP_CAPABILITIES.validate_voltage(
            self._number(voltage_compliance, "Voltage compliance"),
            voltage_range_name,
        )
        dwell_s = self._number(dwell, "Dwell time")
        interval_s = self._number(interval, "Sampling interval")
        limits = (
            self._limits
            if self._connection.snapshot.simulation_mode
            else self._real_hardware_limits
        )
        if voltage > limits.max_voltage_v:
            raise ValueError(
                f"Voltage compliance exceeds the {limits.max_voltage_v:g} V project limit."
            )
        if not 0 < dwell_s <= limits.max_duration_s:
            raise ValueError(
                f"Dwell time must be positive and no greater than "
                f"{limits.max_duration_s:g} seconds."
            )
        if not limits.min_interval_s <= interval_s <= limits.max_interval_s:
            raise ValueError(
                f"Sampling interval must be between {limits.min_interval_s:g} and "
                f"{limits.max_interval_s:g} seconds."
            )
        if interval_s > dwell_s:
            raise ValueError("Sampling interval cannot be longer than dwell time.")

        setpoints: list[float] = []
        for exact in exact_setpoints:
            current = QPX600DP_CAPABILITIES.validate_current(float(exact))
            if current > limits.max_current_a:
                raise ValueError(
                    f"Scan current exceeds the {limits.max_current_a:g} A project limit."
                )
            if voltage * current > limits.max_power_w:
                raise ValueError(
                    f"Requested V*I exceeds the {limits.max_power_w:g} W project limit."
                )
            if not QPX600DP_CAPABILITIES.power_envelope.contains(voltage, current):
                raise ValueError(
                    "Requested scan point is outside the conservative power envelope."
                )
            density = (
                float(density_setpoints[len(setpoints)])
                if density_setpoints else None
            )
            self._safety_validator.validate_operating_point(
                self._channel,
                voltage,
                current,
                voltage_range_name,
                active_area_cm2=(
                    None if selection.active_area_cm2 is None
                    else float(selection.active_area_cm2)
                ),
                current_density_a_cm2=density,
                voltage_label="Voltage compliance",
                current_label="Scan current",
            )
            setpoints.append(current)
        duration_s = len(setpoints) * dwell_s
        if duration_s > limits.max_duration_s:
            raise ValueError(
                f"Total scan duration exceeds the {limits.max_duration_s:g} second "
                "project limit."
            )
        return SteppedScanConfig(
            setpoints=tuple(setpoints),
            fixed_voltage_v=voltage,
            fixed_current_a=setpoints[0],
            dwell_s=dwell_s,
            interval_s=interval_s,
            voltage_range_name=voltage_range_name,
            scan_name="current_scan",
            current_input_mode=selection.mode.value,
            active_area_cm2=(
                None if selection.active_area_cm2 is None
                else float(selection.active_area_cm2)
            ),
            current_density_setpoints_a_cm2=tuple(
                float(value) for value in density_setpoints
            ),
        )
