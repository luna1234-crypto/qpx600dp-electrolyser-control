"""Single-owner asynchronous command worker for every instrument operation.

The worker owns the backend for its entire connected lifetime. Callers submit
small synchronous backend operations and receive futures; they never execute
instrument code on the GUI thread. A priority queue orders work while a
monotonic sequence number preserves FIFO order within each priority.
"""

from collections.abc import Callable, Hashable
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import IntEnum
import itertools
import logging
from queue import PriorityQueue
from threading import Event, Lock, Thread
from typing import Generic, TypeVar, cast

from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.exceptions import InstrumentNotConnectedError


ResultT = TypeVar("ResultT")
BackendOperation = Callable[[PowerSupplyInterface], ResultT]
ResultCallback = Callable[[int, str, object], None]
ErrorCallback = Callable[[int, str, BaseException], None]


class CommandPriority(IntEnum):
    """Lower numeric values execute first; FIFO is preserved within a level."""

    SAFETY = 0
    CONNECTION = 10
    NORMAL = 20
    BACKGROUND = 30


class CommandWorkerError(RuntimeError):
    """Base class for synchronous command-submission failures."""


class CommandQueueFullError(CommandWorkerError):
    """The bounded regular-command allowance is exhausted."""


class WorkerShutdownError(CommandWorkerError):
    """The worker has stopped accepting commands."""


@dataclass(frozen=True, slots=True)
class CommandTicket(Generic[ResultT]):
    """Read-only command identity plus a thread-safe completion future."""

    request_id: int
    name: str
    cancellation_key: Hashable | None
    _future: Future[ResultT] = field(repr=False)

    def cancel(self) -> bool:
        """Cancel only if execution has not started."""
        return self._future.cancel()

    def cancelled(self) -> bool:
        return self._future.cancelled()

    def done(self) -> bool:
        return self._future.done()

    def result(self, timeout: float | None = None) -> ResultT:
        return self._future.result(timeout)

    def exception(self, timeout: float | None = None) -> BaseException | None:
        return self._future.exception(timeout)


@dataclass(order=True, slots=True)
class _QueuedCommand:
    priority: int
    sequence: int
    request_id: int = field(compare=False)
    name: str = field(compare=False)
    execute: Callable[[], object] | None = field(compare=False)
    future: Future[object] = field(compare=False)
    on_result: ResultCallback | None = field(compare=False, default=None)
    on_error: ErrorCallback | None = field(compare=False, default=None)
    cancellation_key: Hashable | None = field(compare=False, default=None)
    regular: bool = field(compare=False, default=True)
    shutdown: bool = field(compare=False, default=False)


class InstrumentCommandWorker:
    """Own one backend and serialize lifecycle plus ordinary operations."""

    _SHUTDOWN_PRIORITY = -1

    def __init__(
        self,
        backend_factory: Callable[[], PowerSupplyInterface],
        *,
        on_connected: Callable[[str], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
        on_failed: Callable[[str, str], None] | None = None,
        queue_capacity: int = 64,
    ) -> None:
        if isinstance(queue_capacity, bool) or queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        if not callable(backend_factory):
            raise TypeError("backend_factory must be callable")
        self._backend_factory = backend_factory
        self._on_connected = on_connected or (lambda _identity: None)
        self._on_disconnected = on_disconnected or (lambda: None)
        self._on_failed = on_failed or (lambda _operation, _message: None)
        self._queue_capacity = queue_capacity
        # Lifecycle and shutdown commands are never blocked by regular-command
        # capacity, so disconnect and teardown always have a queue path.
        self._commands: PriorityQueue[_QueuedCommand] = PriorityQueue()
        self._lock = Lock()
        self._request_ids = itertools.count(1)
        self._sequences = itertools.count()
        self._requests: dict[int, _QueuedCommand] = {}
        self._queued_regular = 0
        self._accepting = True
        self._shutdown_enqueued = False
        self._stopped = Event()
        self._backend: PowerSupplyInterface | None = None
        self._logger = logging.getLogger("qpx_electrolyser.instrument.command_worker")
        self._thread = Thread(
            target=self._run,
            name="InstrumentCommandWorker",
            daemon=True,
        )
        self._thread.start()

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def thread_name(self) -> str:
        return self._thread.name

    def request_connect(self) -> bool:
        """Queue creation, connection and identification on the owner thread."""
        try:
            self._enqueue(
                "connect",
                self._connect,
                priority=CommandPriority.CONNECTION,
                regular=False,
                on_result=lambda _request_id, _name, result: self._safe_callback(
                    self._on_connected, cast(str, result)
                ),
                on_error=lambda _request_id, _name, error: self._safe_callback(
                    self._on_failed, "connect", str(error)
                ),
            )
        except WorkerShutdownError:
            return False
        return True

    def request_disconnect(self) -> bool:
        """Cancel queued ordinary work, then disconnect after any in-flight call."""
        self.cancel_pending()
        try:
            self._enqueue(
                "disconnect",
                self._disconnect,
                priority=CommandPriority.CONNECTION,
                regular=False,
                on_result=lambda _request_id, _name, _result: self._safe_callback(
                    self._on_disconnected
                ),
                on_error=lambda _request_id, _name, error: self._safe_callback(
                    self._on_failed, "disconnect", str(error)
                ),
            )
        except WorkerShutdownError:
            return False
        return True

    def submit(
        self,
        name: str,
        operation: BackendOperation[ResultT],
        *,
        priority: CommandPriority = CommandPriority.NORMAL,
        cancellation_key: Hashable | None = None,
        on_result: ResultCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> CommandTicket[ResultT]:
        """Queue one atomic backend call without waiting for it to finish."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a nonempty string")
        if not callable(operation):
            raise TypeError("operation must be callable")
        if not isinstance(priority, CommandPriority):
            raise TypeError("priority must be a CommandPriority")

        def execute() -> ResultT:
            backend = self._backend
            if backend is None or not backend.is_connected():
                raise InstrumentNotConnectedError(
                    "Connect the shared instrument worker before submitting operations."
                )
            return operation(backend)

        item = self._enqueue(
            name.strip(),
            execute,
            priority=priority,
            regular=True,
            cancellation_key=cancellation_key,
            on_result=on_result,
            on_error=on_error,
        )
        return CommandTicket(
            request_id=item.request_id,
            name=item.name,
            cancellation_key=item.cancellation_key,
            _future=cast(Future[ResultT], item.future),
        )

    def cancel(self, request_id: int) -> bool:
        """Cancel a known queued request; an in-flight operation stays atomic."""
        with self._lock:
            item = self._requests.get(request_id)
            return False if item is None else item.future.cancel()

    def cancel_pending(self, cancellation_key: Hashable | None = None) -> int:
        """Cancel queued ordinary commands, optionally for one logical owner."""
        cancelled = 0
        with self._lock:
            for item in tuple(self._requests.values()):
                if not item.regular:
                    continue
                if cancellation_key is not None and item.cancellation_key != cancellation_key:
                    continue
                if item.future.cancel():
                    cancelled += 1
        return cancelled

    def shutdown(self, timeout_s: float) -> bool:
        """Stop acceptance, cancel queued work and bound the owner-thread join."""
        if timeout_s < 0:
            raise ValueError("timeout_s cannot be negative")
        with self._lock:
            self._accepting = False
            for item in tuple(self._requests.values()):
                item.future.cancel()
            if not self._shutdown_enqueued:
                self._shutdown_enqueued = True
                self._commands.put_nowait(self._shutdown_item())
        self._thread.join(timeout_s)
        return self._stopped.is_set() and not self._thread.is_alive()

    def _enqueue(
        self,
        name: str,
        execute: Callable[[], object],
        *,
        priority: CommandPriority,
        regular: bool,
        cancellation_key: Hashable | None = None,
        on_result: ResultCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> _QueuedCommand:
        with self._lock:
            if not self._accepting:
                raise WorkerShutdownError("The instrument command worker is shutting down.")
            if regular and self._queued_regular >= self._queue_capacity:
                raise CommandQueueFullError("The instrument command queue is full.")
            request_id = next(self._request_ids)
            item = _QueuedCommand(
                priority=int(priority),
                sequence=next(self._sequences),
                request_id=request_id,
                name=name,
                execute=execute,
                future=Future(),
                on_result=on_result,
                on_error=on_error,
                cancellation_key=cancellation_key,
                regular=regular,
            )
            self._requests[request_id] = item
            if regular:
                self._queued_regular += 1
            self._commands.put_nowait(item)
            return item

    def _shutdown_item(self) -> _QueuedCommand:
        return _QueuedCommand(
            priority=self._SHUTDOWN_PRIORITY,
            sequence=next(self._sequences),
            request_id=0,
            name="shutdown",
            execute=None,
            future=Future(),
            regular=False,
            shutdown=True,
        )

    def _run(self) -> None:
        try:
            while True:
                item = self._commands.get()
                if item.shutdown:
                    break
                if item.regular:
                    with self._lock:
                        self._queued_regular -= 1
                if not item.future.set_running_or_notify_cancel():
                    self._forget(item.request_id)
                    continue
                try:
                    if item.execute is None:
                        raise RuntimeError("Queued command has no executable operation.")
                    result = item.execute()
                except BaseException as error:
                    item.future.set_exception(error)
                    if item.on_error is not None:
                        self._safe_callback(item.on_error, item.request_id, item.name, error)
                else:
                    item.future.set_result(result)
                    if item.on_result is not None:
                        self._safe_callback(item.on_result, item.request_id, item.name, result)
                finally:
                    self._forget(item.request_id)
        except BaseException:
            self._logger.exception("Instrument command worker stopped unexpectedly")
        finally:
            self._cleanup_backend("application shutdown")
            with self._lock:
                for item in tuple(self._requests.values()):
                    item.future.cancel()
                self._requests.clear()
                self._queued_regular = 0
            self._stopped.set()

    def _connect(self) -> str:
        if self._backend is not None:
            raise CommandWorkerError("A connection is already active.")
        candidate: PowerSupplyInterface | None = None
        try:
            candidate = self._backend_factory()
            candidate.connect()
            identification = candidate.identify()
            if not identification.strip():
                raise RuntimeError("The instrument returned an empty identification string.")
        except Exception as error:
            self._logger.exception("Simulation connection or identification failed")
            self._cleanup_candidate(candidate)
            raise CommandWorkerError(
                "Could not connect to the simulator. The failed session was cleaned up; "
                "you may retry."
            ) from error
        self._backend = candidate
        self._logger.info("Simulation connection ready: %s", identification)
        return identification

    def _disconnect(self) -> None:
        backend = self._backend
        self._backend = None
        if backend is None:
            return
        try:
            backend.disconnect()
        except Exception as error:
            self._logger.exception("Simulation disconnect failed; backend reference released")
            raise CommandWorkerError(
                "The simulator session did not close cleanly. It was discarded; you may reconnect."
            ) from error
        self._logger.info("Simulation connection closed")

    def _cleanup_candidate(self, backend: PowerSupplyInterface | None) -> None:
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

    def _forget(self, request_id: int) -> None:
        with self._lock:
            self._requests.pop(request_id, None)

    def _safe_callback(self, callback: Callable[..., object], *args: object) -> None:
        try:
            callback(*args)
        except Exception:
            self._logger.exception("Instrument command callback failed")


# Milestone 4 import compatibility. This is an alias, not a second worker.
InstrumentConnectionWorker = InstrumentCommandWorker
