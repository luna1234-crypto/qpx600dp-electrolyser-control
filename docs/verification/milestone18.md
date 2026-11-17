# Milestone 18 — UI Refinement

## Final operator dashboard boundary

The control application keeps application identity, connection/mode/status and
Emergency All-Off in a fixed command bar. Permanent CH1/CH2 cards remain visible
while a left rail opens Dashboard, Manual Control, Experiments, Live Data, Files
& Templates and Settings. Experiments displays exactly one selected
channel/editor. Existing widgets are reparented; no controller, queue, serial
command, logging writer, sample flow or exactly-once Output transition changed.

The cards show authoritative Output state, same-readback measured V/I/P, active
protocol/state, scan or sequence stage and CSV path. Safety enforcement loads
before connection, but its detailed editor starts hidden behind Edit Safety.
CH1/CH2 time graphs are vertical, splitter-resizable, share time zoom and retain
independent Y axes; one active graph may occupy the full area. Qt-native controls
retain keyboard navigation and DPI scaling.

## Confirmed defect fixes

- CH2 Constant Voltage Start no longer moves focus or scroll/navigation to the
  Sequence Editor. Snapshot updates preserve selected tabs, focus locality and
  scroll position. Established CH1/CH2 routing remains isolated.

## Automated evidence

- fixed-header/Emergency accessibility and seven-tab channel composition;
- CH2 CV route, selected-tab, focus and scroll stability;
- channel-local compact status and atomic measured power; and
- complete M9-M17 protocol, fake-serial, safety, logging and persistence
  regressions.

No COM port was enumerated or accessed and no physical instrument command was
sent. Manual DPI and small-screen acceptance follows the checklist in
`tests/manual_smoke_test.md`.

## M18B final refinement

- Connection status and Emergency All-Off stay fixed while Unified Safety and
  channel content use a draggable vertical splitter with persisted sizes and
  Collapse/Restore actions.
- Operator terminology is Chronopotentiometry (CP) and Chronoamperometry (CA),
  while internal/CSV IDs remain unchanged. Compliance fields read “Voltage
  limit / compliance (V)” and the UI identifies two-electrode power-supply
  control rather than potentiostat control.
- Custom Sequence templates are versioned JSON `*.qpxseq.txt` documents with
  notes, stages, transition policy, settling, interval and area values. Import
  is previewed and validated offline and cannot connect or start an output.
- Each new logger/protocol/scan/sequence file is chosen before mutation and
  created exclusively; cancel submits zero commands, Resume keeps the same
  writer and companion files derive from the selected base name.
- Light/Dark themes apply immediately and persist.

Focused coverage includes settings migration/splitter/theme restore, template
round-trip, CSV cancellation/exclusive naming and unchanged M9–M18 controller
regressions.

## M18B validation result — 2026-11-09

- focused M18B/Custom Sequence/settings/UI regressions: **62 passed**;
- complete pytest suite: **559 passed**;
- package, entrypoint and tests `compileall`: passed;
- `pip check`: no broken requirements;
- default-Simulation automated smoke: passed with both outputs OFF, clean
  disconnect and worker shutdown; and
- `git diff --check`: passed.

## Validation result — 2026-11-05

- focused M18 and M9-M17 regressions: **137 passed**;
- complete pytest suite: **554 passed**;
- package, entrypoint and tests `compileall`: passed;
- `pip check`: no broken requirements;
- default-Simulation automated smoke: passed, including dual-channel protocols,
  CSV/plots, final Output Off, disconnect and clean worker shutdown; and
- `git diff --check`: passed.

## Final M18 control-application follow-up — 11 November 2026

- Removed the in-application offline scientific post-processing window, its
  processed exports/configuration and dedicated code/tests. Existing user files
  are untouched; unknown legacy settings keys remain tolerated.
- Preserved the complete raw CSV schema, operator-selected exclusive naming,
  Pause/Resume writer reuse and independent CH1/CH2 logging.
- Closed selectors and numeric spin boxes ignore wheel edits and redirect wheel
  motion to the containing panel. Keyboard, typing, buttons and open dropdowns
  remain deliberate input paths.
- All live time, Staircase LSV and polarisation plots use their existing bounded
  samples for right-drag rectangular zoom and nearest-point tooltips. Auto Range /
  Reset Zoom changes display state only and adds no polling.
- Persisted independent CH1/CH2 safety limits are active before connection. The
  detailed editor starts hidden and explicit disclosure/collapse is visibility-only.
- Scientific post-processing is explicitly external; this application remains
  responsible for control, safety, raw logging and live plotting.

Validation:

- focused removal/input/plot/persistence/raw-CSV regressions: passed;
- complete pytest suite: **546 passed**;
- package, entrypoint and tests `compileall`: passed;
- `pip check`: no broken requirements;
- default-Simulation automated smoke: passed with both outputs OFF, clean
  disconnect and worker shutdown; and
- `git diff --check`: passed.

## Final dashboard regression scope — 11 November 2026

- six-page navigation and exactly one experiment editor;
- fixed command bar/Emergency and permanent independent CH1/CH2 cards;
- Windows-small-screen/DPI-aware geometry, keyboard navigation and wheel guards;
- hidden-but-active persisted Safety and explicit Edit Safety disclosure;
- CH1-above-CH2 vertical Live Data layout, synchronised time zoom, independent
  Y views, local maximise/restore and existing point tooltips;
- no Results / Scientific Plotter entry or analysis subsystem; unknown legacy
  settings remain startup-safe; and
- unchanged raw CSV fields, protocol routes and Simulation behavior.

## Mode and live-data readability follow-up

- Simulation and Real Hardware are whole-card native radio choices with explicit
  check marks, selected-state text, accent borders, keyboard focus and disabled
  rendering. Simulation uses blue; normal Real Hardware uses amber plus a
  Hardware badge. Red remains reserved for faults and Emergency All-Off.
- Selecting Real Hardware retains the existing default-cancel warning and does
  not connect or send commands. Tests click outside the indicator/label and
  confirm the entire card remains the deliberate selection target.
- CH1/CH2 time plots draw six sparse major ticks on elapsed time, voltage and
  current axes with compact `s`, `V` and `A` labels. Light and Dark grid palettes
  are low contrast and remain behind blue voltage/orange current traces.
- Rendering tests exercise Light/Dark themes and 100%, 125% and 150%-equivalent
  widget sizes without changing sampling, polling, zoom, tooltip or CSV paths.

## Recorded Results and paired CSV round-trip — 17 November 2026

- Every run creates a matching exclusive `<base>_raw.csv` and
  `<base>_readable.csv` pair. Pause/Resume continues the same pair.
- Raw CSV remains the complete archival record, including the established
  traceability, protocol, stage, status and error fields.
- Readable CSV contains exactly numeric elapsed time, measured voltage and
  measured current for convenient Excel or external Flowcell Plotter use. No
  separate Flowcell Plotter compatibility acceptance is claimed.
- Recorded Results is an optional manual, offline import workflow. Protocol-aware
  Raw import supports CP, CA, scans, Polarisation and Custom Sequence metadata.
  Compatible same-base Polarisation summary companions provide completed-step
  means with recorded voltage-SD error bars; Custom Sequence stage metadata
  provides time-plot boundaries.
- Missing metadata or a missing/incompatible companion leaves valid basic plots
  available with a local explanation. Exact three-column Readable imports offer
  generic Time and V–I plots only.
- Import owns no serial transport, instrument driver, shared queue, protocol
  controller or output-control path and remains isolated from live operation.

Automated validation for the recorded-data round-trip implementation:

- focused Recorded Results suite: **18 passed**;
- complete pytest suite: **587 passed**;
- package, entrypoint and tests `compileall`: passed; and
- `git diff --check`: passed.

These results verify the automated round-trip and isolation behavior. A full
manual Recorded Results acceptance and Flowcell Plotter compatibility validation
have not been recorded.
