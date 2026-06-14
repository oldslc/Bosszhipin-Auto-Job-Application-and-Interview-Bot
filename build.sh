#!/usr/bin/env bash
# BOSS 直聘全自动投递面试智能体 - 打包脚本
# 用法: bash build.sh

set -e
cd "$(dirname "$0")"

APP_NAME="boss-agent"
DIST_DIR="dist/$APP_NAME"
VERSION=$(date +%Y%m%d)

echo "╔══════════════════════════════════════════════╗"
echo "║  BOSS 直聘全自动投递面试智能体 - 打包脚本        ║"
echo "╚══════════════════════════════════════════════╝"

# 1. 清理敏感信息
echo ""
echo "[1/5] 清理敏感信息..."
> .api_key
> data/cookie.txt
echo "  ✅ .api_key 已清空"
echo "  ✅ data/cookie.txt 已清空"

# 2. 激活 venv
echo ""
echo "[2/5] 检查环境..."
source venv/bin/activate
echo "  ✅ Python $(python3 --version)"
echo "  ✅ PyInstaller $(pyinstaller --version)"

# 3. 创建图标（如果没有）
if [ ! -f icon.png ]; then
    echo ""
    echo "[3/5] 生成图标..."
    python3 -c "
import base64, struct, zlib
# 生成一个简单的 32x32 图标（蓝色背景 + 白色 B 字）
import io
try:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new('RGBA', (256, 256), (22, 27, 34, 255))
    draw = ImageDraw.Draw(img)
    # 外边框
    draw.rounded_rectangle([5,5,251,251], radius=40, outline=(88,166,255,255), width=6)
    # "B" 字
    draw.text((128, 128), 'B', fill=(88,166,255,255), anchor='mm', font_size=160)
    img.save('icon.png')
    print('  ✅ icon.png 已生成')
except ImportError:
    print('  ⚠️ 没有 PIL，跳过图标生成')
" 2>&1
fi

# 4. 运行 PyInstaller
echo ""
echo "[4/5] 运行 PyInstaller..."
rm -rf build dist boss-agent.spec 2>/dev/null

source venv/bin/activate && python -m PyInstaller \
    --name "$APP_NAME" \
    --onefile \
    --distpath dist \
    --add-data "config.py:." \
    --add-data ".api_key:." \
    --add-data "boss_mqtt_pb2.py:." \
    --add-data "boss_mqtt.proto:." \
    --hidden-import "boss_mqtt_pb2" \
    --hidden-import "dashboard" \
    --hidden-import "chat_handler" \
    --hidden-import "llm_client" \
    --hidden-import "mqtt_chat" \
    --hidden-import "mqtt_monitor" \
    --hidden-import "browser" \
    --hidden-import "monitor" \
    --hidden-import "job_hunter" \
    --hidden-import "models" \
    --hidden-import "config" \
    --hidden-import "flask" \
    --hidden-import "requests" \
    --hidden-import "websocket" \
    --hidden-import "boss_mqtt_pb2" \
    --collect-submodules "flask" \
    --collect-submodules "requests" \
    --collect-submodules "websocket" \
    --exclude-module "tkinter" \
    --exclude-module "matplotlib" \
    --exclude-module "numpy" \
    --exclude-module "pandas" \
    --exclude-module "PIL" \
    --exclude-module "cv2" \
    --console \
    main.py 2>&1

echo "  ✅ PyInstaller 打包完成"

# 5. 创建分发目录
echo ""
DIST_DIR="dist/boss-agent-pkg"
echo "[5/5] 创建分发包..."
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"
cp "dist/$APP_NAME" "$DIST_DIR/"

# 创建 Windows 启动脚本
cat > "$DIST_DIR/启动面板.bat" << 'BAT'
@echo off
chcp 65001 >nul
echo BOSS 直聘全自动投递面试智能体
echo ============================
echo.
echo 启动中，请稍候...
echo 控制面板将在浏览器中自动打开
echo.
wsl ./boss-agent &
timeout /t 3 /nobreak >nul
start http://localhost:9200
echo 面板地址: http://localhost:9200
echo 按 Ctrl+C 停止
wait
pause
BAT

# 创建 Linux 启动脚本
cat > "$DIST_DIR/start.sh" << 'SH'
#!/usr/bin/env bash
echo "BOSS 直聘全自动投递面试智能体"
echo ""
echo "启动中..."
./boss-agent &
sleep 3
echo ""
echo "控制面板: http://localhost:9200"
echo ""
wait
SH
chmod +x "$DIST_DIR/start.sh"

# 创建 README
cat > "$DIST_DIR/README.txt" << 'README'
BOSS 直聘全自动投递面试智能体
=========================

功能:
  - 自动搜索职位、投递简历
  - 自动回复 HR 消息（AI 生成）
  - 控制面板: http://localhost:9200

两种模式:
  1. 浏览器模式: 需要 Chrome 以 --remote-debugging-port=9333 启动
  2. MQTT 模式: 无需浏览器，需要 data/cookie.txt 包含有效 cookie

使用:
  Linux/WSL: ./start.sh
  Windows:   双击 启动面板.bat
  
  然后打开浏览器访问 http://localhost:9200

配置:
  所有配置在控制面板中在线编辑，无需手动改文件

注意:
  - 首次使用需要先在 Chrome 中登录 BOSS 直聘
  - MQTT 模式需要先导出 cookie 到 data/cookie.txt
README

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  ✅ 打包完成！                              ║"
echo "║                                              ║"
echo "║  输出: $DIST_DIR/                ║"
echo "║  文件: $APP_NAME (可执行文件)     ║"
echo "║        启动面板.bat (Windows 入口)           ║"
echo "║        start.sh (WSL/Linux 入口)             ║"
echo "║                                              ║"
echo "║  大小: $(du -sh $DIST_DIR 2>/dev/null | cut -f1)                        ║"
echo "╚══════════════════════════════════════════════╝"
