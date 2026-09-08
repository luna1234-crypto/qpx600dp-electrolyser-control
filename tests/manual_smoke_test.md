# Windows visual smoke test (milestone 1)

This procedure checks the offline skeleton only. No power supply is required.

1. Follow the virtual-environment steps in the README.
2. Run `Start_App.cmd` or `.\.venv\Scripts\python.exe main.py`.
3. Confirm a window opens with Instrument, Channel 1 and Channel 2 placeholders.
4. Confirm both output states say unknown, and no operational connection,
   simulation, output or experiment buttons are available.
5. Resize the window and open Help > About. Text should remain readable and the
   application should respond immediately.
6. Close with File > Exit, and launch again using the window's close button to exit.
7. Verify `logs/application_YYYYMMDD.log` contains startup and shutdown records
   from both runs. The first run's records must remain.

Record the Windows edition, Python/PySide6 versions, date and observed result.
This visual Windows check has not been performed in the Linux build workspace.

