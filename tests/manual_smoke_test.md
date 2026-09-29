# Windows visual smoke test (milestone 7)

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
10. Disconnect and confirm both channel panels become disabled with output state
    `UNKNOWN`. Resize the window and open Help > About. Text should remain readable
    and the application should respond immediately.
11. Close with File > Exit, and launch again using the window's close button to exit.
12. Verify `logs/application_YYYYMMDD.log` contains startup, connection,
   disconnection, worker cleanup and shutdown records
   from both runs. The first run's records must remain.

Record the Windows edition, Python/PySide6 versions, date and observed result.
The historical Milestone 4 visual check used the earlier CPX model identity.
This Milestone 7 procedure is Simulation Mode only: do not select Real Hardware,
enter a port, invoke the hardware checker or connect the GUI to a physical supply.
