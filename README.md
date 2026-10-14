# QPX600DP Electrolyser

Python 3.12 / PySide6 desktop project for an Aim-TTi QPX600DP dual-output
power supply. Milestones 1-4 were originally completed under a CPX400DP model
assumption. Version 0.11.0 preserves those historical records, the Milestone 5
QPX600DP serial backend and completed two-channel standard-load Windows
validation, the Milestone 6 shared
asynchronous command queue, Milestone 7 dual-channel manual controls with a
guarded explicit-port hardware connection, Milestone 8 independent CSV logging,
Milestone 9 independent Constant Current protocols with an offline-tested,
guarded Real Hardware follow-up, Milestone 10 bounded real-time plots, and
Milestone 11 independent Simulation Mode Constant Voltage protocols.

Simulation Mode remains the default. A deliberately selected Real Hardware mode
accepts only a manually entered COM name and never enumerates ports. The one
shared backend-owner worker constructs `SerialTransport` and `QPX600DPDriver`
only after explicit Real Hardware Connect. Real Hardware Constant Current Start
and Resume each require a detailed, default-cancel confirmation and fresh queued
OFF/settings preflight. Fake-serial validation and supervised two-channel
resistor-load functional acceptance have passed. Electrolyser and unattended use
remain unapproved pending transient assessment and later safety milestones.
Physical validation includes identification, read-only queries, guarded setpoint
read-back, monitored standard-load Output-On/Off tests on both channels, and
operator-supervised real-hardware GUI acceptance for both channel panels.
Milestone 8 real-hardware read-only CSV acceptance also passed with no load and
both outputs OFF. Electrolyser validation remains pending across the completed
Milestone 5, 7, 8 and resistor-load Milestone 9 acceptance work.

## Windows installation

Extract the project into a QPX600DP working directory, for example:

```text
C:\Users\mo113\OneDrive - Imperial College London\Codex project\QPX 600 DP
```

`main.py`, `README.md` and `qpx_electrolyser/` must sit directly in that folder.
Install 64-bit CPython 3.12, then run in PowerShell:

```powershell
Set-Location -LiteralPath 'C:\Users\mo113\OneDrive - Imperial College London\Codex project\QPX 600 DP'
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe main.py
```

Activation is unnecessary. Later launches may use `Start_App.cmd`. Runtime-only
installation may use `requirements.txt`; `requirements-dev.txt` adds pytest.
PySide6 is pinned to 6.10.1, pyserial to 3.5 and pytest to 9.0.2.

## Running in Simulation Mode

Both supported entrypoints are equivalent:

```powershell
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m qpx_electrolyser
```

`Simulation Mode (no hardware I/O)` is selected by default. After Connect, the
identification begins:

```text
SIMULATOR,QPX600DP,SIMULATED
```

Disconnect closes only the selected communication session and does not imply
that an output is off. Failed connection/identification cleans up and permits a
fresh manual retry. Application shutdown requests
cleanup and performs a bounded join on the single backend-owner thread. Every
backend operation is serialized there; GUI callbacks submit work without waiting
for simulator latency.

Connection identifies the backend and reads both channels before enabling any
manual control. Programmed voltage,
current limit, measured voltage/current, voltage range and an unambiguous output
state are displayed. Setpoint input is checked against `QPX600DP_CAPABILITIES`
before queue submission. Output On always opens a confirmation dialog; Output
Off is queued at safety priority. Controls are disabled while their channel has
a pending command, after a backend fault, and whenever disconnected. Refresh can
recover a channel display after a command fault.

Each channel also has independent Start Logging and Stop Logging controls.
Choose an existing output directory and a 0.1 to 3600 second interval, then log
either channel or both. Every complete measurement request runs at background
priority through the shared command queue; logging and GUI code never call a
backend directly and never change an instrument setting or output state.

CSV files use Windows-safe UTC names and exclusive creation, so existing files
are never overwritten. Each row is flushed immediately. Stop, disconnect,
application shutdown or an error closes the affected file deterministically;
reconnect never silently resumes an old log.

The console demonstration also uses Simulation Mode only:

```powershell
.\.venv\Scripts\python.exe -m examples.simulator_demo
```

## Milestone 6 shared command worker

One `InstrumentCommandWorker` exclusively creates, connects, uses and disconnects
the active backend. Its bounded submission API assigns a monotonic request ID and
FIFO sequence to every command. Equal-priority commands remain FIFO; explicit
`SAFETY`, `CONNECTION`, `NORMAL` and `BACKGROUND` priorities allow future safety
work to move ahead of waiting routine work without interrupting an operation
already in flight.

Each submitted callable is one atomic backend operation. Results and exceptions
are stored in thread-safe command tickets and relayed to the Qt main thread with
queued signals. Pending work can be cancelled globally or by logical-channel key;
an in-flight call finishes atomically. Disconnect cancels waiting ordinary work,
then closes the backend on the owner thread. Shutdown stops acceptance, cancels
queued work, disconnects once and performs a bounded join. Milestone 7 builds the
manual controls and guarded connection on this worker; it does not add a protocol
engine or automatic hardware action.

## Milestone 7 dual-channel manual controls

Channel 1 and Channel 2 each have their own immutable display state and controller,
but share the one Milestone 6 worker and backend. A refresh or mutation plus its
complete read-back is one atomic queued operation. Request IDs ensure that a
result or error updates only the channel that submitted it. Pending-state guards
prevent repeated clicks from creating duplicate work, and channel cancellation
keys remove queued work during disconnect or shutdown.

The controls use central capability definitions for range-specific voltage and
current validation. Empty, nonnumeric, negative, non-finite, out-of-range and
off-grid values are rejected before submission. A failure preserves the last
successful values, disables mutations on that channel, and does not corrupt the
other channel.

### Guarded Real Hardware mode

Selecting Real Hardware requires a warning confirmation. The operator must then
type an explicit `COM<N>` name and press `Connect Real Hardware`; selecting the
mode or typing the name opens nothing. The worker constructs the transport and
driver only for that Connect request, opens only that port, sends `*IDN?`, rejects
any non-QPX600DP identity, and reads CH1 then CH2. This connection transaction
sends no reset, setpoint, range or output-control command. Controls remain
disabled until every initial query succeeds.

Real Output On uses a stronger warning that names the channel and shows the
programmed voltage and current limit. Output Off needs no Output-On confirmation
and retains safety queue priority. A real-session command error closes the shared
transport and requires a manual reconnect; the application never retries or
reconnects automatically.

Supervised real-hardware GUI acceptance passed on both channels. A manually
entered COM3 connection identified the QPX600DP, populated both panels correctly
and disconnected with both outputs OFF. With the verified 100 ohm, 1/4 W load at
4.000 V / 0.080 A, CH1 displayed approximately 4 V / 0.03 A and CH2 approximately
4 V / 0.04 A. The dialogs correctly identified Real Hardware and the selected
channel; live readback, physical ON/CV indication, Output Off and Disconnect
worked normally while the other channel stayed OFF. Final OP1/OP2 state was
visually confirmed OFF and the load was removed after disconnection.

## Milestone 8 independent CSV logging

CH1 and CH2 log independently or simultaneously. Rows contain ISO-8601 UTC and
monotonic elapsed timestamps, channel, programmed voltage/current, measured
voltage/current, selected range, output state, connection state, session
generation, sample status and error text.

Scheduling is anchored to fixed monotonic deadlines, with no more than one
pending logging read per channel. If queue or backend latency passes deadlines,
missed slots are counted rather than replayed in a burst, and the next future
deadline is retained. A shared disconnect stops both loggers. Missing-directory,
permission, disk/write, malformed-read and channel-local backend failures close
only the affected logger unless the shared connection itself fails. See
[Milestone 8 verification](docs/verification/milestone8.md).

Supervised read-only acceptance used a manually entered COM3 with no load and
both outputs OFF. CH1 and CH2 logged together for approximately 30 seconds; CH2
continued for approximately 5 seconds after CH1 stopped. Two independent files
closed normally with increasing timestamps and elapsed times, approximately
4 V / 0.08 A programmed values, approximately 0 V / 0 A no-load measurements
and Output OFF throughout. No instrument setting was changed, and final physical
OP1/OP2 were confirmed OFF.

## Milestone 9 independent Constant Current protocols

Each channel has an independent Constant Current state machine: `IDLE`,
`STARTING`, `RUNNING`, `PAUSING`, `PAUSED`, `RESUMING`, `STOPPING`, `COMPLETED`
and `FAILED`. Inputs are target current, voltage compliance, active duration and
measurement interval. All are validated against the active voltage range,
hardware programming grids, conservative power envelope and the explicit
Simulation Mode project policy of 10 V, 5 A, 50 W, 24 hours maximum duration and
0.1-3600 second intervals.

Start confirms the selected output is OFF, sets voltage then current and verifies
both read-backs. A separate queued final preflight rechecks the session, OFF state
and both settings immediately before exactly one Output On attempt and ON
confirmation. Sampling uses
fixed active-time monotonic deadlines and background queue priority. Pause sends
and confirms Output Off and freezes active time; Resume rechecks the session,
OFF state and programmed settings before one new Output On. Stop, completion,
failure, coordinated disconnect and application shutdown make a bounded
selected-channel Output-Off attempt and close the protocol CSV deterministically.
The other channel remains independent unless the shared connection fails.

Protocol files use the Milestone 8 writer. Two appended columns,
`protocol_name` and `protocol_state`, are blank for standalone logging and retain
backward readability for earlier CSV consumers. If same-channel logging is already
active, Constant Current remains available and appends its samples and state rows
to that one existing file; it never creates a second writer. Pause/Resume retains
the same file, and protocol Stop/completion leaves the manually started logger
running until the operator stops it. A shared logger failure safely fails and
turns off only that channel's protocol. Real Hardware uses a separate explicit
authorization for Start and Resume, default-cancel load/wiring details, the same
documented project envelope, safety-priority OFF cleanup and authoritative OFF
readback. Supervised resistor-load acceptance passed on both channels, including
Pause/Resume, completion, CSV continuity and final OFF confirmation. The supplied
CSVs contain a brief 0.189-0.201 A first loaded sample after startup/resume before
stable readings; electrolyser and unattended use remain unapproved pending its
assessment and later safety milestones. Milestone 10 adds downstream live plots;
sweep/polarization protocols are not implemented. See
[Milestone 9 verification](docs/verification/milestone9.md).

## Milestone 10 responsive real-time plotting

CH1 and CH2 each have an independent live plot of measured voltage and current
against monotonic elapsed time. Plots consume the existing Milestone 8 logger and
Milestone 9 protocol sample signals; they never poll, submit queue work or access
an instrument backend. Both channels can update simultaneously while the shared
worker continues to serialize the original measurement operations.

Voltage uses the left axis in volts, current uses the right axis in amperes, and
the x-axis is elapsed seconds. Legends, channel identity, latest values,
connection state and retained/displayed sample counts are shown. Pause/Resume,
Stop and completion follow the samples already emitted by the protocol. A
disconnect retains visible history, and samples after an explicit reconnect form
a separate line segment. **Clear plot** resets display history only; it does not
change CSV files, logging, protocols or outputs.

Each display retains at most 2,000 representative points. When that bound is
reached, older display points are progressively decimated while the first and
latest samples remain visible. The complete CSV remains unchanged and continues
to be flushed by the existing logger. Repaint notifications are coalesced to at
most ten per second. Plot parsing or rendering failure is isolated to the plot
status and cannot stop recording or control an output. Sweeps and later protocols
remain unimplemented. See
[Milestone 10 verification](docs/verification/milestone10.md).

## Milestone 11 independent Constant Voltage protocols

CH1 and CH2 each provide a Constant Voltage panel with target voltage, current
limit, active duration, sample interval, Start, Pause, Resume and Stop. The
protocol uses the Milestone 9 state machine and safety cleanup: Start confirms
OFF, sets voltage and current limit, verifies both read-backs, performs one
Output-On attempt and confirms ON. Pause, Stop, normal completion, failure,
coordinated disconnect and application shutdown use safety-priority Output Off
and authoritative read-back.

Constant Voltage was introduced and manually accepted in Simulation Mode. Its
guarded Real Hardware follow-up reuses the Milestone 9 confirmation and preflight
architecture and has been validated offline with injected fake serial only. Both
channels may run independently or simultaneously through the one shared command queue. Constant
Current and Constant Voltage cannot own the same channel at the same time. An
already-active Milestone 8 logger is reused as the single CSV writer, with
`constant_voltage` in `protocol_name`; otherwise the protocol creates and closes
one channel CSV. The same emitted readbacks feed the Milestone 10 plots, with no
extra polling or duplicate writer. See
[Milestone 11 verification](docs/verification/milestone11.md).

Windows Simulation Mode manual acceptance confirmed independent and simultaneous
CH1/CH2 operation, automatic per-run CSV creation when no logger was active,
reuse of one active same-channel CSV through Pause/Resume, and a new CSV for a
new protocol run. Physical Real Hardware Constant Voltage acceptance remains pending.

Real Hardware Start and Resume each use a distinct, detailed, default-cancel
authorization showing the channel, target voltage, current limit, duration,
interval, displayed OFF state and CSV destination. After confirmation, the
controller requires the current identified QPX600DP session, rechecks selected
output OFF and voltage range, writes voltage/current limit, verifies both
readbacks, repeats the OFF/settings preflight, and permits exactly one Output-On
attempt. Real Hardware Constant Current and Constant Voltage intervals must be at
least 1.0 second; Simulation Mode retains its 0.1-second minimum. The one ON write
is followed by `*OPC?`, two ON confirmations about 50 ms apart within one second,
and a full authoritative ON readback. Resume then rebases its monotonic schedule,
excludes paused wall time and schedules the first ordinary sample one interval
later. Pause, Stop, completion, failure, coordinated disconnect and shutdown use
safety-priority Output Off with authoritative readback. Failure to confirm OFF
raises a prominent warning; there is no automatic ON retry or reconnect. Physical
Constant Voltage acceptance remains pending.

## Milestone 5 offline physical backend

`SerialTransport` opens only an explicitly supplied port. It never enumerates
ports, clears buffers or transmits during `connect()`. RS232 configuration is
9600 baud, 8N1 with XON/XOFF. Commands are printable ASCII plus LF; queries
require a complete ASCII response ending CRLF. A timeout, partial frame or
non-ASCII frame invalidates and closes the session so a late response cannot be
mistaken for the next transaction.

`QPX600DPDriver` implements the shared `PowerSupplyInterface` for identification,
voltage/current set and readback, measured voltage/current, per-channel output
state/control, voltage-range query and non-destructive status-byte query. Connect
and disconnect do not issue `*RST`, `CONFIG`, `VRANGE`, `IFLOCK`, `LOCAL` or any
output command. Setting voltage first queries the existing range and validates
against it; the driver never changes the range automatically.

The optional commissioning helper requires an explicit port and is read-only by
default:

```powershell
.\.venv\Scripts\python.exe -m qpx_electrolyser.instrument.hardware_check --port COM7
```

Even read-only queries put the instrument into remote state according to Issue 14.
Do not run this command until a supervised hardware-test plan is approved.
Any setting or OFF write additionally requires
`--confirm-write WRITE-QPX600DP`. Output On is available only as a bounded
`--output-on-for` action and also requires:

- `--confirm-output-on ENABLE-QPX600DP-OUTPUT`;
- explicit voltage and current in the same invocation;
- explicit voltage, current and power safety maxima.

The helper attempts OFF in a `finally` block after the requested interval, but
software cannot guarantee OFF after cable loss, OS failure or instrument failure.
Use an independent physical shutdown method during any future test.

A separate one-shot remote-output diagnostic isolates the synchronous serial
transport and QPX600DP driver from Qt, GUI controllers, the shared command queue,
CSV logging and plotting. It is fixed to CH1 at 4.000 V / 0.080 A, requires the
80 V range and both outputs initially OFF, uses one guarded Output-On attempt,
records ten one-second `OP1?` / `V1O?` / `I1O?` samples, and performs one stable
Output-Off cleanup attempt. It never enumerates ports, retries or starts another
run. The future supervised syntax is:

```powershell
.\.venv\Scripts\python.exe -m qpx_electrolyser.instrument.remote_output_diagnostic --port COM3 --confirm-write WRITE-QPX600DP --confirm-output-on MONITOR-QPX600DP-OUTPUT
```

Do not run it without a separately approved physical setup. Timestamped raw
TX/RX diagnostics are written under `logs/` by default with identity details
redacted. A serial timeout invalidates framing; cleanup is still attempted once,
but the tool reports physical OFF as unconfirmed when the invalidated transport
cannot safely transmit or query.

For supervised loaded validation, the separate monitored-output mode requires
explicit expected setpoints, maximum measured current, minimum/maximum measured
voltage, polling interval, voltage-settling time, the write token, and the strong
`MONITOR-QPX600DP-OUTPUT` token. It verifies both outputs OFF before enabling one
channel, polls output state and loaded measurements throughout, applies current/
over-voltage/communication checks immediately, and performs guarded Output-Off
cleanup with final two-channel OFF verification.

Milestone 5 standard-load acceptance passed on both channels with the same
verified 100 ohm +/-5%, 1/4 W resistor at 4.000 V / 0.080 A. CH1 measured a
stable 3.989-3.990 V / 0.037-0.038 A; CH2 measured 3.995-3.998 V /
0.040-0.041 A. Cleanup and final OFF verification passed after each run. Earlier
ModuLab Test Cell attempts were safely aborted; its 10 mA maximum is unsuitable
for this validation because of the QPX600DP current-setting and metering
resolution/accuracy. See [Milestone 5 verification](docs/verification/milestone5.md).

## Hardware capability versus safety policy

The QPX600DP hardware has two voltage ranges per output (60 V and 80 V), a
50 A current rating and up to 600 W per output. Those values are hardware
capabilities, not safe defaults for an electrolyser experiment.

`QPX600DP_CAPABILITIES` contains immutable manual-derived hardware facts.
`UserSafetyLimits` is a separate optional simulator configuration object. It is
`None` by default because this checkpoint has no user safety configuration or
experiment controls; future work must obtain explicit limits rather than copy
80 V, 50 A or 600 W into experiment defaults.

Issue 14 gives two guaranteed power-envelope points, 10 V / 50 A and
80 V / 7.5 A, but no complete mathematical expression for the graph. The
simulator deliberately avoids interpolation:

- through 10 V, it permits at most 50 A;
- above 10 V through 80 V, it conservatively permits at most 7.5 A;
- it always enforces the 600 W hardware ceiling.

This may reject real operating points between the documented anchors. It must
not be interpreted as an exact reconstruction of the instrument curve. See the
[simulator documentation](docs/simulator.md) and
[QPX command/capability reference](docs/qpx600dp_commands.md).

## Diagnostics and checks

Logs append to `logs/application_YYYYMMDD.log`, rotate at 5 MiB and retain five
older segments. They are diagnostic logs, not experimental data. Use
`--log-dir <directory>` to override the location.

Automated GUI launch check:

```powershell
.\.venv\Scripts\python.exe main.py --smoke-test
.\.venv\Scripts\python.exe -m qpx_electrolyser --smoke-test
```

Tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

For headless non-Windows validation only:

```bash
QT_QPA_PLATFORM=offscreen python main.py --smoke-test
QT_QPA_PLATFORM=offscreen python -m qpx_electrolyser --smoke-test
python -m pytest -q
```

No Qt platform override is applied to ordinary Windows launches. An optional
`--screenshot path.png` argument is accepted only with `--smoke-test`.

## Current scope and deferred work

| Area | Status |
| --- | --- |
| Application skeleton, paths and diagnostic logging | Preserved from Milestone 1 |
| Backend contract and documented architecture | Preserved; QPX facts corrected |
| Independent simulation backend | Preserved; QPX ranges/envelope corrected |
| Simulation connection GUI | Preserved; still the default mode |
| Explicit-port `SerialTransport` and QPX600DP backend | Implemented; fake serial and two-channel standard-load Windows validation passed |
| Shared priority instrument command worker | Implemented for simulator and guarded explicit-port backend; FIFO, priority, cancellation and shutdown tested |
| COM-port enumeration | Not implemented and not performed |
| Physical backend in GUI | Guarded explicit-port path implemented; manually entered COM3 read-only connection and disconnect acceptance passed |
| Dual-channel manual controls | Simulation and real-hardware standard-load acceptance passed independently on CH1 and CH2; stronger real Output-On confirmation verified |
| Independent continuous CSV data logging | Implemented for both channels through the shared queue; Simulation and read-only hardware acceptance passed |
| Independent Constant Current protocols | Simulation/fake-serial validated; supervised CH1/CH2 resistor-load acceptance passed, electrolyser and unattended use unapproved |
| Independent live voltage/current plots | Implemented for both channels with bounded display memory and unchanged full CSV data |
| Independent Constant Voltage protocols | Simulation accepted; guarded Real Hardware path fake-serial validated, physical acceptance pending |
| Later sweep/polarization protocols | Not implemented |
| Complete configurable safety system | Not implemented |
| Windows and real-hardware validation | Milestone 5 guarded and Milestone 7 GUI standard-load Output-On/Off validation passed on both channels; electrolyser validation remains pending and out of scope |

The QPX600DP is a programmable DC power supply, not a potentiostat. Any later
voltage sweep would apply discrete two-terminal voltage steps and would not
control potential against a reference electrode.

## Documentation and history

- [QPX600DP capability and command reference](docs/qpx600dp_commands.md)
- [Architecture](docs/architecture.md)
- [Simulator behaviour](docs/simulator.md)
- [Project tree](docs/project_tree.md)
- [Historical milestone records](docs/milestones.md)
- [Milestone 7 verification and real-hardware GUI acceptance](docs/verification/milestone7.md)
- [Guarded real-hardware follow-up verification](docs/verification/milestone7_real_hardware.md)
- [Milestone 8 CSV logging verification](docs/verification/milestone8.md)
- [Milestone 9 Constant Current verification](docs/verification/milestone9.md)
- [Milestone 10 real-time plotting verification](docs/verification/milestone10.md)
- [Milestone 11 Constant Voltage verification](docs/verification/milestone11.md)
- [Simulation-only visual smoke procedure](tests/manual_smoke_test.md)
- [Hardware acceptance and commissioning safeguards](tests/manual_hardware_test.md)

The authoritative QPX Issue 14 manual and Issue 1 addendum are preserved under
`docs/reference/`. The prior CPX manual is retained only under
`docs/reference/superseded/`; it must not be used for QPX implementation.

Git history is left intact. Milestone 1-4 commits continue to show the original
CPX400DP assumption; the model-correction and Milestones 5-11 work record the
current QPX600DP basis.
