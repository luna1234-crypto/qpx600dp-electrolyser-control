# Milestone 16 — Unified Safety Features

## Implemented boundary

Milestone 16 adds one shared `SafetyValidator` instance to the GUI composition.
It owns independent, session-local CH1/CH2 `SafetyLimits` for maximum voltage,
current, power and current density. Each effective maximum is the strictest of
the operator value, established project policy and verified QPX600DP
capability/resolution. Validation rejects unsupported grids and unsafe operating
points without rounding or clamping.

The same validator is injected into manual controls, Constant Current, Constant
Voltage, Staircase LSV, Current Scan, Steady-State Polarisation and every Custom
Sequence child controller. Validation finishes before channel ownership, CSV
creation, setpoint mutation or Output On. Density limits apply whenever an
active area is available. A connected instrument's safe 0 A baseline remains
readable/validatable; every protocol still requires its established positive,
grid-supported current.

Real Hardware Start/Resume remains default-Cancel. The confirmation includes the
channel, protocol/sequence settings, voltage range, duration or step plan,
available area/density, effective safety limits and exact/current CSV target.
Existing identity, session-generation, OFF-state, setpoint readback, exactly-once
Output On/Off and one-second guarded settling behavior is unchanged.

## Emergency All-Off

The red global action:

1. rejects duplicate clicks while active;
2. cancels pending manual and standalone logger work and closes logger writers;
3. asks every owning CH1/CH2 protocol to use its existing authoritative cleanup;
4. waits boundedly for those channel-local cleanups;
5. submits one safety-priority atomic verification of both channels;
6. writes Output Off at most once for a channel still reporting ON; and
7. reports CH1 and CH2 authoritative results separately.

An idle/OFF channel receives no unnecessary output write. A partial failure is
never presented as success. Any unconfirmed channel closes the shared session
and displays a prominent instruction to use the physical front panel. Normal
Stop/failure remains channel-local; only Emergency All-Off, shared connection
loss and application shutdown may affect both channels.

`logs/safety_events.csv` is append-only and flushed per event. Its stable fields
are UTC timestamp, channel, cause, requested action and `OFF_CONFIRMED` or
`OFF_UNCONFIRMED`. QPX600DP identity serial data is redacted. M15B remains an
optional offline reader and has no reference to the validator, queue, emergency
controller or instrument interfaces.

## Automated evidence

- boundary, stricter-limit, power, density and voltage/current-grid rejection;
- independent CH1/CH2 policies and safe zero baseline;
- no command, ownership or CSV creation after failed validation;
- Emergency All-Off with idle, one active, both active and partial-failure paths;
- exactly one required Output-Off write and no duplicate on repeated clicks;
- independent per-channel confirmation and disconnect on unconfirmed OFF;
- protocol cleanup reuse without a second output write;
- append/flush schema and serial-number redaction;
- preserved M9-M15 protocol and M15B offline-analysis regressions.

## Validation result — 2026-11-04

- focused safety and FakeSerial tests: passed;
- complete pytest suite: **536 passed**;
- package, application and test `compileall`: passed;
- `pip check`: no broken requirements;
- disconnected/default Simulation smoke, including simultaneous CH1/CH2 M9-M15
  flows and clean worker shutdown: passed;
- `git diff --check`: passed.

No COM port or physical instrument was accessed. Supervised physical Emergency
All-Off acceptance, quantitative electrolyser science and unattended operation
remain pending.
