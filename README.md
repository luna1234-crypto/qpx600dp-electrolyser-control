# QPX600DP Electrolyser

Python 3.12 / PySide6 desktop project for an Aim-TTi QPX600DP dual-output
power supply. Milestones 1-4 were originally completed under a CPX400DP model
assumption. Version 0.6.0 preserves those historical records, the Milestone 5
QPX600DP serial backend and partial Windows validation, and adds the Milestone 6
shared asynchronous instrument command queue.

The GUI still connects only to `SimulatedPowerSupply`, now through one shared
backend-owner worker used by both future channels. `SerialTransport` and
`QPX600DPDriver` remain outside the GUI. There is no COM-port discovery,
setpoint/output GUI or experiment protocol. Physical validation remains limited
to identification, read-only queries and unloaded voltage-setpoint read-back
with both outputs OFF.

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

Disconnect closes only the simulated communication session and does not imply
that an output is off. Failed connection/identification cleans up and permits a
fresh retry. Physical mode remains disabled. Application shutdown requests
cleanup and performs a bounded join on the single backend-owner thread. Every
backend operation is serialized there; GUI callbacks submit work without waiting
for simulator latency.

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
queued work, disconnects once and performs a bounded join. This milestone adds no
channel controls, Output On path, protocol engine or physical GUI integration.

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
| Simulation connection GUI | Preserved from Milestone 4 |
| Explicit-port `SerialTransport` and QPX600DP backend | Implemented; fake serial plus partial Windows validation |
| Shared priority instrument command worker | Implemented for Simulation Mode; FIFO, priority, cancellation and shutdown tested |
| COM-port enumeration | Not implemented and not performed |
| Physical backend in GUI | Not implemented |
| Output/setpoint GUI controls | Not implemented |
| Experiment protocols, CSV data and plots | Not implemented |
| Complete configurable safety system | Not implemented |
| Windows and real-hardware validation | Partial: identification, read-only check and unloaded setpoint read-back passed; Output On, loaded delivery and electrolyser validation pending |

The QPX600DP is a programmable DC power supply, not a potentiostat. Any later
voltage sweep would apply discrete two-terminal voltage steps and would not
control potential against a reference electrode.

## Documentation and history

- [QPX600DP capability and command reference](docs/qpx600dp_commands.md)
- [Architecture](docs/architecture.md)
- [Simulator behaviour](docs/simulator.md)
- [Project tree](docs/project_tree.md)
- [Historical milestone records](docs/milestones.md)
- [Simulation-only visual smoke procedure](tests/manual_smoke_test.md)
- [Hardware acceptance and commissioning safeguards](tests/manual_hardware_test.md)

The authoritative QPX Issue 14 manual and Issue 1 addendum are preserved under
`docs/reference/`. The prior CPX manual is retained only under
`docs/reference/superseded/`; it must not be used for QPX implementation.

Git history is left intact. Milestone 1-4 commits continue to show the original
CPX400DP assumption; the model-correction and Milestones 5-6 commits record the
current QPX600DP basis.
