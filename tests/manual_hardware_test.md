# Deferred QPX600DP hardware acceptance outline

**Not executed in Milestone 5.** The driver and serial transport have only been
tested against fake serial ports. The GUI cannot use them, and the project never
enumerates COM ports. Perform this procedure only as a separately authorised,
supervised commissioning task with an independent physical means of shutdown.

Use only the active QPX references in `../docs/reference/` and the page-cited
`../docs/qpx600dp_commands.md`. Never use the superseded CPX400DP manual.

## Commissioning helper safeguards

The helper requires an explicitly supplied port and defaults to the read-only
queries listed in the driver reference:

```text
python -m qpx_electrolyser.instrument.hardware_check --port COM7
```

It does not discover or select a port. Even queries cause the instrument to enter
remote mode and lock its keyboard according to Issue 14 p. 22, so "read-only"
means no setting/output changes, not no instrument-side mode indication.

Any setting or OFF command requires the exact token
`--confirm-write WRITE-QPX600DP`. Output On is exposed only through a bounded
`--output-on-for` action and additionally requires:

- `--confirm-output-on ENABLE-QPX600DP-OUTPUT`;
- explicit `--set-voltage` and `--set-current` for the selected channel;
- explicit positive `--safety-max-voltage`, `--safety-max-current` and
  `--safety-max-power` values that contain the requested setpoints.

The tool attempts `OP<N> 0` in a `finally` block after the interval. It cannot
guarantee OFF after cable loss, process/OS failure, instrument failure or loss of
PC power. Never use that attempt as the independent shutdown provision.

## Future supervised procedure

When hardware commissioning is explicitly authorised:

1. Record model, serial number, main/interface firmware, Windows version, USB
   driver and chosen interface. Confirm the model field is QPX600DP.
2. Use outputs physically OFF, a separately protected low-power resistive load
   and an independent meter. Do not begin commissioning with an electrolyser.
3. Determine the serial-number branch for `LOGIC IN`: before 426599 uses the old
   current-driven input; 426599 onward uses the protected voltage-driven input
   described by the addendum. Do not test this input without a separate plan.
4. Capture raw `*IDN?` including terminators. Accept and record the third field
   rather than assuming the command-table `0` or the VXI-11 example's serial form
   (Issue 14 pp. 25, 36).
5. The Milestone 5 driver deliberately does not acquire `IFLOCK`. Before adding
   lock handling later, capture and verify `IFLOCK`, `IFLOCK?` and `IFUNLOCK`
   replies in an isolated test because each produces a response (p. 35).
6. With both outputs OFF, query `VRANGE1?` and `VRANGE2?`; record actual power-on
   ranges. Do not assume the simulator's 60 V configuration is a hardware or
   safety default (p. 34).
7. Establish independent mode with outputs OFF and `CONFIG 2`; verify `CONFIG?`
   before any per-channel operation (pp. 32, 34).
8. Program only explicitly approved low voltage/current values. Read back
   setpoints and measurements, recording exact spaces, units, precision and
   CRLF framing rather than assuming a fixed response width (pp. 31, 33).
9. Enable and confirm one output at a time using `OP<N>` / `OP<N>?`; verify the
   sibling remains OFF and compare with the external meter. Always attempt and
   confirm OFF after each check (p. 33).
10. At modest power, check CC/CV crossover on both outputs. Do not use the
    simulator's conservative piecewise envelope as a claim of the exact hardware
    curve, and do not deliberately exceed the manual envelope (pp. 2, 13).
11. Record destructive status-query behaviour and QPX `LSR` bit meanings. A safe
    malformed query may be considered only with outputs OFF and a reviewed plan
    (pp. 29, 35).
12. Measure command/query latency and OFF-confirmation time. Typical values are
    not hard deadlines; with-verify operations can take up to 5 seconds (pp. 4,
    33).
13. Only at later safety/queue milestones, test cancellation, global OFF,
    communications loss and application exit with a separate physical shutdown
    means. Communications loss must leave output state unknown.

This outline is not evidence that any Windows, USB, serial, LAN, GPIB or hardware
test has passed. Milestone 5 evidence is fake/mock serial only. Future reports
must contain actual raw replies, timestamps, loads, external measurements and
pass/fail results.
