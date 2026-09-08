# Proposed application and communication architecture

**Design only for future milestones.** Current code implements the entrypoints,
main-window placeholders, source paths and diagnostic logging. The empty package
boundaries reserve space without introducing mock drivers or half-built protocols.
This proposal follows the [verified command reference](cpx400dp_commands.md).

## Ownership and data flow

```mermaid
flowchart TD
    GUI["Qt GUI thread"] --> E1["ChannelEngine 1"]
    GUI --> E2["ChannelEngine 2"]
    GUI --> IC["InstrumentController"]
    E1 --> IC
    E2 --> IC
    IC --> Q["Bounded priority command queue"]
    Q --> W["Single communication worker"]
    W --> API["PowerSupplyInterface"]
    API --> SIM["SimulatedPowerSupply"]
    API --> CPX["CPX400DP driver"]
    CPX --> T["SerialTransport or future TCP transport"]
```

The selected backend is instantiated, connected, used and disconnected only by
the communication worker. There is one worker and one instrument session per
physical unit, shared by both channel engines. The simulator uses the same queue
and public interface. One writer per physical connection remains mandatory even
when transport changes.

| Component | Responsibilities | Excluded responsibilities |
| --- | --- | --- |
| MainWindow / reusable ChannelPanel | Compose widgets, collect validated requests, show states and measurements | No serial I/O, protocol loops or waiting on futures |
| InstrumentController | Submit typed actions, own session/channel generations, coordinate cancellation and faults | No direct transport access from calling threads |
| InstrumentCommandWorker | Execute bounded actions, maintain query/reply pairing, return results/errors | No protocol dwell, plotting or disk logging |
| CPX400DP | Validate channels, encode verified commands, parse values/units/status, expose capabilities | No GUI or experiment scheduling |
| SerialTransport | Open/close, ASCII framing, bounded read/write, terminators and transport errors | No knowledge of CC, CV, CSV or cell area |
| ChannelEngine, one per channel | Own state transitions, protocol time, local logger and measured results | No shared-port access |
| TestProtocol implementations | Generate planned steps and averaging rules | No GUI, serial handles or wall-clock sleeps |
| SafetyValidator | Validate full protocol against software and documented hardware capability before Start | No silent clamping or device writes |
| DataLogger, one per channel | Exclusive creation, CSV rows, metadata, flush/sync/close and file errors | No plot buffer or software diagnostic logging |

## Threads and scheduling

Use one Python communication thread with `queue.PriorityQueue`, plus one
interruptible engine thread per channel. The Qt GUI stays on the main thread;
typed Qt signals deliver immutable snapshots back to its slots. Signal delivery
must be queued across threads. Do not call a widget from any worker or wait for
I/O in a GUI callback.

Each engine owns its state machine and uses monotonic time for active test time
and dwell deadlines. It may wait on a condition/event or a bounded command future
in its own thread. CH1 waiting 30 seconds at a polarisation point consumes no
communication-worker time; CH2 continues to receive its own measurements and
setpoint changes. UTC-aware wall timestamps are recorded separately.

The public instrument abstraction will expose connect/disconnect/identify,
set_voltage/current, output_on/off, read_voltage/current and is_connected.
Readback output state, capabilities and status are also needed. is_connected
means session health, not proof of recent communication or output safety.
Drivers receive an injected transport; engines receive controller access, never
the driver's serial object. Store capabilities centrally in a frozen dataclass
populated from the manual's evidence, not repeated numeric literals in widgets.

Connection work in milestone 4 must already run off the GUI thread. Milestone 6
will formalise and test the shared action queue before two-channel controls are
introduced. No interim direct-serial GUI callbacks are acceptable.

## Transaction, cancellation and fairness rules

Every queued action carries a request ID, session generation, channel/run
generation where applicable, priority, FIFO sequence number and completion future.
Bound queued work and in-flight requests per channel; reject overload visibly.
Coalesce redundant display-only refresh requests, never silently discard required
experiment samples. Fair dispatch between channels prevents telemetry starvation.

A query write and its complete reply form one indivisible transport transaction.
Consume replies from `IFLOCK` and `IFUNLOCK` as well as `?` commands. Only one
owner reads destructive status registers, distributing cached timestamped events
to both engines. Keep transactions short. V/I readback pairs should be adjacent
when practical and carry acquisition timestamps; do not claim simultaneous ADC
sampling. Normal setters should be checked using readback and error status at
appropriate transaction boundaries before an engine assumes success.

Before dispatching each write, the worker atomically validates its generation
and reserves the action as in-flight under a short lock. Never hold that lock
during I/O. A cancellation invalidates all older affected actions; late replies
may complete their transport transaction but cannot revive an old engine run.
Check cancellation between setup writes, especially before output ON. A long
multi-command start sequence must not prevent OFF dispatch at short boundaries.

Suggested priority order: global emergency OFF; channel OFF/cancellation;
connection/fault recovery; scheduled setpoints/measurements; optional UI refresh.
Priority cannot interrupt bytes already on the wire or a command inside the
instrument's sequential parser. Avoid voltage-with-verify commands (up to 5 s)
in normal operation; use host-side settling waits. Bound both read and write
timeouts, including the case where flow control prevents a write from finishing.

An ordinary validation error fails that request without damaging healthy queued
work. Timeout, partial response or malformed framing creates an uncertain
transaction boundary: invalidate the shared session, stop both engines and
reject stale queued commands. Do not let a late reply become the next query's
answer, blindly retry an output-on write or auto-resume a timed experiment.
Recovery must explicitly re-establish framing and ownership with outputs off.

## State, pause, stop and shutdown

Planned per-channel enum: DISCONNECTED, IDLE, READY, RUNNING, PAUSED, STOPPING,
COMPLETED, ERROR. A separate outcome records COMPLETED, STOPPED, EMERGENCY or
ERROR; emergency remains visibly latched. Only an engine changes its own state.

| Request/event | Required transition or result |
| --- | --- |
| Connect and complete shared initialisation | DISCONNECTED to IDLE |
| Validate full protocol, reserve files and obtain Start confirmation | IDLE to READY |
| Apply verified initial setpoints and enable selected output | READY to RUNNING |
| Pause | Invalidate pending channel actions; confirm that output OFF succeeded; RUNNING to PAUSED |
| Resume | Restore validated previous setpoints and output state; PAUSED to RUNNING after confirmation |
| Stop | Invalidate channel actions; RUNNING/PAUSED to STOPPING; send channel OFF; finalise its data; return IDLE with STOPPED outcome |
| Finish | Confirm channel OFF and finalise data, then COMPLETED |
| Failed I/O, failed OFF or failed required data write | ERROR, with physical output unknown unless verified |
| Start while running / resume while not paused | Controlled rejection; no new worker or duplicate experiment |

**Pause means output OFF and active protocol time frozen.** Record pause/resume
events and continue appropriately paced monitoring. Preserve wall time separately
from active elapsed time. A policy enum can permit other pause behaviour later;
no alternative is silently enabled. WAIT and OUTPUT_OFF custom steps will need
explicit output semantics before milestone 15 validation is accepted.

Channel Stop never emits `OPALL`, changes CONFIG, resets the instrument or
invalidates the other channel's generation. Global emergency first latches the
controller and invalidates both runs, then requests `OPALL 0`, explicit `OP1 0`
and `OP2 0` as appropriate, and reads back both states where communication permits.
An OFF failure for one output must not prevent attempting the other. Retain data
and display a conspicuous unresolved fault if shutdown cannot be verified.

Disconnect and application exit coordinate a deliberate session shutdown:
invalidate work, request/confirm both outputs OFF, finalise loggers, release the
interface lock and close the port, then join workers. The GUI remains responsive
while waiting and reports any unconfirmed output. Do not rely on destructor
order, daemon-thread termination, LOCAL or `*RST` for shutdown.

The manual documents no communications watchdog. Software cannot guarantee OFF
after USB loss, OS crash or loss of PC power; lab-level shutdown provisions must
cover that gap. Session loss affects both channels; a channel-specific file or
protocol fault should stop only its own output if the transport is still healthy.

## Protocols, safety, data and plotting

Introduce protocols in the requested order: CC, time plots, CV, staircase voltage
sweep, current sweep, polarisation, custom sequence. Use explicit V, A, s, cm2
and A/cm2 fields internally. Convert `I = j * area` at the boundary, reject
non-finite values, and validate every step before arming. Scheduling and final
polarisation-window averaging must be testable with a fake monotonic clock.

Apply basic capability/output safeguards before the first real manual control;
milestone 16 completes the safety system, rather than postponing every safeguard
until then. Validate voltage/current, their programming grids and worst-case
setpoint/compliance power. The 420 W figure is a power-envelope bound, not a
60 V by 20 A rectangle. A proposed initial software power budget of at most
400 W per channel is conservative pending envelope commissioning; it is a
software policy, not a replacement hardware rating. Keep user limits lower as
required for each experiment. Recheck measured power and trip events while running.

Each channel writes rows continuously through its own engine/logger. Reserve
unique files using exclusive creation and sanitised Windows-safe names, including
reserved names. Write companion metadata before enabling the output. Flush each
row or a small bounded batch; add periodic fsync with a documented durability
interval. Flushing is not a guarantee against sudden power loss. Surface file
errors, command channel OFF independently of disk cleanup, and mark ERROR in the
CSV/metadata if a write remains possible. Never invent a successful error row.

Retain full data on disk. Plotting receives bounded snapshots through signals,
with at most 5,000-20,000 retained display points per channel. Pause-plot affects
only display refresh. Engines and loggers continue. Do not enqueue one unbounded
Qt signal per acquisition if the GUI cannot keep up; report overload and use
bounded/coalesced display snapshots. Polarisation summary writes occur at each
completed step and use only the measured final averaging window.

## Milestone boundaries and acceptance

Milestone 1: launch and verify the offline foundation. Milestone 2: record verified
commands and this architecture; rerun milestone 1 regression and stop. No real
connection, simulator or queue implementation is authorised in this delivery.
Later concurrency tests must cover cancellation just before ON, stale replies,
interleaved queries, independent channel shutdown and in-flight emergency latency.
Later data tests must cover unwritable files, interruption and bounded memory.
Windows hardware and 24-hour accelerated simulator acceptance belong to their
specified later milestones; they are not claimed by the current eight tests.
