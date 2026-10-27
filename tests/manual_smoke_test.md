# Windows visual smoke test (milestone 12)

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

## Milestone 12 Voltage Scan checks

With Simulation Mode connected, complete these checks before the final
disconnect/shutdown steps above:

1. In CH1 Voltage Scan enter start `1`, stop `2`, step `0.5`, current limit
   `0.1`, dwell `1` and interval `0.25`. Start and confirm steps `1/3`, `2/3`
   and `3/3` appear in order, the commanded voltage reaches 2 V exactly once,
   plot/measurements update, one CSV closes and the output ends OFF.
2. On CH2 enter start `2`, stop `1`, step `-0.4`, current limit `0.1`, dwell
   `1` and interval `0.25`. Confirm the non-divisible inclusive sequence is
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
   `scan_name`, step index/total and commanded setpoint, and plots use those
   samples without visible extra polling.
7. Keep Simulation Mode selected. The Real Hardware disabled-state and pending
   message are covered by automated widget tests; do not select Real Hardware
   during this manual procedure.

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
6. Confirm both Current Scan Start controls are disabled with the Milestone 13
   acceptance-pending message whenever Real Hardware mode is selected. Do not
   enter or connect a port during this Simulation Mode procedure.

Record the Windows/Python/PySide6 versions and outcome. Real Hardware Current
Scan implementation and acceptance remain pending.
