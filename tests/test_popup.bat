@echo off
cd /d "%~dp0"
echo Starting test in 3 seconds...
"..\.venv\Scripts\python.exe" test_popup.py
echo Done.
pause
