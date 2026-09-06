@echo off
cd /d "%~dp0\.."

REM Resolve python: .venv first, then system PATH, then py launcher
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY where python.exe >nul 2>nul && set "PY=python"
if not defined PY where py.exe >nul 2>nul && set "PY=py"
if not defined PY (
    echo [ERROR] Python not found.
    echo Install Python from https://python.org
    pause
    exit /b 1
)

"%PY%" broadcast.py
echo.
echo ========================================
echo Program exited (error above if any)
echo ========================================
pause
