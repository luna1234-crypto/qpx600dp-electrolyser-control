# Windows visual smoke test (milestone 12)

## Milestone 15 Continuous Stage Handoff (Simulation Mode)

1. Connect in Simulation Mode and add CH1 stages: CV 1.0 V / 0.08 A / 5 s,
   CC 0.03 A / 4.0 V / 5 s, then CV 2.0 V / 0.08 A / 5 s. Use a valid sample
   interval, select **Continuous handoff**, and leave settling at 1.0 s.
2. Start once. Confirm the output remains ON across both boundaries, the GUI
   shows `Continuous handoff settling`, stages advance exactly 1→2→3, and the
   final output is OFF.
3. Inspect the one raw CSV: each boundary has started/settling/completed evidence,
   no ordinary `ok` sample is marked settling, and no artificial OFF/zero row was
   added merely to mark the boundary.
4. Run CH2 simultaneously with a short CC/CV plan and confirm channel isolation.
5. In a separate run, Pause and Resume during a stage; confirm the existing
   guarded OFF/ON behavior, CSV continuity and final OFF.
6. Repeat with **Safe Off between stages** and confirm the established default
   behavior is unchanged.

This procedure checks the simulation connection GUI. Do not connect a power supply.

1. Follow the virtual-environment steps in the README.
2. Run `Start_App.cmd` or `.\.venv\Scripts\python.exe main.py`.
3. Confirm a window opens with Instrument connection, Channel 1 and Channel 2.
4. Confirm Simulation Mode is checked, status is Disconnected, Connect is enabled,
   Disconnect is disabled and the instrument identification is Unavailable.
5. Click Connect. Confirm the window remains responsive, status becomes Connected,
   and identification begins `SIMULATOR,QPX600DP,SIMULATED`. Confirm Connect and
   Simulation Mode are disabled while Disconnect is enabled.
6. Click Disconnect. Confirm status returns to Disconnected and identification
   returns to Unavailable. Keep Simulation Mode selected; do not select Real
   Hardware or enter a port during this smoke test.
7. Confirm both channel panels populate programmed voltage, current limit,
   measured voltage/current, voltage range and `OUTPUT OFF` after their initial
   read-back. Confirm each panel can be refreshed independently.
8. Enter a valid voltage and current limit on one channel. Confirm the other
   channel does not change. Confirm invalid, negative, non-finite, out-of-range
   and off-grid values are rejected without changing displayed read-back.
9. Click Output On and choose No. Confirm the output remains OFF. Click again,
   choose Yes, confirm only that simulated output displays `OUTPUT ON`, then use
   Output Off and confirm `OUTPUT OFF`.
10. In CH1 Constant Current, enter 0.1 A, 2 V, 1.0 s and 0.1 s. Start and
    confirm STARTING, RUNNING, STOPPING and COMPLETED appear, measurements update,
    elapsed increases, remaining decreases, simulated output returns OFF and a
    readable protocol CSV closes.
11. Repeat normal completion on CH2 and confirm CH1 does not change.
12. Start both channels together for 2 s at 0.1 s intervals. Confirm both RUNNING,
    the GUI remains responsive, samples serialize, and two independent files close.
13. Start CH1 for 2 s, Pause after a sample, confirm PAUSED and OUTPUT OFF, wait
    at least 0.5 s and confirm active elapsed time stays fixed. Resume, confirm
    RUNNING/OUTPUT ON, then Stop early and confirm STOPPING, OUTPUT OFF and a
    closed CSV. Confirm CH2 remains unaffected.
13a. Select Current density for CH1, enter `0.05 A/cm²` and active area `2 cm²`,
    and confirm the preview is `0.1 A`. Start, Pause/Resume and complete the run;
    verify its CSV contains `current_density`, area `2`, density `0.05`, and
    nonblank measured power. Repeat independently on CH2.
14. Choose a writable CSV output directory and set a short interval such as
    0.25 seconds. Start CH1 logging; confirm only CH1 shows increasing samples.
    Confirm CH1 Constant Current Start remains enabled. Run, Pause, Resume and Stop
    a short CH1 protocol; confirm logging stays active and the protocol uses the
    same file with `constant_current` protocol name/state rows. Start CH2 logging
    and confirm both counters increase while the window remains responsive. Stop
    CH1 logging and confirm CH2 continues. Open both CSV files and confirm one
    header plus incrementally written rows for the correct channel.
15. In CH1 Constant Voltage, enter 2 V, 0.1 A, 1.0 s and 0.1 s. Start and
    confirm STARTING, RUNNING, STOPPING and COMPLETED, live plot updates,
    `constant_voltage` CSV rows and final OUTPUT OFF.
16. Repeat on CH2, then run both Constant Voltage protocols simultaneously.
    Confirm channel independence, responsive plots and separate/safely shared
    channel CSV ownership. Confirm Constant Current Start is disabled only on a
    channel currently owned by Constant Voltage.
17. Run CH1 Constant Voltage for 2 s; Pause after a sample and confirm PAUSED and
    OUTPUT OFF with frozen active time. Resume, then Stop early and confirm
    authoritative OUTPUT OFF. Keep a manually started CH1 logger active throughout
    and confirm it remains active with one writer and one file after protocol Stop.
18. Scroll to the CH1 and CH2 live plots. While both loggers run, confirm both
    plots update independently, the legends distinguish Voltage (V) and Current
    (A), elapsed seconds increase, and latest values match the channel panels.
19. Run a protocol without standalone logging, Pause and wait. Confirm its plot
    receives the authoritative Output-Off sample and then stops changing while
    paused. Resume and confirm the same trace continues; Stop and normal
    completion must leave the GUI responsive.
20. Run both channel protocols together and confirm both plots update. Clear CH1
    while CH2 continues; confirm only CH1 display history resets and both CSVs
    remain readable and continue receiving rows.
21. Disconnect while CH2 logs. Confirm CH2 logging stops, its file closes, both
    plots retain their history and show disconnected, and
    reconnect does not resume it. Start again and confirm a new file is created.
    Confirm new plot samples form a separate segment after reconnect.
22. Disconnect and confirm both channel panels and protocol Start controls become
    disabled with output state
    `UNKNOWN`. Resize the window and open Help > About. Text should remain readable
    and the application should respond immediately.
23. Close with File > Exit while a simulated protocol is active; confirm Output
    Off cleanup occurs, its CSV remains readable and no worker/thread warning
    appears. Launch again and use the window's close button to exit.
24. Close with File > Exit while a simulated logger is active; confirm the CSV
    remains readable and no worker/thread warning appears. Launch again and use
    the window's close button to exit.
25. Verify `logs/application_YYYYMMDD.log` contains startup, connection,
   disconnection, worker cleanup and shutdown records
   from both runs. The first run's records must remain.

## Milestone 12 Voltage Sweep / Staircase LSV checks

With Simulation Mode connected, complete these checks before the final
disconnect/shutdown steps above:

1. In CH1 Staircase LSV enter start `1`, end `2`, step `500` mV, rate `500`
   mV/s, current limit `3.5` A, settling `0.25` s, measurements per point `2`,
   interval `0.25` s and optional active area `2` cm². Confirm the derived
   duration is 1 s and steps `1/3`, `2/3`, `3/3` appear in order.
2. On CH2 enter start `2`, end `1`, step `-400` mV, rate `400` mV/s,
   current limit `3.5` A, settling `0.25` s, measurements `2` and interval
   `0.25` s. Confirm the non-divisible inclusive sequence is
   2.0, 1.6, 1.2 and 1.0 V, without an overshoot or duplicate endpoint.
3. Start both channels together with short scans. Confirm both progress while
   the GUI remains responsive, their files and plots remain independent, and
   the shared queue never corrupts a readback.
4. Pause CH1 during a dwell. Confirm selected output OFF and active elapsed time
   freezes. Resume and confirm the same step continues before the next step;
   neither the current nor following step may be duplicated or skipped.
5. Stop CH1 early while CH2 continues. Confirm CH1 ends OFF and closes/detaches
   its scan file without changing CH2.
6. Start same-channel standalone logging, then a scan. Confirm the scan reuses
   that one CSV, logging remains active after scan completion, scan rows contain
   `scan_name`, step index/total, commanded setpoint, scan rate, mV step,
   duration, settling and measurement-count metadata.
7. Confirm raw pre-settling samples remain in CSV, one `lsv_point` row exists
   per step, I-V updates for both channels, and CH1 also displays j-V. Clear the
   LSV display and confirm time plots/CSV are unchanged.
8. Keep Simulation Mode selected. Revised Real Hardware Staircase LSV acceptance
   remains pending; do not select Real Hardware during this procedure.

Record the Windows edition, Python/PySide6 versions, date and observed result.
The historical Milestone 4 visual check used the earlier CPX model identity.
This Milestone 12 procedure is Simulation Mode only: do not select Real Hardware,
enter a port, invoke a hardware tool or access a physical supply.

## Milestone 13 Current Scan checks

Keep Simulation Mode connected and use only simulator-safe values:

1. In CH1 Current Scan enter start `0.1`, stop `0.3`, step `0.1`, voltage
   compliance `2`, dwell `1` and interval `0.25`. Confirm steps `1/3` through
   `3/3` appear once in order, CSV/plot measurements update and completion ends
   with simulated Output Off.
2. In CH2 enter start `0.3`, stop `0.1`, step `-0.08`, voltage compliance `2`,
   dwell `1` and interval `0.25`. Confirm the inclusive sequence is `0.30`,
   `0.22`, `0.14`, `0.10 A` without overshoot or a duplicate endpoint.
3. Run CH1 and CH2 together. Confirm independent state, CSV and plots while the
   shared queue keeps the GUI responsive and readbacks coherent.
4. Pause one channel during a dwell, confirm its output is OFF and active time
   freezes, then Resume. Confirm the same step continues and no step is skipped
   or repeated. Stop it early while the other channel continues unaffected.
5. Repeat with same-channel standalone logging already active. Confirm one CSV
   writer is reused, `scan_name` is `current_scan`, commanded current is stored
   in `scan_commanded_setpoint`, and protocol completion leaves the manual logger
   active.
5a. Select Current density, area `2 cm²`, start `0.05`, stop `0.15` and step
   `0.05 A/cm²`. Confirm the preview is `0.1 → 0.3 A; step 0.1 A`, the ampere
   steps execute once in order, and each CSV step retains its density metadata.
6. Keep Simulation Mode selected. The guarded Real Hardware Current Scan path is
   covered by fake serial and remains pending supervised physical acceptance.
   Do not enter or connect a port during this Simulation Mode procedure.

Record the Windows/Python/PySide6 versions and outcome. Real Hardware Current
Scan physical acceptance remains pending.

## Milestone 14 Steady-State Polarisation checks

Keep Simulation Mode connected. For CH1 use area `2 cm²`, density `0.05 → 0.15`
with increment `0.05 A/cm²`, compliance `2 V`, dwell `1 s`, interval `0.1 s`
and final averaging window `0.3 s`. Use the reverse plan on CH2. Confirm derived
ampere plans, independent simultaneous progress, one summary row per completed
step, raw CSV continuity, the exact companion path in each panel, and bounded
mean-V-versus-j plots. Open each companion while the run is active and confirm
completed rows are already visible and its name ends in
`_polarisation_summary.csv`.

Pause CH1 during a dwell and confirm simulated Output Off and frozen active time;
Resume and confirm the same step completes without duplication. Stop a later
partial step and confirm it produces no completed summary row while CH2 continues.
Both outputs must finish OFF. This is power-supply polarisation, not potentiostat
control. Do not select Real Hardware: supervised physical acceptance is pending.

## Milestone 15 Custom Sequence checks

Keep Simulation Mode connected. In CH1 build Constant Current (`0.10 A`, `2 V`,
`2 s`, `0.25 s`), Wait / Output-Off (`1 s`) and Constant Voltage (`1.8 V`,
`0.20 A`, `2 s`, `0.25 s`). In CH2 build Constant Voltage (`1.5 V`, `0.20 A`,
`2 s`, `0.25 s`) followed by Constant Current (`0.15 A`, `2 V`, `2 s`,
`0.25 s`). Exercise add, duplicate, reorder and remove before restoring these
exact plans.

Start both once. Pause CH1 during its first active stage and confirm CH1 alone
is OFF, active time freezes and CH2 continues. Resume CH1 and confirm the same
stage continues. Verify each stage advances once, Wait remains OFF, one raw CSV
covers each channel run and sequence metadata matches the displayed order. Run
a separate longer plan, Stop during its first stage and confirm no later stage
starts. Disconnect and close only after both simulated outputs are OFF and no
worker warning appears.

Do not select Real Hardware: M15 physical acceptance is pending. Custom
Sequences do not approve unattended electrolyser operation.
