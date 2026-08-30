@echo off
REM Double-click this to run the interactive toolkit menu on Windows.
cd /d "%~dp0"
python menu.py
if errorlevel 1 (
    echo.
    echo Something went wrong. If this says "python is not recognized",
    echo you need to install Python from https://www.python.org/downloads/
    echo and make sure "Add Python to PATH" is checked during setup.
    pause
)
