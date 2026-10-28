# Milestone 14 verification — Steady-State Polarisation

## Scope and semantics

Milestone 14 is implemented for the Aim-TTi QPX600DP as two independent
power-supply polarisation controllers. It is not potentiostat control. The
controller subclasses the M13 current-density scan adapter and therefore reuses
the shared stepped-scan state machine, M11 guarded transitions, serialized queue,
single CSV writer and existing sample/time-plot flow.

Inputs are active area, start/end/signed increment current density, voltage
compliance, dwell per step, sampling interval and final averaging window. Decimal
planning converts `I = j × area`; all derived currents and their programming grid,
the voltage/range/project limits, total duration, and averaging sample capacity
are validated before ownership, file creation or mutation. Inclusive
non-divisible endpoints occur once without overshoot.

The output is enabled once and remains ON between setpoints. Pause confirms OFF
and freezes active time; Resume revalidates the same step and continues it. Stop,
completion, file/backend failure, disconnect and shutdown use the existing
authoritative safety-priority Output-Off cleanup. Real Hardware uses the accepted
one-write, one-`*OPC?`, query-free settling, confirmation and authoritative
readback mechanism; there is no retry or reconnect.

## Data reduction

No additional measurement is requested. All scheduled samples remain in the raw
CSV. At a completed step, samples with active timestamps inside the final
averaging window are reduced to one summary row and flushed immediately. The row
contains the existing step/commanded current/current-density fields plus mean
voltage, population voltage SD, mean current/density, sample count and active-time
bounds. A partial or stopped step has no summary row. A summary write failure
fails the owning protocol and enters guarded cleanup.

The bounded live polarisation plot consumes completed summary points only and
shows mean voltage versus commanded current density. Existing time plots and raw
data are unchanged.

## Validation status

- Decimal ascending, descending and inclusive non-divisible planning;
- density/area conversion and derived-current grid validation;
- averaging-window capacity/boundaries and population standard deviation;
- Pause/Resume continuity, partial Stop, completion and file-failure cleanup;
- CH1/CH2 independent and simultaneous operation;
- one raw CSV writer per channel, flushed summary rows and bounded plots;
- no extra polling and M9/M12/M13 regression coverage;
- fake-serial exact guarded start/cleanup order and channel isolation;
- simultaneous Simulation Mode automated smoke and clean worker shutdown.

Supervised physical polarisation acceptance remains pending. No COM port or real
instrument was accessed during implementation or automated validation.
