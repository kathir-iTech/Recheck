@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" serve.py --open
) else if exist "..\qnx-modbus-venv\Scripts\python.exe" (
    "..\qnx-modbus-venv\Scripts\python.exe" serve.py --open
) else (
    python serve.py --open
)
if errorlevel 1 pause
