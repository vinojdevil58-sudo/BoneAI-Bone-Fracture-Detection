@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run SETUP_VENV.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" create_doctor.py
pause
