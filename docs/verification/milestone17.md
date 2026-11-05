# Milestone 17 — Configuration Persistence

## Implemented boundary

One versioned `ApplicationSettings` model and `SettingsStore` persist a fixed
whitelist of preferences in an OS-appropriate per-user JSON file. Help > About
shows the exact path. The model includes:

- last manually entered COM-port text;
- data/export directory;
- independent CH1/CH2 `SafetyLimits`;
- default active area and standalone logging interval;
- Simulation/Real Hardware preference;
- main-window position and size; and
- visibility of live-time, Staircase LSV and polarisation plot groups.

The store writes a flushed same-directory temporary file and uses atomic replace.
It migrates schema 0, safely handles missing/corrupt/unsupported configuration,
rejects invalid and out-of-capability safety values through `SafetyValidator`,
and falls back without crashing. Off-screen geometry is moved to an available
screen. Reset Settings to Defaults is default-Cancel, requires a disconnected
idle application and does not touch M15B plot-configuration files.

## Safety boundary

Every launch starts DISCONNECTED. Restoring Real Hardware mode or a port string
does not enumerate/open a port, construct a backend, connect, issue a command,
enable an output, resume a protocol or restore RUNNING/PAUSED state. Manual
Connect and all existing M9–M16 confirmations remain mandatory. The schema has
no credential, instrument serial, transient authorization token, output state,
protocol state or channel-ownership field.

Smoke-test processes deliberately use isolated defaults and never read or write
the operator's settings file.

## Automated evidence

- versioned save/restart/restore and CH1/CH2 independence;
- missing, corrupt and schema-0 migration paths;
- invalid values and out-of-capability safety-limit rejection;
- atomic-replace failure preserving the previous file;
- disconnected Real Hardware preference restoration with zero backend creation;
- no output/protocol restoration;
- off-screen window recovery;
- plot visibility and default area/interval restoration; and
- confirmed reset without M15B configuration modification.

No COM port or physical hardware is required or accessed by this milestone.

## Validation result — 2026-11-05

- focused persistence and M9-M16/M15B regressions: **46 passed**;
- complete pytest suite: **547 passed**;
- package, entrypoint and tests `compileall`: passed;
- `pip check`: no broken requirements;
- isolated default-Simulation smoke: passed with both channels OFF, clean
  disconnect and clean worker shutdown;
- `git diff --check`: passed.

The automated smoke path deliberately ignored the operator settings file. No COM
port was enumerated or accessed and no physical instrument command was sent.
