# Milestone 9 verification - independent Constant Current protocols

Date: 2026-10-07

## Scope and state machine

CH1 and CH2 each own a `ConstantCurrentController` in the Qt main thread. The
explicit states are IDLE, STARTING, RUNNING, PAUSING, PAUSED, RESUMING, STOPPING,
COMPLETED and FAILED. Controllers own protocol time, request IDs, session
generation and one single-shot sample timer. They either own one protocol CSV or
append through an already-active same-channel logger. They never receive or call
the backend; every operation is submitted to the one Milestone 6 worker.

The implementation is intentionally Simulation Mode only. It adds no COM-port
enumeration, real-hardware protocol path, plotting, CV, sweep or polarization
protocol. The explicit Constant Current policy is 10 V, 5 A and 50 W, with a
maximum active duration of 24 hours and intervals from 0.1 through 3600 seconds.
Hardware programming grids, selected voltage range and the conservative power
envelope are also enforced before any command or file is submitted.

## Command sequences and timing

After GUI/session validation and selection of either an active logger or a newly
and exclusively created protocol CSV, the atomic Start backend sequence is:

```text
read_output_state(channel)
set_voltage(channel, compliance)
set_current(channel, target)
read_set_voltage(channel)
read_set_current(channel)
output_on(channel)
read_output_state(channel)
```

An initially ON output is rejected before any mutation. Read-back mismatch fails
Start. Output On occurs exactly once and is never retried silently. Each RUNNING
sample is one atomic `read_channel` operation at background priority:

```text
read_voltage_range, read_set_voltage, read_set_current,
read_voltage, read_current, read_output_state
```

Sampling deadlines use active monotonic time. At most one sample per channel is
pending; delayed slots are counted and skipped rather than replayed. Pause sends
`output_off` and confirms state OFF, then freezes active time. Resume confirms
the same session and OFF state, rereads both programmed settings, sends one
`output_on` and confirms ON. Stop/completion sends `output_off` and confirms OFF.
Cleanup uses safety priority and never modifies the sibling channel.

Disconnect and application close are coordinated in the GUI: active protocols
first enter STOPPING and receive a bounded cleanup opportunity before the shared
session closes. Communication loss that prevents confirmation produces a
prominent operator warning. File, parse, queue or channel-local protocol failure
stops only that channel while the shared session remains healthy.

## CSV integration

Protocol files reuse Milestone 8 `CsvLogFile`: exclusive Windows-safe creation,
one header, incremental rows, per-row flush and deterministic close. The schema
appends `protocol_name` and `protocol_state`. Standalone Milestone 8 rows leave
both fields blank, so existing behavior and earlier-column readers remain valid.
Constant Current sample rows use `constant_current` and `RUNNING`; terminal/event
rows record the applicable state and status/error.

An active same-channel Milestone 8 logger is a compatible single-writer sink, not
a conflicting owner. Constant Current Start remains enabled and uses that logger's
existing path; protocol rows and ordinary logging rows serialize on the Qt main
thread after their measurements serialize through the shared worker. Pause/Resume
continues on the same file. Protocol Stop/completion detaches without closing the
operator-started logger. Stopping or losing that logger during an active protocol,
including disk/write failure, fails the affected protocol and initiates bounded
Output-Off cleanup. Starting a second logger or writer remains prohibited.

## Verification coverage

Simulator/fake tests cover CH1-only, CH2-only, normal completion, simultaneous
protocols, exact Start order, shared-worker thread ownership, invalid/nonfinite/
out-of-policy input, initially ON refusal, setting mismatch, Pause/Resume/Stop,
paused-time exclusion, early Stop, malformed sample, queue rejection, unexpected
Output Off, CSV failure, no Output-On retry, Output-Off cleanup, channel isolation,
coordinated disconnect, direct connection loss warning and clean worker shutdown.
Coverage also verifies active-logger GUI coexistence, one shared writer/path,
Pause/Resume rows, logger persistence after Stop and completion, and safe protocol
failure after an injected shared-file error.

### PAUSED output-indicator regression

Manual Simulation Mode testing found that the simulator output was confirmed OFF
when CH1 entered `PAUSED`, while the manual channel indicator retained its last ON
sample. The Output-Off operation now returns a complete atomic channel read-back.
`PAUSED` is published only after that read-back confirms OFF, and the same
authoritative result updates the protocol snapshot, protocol indicator and manual
channel indicator. Regression coverage also injects an unconfirmed first OFF
attempt and proves that `PAUSED` is never entered in that path.

The Windows Simulation Mode smoke procedure is in `tests/manual_smoke_test.md`.
The automated native smoke starts both channel protocols through their GUI Start
buttons, waits for independent completion and closed protocol files, exercises
standalone CSV logging, disconnects and exits with a clean worker. The documented
manual procedure additionally covers CH1/CH2 normal completion, simultaneous
operation, Pause/Resume, early Stop, CSV inspection and close while active.

Final validation commands:

```text
python -m pytest -q
python -m compileall -q main.py qpx_electrolyser tests examples
python -m pip check
python main.py --smoke-test
git diff --check
```

No COM port, Test Bridge or physical QPX600DP was enumerated, opened, probed or
commanded during implementation or validation.

Final Windows results:

- complete suite: **277 passed in 15.37s**;
- `compileall`: exit code 0;
- `pip check`: `No broken requirements found.`;
- native Simulation Mode smoke: both active channel loggers remained available
  while both GUI-started protocols completed into the same per-channel files,
  both simulated outputs returned OFF, files closed explicitly, and disconnect
  and worker shutdown completed normally;
- manual visual inspection of the native render confirmed both protocol panels,
  inputs, terminal states, timing, measurements and missed-sample displays;
- Pause/Resume, paused-time exclusion, early Stop, coordinated disconnect and
  close-while-running behavior passed the GUI/controller integration procedure;
- residual application/worker threads or QThread-destroyed warnings: none;
- `git diff --check`: exit code 0.
