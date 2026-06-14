#!/usr/bin/env bash
# Boss直聘 Agent 启停控制脚本
# 用法: ./agent.sh [start|stop|status|restart|logs]

set -e
cd "$(dirname "$0")"
PID_FILE=".agent.pid"
LOG_DIR="logs"
NAME="boss-agent"

mkdir -p "$LOG_DIR"

case "${1:-help}" in
    start)
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "❌ Agent 已在运行中 (PID: $(cat "$PID_FILE"))"
            exit 1
        fi
        echo "🚀 启动 Agent..."
        echo "  内置面板将自动启动在 http://localhost:9200"
        nohup python main.py >> "$LOG_DIR/agent.log" 2>&1 &
        PID=$!
        echo $PID > "$PID_FILE"
        echo "✅ Agent 已启动 (PID: $PID)"
        echo "   日志: $LOG_DIR/agent.log"
        echo "   停止: ./agent.sh stop"
        ;;

    mqtt)
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "❌ Agent 已在运行中 (PID: $(cat "$PID_FILE"))"
            exit 1
        fi
        echo "🚀 启动 Agent (MQTT 模式)..."
        BOSS_MQTT_MODE=true nohup python main.py >> "$LOG_DIR/agent.log" 2>&1 &
        PID=$!
        echo $PID > "$PID_FILE"
        echo "✅ Agent 已启动 (MQTT 模式, PID: $PID)"
        echo "   日志: $LOG_DIR/agent.log"
        echo "   停止: ./agent.sh stop"
        ;;

    stop)
        if [ ! -f "$PID_FILE" ]; then
            echo "⚠️  Agent 未运行 (无 PID 文件)"
            exit 0
        fi
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "🛑 停止 Agent (PID: $PID)..."
            kill "$PID"
            sleep 2
            # 强制终止如果还没停
            if kill -0 "$PID" 2>/dev/null; then
                echo "   强制终止..."
                kill -9 "$PID" 2>/dev/null || true
            fi
            echo "✅ Agent 已停止"
        else
            echo "⚠️  Agent 进程不存在 (PID: $PID)"
        fi
        rm -f "$PID_FILE"
        ;;

    status)
        if [ ! -f "$PID_FILE" ]; then
            echo "📪 Agent 未运行"
            exit 0
        fi
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            # 查看运行时间
            RUNTIME=$(ps -o etime= -p "$PID" 2>/dev/null | tr -d ' ' || echo "?")
            # 查看内存占用
            MEM=$(ps -o rss= -p "$PID" 2>/dev/null | tr -d ' ' || echo "?")
            # 查看模式
            if grep -q "MQTT" "$LOG_DIR/agent.log" 2>/dev/null; then
                MODE="MQTT"
            else
                MODE="浏览器"
            fi
            echo "📡 Agent 运行中"
            echo "  PID:     $PID"
            echo "  模式:    $MODE"
            echo "  运行:    ${RUNTIME}s"
            echo "  内存:    ${MEM}KB"
            # 统计消息数
            MSG_CNT=$(grep -c "消息已发送" "$LOG_DIR/agent.log" 2>/dev/null || echo 0)
            echo "  已发送:  ${MSG_CNT} 条消息"
        else
            echo "💀 Agent 进程已终止 (PID: $PID, 但进程不存在)"
            rm -f "$PID_FILE"
        fi
        ;;

    restart)
        "$0" stop
        sleep 1
        "$0" start
        ;;

    logs)
        tail -f "$LOG_DIR/agent.log"
        ;;

    *)
        echo "Boss直聘 Agent 控制脚本"
        echo ""
        echo "用法: ./agent.sh <command>"
        echo ""
        echo "命令:"
        echo "  start     启动 Agent (浏览器模式)"
        echo "  mqtt      启动 Agent (MQTT 模式, 无需浏览器)"
        echo "  stop      停止 Agent"
        echo "  status    查看运行状态"
        echo "  restart   重启 Agent"
        echo "  logs      查看实时日志"
        echo ""
        echo "示例:"
        echo "  ./agent.sh start    # 后台启动"
        echo "  ./agent.sh status   # 查看状态"
        echo "  ./agent.sh stop     # 停止"
        ;;
esac
