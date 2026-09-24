"""Lifecycle worker ownership, failure cleanup and bounded shutdown tests."""

from collections.abc import Iterator
from queue import Queue
from threading import Event, current_thread
import time

import pytest

from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.connection_worker import InstrumentConnectionWorker
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
        assert set(second.operation_threads) == {"InstrumentConnectionWorker"}

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
