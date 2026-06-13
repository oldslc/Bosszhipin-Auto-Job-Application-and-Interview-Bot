@echo off
chcp 65001 >nul
echo ========================================
echo  启动 Chrome CDP 模式 (端口 9222)
echo  用于 Boss直聘自动对话 Agent
echo ========================================
echo.
echo 正在启动 Chrome --remote-debugging-port=9222 ...
echo 启动后请登录 Boss直聘，再运行 agent
echo.

start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9322

echo Chrome 已启动！
echo 现在可以在 WSL 中运行: python main.py
echo.
pause
