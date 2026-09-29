"""Qt-facing state controller for asynchronous instrument connection work."""

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from enum import Enum
import logging

from PySide6.QtCore import QObject, Qt, Signal, Slot

from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.connection_worker import (
    BackendOperation,
    CommandPriority,
    CommandTicket,
    CommandWorkerError,
    InstrumentCommandWorker,
)
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ConnectionSnapshot:
    """Immutable display state emitted only from the GUI thread."""

    state: ConnectionState
    simulation_mode: bool
    instrument_id: str | None
    message: str


class _WorkerSignals(QObject):
    connected = Signal(str)
    disconnected = Signal()
    failed = Signal(str, str)
    command_completed = Signal(int, str, object)
    command_failed = Signal(int, str, object)


class ConnectionController(QObject):
    """Bridge the one shared backend-owner worker into queued Qt signals."""

    snapshot_changed = Signal(object)
    error_reported = Signal(str)
    command_completed = Signal(int, str, object)
    command_failed = Signal(int, str, object)

    def __init__(
        self,
        backend_factory: Callable[[], PowerSupplyInterface] = SimulatedPowerSupply,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._logger = logging.getLogger("qpx_electrolyser.app.connection_controller")
        self._snapshot = ConnectionSnapshot(
            state=ConnectionState.DISCONNECTED,
            simulation_mode=True,
            instrument_id=None,
            message="Simulation mode ready. No hardware I/O is performed.",
        )
        self._shutting_down = False
        self._worker_signals = _WorkerSignals()
        self._worker_signals.connected.connect(
            self._handle_connected, Qt.ConnectionType.QueuedConnection
        )
        self._worker_signals.disconnected.connect(
            self._handle_disconnected, Qt.ConnectionType.QueuedConnection
        )
        self._worker_signals.failed.connect(
            self._handle_failed, Qt.ConnectionType.QueuedConnection
        )
        self._worker_signals.command_completed.connect(
            self._handle_command_completed, Qt.ConnectionType.QueuedConnection
        )
        self._worker_signals.command_failed.connect(
            self._handle_command_failed, Qt.ConnectionType.QueuedConnection
        )
        self._worker = InstrumentCommandWorker(
            backend_factory,
            on_connected=self._worker_signals.connected.emit,
            on_disconnected=self._worker_signals.disconnected.emit,
            on_failed=self._worker_signals.failed.emit,
        )

    @property
    def snapshot(self) -> ConnectionSnapshot:
        return self._snapshot

    @Slot(bool)
    def set_simulation_mode(self, enabled: bool) -> None:
        if self._shutting_down or self._snapshot.state not in {
            ConnectionState.DISCONNECTED,
            ConnectionState.ERROR,
        }:
            return
        message = (
            "Simulation mode ready. No hardware I/O is performed."
            if enabled
            else "Physical connection is not available in this GUI; the Milestone 5 driver is offline-only."
        )
        self._publish(
            ConnectionSnapshot(ConnectionState.DISCONNECTED, enabled, None, message)
        )

    @Slot()
    def request_connect(self) -> None:
        if self._shutting_down or self._snapshot.state not in {
            ConnectionState.DISCONNECTED,
            ConnectionState.ERROR,
        }:
            return
        if not self._snapshot.simulation_mode:
            self._reject("Enable Simulation Mode; physical GUI connection is not implemented.")
            return
        self._publish(
            ConnectionSnapshot(
                ConnectionState.CONNECTING,
                True,
                None,
                "Connecting to the simulator...",
            )
        )
        if not self._worker.request_connect():
            self._handle_failed("connect", "The connection worker is not accepting requests.")

    @Slot()
    def request_disconnect(self) -> None:
        if self._shutting_down or self._snapshot.state is not ConnectionState.CONNECTED:
            return
        self._publish(
            ConnectionSnapshot(
                ConnectionState.DISCONNECTING,
                self._snapshot.simulation_mode,
                self._snapshot.instrument_id,
                "Disconnecting from the simulator...",
            )
        )
        if not self._worker.request_disconnect():
            self._handle_failed("disconnect", "The connection worker is not accepting requests.")

    def submit_command(
        self,
        name: str,
        operation: BackendOperation[object],
        *,
        priority: CommandPriority = CommandPriority.NORMAL,
        cancellation_key: Hashable | None = None,
    ) -> CommandTicket[object] | None:
        """Submit without blocking; completion is delivered on the Qt main thread."""
        if self._shutting_down or self._snapshot.state is not ConnectionState.CONNECTED:
            self._reject("Connect the simulator before submitting an instrument command.")
            return None
        try:
            return self._worker.submit(
                name,
                operation,
                priority=priority,
                cancellation_key=cancellation_key,
                on_result=self._worker_signals.command_completed.emit,
                on_error=self._worker_signals.command_failed.emit,
            )
        except (CommandWorkerError, TypeError, ValueError) as error:
            self._reject(str(error))
            return None

    def cancel_commands(self, cancellation_key: Hashable | None = None) -> int:
        """Cancel queued commands without interrupting an atomic in-flight call."""
        return self._worker.cancel_pending(cancellation_key)

    def shutdown(self, timeout_s: float = 2.0) -> bool:
        """Stop accepting requests and bound application teardown time."""
        self._shutting_down = True
        stopped = self._worker.shutdown(timeout_s)
        if stopped:
            self._logger.info("Connection worker stopped cleanly")
        else:
            self._logger.error(
                "Connection worker did not stop within %.3f s; daemon thread left for process exit",
                timeout_s,
            )
        return stopped

    @Slot(str)
    def _handle_connected(self, instrument_id: str) -> None:
        if self._shutting_down:
            return
        self._publish(
            ConnectionSnapshot(
                ConnectionState.CONNECTED,
                True,
                instrument_id,
                "Simulator connected. Manual channel controls are available.",
            )
        )

    @Slot()
    def _handle_disconnected(self) -> None:
        if self._shutting_down:
            return
        self._publish(
            ConnectionSnapshot(
                ConnectionState.DISCONNECTED,
                self._snapshot.simulation_mode,
                None,
                "Simulator disconnected. Output state is not inferred from disconnection.",
            )
        )

    @Slot(str, str)
    def _handle_failed(self, operation: str, message: str) -> None:
        if self._shutting_down:
            return
        self._logger.error("Connection operation failed: operation=%s; message=%s", operation, message)
        self._publish(
            ConnectionSnapshot(
                ConnectionState.ERROR,
                self._snapshot.simulation_mode,
                None,
                message,
            )
        )
        self.error_reported.emit(message)

    @Slot(int, str, object)
    def _handle_command_completed(self, request_id: int, name: str, result: object) -> None:
        if not self._shutting_down:
            self.command_completed.emit(request_id, name, result)

    @Slot(int, str, object)
    def _handle_command_failed(
        self, request_id: int, name: str, error: BaseException
    ) -> None:
        if self._shutting_down:
            return
        self._logger.error(
            "Instrument command failed: request_id=%d; name=%s; error=%s",
            request_id,
            name,
            error,
        )
        self.command_failed.emit(request_id, name, error)

    def _reject(self, message: str) -> None:
        self._logger.warning("Connection request rejected: %s", message)
        self.error_reported.emit(message)

    def _publish(self, snapshot: ConnectionSnapshot) -> None:
        self._snapshot = snapshot
        self.snapshot_changed.emit(snapshot)
