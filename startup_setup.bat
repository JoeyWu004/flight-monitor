@echo off
setlocal enabledelayedexpansion
:: =======================================================
::  Flight-Monitor - 开机自启动安装脚本
::  用法：右键以管理员身份运行
::  检测顺序：CONDA_PREFIX → where conda → 常见 conda 路径
::            → where pythonw → 常见 Python 路径 → 注册表 → where python
:: =======================================================

set TASK_NAME=FlightMonitor
set SCRIPT_DIR=%~dp0
set MAIN_SCRIPT=%SCRIPT_DIR%main.py

echo.
echo =========================================================
echo   Flight-Monitor - 开机自启动安装
echo =========================================================
echo.

if not exist "%MAIN_SCRIPT%" (
    echo [错误] 找不到 %MAIN_SCRIPT%
    echo 请确保此脚本与 main.py 在同一目录
    pause
    exit /b 1
)
echo [1/5] 项目路径: %SCRIPT_DIR%

echo [2/5] 正在检测 Python...
set PYTHON_PATH=
set RUN_MODE=

:: ---- 2a. 已激活 conda：直接用 CONDA_PREFIX ----
if defined CONDA_PREFIX if exist "!CONDA_PREFIX!\pythonw.exe" (
    echo       检测到已激活 conda: !CONDA_PREFIX!
    "!CONDA_PREFIX!\pythonw.exe" -c "import DrissionPage" 2^>nul
    if !errorlevel! equ 0 (
        set "PYTHON_PATH=!CONDA_PREFIX!\pythonw.exe"
        set RUN_MODE=conda
        echo       [OK] conda (已激活)
    ) else (
        echo       已激活 conda 无 DrissionPage，检查 envs...
        if exist "!CONDA_PREFIX!\envs" for /d %%e in ("!CONDA_PREFIX!\envs\*") do if not defined PYTHON_PATH if exist "%%e\pythonw.exe" (
            "%%e\pythonw.exe" -c "import DrissionPage" 2^>nul
            if !errorlevel! equ 0 (
                set "PYTHON_PATH=%%e\pythonw.exe"
                set RUN_MODE=conda
                echo       [OK] conda env: %%e
            )
        )
    )
)

:: ---- 2b. where conda 反查根目录（普适方案）----
if not defined PYTHON_PATH for /f "usebackq delims=" %%i in (`where conda 2^>nul`) do if not defined PYTHON_PATH (
    set "CF=%%i"
    set "CR=!CF:\Scripts\conda.exe=!"
    echo       找到 conda: !CR!
    if exist "!CR!\pythonw.exe" (
        "!CR!\pythonw.exe" -c "import DrissionPage" 2^>nul
        if !errorlevel! equ 0 (
            set "PYTHON_PATH=!CR!\pythonw.exe"
            set RUN_MODE=conda
            echo       [OK] conda base
        ) else (
            echo       base 无 DrissionPage，检查 envs...
            if exist "!CR!\envs" for /d %%e in ("!CR!\envs\*") do if not defined PYTHON_PATH if exist "%%e\pythonw.exe" (
                "%%e\pythonw.exe" -c "import DrissionPage" 2^>nul
                if !errorlevel! equ 0 (
                    set "PYTHON_PATH=%%e\pythonw.exe"
                    set RUN_MODE=conda
                    echo       [OK] conda env: %%e
                )
            )
        )
    ) else (
        if exist "!CR!\envs" for /d %%e in ("!CR!\envs\*") do if not defined PYTHON_PATH if exist "%%e\pythonw.exe" (
            "%%e\pythonw.exe" -c "import DrissionPage" 2^>nul
            if !errorlevel! equ 0 (
                set "PYTHON_PATH=%%e\pythonw.exe"
                set RUN_MODE=conda
                echo       [OK] conda env: %%e
            )
        )
    )
)

:: ---- 2c. 常见 conda 安装路径（where conda 失败时的回退）----
if not defined PYTHON_PATH for %%r in (
    "%USERPROFILE%\anaconda3" "%USERPROFILE%\Anaconda3"
    "%USERPROFILE%\miniconda3" "%USERPROFILE%\Miniconda3"
    "%LOCALAPPDATA%\anaconda3" "%LOCALAPPDATA%\Anaconda3"
    "%LOCALAPPDATA%\miniconda3" "%LOCALAPPDATA%\Miniconda3"
    "%ALLUSERSPROFILE%\anaconda3" "%ALLUSERSPROFILE%\miniconda3"
    "C:\anaconda3" "C:\miniconda3" "C:\ProgramData\anaconda3"
    "C:\ProgramData\miniconda3" "D:\anaconda3" "D:\miniconda3"
) do if not defined PYTHON_PATH if exist "%%~r\pythonw.exe" (
    "%%~r\pythonw.exe" -c "import DrissionPage" 2^>nul
    if !errorlevel! equ 0 (
        set "PYTHON_PATH=%%~r\pythonw.exe"
        set RUN_MODE=conda
        echo       [OK] conda base: %%~r
    ) else (
        if exist "%%~r\envs" for /d %%e in ("%%~r\envs\*") do if not defined PYTHON_PATH if exist "%%e\pythonw.exe" (
            "%%e\pythonw.exe" -c "import DrissionPage" 2^>nul
            if !errorlevel! equ 0 (
                set "PYTHON_PATH=%%e\pythonw.exe"
                set RUN_MODE=conda
                echo       [OK] conda env: %%e
            )
        )
    )
)

:: ---- 2d. where pythonw（系统 Python）----
if not defined PYTHON_PATH for /f "usebackq delims=" %%i in (`where pythonw 2^>nul`) do if not defined PYTHON_PATH set "PYTHON_PATH=%%i"

:: ---- 2e. 常见系统 Python 安装路径 ----
if not defined PYTHON_PATH for %%d in (
    "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python39\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python38\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python37\pythonw.exe"
    "C:\Python313\pythonw.exe" "C:\Python312\pythonw.exe"
    "C:\Python311\pythonw.exe" "C:\Python310\pythonw.exe"
    "C:\Program Files\Python313\pythonw.exe"
    "C:\Program Files\Python312\pythonw.exe"
    "C:\Program Files\Python311\pythonw.exe"
) do if not defined PYTHON_PATH if exist "%%d" set "PYTHON_PATH=%%d"

:: ---- 2f. 注册表搜索 ----
if not defined PYTHON_PATH for /f "usebackq tokens=*" %%a in (`reg query "HKLM\SOFTWARE\Python\PythonCore" /s /f "InstallPath" 2^>nul ^| findstr "InstallPath"`) do (
    for /f "usebackq tokens=2,*" %%b in (`reg query "%%a" /ve 2^>nul ^| findstr /r /c:"REG_SZ" /c:"REG_EXPAND_SZ"`) do (
        if not defined PYTHON_PATH if exist "%%c\pythonw.exe" set "PYTHON_PATH=%%c\pythonw.exe"
    )
)
if not defined PYTHON_PATH for /f "usebackq tokens=*" %%a in (`reg query "HKCU\SOFTWARE\Python\PythonCore" /s /f "InstallPath" 2^>nul ^| findstr "InstallPath"`) do (
    for /f "usebackq tokens=2,*" %%b in (`reg query "%%a" /ve 2^>nul ^| findstr /r /c:"REG_SZ" /c:"REG_EXPAND_SZ"`) do (
        if not defined PYTHON_PATH if exist "%%c\pythonw.exe" set "PYTHON_PATH=%%c\pythonw.exe"
    )
)

:: ---- 2g. where python（最终回退）----
if not defined PYTHON_PATH for /f "usebackq delims=" %%i in (`where python 2^>nul`) do if not defined PYTHON_PATH set "PYTHON_PATH=%%i"

:: ---- 未找到 ----
if not defined PYTHON_PATH (
    echo [错误] 未找到 Python！
    echo 请确认 Python / Anaconda / Miniconda 已安装。
    echo 如已安装 conda，请在 conda 终端中运行此脚本。
    pause
    exit /b 1
)

if "%RUN_MODE%"=="" set RUN_MODE=direct
echo       [OK] Python: !PYTHON_PATH!

:: ---- 非 conda 模式验证 DrissionPage ----
if "%RUN_MODE%"=="direct" (
    "!PYTHON_PATH!" -c "import DrissionPage" 2^>nul
    if !errorlevel! neq 0 (
        echo.
        echo ======== 错误 ========
        echo Python: !PYTHON_PATH!
        echo 缺少 DrissionPage 库！
        echo.
        echo 建议:
        echo   1. 如 conda 环境中有 DrissionPage，请先激活再运行
        echo      conda activate 环境名
        echo      startup_setup.bat
        echo   2. 或直接安装: pip install -r requirements.txt
        echo.
        choice /c yn /m "是否需要自动安装？(可能会失败)"
        if !errorlevel! neq 1 exit /b 1
    )
    echo       [OK] DrissionPage 已安装
)

:: =========================================================
::  3. 删除旧任务
:: =========================================================
echo [3/5] 检查旧任务...
schtasks /query /tn "%TASK_NAME%" >nul
if !errorlevel! equ 0 (
    echo       检测到旧任务，正在删除...
    schtasks /delete /tn "%TASK_NAME%" /f >nul
    echo       已删除
) else (
    echo       无旧任务
)

:: =========================================================
::  4. 生成启动脚本
:: =========================================================
echo [4/5] 生成启动脚本...
set LAUNCHER=%SCRIPT_DIR%run_monitor.bat
(echo @echo off) > "%LAUNCHER%"
(echo cd /d "%SCRIPT_DIR%") >> "%LAUNCHER%"
(echo start "" "!PYTHON_PATH!" "%MAIN_SCRIPT%") >> "%LAUNCHER%"
echo       run_monitor.bat 已生成

:: =========================================================
::  5. 创建计划任务
:: =========================================================
echo [5/5] 创建计划任务...
schtasks /create /tn "%TASK_NAME%" /tr "%LAUNCHER%" /sc ONLOGON /delay 0000:30 /it /rl LIMITED /f >nul

if !errorlevel! equ 0 (
    echo       任务创建成功！
    echo.
    echo =========================================================
    echo   安装完成！
    echo =========================================================
    echo.
    echo [*] 任务: %TASK_NAME%
    echo [*] Python: !PYTHON_PATH!
    echo [*] 项目: %SCRIPT_DIR%
    if "%RUN_MODE%"=="conda" echo [*] 方式: conda
    if "%RUN_MODE%"=="direct" echo [*] 方式: 系统 Python
    echo.
    echo ---- 手动控制 ----
    echo   启动: schtasks /run /tn "%TASK_NAME%"
    echo   停止: schtasks /end /tn "%TASK_NAME%"
    echo   状态: schtasks /query /tn "%TASK_NAME%"
    echo   卸载: 双击 startup_remove.bat
    echo.
) else (
    echo [错误] 任务创建失败！
    pause
    exit /b 1
)

set /p RUN_NOW="是否立即运行？(y/n): "
if /i "!RUN_NOW!"=="y" (
    echo 正在启动...
    schtasks /run /tn "%TASK_NAME%"
    echo 启动完成！
)
pause
