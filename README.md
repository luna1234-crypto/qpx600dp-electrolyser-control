# QPX600DP Electrolyser

Python 3.12 / PySide6 desktop project for an Aim-TTi QPX600DP dual-output
power supply. Milestones 1-4 were originally completed under a CPX400DP model
assumption. Version 0.4.1 is a bounded model-correction checkpoint: it preserves
their application, logging, simulator and connection-thread behaviour while
correcting current product identity, documented capabilities and simulation
boundaries. Historical verification records remain historical and are marked as
superseded where appropriate.

This is not yet a physical instrument-control application. The GUI connects to,
identifies and disconnects only `SimulatedPowerSupply`, using one dedicated
backend-owner thread. It has no serial transport, COM-port discovery, physical
driver, setpoint/output GUI controls or experiment protocols. Output state is
explicitly unknown outside the simulator.

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
PySide6 is pinned to 6.10.1 and pytest to 9.0.2.

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
cleanup and performs a bounded join on the single backend-owner thread; that
same ownership boundary is reserved for a future command worker.

The console demonstration also uses Simulation Mode only:

```powershell
.\.venv\Scripts\python.exe -m examples.simulator_demo
```

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
| Physical QPX600DP connection and transport | Not implemented |
| COM-port enumeration | Not implemented and not performed |
| Output/setpoint GUI controls | Not implemented |
| Experiment protocols, CSV data and plots | Not implemented |
| Complete configurable safety system | Not implemented |
| Windows and real-hardware validation | Not performed |

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
- [Deferred hardware acceptance outline](tests/manual_hardware_test.md)

The authoritative QPX Issue 14 manual and Issue 1 addendum are preserved under
`docs/reference/`. The prior CPX manual is retained only under
`docs/reference/superseded/`; it must not be used for QPX implementation.

Git history is left intact. Milestone 1-4 commits continue to show the original
CPX400DP assumption, and the ordinary model-correction commit records when that
assumption was replaced. No remote repository is configured.
