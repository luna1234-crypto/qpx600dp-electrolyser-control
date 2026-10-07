"""Qt-facing controller for guarded simulation or explicit-port sessions."""

from collections.abc import Callable, Hashable
from dataclasses import dataclass, replace
from enum import Enum
import logging
import re

from PySide6.QtCore import QObject, Qt, Signal, Slot

from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.channel_readback import ChannelReadback, read_channel
from qpx_electrolyser.instrument.connection_worker import (
    BackendOperation,
    CommandPriority,
    CommandTicket,
    CommandWorkerError,
    InstrumentCommandWorker,
)
from qpx_electrolyser.instrument.exceptions import InstrumentProtocolError
from qpx_electrolyser.instrument.qpx600dp import QPX600DPDriver
from qpx_electrolyser.instrument.serial_transport import SerialTransport
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ConnectionSnapshot:
    """Immutable display/session state emitted only from the GUI thread."""

    state: ConnectionState
    simulation_mode: bool
    port_name: str
    instrument_id: str | None
    initial_readbacks: tuple[ChannelReadback, ...]
    message: str
    session_id: int = 0

    @property
    def mode_label(self) -> str:
        return "SIMULATION - NO HARDWARE I/O" if self.simulation_mode else "REAL HARDWARE"

    @property
    def real_port_valid(self) -> bool:
        return bool(re.fullmatch(r"COM[1-9]\d*", self.port_name, re.IGNORECASE))


@dataclass(frozen=True, slots=True)
class _ConnectionReady:
    identification: str
    readbacks: tuple[ChannelReadback, ChannelReadback]


class _WorkerSignals(QObject):
    connected = Signal(object)
    disconnected = Signal()
    failed = Signal(str, str)
    command_completed = Signal(int, str, object)
    command_failed = Signal(int, str, object)


RealBackendFactory = Callable[[str], PowerSupplyInterface]


def _physical_backend(port_name: str) -> PowerSupplyInterface:
    """Construct, but do not open, the explicitly selected physical backend."""
    return QPX600DPDriver(SerialTransport(port_name))


class ConnectionController(QObject):
    """Bridge one dynamically selected backend into the shared owner worker."""

    snapshot_changed = Signal(object)
    error_reported = Signal(str)
    command_completed = Signal(int, str, object)
    command_failed = Signal(int, str, object)

    def __init__(
        self,
        simulation_backend_factory: Callable[[], PowerSupplyInterface] = SimulatedPowerSupply,
        parent: QObject | None = None,
        *,
        real_backend_factory: RealBackendFactory = _physical_backend,
    ) -> None:
        super().__init__(parent)
        if not callable(simulation_backend_factory) or not callable(real_backend_factory):
            raise TypeError("backend factories must be callable")
        self._logger = logging.getLogger("qpx_electrolyser.app.connection_controller")
        self._simulation_backend_factory = simulation_backend_factory
        self._real_backend_factory = real_backend_factory
        self._snapshot = ConnectionSnapshot(
            state=ConnectionState.DISCONNECTED,
            simulation_mode=True,
            port_name="",
            instrument_id=None,
            initial_readbacks=(),
            message="Simulation Mode ready. No hardware I/O is performed.",
        )
        self._shutting_down = False
        self._session_id = 0
        self._failure_during_disconnect: str | None = None
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
            self._build_selected_backend,
            on_connected=self._worker_signals.connected.emit,
            on_disconnected=self._worker_signals.disconnected.emit,
            on_failed=self._worker_signals.failed.emit,
            connection_initializer=self._initialize_session,
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
            "Simulation Mode ready. No hardware I/O is performed."
            if enabled
            else "REAL HARDWARE mode selected. Enter an explicit COM port, then press Connect."
        )
        self._publish(replace(
            self._snapshot,
            state=ConnectionState.DISCONNECTED,
            simulation_mode=enabled,
            instrument_id=None,
            initial_readbacks=(),
            message=message,
        ))

    @Slot(str)
    def set_port_name(self, port_name: str) -> None:
        if self._shutting_down or self._snapshot.state not in {
            ConnectionState.DISCONNECTED,
            ConnectionState.ERROR,
        }:
            return
        normalized = port_name.strip().upper()
        valid = bool(re.fullmatch(r"COM[1-9]\d*", normalized, re.IGNORECASE))
        if self._snapshot.simulation_mode:
            message = "Simulation Mode ready. No hardware I/O is performed."
        elif valid:
            message = (
                f"REAL HARDWARE mode armed for {normalized}. The port remains unopened until Connect."
            )
        else:
            message = "Enter a COM port such as COM7. No port has been opened or enumerated."
        self._publish(replace(
            self._snapshot,
            state=ConnectionState.DISCONNECTED,
            port_name=normalized,
            instrument_id=None,
            initial_readbacks=(),
            message=message,
        ))

    @Slot()
    def request_connect(self) -> None:
        if self._shutting_down or self._snapshot.state not in {
            ConnectionState.DISCONNECTED,
            ConnectionState.ERROR,
        }:
            return
        if not self._snapshot.simulation_mode and not self._snapshot.real_port_valid:
            self._reject("Enter an explicit COM port such as COM7 before connecting.")
            return
        target = "simulator" if self._snapshot.simulation_mode else self._snapshot.port_name
        self._failure_during_disconnect = None
        self._publish(replace(
            self._snapshot,
            state=ConnectionState.CONNECTING,
            instrument_id=None,
            initial_readbacks=(),
            message=f"Connecting to {target}; identifying and reading both channels...",
        ))
        if not self._worker.request_connect():
            self._handle_failed("connect", "The connection worker is not accepting requests.")

    @Slot()
    def request_disconnect(self) -> None:
        if self._shutting_down or self._snapshot.state is not ConnectionState.CONNECTED:
            return
        self._publish(replace(
            self._snapshot,
            state=ConnectionState.DISCONNECTING,
            initial_readbacks=(),
            message="Closing the instrument session without changing either output...",
        ))
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
        if self._shutting_down or self._snapshot.state is not ConnectionState.CONNECTED:
            self._reject("Connect and initialize the selected instrument before submitting a command.")
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
        return self._worker.cancel_pending(cancellation_key)

    def shutdown(self, timeout_s: float = 2.0) -> bool:
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

    def _build_selected_backend(self) -> PowerSupplyInterface:
        if self._snapshot.simulation_mode:
            return self._simulation_backend_factory()
        return self._real_backend_factory(self._snapshot.port_name)

    def _initialize_session(
        self, backend: PowerSupplyInterface, identification: str
    ) -> _ConnectionReady:
        if not self._snapshot.simulation_mode:
            parts = [part.strip() for part in identification.split(",")]
            if len(parts) != 4 or parts[1].upper() != "QPX600DP":
                raise InstrumentProtocolError("The selected port is not a QPX600DP.")
        return _ConnectionReady(
            identification=identification,
            readbacks=(read_channel(backend, 1), read_channel(backend, 2)),
        )

    @Slot(object)
    def _handle_connected(self, result: object) -> None:
        if self._shutting_down:
            return
        if not isinstance(result, _ConnectionReady):
            self._handle_failed("connect", "Connection initialization returned an invalid result.")
            return
        target = "Simulator" if self._snapshot.simulation_mode else self._snapshot.port_name
        self._session_id += 1
        self._publish(replace(
            self._snapshot,
            state=ConnectionState.CONNECTED,
            instrument_id=result.identification,
            initial_readbacks=result.readbacks,
            message=f"{target} identified and both channels read successfully.",
            session_id=self._session_id,
        ))

    @Slot()
    def _handle_disconnected(self) -> None:
        if self._shutting_down:
            return
        failure = self._failure_during_disconnect
        self._failure_during_disconnect = None
        self._publish(replace(
            self._snapshot,
            state=ConnectionState.ERROR if failure else ConnectionState.DISCONNECTED,
            instrument_id=None,
            initial_readbacks=(),
            message=failure or "Session closed. Output state is unknown and was not changed.",
        ))
        if failure:
            self.error_reported.emit(failure)

    @Slot(str, str)
    def _handle_failed(self, operation: str, message: str) -> None:
        if self._shutting_down:
            return
        self._logger.error("Connection operation failed: operation=%s; message=%s", operation, message)
        self._publish(replace(
            self._snapshot,
            state=ConnectionState.ERROR,
            instrument_id=None,
            initial_readbacks=(),
            message=message,
        ))
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
        if not self._snapshot.simulation_mode and self._snapshot.state is ConnectionState.CONNECTED:
            self._failure_during_disconnect = (
                f"Real-hardware command failed ({name}): {error}. The session was closed; "
                "reconnect manually after checking the instrument."
            )
            self._publish(replace(
                self._snapshot,
                state=ConnectionState.DISCONNECTING,
                initial_readbacks=(),
                message="Real-hardware command failed; closing the shared session...",
            ))
            if not self._worker.request_disconnect():
                self._handle_failed("disconnect", self._failure_during_disconnect)

    def _reject(self, message: str) -> None:
        self._logger.warning("Connection request rejected: %s", message)
        self.error_reported.emit(message)

    def _publish(self, snapshot: ConnectionSnapshot) -> None:
        self._snapshot = snapshot
        self.snapshot_changed.emit(snapshot)
