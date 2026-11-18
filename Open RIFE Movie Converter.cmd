@echo off
cd /d "%~dp0"
if not exist "%~dp0runtime\work-python\Scripts\pythonw.exe" (
    echo Run "Set Up RIFE.cmd" first. See SETUP.md for the requirements.
    pause
    exit /b 1
)
start "" "%~dp0runtime\work-python\Scripts\pythonw.exe" "%~dp0app.py"
