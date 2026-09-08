# Milestone 3 verification

Date: 2026-09-08. Version: 0.3.0. Starting checkpoint: `e3e1baa` (milestone 2).
Environment: Linux x86-64, CPython 3.12.13, PySide6 / Qt 6.10.1, pytest 9.0.2.

## Results

`python -m pytest -q` completed with **75 passed in 1.92 s**: 67 new capability
and simulator cases plus all 8 existing application/logging cases. Qt subprocesses
used offscreen rendering and the existing isolated Linux libEGL dependency.

| Area | Verified behaviour |
| --- | --- |
| Interface | Abstract contract cannot be instantiated; simulator fulfils it and identifies as simulated |
| Capabilities | Values match the supplied manual; definitions are immutable and validated |
| Connection | Initially disconnected; instrument calls reject closed sessions; connect/disconnect are idempotent |
| Output state | Setters do not enable outputs; OFF preserves setpoints and gives zero readings |
| Channel independence | Either channel can start/change/stop while the sibling retains settings, state and measurements |
| CC / CV | Nonlinear load readback; analytically checked linear-load crossover and voltage compliance |
| Zero / onset behaviour | Zero setpoints and below-onset load behaviour follow the documented simulation convention |
| Resolution | 1 mA programmed current is distinct from 10 mA readback; unsupported grids are rejected |
| Bad arguments | Invalid channels, booleans, non-finite values, range violations and unsupported increments leave active outputs unchanged |
| Model boundary | Unsupported high-power ON and live setpoint changes are rejected without mutation |
| Latency | Each valid operation incurs one configured delay; local no-ops and rejected arguments do not |
| Noise | Reproducible, quantised and independent of sibling-channel polling; instances own separate state |
| Reconnection | Output latches survive link closure; disconnected state cannot be mistaken for OFF |
| Regression | Existing GUI launch, path handling, log preservation, rotation and controlled failures remain working |

The console demo completed with these simulated readings:

```text
SIMULATOR,CPX400DP,SIMULATED,0.3.0
CH1 ON  | 2.74 V | 10.00 A
CH2 ON  | 2.20 V | 5.18 A
After switching CH1 off:
CH1 OFF | 0.00 V | 0.00 A
CH2 ON  | 2.20 V | 5.18 A
```

The model uses constant-size channel state and RNGs without retaining samples.
This is not a 24-hour accelerated stress result: that remains milestone 20.
No native Windows or physical instrument test was performed. GUI simulator
connection controls, real transport/driver, concurrency queue, protocols, CSV
logging and the complete safety system remain unimplemented in this milestone.
