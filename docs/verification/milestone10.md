# Milestone 10 verification - responsive real-time plotting

Date: 2026-10-08

## Scope and data flow

CH1 and CH2 each own a Qt-thread `ChannelPlotController`, bounded
`BoundedPlotSeries` and `ChannelPlotPanel`. The controller consumes the existing
`sample_received` signals from the channel's Milestone 8 logger and Milestone 9
Constant Current controller. It does not poll, submit queue work, receive a
backend reference or create a thread. All instrument readings therefore remain
the original atomic operations serialized by the Milestone 6 shared worker.

Each plot shows measured voltage against a left V axis and measured current
against a right A axis over monotonic elapsed seconds. The canvas includes a
legend, channel identity, latest values, connection status and displayed/received
sample counts. Voltage and current use distinct colors and line styles. Qt repaint
snapshots are coalesced to at most 10 Hz so repeated samples cannot create an
unbounded repaint backlog.

## Bounded display and lifecycle behavior

Each channel retains no more than 2,000 display points. On reaching the bound,
progressive decimation reduces older display data while retaining representative
history, the first point and the newest point. The total received count continues
to increase. This affects only the display: CSV creation, schema, rows, precision,
flush cadence and deterministic close behavior are unchanged.

Pause consumes the protocol's authoritative Output-Off readback and then stops
protocol-only plot updates. Resume continues the same elapsed plot timeline. If
an independently started logger remains active during Pause, its existing sample
stream continues to update the plot and CSV. Stop and completion add their
authoritative readbacks without creating extra polling. Disconnect retains
history and marks the plot disconnected. Reconnect uses a new session ID, so the
next samples draw as a separate segment rather than a misleading line across the
connection gap.

`Clear plot` resets only the selected channel's display origin and bounded
history. It does not close or truncate CSV, stop a logger/protocol, submit a
command or change an output. Malformed plot input is reported locally and a paint
failure renders an unavailable message; neither failure path calls logging,
protocol, queue or output-control APIs.

## Automated coverage

Simulator/Qt tests cover:

- independent CH1 and CH2 values and simultaneous logging/protocol updates;
- monotonic elapsed coordinates and channel/session identity;
- Pause, Resume, Stop and normal completion;
- disconnect/reconnect history with separate session segments;
- 20,000 input samples with the display remaining within its configured bound;
- first/latest preservation and progressive display stride;
- clear/reset without any CSV change;
- active CSV persistence, shared protocol/logger files and full row readability;
- malformed plot input isolated from an active logger and connection;
- native canvas rendering, latest-value labels and clean worker/application exit.

The complete suite passed with **292 tests**. No test opens or enumerates a real
port, and the plotting tests use only `SimulatedPowerSupply` plus constructed
readback objects.

## Windows Simulation Mode smoke

The native Windows smoke launch used version 0.10.0 in Simulation Mode. Both
channel loggers and both short Constant Current protocols ran together, updated
their bounded plots, completed with simulated outputs OFF, closed their CSVs,
disconnected and stopped the shared worker cleanly. The captured native window
was scrolled to the plotting area and visually inspected: both channel canvases,
elapsed-time axis, V/A axes, legends, latest values, connection/sample status and
independent Clear buttons rendered legibly. No QThread-destroyed warning or
residual smoke-test process was observed.

The operator procedure in `tests/manual_smoke_test.md` covers live logging,
protocol-only Pause/Resume, simultaneous plots, single-channel clear, retained
history across disconnect/reconnect, CSV inspection and shutdown. This milestone
was developed and validated in Simulation Mode only. No COM port or physical
instrument was enumerated, opened, probed or commanded.

Validation commands:

```text
python -m pytest -q
python -m compileall -q main.py qpx_electrolyser tests examples
python -m pip check
python main.py --smoke-test --screenshot <temporary-path>
git diff --check
```

Results:

- complete suite: **292 passed in 19.02s**;
- `compileall`: exit code 0;
- `pip check`: no broken requirements;
- native Simulation Mode smoke and plotted-window render: passed;
- `git diff --check`: exit code 0.

Voltage/current sweeps, CV, polarization and later protocol work are not part of
Milestone 10.
