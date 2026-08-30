@echo off
REM Double-click this ONCE to install the required dependencies on Windows.
cd /d "%~dp0"
echo Installing dependencies (palworld-save-tools, pyooz)...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Install failed. If this says "python is not recognized", you need to
    echo install Python first from https://www.python.org/downloads/
    echo ^(check "Add Python to PATH" during setup^), then run this again.
) else (
    echo.
    echo Done! You can now run run.bat to use the tool.
)
pause
