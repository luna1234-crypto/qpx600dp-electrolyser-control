# Milestone 5 verification - QPX600DP serial driver and partial Windows validation

Date: 2026-09-24

## Scope and authority

The implementation was derived only from the project copies of:

- `../reference/qpx600d_dp_instruction_manual_48511-1460_issue14.pdf`,
  QPX600D & DP Instruction Manual, document 48511-1460, Issue 14;
- `../reference/qpx600_addendum_48511-1461_issue1.pdf`, document 48511-1461,
  Issue 1.

The addendum concerns the `LOGIC IN` hardware serial-number split and does not
change the implemented serial command subset. Superseded CPX material was not
used as a command or capability source.

Implemented source anchors, using printed manual pages:

- serial/USB virtual-COM characteristics: Issue 14 pp. 21-23;
- LF command and CRLF response framing, ASCII and sequential parsing:
  Issue 14 pp. 31-32;
- voltage, current, measurement and output commands: Issue 14 p. 33;
- voltage-range query: Issue 14 p. 34;
- non-destructive status-byte query: Issue 14 p. 35;
- identification query: Issue 14 p. 36, with the conflicting example on p. 25.

## Automated verification

Environment: temporary CPython 3.12.14 virtual environment at
`/private/tmp/qpx600dp-py312`, with the pinned runtime and development
requirements installed, including pyserial 3.5.

Command:

```text
/private/tmp/qpx600dp-py312/bin/python -m pytest -q -p no:cacheprovider
```

Result:

```text
162 passed in 3.58s
```

Coverage includes:

- exact 9600/8N1/XON-XOFF serial construction and no bytes on connect;
- exact ASCII command bytes with LF and response framing with CRLF;
- both channels and all implemented command strings;
- fixed-point and status response parsing;
- empty, partial, oversized, non-ASCII and malformed responses;
- read, write, open and close failures, timeout invalidation and retry cleanup;
- disconnected use, idempotent lifecycle and serialized lifecycle/I/O access;
- voltage range/current resolution grids and hardware-capability boundaries;
- read-only hardware-helper defaults and independent write/Output-On gates.

Additional checks:

```text
PYTHONPYCACHEPREFIX=/private/tmp/qpx600dp-m5-pyc \
  /private/tmp/qpx600dp-py312/bin/python -m compileall -q \
  main.py qpx_electrolyser tests examples
/private/tmp/qpx600dp-py312/bin/python -m pip check
git diff --check
```

All returned exit code 0. `pip check` reported no broken requirements.

Both package entrypoints completed a Qt event-loop smoke test with an explicit
writable temporary log directory:

```text
QT_QPA_PLATFORM=offscreen /private/tmp/qpx600dp-py312/bin/python \
  main.py --smoke-test --log-dir /private/tmp/qpx600dp-m5-main-log
QT_QPA_PLATFORM=offscreen /private/tmp/qpx600dp-py312/bin/python \
  -m qpx_electrolyser --smoke-test \
  --log-dir /private/tmp/qpx600dp-m5-module-log
```

Both returned exit code 0, showed version 0.5.0, processed Qt events and stopped
the connection worker cleanly. The GUI deliberately remains Simulation Mode;
the physical driver is not connected to the Milestone 4 GUI.

During the original offline verification, the hardware helper was invoked only
with `--help`. No `--port` was supplied, no port discovery exists in the code,
and no serial factory was opened outside fake tests.

## Windows validation - partial

Supervised Windows validation was performed on Windows 11 Pro 24H2, AMD64,
using 64-bit CPython 3.12.10. Before physical-hardware validation, the complete
automated suite passed with 162 tests. The automatic Simulation Mode smoke test
also passed. A manual Windows Simulation Mode exercise opened the GUI, connected,
displayed the simulated identity, disconnected, reconnected and closed normally;
it did not access a COM port or physical instrument.

The explicit-port helper subsequently identified a QPX600DP on COM3. Its default
read-only check completed successfully. With no load or electrolyser connected
and the operator visually confirming both outputs OFF, guarded voltage-setpoint
writes and read-backs produced this final state:

- channel 1: 10 V programmed, 5 A current limit unchanged, 80 V range unchanged,
  output OFF;
- channel 2: 5 V programmed, 0.055 A current limit unchanged, 80 V range unchanged,
  output OFF;
- status byte: 0.

No current-limit, voltage-range or output-control command was sent. The
instrument serial number is intentionally excluded from this committed record.
Each serial session was closed normally.

## Conservative boundaries and remaining validation

### Guarded monitored-output safety follow-up

The explicit-port helper now provides a separate monitored-output mode for a
future supervised loaded test. This implementation was developed and tested
offline only. It has not been used against COM3 or any physical instrument, so
real monitored-output validation remains pending.

The mode requires every safety value and both independent confirmation tokens:

```text
python -m qpx_electrolyser.instrument.hardware_check \
  --port <EXPLICIT_PORT> \
  --channel <1_OR_2> \
  --monitor-output-for <SECONDS> \
  --expected-voltage <VOLTS> \
  --expected-current-limit <AMPS> \
  --max-measured-current <AMPS> \
  --min-measured-voltage <VOLTS> \
  --max-measured-voltage <VOLTS> \
  --poll-interval <SECONDS> \
  --confirm-write WRITE-QPX600DP \
  --confirm-monitored-output-on MONITOR-QPX600DP-OUTPUT
```

Duration is limited to 60 seconds and the polling interval to 0.05 through
1.0 seconds. All monitored-mode arguments are checked before the driver is
created or a serial port can be opened. The mode conflicts with set-voltage,
set-current, Output-Off, legacy timed-Output-On and legacy safety-limit options;
it never changes programmed voltage, current limit, voltage range, protection
settings or the other channel.

Before Output On it requires the exact supported manufacturer/model identity,
queries both output states, and queries the selected channel's programmed
voltage, current limit, voltage range and unloaded measurements. Both outputs
must be OFF. Programmed values must match the explicitly expected values within
half of the documented programming resolution for the active range/current
grid. After `OP<N> 1`, each bounded polling cycle queries `OP<N>?`, `V<N>O?`
and `I<N>O?` and prints a UTC timestamp, elapsed time, measured voltage,
measured current and output state.

The run stops immediately on the supplied current threshold, outside the
supplied voltage window, unexpected Output Off, parsing/communication failure,
or the deadline. A cleanup scope is active before `OP<N> 1` is attempted. It
attempts `OP<N> 0` exactly once even if the Output-On transaction raises after
partial transmission, then queries `OP<N>?` when communication remains
available and reports explicitly when physical OFF cannot be confirmed. After
a confirmed shutdown it queries both output states. A fully successful run also
prints both channels' final readback and the non-destructive status byte.

Offline fake-driver and stateful fake-serial coverage includes successful
polling, exact command order, either output already ON, identity/setpoint
mismatch, overcurrent, under/over-voltage, unexpected Output Off, malformed and
timed-out responses, failures during and after Output On, guaranteed cleanup
attempt, no automatic retry, other-channel isolation, serial closure, and CLI
validation before driver creation.

- `connect()` and `disconnect()` only open/close the host handle. They do not
  issue reset, range, configuration, status-clear, lock or output commands.
- Set voltage queries the already-selected range and refuses values outside it;
  it never changes the range automatically.
- The driver requires the documented fixed-point response shape and exact unit
  suffixes. Actual firmware formatting and precision remain unverified.
- Issue 14 p. 36 describes the third `*IDN?` field as `0`, while p. 25 shows a
  serial number. The parser conservatively retains any nonempty third field and
  still requires exactly four fields and model `QPX600DP`.
- The default hardware check uses queries only, but the manual says receiving a
  remote command places the instrument in remote state. It is therefore not a
  zero-effect operation and must be supervised on real hardware.
- Set commands have no documented acknowledgement. The driver does not invent
  one and does not query-and-clear error registers behind the caller's back.
- Output On is never automatic. The legacy helper retains its two-token bounded
  dwell interface unchanged. The new monitored mode has a distinct strong
  confirmation token, does not write setpoints, polls throughout the run, and
  makes one best-effort Output-Off attempt; software cannot guarantee physical
  OFF after a cable, process or hardware failure.

This remains partial hardware validation only. Real Output On remains untested.
Loaded voltage delivery, loaded current delivery and operation with an
electrolyser remain pending and must not be inferred from the unloaded setpoint
read-backs or offline monitored-mode tests.
