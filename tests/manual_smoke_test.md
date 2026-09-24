# Windows visual smoke test (milestone 4)

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
   returns to Unavailable. Uncheck Simulation Mode and confirm Connect is disabled
   with a message that the offline-tested physical driver is not exposed by the GUI.
7. Confirm both channel outputs remain unknown and that no setpoint, measurement,
   output or experiment controls exist.
8. Resize the window and open Help > About. Text should remain readable and the
   application should respond immediately.
9. Close with File > Exit, and launch again using the window's close button to exit.
10. Verify `logs/application_YYYYMMDD.log` contains startup, connection,
   disconnection, worker cleanup and shutdown records
   from both runs. The first run's records must remain.

Record the Windows edition, Python/PySide6 versions, date and observed result.
The historical Milestone 4 visual check used the earlier CPX model identity.
The corrected QPX600DP build still requires a native Windows visual check; no
physical supply should be connected for this procedure.
