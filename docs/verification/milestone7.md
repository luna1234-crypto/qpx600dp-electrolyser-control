# Milestone 7 verification - dual-channel manual controls

Date: 2026-09-29

## Scope and safety boundary

Milestone 7 replaces both channel placeholders with independent Simulation Mode
manual controls. Two Qt-thread channel controllers share the single Milestone 6
worker and its one `SimulatedPowerSupply`. The physical `SerialTransport` and
`QPX600DPDriver` remain unavailable to the GUI; there is no port selection or
enumeration path.

Each panel displays programmed voltage, current limit, measured voltage/current,
voltage range and output state. Voltage and current-limit input uses
`QPX600DP_CAPABILITIES`; invalid text is rejected before queue submission.
Refresh, validated setters and deliberately confirmed simulated output actions
are non-blocking. Each action and its complete read-back execute as one atomic
worker operation. Output Off uses safety priority.

## Automated coverage

All new tests use `SimulatedPowerSupply` or subclasses that record/fail calls:

- independent CH1/CH2 targeting and read-back;
- range, measurement and visually explicit output-state display;
- Output On rejection without explicit confirmation and confirmed simulation;
- empty, invalid, negative, non-finite, out-of-range and off-grid input;
- repeated-click suppression while a command is pending;
- concurrent channel submissions through one backend-owner worker;
- main-thread result delivery and worker-thread backend ownership;
- channel-local exception handling with preserved sibling state;
- targeted cancellation, disconnect and disabled/unknown UI state;
- clean bounded worker shutdown with no residual worker warnings.

Existing fake-serial driver/transport tests remain offline. No test enumerates,
opens or probes a COM port.

## Validation results

Validation uses Windows 11 Pro 24H2, AMD64, CPython 3.12.10 64-bit and the
project virtual environment. The final checks are:

```text
python -m pytest -q
python -m compileall -q main.py qpx_electrolyser tests examples
python -m pip check
python main.py --smoke-test
git diff --check
```

Results:

- complete suite: **195 passed in 4.41s**;
- `compileall`: exit code 0;
- `pip check`: `No broken requirements found.`;
- native Windows Simulation Mode GUI smoke test: exit code 0, version 0.7.0
  window shown and visually inspected, Qt events processed and worker stopped
  cleanly;
- residual `InstrumentCommandWorker` threads or QThread-destroyed warnings: none;
- `git diff --check`: exit code 0.

The validation paths used only the simulator and fake backends. COM3, Test
Bridge and the physical QPX600DP were not enumerated, opened, probed or commanded.

## Deferred work

Real-hardware GUI connection, serial-port selection, protocols and automated
sequences, pause/resume, CSV logging, plotting, Emergency All Outputs Off and all
Milestone 8+ functionality remain unimplemented.
