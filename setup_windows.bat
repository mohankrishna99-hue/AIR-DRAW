@echo off
setlocal EnableDelayedExpansion

title AirDraw - Windows Setup & Launcher
echo ============================================================
echo   AirDraw - Hand Gesture Controlled Virtual Canvas
echo   Author: MOHANA KRISHNA A S
echo ============================================================
echo.

:: Change working directory to the folder containing this batch script
cd /d "%~dp0"

:: 1. Detect Python command (py launcher or direct python)
set PYTHON_CMD=

py -c "import sys; print(sys.version)" >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=py
    goto python_found
)

python -c "import sys; print(sys.version)" >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=python
    goto python_found
)

:: Python not found
echo [ERROR] Python is not installed or not in your system PATH.
echo.
echo Please download and install Python from:
echo   https://www.python.org/downloads/
echo.
echo Make sure to check the box "Add python.exe to PATH" during installation.
echo.
pause
exit /b 1

:python_found
echo [OK] Python detected using '%PYTHON_CMD%'
echo.

:: 2. Upgrade pip and install / verify dependencies
echo Installing / verifying required libraries from requirements.txt...
echo.
%PYTHON_CMD% -m pip install --upgrade pip
%PYTHON_CMD% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to install required packages.
    echo Please check your internet connection and try again.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Dependencies verified successfully!
echo ============================================================
echo.

:: 3. Ask user if they want to launch the application now
set /p LAUNCH="Do you want to launch AirDraw right now? (Y/N, default is Y): "
if /i "%LAUNCH%"=="N" (
    echo.
    echo You can start AirDraw anytime by running:
    echo   py airdraw.py
    echo or double clicking this setup_windows.bat file.
    echo.
    pause
    exit /b 0
)

echo.
echo Launching AirDraw...
echo (Press 'Q' inside the camera window to exit)
echo.
%PYTHON_CMD% airdraw.py

pause
exit /b 0
