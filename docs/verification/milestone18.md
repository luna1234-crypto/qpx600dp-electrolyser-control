# Milestone 18 — UI Refinement

## Implemented boundary

The control application now keeps connection/mode/status, independent safety
limits and Emergency All-Off in a fixed, always-visible header. Existing CH1 and
CH2 widgets are reparented into reusable channel workspaces with compact status
and seven protocol tabs: Manual, CC, CV, Voltage Scan, Current Scan,
Polarisation and Custom Sequence. No controller, queue, serial command, logging
writer, sample flow or exactly-once Output transition was changed.

The compact strip shows authoritative Output state, same-readback measured V/I/P,
CV/CC availability, active protocol/state, scan or sequence stage and CSV path.
Plot groups are checkable/collapsible and the two channels can be resized with a
splitter. Mode-coloured header borders distinguish Simulation from Real Hardware;
Qt-native tabs and layouts retain keyboard navigation and DPI scaling.

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
- Persisted independent CH1/CH2 safety limits are active before connection, and
  Unified Safety starts expanded every session. Collapse remains visibility-only.
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
