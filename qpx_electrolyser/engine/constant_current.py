"""Independent guarded constant-current protocol state machines."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import math
from pathlib import Path
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot

from qpx_electrolyser.app.connection_controller import (
    ConnectionController,
    ConnectionSnapshot,
    ConnectionState,
)
from qpx_electrolyser.data.csv_logging import (
    CsvLogFile,
    CsvWriter,
    LoggingSnapshot,
    ProtocolLogSink,
)
from qpx_electrolyser.engine.current_input import CurrentInputMode, CurrentInputSelection
from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.capabilities import QPX600DP_CAPABILITIES
from qpx_electrolyser.instrument.channel_readback import ChannelReadback, read_channel
from qpx_electrolyser.instrument.connection_worker import CommandPriority
from qpx_electrolyser.instrument.exceptions import InstrumentValueError


class ProtocolState(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    RESUMING = "RESUMING"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


TERMINAL_STATES = {ProtocolState.IDLE, ProtocolState.COMPLETED, ProtocolState.FAILED}
OWNING_STATES = {
    ProtocolState.STARTING,
    ProtocolState.RUNNING,
    ProtocolState.PAUSING,
    ProtocolState.PAUSED,
    ProtocolState.RESUMING,
    ProtocolState.STOPPING,
}


@dataclass(frozen=True, slots=True)
class ConstantCurrentSafetyLimits:
    """Explicit Milestone 9 mode policy, below hardware ratings."""

    max_voltage_v: float = 10.0
    max_current_a: float = 5.0
    max_power_w: float = 50.0
    max_duration_s: float = 86_400.0
    min_interval_s: float = 0.1
    max_interval_s: float = 3_600.0


DEFAULT_CONSTANT_CURRENT_LIMITS = ConstantCurrentSafetyLimits()
REAL_HARDWARE_CONSTANT_CURRENT_LIMITS = ConstantCurrentSafetyLimits(min_interval_s=1.0)
REAL_HARDWARE_PROTOCOL_CONFIRMATION = "RUN-QPX600DP-CONSTANT-CURRENT"
OUTPUT_SETTLING_PERIOD_S = 1.0
OUTPUT_CONFIRMATION_INTERVAL_S = 0.1
REAL_HARDWARE_RESUME_QUIESCENCE_S = 1.0


@dataclass(frozen=True, slots=True)
class ConstantCurrentConfig:
    target_current_a: float
    voltage_compliance_v: float
    duration_s: float
    interval_s: float
    voltage_range_name: str
    current_input_mode: str = CurrentInputMode.CURRENT.value
    active_area_cm2: float | None = None
    commanded_current_density_a_cm2: float | None = None


@dataclass(frozen=True, slots=True)
class ProtocolSnapshot:
    channel: int
    state: ProtocolState = ProtocolState.IDLE
    config: ConstantCurrentConfig | None = None
    elapsed_active_s: float = 0.0
    remaining_active_s: float = 0.0
    measured_voltage_v: float | None = None
    measured_current_a: float | None = None
    output_enabled: bool | None = None
    missed_samples: int = 0
    sample_count: int = 0
    file_path: Path | None = None
    message: str = "Constant Current protocol idle"
    error: bool = False
    operator_warning: bool = False
    resume_ready: bool = False
    scan_step_index: int | None = None
    scan_total_steps: int | None = None
    scan_commanded_setpoint: float | None = None

    @property
    def owns_channel(self) -> bool:
        return self.state in OWNING_STATES


@dataclass(frozen=True, slots=True)
class _StartResult:
    programmed_voltage_v: float
    programmed_current_a: float
    output_enabled: bool
    readback: ChannelReadback | None = None


@dataclass(frozen=True, slots=True)
class _PreparedResult:
    programmed_voltage_v: float
    programmed_current_a: float


@dataclass(frozen=True, slots=True)
class _OutputTransitionArmed:
    """One real-hardware output write and its *OPC? have completed."""

    expected_enabled: bool
    programmed_voltage_v: float = 0.0
    programmed_current_a: float = 0.0


@dataclass(frozen=True, slots=True)
class _GuardFailure:
    message: str
    cleanup_required: bool


class SettingsMismatchError(RuntimeError):
    pass


class OutputStateError(RuntimeError):
    pass


def _matches(actual: float, expected: float, resolution: float) -> bool:
    return math.isclose(actual, expected, rel_tol=0.0, abs_tol=resolution / 2 + 1e-12)


class ConstantCurrentController(QObject):
    """Own one protocol timeline and submit every backend action to the shared queue."""

    snapshot_changed = Signal(object)
    sample_received = Signal(object)

    def __init__(
        self,
        channel: int,
        connection: ConnectionController,
        parent: QObject | None = None,
        *,
        safety_limits: ConstantCurrentSafetyLimits = DEFAULT_CONSTANT_CURRENT_LIMITS,
        real_hardware_safety_limits: ConstantCurrentSafetyLimits = (
            REAL_HARDWARE_CONSTANT_CURRENT_LIMITS
        ),
        monotonic=time.monotonic,
        sleep=time.sleep,
        utc_now=lambda: datetime.now(timezone.utc),
        writer_factory=CsvLogFile,
        protocol_name: str = "constant_current",
        display_name: str = "Constant Current",
        command_slug: str = "constant-current",
        current_label: str = "Target current",
        voltage_label: str = "Voltage compliance",
        real_hardware_confirmation_token: str = REAL_HARDWARE_PROTOCOL_CONFIRMATION,
    ) -> None:
        super().__init__(parent)
        QPX600DP_CAPABILITIES.validate_channel(channel)
        self._channel = channel
        self._connection = connection
        self._limits = safety_limits
        self._real_hardware_limits = real_hardware_safety_limits
        self._monotonic = monotonic
        self._sleep = sleep
        self._utc_now = utc_now
        self._writer_factory = writer_factory
        self._protocol_name = protocol_name
        self._display_name = display_name
        self._command_slug = command_slug
        self._current_label = current_label
        self._voltage_label = voltage_label
        self._real_hardware_confirmation_token = real_hardware_confirmation_token
        self._snapshot = ProtocolSnapshot(
            channel=channel,
            message=f"{display_name} protocol idle",
        )
        self._writer: CsvWriter | None = None
        self._logging_sink: ProtocolLogSink | None = None
        self._session_id = 0
        self._pending_request_id: int | None = None
        self._pending_action = ""
        self._action_key = f"{command_slug}-action-{channel}"
        self._sample_key = f"{command_slug}-sample-{channel}"
        self._run_started_at = 0.0
        self._active_before_run_s = 0.0
        self._sample_epoch_active_s = 0.0
        self._next_sample_slot = 0
        self._terminal_after_stop = ProtocolState.COMPLETED
        self._terminal_message = ""
        self._output_off_attempted = False
        self._real_hardware_run = False
        self._resume_quiescence_started_at: float | None = None
        self._resume_quiescence_session_id = 0
        self._output_settling_action = ""
        self._output_settling_session_id = 0
        self._output_settling_expected: bool | None = None
        self._output_settling_voltage_v = 0.0
        self._output_settling_current_a = 0.0

        self._sample_timer = QTimer(self)
        self._sample_timer.setSingleShot(True)
        self._sample_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._sample_timer.timeout.connect(self._submit_sample)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(100)
        self._clock_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._clock_timer.timeout.connect(self._update_clock)
        self._resume_enable_timer = QTimer(self)
        self._resume_enable_timer.setSingleShot(True)
        self._resume_enable_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._resume_enable_timer.timeout.connect(self._complete_resume_quiescence)
        self._output_settling_timer = QTimer(self)
        self._output_settling_timer.setSingleShot(True)
        self._output_settling_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._output_settling_timer.timeout.connect(self._complete_output_settling)

        connection.snapshot_changed.connect(self._connection_changed)
        connection.command_completed.connect(self._command_completed)
        connection.command_failed.connect(self._command_failed)

    @property
    def snapshot(self) -> ProtocolSnapshot:
        return self._snapshot

    def validate_inputs(
        self,
        target_current: str,
        voltage_compliance: str,
        duration: str,
        interval: str,
        voltage_range_name: str | None,
        *,
        current_input_mode: str = CurrentInputMode.CURRENT.value,
        active_area_cm2: str = "",
    ) -> str | None:
        """Return a user-facing validation error without queue or file side effects."""
        return self._validate_controller_inputs(
            target_current,
            voltage_compliance,
            duration,
            interval,
            voltage_range_name,
            current_input_mode=current_input_mode,
            active_area_cm2=active_area_cm2,
        )

    def _validate_controller_inputs(
        self,
        target_current: str,
        voltage_compliance: str,
        duration: str,
        interval: str,
        voltage_range_name: str | None,
        *,
        current_input_mode: str = CurrentInputMode.CURRENT.value,
        active_area_cm2: str = "",
    ) -> str | None:
        if self._connection.snapshot.state is not ConnectionState.CONNECTED:
            return "Connect and initialize the selected instrument before starting."
        if voltage_range_name is None:
            return "Refresh the channel before starting a protocol."
        if not self._connection.snapshot.simulation_mode:
            parts = [
                part.strip()
                for part in (self._connection.snapshot.instrument_id or "").split(",")
            ]
            if len(parts) != 4 or parts[1].upper() != "QPX600DP":
                return "Real Hardware protocol requires an identified QPX600DP session."
        try:
            self._validate_config(
                target_current,
                voltage_compliance,
                duration,
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
        target_current: str,
        voltage_compliance: str,
        duration: str,
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
        self._cancel_resume_quiescence()
        validation_error = self._validate_controller_inputs(
            target_current,
            voltage_compliance,
            duration,
            interval,
            voltage_range_name,
            current_input_mode=current_input_mode,
            active_area_cm2=active_area_cm2,
        )
        if validation_error is not None:
            return self._reject(validation_error)
        if (
            not self._connection.snapshot.simulation_mode
            and real_hardware_confirmation != self._real_hardware_confirmation_token
        ):
            return self._reject(
                f"Real Hardware {self._display_name} start was not confirmed."
            )
        assert voltage_range_name is not None
        try:
            config = self._validate_config(
                target_current,
                voltage_compliance,
                duration,
                interval,
                voltage_range_name,
                current_input_mode=current_input_mode,
                active_area_cm2=active_area_cm2,
            )
        except (ValueError, InstrumentValueError) as error:
            return self._reject(str(error))
        return self._begin_validated_start(config, directory, logging_sink=logging_sink)

    def _begin_validated_start(
        self,
        config: ConstantCurrentConfig,
        directory: Path,
        *,
        logging_sink: ProtocolLogSink | None = None,
    ) -> bool:
        """Acquire logging and submit the common guarded start sequence."""
        writer: CsvWriter | None = None
        if logging_sink is not None:
            if not logging_sink.active or logging_sink.file_path is None:
                return self._reject("The selected channel logger is not active.")
            file_path = logging_sink.file_path
        else:
            try:
                writer = self._writer_factory(Path(directory), self._channel)
            except (OSError, ValueError) as error:
                return self._reject(f"Cannot create protocol CSV: {error}")
            file_path = writer.path

        self._writer = writer
        self._logging_sink = logging_sink
        self._session_id = self._connection.snapshot.session_id
        self._active_before_run_s = 0.0
        self._run_started_at = 0.0
        self._sample_epoch_active_s = 0.0
        self._next_sample_slot = 0
        self._output_off_attempted = False
        self._real_hardware_run = not self._connection.snapshot.simulation_mode
        self._publish(ProtocolSnapshot(
            channel=self._channel,
            state=ProtocolState.STARTING,
            config=config,
            remaining_active_s=config.duration_s,
            file_path=file_path,
            message=(
                f"Checking OFF state and applying {self._display_name.lower()} settings..."
            ),
            **self._initial_snapshot_fields(config),
        ))
        return self._submit_action(
            "start-prepare", lambda supply: self._start_prepare_operation(supply, config)
        )

    @Slot()
    def pause(self) -> None:
        if self._snapshot.state is not ProtocolState.RUNNING:
            return
        self._sample_timer.stop()
        self._connection.cancel_commands(self._sample_key)
        self._publish(replace(
            self._snapshot,
            state=ProtocolState.PAUSING,
            resume_ready=False,
            message="Turning the selected output OFF and confirming readback...",
        ))
        self._submit_output_off_action("pause", "Protocol pause requested")

    @Slot()
    def resume(self, real_hardware_confirmation: str | None = None) -> bool:
        if self._snapshot.state is not ProtocolState.PAUSED or self._snapshot.config is None:
            return False
        if (
            self._pending_request_id is not None
            or self._snapshot.output_enabled is not False
            or not self._snapshot.resume_ready
        ):
            return False
        if self._remaining_active() <= 0:
            self._finish_terminal(ProtocolState.COMPLETED, "Protocol duration completed")
            return False
        if not self._session_is_current():
            self._finish_terminal(
                ProtocolState.FAILED,
                "Cannot resume: the connected session changed.",
                warning=True,
            )
            return False
        if (
            not self._connection.snapshot.simulation_mode
            and real_hardware_confirmation != self._real_hardware_confirmation_token
        ):
            self._publish(replace(
                self._snapshot,
                message=f"Real Hardware {self._display_name} resume was not confirmed.",
                error=True,
            ))
            return False
        config = self._snapshot.config
        self._cancel_resume_quiescence()
        self._publish(replace(
            self._snapshot,
            state=ProtocolState.RESUMING,
            resume_ready=False,
            message="Revalidating settings before guarded Output On...",
        ))
        return self._submit_action(
            "resume-prepare", lambda supply: self._resume_prepare_operation(supply, config)
        )

    @Slot()
    def stop(self, message: str = "Protocol stopped by operator") -> None:
        if not self._snapshot.owns_channel:
            return
        self._begin_cleanup(ProtocolState.COMPLETED, message)

    def shutdown(self) -> None:
        if self._snapshot.owns_channel:
            self._begin_cleanup(ProtocolState.FAILED, "Application shutdown requested")

    def force_shutdown(self, message: str) -> None:
        """Close local resources after a bounded outer shutdown wait expires."""
        if self._snapshot.owns_channel:
            self._finish_terminal(ProtocolState.FAILED, message, warning=True)

    def _validate_config(
        self,
        target_current: str,
        voltage_compliance: str,
        duration: str,
        interval: str,
        voltage_range_name: str,
        *,
        current_input_mode: str = CurrentInputMode.CURRENT.value,
        active_area_cm2: str = "",
    ) -> ConstantCurrentConfig:
        current_input = CurrentInputSelection.parse(
            current_input_mode, active_area_cm2
        ).resolve(target_current, self._current_label)
        current = float(current_input.current_a)
        voltage = self._number(voltage_compliance, self._voltage_label)
        duration_s = self._number(duration, "Duration")
        interval_s = self._number(interval, "Measurement interval")
        current = QPX600DP_CAPABILITIES.validate_current(current)
        voltage = QPX600DP_CAPABILITIES.validate_voltage(voltage, voltage_range_name)
        limits = (
            self._limits
            if self._connection.snapshot.simulation_mode
            else self._real_hardware_limits
        )
        if current > limits.max_current_a:
            raise ValueError(
                f"{self._current_label} exceeds the {limits.max_current_a:g} A project limit."
            )
        if voltage > limits.max_voltage_v:
            raise ValueError(
                f"{self._voltage_label} exceeds the {limits.max_voltage_v:g} V project limit."
            )
        if voltage * current > limits.max_power_w:
            raise ValueError(f"Requested V*I exceeds the {limits.max_power_w:g} W project limit.")
        if not QPX600DP_CAPABILITIES.power_envelope.contains(voltage, current):
            raise ValueError("Requested operating point is outside the conservative power envelope.")
        if not 0 < duration_s <= limits.max_duration_s:
            raise ValueError(
                f"Duration must be positive and no greater than {limits.max_duration_s:g} seconds."
            )
        if not limits.min_interval_s <= interval_s <= limits.max_interval_s:
            raise ValueError(
                f"Measurement interval must be between {limits.min_interval_s:g} and "
                f"{limits.max_interval_s:g} seconds."
            )
        if interval_s > duration_s:
            raise ValueError("Measurement interval cannot be longer than the total duration.")
        return ConstantCurrentConfig(
            current,
            voltage,
            duration_s,
            interval_s,
            voltage_range_name,
            current_input_mode=current_input.input_mode.value,
            active_area_cm2=(
                None if current_input.active_area_cm2 is None
                else float(current_input.active_area_cm2)
            ),
            commanded_current_density_a_cm2=(
                None if current_input.current_density_a_cm2 is None
                else float(current_input.current_density_a_cm2)
            ),
        )

    @staticmethod
    def _number(text: str, label: str) -> float:
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{label} is required.")
        try:
            value = float(text)
        except ValueError as error:
            raise ValueError(f"{label} must be numeric.") from error
        if not math.isfinite(value):
            raise ValueError(f"{label} must be finite.")
        return value

    def _start_prepare_operation(
        self, supply: PowerSupplyInterface, config: ConstantCurrentConfig
    ) -> _PreparedResult | _GuardFailure:
        try:
            if supply.read_output_state(self._channel):
                return _GuardFailure(
                    "Selected output is already ON; protocol start refused.",
                    cleanup_required=False,
                )
            supply.set_voltage(self._channel, self._requested_voltage(config))
            supply.set_current(self._channel, self._requested_current(config))
            voltage = supply.read_set_voltage(self._channel)
            current = supply.read_set_current(self._channel)
            self._verify_settings(voltage, current, config)
        except Exception as error:
            return _GuardFailure(
                str(error) or type(error).__name__, cleanup_required=True
            )
        return _PreparedResult(voltage, current)

    def _resume_prepare_operation(
        self, supply: PowerSupplyInterface, config: ConstantCurrentConfig
    ) -> _PreparedResult | _GuardFailure:
        try:
            if supply.read_output_state(self._channel):
                return _GuardFailure(
                    "Selected output was not OFF before resume.", cleanup_required=True
                )
            voltage = supply.read_set_voltage(self._channel)
            current = supply.read_set_current(self._channel)
            self._verify_settings(voltage, current, config)
        except Exception as error:
            return _GuardFailure(
                str(error) or type(error).__name__, cleanup_required=True
            )
        return _PreparedResult(voltage, current)

    def _output_on_operation(
        self, supply: PowerSupplyInterface, config: ConstantCurrentConfig
    ) -> _StartResult | _OutputTransitionArmed | _GuardFailure:
        """Revalidate immediately before the one permitted Output-On attempt."""
        try:
            if supply.read_output_state(self._channel):
                return _GuardFailure(
                    "Selected output was not OFF immediately before Output On.",
                    cleanup_required=True,
                )
            voltage = supply.read_set_voltage(self._channel)
            current = supply.read_set_current(self._channel)
            self._verify_settings(voltage, current, config)
            supply.output_on(self._channel)
            if not self._real_hardware_run:
                output_enabled = supply.read_output_state(self._channel)
                if not output_enabled:
                    return _GuardFailure(
                        "Output On could not be confirmed.", cleanup_required=True
                    )
                return _StartResult(voltage, current, output_enabled)
            supply.confirm_operation_complete()
            return _OutputTransitionArmed(True, voltage, current)
        except Exception as error:
            return _GuardFailure(
                f"Output-On transaction failed: {str(error) or type(error).__name__}",
                cleanup_required=True,
            )

    def _verify_settings(
        self, voltage: float, current: float, config: ConstantCurrentConfig
    ) -> None:
        voltage_resolution = QPX600DP_CAPABILITIES.voltage_range(
            config.voltage_range_name
        ).programming_resolution_v
        if not _matches(voltage, self._requested_voltage(config), voltage_resolution):
            raise SettingsMismatchError(f"{self._voltage_label} read-back mismatch.")
        if not _matches(
            current,
            self._requested_current(config),
            QPX600DP_CAPABILITIES.current_programming_resolution_a,
        ):
            raise SettingsMismatchError(f"{self._current_label} read-back mismatch.")

    def _requested_voltage(self, config: ConstantCurrentConfig) -> float:
        """Return the voltage required at the current protocol position."""
        return config.voltage_compliance_v

    def _requested_current(self, config: ConstantCurrentConfig) -> float:
        """Return the current required at the current protocol position."""
        return config.target_current_a

    def _protocol_csv_metadata(self) -> dict[str, object]:
        """Optional protocol-specific columns supplied to the shared CSV writer."""
        config = self._snapshot.config
        if not isinstance(config, ConstantCurrentConfig):
            return {}
        return {
            "current_input_mode": config.current_input_mode,
            "active_area_cm2": config.active_area_cm2,
            "commanded_current_density_a_cm2": (
                config.commanded_current_density_a_cm2
            ),
        }

    def _initial_snapshot_fields(
        self, config: ConstantCurrentConfig
    ) -> dict[str, object]:
        """Optional snapshot fields supplied by specialized protocol controllers."""
        return {}

    def _output_off_operation(
        self, supply: PowerSupplyInterface
    ) -> ChannelReadback | _OutputTransitionArmed:
        supply.output_off(self._channel)
        if not self._real_hardware_run:
            readback = read_channel(supply, self._channel)
            if readback.output_enabled:
                raise OutputStateError("Output OFF could not be confirmed.")
            return readback
        supply.confirm_operation_complete()
        return _OutputTransitionArmed(False)

    def _confirm_output_transition_operation(
        self,
        supply: PowerSupplyInterface,
        expected_enabled: bool,
        programmed_voltage_v: float,
        programmed_current_a: float,
    ) -> ChannelReadback | _StartResult:
        try:
            first = supply.read_output_state(self._channel)
            self._sleep(OUTPUT_CONFIRMATION_INTERVAL_S)
            second = supply.read_output_state(self._channel)
            if first is not expected_enabled or second is not expected_enabled:
                state = "ON" if expected_enabled else "OFF"
                raise OutputStateError(
                    f"Output did not remain {state} after the 1.0-second settling period."
                    + (
                        " Manual front-panel intervention is required."
                        if not expected_enabled
                        else ""
                    )
                )
            readback = read_channel(supply, self._channel)
            if readback.output_enabled is not expected_enabled:
                state = "ON" if expected_enabled else "OFF"
                raise OutputStateError(
                    f"Output {state} did not remain confirmed during authoritative read-back."
                    + (
                        " Manual front-panel intervention is required."
                        if not expected_enabled
                        else ""
                    )
                )
        except Exception as error:
            if expected_enabled:
                return _GuardFailure(
                    str(error) or type(error).__name__, cleanup_required=True
                )
            raise
        if expected_enabled:
            return _StartResult(
                programmed_voltage_v,
                programmed_current_a,
                True,
                readback,
            )
        return readback

    def _submit_output_off_action(self, action: str, reason: str) -> bool:
        """Submit at most one Output-Off write for the current shutdown attempt."""
        if self._output_off_attempted:
            self._finish_terminal(
                ProtocolState.FAILED,
                f"{reason}; Output Off was already attempted and physical OFF could not "
                "be reconfirmed. Manual front-panel intervention is required.",
                warning=True,
            )
            return False
        self._output_off_attempted = True
        if self._real_hardware_run:
            self._suspend_logging_sampling()
        return self._submit_action(
            action,
            self._output_off_operation,
            priority=CommandPriority.SAFETY,
            diagnostic_reason=reason,
        )

    def _sample_operation(
        self, supply: PowerSupplyInterface
    ) -> ChannelReadback | _GuardFailure:
        try:
            return read_channel(supply, self._channel)
        except Exception as error:
            return _GuardFailure(
                f"Sample transaction failed: {str(error) or type(error).__name__}",
                cleanup_required=True,
            )

    def _submit_action(
        self,
        action: str,
        operation,
        *,
        priority: CommandPriority = CommandPriority.NORMAL,
        diagnostic_reason: str = "",
    ) -> bool:
        ticket = self._connection.submit_command(
            f"ch{self._channel}-{self._command_slug}-{action}",
            operation,
            priority=priority,
            cancellation_key=self._action_key,
            diagnostic_reason=diagnostic_reason,
        )
        if ticket is None:
            self._finish_terminal(
                ProtocolState.FAILED,
                f"Shared command queue rejected protocol {action}.",
                warning=self._snapshot.state is ProtocolState.STOPPING,
            )
            return False
        self._pending_request_id = ticket.request_id
        self._pending_action = action
        return True

    @Slot()
    def _submit_sample(self) -> None:
        if self._snapshot.state is not ProtocolState.RUNNING or self._snapshot.config is None:
            return
        elapsed = self._elapsed_active()
        if elapsed >= self._snapshot.config.duration_s:
            self._begin_cleanup(ProtocolState.COMPLETED, "Protocol duration completed")
            return
        segment_elapsed = max(0.0, elapsed - self._sample_epoch_active_s)
        current_slot = math.floor(segment_elapsed / self._snapshot.config.interval_s)
        if current_slot > self._next_sample_slot:
            self._publish(replace(
                self._snapshot,
                missed_samples=self._snapshot.missed_samples + current_slot - self._next_sample_slot,
            ))
            self._next_sample_slot = current_slot
        ticket = self._connection.submit_command(
            f"ch{self._channel}-{self._command_slug}-sample",
            self._sample_operation,
            priority=CommandPriority.BACKGROUND,
            cancellation_key=self._sample_key,
        )
        if ticket is None:
            self._begin_cleanup(ProtocolState.FAILED, "Shared queue rejected a sample.")
            return
        self._pending_request_id = ticket.request_id
        self._pending_action = "sample"
        self._next_sample_slot += 1

    @Slot(int, str, object)
    def _command_completed(self, request_id: int, _name: str, result: object) -> None:
        if request_id != self._pending_request_id:
            return
        action = self._pending_action
        self._pending_request_id = None
        self._pending_action = ""
        if action in {"start-prepare", "resume-prepare"}:
            if isinstance(result, _GuardFailure):
                if result.cleanup_required:
                    self._begin_cleanup(ProtocolState.FAILED, result.message)
                else:
                    self._finish_terminal(
                        ProtocolState.FAILED, result.message, warning=True
                    )
                return
            if not isinstance(result, _PreparedResult) or self._snapshot.config is None:
                self._begin_cleanup(ProtocolState.FAILED, "Malformed protocol preflight result.")
                return
            if not self._session_is_current():
                self._begin_cleanup(
                    ProtocolState.FAILED,
                    "Connected session changed before Output On.",
                )
                return
            phase = "start-output-on" if action == "start-prepare" else "resume-output-on"
            config = self._snapshot.config
            self._publish(replace(
                self._snapshot,
                message="Final OFF/settings preflight before guarded Output On...",
            ))
            # A fresh Output-On attempt starts a new shutdown-attempt lifetime.
            self._output_off_attempted = False
            if self._real_hardware_run:
                self._suspend_logging_sampling()
            self._submit_action(
                phase,
                lambda supply: self._output_on_operation(supply, config),
            )
            return
        if action in {"start-output-on-confirm", "resume-output-on-confirm"}:
            action = action.removesuffix("-confirm")
            self._resume_logging_sampling()
        if action in {"start-output-on", "resume-output-on"}:
            if isinstance(result, _OutputTransitionArmed):
                self._start_output_settling(action, result)
                return
            if isinstance(result, _GuardFailure):
                self._begin_cleanup(ProtocolState.FAILED, result.message)
                return
            if not isinstance(result, _StartResult) or not result.output_enabled:
                self._begin_cleanup(ProtocolState.FAILED, "Malformed Output-On result.")
                return
            if not self._session_is_current():
                self._begin_cleanup(ProtocolState.FAILED, "Connected session changed during start.")
                return
            if result.readback is not None:
                try:
                    self._append(
                        result.readback,
                        "output_on_stabilized",
                        self._snapshot.state,
                    )
                except (OSError, ValueError) as error:
                    self._begin_cleanup(
                        ProtocolState.FAILED,
                        f"Protocol CSV write failed: {error}",
                    )
                    return
                self.sample_received.emit(result.readback)
            self._run_started_at = self._monotonic()
            self._publish(replace(
                self._snapshot,
                state=ProtocolState.RUNNING,
                measured_voltage_v=(
                    result.readback.measured_voltage_v
                    if result.readback is not None
                    else self._snapshot.measured_voltage_v
                ),
                measured_current_a=(
                    result.readback.measured_current_a
                    if result.readback is not None
                    else self._snapshot.measured_current_a
                ),
                output_enabled=True,
                message=f"{self._display_name} protocol running",
                error=False,
                operator_warning=False,
            ))
            self._clock_timer.start()
            if self._real_hardware_run or action == "resume-output-on":
                self._rebase_sample_schedule()
                self._schedule_next_sample()
            else:
                self._sample_timer.start(0)
            return
        if action == "sample":
            if isinstance(result, _GuardFailure):
                self._begin_cleanup(ProtocolState.FAILED, result.message)
                return
            if not isinstance(result, ChannelReadback) or result.channel != self._channel:
                self._begin_cleanup(ProtocolState.FAILED, "Malformed protocol sample.")
                return
            if not result.output_enabled:
                self._begin_cleanup(ProtocolState.FAILED, "Output unexpectedly reported OFF.")
                return
            try:
                self._append(result, "ok", ProtocolState.RUNNING)
            except (OSError, ValueError) as error:
                self._begin_cleanup(ProtocolState.FAILED, f"Protocol CSV write failed: {error}")
                return
            self.sample_received.emit(result)
            self._publish(replace(
                self._snapshot,
                measured_voltage_v=result.measured_voltage_v,
                measured_current_a=result.measured_current_a,
                output_enabled=result.output_enabled,
                sample_count=self._snapshot.sample_count + 1,
                message=f"Logged protocol sample {self._snapshot.sample_count + 1}",
            ))
            self._schedule_next_sample()
            return
        if action in {"pause", "stop"} and isinstance(result, _OutputTransitionArmed):
            self._start_output_settling(action, result)
            return
        if action in {"pause-confirm", "stop-confirm"}:
            action = action.removesuffix("-confirm")
            self._resume_logging_sampling()
        if action == "pause":
            if (
                not isinstance(result, ChannelReadback)
                or result.channel != self._channel
                or result.output_enabled
            ):
                self._finish_terminal(
                    ProtocolState.FAILED,
                    "Malformed or unconfirmed Output-Off read-back during pause; "
                    "physical OFF could not be confirmed. Manual front-panel "
                    "intervention is required.",
                    warning=True,
                )
                return
            self._active_before_run_s = self._elapsed_active()
            self._run_started_at = 0.0
            self._clock_timer.stop()
            self._publish(replace(
                self._snapshot,
                measured_voltage_v=result.measured_voltage_v,
                measured_current_a=result.measured_current_a,
                output_enabled=result.output_enabled,
            ))
            self.sample_received.emit(result)
            if self._remaining_active() <= 0:
                self._finish_terminal(ProtocolState.COMPLETED, "Protocol duration completed")
                return
            self._publish(replace(
                self._snapshot,
                state=ProtocolState.PAUSED,
                elapsed_active_s=self._active_before_run_s,
                remaining_active_s=self._remaining_active(),
                resume_ready=not self._real_hardware_run,
                message=(
                    "Output OFF confirmed; waiting before Resume."
                    if self._real_hardware_run
                    else "Protocol paused; selected output confirmed OFF"
                ),
            ))
            self._append_event_best_effort("paused", ProtocolState.PAUSED)
            if self._real_hardware_run:
                self._start_resume_quiescence()
            return
        if action == "stop":
            if (
                not isinstance(result, ChannelReadback)
                or result.channel != self._channel
                or result.output_enabled
            ):
                self._finish_terminal(
                    ProtocolState.FAILED,
                    "Malformed or unconfirmed Output-Off cleanup read-back.",
                    warning=True,
                )
                return
            self._publish(replace(
                self._snapshot,
                measured_voltage_v=result.measured_voltage_v,
                measured_current_a=result.measured_current_a,
                output_enabled=result.output_enabled,
            ))
            self.sample_received.emit(result)
            self._finish_terminal(self._terminal_after_stop, self._terminal_message)

    @Slot(int, str, object)
    def _command_failed(self, request_id: int, _name: str, error: BaseException) -> None:
        if request_id != self._pending_request_id:
            return
        action = self._pending_action
        self._pending_request_id = None
        self._pending_action = ""
        message = str(error) or type(error).__name__
        if action in {"pause", "stop", "pause-confirm", "stop-confirm"}:
            self._resume_logging_sampling()
            self._finish_terminal(
                ProtocolState.FAILED,
                f"Output-Off cleanup failed: {message}; physical OFF could not be confirmed.",
                warning=True,
            )
            return
        self._begin_cleanup(ProtocolState.FAILED, f"Protocol {action} failed: {message}")

    def _begin_cleanup(self, terminal: ProtocolState, message: str) -> None:
        if self._snapshot.state is ProtocolState.STOPPING:
            return
        self._cancel_output_settling()
        self._cancel_resume_quiescence()
        self._sample_timer.stop()
        self._clock_timer.stop()
        self._connection.cancel_commands(self._sample_key)
        self._active_before_run_s = self._elapsed_active()
        self._run_started_at = 0.0
        self._terminal_after_stop = terminal
        self._terminal_message = message
        self._publish(replace(
            self._snapshot,
            state=ProtocolState.STOPPING,
            resume_ready=False,
            elapsed_active_s=self._active_before_run_s,
            remaining_active_s=self._remaining_active(),
            message="Attempting selected-channel Output Off...",
            error=terminal is ProtocolState.FAILED,
        ))
        if not self._session_is_current():
            self._finish_terminal(
                ProtocolState.FAILED,
                f"{message}; physical OFF could not be confirmed because the session is "
                "unavailable. Manual front-panel intervention is required.",
                warning=True,
            )
            return
        if self._output_off_attempted:
            if not self._snapshot.output_enabled:
                self._finish_terminal(terminal, message)
            else:
                self._finish_terminal(
                    ProtocolState.FAILED,
                    f"{message}; Output Off was already attempted and physical OFF could "
                    "not be reconfirmed. Manual front-panel intervention is required.",
                    warning=True,
                )
            return
        self._submit_output_off_action("stop", message)

    def _finish_terminal(
        self,
        state: ProtocolState,
        message: str,
        *,
        warning: bool = False,
    ) -> None:
        self._cancel_resume_quiescence()
        self._cancel_output_settling()
        self._resume_logging_sampling()
        self._sample_timer.stop()
        self._clock_timer.stop()
        self._connection.cancel_commands(self._sample_key)
        self._connection.cancel_commands(self._action_key)
        self._pending_request_id = None
        self._pending_action = ""
        self._append_event_best_effort("completed" if state is ProtocolState.COMPLETED else "error", state, message)
        writer, self._writer = self._writer, None
        self._logging_sink = None
        close_error: OSError | None = None
        if writer is not None:
            try:
                writer.close()
            except (OSError, ValueError) as error:
                close_error = error
        if close_error is not None:
            state = ProtocolState.FAILED
            message = f"{message}; protocol CSV close failed: {close_error}"
        self._publish(replace(
            self._snapshot,
            state=state,
            resume_ready=False,
            elapsed_active_s=self._active_before_run_s,
            remaining_active_s=self._remaining_active(),
            message=message,
            error=state is ProtocolState.FAILED,
            operator_warning=warning or close_error is not None,
        ))

    def _schedule_next_sample(self) -> None:
        if self._snapshot.state is not ProtocolState.RUNNING or self._snapshot.config is None:
            return
        elapsed = self._elapsed_active()
        if elapsed >= self._snapshot.config.duration_s:
            self._begin_cleanup(ProtocolState.COMPLETED, "Protocol duration completed")
            return
        segment_elapsed = max(0.0, elapsed - self._sample_epoch_active_s)
        due_slot = math.floor(segment_elapsed / self._snapshot.config.interval_s) + 1
        if due_slot > self._next_sample_slot:
            self._publish(replace(
                self._snapshot,
                missed_samples=self._snapshot.missed_samples + due_slot - self._next_sample_slot,
            ))
            self._next_sample_slot = due_slot
        next_active_s = (
            self._sample_epoch_active_s
            + self._next_sample_slot * self._snapshot.config.interval_s
        )
        delay_s = max(0.0, next_active_s - elapsed)
        delay_s = min(delay_s, self._remaining_active())
        self._sample_timer.start(max(0, math.ceil(delay_s * 1000)))

    def _rebase_sample_schedule(self) -> None:
        """Start a fresh interval grid without consuming an immediate sample slot."""
        self._sample_epoch_active_s = self._elapsed_active()
        self._next_sample_slot = 1

    def _start_output_settling(
        self, action: str, transition: _OutputTransitionArmed
    ) -> None:
        """Wait a full second without channel traffic before queued confirmation."""
        self._cancel_output_settling()
        self._output_settling_action = action
        self._output_settling_session_id = self._session_id
        self._output_settling_expected = transition.expected_enabled
        self._output_settling_voltage_v = transition.programmed_voltage_v
        self._output_settling_current_a = transition.programmed_current_a
        state = "ON" if transition.expected_enabled else "OFF"
        self._publish(replace(
            self._snapshot,
            message=f"Output {state} requested; waiting 1.0 s for hardware settling...",
            resume_ready=False,
        ))
        self._output_settling_timer.start(math.ceil(OUTPUT_SETTLING_PERIOD_S * 1000))

    def _cancel_output_settling(self) -> None:
        self._output_settling_timer.stop()
        self._output_settling_action = ""
        self._output_settling_session_id = 0
        self._output_settling_expected = None
        self._output_settling_voltage_v = 0.0
        self._output_settling_current_a = 0.0

    @Slot()
    def _complete_output_settling(self) -> None:
        action = self._output_settling_action
        expected_session_id = self._output_settling_session_id
        expected_enabled = self._output_settling_expected
        voltage = self._output_settling_voltage_v
        current = self._output_settling_current_a
        self._cancel_output_settling()
        if not action or expected_enabled is None:
            return
        expected_protocol_state = (
            {ProtocolState.STARTING, ProtocolState.RESUMING}
            if expected_enabled
            else {ProtocolState.PAUSING, ProtocolState.STOPPING}
        )
        if (
            not self._real_hardware_run
            or self._snapshot.state not in expected_protocol_state
            or self._pending_request_id is not None
            or expected_session_id != self._session_id
            or not self._session_is_current()
        ):
            return
        confirmation_action = f"{action}-confirm"
        self._submit_action(
            confirmation_action,
            lambda supply: self._confirm_output_transition_operation(
                supply,
                expected_enabled,
                voltage,
                current,
            ),
            priority=(
                CommandPriority.NORMAL
                if expected_enabled
                else CommandPriority.SAFETY
            ),
            diagnostic_reason=(
                ""
                if expected_enabled
                else self._terminal_message or "Confirming selected-channel Output Off"
            ),
        )

    def _suspend_logging_sampling(self) -> None:
        sink = self._logging_sink
        if sink is not None:
            sink.suspend_sampling()

    def _resume_logging_sampling(self) -> None:
        sink = self._logging_sink
        if sink is not None:
            sink.resume_sampling()

    def _start_resume_quiescence(self) -> None:
        """Gate Real Hardware Resume for a full monotonic second after confirmed OFF."""
        self._cancel_resume_quiescence()
        self._resume_quiescence_started_at = self._monotonic()
        self._resume_quiescence_session_id = self._session_id
        self._resume_enable_timer.start(math.ceil(REAL_HARDWARE_RESUME_QUIESCENCE_S * 1000))

    def _cancel_resume_quiescence(self) -> None:
        self._resume_enable_timer.stop()
        self._resume_quiescence_started_at = None
        self._resume_quiescence_session_id = 0

    @Slot()
    def _complete_resume_quiescence(self) -> None:
        started_at = self._resume_quiescence_started_at
        if started_at is None:
            return
        elapsed = max(0.0, self._monotonic() - started_at)
        remaining = REAL_HARDWARE_RESUME_QUIESCENCE_S - elapsed
        if remaining > 0:
            self._resume_enable_timer.start(max(1, math.ceil(remaining * 1000)))
            return
        expected_session_id = self._resume_quiescence_session_id
        self._cancel_resume_quiescence()
        if (
            not self._real_hardware_run
            or self._snapshot.state is not ProtocolState.PAUSED
            or self._snapshot.output_enabled is not False
            or self._pending_request_id is not None
            or expected_session_id != self._session_id
            or not self._session_is_current()
        ):
            return
        self._publish(replace(
            self._snapshot,
            resume_ready=True,
            message="Protocol paused; Output OFF confirmed; Resume available.",
            error=False,
        ))

    @Slot()
    def _update_clock(self) -> None:
        if self._snapshot.state not in {ProtocolState.RUNNING, ProtocolState.PAUSING}:
            return
        elapsed = self._elapsed_active()
        self._publish(replace(
            self._snapshot,
            elapsed_active_s=elapsed,
            remaining_active_s=self._remaining_active(elapsed),
        ))
        if (
            self._snapshot.state is ProtocolState.RUNNING
            and self._snapshot.config is not None
            and elapsed >= self._snapshot.config.duration_s
        ):
            self._begin_cleanup(ProtocolState.COMPLETED, "Protocol duration completed")

    def _elapsed_active(self) -> float:
        if self._run_started_at:
            return self._active_before_run_s + max(0.0, self._monotonic() - self._run_started_at)
        return self._active_before_run_s

    def _remaining_active(self, elapsed: float | None = None) -> float:
        config = self._snapshot.config
        if config is None:
            return 0.0
        return max(0.0, config.duration_s - (self._elapsed_active() if elapsed is None else elapsed))

    def _append(
        self,
        readback: ChannelReadback | None,
        status: str,
        state: ProtocolState,
        error: str = "",
    ) -> None:
        logging_sink = self._logging_sink
        if logging_sink is not None:
            logging_sink.append_protocol(
                readback,
                status=status,
                protocol_state=state.value,
                error=error,
                protocol_name=self._protocol_name,
                **self._protocol_csv_metadata(),
            )
            return
        writer = self._writer
        if writer is None:
            raise ValueError("Protocol CSV writer is unavailable")
        writer.append(
            timestamp=self._utc_now(),
            elapsed_s=self._elapsed_active(),
            channel=self._channel,
            readback=readback,
            connection_state=self._connection.snapshot.state.value,
            session_id=self._session_id,
            status=status,
            error=error,
            protocol_name=self._protocol_name,
            protocol_state=state.value,
            **self._protocol_csv_metadata(),
        )

    def _append_event_best_effort(
        self,
        status: str,
        state: ProtocolState,
        error: str = "",
    ) -> None:
        if self._writer is None and self._logging_sink is None:
            return
        try:
            self._append(None, status, state, error)
        except (OSError, ValueError):
            pass

    @Slot(object)
    def logging_snapshot_changed(self, snapshot: object) -> None:
        if (
            self._logging_sink is not None
            and self._snapshot.owns_channel
            and isinstance(snapshot, LoggingSnapshot)
            and snapshot.channel == self._channel
            and not snapshot.active
        ):
            self._begin_cleanup(
                ProtocolState.FAILED,
                f"Active protocol CSV logging became unavailable: {snapshot.message}",
            )

    @Slot(object)
    def _connection_changed(self, snapshot: ConnectionSnapshot) -> None:
        if self._snapshot.owns_channel and (
            snapshot.state is not ConnectionState.CONNECTED
            or snapshot.session_id != self._session_id
        ):
            self._finish_terminal(
                ProtocolState.FAILED,
                "Connection lost; physical OFF could not be confirmed. "
                "Manual front-panel intervention is required.",
                warning=True,
            )

    def _session_is_current(self) -> bool:
        snapshot = self._connection.snapshot
        return (
            snapshot.state is ConnectionState.CONNECTED
            and snapshot.session_id == self._session_id
        )

    def _reject(self, message: str) -> bool:
        self._publish(replace(
            self._snapshot,
            state=ProtocolState.FAILED,
            message=message,
            error=True,
        ))
        return False

    def _publish(self, snapshot: ProtocolSnapshot) -> None:
        self._snapshot = snapshot
        self.snapshot_changed.emit(snapshot)
