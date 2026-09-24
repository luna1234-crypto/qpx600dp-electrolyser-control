"""Backend errors that callers can handle without importing a transport or Qt."""


class InstrumentError(Exception):
    """Base class for an instrument operation that did not succeed."""


class InstrumentNotConnectedError(InstrumentError):
    """An operation needs an open instrument session."""


class InstrumentConnectionError(InstrumentError):
    """A transport session could not be opened or closed cleanly."""


class InstrumentTransportError(InstrumentError):
    """A connected transport failed while transferring a command or response."""


class InstrumentTimeoutError(InstrumentTransportError, TimeoutError):
    """A complete instrument response did not arrive before the configured deadline."""


class InstrumentProtocolError(InstrumentError):
    """A framed response did not match the documented QPX600DP protocol."""


class InstrumentValueError(InstrumentError, ValueError):
    """A channel, configuration value or setpoint is invalid; nothing was applied."""


class UnsupportedOperatingPointError(InstrumentError):
    """The simulator cannot represent this operating point as regulated output."""
