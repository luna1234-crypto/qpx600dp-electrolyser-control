"""Two independent simulated outputs behind the shared backend contract."""

from collections.abc import Callable
from dataclasses import dataclass, replace
import logging
import random
import time

from qpx_electrolyser import __version__
from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.capabilities import QPX600DP_CAPABILITIES, InstrumentCapabilities
from qpx_electrolyser.instrument.exceptions import (
    InstrumentNotConnectedError,
    UnsupportedOperatingPointError,
)
from qpx_electrolyser.instrument.simulator_config import ElectrolyserModel, SimulatorConfig


@dataclass(frozen=True, slots=True)
class _ChannelSettings:
    voltage_v: float = 0.0
    current_a: float = 0.0
    output_enabled: bool = False


class SimulatedPowerSupply(PowerSupplyInterface):
    """Synchronous backend with no ports, Qt objects, worker threads or sample history.

    Call from the one shared owner thread, as required for the physical backend.
    Latency is deliberate and must never be invoked from a GUI callback.
    Disconnect closes only the simulated link and preserves physical state.
    """

    def __init__(
        self,
        config: SimulatorConfig | None = None,
        *,
        delay: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config if config is not None else SimulatorConfig()
        self._delay = delay
        self._connected = False
        self._channels = [_ChannelSettings() for _ in range(self.capabilities.number_of_channels)]
        seed_source = random.Random(self._config.random_seed)
        # Each channel and quantity has its own RNG, so polling the other channel
        # (or polling current more often) cannot consume its voltage-noise sequence.
        self._voltage_rng = [random.Random(seed_source.getrandbits(128)) for _ in self._channels]
        self._current_rng = [random.Random(seed_source.getrandbits(128)) for _ in self._channels]
        self._logger = logging.getLogger("qpx_electrolyser.instrument.simulator")

    @property
    def capabilities(self) -> InstrumentCapabilities:
        return QPX600DP_CAPABILITIES

    @property
    def config(self) -> SimulatorConfig:
        return self._config

    def connect(self) -> None:
        if not self._connected:
            self._wait()
            self._connected = True
            self._logger.info("Simulator connected; existing output states preserved")

    def disconnect(self) -> None:
        if self._connected:
            self._wait()
            self._connected = False
            self._logger.info("Simulator disconnected; output states have not been changed")

    def is_connected(self) -> bool:
        return self._connected

    def identify(self) -> str:
        self._require_connection()
        self._wait()
        return f"SIMULATOR,QPX600DP,SIMULATED,{__version__}"

    def set_voltage(self, channel: int, voltage_v: float) -> None:
        index = self._channel_index(channel)
        value = self.capabilities.validate_voltage(
            voltage_v, self._config.channel_voltage_ranges[index]
        )
        self._apply(index, replace(self._channels[index], voltage_v=value))
        self._logger.debug("Simulator CH%d voltage set to %g V", channel, value)

    def set_current(self, channel: int, current_a: float) -> None:
        index = self._channel_index(channel)
        value = self.capabilities.validate_current(current_a)
        self._apply(index, replace(self._channels[index], current_a=value))
        self._logger.debug("Simulator CH%d current limit set to %g A", channel, value)

    def read_set_voltage(self, channel: int) -> float:
        index = self._channel_index(channel)
        self._wait()
        return self._channels[index].voltage_v

    def read_set_current(self, channel: int) -> float:
        index = self._channel_index(channel)
        self._wait()
        return self._channels[index].current_a

    def output_on(self, channel: int) -> None:
        index = self._channel_index(channel)
        self._apply(index, replace(self._channels[index], output_enabled=True))
        self._logger.info("Simulator CH%d output ON", channel)

    def output_off(self, channel: int) -> None:
        index = self._channel_index(channel)
        self._apply(index, replace(self._channels[index], output_enabled=False))
        self._logger.info("Simulator CH%d output OFF", channel)

    def read_output_state(self, channel: int) -> bool:
        index = self._channel_index(channel)
        self._wait()
        return self._channels[index].output_enabled

    def read_voltage(self, channel: int) -> float:
        index = self._channel_index(channel)
        voltage_v, _ = self._measurement(index)
        if not self._channels[index].output_enabled:
            return 0.0
        return self._readback(
            voltage_v, self._config.voltage_noise_sd_v,
            self.capabilities.voltage_range(
                self._config.channel_voltage_ranges[index]
            ).measurement_resolution_v,
            self._voltage_rng[index],
        )

    def read_current(self, channel: int) -> float:
        index = self._channel_index(channel)
        _, current_a = self._measurement(index)
        if not self._channels[index].output_enabled:
            return 0.0
        return self._readback(
            current_a, self._config.current_noise_sd_a,
            self.capabilities.measurement_current_resolution_a, self._current_rng[index],
        )

    def _apply(self, index: int, candidate: _ChannelSettings) -> None:
        # Validate before latency and mutation. Rejected settings/ON requests leave
        # both outputs unchanged; an OFF request is always model-compatible.
        self._validate_user_setpoints(candidate)
        self._operating_point(candidate, self._config.channel_models[index])
        self._wait()
        self._channels[index] = candidate

    def _measurement(self, index: int) -> tuple[float, float]:
        self._wait()
        return self._operating_point(self._channels[index], self._config.channel_models[index])

    def _operating_point(self, settings: _ChannelSettings, model: ElectrolyserModel) -> tuple[float, float]:
        if not settings.output_enabled or settings.voltage_v == 0 or settings.current_a == 0:
            return 0.0, 0.0
        if settings.voltage_v <= model.onset_voltage_v:
            return settings.voltage_v, 0.0

        required_voltage = model.voltage_at_current(settings.current_a)
        if required_voltage <= settings.voltage_v:
            voltage_v, current_a = required_voltage, settings.current_a  # CC
        else:
            # Monotonic inverse on a bounded interval; never iterate to convergence
            # without a bound. Error after 64 iterations is far below 10 mA readback.
            low, high = 0.0, settings.current_a
            for _ in range(64):
                middle = (low + high) / 2
                if model.voltage_at_current(middle) <= settings.voltage_v:
                    low = middle
                else:
                    high = middle
            voltage_v, current_a = settings.voltage_v, low  # CV

        if not self.capabilities.power_envelope.contains(voltage_v, current_a):
            raise UnsupportedOperatingPointError(
                "Simulated operating point is outside the conservative QPX600DP hardware "
                "power envelope. The undocumented part of the Issue 14 curve is not "
                "interpolated; the change was not applied."
            )
        limits = self._config.user_safety_limits
        if limits is not None and (
            voltage_v > limits.max_voltage_v
            or current_a > limits.max_current_a
            or voltage_v * current_a > limits.max_power_w
        ):
            raise UnsupportedOperatingPointError(
                "Simulated operating point exceeds the configured user safety limits; "
                "the change was not applied."
            )
        return voltage_v, current_a

    def _validate_user_setpoints(self, settings: _ChannelSettings) -> None:
        limits = self._config.user_safety_limits
        if limits is None:
            return
        if settings.voltage_v > limits.max_voltage_v:
            raise UnsupportedOperatingPointError(
                f"Voltage setpoint exceeds the configured {limits.max_voltage_v:g} V safety limit."
            )
        if settings.current_a > limits.max_current_a:
            raise UnsupportedOperatingPointError(
                f"Current setpoint exceeds the configured {limits.max_current_a:g} A safety limit."
            )

    @staticmethod
    def _readback(value: float, noise_sd: float, resolution: float, rng: random.Random) -> float:
        measured = value + (rng.gauss(0.0, noise_sd) if noise_sd else 0.0)
        # Quantisation and nonnegative noise floor apply to measurements only.
        return round(max(0.0, measured) / resolution) * resolution

    def _channel_index(self, channel: int) -> int:
        self.capabilities.validate_channel(channel)
        self._require_connection()
        return channel - 1

    def _require_connection(self) -> None:
        if not self._connected:
            raise InstrumentNotConnectedError("Connect the simulated power supply before this operation.")

    def _wait(self) -> None:
        if self._config.communication_delay_s:
            self._delay(self._config.communication_delay_s)
