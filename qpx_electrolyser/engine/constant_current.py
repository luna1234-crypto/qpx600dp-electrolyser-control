"""Independent Simulation Mode constant-current protocol state machines."""

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
from qpx_electrolyser.data.csv_logging import CsvLogFile, CsvWriter
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
    """Explicit Milestone 9 Simulation Mode policy, below hardware ratings."""

    max_voltage_v: float = 10.0
    max_current_a: float = 5.0
    max_power_w: float = 50.0
    max_duration_s: float = 86_400.0
    min_interval_s: float = 0.1
    max_interval_s: float = 3_600.0


DEFAULT_CONSTANT_CURRENT_LIMITS = ConstantCurrentSafetyLimits()


@dataclass(frozen=True, slots=True)
class ConstantCurrentConfig:
    target_current_a: float
    voltage_compliance_v: float
    duration_s: float
    interval_s: float
    voltage_range_name: str


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

    @property
    def owns_channel(self) -> bool:
        return self.state in OWNING_STATES


@dataclass(frozen=True, slots=True)
class _StartResult:
    programmed_voltage_v: float
    programmed_current_a: float
    output_enabled: bool


class OutputInitiallyOnError(RuntimeError):
    pass


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
        monotonic=time.monotonic,
        utc_now=lambda: datetime.now(timezone.utc),
        writer_factory=CsvLogFile,
    ) -> None:
        super().__init__(parent)
        QPX600DP_CAPABILITIES.validate_channel(channel)
        self._channel = channel
        self._connection = connection
        self._limits = safety_limits
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._writer_factory = writer_factory
        self._snapshot = ProtocolSnapshot(channel=channel)
        self._writer: CsvWriter | None = None
        self._session_id = 0
        self._pending_request_id: int | None = None
        self._pending_action = ""
        self._action_key = f"constant-current-action-{channel}"
        self._sample_key = f"constant-current-sample-{channel}"
        self._run_started_at = 0.0
        self._active_before_run_s = 0.0
        self._next_sample_slot = 0
        self._terminal_after_stop = ProtocolState.COMPLETED
        self._terminal_message = ""

        self._sample_timer = QTimer(self)
        self._sample_timer.setSingleShot(True)
        self._sample_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._sample_timer.timeout.connect(self._submit_sample)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(100)
        self._clock_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._clock_timer.timeout.connect(self._update_clock)

        connection.snapshot_changed.connect(self._connection_changed)
        connection.command_completed.connect(self._command_completed)
        connection.command_failed.connect(self._command_failed)

    @property
    def snapshot(self) -> ProtocolSnapshot:
        return self._snapshot

    def start(
        self,
        target_current: str,
        voltage_compliance: str,
        duration: str,
        interval: str,
        voltage_range_name: str | None,
        directory: Path,
        *,
        standalone_logging_active: bool = False,
    ) -> bool:
        if self._snapshot.owns_channel:
            return False
        if self._connection.snapshot.state is not ConnectionState.CONNECTED:
            return self._reject("Connect and initialize the simulator before starting.")
        if not self._connection.snapshot.simulation_mode:
            return self._reject("Constant Current protocols are Simulation Mode only.")
        if standalone_logging_active:
            return self._reject("Stop standalone logging on this channel before starting a protocol.")
        if voltage_range_name is None:
            return self._reject("Refresh the channel before starting a protocol.")
        try:
            config = self._validate_config(
                target_current, voltage_compliance, duration, interval, voltage_range_name
            )
        except (ValueError, InstrumentValueError) as error:
            return self._reject(str(error))
        try:
            writer = self._writer_factory(Path(directory), self._channel)
        except (OSError, ValueError) as error:
            return self._reject(f"Cannot create protocol CSV: {error}")

        self._writer = writer
        self._session_id = self._connection.snapshot.session_id
        self._active_before_run_s = 0.0
        self._run_started_at = 0.0
        self._next_sample_slot = 0
        self._publish(ProtocolSnapshot(
            channel=self._channel,
            state=ProtocolState.STARTING,
            config=config,
            remaining_active_s=config.duration_s,
            file_path=writer.path,
            message="Checking OFF state and applying constant-current settings...",
        ))
        return self._submit_action("start", lambda supply: self._start_operation(supply, config))

    @Slot()
    def pause(self) -> None:
        if self._snapshot.state is not ProtocolState.RUNNING:
            return
        self._sample_timer.stop()
        self._connection.cancel_commands(self._sample_key)
        self._publish(replace(
            self._snapshot,
            state=ProtocolState.PAUSING,
            message="Turning the selected simulated output OFF...",
        ))
        self._submit_action("pause", self._output_off_operation, priority=CommandPriority.SAFETY)

    @Slot()
    def resume(self) -> None:
        if self._snapshot.state is not ProtocolState.PAUSED or self._snapshot.config is None:
            return
        if self._remaining_active() <= 0:
            self._finish_terminal(ProtocolState.COMPLETED, "Protocol duration completed")
            return
        if not self._session_is_current():
            self._finish_terminal(
                ProtocolState.FAILED,
                "Cannot resume: the connected session changed.",
                warning=True,
            )
            return
        config = self._snapshot.config
        self._publish(replace(
            self._snapshot,
            state=ProtocolState.RESUMING,
            message="Revalidating settings before simulated Output On...",
        ))
        self._submit_action("resume", lambda supply: self._resume_operation(supply, config))

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
    ) -> ConstantCurrentConfig:
        current = self._number(target_current, "Target current")
        voltage = self._number(voltage_compliance, "Voltage compliance")
        duration_s = self._number(duration, "Duration")
        interval_s = self._number(interval, "Measurement interval")
        current = QPX600DP_CAPABILITIES.validate_current(current)
        voltage = QPX600DP_CAPABILITIES.validate_voltage(voltage, voltage_range_name)
        limits = self._limits
        if current > limits.max_current_a:
            raise ValueError(f"Target current exceeds the {limits.max_current_a:g} A project limit.")
        if voltage > limits.max_voltage_v:
            raise ValueError(
                f"Voltage compliance exceeds the {limits.max_voltage_v:g} V project limit."
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
        return ConstantCurrentConfig(current, voltage, duration_s, interval_s, voltage_range_name)

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

    def _start_operation(
        self, supply: PowerSupplyInterface, config: ConstantCurrentConfig
    ) -> _StartResult:
        if supply.read_output_state(self._channel):
            raise OutputInitiallyOnError("Selected output is already ON; protocol start refused.")
        supply.set_voltage(self._channel, config.voltage_compliance_v)
        supply.set_current(self._channel, config.target_current_a)
        voltage = supply.read_set_voltage(self._channel)
        current = supply.read_set_current(self._channel)
        self._verify_settings(voltage, current, config)
        supply.output_on(self._channel)
        output_enabled = supply.read_output_state(self._channel)
        if not output_enabled:
            raise OutputStateError("Output On could not be confirmed.")
        return _StartResult(voltage, current, output_enabled)

    def _resume_operation(
        self, supply: PowerSupplyInterface, config: ConstantCurrentConfig
    ) -> _StartResult:
        if supply.read_output_state(self._channel):
            raise OutputStateError("Selected output was not OFF before resume.")
        voltage = supply.read_set_voltage(self._channel)
        current = supply.read_set_current(self._channel)
        self._verify_settings(voltage, current, config)
        supply.output_on(self._channel)
        output_enabled = supply.read_output_state(self._channel)
        if not output_enabled:
            raise OutputStateError("Output On could not be confirmed during resume.")
        return _StartResult(voltage, current, output_enabled)

    def _verify_settings(
        self, voltage: float, current: float, config: ConstantCurrentConfig
    ) -> None:
        voltage_resolution = QPX600DP_CAPABILITIES.voltage_range(
            config.voltage_range_name
        ).programming_resolution_v
        if not _matches(voltage, config.voltage_compliance_v, voltage_resolution):
            raise SettingsMismatchError("Voltage compliance read-back mismatch.")
        if not _matches(
            current,
            config.target_current_a,
            QPX600DP_CAPABILITIES.current_programming_resolution_a,
        ):
            raise SettingsMismatchError("Current target read-back mismatch.")

    def _output_off_operation(self, supply: PowerSupplyInterface) -> ChannelReadback:
        supply.output_off(self._channel)
        readback = read_channel(supply, self._channel)
        if readback.output_enabled:
            raise OutputStateError("Output OFF could not be confirmed.")
        return readback

    def _submit_action(
        self,
        action: str,
        operation,
        *,
        priority: CommandPriority = CommandPriority.NORMAL,
    ) -> bool:
        ticket = self._connection.submit_command(
            f"ch{self._channel}-constant-current-{action}",
            operation,
            priority=priority,
            cancellation_key=self._action_key,
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
        current_slot = math.floor(elapsed / self._snapshot.config.interval_s)
        if current_slot > self._next_sample_slot:
            self._publish(replace(
                self._snapshot,
                missed_samples=self._snapshot.missed_samples + current_slot - self._next_sample_slot,
            ))
            self._next_sample_slot = current_slot
        ticket = self._connection.submit_command(
            f"ch{self._channel}-constant-current-sample",
            lambda supply: read_channel(supply, self._channel),
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
        if action in {"start", "resume"}:
            if not isinstance(result, _StartResult) or not result.output_enabled:
                self._begin_cleanup(ProtocolState.FAILED, "Malformed Output-On result.")
                return
            if not self._session_is_current():
                self._begin_cleanup(ProtocolState.FAILED, "Connected session changed during start.")
                return
            self._run_started_at = self._monotonic()
            self._publish(replace(
                self._snapshot,
                state=ProtocolState.RUNNING,
                output_enabled=True,
                message="Constant Current protocol running",
                error=False,
                operator_warning=False,
            ))
            self._clock_timer.start()
            self._sample_timer.start(0)
            return
        if action == "sample":
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
        if action == "pause":
            if (
                not isinstance(result, ChannelReadback)
                or result.channel != self._channel
                or result.output_enabled
            ):
                self._begin_cleanup(
                    ProtocolState.FAILED,
                    "Malformed or unconfirmed Output-Off read-back during pause.",
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
                message="Protocol paused; selected output confirmed OFF",
            ))
            self._append_event_best_effort("paused", ProtocolState.PAUSED)
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
        if action == "start" and isinstance(error, OutputInitiallyOnError):
            self._finish_terminal(ProtocolState.FAILED, message)
            return
        if action == "stop":
            self._finish_terminal(
                ProtocolState.FAILED,
                f"Output-Off cleanup failed: {message}",
                warning=True,
            )
            return
        self._begin_cleanup(ProtocolState.FAILED, f"Protocol {action} failed: {message}")

    def _begin_cleanup(self, terminal: ProtocolState, message: str) -> None:
        if self._snapshot.state is ProtocolState.STOPPING:
            return
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
            elapsed_active_s=self._active_before_run_s,
            remaining_active_s=self._remaining_active(),
            message="Attempting selected-channel Output Off...",
            error=terminal is ProtocolState.FAILED,
        ))
        if not self._session_is_current():
            self._finish_terminal(
                ProtocolState.FAILED,
                f"{message}; physical OFF could not be confirmed because the session is unavailable.",
                warning=True,
            )
            return
        self._submit_action("stop", self._output_off_operation, priority=CommandPriority.SAFETY)

    def _finish_terminal(
        self,
        state: ProtocolState,
        message: str,
        *,
        warning: bool = False,
    ) -> None:
        self._sample_timer.stop()
        self._clock_timer.stop()
        self._connection.cancel_commands(self._sample_key)
        self._connection.cancel_commands(self._action_key)
        self._pending_request_id = None
        self._pending_action = ""
        self._append_event_best_effort("completed" if state is ProtocolState.COMPLETED else "error", state, message)
        writer, self._writer = self._writer, None
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
        due_slot = math.floor(elapsed / self._snapshot.config.interval_s) + 1
        if due_slot > self._next_sample_slot:
            self._publish(replace(
                self._snapshot,
                missed_samples=self._snapshot.missed_samples + due_slot - self._next_sample_slot,
            ))
            self._next_sample_slot = due_slot
        next_active_s = self._next_sample_slot * self._snapshot.config.interval_s
        delay_s = max(0.0, next_active_s - elapsed)
        delay_s = min(delay_s, self._remaining_active())
        self._sample_timer.start(max(0, math.ceil(delay_s * 1000)))

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
            protocol_name="constant_current",
            protocol_state=state.value,
        )

    def _append_event_best_effort(
        self,
        status: str,
        state: ProtocolState,
        error: str = "",
    ) -> None:
        if self._writer is None:
            return
        try:
            self._append(None, status, state, error)
        except (OSError, ValueError):
            pass

    @Slot(object)
    def _connection_changed(self, snapshot: ConnectionSnapshot) -> None:
        if self._snapshot.owns_channel and (
            snapshot.state is not ConnectionState.CONNECTED
            or snapshot.session_id != self._session_id
        ):
            self._finish_terminal(
                ProtocolState.FAILED,
                "Connection lost; physical OFF could not be confirmed.",
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
