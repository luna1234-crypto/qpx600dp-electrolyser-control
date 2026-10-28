# Milestone 12 verification - Voltage Sweep / Staircase LSV

Date: 2026-10-28

## Implemented scope

CH1 and CH2 share the existing axis-neutral `SteppedScanController`. The
voltage adapter now implements a Voltage Sweep / Staircase LSV with start/end
voltage, signed step in mV, positive scan rate in mV/s, current limit, settling
time, measurements per point, sampling/logging interval and optional active
area. Milestone 13's current-axis adapter is unchanged.

All planning uses `Decimal`. Step duration is exactly
`abs(step_mV) / scan_rate_mV_per_s`; the signed step must follow scan direction.
Inclusive non-divisible endpoints are appended once without overshoot. Start,
end and step are checked against the selected QPX600DP programming grid before
channel ownership, file creation or a queued mutation.

Scheduled samples occur at interval multiples strictly before the step
boundary. Samples whose active step elapsed time is less than settling time are
retained as ordinary CSV rows but excluded from the representative point. The
configuration is rejected unless it can provide the requested number of
post-settling samples. At each boundary the most recent requested valid samples
are averaged into one representative mean voltage/current point. If active area
is supplied, mean current density is `mean_current / active_area`. A runtime
shortfall records an invalid point and fails through existing safe cleanup; it
never adds a compensating backend poll.

## Plot and CSV data flow

Existing voltage/current time plots remain fed by the scheduled sample signal.
Representative points feed a separate bounded I-V display, plus j-V when active
area is valid. Neither plot can access the backend.

The existing CSV column order remains intact. New fields are appended for scan
rate, signed mV step, derived duration, settling time, measurements per point,
measured current density, point validity and point-result index. The previously
appended active-area field is reused. Ordinary raw rows retain the scan timing
metadata; one `lsv_point` row records each representative result. Older rows and
readers remain backward-readable because new fields are trailing and blank when
not applicable.

## Safety and acceptance status

Initial settings, exactly-once Output On/Off, one `*OPC?`, the query-free
one-second Real Hardware transition settle, two state confirmations,
authoritative readback, session-generation checks and one priority cleanup
attempt remain inherited from the accepted Milestone 11 mechanism. Each later
voltage remains one serialized set/readback operation. Pause freezes active
time; Resume continues the same point and CSV without duplicate or skipped
steps. CH1/CH2 ownership and shared-queue serialization are unchanged.

The revised Staircase LSV workflow has now passed supervised 100-ohm functional
acceptance. CH1 completed an ascending `1 -> 4 V` sweep with valid
representative points; Pause/Resume preserved point and CSV continuity, and an
operator Stop terminated safely. CH2 completed a descending `4 -> 1 V` sweep.
No step was skipped or duplicated. The load response was approximately
`0.01 A` per volt, consistent with the nominal resistor. At the end, both GUI
states and both physical CH1/CH2 indicators were confirmed OFF.

This is supervised resistor-load functional acceptance only. It is not
metrology calibration, electrolyser validation or unattended-operation
approval. The instrument serial number is intentionally omitted.

## Verification

Automated coverage includes ascending/descending and non-divisible planning,
Decimal timing, programming-grid rejection, invalid sign/rate/settling/sample
capacity, settling exclusion, representative averaging/current density,
Pause/Resume step continuity, bounded I-V/j-V storage, append-only CSV fields,
simultaneous CH1/CH2 operation, no plot polling, Milestone 13 regressions and
guarded fake-serial ordering/cleanup.

The automated smoke runs simultaneous CH1/CH2 Staircase LSV scans in Simulation
Mode, reuses active channel loggers, checks inclusive completion and final OFF,
then continues through the unchanged Current Scan smoke and clean shutdown.
Manual inputs and observations are listed in `tests/manual_smoke_test.md`.

Final offline validation: focused LSV/CSV/fake-serial/M13 tests **83 passed**;
full pytest **462 passed**; `compileall`, `pip check`, simultaneous Simulation
Mode smoke and `git diff --check` passed with clean worker/QThread shutdown.
