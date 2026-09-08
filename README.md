# CPX400DP Electrolyser

Python 3.12 / PySide6 desktop project for an AIM-TTi CPX400DP dual-output supply.
**This delivery is limited to milestones 1 and 2. It is an offline application
skeleton, not yet an instrument-control application ready for laboratory use.**

The window launches, processes Qt events, shows two equal channel placeholders,
and writes diagnostic logs. No serial port is opened and no output command is
sent. Output states are explicitly unknown.

## Windows installation

Extract the contents of the project archive directly into:

```text
C:\Users\mo113\OneDrive - Imperial College London\Codex project\CPX 400 DP
```

`main.py`, `README.md` and `cpx_electrolyser/` should sit directly inside that
folder. The project root is determined from the source files, so spaces in the
Windows folder name and launching from another directory are supported.

Install 64-bit CPython 3.12 with the Python launcher, then run in PowerShell:

```powershell
Set-Location -LiteralPath 'C:\Users\mo113\OneDrive - Imperial College London\Codex project\CPX 400 DP'
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe main.py
```

Activation is unnecessary: calling the virtual environment's Python directly
avoids changing PowerShell execution policy. On subsequent launches, double-click
`Start_App.cmd`. Runtime-only installation may use `requirements.txt` instead;
`requirements-dev.txt` also installs pytest.

PySide6 is pinned to 6.10.1; pytest is pinned to 9.0.2. PySide6 wheels include Qt,
so a separate Qt installation is unnecessary ([Qt installation guide](https://doc.qt.io/qtforpython-6/gettingstarted.html)).
Transitive dependencies are resolved by pip and are not a complete platform lock.

## Running and diagnostics

From the project root, `python main.py` and `python -m cpx_electrolyser` are
equivalent. Logs are appended to `logs/application_YYYYMMDD.log`; the date is
the launch date. Each file rotates at 5 MiB with five retained segments. Logs
include local timestamps with UTC offsets, thread names and detailed exceptions.
They are diagnostic records, not experimental CSVs. Run one application instance
per project directory to avoid concurrent processes rotating the same log file.

An unwritable log directory prevents startup with a concise message. To choose
another directory, run `python main.py --log-dir <directory>`. Neither this option
nor the window geometry is persisted yet.

Automated launch check (opens briefly and exits):

```powershell
.\.venv\Scripts\python.exe main.py --smoke-test
```

For Linux headless verification only:

```bash
QT_QPA_PLATFORM=offscreen python main.py --smoke-test
python -m pytest -q
```

No Qt platform override is applied to normal Windows launches. The test suite
sets `offscreen` for its own subprocesses. An optional `--screenshot path.png`
argument with `--smoke-test` records the actual Qt window.

Minimal Ubuntu containers may also need the system `libegl1` package before Qt
Widgets can import, even with offscreen rendering. This is a Linux validation
dependency, not an additional Windows installation step
([Ubuntu package reference](https://packages.ubuntu.com/noble/libegl1)).

## Features scheduled for later milestones

| Operator task | Scheduled milestone | Status in this build |
| --- | --- | --- |
| Run Simulation Mode | 3-4 | Not implemented |
| Connect CPX400DP / choose COM port | 4-6 | Not implemented |
| Manually control independent outputs | 7 | Not implemented |
| Find experiment CSVs | 8 onward | No experiment data is generated |
| Start CC / simultaneous tests | 9 | Not implemented |
| View live time plots | 10 | Not implemented |
| Run CV / voltage sweep / current sweep | 11-13 | Not implemented |
| Run a polarisation curve | 14 | Not implemented |
| Edit custom sequences | 15 | Not implemented |
| Emergency all-off and complete fault handling | 16 | No software stop control exists yet |
| Restore settings / refine UI | 17-18 | Not implemented |
| Build Windows executable | 19 | Deferred; no executable is included |
| Final regression and accelerated stress tests | 20 | Deferred |

The CPX400DP is a programmable power supply, **not a potentiostat**. A later
"Voltage Sweep / Staircase LSV" feature will apply discrete voltage steps across
the two-terminal cell; it will not control potential against a reference electrode.

The planned stack retains pyserial for serial transport, pyqtgraph for plotting,
the standard-library CSV/JSON modules for data, pytest for tests, and PyInstaller
for packaging. Unused plotting, communication and packaging dependencies will be
added and pinned when their milestones are implemented.

## Development and verification

Each milestone must run, pass its relevant tests, and be committed before the
next milestone begins. See [milestone records](docs/milestones.md) and the
[Windows visual smoke test](tests/manual_smoke_test.md).

Only the offline skeleton can be checked in this delivery. Windows desktop,
USB driver and live hardware checks remain to be performed on the lab PC.
Long-run reliability, CSV recovery and all future safety actions are not yet
implemented or tested. The requested Windows path was not accessible from the
Linux build workspace; extract the delivered archive there before running.

Git history is maintained in separate stable milestone commits. The archive
includes the repository's `.git/` directory. With Git installed, run
`git log --oneline` from the extracted project root to inspect the milestones;
Git is not required to run the application. No remote repository is configured.
