# QPX600DP simulation backend

`SimulatedPowerSupply` is a deterministic, synchronous, two-channel backend for
development without hardware. It does not open ports or emit QPX ASCII commands.
Milestone 4 uses only its connect, identify and disconnect lifecycle through the
single backend-owner thread.

The simulator identifies as:

```text
SIMULATOR,QPX600DP,SIMULATED,<application version>
```

This is intentionally a simulation identity, not a byte-for-byte emulation of
physical `*IDN?` output.

## Example

```python
from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.instrument.simulator_config import SimulatorConfig

supply: PowerSupplyInterface = SimulatedPowerSupply(
    SimulatorConfig(communication_delay_s=0.01, random_seed=123)
)
supply.connect()
try:
    supply.set_voltage(1, 3.0)
    supply.set_current(1, 10.0)
    supply.output_on(1)
    print(supply.read_voltage(1), supply.read_current(1))
finally:
    supply.output_off(1)
    supply.disconnect()
```

Calls are synchronous and intentionally may delay. GUI code must route them
through the backend-owner thread rather than call them from a Qt callback.

## Hardware capabilities

`QPX600DP_CAPABILITIES` records the Issue 14 hardware facts:

- two channels;
- selectable 60 V / 1 mV and 80 V / 2 mV voltage ranges;
- current setting range 0.01-50 A in 10 mA steps;
- current readback resolution 10 mA;
- up to 600 W per output, subject to the PowerFlex envelope;
- standard USB virtual COM, RS232, LAN and analogue interfaces, with optional
  GPIB.

The high-range simulated voltage readback uses 2 mV conservatively. Issue 14
printed p. 3 lists a generic 1 mV voltage-meter resolution, while printed p. 11
says both set and actual values are 2 mV in the 80 V range. This ambiguity is
recorded rather than hidden.

`SimulatorConfig.channel_voltage_ranges` selects `"60 V"` or `"80 V"` for each
simulated channel. Both default to `"60 V"` as a simulator configuration choice,
not as an experimental safety limit. There is no runtime range-changing API or
GUI control in this checkpoint.

## Conservative power envelope

Issue 14 printed p. 13 explicitly states two guaranteed points:

- 10 V / 50 A;
- 80 V / 7.5 A.

It also says lower output voltages are limited by the 50 A maximum. The manual
shows the region between the anchors as a graph but provides no complete equation.
The implementation therefore uses this deliberately restrictive piecewise model:

| Actual simulated voltage | Maximum simulated current |
| --- | ---: |
| 0-10 V inclusive | 50 A |
| Above 10 V through 80 V | 7.5 A |

The 600 W ceiling is also enforced. No line, curve fit or `600 / V` formula is
invented. Consequently, the simulator may reject points that a physical unit can
regulate between 10 V and 80 V. `UnsupportedOperatingPointError` reports that
limitation and the rejected change does not mutate channel state.

## User safety limits are separate

Hardware capability is not experiment safety policy. Optional
`UserSafetyLimits(max_voltage_v, max_current_a, max_power_w)` can impose lower
setpoint and operating-power limits. `SimulatorConfig.user_safety_limits` is
`None` by default: no future experiment may interpret that as permission to use
the hardware maxima. Persistent operator configuration and safe experiment
defaults have not been implemented.

When limits are provided:

- voltage and current setpoints above them are rejected before delay or mutation;
- actual simulated voltage/current/power is checked before output enable or a
  live setting change;
- the hardware envelope remains independently enforced.

## Load and CC/CV behaviour

Each channel has its own immutable `ElectrolyserModel`:

```text
V(I) = V0 + R*I + a*ln(1 + I / 1 A)
```

This is a toy monotonic load, not fitted scientific data. At output enable:

- at or below onset voltage, applied voltage is shown with zero current;
- when the configured current is reachable, the channel operates in simulated CC;
- otherwise a bounded 64-iteration inverse finds the CV crossover;
- the actual point must pass both the conservative hardware envelope and any
  explicit user safety limits.

Outputs start disabled with zero internal setpoints. The QPX programming API
rejects a requested current below 0.01 A; the internal zero is only the simulator's
unprogrammed initial state. Setting voltage/current never enables an output.

## Resolution, noise and lifecycle

Measurements are independently quantised per channel. Optional Gaussian noise
is generated from separate voltage/current random streams, so polling one channel
does not consume another channel's sequence. A seed provides reproducibility.

`disconnect()` closes the simulated session but preserves settings and output
latches, mirroring the safety rule that communications loss is not evidence of
OFF. All instrument operations except local capability inspection, connect,
disconnect and `is_connected` reject a disconnected session.

The backend owns no Qt objects or worker thread. Its state is constant-size and
does not retain sample history. Experiment logging, plotting, protocol scheduling,
physical transport and long-run stress acceptance remain out of scope.
