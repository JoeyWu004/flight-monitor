@echo off
:: =======================================================
::  Flight-Monitor - 卸载开机自启动
::  用法：右键以管理员身份运行
:: =======================================================

set TASK_NAME=FlightMonitor

echo.
echo =========================================================
echo   Flight-Monitor - 卸载开机自启动
echo =========================================================
echo.

:: 1. 先停止正在运行的任务
schtasks /query /tn "%TASK_NAME%" >nul
if %errorlevel% equ 0 (
    echo [1/2] 正在停止运行中的任务...
    schtasks /end /tn "%TASK_NAME%" >nul
    timeout /t 2 /nobreak >nul
    echo       已停止
) else (
    echo [1/2] 任务未运行，无需停止
)

:: 2. 删除任务
echo [2/2] 正在删除任务...
schtasks /delete /tn "%TASK_NAME%" /f >nul
if %errorlevel% equ 0 (
    echo       任务已删除
    echo.
    echo 卸载完成，以后将不再开机自动运行。
    echo monitor.log 日志文件未删除，可手动删除。
) else (
    echo       任务不存在或已删除
)

echo.
pause
