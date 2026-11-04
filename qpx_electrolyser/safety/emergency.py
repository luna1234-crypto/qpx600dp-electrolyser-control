"""Unified, channel-independent Emergency All-Off orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Iterable, Mapping

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.channel_readback import read_channel
from qpx_electrolyser.instrument.connection_worker import CommandPriority
from qpx_electrolyser.safety.event_log import SafetyEventLog


@dataclass(frozen=True, slots=True)
class EmergencyChannelResult:
    channel: int
    output_off_write_sent: bool
    confirmed_off: bool
    message: str


@dataclass(frozen=True, slots=True)
class EmergencySnapshot:
    active: bool = False
    message: str = "Emergency All-Off ready"
    results: tuple[EmergencyChannelResult, ...] = ()
    warning: bool = False


class EmergencyAllOffController(QObject):
    """Coordinate existing protocol cleanup, then independently verify both outputs."""

    snapshot_changed = Signal(object)

    def __init__(
        self,
        connection: ConnectionController,
        event_log: SafetyEventLog,
        parent: QObject | None = None,
        *,
        monotonic=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        super().__init__(parent)
        self._connection = connection
        self._event_log = event_log
        self._monotonic = monotonic
        self._sleep = sleep
        self._protocols: Mapping[int, tuple[object, ...]] = {1: (), 2: ()}
        self._manuals: Mapping[int, object] = {}
        self._loggers: Mapping[int, object] = {}
        self._snapshot = EmergencySnapshot()
        self._request_id: int | None = None
        self._deadline = 0.0
        self._cause = ""
        connection.command_completed.connect(self._command_completed)
        connection.command_failed.connect(self._command_failed)
        connection.snapshot_changed.connect(self._connection_changed)

    @property
    def snapshot(self) -> EmergencySnapshot:
        return self._snapshot

    def register(
        self,
        protocols: Mapping[int, Iterable[object]],
        manuals: Mapping[int, object],
        loggers: Mapping[int, object] | None = None,
    ) -> None:
        self._protocols = {
            channel: tuple(protocols.get(channel, ())) for channel in (1, 2)
        }
        self._manuals = dict(manuals)
        self._loggers = dict(loggers or {})

    @Slot()
    def request(self, cause: str = "Operator pressed Emergency All-Off") -> bool:
        if self._snapshot.active:
            return False
        self._cause = cause
        self._publish(EmergencySnapshot(True, "Stopping both channels and cancelling pending work..."))
        for manual in self._manuals.values():
            cancel = getattr(manual, "cancel", None)
            if callable(cancel):
                cancel()
        # Standalone measurement polls are read-only, but an emergency action
        # must clear pending queue work so the safety-priority transition is
        # not delayed.  Closing the logger is deterministic and cannot mutate
        # either instrument channel.
        for logger in self._loggers.values():
            stop = getattr(logger, "stop", None)
            if callable(stop):
                stop("Logging stopped by Emergency All-Off")
        for controllers in self._protocols.values():
            for controller in controllers:
                snapshot = getattr(controller, "snapshot", None)
                if snapshot is not None and getattr(snapshot, "owns_channel", False):
                    controller.stop("Emergency All-Off requested")
        self._deadline = self._monotonic() + 5.0
        QTimer.singleShot(0, self._await_protocol_cleanup)
        return True

    def _await_protocol_cleanup(self) -> None:
        if not self._snapshot.active:
            return
        active = any(
            getattr(getattr(controller, "snapshot", None), "owns_channel", False)
            for controllers in self._protocols.values()
            for controller in controllers
        )
        if active and self._monotonic() < self._deadline:
            QTimer.singleShot(25, self._await_protocol_cleanup)
            return
        if active:
            for controllers in self._protocols.values():
                for controller in controllers:
                    snapshot = getattr(controller, "snapshot", None)
                    if snapshot is not None and getattr(snapshot, "owns_channel", False):
                        controller.force_shutdown(
                            "Emergency cleanup timed out; physical OFF is unconfirmed."
                        )
        self._submit_authoritative_verification()

    def _submit_authoritative_verification(self) -> None:
        if self._connection.snapshot.state is not ConnectionState.CONNECTED:
            self._finish_unconfirmed("No active session; use the physical front panel immediately.")
            return
        real_hardware = not self._connection.snapshot.simulation_mode

        def operation(supply: PowerSupplyInterface) -> tuple[EmergencyChannelResult, ...]:
            results: list[EmergencyChannelResult] = []
            for channel in (1, 2):
                write_sent = False
                try:
                    if supply.read_output_state(channel):
                        supply.output_off(channel)
                        write_sent = True
                        if real_hardware:
                            supply.confirm_operation_complete()
                            self._sleep(1.0)
                            first = supply.read_output_state(channel)
                            self._sleep(0.1)
                            second = supply.read_output_state(channel)
                            if first or second:
                                raise RuntimeError("Output OFF did not remain stable.")
                    readback = read_channel(supply, channel)
                    if readback.output_enabled:
                        raise RuntimeError("Authoritative read-back still reports Output ON.")
                    results.append(EmergencyChannelResult(
                        channel, write_sent, True, "Output OFF authoritatively confirmed"
                    ))
                except Exception as error:
                    results.append(EmergencyChannelResult(
                        channel,
                        write_sent,
                        False,
                        str(error) or type(error).__name__,
                    ))
            return tuple(results)

        ticket = self._connection.submit_command(
            "emergency-all-off-verify",
            operation,
            priority=CommandPriority.SAFETY,
            cancellation_key=("emergency-all-off", id(self)),
            diagnostic_reason=self._cause,
        )
        if ticket is None:
            self._finish_unconfirmed("The shared queue rejected Emergency All-Off verification.")
            return
        self._request_id = ticket.request_id
        self._publish(EmergencySnapshot(True, "Authoritatively confirming CH1 and CH2 OFF..."))

    @Slot(int, str, object)
    def _command_completed(self, request_id: int, _name: str, result: object) -> None:
        if request_id != self._request_id:
            return
        self._request_id = None
        if not isinstance(result, tuple) or not all(
            isinstance(item, EmergencyChannelResult) for item in result
        ):
            self._finish_unconfirmed("Emergency verification returned malformed results.")
            return
        results = result
        for item in results:
            self._event_log.append(
                channel=item.channel,
                cause=self._cause,
                requested_action="Emergency Output Off and authoritative confirmation",
                confirmed_off=item.confirmed_off,
            )
        warning = not all(item.confirmed_off for item in results)
        details = "; ".join(
            f"CH{item.channel}: {'OFF confirmed' if item.confirmed_off else 'OFF UNCONFIRMED'}"
            for item in results
        )
        self._publish(EmergencySnapshot(False, details, results, warning))
        if warning and self._connection.snapshot.state is ConnectionState.CONNECTED:
            self._connection.request_disconnect()

    @Slot(int, str, object)
    def _command_failed(self, request_id: int, _name: str, error: object) -> None:
        if request_id == self._request_id:
            self._request_id = None
            self._finish_unconfirmed(f"Emergency verification failed: {error}")

    @Slot(object)
    def _connection_changed(self, snapshot: object) -> None:
        if (
            self._snapshot.active
            and self._request_id is not None
            and getattr(snapshot, "state", None) is not ConnectionState.CONNECTED
        ):
            self._request_id = None
            self._finish_unconfirmed("Connection was lost before both outputs were confirmed OFF.")

    def _finish_unconfirmed(self, message: str) -> None:
        results = tuple(
            EmergencyChannelResult(channel, False, False, message) for channel in (1, 2)
        )
        for item in results:
            self._event_log.append(
                channel=item.channel,
                cause=self._cause or "Emergency cleanup failure",
                requested_action="Emergency Output Off and authoritative confirmation",
                confirmed_off=False,
            )
        self._publish(EmergencySnapshot(False, message, results, True))
        if self._connection.snapshot.state is ConnectionState.CONNECTED:
            self._connection.request_disconnect()

    def _publish(self, snapshot: EmergencySnapshot) -> None:
        self._snapshot = snapshot
        self.snapshot_changed.emit(snapshot)
