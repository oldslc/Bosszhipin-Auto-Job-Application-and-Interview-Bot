@echo off
chcp 65001 >nul
cd /d "%~dp0"

if "%1"=="" goto help
if "%1"=="start" goto start
if "%1"=="mqtt" goto mqtt
if "%1"=="stop" goto stop
if "%1"=="status" goto status
if "%1"=="restart" goto restart
if "%1"=="logs" goto logs
goto help

:start
echo 启动 Agent (浏览器模式)...
start /B "" python main.py
echo 已启动，可在 WSL 中用 ./agent.sh status 查看状态
exit /b

:mqtt
echo 启动 Agent (MQTT 模式)...
set BOSS_MQTT_MODE=true
start /B "" python main.py
echo 已启动，可在 WSL 中用 ./agent.sh status 查看状态
exit /b

:stop
echo 停止 Agent...
wsl ./agent.sh stop
exit /b

:status
wsl ./agent.sh status
exit /b

:restart
wsl ./agent.sh restart
exit /b

:logs
wsl ./agent.sh logs
exit /b

:help
echo Boss直聘 Agent 控制
echo.
echo 用法: agent.bat [start^|mqtt^|stop^|status^|restart^|logs]
echo.
echo   start    启动 (浏览器模式)
echo   mqtt     启动 (MQTT 模式)
echo   stop     停止
echo   status   查看状态
echo   restart  重启
echo   logs     查看日志
echo.
pause
