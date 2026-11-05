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

- Real M15 raw CSVs may omit a redundant `stage_completed` row for a naturally
  completed final child stage. The analyser now accepts the unambiguous
  `completed` + COMPLETED + `Protocol duration completed` shape. It does not
  accept `Custom Sequence stopped by operator`; completed stages before Stop
  remain independently valid.
- Results / Plotter now has typed controls for curve visibility, colour, marker,
  marker size, line style, line width and order. Changes repaint immediately and
  persist through the existing configuration format.
- CH2 Constant Voltage Start no longer moves focus or scroll/navigation to the
  Sequence Editor. Snapshot updates preserve selected tabs, focus locality and
  scroll position. Established CH1/CH2 routing remains isolated.

## Automated evidence

- real-schema natural completion, Stop and Pause/Resume continuity;
- typed Plotter style editing and configuration round-trip;
- fixed-header/Emergency accessibility and seven-tab channel composition;
- CH2 CV route, selected-tab, focus and scroll stability;
- channel-local compact status and atomic measured power; and
- complete M9-M17 protocol, fake-serial, safety, logging and persistence
  regressions.

No COM port was enumerated or accessed and no physical instrument command was
sent. Manual DPI and small-screen acceptance follows the checklist in
`tests/manual_smoke_test.md`.

## Validation result — 2026-11-05

- focused M18/M15B and M9-M17 regressions: **137 passed**;
- complete pytest suite: **554 passed**;
- package, entrypoint and tests `compileall`: passed;
- `pip check`: no broken requirements;
- default-Simulation automated smoke: passed, including dual-channel protocols,
  CSV/plots, final Output Off, disconnect and clean worker shutdown; and
- `git diff --check`: passed.
