@echo off
cd /d "%~dp0"
python tools\gameui.py
if errorlevel 1 pause
