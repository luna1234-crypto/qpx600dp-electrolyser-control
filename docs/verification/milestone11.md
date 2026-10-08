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

## Real-hardware transaction diagnostics follow-up

The shared worker now attaches the active session ID, request ID, queue operation
name, priority and Output-Off cause to each atomic backend operation. The real
serial transport records structured `hardware_transaction` JSON objects for each
transmitted command and each exact framed response, using a monotonic timestamp.
`*IDN?` response content is always replaced by an explicit redaction marker, and
the general connection-ready message no longer contains identity details.

Every intentional `OP1 0` or `OP2 0` additionally records an
`output_off_intent` object before transmission. Protocol Pause records its pause
cause; Stop, completion and failure cleanup record the initiating terminal cause.
These diagnostics append to the existing rotating
`logs/application_YYYYMMDD.log`; they do not alter CSV data or its schema.

Fake-serial regression coverage reproduces authoritative ON confirmation, a
first ON sample and a second OFF sample. It proves the second `OP1?` response is
received before exactly one safety-priority cleanup `OP1 0`, validates complete
TX/RX alignment, confirms active logging remains query-only, and confirms a
delayed stale callback cannot submit additional output commands. The existing
fail-safe rule remains unchanged: one unexpected OFF fails the protocol and
requests Output Off, with no retry, debounce, ignored response or automatic
Output On.

## Bounded Output-Off confirmation follow-up

Diagnostics from a supervised Pause showed that the single `OP1 0` write was
followed about 37 ms later by `OP1? = 1`, while the channel still measured
3.991 V / 0.037 A. Command/response alignment was intact and no other operation
interleaved. A prior successful completion had reported OFF about 45 ms after
its Output-Off write. This established that one immediate query was not a
reliable confirmation boundary; it did not justify repeating the write or
treating voltage decay as proof of OFF.

All protocol Output-Off paths now use one shared safety-priority transaction:

- transmit `OP<N> 0` exactly once;
- query `OP<N>?` immediately, then at approximately 50 ms intervals while it
  remains ON;
- stop polling at a fixed one-second deadline, without issuing a query after
  that deadline;
- after the first explicit OFF response, perform one complete authoritative
  selected-channel readback and require its output field to remain OFF;
- on timeout or readback failure, retain the prominent physical-OFF-unconfirmed
  warning, close the Real Hardware session and require manual front-panel
  intervention.

The helper is used for Pause, Stop, natural completion, protocol failure,
coordinated disconnect and application shutdown. It does not retry Output Off
or Output On, and it does not affect the other channel. Deterministic
fake-serial tests cover immediate confirmation, one and multiple delayed ON
responses before OFF, timeout, exactly one Output-Off write, Pause near natural
completion, Stop, completion, disconnect, shutdown and stale callbacks. Real
hardware was not accessed for this follow-up; guarded Constant Voltage physical
acceptance remains pending.

Follow-up offline validation:

- focused protocol and fake-serial suite: **73 passed**;
- complete suite: **320 passed**;
- `compileall`: passed;
- `pip check`: no broken requirements;
- automated Simulation Mode smoke: passed with clean disconnect and worker
  shutdown;
- `git diff --check`: passed.

## Stable Output-Off confirmation follow-up

Subsequent supervised diagnostics showed that an immediate `OP<N>? = 0` was
not sufficient: failed Pause operations could return OFF within 7-8 ms and then
return ON about 41-42 ms after the single Output-Off write. Successful cases
remained OFF. The transaction stream was aligned, writes were complete, and no
other operation interleaved. The earlier single-OFF acceptance rule above is
therefore superseded by stable confirmation.

Each selected-channel shutdown attempt is now one atomic safety-priority queue
operation with this exact sequence:

1. transmit `OP<N> 0` exactly once;
2. query `*OPC?` exactly once and require the documented response `1`;
3. query `OP<N>?` immediately and then at approximately 50 ms intervals,
   resetting the consecutive-OFF count whenever ON is reported;
4. require two consecutive OFF responses within the original fixed one-second
   deadline;
5. perform one complete authoritative selected-channel readback and require its
   final `OP<N>?` response to remain OFF.

Voltage or current decay is never used as OFF evidence. A malformed or timed-out
`*OPC?`, confirmation timeout, or authoritative ON result fails the operation,
closes the Real Hardware session through the existing connection fail-safe, and
retains the prominent manual front-panel intervention warning.

The controller records that the Output-Off attempt was submitted before the
worker can transmit it. Pause failure is terminal rather than recursively
entering Stop cleanup, so no error callback can submit a second `OP<N> 0`.
Stopping an already-paused channel also completes from its authoritative cached
OFF state without another write. A fresh successful Output-On attempt starts a
new shutdown-attempt lifetime. This applies identically to CH1/CH2 Pause, Stop,
natural completion, failure cleanup, coordinated disconnect and shutdown.

Deterministic fake-serial coverage includes immediate stable OFF, the observed
OFF-ON rebound followed by stable OFF, repeated rebound through timeout,
authoritative readback rebound, malformed and timed-out `*OPC?`, Pause near
completion, stale callbacks, channel isolation, terminal cleanup and exact
Output-Off write counts on success and every failure path. No physical port or
instrument was accessed for this follow-up.

Stable-confirmation offline validation:

- focused driver, protocol and fake-serial suite: **121 passed**;
- complete suite: **327 passed**;
- `compileall`: passed;
- `pip check`: no broken requirements;
- automated Simulation Mode smoke: passed with clean disconnect and worker
  shutdown;
- `git diff --check`: passed.

## Real Hardware Resume stabilization and scheduling follow-up

Supervised evidence showed that ordinary 1.0-second Start runs completed, while
a Resume could return `OP1? = 1` and then report OFF in the former zero-delay
sample about 30 ms later, before voltage rose. The trace contained no Output-Off
write, stale callback, logger request or competing queue operation. The scheduler
also retained its pre-Pause slot index, so that zero-delay sample consumed the
old grid and skipped the next intended active-time slot.

Constant Current and Constant Voltage now require a measurement interval of at
least 1.0 second in Real Hardware mode. Simulation Mode retains its 0.1-second
minimum. Validation occurs before CSV creation, queue submission or instrument
mutation and reports the active mode's permitted interval range.

Real Hardware Start and Resume share this one atomic Output-On transaction after
their existing OFF/settings preflight:

1. transmit `OP<N> 1` exactly once;
2. query `*OPC?` exactly once and require `1`;
3. require two consecutive `OP<N>? = 1` responses approximately 50 ms apart,
   within one fixed 1.0-second stabilization window;
4. fail immediately on any explicit OFF response, without retrying Output On;
5. perform one complete authoritative channel readback and require its final
   `OP<N>?` to remain ON.

The authoritative readback is recorded through the existing CSV/plot flow, but
does not consume an ordinary sample slot or require measured voltage to have
already reached the target. Only after it succeeds does the protocol enter
`RUNNING`. Resume rebases a fresh monotonic sampling epoch at the preserved
active elapsed time; paused wall time remains excluded, and the first ordinary
sample is due one full configured interval later. Missed-slot accounting is
relative to that new epoch, so no pre-Pause slot is reused or skipped.

Every failure after the single Output-On attempt enters the existing
exactly-once stable Output-Off cleanup. A serial timeout invalidates framing and
closes the session before another command can safely be transmitted; that case
retains the physical-OFF-unconfirmed warning and records that cleanup was
attempted, without risking a write on an unaligned session. No automatic retry,
reconnect, duplicate writer, extra polling path or cross-channel command was
added. This follow-up was implemented and tested offline only; COM3 and the
physical instrument were not accessed.

Resume-stabilization offline validation:

- focused protocol and fake-serial suite: **82 passed**;
- complete suite: **345 passed**;
- `compileall`: passed;
- `pip check`: no broken requirements;
- automated Simulation Mode smoke: passed with final OFF cleanup, clean
  disconnect and worker shutdown;
- `git diff --check`: passed.
