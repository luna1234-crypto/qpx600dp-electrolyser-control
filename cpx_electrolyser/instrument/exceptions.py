"""Backend errors that callers can handle without importing a transport or Qt."""


class InstrumentError(Exception):
    """Base class for an instrument operation that did not succeed."""


class InstrumentNotConnectedError(InstrumentError):
    """An operation needs an open instrument session."""


class InstrumentValueError(InstrumentError, ValueError):
    """A channel, configuration value or setpoint is invalid; nothing was applied."""


class UnsupportedOperatingPointError(InstrumentError):
    """The simulator cannot represent this operating point as regulated output."""
