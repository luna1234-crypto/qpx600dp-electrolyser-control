"""Qt-facing state controller for asynchronous instrument connection work."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import logging

from PySide6.QtCore import QObject, Qt, Signal, Slot

from cpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from cpx_electrolyser.instrument.connection_worker import InstrumentConnectionWorker
from cpx_electrolyser.instrument.simulator import SimulatedPowerSupply


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


class ConnectionController(QObject):
    """Validate GUI requests and bridge one lifecycle worker into Qt signals."""

    snapshot_changed = Signal(object)
    error_reported = Signal(str)

    def __init__(
        self,
        backend_factory: Callable[[], PowerSupplyInterface] = SimulatedPowerSupply,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._logger = logging.getLogger("cpx_electrolyser.app.connection_controller")
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
        self._worker = InstrumentConnectionWorker(
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
            else "Physical CPX400DP connections are not available in Milestone 4."
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
            self._reject("Enable Simulation Mode to connect in Milestone 4.")
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
                "Simulator connected. Outputs remain unavailable in this milestone.",
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

    def _reject(self, message: str) -> None:
        self._logger.warning("Connection request rejected: %s", message)
        self.error_reported.emit(message)

    def _publish(self, snapshot: ConnectionSnapshot) -> None:
        self._snapshot = snapshot
        self.snapshot_changed.emit(snapshot)
