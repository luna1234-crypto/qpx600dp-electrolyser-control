# CPX400DP verified command reference

Milestone 2, verified 2026-09-08. **This is the contract for the future driver;
the current application sends no commands.** Verification is against the
supplied manual, not a connected instrument.

Source: [CPX400DP PowerFlex Dual DC Power Supply instruction manual, Issue 1](reference/cpx400dp_manual_issue1.pdf),
book part number **48511-1480**, 34 PDF pages. References below use **printed page
numbers**; PDF viewer page = printed page + 1. Original attachment:
`6c813f9e-bd03-448f-bdc2-f37fd54e1039.pdf`.
SHA-256: `652fa24f5442025bd4d5dc787de8a77c93981723df47f4e1330372f70128b1b8`.

## Verified capabilities

| Capability | Value in supplied manual | Printed pages |
| --- | --- | --- |
| Outputs | 2; independent and electrically isolated in independent mode | 2-3 |
| Voltage range per output | 0-60 V | 2 |
| Current range per output | 0-20 A | 2 |
| Power envelope per output | Up to 420 W; not 60 V at 20 A simultaneously | 2, 12-13 |
| Stated envelope operating points, both outputs loaded | 60 V / 7 A; 42 V / 10 A; 20 V / 20 A | 13 |
| Remote voltage programming resolution | 0.01 V (10 mV) | 4 |
| Remote current programming resolution | 0.001 A (1 mA) | 4 |
| Meter voltage / current resolution | 0.01 V / 0.01 A | 3 |
| Meter reading rate | 4 Hz | 3 |
| Interfaces | USB 2.0 Full Speed virtual COM, RS232, LAN, GPIB | 4, 10 |

Programming resolution is not measurement resolution or accuracy. The manual
does not specify a separate higher-resolution remote ADC or guaranteed remote
sampling rate; use the meter resolutions conservatively in the future capability
model, pending hardware checks. Printed p.12 describes continuous current
limiting down to 10 mA; 1 mA programming increments do not establish accurate
1 mA CC regulation. Exceeding the power envelope causes **UNREG**, rather than a
guaranteed rejection of the requested V/I settings.

## Serial / USB framing

| Property | Verified behaviour | Printed pages |
| --- | --- | --- |
| Physical RS232 settings | Fixed 9600 baud, 8 data bits, no parity, 1 stop bit | 18-19 |
| RS232 flow control | XON/XOFF; input buffer 256 bytes, approximately 200 queued characters triggers XOFF, about 100 free bytes triggers XON | 19, 27 |
| RS232 cable | Straight-through, not a null-modem cable | 18 |
| USB settings | Virtual COM port; baud-rate setting is ignored | 19 |
| Encoding | ASCII, case insensitive; high bit ignored | 19, 27-28 |
| Command terminator | LF, byte `0x0A` (`\n`) | 27 |
| Response terminator | CR LF, bytes `0x0D 0x0A` (`\r\n`) | 27 |
| Grouped commands | Semicolon separated, final LF required | 27 |
| Execution order | Sequential; a command/query completes before the parser starts the next | 27-28 |

Planned USB host configuration: nominal 9600 / 8-N-1, hardware RTS/CTS and
DSR/DTR flow control disabled. The manual does **not** separately specify USB
XON/XOFF behaviour; keep USB software-flow control as a distinct configuration
choice to verify on hardware (initially disabled). Enable XON/XOFF for physical
RS232. Do not mistake the ignored USB baud setting for a measured USB speed.

Use one complete command per write and consume each expected response before
another query. Ordinary setters have no documented acknowledgement. A successful
write is not proof that the instrument accepted a setting. Reject empty,
unterminated, malformed, non-finite or unexpected-channel responses in the driver.
Transport handles ASCII framing and deadlines; CPX400DP handles units and syntax.

## Commands required for the planned application

`n` is 1 or 2; `<nrf>` is numeric input, `<nr1>` an integer, `<nr2>` fixed-point
output. Send a decimal point `.`. Numeric input is rounded by the instrument to
its supported precision (p.28), so validate resolution **before** transmission.
Table entries omit the final LF on commands and CRLF on replies.

| Operation | Channel 1 | Channel 2 | Response | Printed page |
| --- | --- | --- | --- | --- |
| Set voltage / CC compliance, V | `V1 <nrf>` | `V2 <nrf>` | No reply documented | 28 |
| Set current / CV current limit, A | `I1 <nrf>` | `I2 <nrf>` | No reply documented | 28 |
| Output ON | `OP1 1` | `OP2 1` | No reply documented | 29 |
| Output OFF | `OP1 0` | `OP2 0` | No reply documented | 29 |
| Measured voltage | `V1O?` | `V2O?` | `<nr2>V` | 29 |
| Measured current | `I1O?` | `I2O?` | `<nr2>A` | 29 |
| Output enable state | `OP1?` | `OP2?` | `0` OFF, `1` ON | 29 |
| Programmed voltage | `V1?` | `V2?` | `V <n> <nr2>` as printed | 28 |
| Programmed current limit | `I1?` | `I2?` | `I <n> <nr2>` as printed | 28 |
| Read and clear limit events | `LSR1?` | `LSR2?` | Integer bit field | 24, 30 |
| Set overvoltage trip, V | `OVP1 <nrf>` | `OVP2 <nrf>` | No reply documented | 28 |
| Read overvoltage trip | `OVP1?` | `OVP2?` | `VP<n> <nr2>` | 29 |
| Set overcurrent trip, A | `OCP1 <nrf>` | `OCP2 <nrf>` | No reply documented | 28 |
| Read overcurrent trip | `OCP1?` | `OCP2?` | `CP<n> <nr2>` | 29 |

For example, the command bytes for 2.00 V on CH1 are `V1 2.00\n`; a measured
voltage reply has the form `2.00V\r\n`. This is a syntax example, not a lab reading.
The character before `?` in `V1O?` / `I1O?` is the **letter O**, not zero.
Setpoint-query replies contain a channel prefix; measured replies do not.
Confirm actual setpoint-prefix whitespace and digit counts on the supplied unit;
do not assume fixed response lengths from the manual's notation.

| Shared operation | Command | Response / behaviour | Printed page |
| --- | --- | --- | --- |
| Identify | `*IDN?` | `<NAME>,<model>,<Serial No.>,<version>`; version describes main and interface firmware | 31 |
| Acquire interface ownership | `IFLOCK` | `1` acquired; `-1` unavailable | 17, 29 |
| Check ownership | `IFLOCK?` | `1` owned here; `0` unlocked; `-1` unavailable | 29 |
| Release ownership | `IFUNLOCK` | `0` released; `-1` failed | 29 |
| Select independent channels | `CONFIG 2` | CH2 must be OFF before changing configuration | 24, 30 |
| Read configuration | `CONFIG?` | `2` independent; `0` voltage tracking | 30 |
| Simultaneous all-output OFF | `OPALL 0` | All outputs off; already-off outputs remain off | 29 |
| Read and clear execution error | `EER?` | Integer error code | 23-24, 30 |
| Read and clear standard events | `*ESR?` | Integer bit field | 23, 30 |
| Read status byte | `*STB?` | Integer status summary; enables affect event summary bits | 24, 31 |
| Command-order synchronization | `*OPC?` | Always `1` when executed; not proof of physical settling or command success | 28, 30 |

**`IFLOCK` and `IFUNLOCK` produce replies even though neither ends in `?`.**
Ignoring those replies would desynchronise subsequent measurements.
`I<n>` is the normal current limit; `OCP<n>` is a separate protection trip.
OVP covers 1-66 V in 0.1 V increments, typically 1 ms response. OCP resolution is
0.01 A, with typical response 500 ms; local-mode/default OCP is 22 A (pp.2, 14, 26).
The supplied specification does not give an explicit complete programmable OCP
range: do not infer one from the 22 A default.

## Status interpretation

`LSR<n>?` reads **latched events and clears them**. Bits: 0 entered CV;
1 entered CC; 2 OVP trip; 3 OCP trip; 4 entered power-limit/UNREG;
6 trip requiring front-panel action or AC cycling. Bits 5 and 7 are reserved
(p.24). These are not a non-destructive live CV/CC register. One communication
owner must read, timestamp and distribute status observations.

`*ESR?` also clears on read: bit 7 power-on, 5 command error, 4 execution error,
3 verify timeout, 2 query error, 0 operation complete (p.23). `EER?` clears the
last execution error; relevant codes are 0 none, 100 range error, 103 unavailable
second output, 104 command forbidden while output on, and 200 no write privileges
(pp.23-24). Preserve error information before issuing any status-clear command.

## Timing and electrochemical limitations

- Command processing for a voltage step is **typically <25 ms** from terminator
  receipt until voltage begins changing. This is not a guaranteed maximum or a
  complete query-cycle time (p.4). No guaranteed maximum commands/second is given.
- Resistive-load settling, excluding command processing: rising 8 ms for both
  specified ranges; falling at 90% load 80 ms (60 V/7 A) or 10 ms (20 V/20 A);
  falling at no load 1.5 s or 1.2 s respectively. Criterion: within 1% of total
  excursion (p.3). An electrolyser need not have the same response.
- `V<n>V <nrf>` (voltage with verify) can occupy the sequential parser for up to
  **5 s**, with a tolerance of +/-5% or +/-10 counts, whichever is larger.
  A failure sets ESR bit 3 (p.28). Avoid it for normal dual-channel scheduling:
  it can delay both the other channel and an emergency OFF command.
- Proposed ordinary I/O deadlines must be configurable and measured during
  hardware commissioning; no invented timeout or polling limit is a manual fact.
  Host scheduling must include both channels' transport and settling costs.
- A 10 mV step at 5 mV/s requests 2 s per point. A sub-10 mV step is unsupported
  by documented programming resolution. Future UI must warn and require an
  explicit supported choice; record achieved timing as well as requested timing.
- `*TRG` does nothing; no trigger capability exists. `*TST?` always returns zero
  and is not a real self-test (p.31). V and I are separate readback queries, not
  documented simultaneous samples. This instrument supports staircase voltage
  sweeps, not potentiostat LSV or reference-electrode potential control.

## Session and output limitations

- Entering remote mode retains V/I settings, VTRACK and output states, cancels
  S-Lock, and defaults both ranges to 60 V/20 A. Software limits must be enforced
  independently of front-panel custom ranges (p.18).
- Power-up OFF is the factory default, but each output can be configured to
  restore its previous state (pp.11, 26). `*RST` is not a documented all-off
  operation; its defaults include 1 V, 1 A, OVP 66 V and OCP 22 A. Neither reset,
  connection nor return to LOCAL establishes that outputs are off.
- Initial connection readiness should acquire ownership, explicitly switch both
  outputs off, verify both `OP<n>?` replies, then set and check `CONFIG 2` with
  no experiments active. Never repeat that shared initialisation when starting,
  pausing or stopping only one channel. No automatic `*RST` or trip reset.
- No communications watchdog or automatic output-off on PC/USB failure is
  documented. After link loss, software must show output state **unknown** unless
  OFF is confirmed. A GUI emergency button cannot guarantee physical shutdown
  across a failed link; an independent lab shutdown method is required for that
  failure mode. OFF is electronic, and the load remains connected to the output
  stage (p.14).
- The supply sources current and cannot sink it (p.14). Its output capacitor may
  deliver a transient outside the continuous current-limit setting (p.12).
- Remote sensing is selected by physical switches, not a listed remote command.
  Lead drop must not exceed 0.5 V per output lead; sensing affects the relevance
  of readback voltage to actual cell voltage (pp.10, 13, 18).
- This Issue 1 manual describes Windows 2000/XP/Vista/7 USB driver installation
  (p.19). It does not establish Windows 10/11 driver compatibility for the actual
  instrument. Check enumeration and firmware/driver versions on the lab PC.

Future Ethernet transport can retain the same driver command vocabulary:
the manual specifies raw TCP **port 9221**, two socket connections, and
semicolon/LF command separators (p.21). Its description of TCP frame boundaries
does not replace correct stream framing in host code. VXI-11 is for discovery,
not full instrument control (pp.20-21). Ethernet is not implemented here.
