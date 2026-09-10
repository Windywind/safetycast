@echo off
cd /d "%~dp0\.."

REM Build SafetyCast.exe with PyInstaller (onefile, windowed).
REM Output: dist\SafetyCast.exe  (single portable file, no Python needed)
REM Note: startup is 1-3s slower than onedir (self-extract to temp each run).

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY where python.exe >nul 2>nul && set "PY=python"
if not defined PY (
    echo [ERROR] python not found. Run script\setup.bat first.
    pause
    exit /b 1
)

"%PY%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [..] PyInstaller not found, installing ...
    "%PY%" -m pip install pyinstaller
    if errorlevel 1 (
        echo [FAIL] pip install pyinstaller failed
        pause
        exit /b 1
    )
)

"%PY%" -m PyInstaller --noconfirm --clean --windowed --onefile --name SafetyCast --icon app.ico --add-data "app.ico;." broadcast.py
if errorlevel 1 (
    echo [FAIL] PyInstaller build failed
    pause
    exit /b 1
)

echo [OK] dist\SafetyCast.exe ready (single file)
echo      Copy it anywhere; data lives in %%APPDATA%%\SafetyCast\
echo      (old data beside the exe is migrated on first run).
echo      Optional: put a custom app.ico beside it to replace the tray icon.
pause
