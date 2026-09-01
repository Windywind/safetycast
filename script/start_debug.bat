@echo off
cd /d "%~dp0\.."
".venv\Scripts\python.exe" broadcast.py
echo.
echo ========================================
echo Program exited (error above if any)
echo ========================================
pause
