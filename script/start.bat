@echo off
cd /d "%~dp0\.."
start "" ".venv\Scripts\pythonw.exe" broadcast.py
timeout /t 3 /nobreak >nul
tasklist | findstr /i "pythonw.exe" >nul
if errorlevel 1 (
    echo [FAIL] pythonw not running, use start_debug.bat to see error
    pause
) else (
    echo [OK] running, check tray (click ^ arrow for hidden icons)
    timeout /t 5 /nobreak >nul
)
