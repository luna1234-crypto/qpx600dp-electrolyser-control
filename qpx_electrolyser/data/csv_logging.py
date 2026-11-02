"""Independent incremental CSV logging fed only by the shared command queue."""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
from pathlib import Path
import time
from typing import Protocol, TextIO

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot

from qpx_electrolyser.app.connection_controller import (
    ConnectionController,
    ConnectionSnapshot,
    ConnectionState,
)
from qpx_electrolyser.instrument.channel_readback import ChannelReadback, read_channel
from qpx_electrolyser.instrument.connection_worker import CommandPriority


CSV_COLUMNS = (
    "timestamp_iso",
    "elapsed_seconds",
    "channel",
    "programmed_voltage_v",
    "programmed_current_limit_a",
    "measured_voltage_v",
    "measured_current_a",
    "voltage_range",
    "output_state",
    "connection_state",
    "session_id",
    "sample_status",
    "error",
    "protocol_name",
    "protocol_state",
    "scan_name",
    "scan_step_index",
    "scan_total_steps",
    "scan_commanded_setpoint",
    "measured_power_w",
    "current_input_mode",
    "active_area_cm2",
    "commanded_current_density_a_cm2",
    "scan_rate_mv_per_s",
    "scan_step_mv",
    "scan_step_duration_s",
    "scan_settling_time_s",
    "scan_measurements_per_point",
    "measured_current_density_a_cm2",
    "scan_point_valid",
    "scan_point_result_index",
    "polarisation_averaging_window_s",
    "polarisation_voltage_sd_v",
    "polarisation_sample_count",
    "polarisation_active_start_s",
    "polarisation_active_end_s",
    "sequence_name",
    "sequence_run_id",
    "sequence_stage_index",
    "sequence_stage_total",
    "sequence_stage_type",
    "sequence_stage_state",
    "sequence_transition_policy",
    "sequence_handoff_state",
)


class CsvWriter(Protocol):
    path: Path

    def append(
        self,
        *,
        timestamp: datetime,
        elapsed_s: float,
        channel: int,
        readback: ChannelReadback | None,
        connection_state: str,
        session_id: int,
        status: str,
        error: str = "",
        protocol_name: str = "",
        protocol_state: str = "",
        scan_name: str = "",
        scan_step_index: int | None = None,
        scan_total_steps: int | None = None,
        scan_commanded_setpoint: float | None = None,
        current_input_mode: str = "",
        active_area_cm2: float | None = None,
        commanded_current_density_a_cm2: float | None = None,
        scan_rate_mv_per_s: float | None = None,
        scan_step_mv: float | None = None,
        scan_step_duration_s: float | None = None,
        scan_settling_time_s: float | None = None,
        scan_measurements_per_point: int | None = None,
        measured_current_density_a_cm2: float | None = None,
        scan_point_valid: bool | None = None,
        scan_point_result_index: int | None = None,
        polarisation_averaging_window_s: float | None = None,
        polarisation_voltage_sd_v: float | None = None,
        polarisation_sample_count: int | None = None,
        polarisation_active_start_s: float | None = None,
        polarisation_active_end_s: float | None = None,
        sequence_name: str = "",
        sequence_run_id: str = "",
        sequence_stage_index: int | None = None,
        sequence_stage_total: int | None = None,
        sequence_stage_type: str = "",
        sequence_stage_state: str = "",
        sequence_transition_policy: str = "",
        sequence_handoff_state: str = "",
    ) -> None: ...

    def close(self) -> None: ...


class ProtocolLogSink(Protocol):
    """Existing channel logger interface used by a same-channel protocol."""

    @property
    def active(self) -> bool: ...

    @property
    def file_path(self) -> Path | None: ...

    def append_protocol(
        self,
        readback: ChannelReadback | None,
        *,
        status: str,
        protocol_state: str,
        error: str = "",
        protocol_name: str = "constant_current",
        scan_name: str = "",
        scan_step_index: int | None = None,
        scan_total_steps: int | None = None,
        scan_commanded_setpoint: float | None = None,
        current_input_mode: str = "",
        active_area_cm2: float | None = None,
        commanded_current_density_a_cm2: float | None = None,
        scan_rate_mv_per_s: float | None = None,
        scan_step_mv: float | None = None,
        scan_step_duration_s: float | None = None,
        scan_settling_time_s: float | None = None,
        scan_measurements_per_point: int | None = None,
        measured_current_density_a_cm2: float | None = None,
        scan_point_valid: bool | None = None,
        scan_point_result_index: int | None = None,
        polarisation_averaging_window_s: float | None = None,
        polarisation_voltage_sd_v: float | None = None,
        polarisation_sample_count: int | None = None,
        polarisation_active_start_s: float | None = None,
        polarisation_active_end_s: float | None = None,
        sequence_name: str = "",
        sequence_run_id: str = "",
        sequence_stage_index: int | None = None,
        sequence_stage_total: int | None = None,
        sequence_stage_type: str = "",
        sequence_stage_state: str = "",
        sequence_transition_policy: str = "",
        sequence_handoff_state: str = "",
    ) -> None: ...

    def suspend_sampling(self) -> None: ...

    def resume_sampling(self) -> None: ...


class CsvLogFile:
    """One exclusively-created, incrementally flushed channel log."""

    def __init__(self, directory: Path, channel: int, *, created_at: datetime | None = None) -> None:
        if not isinstance(directory, Path):
            directory = Path(directory)
        if not directory.exists():
            raise FileNotFoundError(f"Logging directory does not exist: {directory}")
        if not directory.is_dir():
            raise NotADirectoryError(f"Logging destination is not a directory: {directory}")
        instant = created_at or datetime.now(timezone.utc)
        stamp = instant.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        self.path = directory / f"qpx600dp_ch{channel}_{stamp}.csv"
        self._stream: TextIO | None = self.path.open("x", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._stream, fieldnames=CSV_COLUMNS, extrasaction="raise")
        try:
            self._writer.writeheader()
            self._stream.flush()
        except BaseException:
            self.close()
            raise

    def append(
        self,
        *,
        timestamp: datetime,
        elapsed_s: float,
        channel: int,
        readback: ChannelReadback | None,
        connection_state: str,
        session_id: int,
        status: str,
        error: str = "",
        protocol_name: str = "",
        protocol_state: str = "",
        scan_name: str = "",
        scan_step_index: int | None = None,
        scan_total_steps: int | None = None,
        scan_commanded_setpoint: float | None = None,
        current_input_mode: str = "",
        active_area_cm2: float | None = None,
        commanded_current_density_a_cm2: float | None = None,
        scan_rate_mv_per_s: float | None = None,
        scan_step_mv: float | None = None,
        scan_step_duration_s: float | None = None,
        scan_settling_time_s: float | None = None,
        scan_measurements_per_point: int | None = None,
        measured_current_density_a_cm2: float | None = None,
        scan_point_valid: bool | None = None,
        scan_point_result_index: int | None = None,
        polarisation_averaging_window_s: float | None = None,
        polarisation_voltage_sd_v: float | None = None,
        polarisation_sample_count: int | None = None,
        polarisation_active_start_s: float | None = None,
        polarisation_active_end_s: float | None = None,
        sequence_name: str = "",
        sequence_run_id: str = "",
        sequence_stage_index: int | None = None,
        sequence_stage_total: int | None = None,
        sequence_stage_type: str = "",
        sequence_stage_state: str = "",
        sequence_transition_policy: str = "",
        sequence_handoff_state: str = "",
    ) -> None:
        stream = self._stream
        if stream is None:
            raise ValueError("CSV log is closed")
        values = {
            "timestamp_iso": timestamp.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "elapsed_seconds": f"{elapsed_s:.6f}",
            "channel": str(channel),
            "programmed_voltage_v": "" if readback is None else f"{readback.programmed_voltage_v:.12g}",
            "programmed_current_limit_a": "" if readback is None else f"{readback.current_limit_a:.12g}",
            "measured_voltage_v": "" if readback is None else f"{readback.measured_voltage_v:.12g}",
            "measured_current_a": "" if readback is None else f"{readback.measured_current_a:.12g}",
            "voltage_range": "" if readback is None else readback.voltage_range_name,
            "output_state": "" if readback is None else ("ON" if readback.output_enabled else "OFF"),
            "connection_state": connection_state,
            "session_id": str(session_id),
            "protocol_name": protocol_name,
            "protocol_state": protocol_state,
            "scan_name": scan_name,
            "scan_step_index": "" if scan_step_index is None else str(scan_step_index),
            "scan_total_steps": "" if scan_total_steps is None else str(scan_total_steps),
            "scan_commanded_setpoint": (
                "" if scan_commanded_setpoint is None
                else f"{scan_commanded_setpoint:.12g}"
            ),
            "measured_power_w": (
                "" if readback is None
                else f"{readback.measured_voltage_v * readback.measured_current_a:.12g}"
            ),
            "current_input_mode": current_input_mode,
            "active_area_cm2": (
                "" if active_area_cm2 is None else f"{active_area_cm2:.12g}"
            ),
            "commanded_current_density_a_cm2": (
                "" if commanded_current_density_a_cm2 is None
                else f"{commanded_current_density_a_cm2:.12g}"
            ),
            "scan_rate_mv_per_s": (
                "" if scan_rate_mv_per_s is None else f"{scan_rate_mv_per_s:.12g}"
            ),
            "scan_step_mv": "" if scan_step_mv is None else f"{scan_step_mv:.12g}",
            "scan_step_duration_s": (
                "" if scan_step_duration_s is None else f"{scan_step_duration_s:.12g}"
            ),
            "scan_settling_time_s": (
                "" if scan_settling_time_s is None else f"{scan_settling_time_s:.12g}"
            ),
            "scan_measurements_per_point": (
                "" if scan_measurements_per_point is None else str(scan_measurements_per_point)
            ),
            "measured_current_density_a_cm2": (
                "" if measured_current_density_a_cm2 is None
                else f"{measured_current_density_a_cm2:.12g}"
            ),
            "scan_point_valid": (
                "" if scan_point_valid is None else ("true" if scan_point_valid else "false")
            ),
            "scan_point_result_index": (
                "" if scan_point_result_index is None else str(scan_point_result_index)
            ),
            "polarisation_averaging_window_s": (
                "" if polarisation_averaging_window_s is None
                else f"{polarisation_averaging_window_s:.12g}"
            ),
            "polarisation_voltage_sd_v": (
                "" if polarisation_voltage_sd_v is None
                else f"{polarisation_voltage_sd_v:.12g}"
            ),
            "polarisation_sample_count": (
                "" if polarisation_sample_count is None else str(polarisation_sample_count)
            ),
            "polarisation_active_start_s": (
                "" if polarisation_active_start_s is None
                else f"{polarisation_active_start_s:.12g}"
            ),
            "polarisation_active_end_s": (
                "" if polarisation_active_end_s is None
                else f"{polarisation_active_end_s:.12g}"
            ),
            "sequence_name": sequence_name,
            "sequence_run_id": sequence_run_id,
            "sequence_stage_index": (
                "" if sequence_stage_index is None else str(sequence_stage_index)
            ),
            "sequence_stage_total": (
                "" if sequence_stage_total is None else str(sequence_stage_total)
            ),
            "sequence_stage_type": sequence_stage_type,
            "sequence_stage_state": sequence_stage_state,
            "sequence_transition_policy": sequence_transition_policy,
            "sequence_handoff_state": sequence_handoff_state,
            "sample_status": status,
            "error": error,
        }
        self._writer.writerow(values)
        stream.flush()

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.close()


@dataclass(frozen=True, slots=True)
class LoggingSnapshot:
    channel: int
    active: bool = False
    pending: bool = False
    file_path: Path | None = None
    sample_count: int = 0
    missed_samples: int = 0
    message: str = "Logging stopped"
    error: bool = False


class ChannelLoggingController(QObject):
    """Schedule one channel without blocking Qt or bypassing the shared worker."""

    snapshot_changed = Signal(object)
    sample_received = Signal(object)

    def __init__(
        self,
        channel: int,
        connection: ConnectionController,
        parent: QObject | None = None,
        *,
        monotonic=time.monotonic,
        utc_now=lambda: datetime.now(timezone.utc),
        writer_factory=CsvLogFile,
    ) -> None:
        super().__init__(parent)
        if channel not in (1, 2):
            raise ValueError("channel must be 1 or 2")
        self._channel = channel
        self._connection = connection
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._writer_factory = writer_factory
        self._writer: CsvWriter | None = None
        self._interval_s = 1.0
        self._started_at = 0.0
        self._next_slot = 0
        self._pending_request_id: int | None = None
        self._sampling_suspended = False
        self._cancellation_key = f"csv-logger-channel-{channel}"
        self._snapshot = LoggingSnapshot(channel=channel)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._submit_due_sample)
        connection.snapshot_changed.connect(self._connection_changed)
        connection.command_completed.connect(self._command_completed)
        connection.command_failed.connect(self._command_failed)

    @property
    def snapshot(self) -> LoggingSnapshot:
        return self._snapshot

    @property
    def active(self) -> bool:
        return self._snapshot.active

    @property
    def file_path(self) -> Path | None:
        return self._snapshot.file_path

    def start(self, directory: Path, interval_s: float) -> bool:
        if self._snapshot.active:
            return False
        if self._connection.snapshot.state is not ConnectionState.CONNECTED:
            self._publish(replace(self._snapshot, message="Connect before starting logging.", error=True))
            return False
        if isinstance(interval_s, bool) or not math.isfinite(interval_s) or not 0.1 <= interval_s <= 3600:
            self._publish(replace(
                self._snapshot,
                message="Logging interval must be finite and between 0.1 and 3600 seconds.",
                error=True,
            ))
            return False
        try:
            writer = self._writer_factory(Path(directory), self._channel)
        except (OSError, ValueError) as error:
            self._publish(replace(self._snapshot, message=f"Cannot start logging: {error}", error=True))
            return False
        self._writer = writer
        self._interval_s = float(interval_s)
        self._started_at = self._monotonic()
        self._next_slot = 0
        self._pending_request_id = None
        self._sampling_suspended = False
        self._publish(LoggingSnapshot(
            channel=self._channel,
            active=True,
            file_path=writer.path,
            message=f"Logging every {self._interval_s:g} s",
        ))
        self._timer.start(0)
        return True

    def stop(self, message: str = "Logging stopped") -> None:
        self._timer.stop()
        self._connection.cancel_commands(self._cancellation_key)
        self._pending_request_id = None
        self._sampling_suspended = False
        writer, self._writer = self._writer, None
        close_error: OSError | None = None
        if writer is not None:
            try:
                writer.close()
            except OSError as error:
                close_error = error
        self._publish(replace(
            self._snapshot,
            active=False,
            pending=False,
            message=f"{message}; close failed: {close_error}" if close_error else message,
            error=self._snapshot.error or close_error is not None,
        ))

    def shutdown(self) -> None:
        self.stop("Logging closed for application shutdown")

    def append_protocol(
        self,
        readback: ChannelReadback | None,
        *,
        status: str,
        protocol_state: str,
        error: str = "",
        protocol_name: str = "constant_current",
        scan_name: str = "",
        scan_step_index: int | None = None,
        scan_total_steps: int | None = None,
        scan_commanded_setpoint: float | None = None,
        current_input_mode: str = "",
        active_area_cm2: float | None = None,
        commanded_current_density_a_cm2: float | None = None,
        scan_rate_mv_per_s: float | None = None,
        scan_step_mv: float | None = None,
        scan_step_duration_s: float | None = None,
        scan_settling_time_s: float | None = None,
        scan_measurements_per_point: int | None = None,
        measured_current_density_a_cm2: float | None = None,
        scan_point_valid: bool | None = None,
        scan_point_result_index: int | None = None,
        polarisation_averaging_window_s: float | None = None,
        polarisation_voltage_sd_v: float | None = None,
        polarisation_sample_count: int | None = None,
        polarisation_active_start_s: float | None = None,
        polarisation_active_end_s: float | None = None,
        sequence_name: str = "",
        sequence_run_id: str = "",
        sequence_stage_index: int | None = None,
        sequence_stage_total: int | None = None,
        sequence_stage_type: str = "",
        sequence_stage_state: str = "",
        sequence_transition_policy: str = "",
        sequence_handoff_state: str = "",
    ) -> None:
        """Append a protocol row through this logger's one open writer.

        Logger and protocol callbacks execute on the Qt main thread, while every
        measurement that supplies ``readback`` remains serialized by the shared
        instrument queue.
        """
        writer = self._writer
        if not self._snapshot.active or writer is None:
            raise ValueError("Active channel CSV logger is unavailable")
        connection = self._connection.snapshot
        try:
            writer.append(
                timestamp=self._utc_now(),
                elapsed_s=max(0.0, self._monotonic() - self._started_at),
                channel=self._channel,
                readback=readback,
                connection_state=connection.state.value,
                session_id=connection.session_id,
                status=status,
                error=error,
                protocol_name=protocol_name,
                protocol_state=protocol_state,
                scan_name=scan_name,
                scan_step_index=scan_step_index,
                scan_total_steps=scan_total_steps,
                scan_commanded_setpoint=scan_commanded_setpoint,
                current_input_mode=current_input_mode,
                active_area_cm2=active_area_cm2,
                commanded_current_density_a_cm2=commanded_current_density_a_cm2,
                scan_rate_mv_per_s=scan_rate_mv_per_s,
                scan_step_mv=scan_step_mv,
                scan_step_duration_s=scan_step_duration_s,
                scan_settling_time_s=scan_settling_time_s,
                scan_measurements_per_point=scan_measurements_per_point,
                measured_current_density_a_cm2=measured_current_density_a_cm2,
                scan_point_valid=scan_point_valid,
                scan_point_result_index=scan_point_result_index,
                polarisation_averaging_window_s=polarisation_averaging_window_s,
                polarisation_voltage_sd_v=polarisation_voltage_sd_v,
                polarisation_sample_count=polarisation_sample_count,
                polarisation_active_start_s=polarisation_active_start_s,
                polarisation_active_end_s=polarisation_active_end_s,
                sequence_name=sequence_name,
                sequence_run_id=sequence_run_id,
                sequence_stage_index=sequence_stage_index,
                sequence_stage_total=sequence_stage_total,
                sequence_stage_type=sequence_stage_type,
                sequence_stage_state=sequence_stage_state,
                sequence_transition_policy=sequence_transition_policy,
                sequence_handoff_state=sequence_handoff_state,
            )
        except (OSError, ValueError) as append_error:
            self._fail_file(append_error)
            raise
        self._publish(replace(
            self._snapshot,
            sample_count=self._snapshot.sample_count + 1,
            message=f"Logged {protocol_name.replace('_', ' ').title()} {status} row",
            error=False,
        ))

    def suspend_sampling(self) -> None:
        """Temporarily suppress standalone polls while a hardware output settles."""
        if not self._snapshot.active or self._sampling_suspended:
            return
        self._sampling_suspended = True
        self._timer.stop()
        # A queued poll is cancelled. An already-running poll remains atomic and
        # necessarily completes before the later output-transition command.
        if self._connection.cancel_commands(self._cancellation_key):
            self._pending_request_id = None
            self._publish(replace(self._snapshot, pending=False))

    def resume_sampling(self) -> None:
        """Resume the same file and monotonic logging schedule after settling."""
        if not self._snapshot.active or not self._sampling_suspended:
            return
        self._sampling_suspended = False
        if self._pending_request_id is None:
            self._schedule_next()

    @Slot()
    def _submit_due_sample(self) -> None:
        if (
            not self._snapshot.active
            or self._sampling_suspended
            or self._pending_request_id is not None
        ):
            return
        if self._connection.snapshot.state is not ConnectionState.CONNECTED:
            self._stop_with_event("disconnected", "Connection is not available.")
            return
        elapsed = max(0.0, self._monotonic() - self._started_at)
        current_slot = math.floor(elapsed / self._interval_s)
        if current_slot > self._next_slot:
            self._publish(replace(
                self._snapshot,
                missed_samples=self._snapshot.missed_samples + current_slot - self._next_slot,
            ))
            self._next_slot = current_slot
        ticket = self._connection.submit_command(
            f"ch{self._channel}-log-sample",
            lambda supply: read_channel(supply, self._channel),
            priority=CommandPriority.BACKGROUND,
            cancellation_key=self._cancellation_key,
        )
        if ticket is None:
            self._stop_with_event("queue-error", "Shared command queue rejected the sample.")
            return
        self._pending_request_id = ticket.request_id
        self._next_slot += 1
        self._publish(replace(self._snapshot, pending=True, message="Reading sample..."))

    @Slot(int, str, object)
    def _command_completed(self, request_id: int, _name: str, result: object) -> None:
        if request_id != self._pending_request_id or not self._snapshot.active:
            return
        self._pending_request_id = None
        if not isinstance(result, ChannelReadback) or result.channel != self._channel:
            self._stop_with_event("malformed", "Queued measurement returned malformed channel data.")
            return
        try:
            self._append(result, "ok")
        except (OSError, ValueError) as error:
            self._fail_file(error)
            return
        self.sample_received.emit(result)
        self._publish(replace(
            self._snapshot,
            pending=False,
            sample_count=self._snapshot.sample_count + 1,
            message=f"Logged sample {self._snapshot.sample_count + 1}",
            error=False,
        ))
        if not self._sampling_suspended:
            self._schedule_next()

    @Slot(int, str, object)
    def _command_failed(self, request_id: int, _name: str, error: BaseException) -> None:
        if request_id != self._pending_request_id or not self._snapshot.active:
            return
        self._pending_request_id = None
        self._stop_with_event("backend-error", str(error) or type(error).__name__)

    @Slot(object)
    def _connection_changed(self, snapshot: ConnectionSnapshot) -> None:
        if self._snapshot.active and snapshot.state is not ConnectionState.CONNECTED:
            self._stop_with_event(snapshot.state.value, snapshot.message)

    def _schedule_next(self) -> None:
        if not self._snapshot.active or self._sampling_suspended:
            return
        now = self._monotonic()
        elapsed = max(0.0, now - self._started_at)
        next_due_slot = math.floor(elapsed / self._interval_s) + 1
        if next_due_slot > self._next_slot:
            self._publish(replace(
                self._snapshot,
                missed_samples=self._snapshot.missed_samples + next_due_slot - self._next_slot,
            ))
            self._next_slot = next_due_slot
        delay_s = max(0.0, self._started_at + self._next_slot * self._interval_s - now)
        self._timer.start(max(0, math.ceil(delay_s * 1000)))

    def _append(self, readback: ChannelReadback | None, status: str, error: str = "") -> None:
        writer = self._writer
        if writer is None:
            raise ValueError("CSV writer is unavailable")
        connection = self._connection.snapshot
        writer.append(
            timestamp=self._utc_now(),
            elapsed_s=max(0.0, self._monotonic() - self._started_at),
            channel=self._channel,
            readback=readback,
            connection_state=connection.state.value,
            session_id=connection.session_id,
            status=status,
            error=error,
        )

    def _stop_with_event(self, status: str, message: str) -> None:
        try:
            if self._writer is not None:
                self._append(None, status, message)
        except (OSError, ValueError) as error:
            message = f"{message}; could not record final status: {error}"
        self._publish(replace(self._snapshot, error=True))
        self.stop(f"Logging stopped: {message}")

    def _fail_file(self, error: BaseException) -> None:
        self._publish(replace(self._snapshot, error=True))
        self.stop(f"Logging stopped after file error: {error}")

    def _publish(self, snapshot: LoggingSnapshot) -> None:
        self._snapshot = snapshot
        self.snapshot_changed.emit(snapshot)
