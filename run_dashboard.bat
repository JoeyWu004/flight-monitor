@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title Flight-Monitor Dashboard
cd /d "%~dp0"

echo ============================================================
echo   Flight-Monitor Dashboard (Local)
echo ============================================================
echo.

set PYTHON_EXE=

:: ================================================================
::  Detect Python environment (same logic as startup_setup.bat)
::  Priority: CONDA_PREFIX > where conda > common paths > where
:: ================================================================

:: --- 1. CONDA_PREFIX (already activated conda) ---
if defined CONDA_PREFIX (
    if exist "!CONDA_PREFIX!\python.exe" (
        set "PYTHON_EXE=!CONDA_PREFIX!\python.exe"
        echo [1] Found via CONDA_PREFIX
    ) else if exist "!CONDA_PREFIX!\envs" (
        for /d %%e in ("!CONDA_PREFIX!\envs\*") do if not defined PYTHON_EXE if exist "%%e\python.exe" (
            set "PYTHON_EXE=%%e\python.exe"
            echo [1] Found via CONDA_PREFIX envs
        )
    )
)

:: --- 2. where conda: locate conda base, then check base and envs ---
if not defined PYTHON_EXE for /f "usebackq delims=" %%i in (`where conda 2^>nul`) do if not defined PYTHON_EXE (
    set "CF=%%i"
    set "CR=!CF:\Scripts\conda.exe=!"
    if exist "!CR!\python.exe" (
        set "PYTHON_EXE=!CR!\python.exe"
        echo [2] Found via where conda base
    ) else if exist "!CR!\envs" (
        for /d %%e in ("!CR!\envs\*") do if not defined PYTHON_EXE if exist "%%e\python.exe" (
            set "PYTHON_EXE=%%e\python.exe"
            echo [2] Found via where conda envs
        )
    )
)

:: --- 3. Common conda installation paths ---
if not defined PYTHON_EXE for %%r in (
    "%USERPROFILE%\anaconda3"  "%USERPROFILE%\Anaconda3"
    "%USERPROFILE%\miniconda3" "%USERPROFILE%\Miniconda3"
    "%LOCALAPPDATA%\anaconda3" "%LOCALAPPDATA%\miniconda3"
    "%ALLUSERSPROFILE%\anaconda3" "%ALLUSERSPROFILE%\miniconda3"
    "C:\anaconda3"  "C:\miniconda3"
    "C:\ProgramData\anaconda3"  "C:\ProgramData\miniconda3"
    "D:\anaconda3"  "D:\miniconda3"  "D:\Softwares\anaconda3"  "D:\Softwares\miniconda3"
) do if not defined PYTHON_EXE (
    if exist "%%~r\python.exe" (
        set "PYTHON_EXE=%%~r\python.exe"
        echo [3] Found at %%~r
    ) else if exist "%%~r\envs" (
        for /d %%e in ("%%~r\envs\*") do if not defined PYTHON_EXE if exist "%%e\python.exe" (
            set "PYTHON_EXE=%%e\python.exe"
            echo [3] Found at %%e
        )
    )
)

:: --- 4. where pythonw ---
if not defined PYTHON_EXE for /f "usebackq delims=" %%i in (`where pythonw 2^>nul`) do if not defined PYTHON_EXE (
    set "PYTHONW=%%i"
    set "PYDIR=%%~dpi"
    if exist "!PYDIR!python.exe" (
        set "PYTHON_EXE=!PYDIR!python.exe"
        echo [4] Found via where pythonw
    )
)

:: --- 5. Common system Python paths ---
if not defined PYTHON_EXE for %%d in (
    "%LOCALAPPDATA%\Programs\Python\Python313"
    "%LOCALAPPDATA%\Programs\Python\Python312"
    "%LOCALAPPDATA%\Programs\Python\Python311"
    "%LOCALAPPDATA%\Programs\Python\Python310"
    "%LOCALAPPDATA%\Programs\Python\Python39"
    "%LOCALAPPDATA%\Programs\Python\Python38"
    "C:\Python313" "C:\Python312" "C:\Python311"
    "C:\Program Files\Python313" "C:\Program Files\Python312" "C:\Program Files\Python311"
) do if not defined PYTHON_EXE if exist "%%~d\python.exe" (
    set "PYTHON_EXE=%%~d\python.exe"
    echo [5] Found at %%~d
)

:: --- 6. Registry lookup ---
if not defined PYTHON_EXE (
    for /f "usebackq tokens=2,*" %%a in (`reg query "HKLM\SOFTWARE\Python\PythonCore" /s /f "InstallPath" 2^>nul ^| findstr "InstallPath"`) do (
        for /f "usebackq tokens=2,*" %%c in (`reg query "%%a %%b" /ve 2^>nul ^| findstr /r /c:"REG_SZ" /c:"REG_EXPAND_SZ"`) do (
            if not defined PYTHON_EXE if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
        )
    )
)
if not defined PYTHON_EXE (
    for /f "usebackq tokens=2,*" %%a in (`reg query "HKCU\SOFTWARE\Python\PythonCore" /s /f "InstallPath" 2^>nul ^| findstr "InstallPath"`) do (
        for /f "usebackq tokens=2,*" %%c in (`reg query "%%a %%b" /ve 2^>nul ^| findstr /r /c:"REG_SZ" /c:"REG_EXPAND_SZ"`) do (
            if not defined PYTHON_EXE if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
        )
    )
)
if defined PYTHON_EXE echo [6] Found via Registry

:: --- 7. where python (last resort) ---
if not defined PYTHON_EXE for /f "usebackq delims=" %%i in (`where python 2^>nul`) do if not defined PYTHON_EXE (
    set "PYTHON_EXE=%%i"
    echo [7] Found via where python
)

:: ============================================================
::  Fail if no Python
:: ============================================================
if not defined PYTHON_EXE (
    echo.
    echo [ERROR] Python not found!
    echo   Run startup_setup.bat first, or install Python.
    echo   https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo        !PYTHON_EXE!
echo.

:: ============================================================
::  Check / install server dependencies
:: ============================================================
echo Checking dependencies ...
"!PYTHON_EXE!" -c "import fastapi, uvicorn, jwt" >nul 2>&1
if errorlevel 1 (
    echo Installing: fastapi uvicorn PyJWT python-multipart ...
    "!PYTHON_EXE!" -m pip install -r server\requirements.txt -q
    if errorlevel 1 (
        echo [ERROR] Install failed. Run manually:
        echo   "!PYTHON_EXE!" -m pip install -r server\requirements.txt
        pause
        exit /b 1
    )
    echo Done.
)
echo Dependencies OK.
echo.

:: ============================================================
::  Database
:: ============================================================
if not exist "flight_monitor.db" (
    echo [WARNING] flight_monitor.db not found. No data to show.
    echo           Run run_monitor.bat to fetch flight data first.
    echo.
)

:: ============================================================
::  Start
:: ============================================================
echo ============================================================
echo   http://127.0.0.1:8000
echo   No login required. Close this window to stop.
echo ============================================================
echo.

start "" cmd /c "timeout /t 3 >nul && start http://127.0.0.1:8000"

set NO_AUTH=1
"!PYTHON_EXE!" server\server.py
pause
