# QPX600DP Electrolyser

Python 3.12 / PySide6 desktop project for an Aim-TTi QPX600DP dual-output
power supply. Milestones 1-4 were originally completed under a CPX400DP model
assumption. Version 0.7.1 preserves those historical records, the Milestone 5
QPX600DP serial backend and completed two-channel standard-load Windows
validation, the Milestone 6 shared
asynchronous command queue, and adds Milestone 7 dual-channel manual controls
with a guarded explicit-port hardware connection.

Simulation Mode remains the default. A deliberately selected Real Hardware mode
accepts only a manually entered COM name and never enumerates ports. The one
shared backend-owner worker constructs `SerialTransport` and `QPX600DPDriver`
only after explicit Real Hardware Connect. There is no experiment protocol.
Physical validation includes identification, read-only queries, guarded setpoint
read-back, and monitored standard-load Output-On/Off tests on both channels.
Electrolyser validation remains outside Milestone 5 and is pending.

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
reconnects automatically. Real-hardware GUI acceptance remains pending.

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
| Physical backend in GUI | Guarded explicit-port path implemented with fake-transport validation; real acceptance pending |
| Dual-channel manual controls | Implemented for simulator and identified QPX600DP; stronger confirmation for real Output On |
| Experiment protocols, CSV data and plots | Not implemented |
| Complete configurable safety system | Not implemented |
| Windows and real-hardware validation | Milestone 5 standard-load Output-On/Off validation passed on both channels; electrolyser validation remains pending and out of scope |

The QPX600DP is a programmable DC power supply, not a potentiostat. Any later
voltage sweep would apply discrete two-terminal voltage steps and would not
control potential against a reference electrode.

## Documentation and history

- [QPX600DP capability and command reference](docs/qpx600dp_commands.md)
- [Architecture](docs/architecture.md)
- [Simulator behaviour](docs/simulator.md)
- [Project tree](docs/project_tree.md)
- [Historical milestone records](docs/milestones.md)
- [Guarded real-hardware follow-up verification](docs/verification/milestone7_real_hardware.md)
- [Simulation-only visual smoke procedure](tests/manual_smoke_test.md)
- [Hardware acceptance and commissioning safeguards](tests/manual_hardware_test.md)

The authoritative QPX Issue 14 manual and Issue 1 addendum are preserved under
`docs/reference/`. The prior CPX manual is retained only under
`docs/reference/superseded/`; it must not be used for QPX implementation.

Git history is left intact. Milestone 1-4 commits continue to show the original
CPX400DP assumption; the model-correction and Milestones 5-7 commits record the
current QPX600DP basis.
