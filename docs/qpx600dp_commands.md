# QPX600DP authoritative capability and command reference

This is the active model reference after the post-Milestone-4 model correction.
It is documentation only: the project still has no physical driver, transport,
COM-port discovery or code that sends an instrument command.

## Authoritative sources

- `reference/qpx600d_dp_instruction_manual_48511-1460_issue14.pdf` - document
  48511-1460, Issue 14; SHA-256
  `4b58c7f88aae9f55cd40fc5472a564ea380f58b8af7d99211796e2fe74962770`.
- `reference/qpx600_addendum_48511-1461_issue1.pdf` - document 48511-1461,
  Issue 1; SHA-256
  `5c5766aa33e33264b6b47b038713b6db70cefd4b1f773fd7e4185e26f5df8863`.
- The old CPX400DP Issue 1 manual is retained under `reference/superseded/`
  only to preserve history. It must not be used for QPX600DP implementation.

Page references below are the page numbers printed in the QPX manual. The PDF
page number is one greater because the cover is unnumbered. If the addendum and
main manual differ, the addendum is the correction.

## Hardware capabilities - not software safety limits

| Capability | QPX600DP value | Source |
| --- | --- | --- |
| Outputs | 2 independent outputs | Issue 14 pp. 2, 4 |
| Voltage ranges | 0-60 V and 0-80 V, independently selectable per output | Issue 14 pp. 2, 11, 33 |
| Voltage setting resolution | 1 mV in 60 V range; 2 mV in 80 V range | Issue 14 pp. 2, 11 |
| Current range | 0.01-50 A | Issue 14 pp. 2, 12 |
| Current setting resolution | 10 mA | Issue 14 pp. 2, 12 |
| Power | Up to 600 W per output, subject to the PowerFlex envelope | Issue 14 pp. 2, 13 |
| Voltage meter resolution | 1 mV in the specification table; p. 11 states actual values use 2 mV in the 80 V range | Issue 14 pp. 3, 11 |
| Current meter resolution | 10 mA | Issue 14 p. 3 |
| OVP | 2-90 V, 0.1 V resolution, typical response 100 us | Issue 14 p. 2 |
| OCP | 2-55 A, 0.1 A resolution, typical response 100 ms | Issue 14 p. 2 |
| Digital interfaces | RS232, USB, LAN; GPIB optional | Issue 14 pp. 4, 21-26 |
| Analogue interface | Per-output control and monitoring | Issue 14 pp. 3, 9-10, 21 |
| Remote command processing | Voltage change begins typically within 100 ms of receiving the terminator | Issue 14 p. 4 |

These are physical ratings. They are not defaults for an electrolyser experiment.
The code represents optional user safety limits separately; no user safety limit
is inferred from 80 V, 50 A or 600 W.

### Conservative simulator power envelope

Issue 14 p. 13 states that the guaranteed envelope gives 80 V / 7.5 A and
10 V / 50 A under all supply conditions and that lower voltages are restricted
by the 50 A maximum. It shows the rest as a graph but gives no complete equation.

The simulator therefore uses only these documented segments:

- 0-10 V inclusive: at most 50 A;
- above 10 V through 80 V: at most 7.5 A;
- always at most 600 W.

There is deliberately no interpolation or `600 / V` assumption. This conservative
model may reject points the real instrument can regulate between the two anchors.
It must not be described as the exact hardware curve. Hardware commissioning may
record behaviour but must not silently broaden this documented software model.

## Framing and sequencing

| Behaviour | Requirement | Source |
| --- | --- | --- |
| Command encoding | ASCII, case-insensitive; high bit ignored | Issue 14 pp. 31-32 |
| Command terminator | LF (`0x0A`) | Issue 14 p. 31 |
| Response terminator | CRLF (`0x0D 0x0A`) for RS232/USB/LAN | Issue 14 p. 31 |
| Group separator | Semicolon; the group ends with LF | Issue 14 p. 31 |
| Execution | Sequential; the parser waits for the prior command/query | Issue 14 pp. 31-32 |
| RS232 queue | 256 bytes, XOFF near 200 queued characters and XON after about 100 spaces free | Issue 14 p. 31 |
| LAN queue | 1500 bytes | Issue 14 p. 31 |
| With-verify timeout | Up to 5 seconds; timeout sets ESR bit 3 | Issue 14 p. 33 |

USB is a virtual COM interface. RS232 is fixed at 9600 baud, 8 data bits,
1 stop bit, no parity; USB ignores the baud setting (Issue 14 pp. 21-23).
LAN raw socket service uses TCP port 9221 and permits two simultaneous connections
(Issue 14 p. 25). None of these transports is implemented in this correction.

## Command catalogue

Every command below carries its source page. This catalogue is the only allowed
starting point for a future driver. A driver must attach the same page reference
to each implemented command or its command-registry entry.

### Output and setpoint commands

| Command | Meaning | Source |
| --- | --- | --- |
| `V<N> <NRF>` / `V<N>V <NRF>` | Set voltage, optionally with verify | Issue 14 p. 33 |
| `I<N> <NRF>` | Set current limit | Issue 14 p. 33 |
| `OVP<N> <NRF>` / `OCP<N> <NRF>` | Set protection trip levels | Issue 14 p. 33 |
| `DAMPING<N> <NRF>` | Current-meter averaging, 0 off / 1 on | Issue 14 p. 33 |
| `V<N>?` / `I<N>?` | Query voltage/current setpoints | Issue 14 p. 33 |
| `OVP<N>?` / `OCP<N>?` | Query trip settings | Issue 14 p. 33 |
| `V<N>O?` / `I<N>O?` | Query measured output voltage/current | Issue 14 p. 33 |
| `DELTAV<N> <NRF>` / `DELTAI<N> <NRF>` | Set step sizes | Issue 14 p. 33 |
| `DELTAV<N>?` / `DELTAI<N>?` | Query step sizes | Issue 14 p. 33 |
| `INCV<N>` / `INCV<N>V` / `DECV<N>` / `DECV<N>V` | Step voltage, optionally with verify | Issue 14 p. 33 |
| `INCI<N>` / `DECI<N>` | Step current limit | Issue 14 p. 33 |
| `OP<N> <NRF>` / `OP<N>?` | Set/query one output, 0 off / 1 on | Issue 14 p. 33 |
| `OPALL <NRF>` | Switch both outputs together | Issue 14 p. 34 |
| `SENSE<N> <NRF>` | Select local or remote sense | Issue 14 p. 34 |
| `VRANGE<N> <NRF>` / `VRANGE<N>?` | Set/query 60 V range code 1 or 80 V range code 2 | Issue 14 p. 34 |

### Linking, configuration and storage

| Command | Meaning | Source |
| --- | --- | --- |
| `RATIO <NRF>` / `RATIO?` | Voltage-tracking ratio, valid only in mode 0 | Issue 14 p. 34 |
| `CONFIG <NRF>` / `CONFIG?` | Set/query operating mode | Issue 14 pp. 32, 34 |
| `SAV<N> <NRF>` / `RCL<N> <NRF>` | Save/recall output setup slot 0-9 | Issue 14 p. 34 |

`CONFIG` values are 0 voltage tracking, 2 independent, 3 linked voltage display,
and 4 linked current display. Mode 1 is reserved and cannot be selected; `-1` is
reported while analogue control is active (Issue 14 p. 32). The old CPX command
`TRIPCONFIG` is not in the QPX600DP Issue 14 command list and must not be sent.

### Status and system commands

| Command | Meaning | Source |
| --- | --- | --- |
| `*CLS` | Clear status structure | Issue 14 p. 34 |
| `*ESE <NRF>` / `*ESE?` | Set/query Event Status Enable | Issue 14 p. 34 |
| `*ESR?` | Query and clear Event Status Register | Issue 14 p. 34 |
| `*IST?` | Query IEEE 488.2 local message | Issue 14 p. 34 |
| `*OPC` / `*OPC?` | Set/query operation complete | Issue 14 pp. 33-34 |
| `*PRE <NRF>` / `*PRE?` | Set/query Parallel Poll Enable | Issue 14 p. 35 |
| `*SRE <NRF>` / `*SRE?` | Set/query Service Request Enable | Issue 14 p. 35 |
| `*STB?` | Query Status Byte | Issue 14 p. 35 |
| `*WAI` | Wait command; no-op because execution is sequential | Issue 14 p. 35 |
| `*RST` | Restore factory defaults except remote-interface settings | Issue 14 pp. 18, 35 |
| `TRIPRST` | Attempt to clear all trips | Issue 14 p. 35 |
| `EER?` | Query and clear Execution Error Register | Issue 14 pp. 29, 35 |
| `LSR<N>?` | Query and clear Limit Status Register | Issue 14 pp. 29, 35 |
| `LSE<N> <NRF>` / `LSE<N>?` | Set/query Limit Status Enable | Issue 14 pp. 29, 35 |
| `QER?` | Query and clear Query Error Register | Issue 14 pp. 29, 35 |

`LSR` bits are: bit 0 CV, bit 1 CC, bit 2 power limit, bit 3 OVP,
bit 4 OCP, bit 5 sense trip, bit 6 fault requiring AC power cycling and bit 7
reserved (Issue 14 p. 29). Destructive status queries must never be treated as
non-destructive polling.

Execution errors are 0 none, 100 range error, 101 corrupted recall data,
102 empty recall store, 103 command invalid in the present circumstances and
200 read-only/no authority (Issue 14 p. 29).

### Interface and identification commands

| Command | Meaning | Source |
| --- | --- | --- |
| `LOCAL` | Return to local control until a later command | Issue 14 p. 35 |
| `LOCALLOCKOUT <NRF>` | Lock/unlock keyboard; firmware before 4.22 may reject it | Issue 14 p. 35 |
| `IFLOCK` / `IFLOCK?` / `IFUNLOCK` | Acquire, query and release interface lock | Issue 14 p. 35 |
| `ADDRESS?` | Query bus address | Issue 14 p. 35 |
| `IPADDR?` / `NETMASK?` / `NETCONFIG?` | Query LAN configuration | Issue 14 pp. 35-36 |
| `NETCONFIG <CPD>` / `IPADDR <quad>` / `NETMASK <quad>` | Set next-boot LAN parameters | Issue 14 p. 36 |
| `*IDN?` | Query identification | Issue 14 p. 36; VXI-11 example p. 25 |
| `*TST?` | Always returns 0; no self-test capability | Issue 14 p. 36 |
| `*TRG` | Ignored; no trigger capability | Issue 14 p. 36 |

The `*IDN?` table describes the third field as `0`, while the VXI-11 discovery
example uses a serial number. A future parser must retain an arbitrary third
field rather than enforce either form. The simulator intentionally returns
`SIMULATOR,QPX600DP,SIMULATED,<version>` and does not claim to emulate a physical
identity response byte-for-byte.

## Addendum: logic input serial-number split

The addendum applies a serial-number boundary omitted from the Issue 14 wording:

- before serial 426599: current-driven input, activation above about 1 mA, no
  820 ohm series resistor;
- serial 426599 onward: voltage-driven input, 820 ohm series resistor, activates
  above about 3.3 V, maximum +20 V and no more negative than -3 V.

This application does not control `LOGIC IN`. The distinction belongs in future
hardware commissioning and must remain unresolved until the target serial number
is recorded (Addendum 48511-1461 Issue 1, p. 1).

## Implementation boundary

The current executable implements only the generic synchronous backend API and
Simulation Mode lifecycle. It does not implement any ASCII remote command listed
above. No QPX600DP physical driver may be added as part of this model-correction
checkpoint.
