@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3 and tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)
python tools\lickey_ui.py
if errorlevel 1 pause
