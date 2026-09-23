# Simulator backend and milestone 4 GUI connection

The simulator is a Python backend for testing application behaviour without a
CPX400DP. Milestone 4 exposes its connection lifecycle in the GUI through one
dedicated owner thread; the simulator module itself still contains no Qt or
serial code. No protocol scheduler or experiment logger is implemented. The
console demo is run from the project root with:

```powershell
.\.venv\Scripts\python.exe -m examples.simulator_demo
```

## Public contract

`instrument/base_power_supply.py` defines `PowerSupplyInterface`. The simulator
implements every abstract operation, including explicit programmed-setpoint
readback and output enable-state readback. All units are V, A and s; channel
identifiers are integers 1 and 2. Capabilities are accessible while disconnected.
Instrument operations require a connection and raise `InstrumentNotConnectedError`
if the session is closed, including attempts to read or turn an output off.

Calls are synchronous and intentionally may wait for simulated communication
latency. GUI Connect, Identify and Disconnect calls belong to the milestone 4
`InstrumentConnectionWorker`. The simulator is not a thread-safe replacement
for the future shared command queue, and it must not be called directly from GUI
callbacks. Constructing the simulator itself starts no thread.

```python
from cpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from cpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from cpx_electrolyser.instrument.simulator_config import SimulatorConfig

supply: PowerSupplyInterface = SimulatedPowerSupply(
    SimulatorConfig(communication_delay_s=0.01, random_seed=123)
)
supply.connect()
try:
    supply.set_voltage(1, 3.0)     # Compliance, V
    supply.set_current(1, 10.0)    # Requested current, A
    supply.output_on(1)
    print(supply.read_voltage(1), supply.read_current(1))
finally:
    try:
        supply.output_off(1)
    finally:
        supply.disconnect()
```

This example creates a fresh simulator and uses only CH1. The two-channel demo
attempts both OFF calls independently before disconnecting.

## Load and crossover

Each output has its own immutable `ElectrolyserModel`, initially:

| Parameter | Default |
| --- | --- |
| `onset_voltage_v` | 1.5 V |
| `resistance_ohm` | 0.1 ohm |
| `logarithmic_coefficient_v` | 0.1 V |

For positive current, the toy load curve is:

`Vcell = V0 + R * I + a * ln(1 + I / 1 A)`

If the voltage required to support the current limit fits under the voltage
setpoint, the simulated output operates in CC: current equals its limit and
voltage follows the load curve. Otherwise, it operates in CV: voltage equals
its setpoint and current is found by a bounded 64-iteration bisection of the
monotonic load curve. The current cannot exceed the programmed current limit.

Below the model's onset voltage, a nonzero enabled voltage can be present with
zero current. An output that is disabled reads exactly zero V and A. With an
enabled zero voltage setpoint or zero current limit, ideal V/I are also zero;
enabled-output measurement noise may still affect readback. The zero-current
convention is a simulation simplification, not a CPX400DP specification.

The load is instantaneous, without capacitance, electrochemical transients,
open-circuit back EMF, temperature dependence, ageing or fitted scientific
meaning. V/I are separate read operations, not simultaneous ADC samples.

## Configuration

`SimulatorConfig` is a frozen dataclass; its channel-model tuple and each model
are immutable. Create a new simulator to change its model or noise settings.

| Field | Default / allowed values |
| --- | --- |
| `channel_models` | Exactly two `ElectrolyserModel` instances in a tuple |
| `communication_delay_s` | 0.01 s; range 0-5 s per valid instrument operation |
| `voltage_noise_sd_v` | 0 V; range 0-60 V |
| `current_noise_sd_a` | 0 A; range 0-20 A |
| `random_seed` | 0; integer for reproducibility, or None for a fresh seed |
| `max_modelled_power_w` | 400 W; positive and at most 400 W |

Delay/noise ranges are simulation configuration guards, not measured instrument
characteristics. Tests set delay to zero or inject a delay callback, so they
exercise latency accounting without actually sleeping. Health/capability lookups,
repeated connect/disconnect no-ops and rejected arguments do not incur delay.

Noise is Gaussian and applied after solving the electrical operating point.
Readbacks are floored at zero and quantised to 10 mV / 10 mA using Python's
nearest rounding. Noise may place a reading above its ideal limit; it never
changes the load, setpoints or output state. Disabled outputs report zero without
noise. Separate RNGs for each channel and measured quantity ensure that polling
one channel cannot alter the other's noise sequence. No measurement history is
retained, and module-level random state is not used.

## Capabilities and rejection semantics

`instrument/capabilities.py` contains immutable `CPX400DP_CAPABILITIES`, with
60 V, 20 A and a 420 W hardware envelope per channel. Remote programming uses
10 mV voltage and 1 mA current increments; readback conservatively uses the
documented 10 mV / 10 mA meter resolution. The 4 Hz meter cadence is not emulated
and is not promised as a remote sampling specification. Sources remain in the
[command reference](cpx400dp_commands.md).

Channel IDs, finite numbers, range and programming increments are validated.
Booleans and numeric strings are not numbers for this API. Unsupported steps
raise `InstrumentValueError`; no warning-free rounding or unsafe setpoint clamping
is performed. Only a few floating-point ULPs of representation error are
normalised, so `0.1 + 0.2` is accepted on the voltage grid.

The real supply can become unregulated outside its power envelope. This
simulator does not pretend to model that regime or a perfect constant-power
controller: it rejects a candidate active operating point above its conservative
400 W model limit with `UnsupportedOperatingPointError`. The output state and
previous settings remain unchanged. OFF always remains allowed while connected.

The model checks actual ideal load power, not the product of compliance and
current limit. Independent in-range V/I settings can be stored while OFF even
if their product exceeds 400 W; enabling them must pass the operating-point
check. Full-protocol validation, user safety limits, trip registers, OCP/OVP,
communication-loss recovery and emergency cancellation are still later work.
This backend is not evidence that those safety mechanisms have been implemented.

## Connection lifecycle

A newly constructed simulator is disconnected with both outputs OFF and both
setpoints zero. First connect makes the session available. Repeated connect
does not reset an active output. Disconnect closes the simulated communications
link while preserving setpoints, output latches and RNG state. Reconnecting
therefore reveals the same output state; it does not silently restart or stop it.

This deliberately exercises the manual's absence of a documented watchdog.
The session cannot be used to confirm output state while disconnected. The
future controller must explicitly command and confirm both outputs OFF before
normal shared shutdown. A link close is never a substitute for that sequence.
