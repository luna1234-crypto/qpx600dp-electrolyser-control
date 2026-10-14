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

## Standalone remote-output diagnostic follow-up

An additional one-shot CLI isolates the real QPX600DP serial transport and
driver from Qt, the GUI, protocol controllers, the shared queue, CSV logging,
plots and background threads. It accepts only an explicitly supplied port and
does not enumerate ports. Authorization is rejected before backend construction
unless both existing exact tokens are supplied:

```powershell
.\.venv\Scripts\python.exe -m qpx_electrolyser.instrument.remote_output_diagnostic --port COM3 --confirm-write WRITE-QPX600DP --confirm-output-on MONITOR-QPX600DP-OUTPUT
```

One process can perform only the fixed CH1 run: identify QPX600DP; require both
outputs OFF; verify existing CH1 settings and the 80 V range; program and verify
4.000 V / 0.080 A; transmit `OP1 1` once; require `*OPC? = 1` and two stable ON
responses; then take ten once-per-second samples containing only `OP1?`, `V1O?`
and `I1O?`. An OFF response stops monitoring immediately. The `finally` path
attempts `OP1 0` once and applies the same one-second, two-consecutive-OFF plus
authoritative-readback confirmation policy. There is no retry or second run.

Each invocation creates one exclusive timestamped raw diagnostic under `logs/`
by default. Every serial TX and exact framed RX is timestamped; the identity
response is redacted. Fake-serial tests cover success, spontaneous OFF, timeout,
identity mismatch, either initially-ON output, authorization rejection and exact
Output-On/Output-Off counts. A timeout deliberately invalidates framing, so the
one cleanup attempt can be rejected before transmission and is reported as
physical OFF unconfirmed rather than reopening or writing through an unaligned
session. This tool was not run against COM3 or any physical instrument.

## Isolated Pause/Resume diagnostic follow-up

The standalone CLI now has a separate guarded Pause/Resume mode. The original
basic diagnostic command and sequence remain unchanged. Authorization is rejected
before backend construction unless both mode-specific exact tokens and the
existing write token are present:

```powershell
.\.venv\Scripts\python.exe -m qpx_electrolyser.instrument.remote_output_diagnostic --port COM3 --pause-resume-diagnostic PAUSE-RESUME-QPX600DP --confirm-write WRITE-QPX600DP --confirm-pause-resume-output-on PAUSE-RESUME-QPX600DP-OUTPUT
```

The new path remains synchronous and uses only `SerialTransport` and
`QPX600DPDriver`. It has no Qt, GUI, controller, shared queue, CSV, plot,
background-thread or port-enumeration dependency. After identity, both-output-OFF,
80 V range and fixed 4.000 V / 0.080 A checks, it performs exactly one initial
`OP1 1`, `*OPC?`, stable-ON confirmation and three one-second samples. Pause
sends one `OP1 0`, confirms stable OFF with the authoritative readback, and
requires two further one-second `OP1? = 0` checks. Resume rechecks OP1 and both
setpoints before its single `OP1 1`, `*OPC?`, stable-ON confirmation and five
one-second samples. Final cleanup sends one `OP1 0` for the resumed lifetime and
requires stable OFF plus authoritative readback.

Output-lifetime state records each attempted ON and OFF write before transmission.
Consequently, an exception after a partial write cannot cause an automatic retry,
and a failed Pause confirmation cannot cause a second OFF write. Failures during
an active lifetime receive the one remaining cleanup attempt; failures after a
confirmed Pause remain OFF without an unpaired write. Raw diagnostics use the
separate `qpx600dp_pause_resume_*.log` prefix and redact identity details.

Offline fake-serial coverage verifies full success, Pause OFF failure, Resume
ON-to-OFF failure, pre-Resume setpoint mismatch, exact one ON/OFF write per
output lifetime, failure cleanup, token rejection, serial closure and absence of
automatic retry. This mode has not been run against COM3 or physical hardware.

## Direct-pyserial isolation follow-up

A separate one-shot module now implements the fixed CH1 diagnostic directly on
`pyserial`. It imports no project driver or transport and has no Qt, GUI,
controller, shared-queue, logging-engine, CSV, plotting or thread dependency.
It opens only the explicitly supplied port using 9600 baud, 8 data bits, no
parity, one stop bit and XON/XOFF; it never enumerates ports.

```powershell
.\.venv\Scripts\python.exe -m qpx_electrolyser.instrument.pyserial_output_diagnostic --port COM3 --confirm-write WRITE-QPX600DP --confirm-output-on MONITOR-QPX600DP-OUTPUT
```

Authorization is validated before report creation or serial construction. The
fixed run identifies QPX600DP, requires `OP1? = 0` and `OP2? = 0`, requires the
CH1 80 V range, programs and verifies 4.000 V / 0.080 A, sends `OP1 1` once,
requires `*OPC? = 1` plus two ON responses, and records ten once-per-second
`OP1?`, `V1O?`, `I1O?` samples. After any Output-On attempt, `finally` sends
`OP1 0` once and requires `*OPC? = 1` plus two OFF responses. It never retries
or automatically starts another run.

The exclusive `qpx600dp_direct_pyserial_*.log` report records UTC and monotonic
timestamps and exact byte representations for every TX and RX frame. The raw
identity frame is intentionally retained and the report must therefore be
handled as sensitive evidence. Fake-serial tests cover the exact configuration
and command order, success, spontaneous OFF, ON-confirmation failure, timeout,
identity mismatch, initially-ON rejection, authorization-before-open, guaranteed
single cleanup and prohibited-import isolation. This implementation and its
tests were completed offline; COM3 and physical hardware were not accessed.

## Pause-to-Resume quiescence safety gate

Real Hardware protocols now keep Resume disabled throughout Pause and stable
Output-Off confirmation, then for a further fixed 1.0-second monotonic
quiescence period after authoritative OFF readback succeeds. During that period
the protocol remains `PAUSED` and reports `Output OFF confirmed; waiting before
Resume.` The Qt timer is single-shot and does not block the GUI thread.

Resume becomes available only if the original connection session is still
current, the protocol remains `PAUSED`, authoritative output state is OFF, no
command is pending, and the complete quiescence interval has elapsed. Early or
repeated Resume requests return without changing state, logging ownership or
submitting a command. Stop, disconnect, failure, shutdown, session replacement
and protocol replacement cancel the pending timer, preventing a late callback
from enabling Resume.

After the gate opens, the existing Resume transaction is unchanged: fresh
OFF/setpoint preflight, explicit operator confirmation, one Output-On write,
stable-ON confirmation, and continuation in the same CSV. Simulation Mode keeps
its existing immediate post-Pause Resume behavior. This change was developed
and tested with fake serial only; no COM port or physical instrument was
accessed.
