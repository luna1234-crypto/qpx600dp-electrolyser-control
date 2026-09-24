# Milestone 4 verification

> Historical record: this checkpoint and its GUI evidence used the original
> CPX400DP identity. The later model correction is recorded separately in
> `model_correction_qpx600dp.md`.

Date: 2026-09-23. Version: 0.4.0. Starting checkpoint: `4238b2a` (milestone 3).
Environment: macOS, CPython 3.12.14, PySide6 / Qt 6.10.1, pytest 9.0.2.
No Windows desktop or physical CPX400DP was used.

## Results

`python -m pytest -q -p no:cacheprovider` completed with **80 passed**: all 75
milestone 1-3 tests plus 5 connection-worker and GUI tests.

| Area | Verified behaviour |
| --- | --- |
| GUI scope | Simulation Mode, Connect, Disconnect, status and instrument ID only |
| Responsiveness | Delayed simulator connection returns immediately to Qt; a queued GUI timer continues to run |
| Ownership | Connect, Identify and Disconnect all execute on the single `InstrumentConnectionWorker` thread |
| Failure cleanup | Identification failure invokes disconnect, releases the candidate and reports a retryable error |
| Retry | A fresh simulator connects successfully after the injected failure |
| State controls | Button and mode enablement follows DISCONNECTED, CONNECTING, CONNECTED, DISCONNECTING and ERROR |
| Shutdown | Join timeout is bounded; a temporarily blocked worker exits cleanly after release without unsafe termination |
| Launch | Both application entrypoints still run real Qt event loops and exit successfully |
| Visual review | Disconnected and connected offscreen renders were inspected; labels and simulator ID were readable |

The normal smoke launch returned exit code 0 and logged clean worker shutdown.
The temporary validation environment was outside the repository; no virtual
environment or generated test artifact was added to the project.

## Boundaries retained

The lifecycle worker has a small bounded FIFO and owns only connection operations.
It is the ownership seam for milestone 6, where request metadata, priorities,
cancellation, futures and all instrument calls will be added. Milestone 6 must
retain one backend owner rather than running this lifecycle worker beside a second
command worker.

No COM-port discovery, pyserial dependency, serial transport, CPX400DP driver,
setpoint/readback command, output control, emergency action, protocol, experiment
logger or configuration persistence was introduced. Real Windows, USB-driver and
hardware tests remain required in their later milestones.
