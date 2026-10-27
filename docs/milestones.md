# Milestone verification log

> Milestones 1-4 below are historical records created under the original
> CPX400DP assumption. Their claims are intentionally not rewritten as QPX600DP
> results. The post-Milestone-4 model correction is recorded separately at the
> end of this file and in `verification/model_correction_qpx600dp.md`.

## Milestone 1 - Project skeleton

Scope: source layout, Python requirements, minimal PySide6 window, application
entrypoints, bounded UTF-8 diagnostic logs and Windows setup instructions.

Verified on 2026-09-08: 8 pytest tests passed; both entrypoints launched real Qt
event loops and exited successfully; a standalone launch returned exit code 0.
The rendered window was inspected for readable labels and equal channel panels.
Source compilation and installed dependency checks passed. See
[verification details](verification/milestone1.md).

No instrument driver, simulator, command queue, experiment logging or protocol
implementation is part of this milestone. This checkpoint is committed before
milestone 2 documentation is created.

## Milestone 2 - CPX400DP command documentation

Verified on 2026-09-08 after the milestone 1 commit:

- Inspected the supplied Issue 1 manual's specifications, remote interfaces,
  command tables, status registers and operating limitations.
- Rendered and visually inspected the power-envelope specification and command
  tables; checked 22 selected command/page anchors programmatically.
- Wrote the verified command reference and the proposed ownership/threading,
  cancellation, data, safety and scheduling architecture.
- Preserved the original manual byte-for-byte with its SHA-256 recorded.
- Reran all 8 milestone 1 tests successfully and launched the application again
  with exit code 0. Documentation relative-link and whitespace checks passed.

See [verification details](verification/milestone2.md). This milestone changed
documentation only. At that checkpoint, milestones 3-20 remained unimplemented.

## Milestone 3 - Instrument abstraction and simulator

Implemented the synchronous `PowerSupplyInterface`, a frozen capability model
grounded in the supplied manual, configurable independent simulated channels,
CC/CV crossover, output enable/readback, bounded command delay and optional
reproducible measurement noise. Included a console demonstration that turns off
one output while the other remains active.

Verified on 2026-09-08: **75 tests passed**, comprising 67 new capability/simulator
cases and the existing 8 application/logging cases. The console demo completed
successfully and preserved CH2's 2.20 V / 5.18 A readback when CH1 was stopped.
See [verification details](verification/milestone3.md) and
[simulator behaviour](simulator.md).

The simulator's 400 W model boundary is deliberately conservative, distinct from
the hardware's 420 W envelope rating and the future application safety system.
Disconnect preserves simulated output states; it is not an implicit OFF command.
No GUI connection controls, physical driver, shared worker queue, experiment
protocols or experimental data logger were introduced. Stop here after the
milestone commit; milestones 4-20 are still pending.

## Milestone 4 - Basic connection GUI

Implemented a reusable connection panel and explicit connection-state snapshots.
Simulation Connect, Identify and Disconnect execute on one dedicated backend-owner
thread, not the Qt thread. A failed connection or identification attempts cleanup,
discards the candidate and permits retry with a fresh backend. Application exit
requests cleanup and uses a bounded thread join.

Verified on 2026-09-23: **80 tests passed** (75 existing and 5 new). Tests cover
failure cleanup/retry, single-thread backend ownership, bounded shutdown, button
states, asynchronous GUI transitions and Qt event-loop responsiveness. Both
disconnected and connected window states were visually inspected after offscreen
rendering on macOS. See [verification details](verification/milestone4.md).

The lifecycle worker is intentionally limited to connection operations. Milestone
6 must evolve this same single-owner boundary into the full priority command worker,
not create a second instrument owner. No serial dependency, COM-port enumeration,
physical driver, output control or experimental operation was added. Native Windows
and physical hardware validation remain outstanding.

## Post-Milestone-4 model correction - QPX600DP

Implemented on 2026-09-24 after the field target was confirmed as Aim-TTi
QPX600DP. This is a correction checkpoint, not Milestone 5. The canonical package,
current UI identity, capabilities, simulator ID, voltage ranges, resolutions,
tests and active documentation now target QPX600DP. The Issue 14 manual and
Issue 1 addendum are the active references; CPX material remains only as clearly
marked historical/superseded evidence.

The simulator uses only manual-stated power-envelope anchors: 50 A through 10 V
and 7.5 A above 10 V through 80 V, with the separate 600 W ceiling. It does not
interpolate the undocumented graph. Hardware ratings remain separate from the
optional `UserSafetyLimits`; no experiment safety defaults were introduced.

Verified with CPython 3.12.14: **90 tests passed**. `python main.py` completed a
visible Simulation Mode connect/disconnect/reconnect/close check, and
`python -m qpx_electrolyser --smoke-test` processed a visible Qt event loop and
stopped cleanly. See [verification details](verification/model_correction_qpx600dp.md).

No COM port was enumerated, no physical transport or driver was added, no
instrument command was sent and no real output was controlled. Stop at this
correction checkpoint; Milestone 5 remains unstarted.

## Milestone 5 - Offline QPX600DP serial driver

Implemented the explicit-port `SerialTransport` and synchronous
`QPX600DPDriver` from the QPX600D & DP Issue 14 command reference. The driver
supports connection lifecycle, identification, dual-channel voltage/current
set and read, measured values, explicit output On/Off, output-state query,
existing voltage-range query and non-destructive status-byte query. Connecting
and disconnecting send no instrument bytes and do not reset, reconfigure or
enable an output.

Verified on 2026-09-24 with CPython 3.12.14: **162 tests passed**. Fake serial
tests cover exact command bytes and terminators, both channels, parsing,
timeouts, malformed frames and values, lifecycle cleanup, error propagation and
numeric boundaries. Both application entrypoints completed offscreen smoke
tests, source compilation and dependency checks passed, and `git diff --check`
reported no errors. See [verification details](verification/milestone5.md).

The explicit-port hardware helper is read-only by default. Every mutation
requires a write-confirmation token. Guarded monitored Output On additionally
requires a separate strong token, explicit expected setpoints, current and
voltage limits, bounded polling and voltage-settling time. It polls output state,
loaded voltage and loaded current throughout the authorised duration and always
attempts selected-channel Output Off after Output On is attempted.

Supervised Windows validation identified a QPX600DP, passed default read-only
checks and guarded unloaded setpoint read-back, and completed standard-load tests
on both channels using the same verified 100 ohm +/-5%, 1/4 W resistor. At
4.000 V / 0.080 A, CH1 measured a stable 3.989-3.990 V / 0.037-0.038 A for
5.015 seconds; CH2 measured 3.995-3.998 V / 0.040-0.041 A for approximately
5.03 seconds. Output-Off cleanup passed after each run and final OP1/OP2 state
was OFF. The operator visually confirmed the CH2 physical ON indicator and CV
display. Values were consistent with the nominal load.

Earlier ModuLab Test Cell attempts were safely aborted. Its 10 mA maximum was
determined incompatible with the QPX600DP current-setting and metering
resolution/accuracy for this validation. Electrolyser validation is outside
Milestone 5 and remains pending. See [verification details](verification/milestone5.md).

## Milestone 6 - Shared asynchronous instrument command queue

Evolved the Milestone 4 lifecycle owner into one `InstrumentCommandWorker` that
exclusively creates, connects, uses and disconnects the active backend. All
operations share one priority queue and owner thread. Monotonic sequence numbers
preserve FIFO order within each priority, while explicit safety, connection,
normal and background levels provide a tested path for future urgent commands.

Command tickets expose thread-safe completion, exception and pre-dispatch
cancellation state without making GUI callbacks wait. Optional cancellation keys
support future channel/run invalidation. Disconnect cancels queued ordinary work
and runs after an atomic in-flight operation; shutdown stops new submissions,
cancels queued work, disconnects the backend on its owner thread and performs a
bounded join. Results and errors cross back to the Qt main thread through queued
signals.

Simulation and fake-backend tests cover FIFO and priority order, atomic execution,
concurrent submissions from two logical channels, exception isolation, bounded
capacity, disconnect, targeted cancellation, duplicate-connect prevention,
post-shutdown rejection and clean thread termination. The complete suite, source
compilation, dependency check and Windows Simulation Mode smoke test passed:
**172 tests passed**. See [verification details](verification/milestone6.md).

The GUI remains Simulation Mode only. This milestone adds no channel controls,
protocols, data logging, plotting, Emergency Off GUI, Output On path or real
serial integration. No COM port or physical instrument was accessed.

## Milestone 7 - Independent dual-channel manual-control GUI

Replaced both channel placeholders with reusable independent control panels.
Each displays programmed voltage, current limit, measured voltage/current,
selected voltage range and a conspicuous output state. Validated voltage and
current-limit setters, Refresh, simulated Output On with deliberate confirmation,
and simulated Output Off all use the one Milestone 6 queue. Each mutation and its
read-back are atomic; request IDs and cancellation keys keep channel state
isolated and prevent repeated-click duplicates.

Input validation uses the repository capability definitions and rejects empty,
invalid, negative, non-finite, out-of-range and off-grid values before queueing.
Backend failures retain the last successful display and fault only the affected
panel. Disconnect, cancellation and shutdown return the UI to a disabled state.
Tests cover both channel targets, read-back, output confirmation/state, invalid
input, duplicate clicks, concurrent submissions, exceptions, cancellation,
disconnect and clean shutdown. See [verification details](verification/milestone7.md).

Verified on Windows 11 Pro 24H2 with 64-bit CPython 3.12.10: **195 tests
passed**. Full source compilation and `pip check` passed. The native Windows
Simulation Mode GUI opened, rendered the two disabled disconnected panels,
processed Qt events and closed with clean worker shutdown. No residual worker or
QThread-destroyed warning was observed, and `git diff --check` passed.

The GUI remains strictly Simulation Mode only. No serial-port selection, real
hardware integration, protocol/sequencing, data logging, plotting, Emergency All
Outputs Off or later-milestone feature is included.

### Milestone 7 follow-up - guarded real-hardware connection

Added an explicit Real Hardware mode while retaining Simulation Mode as the
default. Entering real mode requires a warning acknowledgement, a manually typed
`COM<N>` name and a separate Connect action. Port selection performs no I/O and
there is no enumeration or probing. Only the shared worker constructs the
`SerialTransport` / `QPX600DPDriver`, and only after Connect.

Connection opens the selected port, identifies a QPX600DP and atomically reads
both channel states before enabling controls. The sequence contains queries only;
it does not reset, change voltage/current/range or alter outputs. Wrong identity,
timeout, malformed reply, busy port and incomplete initial state close the
candidate session without retry. Real Output On has a stronger channel/setpoint
confirmation, while Output Off remains directly available at safety priority.
Any later real command error closes the shared session and requires manual
reconnection.

Fake-serial tests cover exact query order, absence of connection writes, both
channel mappings, setpoint writes with outputs OFF, Output-On confirmation and
cancellation, duplicate suppression, failure cleanup/recovery, disconnect and
shutdown. The complete suite passed with **206 tests**. See
[follow-up verification](verification/milestone7_real_hardware.md).

No real port was enumerated, opened or probed during development or automated
validation. COM3, Test Bridge and the physical QPX600DP were not accessed during
that implementation phase.

Supervised real-hardware GUI acceptance was completed on 2026-10-07. A manually
entered COM3 connection identified the QPX600DP, populated both channel panels
correctly, and disconnected normally with both outputs OFF. Independent loaded
GUI checks then used the same verified 100 ohm, 1/4 W resistor, moved between
channels only after both outputs were OFF. At 4.000 V / 0.080 A, CH1 displayed
approximately 4 V / 0.03 A and CH2 approximately 4 V / 0.04 A. The confirmation
dialogs correctly named Real Hardware and the selected channel; Output On, live
readback, physical ON/CV indication, Output Off and Disconnect operated normally
for each channel while the other channel remained OFF. Final OP1 and OP2 were
visually confirmed OFF and the load was removed after disconnection.

This completes Milestone 7 real-hardware GUI acceptance. Electrolyser validation
remains pending and outside Milestone 7; this follow-up is not Milestone 8. See
[Milestone 7 verification](verification/milestone7.md).

## Milestone 8 - Independent continuous CSV logging

Added independent Start Logging and Stop Logging controls for CH1 and CH2, a
shared user-selected output directory and a bounded sample interval. Either
channel can log alone or both can log simultaneously. Every sample is one atomic
`BACKGROUND` operation on the Milestone 6 queue; logging and GUI code never hold
or call the backend.

CSV files are created exclusively with Windows-safe UTC names, written with one
fixed header, flushed after every row and closed deterministically. Rows record
wall and monotonic time, channel, both programmed values, both measurements,
range, output state, connection state/session generation and sample status/error.
Fixed monotonic deadlines avoid drift; delayed slots are counted and skipped,
not replayed as a burst, and overlapping channel polls are prevented.

Disconnect, reconnect, shutdown, missing directory, permission/disk/write error,
malformed results and backend/queue failures are covered. A file failure stops
only its channel. Reconnect requires a new explicit Start and new file. Protocols
and live plotting were deferred at the Milestone 8 checkpoint. The complete suite and Simulation Mode smoke
test passed without residual worker threads or QThread warnings. See
[verification details](verification/milestone8.md).

Supervised real-hardware read-only acceptance subsequently connected to a
manually entered COM3, identified the QPX600DP and ran both independent loggers
with no load and both outputs OFF. Both logged simultaneously for approximately
30 seconds; after CH1 stopped, CH2 continued for approximately 5 seconds. The
two files closed successfully and showed increasing timestamps/elapsed times,
approximately 4 V / 0.08 A programmed values, approximately 0 V / 0 A no-load
measurements and Output OFF throughout. Disconnect completed normally and final
physical OP1/OP2 were confirmed OFF. No settings were changed, and no overwrite,
GUI freeze, logging error or residual process was observed. The instrument serial
number, exact filenames, row counts and unrecorded measurement precision are not
included.

## Milestone 9 - Independent Constant Current protocols

Added independent CH1 and CH2 Constant Current panels and per-channel state
machines: IDLE, STARTING, RUNNING, PAUSING, PAUSED, RESUMING, STOPPING,
COMPLETED and FAILED. Target current, voltage compliance, active duration and
measurement interval are validated before queue submission against QPX
programming grids, the conservative power envelope and separate Simulation/Real
Hardware project policy objects currently set to 10 V / 5 A / 50 W, 24 hours
maximum and 0.1-3600 second intervals.

Start confirms OFF, sets voltage then current and verifies read-back. A separate
queued final preflight rechecks session/OFF/settings immediately before one
Output On and ON confirmation. Fixed active-time monotonic sampling never overlaps
and counts skipped deadlines. Pause confirms Output Off and freezes active time;
Resume revalidates the same session, OFF state and programmed settings before a
single new Output On. Stop, completion, failures, coordinated disconnect and
application close make a bounded selected-channel Output-Off attempt and close
the protocol CSV. One channel's local protocol/file failure does not stop the
other.

Protocol samples use the Milestone 8 exclusive, incremental, flushed CSV writer.
When a same-channel logger is already active, the protocol reuses that single file
and writer through Pause/Resume; Stop or completion leaves the operator-started
logger active. A logger failure safely stops the affected protocol. The appended
`protocol_name` and `protocol_state` columns are blank for standalone rows,
preserving backward readability. Tests cover both channels, simultaneous operation, exact command order,
input rejection, initially-ON refusal, mismatch, Pause/Resume/Stop, active-time
accounting, failure cleanup, queue serialization and clean shutdown. The guarded
Real Hardware follow-up adds default-cancel Start/Resume authorization, stale and
duplicate request rejection, authoritative OFF cleanup, channel isolation and
single-writer CSV ownership. The implementation was first validated with injected
fake serial only, followed by the supervised resistor-load acceptance below.
At the Milestone 9 checkpoint, plotting, CV, sweeps and polarization remained
out of scope. See
[verification details](verification/milestone9.md).

The later supervised CH1/CH2 resistor-load acceptance passed. At 2.000 V
compliance and a 0.100 A target, stable CSV measurements were 0.939-0.949 V /
0.091-0.094 A on CH1 and 1.025-1.032 V / 0.102-0.104 A on CH2. Pause, Resume,
normal completion, continued CSV ownership and final Output Off all passed. A
brief 0.189-0.201 A first loaded sample was observed after startup/resume.
Electrolyser validation and unattended operation remain unapproved pending
assessment of that transient and later safety milestones.

## Milestone 10 - Responsive real-time plotting

Added independent CH1 and CH2 voltage/current plots driven only by the existing
Milestone 8 logger and Milestone 9 protocol sample signals. Plotting performs no
instrument polling, owns no backend and submits no queue commands. Manual logging
and Constant Current can update either plot independently or both simultaneously.

Each channel displays elapsed seconds, voltage in volts, current in amperes,
legends, channel identity, latest values, connection state and retained/received
sample counts. Pause, Resume, Stop and completion follow authoritative protocol
samples. Disconnect retains history; reconnect starts a separate line segment.
Clear affects only the selected display and never CSV data, logging, protocols or
outputs.

Display storage is bounded at 2,000 points per channel. Progressive decimation
retains representative history plus the first and latest points while the full
CSV remains unchanged. Repaint snapshots are coalesced at 10 Hz, no additional
thread is created, and plot update/rendering failure is isolated from recording
and output control. Voltage/current sweeps and later protocols remain deferred.
See [verification details](verification/milestone10.md).

## Milestone 11 - Independent Constant Voltage protocols

Added independent CH1 and CH2 Constant Voltage panels with target voltage,
current limit, duration, interval, Start, Pause, Resume and Stop. The implementation
specializes the Milestone 9 state machine, so queue serialization, fixed
active-time scheduling, missed-slot accounting, settings read-back, exactly-one
Output-On attempts and safety-priority confirmed Output-Off cleanup stay on the
same proven paths.

Constant Voltage was initially implemented and accepted in Simulation Mode.
Either channel or both can run, while one channel cannot run Constant Current and
Constant Voltage simultaneously. A channel-local failure does not stop the other
channel. Coordinated disconnect and application shutdown stop every active
protocol and await selected-channel OFF confirmation before proceeding.

Protocol samples use the existing Milestone 8 CSV writer and Milestone 10 plot
signal. An operator-started logger remains the single writer and receives
`constant_voltage` protocol rows through Pause/Resume/Stop; protocol completion
does not stop that logger. With no active logger, the protocol owns one exclusive
CSV and closes it deterministically. No extra measurement polling, backend path
or thread was added. See [verification details](verification/milestone11.md).

Windows Simulation Mode manual acceptance subsequently passed for independent
and simultaneous CH1/CH2 operation. A protocol without standalone logging
created one channel CSV; an already-active same-channel logger was reused;
Pause/Resume retained the same file; and a later new run created a new file.

The guarded Real Hardware follow-up reuses Milestone 9's explicit default-cancel
Start/Resume confirmation, exact QPX identity/session checks, fresh selected-OFF
and range/settings preflight, exactly-one Output-On attempt, safety-priority OFF
cleanup, authoritative OFF readback, stale-request rejection and single-writer
CSV ownership. Injected fake-serial tests passed for both channels, simultaneous
queue use and failure paths. Milestone 12 is recorded below.

A subsequent offline stabilization follow-up sets a 1.0-second minimum interval
for both Real Hardware Constant Current and Constant Voltage while preserving the
Simulation Mode 0.1-second minimum. Start and Resume issue Output On once,
require `*OPC? = 1`, wait through a non-blocking 1.0-second silent settling
period, require two matching ON responses approximately 100 ms apart, and finish
with an authoritative ON readback. Resume rebases its monotonic sample grid at
the preserved active elapsed time; it performs no zero-delay ordinary sample and
the first scheduled sample occurs one full interval later. Failure retains the
existing exactly-once stable OFF cleanup and never retries Output On.

Supervised Real Hardware resistor-load acceptance was subsequently completed on
CH1 and CH2 separately. Each channel used a verified 100-ohm load, 4.000 V /
0.080 A settings and a 1.0-second sample interval. Each passed one 30-second run
with multiple supervised Pause/Resume cycles plus a separate uninterrupted
10-second natural-completion run. Resume became available only after the
internal settling/confirmation stage; every Pause physically switched the
selected output OFF; Resume restored stable CV operation at approximately 4 V /
0.04 A; and paused time was excluded from active duration. CSV continuity and
real-time plots remained correct, while the unselected channel stayed OFF and
unchanged. Both channels were physically and authoritatively confirmed OFF at
the end. This is supervised resistor-load acceptance only: electrolyser use and
unattended operation remain unapproved.

## Milestone 12 - Automatic stepped Voltage Scan

Implemented independent CH1 and CH2 Voltage Scan panels for Simulation Mode.
Each accepts start/stop voltage, a signed voltage step, current limit, dwell time
per step and sample interval. Exact decimal planning supports ascending and
descending scans and includes a non-divisible endpoint exactly once without
drift, duplication or overshoot. Every point is validated against the active
QPX600DP range and programming grid, conservative hardware envelope and the
existing Simulation Mode project limits before channel ownership, file creation
or queue submission.

The axis-neutral `SteppedScanController` extends the Milestone 9–11 protocol
state machine rather than copying it. It owns dwell/step scheduling and accepts
an axis adapter; Milestone 12 supplies the voltage adapter, while Milestone 13
can supply a current adapter. Initial setpoint/current application, one Output-On
lifetime, Pause/Resume, safety cleanup, disconnect and shutdown all remain on the
existing shared paths. Each later voltage is set and read back as one atomic
worker operation. Queue latency is excluded from the following dwell, while
late sample slots are counted rather than replayed.

Pause confirms Output Off and freezes active time. Resume revalidates the current
step and continues its remaining dwell without skipping or duplicating a step.
Stop, completion, logging failure, connection loss and shutdown use the existing
bounded selected-channel cleanup. CH1 and CH2 can scan simultaneously; the one
shared worker serializes all operations, and only the selected channel's manual
controls and other protocols are disabled.

One scan either creates one channel CSV or reuses the active same-channel logger.
The backward-readable schema appends only `scan_name`, `scan_step_index`,
`scan_total_steps` and `scan_commanded_setpoint`; non-scan rows leave them blank.
Existing sample signals update the Milestone 10 plots without another poll,
writer or thread.

Focused Simulation/fake-backend tests cover exact sequence generation, range and
policy validation, command order, step timing, missed slots, Pause/Resume,
completion and failure cleanup, duplicate/stale actions, logging ownership,
plot reuse, two-channel isolation and simultaneous scans. The automated Windows
Simulation Mode smoke test completes simultaneous CH1/CH2 scans and verifies
clean disconnect and worker shutdown.

Manual Windows Simulation Mode acceptance subsequently passed. CH1 completed
an ascending `1.0 -> 1.5 -> 2.0 V` scan while CH2 simultaneously completed the
descending non-divisible sequence `2.0 -> 1.6 -> 1.2 -> 1.0 V`; the channels
remained isolated. Independent 3-second dwell timing and 0.25-second sampling,
Pause/Resume without skipped or duplicated steps, CSV scan metadata, live plots,
completion and final simulated Output Off all passed. A 3.50 A limit allowed
the documented simulator load model to reach each commanded voltage. That value
is Simulation Mode-only and is not approved for physical hardware.

Milestone 12 Real Hardware Voltage Scan is not implemented or enabled. Its panel
is visibly disabled outside Simulation Mode and reports that real-hardware
acceptance is pending. No COM port or physical instrument was accessed during
implementation or validation. See [verification details](verification/milestone12.md).
