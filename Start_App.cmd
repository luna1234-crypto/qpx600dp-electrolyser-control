@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Create the virtual environment and install requirements using README.md first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo The application could not run. Check the message above and the logs folder.
    pause
    exit /b 1
)

