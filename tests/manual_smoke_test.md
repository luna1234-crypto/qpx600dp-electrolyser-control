# Windows visual smoke test (milestone 10)

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
15. Scroll to the CH1 and CH2 live plots. While both loggers run, confirm both
    plots update independently, the legends distinguish Voltage (V) and Current
    (A), elapsed seconds increase, and latest values match the channel panels.
16. Run a protocol without standalone logging, Pause and wait. Confirm its plot
    receives the authoritative Output-Off sample and then stops changing while
    paused. Resume and confirm the same trace continues; Stop and normal
    completion must leave the GUI responsive.
17. Run both channel protocols together and confirm both plots update. Clear CH1
    while CH2 continues; confirm only CH1 display history resets and both CSVs
    remain readable and continue receiving rows.
18. Disconnect while CH2 logs. Confirm CH2 logging stops, its file closes, both
    plots retain their history and show disconnected, and
    reconnect does not resume it. Start again and confirm a new file is created.
    Confirm new plot samples form a separate segment after reconnect.
19. Disconnect and confirm both channel panels and protocol Start controls become
    disabled with output state
    `UNKNOWN`. Resize the window and open Help > About. Text should remain readable
    and the application should respond immediately.
20. Close with File > Exit while a simulated protocol is active; confirm Output
    Off cleanup occurs, its CSV remains readable and no worker/thread warning
    appears. Launch again and use the window's close button to exit.
21. Close with File > Exit while a simulated logger is active; confirm the CSV
    remains readable and no worker/thread warning appears. Launch again and use
    the window's close button to exit.
22. Verify `logs/application_YYYYMMDD.log` contains startup, connection,
   disconnection, worker cleanup and shutdown records
   from both runs. The first run's records must remain.

Record the Windows edition, Python/PySide6 versions, date and observed result.
The historical Milestone 4 visual check used the earlier CPX model identity.
This Milestone 10 procedure is Simulation Mode only: do not select Real Hardware,
enter a port, invoke the hardware checker or connect the GUI to a physical supply.
