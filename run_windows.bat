@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run SETUP_VENV.bat first to create the environment and install packages.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" app.py
pause
