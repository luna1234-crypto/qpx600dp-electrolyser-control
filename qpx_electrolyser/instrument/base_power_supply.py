"""Synchronous backend contract owned by one communication-worker thread."""

from abc import ABC, abstractmethod

from qpx_electrolyser.instrument.capabilities import (
    InstrumentCapabilities,
    VoltageRangeCapability,
)


class PowerSupplyInterface(ABC):
    """No Qt, protocol, filesystem or transport details cross this interface.

    Calls may block for I/O: the shared communication worker owns the instance
    and serializes every call. Do not call backends from GUI handlers.
    A disconnected session rejects all instrument operations except lifecycle
    calls and local capability/connection inspection. Setters never enable output.
    """

    @property
    @abstractmethod
    def capabilities(self) -> InstrumentCapabilities:
        """Return static documented capabilities; no connection is needed."""

    @abstractmethod
    def connect(self) -> None:
        """Open the session; repeated calls must not reset an active session."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the link; this is NOT an output-off or shutdown operation."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return local session state, not evidence that outputs are safe."""

    @abstractmethod
    def identify(self) -> str:
        """Return backend identification; simulators must identify as simulated."""

    @abstractmethod
    def set_voltage(self, channel: int, voltage_v: float) -> None:
        """Set voltage / CC compliance without enabling the output."""

    @abstractmethod
    def set_current(self, channel: int, current_a: float) -> None:
        """Set current / CV current limit without enabling the output."""

    @abstractmethod
    def read_set_voltage(self, channel: int) -> float:
        """Return programmed voltage in V, distinct from measured voltage."""

    @abstractmethod
    def read_set_current(self, channel: int) -> float:
        """Return programmed current limit in A, distinct from measured current."""

    @abstractmethod
    def read_voltage_range(self, channel: int) -> VoltageRangeCapability:
        """Return the selected voltage range without changing it."""

    @abstractmethod
    def output_on(self, channel: int) -> None:
        """Enable only the selected output at its existing settings."""

    @abstractmethod
    def output_off(self, channel: int) -> None:
        """Disable only the selected output without changing its settings."""

    @abstractmethod
    def read_output_state(self, channel: int) -> bool:
        """Query output enable state; disconnected is unknown and must raise."""

    @abstractmethod
    def read_voltage(self, channel: int) -> float:
        """Measure output voltage in V."""

    @abstractmethod
    def read_current(self, channel: int) -> float:
        """Measure output current in A."""
