# Milestone 12 verification - automatic stepped Voltage Scan

Date: 2026-10-14

## Accepted scope

Milestone 12 implements independent CH1 and CH2 automatic stepped Voltage Scan
in Simulation Mode. No COM port was enumerated, opened or probed; Test Bridge,
`hardware_check`, diagnostic tools and the physical QPX600DP were not used.
There is no enabled Real Hardware scan path. The GUI disables Voltage Scan in
Real Hardware context and displays `Milestone 12 real-hardware acceptance
pending`.

Each panel accepts start voltage, stop voltage, signed voltage step, current
limit, dwell time per step and sample interval. It displays protocol state,
current/total step, commanded setpoint, active elapsed/remaining time, latest
measurements, output state, missed samples and operator status.

## Reusable scan core

`SteppedScanController` extends the established Constant Current/Constant
Voltage controller rather than copying its state machine. It reuses:

- `IDLE`, `STARTING`, `RUNNING`, `PAUSING`, `PAUSED`, `RESUMING`, `STOPPING`,
  `COMPLETED` and `FAILED` ownership states;
- the one shared serialized command worker and per-channel cancellation keys;
- guarded settings application/read-back and exactly-one output lifetime;
- Pause/Resume active-time accounting and bounded Output-Off cleanup;
- single-writer CSV ownership and existing sample signals for plots/manual state;
- disconnect and application-shutdown coordination.

The reusable controller accepts a scan axis. Milestone 12 supplies the voltage
axis; Milestone 13 can supply a current axis without copying dwell, step,
ownership, logging or cleanup behavior.

## Setpoint and timing behavior

Setpoints are planned with `Decimal` arithmetic before ownership, file creation
or queue submission. Positive steps are required for ascending scans and
negative steps for descending scans. Zero and wrong-direction steps are
rejected. The stop value is included once; when a step does not divide the span,
the exact stop is appended without overshoot. Every planned voltage and the
fixed current limit must satisfy the selected QPX600DP voltage range,
programming grids, conservative power envelope and existing project policy.

The initial queued transaction confirms selected output OFF, writes initial
voltage and current limit, and verifies both. A fresh preflight then enables the
simulated output once. Sampling uses monotonic active time with one pending poll
maximum. At each dwell boundary, timing is frozen while one atomic worker action
sets the next voltage and obtains authoritative read-back. The next dwell begins
only after verification, so queue latency does not consume it. Late sampling
slots increment the missed count and are never replayed in a burst.

Pause cancels a pending sample, confirms selected output OFF and freezes active
time. Resume verifies the current step and continues its remaining dwell; it
does not advance, duplicate or skip a step. Stop, normal completion, scan/write
failure, logging failure, disconnect and shutdown use the existing channel-local
cleanup. The other channel is untouched unless the shared connection fails.

## CSV and plot compatibility

A scan creates one exclusive channel CSV when no logger is active or appends to
the already-active same-channel logger. Pause/Resume retains the same writer;
completion does not stop an operator-owned logger. Four columns are appended to
the existing schema:

1. `scan_name`
2. `scan_step_index`
3. `scan_total_steps`
4. `scan_commanded_setpoint`

They are blank for standalone and Milestone 8–11 rows, preserving the earlier
columns and backward readability. Scan samples and verified step readbacks feed
the existing plot signal. Plotting performs no queue submission or additional
backend poll.

## Automated evidence

Focused simulator/fake-backend and regression tests cover:

- exact ascending, descending, decimal and non-divisible endpoint planning;
- invalid, non-finite, zero and wrong-direction inputs;
- capability, range, power, current, dwell and interval validation;
- exact initial command order, one Output On/Off lifetime and worker-thread use;
- ordered step application without duplicates or omissions;
- monotonic dwell/sample scheduling and missed-slot counting;
- Pause/Resume during dwell and at a step boundary;
- Stop, completion, step failure, logging failure, coordinated disconnect and
  shutdown cleanup;
- duplicate starts/stops and stale callback rejection;
- CH1/CH2 isolation and simultaneous queue serialization;
- one writer per channel, active-logger reuse, CSV continuity and scan columns;
- plot reuse without a separate polling path;
- visibly disabled Real Hardware scan controls.

The automated Simulation Mode application smoke connects the simulator, keeps
both standalone loggers active, completes simultaneous CH1/CH2 Constant Current,
Constant Voltage and Voltage Scan runs, verifies CSV reuse and inclusive scan
completion, exercises both plots, disconnects and exits without residual worker
or QThread warnings.

Validation results:

- focused Milestone 12 and M8–M11 regression suite: **78 passed**;
- full pytest suite: **403 passed**;
- `compileall`: **passed**;
- `pip check`: **no broken requirements**;
- automated Simulation Mode smoke including simultaneous CH1/CH2 scans:
  **passed**;
- `git diff --check`: **passed**.

The Windows manual procedure is documented in
[`tests/manual_smoke_test.md`](../../tests/manual_smoke_test.md). Final operator
acceptance was performed after the committed GUI was launched in Simulation
Mode.

## Manual Windows Simulation Mode acceptance

Manual operator acceptance passed on 2026-10-14. CH1 completed an ascending
`1.0 -> 1.5 -> 2.0 V` scan while CH2 simultaneously completed a descending,
non-divisible `2.0 -> 1.6 -> 1.2 -> 1.0 V` scan. The channels remained isolated.
Each used an independent 3-second dwell and 0.25-second sampling interval.

For this Simulation Mode acceptance only, the current limit was 3.50 A. Under
the documented simulator load curve, that limit permits the simulated load to
reach 2.0 V without entering current-limit operation, so measured voltage
matched every commanded scan setpoint. This 3.50 A value is not approved or
recommended for physical hardware.

Pause/Resume completed without a skipped or duplicated step, and paused time
was excluded from active timing. CSV step metadata, real-time plots, normal
completion and final simulated Output Off all passed. Real Hardware Voltage
Scan implementation and acceptance remain pending and unapproved.
