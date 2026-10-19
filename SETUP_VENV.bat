@echo off
setlocal
cd /d "%~dp0"
echo ========================================
echo BoneAI - Create Windows Virtual Environment
echo ========================================
set "PYTHON_CMD="
where py >nul 2>nul
if %errorlevel%==0 (
    py -3.13 --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3.13"
    if not defined PYTHON_CMD (
        py -3.12 --version >nul 2>nul
        if not errorlevel 1 set "PYTHON_CMD=py -3.12"
    )
    if not defined PYTHON_CMD (
        py -3.11 --version >nul 2>nul
        if not errorlevel 1 set "PYTHON_CMD=py -3.11"
    )
    if not defined PYTHON_CMD (
        py --version >nul 2>nul
        if not errorlevel 1 set "PYTHON_CMD=py"
    )
)
if not defined PYTHON_CMD (
    python --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
    echo Python was not found. Install Python 3.12 or 3.13 from https://www.python.org/downloads/windows/
    echo During setup, select Add Python to PATH.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv using %PYTHON_CMD% ...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto failed
) else (
    echo Existing .venv found.
)
echo Upgrading pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
echo Installing BoneAI requirements. This may take several minutes...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo Setup completed.
echo Next: run CREATE_DOCTOR.bat once, then RUN_BONEAI.bat.
pause
exit /b 0
:failed
echo.
echo Setup failed. If the error mentions an unavailable PyTorch or package wheel on Python 3.13,
echo install Python 3.12 and run SETUP_VENV.bat again after deleting the failed .venv folder.
pause
exit /b 1
