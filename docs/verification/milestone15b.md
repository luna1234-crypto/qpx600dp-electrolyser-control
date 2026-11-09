# Milestone 15B Results / Scientific Plotter verification

Date: 2 November 2026. Target: optional offline analysis for the Aim-TTi
QPX600DP application. No COM port, Test Bridge or physical instrument was
enumerated or accessed.

## Isolation and scope

Results / Plotter is a non-modal optional window labelled **Offline analysis —
does not control the instrument**. The experiment-control application remains
the primary function and lazily imports the extension only when the Tools action
is selected. Failure to load or use the Plotter is caught locally.

The processing package is Qt-independent. The presentation package imports only
that processing API and Qt; it does not import or reference SerialTransport,
QPX600DPDriver, the connection/shared queue, ChannelEngine, protocol controllers,
outputs or safety transitions. Tests open and close the window while a sequence
still owns a paused channel and prove that no backend operation is submitted and
the protocol state is unchanged.

## Processing semantics

Only existing M15 stage-aware raw CSV files are accepted. Files are opened
read-only and never renamed, moved or deleted. Rows are grouped by sequence run
ID, channel and stage index. Only completed stages are eligible. A valid source
row must be an error-free `ok`, `RUNNING`, Output-ON row with finite available
measurements. Pause/Resume, OFF, handoff/settling, start/stop/cleanup and failed,
error or malformed rows are excluded and break continuity.

The final continuous segment must span the fixed full 30-second window. A short
segment is reported exactly as `Insufficient stable 30-second window`; a shorter
average is never substituted. One point per accepted stage contains Decimal
means and population SD for voltage/current, current density, sample count and
actual elapsed/timestamp bounds. Recorded active area is preferred; a finite
positive operator override is explicitly recorded as `overridden`.

## Presentation and exports

Multiple experiments remain loaded as independently visible and ordered curves.
Material, legend, colour, marker, size, line style/width and visibility are
editable. Qt-native previews support mean voltage versus measured current or
current density with optional voltage population-SD error bars. Plot data is
processed offline and adds no backend polling.

I–V and j–V plots retain separate Auto/Manual axis definitions. Manual X/Y
limits require finite increasing bounds; major-tick intervals must be positive
and remain within the bounded tick-count limit. Invalid edits are contained in
the Plotter status area and leave both processed data and the last valid preview
unchanged. Auto Range and Reset Axis Defaults act only on the selected plot.
Legend visibility remains a separate control from Best/Auto, four-corner and
Outside Right placement. Best/Auto selects the least occupied data corner;
outside placement reserves figure space so preview and exports do not clip it.

Combined processed CSV, PNG, SVG, PDF, plot-configuration JSON and processing
metadata JSON use sanitized exclusive filenames with numbered alternatives.
Metadata records the fixed window, exclusions, software version, timestamp,
sources and display settings. Numeric presentation formatting does not change
the processed Decimal values. Versioned configuration saves retain both plot
axes and legend placement while accepting the earlier configuration schema.
PNG/SVG/PDF rendering consumes those same visible limits, tick intervals and
legend placement. Source analysis has a documented finite row bound;
local worker shutdown is deterministic.

Focused tests cover exact window boundaries and known population SD, continuity
breaks, incomplete stages, multiple runs/channels/files, recorded and overridden
area, malformed/empty/incompatible inputs, style/config persistence, precise CSV
and all plot formats, filename collisions, exception containment, active-control
isolation and forbidden control-layer dependencies.

Final validation passed with 13 focused M15B tests and 522 full-suite tests,
plus package `compileall`, `pip check`, the offscreen Simulation Mode smoke test
and `git diff --check`. The simulator disconnected and its shared worker stopped
cleanly.

Old Test Bridge import, M14 summary import, GEIS parsing and iR correction remain
deferred. Stages shorter than 30 continuous valid seconds cannot produce accepted
scientific points. M15B does not change or extend M15 hardware acceptance and
does not approve unattended electrolyser operation.

## Scientific-axis and legend refinement — 9 November 2026

- I–V and j–V Auto/Manual axes, range/tick validation, plot-specific state,
  local-error containment, Auto Range and Reset Axis Defaults: passed.
- Best/Auto, four-corner and Outside Right legend placement, independent
  visibility, configuration round-trip and common preview/export rendering:
  passed.
- Focused M15B/M18B regressions: **32 passed**.
- Complete suite, run in two process-isolated Windows Qt groups: **569 passed**
  (**520 + 49 fake-serial GUI tests**).
- `compileall`, `pip check`, Simulation Mode smoke and `git diff --check`:
  passed; both simulated outputs finished OFF and the worker shut down cleanly.
