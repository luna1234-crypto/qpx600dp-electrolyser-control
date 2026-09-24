# QPX600DP model-correction verification

Date: 2026-09-24. Version: 0.4.1. Starting checkpoint: `c47b144`
(Milestone 4). Environment: macOS, CPython 3.12.14, PySide6 / Qt 6.10.1,
pytest 9.0.2.

This is a post-Milestone-4 model correction, not Milestone 5. No Windows desktop,
physical instrument, COM-port enumeration, transport or instrument command was
used.

## Authority and source integrity

| Reference | SHA-256 |
| --- | --- |
| QPX600D & DP Instruction Manual, 48511-1460, Issue 14 | `4b58c7f88aae9f55cd40fc5472a564ea380f58b8af7d99211796e2fe74962770` |
| QPX600 Addendum, 48511-1461, Issue 1 | `5c5766aa33e33264b6b47b038713b6db70cefd4b1f773fd7e4185e26f5df8863` |

The prior CPX400DP manual remains byte-preserved under
`docs/reference/superseded/`, with an explicit prohibition on using it for QPX
implementation. Historical Milestone 1-4 verification text and screenshots were
not rewritten as QPX evidence; each record now points to this correction.

## Corrections verified

| Area | Result |
| --- | --- |
| Canonical package | Renamed to `qpx_electrolyser`; runtime imports and logger namespaces corrected |
| Product identity | Window, About text, CLI description and simulator ID use QPX600DP |
| Voltage ranges | 60 V / 1 mV and 80 V / 2 mV represented independently |
| Current | 0.01-50 A hardware range with 10 mA programming/readback resolution |
| Power | 600 W rating stored as hardware capability, not a safety default |
| Envelope | Conservative manual-anchor segments: 50 A through 10 V, 7.5 A above 10 V through 80 V, with 600 W ceiling |
| Safety policy | Optional `UserSafetyLimits` is a separate type; no limit is inferred by default |
| Addendum | Serial 426599 `LOGIC IN` split recorded; no runtime control added |
| M4 connection | Existing single-owner thread, cleanup/retry and bounded shutdown preserved |

The Issue 14 graph does not provide a complete mathematical formula. No
interpolation, curve fit or `600 / V` assumption was introduced. The conservative
segment can reject hardware-valid intermediate points and is documented as a
limitation rather than an exact curve.

## Automated and GUI results

| Check | Result |
| --- | --- |
| `python -m pytest -q -p no:cacheprovider` | 90 passed in 5.82 s |
| Runtime identity/reference guard | Passed; old package absent and CPX manual present only in the superseded reference location |
| `python main.py` in the Python 3.12 environment | Visible Qt window launched; Simulation Mode connected, identified `SIMULATOR,QPX600DP,SIMULATED,0.4.1`, disconnected, reconnected and closed; exit code 0 |
| `python -m qpx_electrolyser --smoke-test` | Visible window and real Qt event loop processed; exit code 0; worker stopped cleanly |
| Entrypoint subprocess regression tests | Both `main.py` and `python -m qpx_electrolyser` passed through the test suite |
| `python -m compileall -q main.py qpx_electrolyser tests examples` | Passed with bytecode cache redirected to the temporary test area |
| Source whitespace check | `git diff --check` passed |
| Copied reference hashes | Both matched the supplied files |

## Boundaries retained

- No physical QPX600DP driver exists.
- No serial, USB, LAN, GPIB or analogue transport was opened.
- No COM ports were enumerated.
- No remote command was sent.
- No output-control GUI or experiment protocol was added.
- Native Windows and real-hardware validation remain outstanding.
- Work stops at this model-correction checkpoint; Milestone 5 has not started.
