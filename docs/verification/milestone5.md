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
- Output On is never automatic. The helper requires two independent confirmation
  tokens, explicit voltage/current setpoints, and separate user safety limits;
  it attempts Output Off in a `finally` block but cannot guarantee OFF after a
  cable, process or hardware failure.

This is partial hardware validation only. Output On remains untested. Loaded
voltage delivery, loaded current delivery and operation with an electrolyser
remain pending and must not be inferred from the unloaded setpoint read-backs.
Milestone 6 was not started.
