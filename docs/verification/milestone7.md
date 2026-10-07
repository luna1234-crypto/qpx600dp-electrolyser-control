# Milestone 7 verification - dual-channel manual controls and hardware acceptance

Initial Simulation Mode verification: 2026-09-29

Real-hardware GUI acceptance: 2026-10-07

## Scope and safety boundary

The initial Milestone 7 checkpoint replaced both channel placeholders with
independent Simulation Mode manual controls. Two Qt-thread channel controllers
shared the single Milestone 6 worker and its one `SimulatedPowerSupply`. The
guarded explicit-port real-hardware connection was added in the subsequent
Milestone 7 follow-up without adding port enumeration.

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

## Real-hardware GUI acceptance

After the guarded real-hardware follow-up and the independent Milestone 5
standard-load checks, the operator completed supervised GUI acceptance. The
instrument serial number is intentionally omitted.

For read-only acceptance, the operator selected Real Hardware, manually entered
COM3 and connected. The GUI correctly recognized a QPX600DP and both channel
panels matched the physical instrument. Disconnect completed normally and both
outputs remained OFF.

For CH1 loaded acceptance, one verified 100 ohm, 1/4 W resistor was connected to
CH1 only and CH1 was programmed to 4.000 V / 0.080 A. The confirmation dialog
correctly identified Real Hardware and CH1. GUI Output On succeeded; the display
showed approximately 4 V and approximately 0.03 A, consistent with the earlier
3.989-3.990 V / 0.037-0.038 A guarded hardware-check result and instrument
metering accuracy. The operator observed the physical ON indicator and CV state.
CH2 remained OFF. GUI Output Off and Disconnect both completed normally.

Only after both outputs were OFF, the same resistor was moved to CH2. CH2 was
programmed to 4.000 V / 0.080 A, and the confirmation dialog correctly identified
Real Hardware and CH2. GUI Output On, live voltage/current display, physical ON
and CV indication, Output Off and Disconnect all operated normally. Operator
observation was approximately 4 V and approximately 0.04 A; no more precise GUI
measurement is claimed. CH1 remained OFF.

At completion, OP1 and OP2 were visually confirmed OFF and the resistor was
removed only after disconnection. This completes Milestone 7 real-hardware GUI
acceptance for independent manual control with the standard resistor load.
Electrolyser validation remains pending and is outside Milestone 7.

After recording the acceptance result, the complete automated suite passed with
238 tests. `compileall`, `pip check`, the non-interactive Windows Simulation Mode
smoke test and `git diff --check` also passed. These final documentation checks
used only simulator and fake backends; they did not reconnect to COM3 or send any
further instrument command.

## Deferred work

Protocols and automated sequences, pause/resume, CSV logging, plotting,
Emergency All Outputs Off and all Milestone 8+ functionality remain
unimplemented. Electrolyser validation also remains pending and out of scope.

The guarded explicit-port connection was added in the subsequent Milestone 7
follow-up; see [its separate verification record](milestone7_real_hardware.md).
