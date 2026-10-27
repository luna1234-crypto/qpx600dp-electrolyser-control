# Milestone 13 verification - automatic stepped Current Scan

Date: 2026-10-27

## Accepted scope

Milestone 13 implements independent CH1 and CH2 automatic stepped Current Scan.
Simulation Mode and supervised CH1/CH2 resistor-load operation are accepted. A
guarded Real Hardware follow-up was fake-serial tested before supervised use. No
COM port, Test Bridge, `hardware_check`, diagnostic tool or physical instrument
was accessed.

Each panel accepts start current, stop current, signed current step, voltage
compliance, dwell per step and sampling interval. It displays state, step/total,
commanded current, active elapsed/remaining time, measurements, output state,
missed samples and concise status.

## Shared scan engine

`CurrentScanController` supplies only current-axis validation and setpoint
selection to the Milestone 12 `SteppedScanController`. The existing shared engine
continues to own protocol state, monotonic dwell/sample scheduling, queue
submission, Pause/Resume active-time accounting, logging ownership, plotting
signals and bounded Output-Off cleanup. No duplicated scan state machine,
backend access, writer, poller or worker thread was added.

Before ownership or mutation, the full exact-decimal plan is checked against the
QPX600DP current programming grid, selected voltage range, conservative power
envelope and project voltage/current/power/duration limits. Ascending and
descending plans include a non-divisible endpoint exactly once without drift or
overshoot.

## Sequence and safety behavior

The initial queued transaction confirms selected output OFF, applies voltage
compliance and initial current, and verifies both. The existing guarded start
path enables the simulated output once. Each dwell uses active monotonic time;
at a boundary one atomic queue action sets the next current and obtains complete
authoritative readback. Queue latency is excluded from the next dwell, late
sampling slots are counted, and no overlapping poll is submitted.

Pause confirms selected Output Off and freezes active time. Resume revalidates
the same current step before continuing, without skipping or duplicating it.
Stop, normal completion, setting/readback failure, logging failure, disconnect
and shutdown use the existing bounded channel-local cleanup. CH1 and CH2 remain
isolated while their actions share the one serialized worker.

## CSV and plot integration

A Current Scan creates one channel CSV or reuses an already-active same-channel
logger. Pause/Resume preserves the writer, and completion does not stop an
operator-owned logger. The backward-readable optional scan fields are reused:

- `scan_name` is `current_scan`;
- `scan_step_index` and `scan_total_steps` identify the step;
- `scan_commanded_setpoint` contains the commanded current in amperes.

Verified scan readbacks flow through the existing sample signal to the manual
display and bounded plots. Plotting does not submit any backend request.

## Automated coverage

Focused simulator/fake-backend and Qt tests cover:

- exact ascending, descending and non-divisible inclusive plans;
- invalid, zero, wrong-direction, non-finite and off-grid values;
- current capability, voltage range, project and power validation before side effects;
- exact initial/step command order and setpoint readback mismatch;
- one Output-On/Off lifetime, safe completion and other-channel isolation;
- dwell/sample timing, missed slots and no overlapping polling;
- Pause/Resume during dwell and inherited boundary behavior;
- Stop, failure, logging failure, disconnect and shutdown cleanup;
- duplicate/stale action rejection;
- simultaneous CH1/CH2 serialization;
- one-writer CSV continuity, `current_scan` metadata and existing plot flow;
- guarded Real Hardware confirmation, command ordering, failure and cleanup.

The automated Simulation Mode application smoke runs simultaneous CH1/CH2
Current Scans after the existing Constant Current, Constant Voltage and Voltage
Scan checks. It verifies active logger reuse, inclusive completion, plots, final
simulated Output Off, clean disconnect and no residual worker/QThread warning.

Validation results:

- focused Milestone 12/13 and guarded fake-serial scan suite: **57 passed**;
- complete regression suite: **437 passed**;
- `compileall`: **passed**;
- `pip check`: **no broken requirements**;
- simultaneous CH1/CH2 Simulation Mode smoke: **passed**;
- clean disconnect/worker shutdown with no QThread-destroyed warning: **passed**;
- `git diff --check`: **passed**.

## Manual Windows Simulation Mode acceptance

Manual operator acceptance passed on 2026-10-27 using the procedure in
[`tests/manual_smoke_test.md`](../../tests/manual_smoke_test.md):

- CH1 completed an ascending Current Scan;
- CH2 completed a descending Current Scan;
- independent and simultaneous CH1/CH2 operation passed with channel isolation;
- Pause/Resume and early Stop passed;
- the step sequences, real-time plots and CSV scan metadata were correct;
- both simulated outputs finished OFF; and
- invalid input produced a visible validation error in the affected channel panel.

This section records Simulation Mode acceptance. Supervised Real Hardware
acceptance is recorded separately below.

## Simulation Mode Start feedback follow-up

Manual testing exposed an integration-only feedback defect: `MainWindow`
validated Current Scan input before calling the controller, but a rejected Start
was written only to the status bar. Because the controller correctly remained
`IDLE`, the Current Scan panel itself showed no state or message change and the
click appeared to do nothing. Button signal emission, CH1/CH2 mapping and signed
negative-step handling were otherwise correct.

Rejected Current Scan Start requests now display the validation message directly
beside that channel's scan controls as well as in the status bar. No controller,
queue, simulator or scan-state behavior changed. Qt regressions prove CH1 and
CH2 Start route only to their matching controllers, simultaneous starts work,
`-0.08 A` produces the inclusive descending sequence, invalid input performs no
backend operation and displays a clear error, and cross-channel ownership is
unchanged.

## Guarded Real Hardware follow-up

Current Scan shares the same guarded implementation as Voltage Scan and does not
copy the scan or transition state machine. Start and Resume use detailed,
default-cancel confirmation, require an identified QPX600DP session, selected
output OFF, the expected voltage range and verified voltage-compliance/current
readbacks. Output transitions are the accepted Milestone 11 sequence: one
`OP<N>` write, one `*OPC?`, a query-free non-blocking 1.0-second settle, two
state confirmations about 100 ms apart and an authoritative channel readback.

Each new current is set and fully read back in one serialized queue operation
before dwell. Pause freezes active time and reuses the CSV on Resume. Stop,
completion, failure, disconnect, logging failure and shutdown use the inherited
single safety-priority Output-Off attempt with no retry or reconnect. Fake serial
tests cover exact order, default cancellation, mismatch and malformed-response
cleanup, stale/double Resume rejection, CSV continuity, both channels and
simultaneous scans. Supervised 100-ohm validation is recorded below.

## Supervised Real Hardware acceptance

Supervised functional acceptance passed on 2026-10-27. CH1 and CH2 each
completed Current Scan with the verified 100-ohm load. Pause/Resume, early Stop
and natural completion passed. The commanded step sequence, CSV scan metadata
and real-time plots were correct, with no skipped or duplicated step. Channel
isolation passed, and both the physical indicators and GUI reported both outputs
OFF at the end.

This is resistor-load functional acceptance. Low-current readings were not
evaluated as metrology calibration evidence.
