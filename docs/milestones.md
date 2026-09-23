# Milestone verification log

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
