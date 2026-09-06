@echo off
cd /d "%~dp0\.."

REM Resolve pythonw: .venv first, then system PATH, then pyw launcher
set "PYW="
if exist ".venv\Scripts\pythonw.exe" set "PYW=.venv\Scripts\pythonw.exe"
if not defined PYW where pythonw >nul 2>nul && set "PYW=pythonw"
if not defined PYW where pyw >nul 2>nul && set "PYW=pyw"
if not defined PYW (
    echo [ERROR] pythonw not found.
    echo Install Python from https://python.org or use start_debug.bat
    pause
    exit /b 1
)

start "" "%PYW%" broadcast.py
timeout /t 3 /nobreak >nul
tasklist | findstr /i "pythonw.exe pyw.exe" >nul
if errorlevel 1 (
    echo [FAIL] pythonw not running, use start_debug.bat to see error
    pause
) else (
    echo [OK] running, check tray (click ^ arrow for hidden icons)
    timeout /t 5 /nobreak >nul
)
