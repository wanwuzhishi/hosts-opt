@echo off
setlocal
cd /d "%~dp0"

where pythonw.exe >nul 2>nul && (
    start "" pythonw.exe "%~dp0hosts_opt_gui.py"
    exit /b 0
)

where pyw.exe >nul 2>nul && (
    start "" pyw.exe -3 "%~dp0hosts_opt_gui.py"
    exit /b 0
)

echo Python 3.10+ not found. Install Python or add pythonw.exe/pyw.exe to PATH.
pause
exit /b 1
