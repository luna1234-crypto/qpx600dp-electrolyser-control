# Milestone 7 follow-up verification - guarded real-hardware connection

Date: 2026-09-29

## Scope and manual evidence

The implementation was checked against the local QPX600D & DP Issue 14 manual
and Issue 1 addendum. Issue 14 printed pp. 21-23 define USB virtual-COM and
9600/8N1/XON-XOFF operation; pp. 31-32 define sequential LF-command / CRLF-reply
framing; pp. 33-34 define channel query, setpoint, output and range commands; and
p. 36 defines `*IDN?`. The addendum changes LOGIC IN hardware only and does not
alter this command path.

Simulation Mode remains the default. Real Hardware mode requires a warning
acknowledgement, manual `COM<N>` entry and explicit Connect. Selection and entry
perform no I/O. The backend is created on the existing worker only after Connect.

## Guarded connection transaction

The exact fake-verified command order is:

```text
*IDN?
VRANGE1?  V1?  I1?  V1O?  I1O?  OP1?
VRANGE2?  V2?  I2?  V2O?  I2O?  OP2?
```

All are queries. Connect/disconnect send no reset, set voltage/current/range,
output-control, lock or status-clear command. A non-QPX600DP identity or any
initial read failure prevents CONNECTED state and closes the candidate transport.
Controls remain disabled until both read-backs succeed.

## Offline automated coverage

Every physical-path test injects a fake serial factory. Coverage includes:

- no backend construction or port open on mode selection / port entry;
- exact open, identify and CH1-then-CH2 query order;
- no mutation during connection or disconnect;
- wrong identity, timeout, malformed reply and busy-port cleanup;
- CH1/CH2 mapping and isolation;
- voltage/current writes with both fake output states OFF;
- detailed real Output-On confirmation, cancellation and confirmed routing;
- queue ordering and duplicate-request suppression;
- real command failure cleanup and explicit-only recovery;
- Simulation Mode isolation from the real-backend factory;
- bounded clean shutdown and existing worker/thread tests.

The source contains no port enumeration API. Tests construct `SerialTransport`
only with injected fake serial factories; none invokes pyserial's real opener.

## Validation results

Final validation commands:

```text
python -m pytest -q
python -m compileall -q main.py qpx_electrolyser tests examples
python -m pip check
python main.py --smoke-test
git diff --check
```

Results:

- complete suite: **206 passed in 5.32s**;
- `compileall`: exit code 0;
- `pip check`: `No broken requirements found.`;
- native Windows Simulation Mode smoke: connected to the simulator, identified
  it, populated both channel panels, visually showed both outputs OFF, then
  disconnected and stopped the worker cleanly;
- residual worker/QThread-destroyed warnings: none;
- `git diff --check`: exit code 0.

## Acceptance boundary

No real COM port, Test Bridge or physical QPX600DP was accessed during this work.
The guarded GUI path has only fake-transport validation. Real-hardware GUI
connection, read-back, setpoint and Output On/Off acceptance remain pending a
separately approved supervised procedure. Protocols, automated sequences,
logging, plotting, automatic reconnect, Emergency Off and Milestone 8+ behavior
remain unimplemented.
