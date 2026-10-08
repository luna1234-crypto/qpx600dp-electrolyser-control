# Milestone 11 verification - independent Constant Voltage protocols

Date: 2026-10-08

## Scope

CH1 and CH2 each have a Constant Voltage panel with target voltage, current
limit, active duration, measurement interval, Start, Pause, Resume and Stop.
Milestone 11 is Simulation Mode only. No COM port or physical instrument was
enumerated, opened, probed or commanded during implementation or validation.

`ConstantVoltageController` specializes the Milestone 9 state machine rather
than creating another scheduler. It uses the same `IDLE`, `STARTING`, `RUNNING`,
`PAUSING`, `PAUSED`, `RESUMING`, `STOPPING`, `COMPLETED` and `FAILED` states,
the shared Milestone 6 queue, fixed monotonic active-time schedule, missed-slot
accounting and bounded safety cleanup.

Start confirms the selected output is OFF, writes target voltage then current
limit, reads both settings back, performs a fresh OFF/settings preflight, sends
Output On exactly once and confirms ON. Pause and Stop use safety-priority Output
Off and require authoritative OFF readback. Resume revalidates the same session,
OFF state and settings before one Output-On attempt. Completion, local failure,
coordinated disconnect and application shutdown use the same bounded cleanup.

## CSV and plot integration

Protocol reads emit the existing immutable channel readback. The Milestone 10
plot consumes that signal; plotting creates no measurement request. If a
Milestone 8 channel logger is already active, CV appends `constant_voltage` and
protocol state rows through that logger's one writer. Pause/Resume keep the file,
and completion or Stop does not stop an operator-started logger. Otherwise CV
owns one exclusively created protocol CSV and closes it deterministically.

Constant Current and Constant Voltage are mutually exclusive on the same channel.
CH1 and CH2 remain independent and may run CV simultaneously while the shared
worker serializes every complete command-response operation.

## Automated and Windows Simulation validation

Focused tests cover both channels, exact start order, one Output-On attempt,
confirmed completion cleanup, invalid inputs before file/queue side effects,
simultaneous serialized operation, Pause/Resume active-time exclusion, early
Stop, shared logger ownership, CSV protocol identity, live-plot updates,
cross-protocol ownership and shutdown cleanup.

The complete automated suite, compileall, dependency check, automated Simulation
Mode smoke and `git diff --check` passed before manual acceptance. The
automated smoke runs both Constant Current channels followed by both Constant
Voltage channels while the two existing channel loggers and plots remain active;
all outputs must finish OFF and the shared worker must exit without QThread
warnings or residual processes.

Validation results:

- complete suite: **304 passed in 23.65s**;
- `compileall`: passed;
- `pip check`: no broken requirements;
- automated Simulation Mode smoke: passed for simultaneous CH1/CH2 Constant
  Current followed by simultaneous CH1/CH2 Constant Voltage, shared logger/plot
  flow, final OFF cleanup, disconnect and worker shutdown;
- `git diff --check`: passed;
- no residual smoke process or QThread-destroyed warning was observed.

Real Hardware Constant Voltage, sweeps and Milestone 12 are not implemented.

## Manual Windows Simulation Mode acceptance

Operator acceptance passed after the automated validation. The following CSV
ownership and concurrency behavior was confirmed:

- starting a protocol without an active same-channel logger automatically
  created one channel-specific CSV;
- when same-channel logging was already active, the protocol reused that
  existing CSV rather than creating a second writer or file;
- Pause and Resume retained the same CSV;
- a new protocol run created a new CSV;
- independent CH1-only and CH2-only operation passed;
- simultaneous CH1/CH2 operation passed.

The acceptance used Simulation Mode only. No real instrument or COM port was
accessed, and no Real Hardware Constant Voltage acceptance is claimed.
