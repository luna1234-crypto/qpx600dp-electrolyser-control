# Planned CPX400DP hardware acceptance

**For milestone 5 and later, once a driver and connection worker exist.**
The current skeleton cannot run these checks. No hardware has been exercised.
Use the [supplied manual](../docs/reference/cpx400dp_manual_issue1.pdf) and record
the actual results instead of pre-filling PASS.

1. Record model, serial, main/interface firmware, Windows version, USB driver,
   COM port and chosen interface settings. Confirm the unit is a CPX400DP.
2. Begin with outputs OFF, an appropriate low-power resistive test load and a
   meter. Do initial driver commissioning without an electrolyser attached.
3. Check USB enumeration in Device Manager. Query `*IDN?`; retain the raw reply
   including CRLF. Confirm the model field and version format.
4. Acquire `IFLOCK`, consume its reply, and confirm `IFLOCK?` is 1. A denied lock
   must stop commissioning rather than override another controller.
5. Explicitly command each output OFF and verify `OP1?` / `OP2?` return 0.
   With CH2 OFF set `CONFIG 2`, then verify `CONFIG?` returns 2. Record error
   registers before clearing them. Never use reset as an OFF substitute.
6. With outputs OFF, program a suitably low test point such as 1.00 V and
   0.100 A. Read back the setpoints. Record exact channel-prefix spacing, units,
   numerical precision and line endings rather than assuming a fixed width.
7. Under a supervised test, enable CH1 only. Compare measured readbacks and
   the external meter within the manual's accuracy/operating conditions. Confirm
   CH2 remains OFF; command and confirm CH1 OFF. Repeat for CH2.
8. With suitable loads, exercise CV and CC crossover at modest power. Confirm
   normal current limiting and OCP are distinct. Check both channels, then
   operate both together and stop one without changing the other's settings.
9. Check error parsing with a safe malformed query while outputs are OFF.
   Record `*ESR?` / `EER?`; verify clearing behaviour. Do not test unsafe physical
   setpoints or deliberately exceed the power envelope.
10. Measure query and setup latency, measurement update behaviour, voltage
    settling and OFF confirmation time with one and two active channels.
    Determine usable polling/timeout settings. Extra digits or faster replies
    alone do not prove higher measurement resolution or fresh samples.
11. In a supervised low-power test with a separate means of shutdown, test a
    disconnected USB cable. Confirm software reports ERROR and unconfirmed
    output state; record whether the real supply stays energised. Restore OFF
    physically when communication cannot do so. Do not expect a documented
    watchdog or automatic output-off.
12. At the later queue/safety milestones, test stale queued ON cancellation,
    pause, independent Stop, global emergency, disconnect and application exit.
    Confirm both OFF replies on shared shutdown, release the lock while
    consuming its response, and close the port.

This is an acceptance checklist, not evidence that Windows, hardware, long-run
logging or emergency latency has passed. Record pass/fail, raw responses,
timestamps, loads and measured values for each step in the commissioning report.
