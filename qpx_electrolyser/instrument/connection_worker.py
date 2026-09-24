"""Single-owner lifecycle worker for the milestone 4 simulated connection.

This deliberately small FIFO worker is not the milestone 6 command worker.  It
establishes the ownership boundary that milestone 6 will extend: the backend is
created, connected, queried and disconnected on one dedicated thread, and is
never exposed to the GUI thread.
"""

from collections.abc import Callable
from enum import Enum, auto
import logging
from queue import Full, Queue
from threading import Event, Thread

from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface


class _LifecycleCommand(Enum):
    CONNECT = auto()
    DISCONNECT = auto()
    WAKE_FOR_SHUTDOWN = auto()


class InstrumentConnectionWorker:
    """Own one backend on one daemon thread and serialize lifecycle requests.

    The bounded join used by :meth:`shutdown` lets application teardown finish
    even if a future backend blocks unexpectedly.  Normal simulated operations
    finish well inside that bound.  A timed-out daemon thread is reported as an
    unresolved cleanup fault rather than being terminated unsafely.
    """

    def __init__(
        self,
        backend_factory: Callable[[], PowerSupplyInterface],
        *,
        on_connected: Callable[[str], None],
        on_disconnected: Callable[[], None],
        on_failed: Callable[[str, str], None],
        queue_capacity: int = 4,
    ) -> None:
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        self._backend_factory = backend_factory
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._on_failed = on_failed
        self._commands: Queue[_LifecycleCommand] = Queue(maxsize=queue_capacity)
        self._stop_requested = Event()
        self._stopped = Event()
        self._backend: PowerSupplyInterface | None = None
        self._logger = logging.getLogger("qpx_electrolyser.instrument.connection_worker")
        self._thread = Thread(
            target=self._run,
            name="InstrumentConnectionWorker",
            daemon=True,
        )
        self._thread.start()

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def request_connect(self) -> bool:
        """Queue one connection attempt without waiting for instrument work."""
        return self._submit(_LifecycleCommand.CONNECT)

    def request_disconnect(self) -> bool:
        """Queue one disconnect attempt without waiting for instrument work."""
        return self._submit(_LifecycleCommand.DISCONNECT)

    def shutdown(self, timeout_s: float) -> bool:
        """Request cleanup and wait no longer than ``timeout_s`` for the owner."""
        if timeout_s < 0:
            raise ValueError("timeout_s cannot be negative")
        self._stop_requested.set()
        try:
            self._commands.put_nowait(_LifecycleCommand.WAKE_FOR_SHUTDOWN)
        except Full:
            # A queued lifecycle request will wake the worker. It observes the
            # stop flag before beginning any later request.
            pass
        self._thread.join(timeout_s)
        return self._stopped.is_set() and not self._thread.is_alive()

    def _submit(self, command: _LifecycleCommand) -> bool:
        if self._stop_requested.is_set():
            return False
        try:
            self._commands.put_nowait(command)
        except Full:
            return False
        return True

    def _run(self) -> None:
        try:
            while True:
                command = self._commands.get()
                if self._stop_requested.is_set():
                    break
                if command is _LifecycleCommand.CONNECT:
                    self._connect()
                elif command is _LifecycleCommand.DISCONNECT:
                    self._disconnect()
        except BaseException:
            self._logger.exception("Connection worker stopped after an unexpected error")
        finally:
            self._cleanup_backend("application shutdown")
            self._stopped.set()

    def _connect(self) -> None:
        if self._backend is not None:
            self._on_failed("connect", "A connection is already active.")
            return

        backend: PowerSupplyInterface | None = None
        try:
            backend = self._backend_factory()
            backend.connect()
            identification = backend.identify()
            if not identification.strip():
                raise RuntimeError("The instrument returned an empty identification string.")
        except Exception:
            self._logger.exception("Simulation connection or identification failed")
            self._cleanup_failed_candidate(backend)
            self._on_failed(
                "connect",
                "Could not connect to the simulator. The failed session was cleaned up; you may retry.",
            )
            return

        self._backend = backend
        self._logger.info("Simulation connection ready: %s", identification)
        self._on_connected(identification)

    def _disconnect(self) -> None:
        backend = self._backend
        # Remove the backend first so a failure cannot leave a stale session
        # presented as reusable. A later Connect always creates a fresh object.
        self._backend = None
        if backend is None:
            self._on_disconnected()
            return
        try:
            backend.disconnect()
        except Exception:
            self._logger.exception("Simulation disconnect failed; backend reference released")
            self._on_failed(
                "disconnect",
                "The simulator session did not close cleanly. It was discarded; you may reconnect.",
            )
            return
        self._logger.info("Simulation connection closed")
        self._on_disconnected()

    def _cleanup_failed_candidate(self, backend: PowerSupplyInterface | None) -> None:
        if backend is None:
            return
        try:
            backend.disconnect()
        except Exception:
            self._logger.exception("Failed connection candidate also failed during cleanup")

    def _cleanup_backend(self, reason: str) -> None:
        backend = self._backend
        self._backend = None
        if backend is None:
            return
        try:
            backend.disconnect()
        except Exception:
            self._logger.exception("Backend cleanup failed during %s", reason)
        else:
            self._logger.info("Backend cleaned up during %s", reason)
