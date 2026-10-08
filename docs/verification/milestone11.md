# Milestone 11 verification - independent Constant Voltage protocols

Date: 2026-10-08

## Scope

CH1 and CH2 each have a Constant Voltage panel with target voltage, current
limit, active duration, measurement interval, Start, Pause, Resume and Stop.
The initial implementation and manual acceptance used Simulation Mode. The later
guarded Real Hardware follow-up was developed with injected fake serial only. No
COM port or physical instrument was enumerated, opened, probed or commanded.

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

- guarded Constant Voltage focused suite: **65 passed in 7.35s**;
- complete suite: **311 passed in 23.52s**;
- `compileall`: passed;
- `pip check`: no broken requirements;
- automated Simulation Mode smoke: passed for simultaneous CH1/CH2 Constant
  Current followed by simultaneous CH1/CH2 Constant Voltage, shared logger/plot
  flow, final OFF cleanup, disconnect and worker shutdown;
- `git diff --check`: passed;
- no residual smoke process or QThread-destroyed warning was observed.

Sweeps and Milestone 12 are not implemented.

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
accessed.

## Guarded Real Hardware follow-up

Real Hardware Constant Voltage Start and Resume now require a distinct exact
confirmation token that is supplied only after a detailed, default-cancel GUI
dialog. The dialog identifies Real Hardware, channel, target voltage, current
limit, duration, sample interval, current displayed OFF state and CSV destination.
Cancelling submits no command and creates no protocol file.

The controller independently requires the current identified QPX600DP session.
Its queued start transaction queries selected output state, refuses an initially
ON output, queries the existing voltage range before writing, writes only selected
channel voltage/current limit and verifies both readbacks. A second fresh
OFF/settings preflight immediately precedes exactly one Output-On attempt and ON
readback. Resume repeats the confirmation and preflight. There is no automatic
Output-On retry, connection retry or reconnect.

Pause, Stop, natural completion, failure, coordinated disconnect and shutdown use
safety-priority selected-channel Output Off followed by a complete authoritative
readback. An unavailable or unconfirmed OFF state produces a prominent operator
warning. Request IDs, channel-specific cancellation keys and owning states reject
double-clicks and stale callbacks. CH1/CH2 remain isolated on the one shared
worker. Existing logger ownership and plot signals are unchanged, so the guarded
path creates one CSV only when needed, reuses an active same-channel CSV, and
adds no measurement polling.

Fake-serial coverage verifies exact query/write order, cancelled confirmation,
missing token, invalid values, initially-ON refusal, readback mismatch, ambiguous
Output-On failure without retry, cleanup/readback failure warnings, priority OFF,
Pause/Resume confirmation and CSV reuse, natural completion, early Stop,
coordinated disconnect, logger failure, stale start cancellation, duplicate-click
protection, simultaneous channels and CH1/CH2 mutation isolation. Physical
Constant Voltage acceptance remains pending.
