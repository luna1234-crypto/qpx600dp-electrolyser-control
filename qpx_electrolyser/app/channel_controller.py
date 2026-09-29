"""Per-channel manual controls routed through the one shared command queue."""

from dataclasses import dataclass, replace
import math

from PySide6.QtCore import QObject, Signal, Slot

from qpx_electrolyser.app.connection_controller import (
    ConnectionController,
    ConnectionSnapshot,
    ConnectionState,
)
from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.capabilities import QPX600DP_CAPABILITIES
from qpx_electrolyser.instrument.connection_worker import BackendOperation, CommandPriority
from qpx_electrolyser.instrument.exceptions import InstrumentValueError


@dataclass(frozen=True, slots=True)
class ChannelSnapshot:
    channel: int
    connected: bool = False
    pending: bool = False
    programmed_voltage_v: float | None = None
    current_limit_a: float | None = None
    measured_voltage_v: float | None = None
    measured_current_a: float | None = None
    voltage_range_name: str | None = None
    output_enabled: bool | None = None
    message: str = "Disconnected"
    error: bool = False
    faulted: bool = False


def _read_channel(supply: PowerSupplyInterface, channel: int) -> ChannelSnapshot:
    """Read one channel as a single queue operation, so values cannot interleave."""
    voltage_range = supply.read_voltage_range(channel)
    return ChannelSnapshot(
        channel=channel,
        connected=True,
        programmed_voltage_v=supply.read_set_voltage(channel),
        current_limit_a=supply.read_set_current(channel),
        measured_voltage_v=supply.read_voltage(channel),
        measured_current_a=supply.read_current(channel),
        voltage_range_name=voltage_range.name,
        output_enabled=supply.read_output_state(channel),
        message="Read-back complete",
    )


class ManualChannelController(QObject):
    """Validate GUI intent and serialize it through a shared connection controller."""

    snapshot_changed = Signal(object)

    def __init__(
        self,
        channel: int,
        connection_controller: ConnectionController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        QPX600DP_CAPABILITIES.validate_channel(channel)
        self._channel = channel
        self._connection = connection_controller
        self._cancellation_key = f"manual-channel-{channel}"
        self._pending_request_id: int | None = None
        self._snapshot = ChannelSnapshot(channel=channel)
        connection_controller.snapshot_changed.connect(self._connection_changed)
        connection_controller.command_completed.connect(self._command_completed)
        connection_controller.command_failed.connect(self._command_failed)

    @property
    def snapshot(self) -> ChannelSnapshot:
        return self._snapshot

    @Slot()
    def request_refresh(self) -> None:
        self._submit("refresh", lambda supply: _read_channel(supply, self._channel))

    @Slot(str)
    def request_set_voltage(self, text: str) -> None:
        voltage_range = self._snapshot.voltage_range_name
        if voltage_range is None:
            self._validation_failed("Refresh the channel before setting voltage.")
            return
        value = self._parse_number(text, "Voltage")
        if value is None:
            return
        try:
            voltage = QPX600DP_CAPABILITIES.validate_voltage(value, voltage_range)
        except InstrumentValueError as error:
            self._validation_failed(str(error))
            return

        def operation(supply: PowerSupplyInterface) -> ChannelSnapshot:
            supply.set_voltage(self._channel, voltage)
            return _read_channel(supply, self._channel)

        self._submit("set-voltage", operation)

    @Slot(str)
    def request_set_current(self, text: str) -> None:
        value = self._parse_number(text, "Current limit")
        if value is None:
            return
        try:
            current = QPX600DP_CAPABILITIES.validate_current(value)
        except InstrumentValueError as error:
            self._validation_failed(str(error))
            return

        def operation(supply: PowerSupplyInterface) -> ChannelSnapshot:
            supply.set_current(self._channel, current)
            return _read_channel(supply, self._channel)

        self._submit("set-current", operation)

    @Slot(bool)
    def request_output_on(self, confirmed: bool = False) -> None:
        if not confirmed:
            self._validation_failed("Output On was not confirmed; no command was submitted.")
            return

        def operation(supply: PowerSupplyInterface) -> ChannelSnapshot:
            supply.output_on(self._channel)
            return _read_channel(supply, self._channel)

        self._submit("output-on", operation)

    @Slot()
    def request_output_off(self) -> None:
        def operation(supply: PowerSupplyInterface) -> ChannelSnapshot:
            supply.output_off(self._channel)
            return _read_channel(supply, self._channel)

        self._submit("output-off", operation, priority=CommandPriority.SAFETY)

    def cancel(self) -> int:
        count = self._connection.cancel_commands(self._cancellation_key)
        if count and self._pending_request_id is not None:
            self._pending_request_id = None
            self._publish(replace(self._snapshot, pending=False, message="Command cancelled"))
        return count

    def _submit(
        self,
        action: str,
        operation: BackendOperation[object],
        *,
        priority: CommandPriority = CommandPriority.NORMAL,
    ) -> None:
        if self._pending_request_id is not None:
            return
        if self._connection.snapshot.state is not ConnectionState.CONNECTED:
            self._validation_failed("Connect the simulator before controlling a channel.")
            return
        ticket = self._connection.submit_command(
            f"ch{self._channel}-{action}",
            operation,
            priority=priority,
            cancellation_key=self._cancellation_key,
        )
        if ticket is None:
            return
        self._pending_request_id = ticket.request_id
        self._publish(replace(
            self._snapshot,
            connected=True,
            pending=True,
            message=f"{action.replace('-', ' ').capitalize()} pending...",
            error=False,
        ))

    @Slot(object)
    def _connection_changed(self, snapshot: ConnectionSnapshot) -> None:
        if snapshot.state is ConnectionState.CONNECTED:
            if not self._snapshot.connected and self._pending_request_id is None:
                self._publish(ChannelSnapshot(
                    channel=self._channel,
                    connected=True,
                    message="Initial read-back pending...",
                ))
                self.request_refresh()
            return
        self._connection.cancel_commands(self._cancellation_key)
        self._pending_request_id = None
        self._publish(ChannelSnapshot(channel=self._channel, message="Disconnected"))

    @Slot(int, str, object)
    def _command_completed(self, request_id: int, _name: str, result: object) -> None:
        if request_id != self._pending_request_id:
            return
        self._pending_request_id = None
        if (
            self._connection.snapshot.state is ConnectionState.CONNECTED
            and isinstance(result, ChannelSnapshot)
            and result.channel == self._channel
        ):
            self._publish(result)

    @Slot(int, str, object)
    def _command_failed(self, request_id: int, _name: str, error: BaseException) -> None:
        if request_id != self._pending_request_id:
            return
        self._pending_request_id = None
        if self._connection.snapshot.state is ConnectionState.CONNECTED:
            self._publish(replace(
                self._snapshot,
                pending=False,
                message=f"Command failed: {error}",
                error=True,
                faulted=True,
            ))

    def _parse_number(self, text: str, label: str) -> float | None:
        if not isinstance(text, str) or not text.strip():
            self._validation_failed(f"{label} is required.")
            return None
        try:
            value = float(text)
        except ValueError:
            self._validation_failed(f"{label} must be a number.")
            return None
        if not math.isfinite(value) or value < 0:
            self._validation_failed(f"{label} must be finite and nonnegative.")
            return None
        return value

    def _validation_failed(self, message: str) -> None:
        self._publish(replace(
            self._snapshot,
            pending=False,
            message=message,
            error=True,
            faulted=self._snapshot.faulted,
        ))

    def _publish(self, snapshot: ChannelSnapshot) -> None:
        self._snapshot = snapshot
        self.snapshot_changed.emit(snapshot)
