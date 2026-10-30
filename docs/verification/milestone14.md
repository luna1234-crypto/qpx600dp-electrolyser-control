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
and freezes active time as soon as the Pause request is accepted; the guarded
Output-Off transaction and any paused wall-clock time therefore do not advance
the dwell or final averaging window. Resume revalidates the same step, preserves
its consumed sample slots, and rebuilds the next active-time sample/boundary
deadline without skipping a sample exactly due at the Pause instant. A stale
pre-Pause timer callback cannot poll early or complete the step. Stop,
completion, file/backend failure, disconnect and shutdown use the existing
authoritative safety-priority Output-Off cleanup. Real Hardware uses the accepted
one-write, one-`*OPC?`, query-free settling, confirmation and authoritative
readback mechanism; there is no retry or reconnect.

## Data reduction

No additional measurement is requested. All scheduled samples remain in the raw
CSV. At a completed step, samples with active timestamps inside the final
averaging window are reduced to one summary row and flushed immediately. The raw
CSV retains its existing embedded summary row. Every M14 run additionally owns
one exclusive companion beside the raw file, named from the same raw run identity
with a `_polarisation_summary.csv` suffix (and a non-overwriting numeric
discriminator if that name already exists). Its stable header contains step,
commanded density/current, mean voltage, population voltage SD, mean
current/density, count and active-time bounds. The exact companion path is shown
in the panel. Pause/Resume keeps the same writer; a partial or stopped step has no
summary row. Creation failure rejects the run before backend mutation, while a
write failure fails the owning protocol and enters guarded cleanup. Both writers
are deterministically flushed and closed on every terminal path.

The bounded live polarisation plot consumes completed summary points only and
shows mean voltage versus commanded current density. Existing time plots and raw
data are unchanged.

## Validation status

### Simulation acceptance

- Decimal ascending, descending and inclusive non-divisible planning;
- density/area conversion and derived-current grid validation;
- averaging-window capacity/boundaries and population standard deviation;
- Pause before, at and inside the final averaging window, repeated Pause/Resume,
  stale boundary callbacks, partial Stop, completion and file-failure cleanup;
- CH1/CH2 independent and simultaneous operation;
- one raw and one exclusive companion writer per run, per-row flush,
  Pause/Resume continuity, deterministic closure and bounded plots;
- no extra polling and M9/M12/M13 regression coverage;
- fake-serial exact guarded start/cleanup order and channel isolation;
- simultaneous Simulation Mode automated smoke and clean worker shutdown.

Manual Simulation Mode acceptance passed. Companion
`*_polarisation_summary.csv` creation, immediate per-step flush, Pause/Resume
continuity and partial-step exclusion were verified together with raw CSV and
plot continuity.

### Supervised resistor-load hardware acceptance

Supervised functional acceptance passed independently on CH1 and CH2 using a
verified 100-ohm resistor load. Guarded Start, Pause, Resume, natural completion
and authoritative Output-Off cleanup passed. Raw CSV data, the independent
companion summary CSV, bounded plots and completed-step statistics remained
consistent. Both selected-channel cleanup and final physical output state were
confirmed OFF; the unselected channel remained isolated.

The Pause/Resume averaging-window defect described below was fixed and the same
workflow was subsequently verified on real hardware. A separate CH1 run using
20 seconds per step showed that the observed low-current deviation remained
stable and was not caused by insufficient dwell time. The official Aim-TTi Test
Bridge produced the same result with the same QPX600DP, resistor load and
settings. This independently supports acceptance of the application's command
path, active-time scheduling, data reduction and calculations.

The remaining approximately 20–40 mA deviation is assigned to QPX600DP
low-current regulation/readback behaviour or instrument calibration, not the
application. This is resistor-load functional acceptance only. It is not
quantitative low-current metrology, calibration evidence, or electrolyser
scientific acceptance.

Formal electrolyser polarisation requires externally verified current
measurement or a more suitable low-current instrument. Unattended electrolyser
operation is not approved. No instrument serial number is retained in this
record.

The earlier Real Hardware failure reporting `completed step has no samples in its
final averaging window` was traced to active time continuing through guarded
Output-Off confirmation. This was a scheduling/summary-construction failure, not
a CSV write failure. Summary construction errors are now reported separately;
`Polarisation summary CSV write failed` is reserved for writer/resource errors.
