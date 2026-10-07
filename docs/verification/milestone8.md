# Milestone 8 verification - independent continuous CSV logging

Date: 2026-10-07

## Scope and design

CH1 and CH2 each own a Qt-thread `ChannelLoggingController`, a single-shot
monotonic scheduler and at most one exclusive `CsvLogFile`. Either channel can
run alone or both can run simultaneously. A logger submits only an atomic
`read_channel` operation at `BACKGROUND` priority through the existing shared
Milestone 6 queue. It never receives a backend reference and never changes
voltage, current, range, protection or output state.

The fixed schema is:

```text
timestamp_iso,elapsed_seconds,channel,programmed_voltage_v,
programmed_current_limit_a,measured_voltage_v,measured_current_a,
voltage_range,output_state,connection_state,session_id,sample_status,error
```

UTC filenames contain only Windows-safe characters and are opened with exclusive
creation. The header is written once, every row is flushed immediately, and the
file closes on channel stop, disconnect, error or application shutdown. A
reconnected session receives a new increasing session ID; logging never resumes
automatically and a new Start creates a new file.

Scheduling uses `start_monotonic + sample_index * interval`, with a supported
interval of 0.1 through 3600 seconds. Only one read per logger can be pending.
Deadlines passed because of queue/backend latency increment the missed-sample
count and are skipped rather than submitted in a catch-up burst. The next future
deadline preserves cadence without accumulated drift.

## Automated verification

Simulator and fake-only tests cover:

- CH1-only, CH2-only and simultaneous logging;
- exact headers and values, exclusive no-overwrite creation, per-row flush and
  idempotent deterministic close;
- monotonic elapsed values, delayed/missed-slot counting and no overlapping
  requests;
- disconnect, explicit reconnect/new-file behavior and application shutdown;
- missing directory, permission/open failure and incremental disk/write failure;
- malformed queued results and backend/queue errors;
- affected-channel-only file failure while the sibling logger and shared
  connection remain healthy;
- all backend reads running on the one `InstrumentCommandWorker` and clean exit
  without residual threads or QThread-destroyed warnings.

Final validation commands:

```text
python -m pytest -q
python -m compileall -q main.py qpx_electrolyser tests examples
python -m pip check
python main.py --smoke-test
git diff --check
```

The native Windows Simulation Mode manual procedure additionally exercises
independent start/stop, simultaneous files, continued sibling logging, file
inspection, disconnect/no-auto-resume and close while active. Real Hardware mode
is not selected. No COM port is enumerated or opened, `hardware_check` is not
run, and no physical instrument command is sent.

Final results on Windows 11 Pro 24H2 with CPython 3.12.10 64-bit:

- complete suite: **250 passed in 10.15s**;
- `compileall`: exit code 0;
- `pip check`: `No broken requirements found.`;
- Simulation Mode smoke: exit code 0, both channel CSV files received samples,
  both files closed, simulator disconnected and worker stopped cleanly;
- rendered native window inspected with Simulation Mode visibly selected and
  independent CH1/CH2 logging controls present;
- residual worker threads or QThread-destroyed warnings: none;
- `git diff --check`: exit code 0.

The manual procedure is recorded in `tests/manual_smoke_test.md`. Native desktop
input automation was unavailable on this host, so independent button behavior,
simultaneous logging and shutdown were exercised by the Qt GUI integration test
and end-to-end smoke path, while the captured native Windows rendering was
inspected separately. No unperformed operator click sequence is claimed.

## Deferred work

Milestone 8 does not add protocols, automated sequences, pause/resume, live
plotting, Emergency Off, automatic reconnection or any Milestone 9+ behavior.
Real-instrument logging acceptance is not claimed by this Simulation Mode
milestone.
