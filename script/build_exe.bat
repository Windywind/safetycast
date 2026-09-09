@echo off
cd /d "%~dp0\.."

REM Build SafetyCast.exe with PyInstaller (onedir, windowed).
REM Output: dist\SafetyCast\  (portable folder, config.json + app.ico copied in)

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

"%PY%" -m PyInstaller --noconfirm --clean --windowed --name SafetyCast --icon app.ico broadcast.py
if errorlevel 1 (
    echo [FAIL] PyInstaller build failed
    pause
    exit /b 1
)

REM config.json / log/ resolve next to the exe when frozen (see BASE_DIR in
REM broadcast.py), so ship the icon and current config beside it.
copy /y app.ico "dist\SafetyCast\" >nul
if exist config.json copy /y config.json "dist\SafetyCast\" >nul

echo [OK] dist\SafetyCast\SafetyCast.exe ready
echo      Double-click to run; enable autostart in Settings if needed.
pause
