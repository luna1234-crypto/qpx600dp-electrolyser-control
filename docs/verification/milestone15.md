# Milestone 15 Custom Sequence verification

Date: 2 November 2026. Target: Aim-TTi QPX600DP application, Simulation Mode
only. No COM port, Test Bridge or physical instrument was enumerated or
accessed. M14 electrolyser scientific acceptance remains pending.

## Implemented scope

Each channel has an ordered editor supporting the recorded M15 stage set:
Constant Current, Constant Voltage, Voltage Sweep / Staircase LSV, Current Scan,
Steady-State Polarisation and Wait / Output-Off. Stages can be added, updated,
duplicated, removed and reordered before Start. There is no recipe persistence;
that remains M17.

The full sequence validates before ownership, file creation or mutation. The
orchestrator delegates active stages to the M9–M14 controllers and therefore
reuses their queue, atomic backend ownership, guarded transitions, timers,
cleanup, sampling and plots. Wait / Output-Off is a query-free monotonic timer
entered only after the prior stage has authoritatively completed OFF. A stage
advances once only after its controller publishes an authoritative COMPLETED
state; generation checks reject stale terminal callbacks.

One exclusive raw CSV belongs to the full channel run. Existing columns remain
in their original order and the following optional fields are appended:
`sequence_name`, `sequence_run_id`, `sequence_stage_index`,
`sequence_stage_total`, `sequence_stage_type` and `sequence_stage_state`.
Pause/Resume retains the writer and current stage. Stop closes the writer and
does not start a later stage. Existing samples and point results feed the
existing time, LSV and polarisation plots without extra polling.

## Automated Simulation acceptance

The acceptance test ran both plans simultaneously:

- CH1: Constant Current 0.10 A, 2 V compliance, 2 s; Wait / Output-Off 1 s;
  Constant Voltage 1.8 V, 0.20 A limit, 2 s.
- CH2: Constant Voltage 1.5 V, 0.20 A limit, 2 s; Constant Current 0.15 A,
  2 V compliance, 2 s.

CH1 was paused and resumed while CH2 continued. CSV inspection confirmed the
exact stage order, one completed-stage marker per stage, one run ID and one raw
file per channel. A separate early-Stop case confirmed that the next stage was
not started. Both final simulator output states were OFF. GUI integration also
confirmed editor operations, correct CH1 targeting and one explicit Start.

Validation completed with 495 pytest tests passing, package compileall passing,
`pip check` passing, the native/offscreen Simulation smoke passing through all
M9–M15 protocols on both channels, and `git diff --check` passing. The command
worker shut down cleanly without residual process or QThread warning.

## Pending acceptance and boundaries

The guarded Real Hardware follow-up enables Start only after a strong,
default-cancel dialog shows channel and the complete ordered stage list. After
confirmation, one atomic queued preflight freshly issues identity and complete
CH1/CH2 readbacks. It rejects a non-QPX600DP identity or selected output ON
before CSV creation or mutation. Session and request IDs reject stale results.
Every successful stage still delegates to its M11–M14 exactly-once guarded
Output transitions and authoritative cleanup; no serial state machine is
duplicated.

Fake serial covers CH1 and CH2 exact preflight order, cancellation with no
command, a selected output changing ON after connection, identity change,
two-stage advancement, Pause/Resume, Stop, writer failure, stale callbacks,
channel isolation and final OFF. Supervised physical M15 acceptance remains
pending. No automatic launch, reconnect, retry, restart, resume or subsequent
run is performed. M16 global emergency behavior and M17 saved recipes are not
implemented here. This milestone does not approve quantitative electrolyser
science or unattended electrolyser operation.
