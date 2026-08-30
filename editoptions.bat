@echo off
REM Double-click this to jump straight to the world-settings editor on Windows
REM (skips the toolkit menu). Use run.bat if you want the full menu instead.
cd /d "%~dp0"
python optioneditor.py
if errorlevel 1 (
    echo.
    echo Something went wrong. If this says "python is not recognized",
    echo you need to install Python from https://www.python.org/downloads/
    echo and make sure "Add Python to PATH" is checked during setup.
    pause
)
