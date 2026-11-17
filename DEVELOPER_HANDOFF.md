# QPX600DP developer handoff

This file describes source development and Windows repackaging. The packaged
`dist` directory is generated output, not source.

## Source repository

Current company laboratory checkout:

```text
C:\Users\Admin\Documents\GitHub\qpx600dp-electrolyser-control
```

Future colleagues should clone the company-controlled repository into a normal
writable work directory. **Always edit, test and commit the source repository;
never edit files under `dist`.** Rebuild `dist` from a reviewed source commit.

## Python environment

Install company-approved 64-bit CPython 3.12, then run from the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
```

The virtual environment is disposable and machine-specific. Do not copy or
archive `.venv` as a source backup.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q main.py qpx_electrolyser tests examples
.\.venv\Scripts\python.exe main.py --smoke-test
git diff --check
```

The smoke test is Simulation Mode only. Real Hardware remains explicit-port,
operator-controlled work and must follow the documented safety procedures.

## Source GUI launch

```powershell
.\.venv\Scripts\python.exe main.py
```

Every launch starts disconnected. Selecting Real Hardware does not connect;
connection still requires the existing deliberate operator action.

## Windows packaging and repackaging

PyInstaller is pinned in `requirements-dev.txt`. Build the one-folder release
and refresh the desktop shortcut with:

```powershell
.\packaging\package_windows.ps1 -CreateDesktopShortcut
```

If a reviewed prior release folder is already present, use:

```powershell
.\packaging\package_windows.ps1 -Force -CreateDesktopShortcut
```

The script records source commit/build information in generated
`BUILD_INFO.txt`. It excludes source tests, virtual environments, Git data,
diagnostic logs, experimental data and CSV files from the bundle.

Packaged executable:

```text
<repository>\dist\QPX600DP Electrolyser Control\QPX600DP Electrolyser Control.exe
```

Desktop shortcut:

```text
%USERPROFILE%\Desktop\QPX600DP Electrolyser Control.lnk
```

The shortcut targets the executable in the complete one-folder release and uses
that release folder as `Start in`. Do not move the executable away from its
`_internal` dependencies. Copy or archive the complete release folder together.

## Source preservation

`build`, `dist`, shortcuts, logs and experimental `Data` are deliberately not
committed. Preserve the complete Git repository, including `.git`, on a
company-controlled remote and verified company backup. The tracked icon source,
PyInstaller spec, runtime hook, packaging script, requirements and this handoff
file are sufficient to reproduce the Windows release from source.
