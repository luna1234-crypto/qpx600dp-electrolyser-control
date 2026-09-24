"""Shared command-worker ordering, ownership, cancellation and shutdown tests."""

from collections.abc import Callable, Iterator
from concurrent.futures import CancelledError
from queue import Queue
from threading import Event, Lock, Thread, current_thread, enumerate as enumerate_threads
import time

import pytest

from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.connection_worker import (
    CommandPriority,
    CommandQueueFullError,
    InstrumentCommandWorker,
    InstrumentConnectionWorker,
    WorkerShutdownError,
)
from qpx_electrolyser.instrument.exceptions import InstrumentNotConnectedError
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.instrument.simulator_config import SimulatorConfig


class TrackingSimulator(SimulatedPowerSupply):
    def __init__(self, *, fail_identify: bool = False) -> None:
        super().__init__(SimulatorConfig(communication_delay_s=0))
        self.fail_identify = fail_identify
        self.disconnect_calls = 0
        self.operation_threads: list[str] = []

    def connect(self) -> None:
        self.operation_threads.append(current_thread().name)
        super().connect()

    def identify(self) -> str:
        self.operation_threads.append(current_thread().name)
        if self.fail_identify:
            raise RuntimeError("injected identification failure")
        return super().identify()

    def disconnect(self) -> None:
        self.operation_threads.append(current_thread().name)
        self.disconnect_calls += 1
        super().disconnect()


def test_failed_connection_is_cleaned_and_a_fresh_backend_can_retry() -> None:
    first = TrackingSimulator(fail_identify=True)
    second = TrackingSimulator()
    candidates: Iterator[PowerSupplyInterface] = iter((first, second))
    outcomes: Queue[tuple[str, str]] = Queue()
    worker = InstrumentConnectionWorker(
        lambda: next(candidates),
        on_connected=lambda identity: outcomes.put(("connected", identity)),
        on_disconnected=lambda: outcomes.put(("disconnected", "")),
        on_failed=lambda operation, message: outcomes.put((operation, message)),
    )
    try:
        assert worker.request_connect()
        operation, message = outcomes.get(timeout=2)
        assert operation == "connect"
        assert "cleaned up" in message
        assert first.disconnect_calls == 1
        assert not first.is_connected()

        assert worker.request_connect()
        operation, identity = outcomes.get(timeout=2)
        assert operation == "connected"
        assert identity.startswith("SIMULATOR,QPX600DP")
        assert second.is_connected()
        assert set(second.operation_threads) == {"InstrumentCommandWorker"}

        assert worker.request_disconnect()
        assert outcomes.get(timeout=2) == ("disconnected", "")
        assert second.disconnect_calls == 1
        assert not second.is_connected()
    finally:
        assert worker.shutdown(timeout_s=1.0)


def test_shutdown_wait_is_bounded_if_backend_temporarily_blocks() -> None:
    started = Event()
    release = Event()

    class BlockingSimulator(TrackingSimulator):
        def connect(self) -> None:
            started.set()
            release.wait(timeout=2)
            super().connect()

    worker = InstrumentConnectionWorker(
        BlockingSimulator,
        on_connected=lambda _identity: None,
        on_disconnected=lambda: None,
        on_failed=lambda _operation, _message: None,
    )
    assert worker.request_connect()
    assert started.wait(timeout=1)
    before = time.monotonic()
    assert not worker.shutdown(timeout_s=0.02)
    assert time.monotonic() - before < 0.25
    release.set()
    assert worker.shutdown(timeout_s=1.0)
    assert not worker.is_alive


def test_invalid_worker_configuration_and_shutdown_timeout_are_rejected() -> None:
    callbacks = {
        "on_connected": lambda _identity: None,
        "on_disconnected": lambda: None,
        "on_failed": lambda _operation, _message: None,
    }
    with pytest.raises(ValueError, match="queue_capacity"):
        InstrumentConnectionWorker(TrackingSimulator, queue_capacity=0, **callbacks)

    worker = InstrumentConnectionWorker(TrackingSimulator, **callbacks)
    try:
        with pytest.raises(ValueError, match="timeout_s"):
            worker.shutdown(timeout_s=-0.1)
    finally:
        assert worker.shutdown(timeout_s=1.0)


def connected_worker(
    backend: TrackingSimulator | None = None,
) -> tuple[InstrumentCommandWorker, TrackingSimulator, Queue[tuple[str, str]]]:
    instance = backend or TrackingSimulator()
    outcomes: Queue[tuple[str, str]] = Queue()
    worker = InstrumentCommandWorker(
        lambda: instance,
        on_connected=lambda identity: outcomes.put(("connected", identity)),
        on_disconnected=lambda: outcomes.put(("disconnected", "")),
        on_failed=lambda operation, message: outcomes.put((operation, message)),
    )
    assert worker.request_connect()
    operation, identity = outcomes.get(timeout=2)
    assert operation == "connected"
    assert identity.startswith("SIMULATOR,QPX600DP")
    return worker, instance, outcomes


def test_equal_priority_commands_execute_fifo_on_the_single_owner() -> None:
    worker, _backend, _outcomes = connected_worker()
    order: list[int] = []
    try:
        tickets = [
            worker.submit(
                f"fifo-{index}",
                lambda _supply, value=index: order.append(value) or value,
            )
            for index in range(8)
        ]
        assert [ticket.result(timeout=2) for ticket in tickets] == list(range(8))
        assert order == list(range(8))
    finally:
        assert worker.shutdown(timeout_s=1.0)


def test_priority_reorders_waiting_commands_and_preserves_fifo_per_level() -> None:
    worker, _backend, _outcomes = connected_worker()
    started = Event()
    release = Event()
    order: list[str] = []

    def block(_supply: PowerSupplyInterface) -> str:
        order.append("block")
        started.set()
        assert release.wait(timeout=2)
        return "block"

    try:
        blocker = worker.submit("block", block)
        assert started.wait(timeout=1)
        tickets = [
            worker.submit("background-1", lambda _s: order.append("background-1"),
                          priority=CommandPriority.BACKGROUND),
            worker.submit("normal-1", lambda _s: order.append("normal-1")),
            worker.submit("safety-1", lambda _s: order.append("safety-1"),
                          priority=CommandPriority.SAFETY),
            worker.submit("safety-2", lambda _s: order.append("safety-2"),
                          priority=CommandPriority.SAFETY),
            worker.submit("normal-2", lambda _s: order.append("normal-2")),
            worker.submit("background-2", lambda _s: order.append("background-2"),
                          priority=CommandPriority.BACKGROUND),
        ]
        release.set()
        assert blocker.result(timeout=2) == "block"
        for ticket in tickets:
            ticket.result(timeout=2)
        assert order == [
            "block", "safety-1", "safety-2", "normal-1", "normal-2",
            "background-1", "background-2",
        ]
    finally:
        release.set()
        assert worker.shutdown(timeout_s=1.0)


def test_concurrent_channel_submissions_stay_atomic_and_keep_channel_order() -> None:
    worker, backend, _outcomes = connected_worker()
    guard = Lock()
    tickets_guard = Lock()
    active = 0
    maximum_active = 0
    execution: list[tuple[str, int]] = []
    tickets: list[object] = []
    owner_threads: set[str] = set()

    def operation(channel: str, index: int) -> Callable[[PowerSupplyInterface], tuple[str, int]]:
        def run(supply: PowerSupplyInterface) -> tuple[str, int]:
            nonlocal active, maximum_active
            assert supply is backend
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            owner_threads.add(current_thread().name)
            time.sleep(0.002)
            execution.append((channel, index))
            with guard:
                active -= 1
            return channel, index
        return run

    def submit_channel(channel: str) -> None:
        local = [
            worker.submit(
                f"{channel}-{index}", operation(channel, index), cancellation_key=channel
            )
            for index in range(10)
        ]
        with tickets_guard:
            tickets.extend(local)

    submitters = [Thread(target=submit_channel, args=(channel,)) for channel in ("ch1", "ch2")]
    try:
        for thread in submitters:
            thread.start()
        for thread in submitters:
            thread.join(timeout=2)
            assert not thread.is_alive()
        for ticket in tickets:
            ticket.result(timeout=2)  # type: ignore[union-attr]
        assert maximum_active == 1
        assert owner_threads == {"InstrumentCommandWorker"}
        for channel in ("ch1", "ch2"):
            assert [index for item, index in execution if item == channel] == list(range(10))
    finally:
        assert worker.shutdown(timeout_s=1.0)


def test_command_exception_isolated_and_reported_without_stopping_worker() -> None:
    worker, _backend, _outcomes = connected_worker()
    errors: Queue[tuple[int, str, BaseException]] = Queue()
    try:
        failed = worker.submit(
            "fails",
            lambda _supply: (_ for _ in ()).throw(ValueError("injected command failure")),
            on_error=lambda request_id, name, error: errors.put((request_id, name, error)),
        )
        healthy = worker.submit("healthy", lambda supply: supply.identify())
        with pytest.raises(ValueError, match="injected"):
            failed.result(timeout=2)
        request_id, name, error = errors.get(timeout=2)
        assert (request_id, name) == (failed.request_id, "fails")
        assert isinstance(error, ValueError)
        assert healthy.result(timeout=2).startswith("SIMULATOR,QPX600DP")
        assert worker.is_alive
    finally:
        assert worker.shutdown(timeout_s=1.0)


def test_targeted_cancellation_skips_queued_channel_commands() -> None:
    worker, _backend, _outcomes = connected_worker()
    started = Event()
    release = Event()
    executed: list[str] = []
    try:
        blocker = worker.submit(
            "block",
            lambda _s: started.set() or release.wait(timeout=2),
        )
        assert started.wait(timeout=1)
        ch1 = [
            worker.submit(f"ch1-{index}", lambda _s, i=index: executed.append(f"ch1-{i}"),
                          cancellation_key="ch1")
            for index in range(2)
        ]
        ch2 = worker.submit("ch2", lambda _s: executed.append("ch2"), cancellation_key="ch2")
        assert worker.cancel_pending("ch1") == 2
        release.set()
        blocker.result(timeout=2)
        for ticket in ch1:
            with pytest.raises(CancelledError):
                ticket.result(timeout=2)
        ch2.result(timeout=2)
        assert executed == ["ch2"]
    finally:
        release.set()
        assert worker.shutdown(timeout_s=1.0)


def test_disconnect_cancels_waiting_work_and_releases_backend_on_owner_thread() -> None:
    worker, backend, outcomes = connected_worker()
    started = Event()
    release = Event()
    try:
        blocker = worker.submit(
            "in-flight",
            lambda _s: started.set() or release.wait(timeout=2),
        )
        assert started.wait(timeout=1)
        pending = worker.submit("pending", lambda _s: pytest.fail("must be cancelled"))
        assert worker.request_disconnect()
        release.set()
        blocker.result(timeout=2)
        with pytest.raises(CancelledError):
            pending.result(timeout=2)
        assert outcomes.get(timeout=2) == ("disconnected", "")
        assert not backend.is_connected()
        assert backend.operation_threads[-1] == "InstrumentCommandWorker"
        disconnected = worker.submit("after-disconnect", lambda supply: supply.identify())
        with pytest.raises(InstrumentNotConnectedError):
            disconnected.result(timeout=2)
    finally:
        release.set()
        assert worker.shutdown(timeout_s=1.0)


def test_duplicate_connect_does_not_create_a_duplicate_backend() -> None:
    backend = TrackingSimulator()
    factory_calls = 0
    outcomes: Queue[tuple[str, str]] = Queue()

    def factory() -> PowerSupplyInterface:
        nonlocal factory_calls
        factory_calls += 1
        return backend

    worker = InstrumentCommandWorker(
        factory,
        on_connected=lambda identity: outcomes.put(("connected", identity)),
        on_failed=lambda operation, message: outcomes.put((operation, message)),
    )
    try:
        assert worker.request_connect()
        assert worker.request_connect()
        assert outcomes.get(timeout=2)[0] == "connected"
        operation, message = outcomes.get(timeout=2)
        assert operation == "connect"
        assert "already active" in message
        assert factory_calls == 1
    finally:
        assert worker.shutdown(timeout_s=1.0)


def test_clean_shutdown_rejects_new_work_and_leaves_no_worker_thread() -> None:
    worker, backend, _outcomes = connected_worker()
    assert worker.shutdown(timeout_s=1.0)
    assert not worker.is_alive
    assert not backend.is_connected()
    assert not worker.request_connect()
    assert not worker.request_disconnect()
    with pytest.raises(WorkerShutdownError):
        worker.submit("late", lambda supply: supply.identify())
    assert worker.shutdown(timeout_s=0)
    assert not any(
        thread.name == "InstrumentCommandWorker" and thread.is_alive()
        for thread in enumerate_threads()
    )


def test_regular_queue_capacity_is_bounded_without_blocking_disconnect() -> None:
    backend = TrackingSimulator()
    outcomes: Queue[tuple[str, str]] = Queue()
    worker = InstrumentCommandWorker(
        lambda: backend,
        queue_capacity=1,
        on_connected=lambda identity: outcomes.put(("connected", identity)),
        on_disconnected=lambda: outcomes.put(("disconnected", "")),
    )
    started = Event()
    release = Event()
    try:
        assert worker.request_connect()
        assert outcomes.get(timeout=2)[0] == "connected"
        running = worker.submit(
            "running", lambda _s: started.set() or release.wait(timeout=2)
        )
        assert started.wait(timeout=1)
        queued = worker.submit("queued", lambda _s: None)
        with pytest.raises(CommandQueueFullError):
            worker.submit("overflow", lambda _s: None)
        assert worker.request_disconnect()
        release.set()
        running.result(timeout=2)
        with pytest.raises(CancelledError):
            queued.result(timeout=2)
        assert outcomes.get(timeout=2) == ("disconnected", "")
    finally:
        release.set()
        assert worker.shutdown(timeout_s=1.0)
