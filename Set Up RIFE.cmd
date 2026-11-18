@echo off
setlocal
cd /d "%~dp0"
set "RIFE_VENV=runtime\work-python"
set "RIFE_WORKER=%RIFE_VENV%\Scripts\python.exe"
set "RIFE_PYTHON_CHECK=import struct, sys, tkinter; sys.exit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8 else 1)"


if exist "%RIFE_WORKER%" goto install
if defined RIFE_SETUP_PYTHON goto explicit_python
py -3.12 -c "%RIFE_PYTHON_CHECK%"
if errorlevel 1 goto missing_python
py -3.12 -m venv "%RIFE_VENV%"
if errorlevel 1 goto install_failed
goto install

:explicit_python
"%RIFE_SETUP_PYTHON%" -c "%RIFE_PYTHON_CHECK%"
if errorlevel 1 goto missing_python
"%RIFE_SETUP_PYTHON%" -m venv "%RIFE_VENV%"
if errorlevel 1 goto install_failed

:install
"%RIFE_WORKER%" -c "%RIFE_PYTHON_CHECK%"
if errorlevel 1 goto copied_environment
"%RIFE_WORKER%" -m pip --disable-pip-version-check install --only-binary=:all: -r requirements.txt
if errorlevel 1 goto install_failed
"%RIFE_WORKER%" -c "import tkinter, vapoursynth, onnx, onnxconverter_common"
if errorlevel 1 goto install_failed
echo.
"%RIFE_WORKER%" setup_runtime.py
if errorlevel 1 goto install_failed
echo Setup complete. Double-click "Open RIFE Movie Converter.cmd".
pause
exit /b 0

:missing_python
echo Install 64-bit Python 3.12 with Tcl/Tk and the py launcher, then run setup again.
goto failed

:copied_environment
echo The work-python folder belongs to a different Python installation.
echo Rename runtime\work-python to work-python.old, then run setup again.
goto failed

:install_failed
echo Setup could not finish. Check the error above and your internet connection, then retry.

:failed
pause
exit /b 1
