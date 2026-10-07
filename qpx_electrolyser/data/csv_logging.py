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
    ) -> None: ...

    def close(self) -> None: ...


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

    @Slot()
    def _submit_due_sample(self) -> None:
        if not self._snapshot.active or self._pending_request_id is not None:
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
        if not self._snapshot.active:
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
