# Milestone 6 verification - shared asynchronous instrument command queue

Date: 2026-09-24

## Scope

Milestone 6 evolves the existing simulation lifecycle thread into one shared
backend-owner worker. It does not introduce another owner thread. The worker:

- creates, connects, identifies, uses and disconnects one backend exclusively;
- serializes lifecycle and ordinary operations through one priority queue;
- preserves FIFO order within a priority using monotonic sequence numbers;
- keeps each submitted backend operation atomic once dispatch begins;
- returns results or exceptions with thread-safe command tickets;
- relays command completion and failure to the Qt main thread with queued signals;
- supports cancellation by request or logical cancellation key;
- reserves lifecycle/shutdown queue access even when regular capacity is full;
- rejects duplicate connection state, queue overflow and submissions after shutdown;
- performs bounded shutdown and owner-thread backend cleanup.

The GUI continues to construct only `SimulatedPowerSupply`. No physical-backend
factory, COM-port selection or serial transport is connected to the GUI.

## Automated coverage

The worker tests exercise:

- FIFO order for equal-priority commands;
- priority order and FIFO stability within each priority;
- one-at-a-time atomic execution on the single owner thread;
- concurrent submissions from two logical channels with per-channel ordering;
- isolated command exceptions followed by healthy work;
- targeted cancellation and cancelled-future reporting;
- disconnect after an in-flight operation with queued-work cancellation;
- bounded regular capacity with an always-available disconnect path;
- failed connection cleanup and retry with a fresh backend;
- duplicate-connect rejection without duplicate backend construction;
- bounded shutdown during a temporarily blocked fake backend;
- backend cleanup, post-shutdown rejection and no residual worker thread;
- non-blocking Qt submission and main-thread delivery of results and errors.

All tests use `SimulatedPowerSupply` or injected fake backends. Hardware-helper
tests retain their fake driver. No test enumerates or opens a real serial port.

## Validation results

Validation used Windows 11 Pro 24H2, AMD64, with 64-bit CPython 3.12.10 and the
project virtual environment. The final command results are recorded at the
Milestone 6 commit:

```text
python -m pytest -q
python -m compileall -q main.py qpx_electrolyser tests examples
python -m pip check
python main.py --smoke-test
git diff --check
```

The Simulation Mode smoke test uses Qt's offscreen platform and a temporary log
directory. It must show the window, process Qt events and stop the shared worker
cleanly without a `QThread: Destroyed while thread is still running` warning.

Results:

- complete suite: **172 passed in 3.10s**;
- `compileall`: exit code 0;
- `pip check`: `No broken requirements found.`;
- Simulation Mode GUI smoke test: exit code 0, version 0.6.0 window shown, Qt
  events processed and worker stopped cleanly;
- residual-thread or QThread-destroyed warnings: none;
- `git diff --check`: exit code 0.

## Deferred work

Milestone 6 adds no Milestone 7 channel controls, protocols, experiment logging,
plotting, Emergency Off GUI or Output On functionality. It does not integrate
the real serial backend into the GUI. Session/run generations, protocol engines,
telemetry fairness and emergency-output semantics remain later milestone work.
