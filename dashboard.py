"""
BOSS 求职 Agent 交互式控制面板
单文件 Flask 应用，后端 Python + 前端 HTML/CSS/JS 内联
GitHub Dark 主题
"""
import ast
import json
import os
import re
import subprocess
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, jsonify, request

# PyInstaller 兼容：单文件模式时数据在 _MEIPASS
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONV_DIR = os.path.join(DATA_DIR, "conversations")
LOG_DIR = os.path.join(BASE_DIR, "logs")
PID_FILE = os.path.join(BASE_DIR, ".agent.pid")
AGENT_SH = os.path.join(BASE_DIR, "agent.sh")
API_KEY_FILE = os.path.join(BASE_DIR, ".api_key")
CONFIG_FILE = os.path.join(BASE_DIR, "config.py")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CONV_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Config helpers: read/write config.py via ast + regex
# ---------------------------------------------------------------------------

# Fields that are saved to .api_key instead of config.py
API_KEY_FIELD = "LLM_API_KEY"

# Fields that come from env (display only, not saved to file)
ENV_ONLY_FIELDS = {"MQTT_MODE"}

# Fields that don't exist in config.py yet but we support (will be appended)
DYNAMIC_FIELDS = {"EASY_KEYWORDS"}

# Ordered list of fields exposed in the UI (for display/save ordering)
EDITABLE_FIELDS = [
    "SALARY_MIN", "CITIES", "REPLY_STYLE", "PERSONA",
    "POLL_INTERVAL", "MAX_REPLIES_PER_HOUR",
    "SENSITIVE_KEYWORDS", "EASY_KEYWORDS",
    "LLM_API_URL", "LLM_MODEL", "LLM_API_KEY",
    "LLM_TIMEOUT", "LLM_MAX_RETRIES",
    "CHROME_CDP_PORT", "PROXY",
    "MQTT_MODE",
]

# List-valued fields that need special repr
LIST_FIELDS = {"CITIES", "SENSITIVE_KEYWORDS", "EASY_KEYWORDS"}

# Multi-line string fields
MULTILINE_FIELDS = {"PERSONA"}


def _parse_config_py() -> dict:
    """Parse config.py using ast and return all variable values as a dict."""
    result = {}
    if not os.path.exists(CONFIG_FILE):
        return result

    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        result[name] = _ast_value(node.value, source)
    except SyntaxError:
        pass

    return result


def _ast_value(node, source: str):
    """Convert an AST node to a Python value."""
    if isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.List):
        return [_ast_value(el, source) for el in node.elts]
    elif isinstance(node, ast.Name):
        # True/False/None are Constant in Python 3.8+
        if node.id in ("True", "False", "None"):
            return {"True": True, "False": False, "None": None}[node.id]
        return node.id
    elif isinstance(node, ast.Call):
        # Handle LLM_API_KEY=open(....ip() etc.
        return _get_source_snippet(source, node)
    elif isinstance(node, ast.Expr):
        if isinstance(node.value, ast.Constant):
            return node.value.value
    return None


def _get_source_snippet(source: str, node) -> str:
    """Get the source text for an AST node."""
    try:
        lines = source.splitlines()
        if hasattr(node, 'lineno') and hasattr(node, 'end_lineno'):
            if node.lineno == node.end_lineno:
                line = lines[node.lineno - 1]
                return line[node.col_offset:node.end_col_offset].strip()
            else:
                parts = []
                parts.append(lines[node.lineno - 1][node.col_offset:])
                for i in range(node.lineno, node.end_lineno - 1):
                    parts.append(lines[i])
                parts.append(lines[node.end_lineno - 1][:node.end_col_offset])
                return "\n".join(parts).strip()
    except (IndexError, AttributeError):
        pass
    return ""


def _get_api_key() -> str:
    """Read API key from .api_key file."""
    if os.path.exists(API_KEY_FILE):
        try:
            with open(API_KEY_FILE, encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""


def _set_api_key(value: str) -> None:
    """Write API key to .api_key file."""
    with open(API_KEY_FILE, "w", encoding="utf-8") as f:
        f.write(value.strip())


def _mask_key(key: str) -> str:
    """Mask a sensitive key (show first 4 + last 4)."""
    if len(key) <= 8:
        return "****" if key else ""
    return key[:4] + "..." + key[-4:]


def get_config_for_ui() -> dict:
    """Get all config values for the frontend (masked where needed)."""
    cfg = _parse_config_py()

    # LLM_API_KEY from separate file
    raw_key = _get_api_key()
    cfg["LLM_API_KEY"] = _mask_key(raw_key) if raw_key else ""
    cfg["_LLM_API_KEY_RAW"] = raw_key  # hidden from UI, used internally

    # MQTT_MODE from env
    cfg["MQTT_MODE"] = os.environ.get("BOSS_MQTT_MODE", "false").lower() == "true"

    # EASY_KEYWORDS might not exist yet
    if "EASY_KEYWORDS" not in cfg:
        cfg["EASY_KEYWORDS"] = []

    # Ensure defaults for fields that might have None
    for f in EDITABLE_FIELDS:
        if f not in cfg:
            cfg[f] = "" if f not in LIST_FIELDS else []

    return cfg


def _repr_value_for_config(key: str, value) -> str:
    """Convert a Python value to its config.py source representation."""
    if key in MULTILINE_FIELDS and isinstance(value, str):
        # Multi-line string: use triple quotes
        return f'"""{value}"""'
    elif key in LIST_FIELDS and isinstance(value, (list, tuple)):
        if len(value) == 0:
            return "[]"
        items = ", ".join(repr(v) for v in value)
        return f"[{items}]"
    elif isinstance(value, str):
        return repr(value)
    elif isinstance(value, bool):
        return "True" if value else "False"
    else:
        return str(value)


def _replace_in_config(key: str, value) -> bool:
    """Replace a single config variable in config.py. Returns True if replaced."""
    if not os.path.exists(CONFIG_FILE):
        return False

    with open(CONFIG_FILE, encoding="utf-8") as f:
        content = f.read()

    new_repr = _repr_value_for_config(key, value)

    if key in MULTILINE_FIELDS:
        # Match: KEY = """..."""
        escaped_key = re.escape(key)
        pattern = rf'^{escaped_key}\s*=\s*""".*?"""'
        replacement = f'{key} = {new_repr}'
        new_content, count = re.subn(
            pattern, replacement, content, count=1, flags=re.MULTILINE | re.DOTALL
        )
    elif key in LIST_FIELDS:
        # Match: KEY = [...]
        escaped_key = re.escape(key)
        pattern = rf'^{escaped_key}\s*=\s*\[.*?\]'
        replacement = f'{key} = {new_repr}'
        new_content, count = re.subn(
            pattern, replacement, content, count=1, flags=re.MULTILINE | re.DOTALL
        )
    elif isinstance(value, int):
        escaped_key = re.escape(key)
        pattern = rf'^{escaped_key}\s*=\s*\d+'
        replacement = f'{key} = {new_repr}'
        new_content, count = re.subn(
            pattern, replacement, content, count=1, flags=re.MULTILINE
        )
    elif isinstance(value, bool):
        escaped_key = re.escape(key)
        pattern = rf'^{escaped_key}\s*=\s*(?:True|False)'
        replacement = f'{key} = {new_repr}'
        new_content, count = re.subn(
            pattern, replacement, content, count=1, flags=re.MULTILINE
        )
    elif isinstance(value, str):
        # Match: KEY = "..."  (simple quoted string, not triple-quoted)
        escaped_key = re.escape(key)
        pattern = rf'^{escaped_key}\s*=\s*".*?"'
        replacement = f'{key} = {new_repr}'
        new_content, count = re.subn(
            pattern, replacement, content, count=1, flags=re.MULTILINE
        )
        if count == 0:
            # Try single quotes
            pattern = rf"^{escaped_key}\s*=\s*'.*?'"
            new_content, count = re.subn(
                pattern, replacement, content, count=1, flags=re.MULTILINE
            )
    else:
        return False

    if count > 0:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(new_content)
        return True
    return False


def _append_to_config(key: str, value) -> bool:
    """Append a new variable to config.py (for fields that don't exist yet)."""
    if not os.path.exists(CONFIG_FILE):
        return False

    new_repr = _repr_value_for_config(key, value)
    line = f"\n{key} = {new_repr}\n"

    with open(CONFIG_FILE, "a", encoding="utf-8") as f:
        f.write(line)
    return True


def save_config_updates(updates: dict) -> dict:
    """Save config updates. Returns {success: bool, message: str, changed: list}."""
    changed = []
    errors = []

    for key, value in updates.items():
        if key in ENV_ONLY_FIELDS:
            continue  # MQTT_MODE is env-only

        if key == API_KEY_FIELD:
            _set_api_key(value)
            changed.append(key)
            continue

        # Try to replace existing, otherwise append
        if _replace_in_config(key, value):
            changed.append(key)
        elif key in DYNAMIC_FIELDS:
            if _append_to_config(key, value):
                changed.append(key)
            else:
                errors.append(f"无法添加 {key}")
        else:
            errors.append(f"配置项 {key} 未找到")

    # Update the running config module if importable
    try:
        import importlib
        import config as cfg_mod
        for key in changed:
            setattr(cfg_mod, key, updates[key])
        importlib.reload(cfg_mod)
    except (ImportError, AttributeError):
        pass

    return {
        "success": len(errors) == 0,
        "message": f"已更新 {len(changed)} 项" + (f"，{len(errors)} 项失败" if errors else ""),
        "changed": changed,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Agent status helpers
# ---------------------------------------------------------------------------

def get_agent_status():
    """Return (running, pid, mode)."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                pid_str = f.read().strip()
                pid = int(pid_str)
            os.kill(pid, 0)

            mode = "浏览器"
            log_file = os.path.join(LOG_DIR, "agent.log")
            if os.path.exists(log_file):
                with open(log_file, encoding="utf-8", errors="ignore") as f:
                    content = f.read(4096)
                    if "MQTT" in content:
                        mode = "MQTT"
            return True, pid, mode
        except (OSError, ValueError, IOError):
            pass
    return False, None, "-"


def get_conversations(limit=50):
    """读取对话记录列表，每条含hr_name/company/position/status/last_msg/last_time/id."""
    convs = []
    if os.path.exists(CONV_DIR):
        files = sorted(
            (f for f in os.listdir(CONV_DIR) if f.endswith(".json")),
            reverse=True,
        )[:limit]
        for fname in files:
            try:
                with open(os.path.join(CONV_DIR, fname), encoding="utf-8") as f:
                    data = json.load(f)
                msgs = data.get("messages", [])
                last = msgs[-1] if msgs else {}
                convs.append({
                    "id": fname.replace(".json", ""),
                    "hr_name": data.get("hr_name", "?"),
                    "company": data.get("company", "?"),
                    "position": data.get("position", "?"),
                    "status": data.get("status", "unknown"),
                    "last_msg": (
                        last.get("content", "")[:80]
                        if isinstance(last, dict)
                        else str(last)[:80]
                    ),
                    "last_time": (
                        last.get("timestamp", "")
                        if isinstance(last, dict)
                        else ""
                    ),
                })
            except Exception:
                pass
    return convs


def get_deliveries(limit=50):
    """读取投递记录."""
    path = os.path.join(DATA_DIR, "delivered.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data[-limit:]
        except Exception:
            pass
    return []


def get_logs(max_lines=200, level=None):
    """读取日志，支持级别过滤."""
    log_file = os.path.join(LOG_DIR, "agent.log")
    if not os.path.exists(log_file):
        return ""
    try:
        with open(log_file, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception:
        return ""

    if level:
        level_upper = level.upper()
        filtered = []
        for line in lines:
            upper = line.upper()
            if level_upper == "ERROR" and ("ERROR" in upper or "FATAL" in upper or "CRITICAL" in upper):
                filtered.append(line)
            elif level_upper == "WARN" and ("WARN" in upper or "WARNING" in upper):
                filtered.append(line)
            elif level_upper == "INFO":
                # Show INFO, WARN, ERROR
                if any(tag in upper for tag in ["INFO", "WARN", "WARNING", "ERROR", "FATAL", "CRITICAL"]):
                    filtered.append(line)
        lines = filtered

    return "".join(lines[-max_lines:])


def get_stats():
    """汇总统计数据."""
    running, pid, mode = get_agent_status()
    convs = get_conversations(limit=200)
    deliveries = get_deliveries(limit=200)

    total_convs = len(convs)
    replied = sum(1 for c in convs if c.get("last_msg", "").strip())
    delivered_count = len(deliveries)
    last_active = convs[0]["last_time"][:16] if convs else "-"

    # Count sent messages from the agent log
    sent_msgs = 0
    log_file = os.path.join(LOG_DIR, "agent.log")
    if os.path.exists(log_file):
        try:
            with open(log_file, encoding="utf-8", errors="ignore") as f:
                content = f.read()
                sent_msgs = content.count("消息已发送")
        except Exception:
            pass

    return {
        "running": running,
        "pid": pid,
        "mode": mode,
        "conversations": total_convs,
        "replies": replied,
        "delivered": delivered_count,
        "sent_msgs": sent_msgs,
        "last_active": last_active,
    }


def run_agent_cmd(cmd):
    """控制 Agent 启停。内置模式，无需 agent.sh。"""
    try:
        if cmd == "start":
            # 启动新 agent 进程（后台）
            proc = subprocess.Popen(
                [sys.executable, os.path.join(BASE_DIR, "main.py")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=BASE_DIR,
            )
            with open(PID_FILE, "w") as f:
                f.write(str(proc.pid))
            return True, f"Agent 已启动 (PID: {proc.pid})"

        elif cmd == "mqtt":
            env = os.environ.copy()
            env["BOSS_MQTT_MODE"] = "true"
            proc = subprocess.Popen(
                [sys.executable, os.path.join(BASE_DIR, "main.py")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=BASE_DIR, env=env,
            )
            with open(PID_FILE, "w") as f:
                f.write(str(proc.pid))
            return True, f"Agent 已启动 (MQTT, PID: {proc.pid})"

        elif cmd == "stop":
            if os.path.exists(PID_FILE):
                with open(PID_FILE) as f:
                    pid_str = f.read().strip()
                if pid_str:
                    try:
                        os.kill(int(pid_str), signal.SIGTERM)
                        time.sleep(1)
                        # 强制终止
                        try:
                            os.kill(int(pid_str), signal.SIGKILL)
                        except:
                            pass
                    except ProcessLookupError:
                        pass
                os.remove(PID_FILE)
            return True, "Agent 已停止"

        elif cmd == "restart":
            run_agent_cmd("stop")
            time.sleep(1)
            return run_agent_cmd("start")

        return True, f"操作 {cmd} 完成"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

# Serve the SPA HTML at /
@app.route("/")
def index():
    return HTML_PAGE


# ---- Config ----

@app.route("/api/config")
def api_get_config():
    cfg = get_config_for_ui()
    # Remove raw key from output
    cfg.pop("_LLM_API_KEY_RAW", None)
    return jsonify(cfg)


@app.route("/api/config", methods=["POST"])
def api_save_config():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "message": "无效的 JSON"})
    result = save_config_updates(data)
    return jsonify(result)


# ---- Status ----

@app.route("/api/status")
def api_status():
    return jsonify({
        "stats": get_stats(),
        "conversations": get_conversations(limit=10),
        "deliveries": get_deliveries(limit=10),
    })


# ---- Agent control ----

@app.route("/api/start", methods=["POST"])
def api_start():
    ok, msg = run_agent_cmd("start")
    return jsonify({"success": ok, "message": msg})


@app.route("/api/mqtt", methods=["POST"])
def api_mqtt():
    ok, msg = run_agent_cmd("mqtt")
    return jsonify({"success": ok, "message": msg})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    ok, msg = run_agent_cmd("stop")
    return jsonify({"success": ok, "message": msg})


@app.route("/api/restart", methods=["POST"])
def api_restart():
    ok, msg = run_agent_cmd("restart")
    return jsonify({"success": ok, "message": msg})


# ---- LLM test ----

@app.route("/api/llm-test", methods=["POST"])
def api_llm_test():
    cfg = _parse_config_py()
    api_url = cfg.get("LLM_API_URL", "")
    model = cfg.get("LLM_MODEL", "")
    api_key = _get_api_key()
    timeout = cfg.get("LLM_TIMEOUT", 30)

    if not api_url:
        return jsonify({"success": False, "message": "LLM_API_URL 未配置"})
    if not api_key:
        return jsonify({"success": False, "message": "API Key 未配置"})

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Hello, respond with 'OK' only."}],
            "max_tokens": 10,
        }
        resp = requests.post(
            api_url, headers=headers, json=payload, timeout=timeout
        )
        if resp.status_code == 200:
            result = resp.json()
            reply = ""
            try:
                reply = result["choices"][0]["message"]["content"]
            except (KeyError, IndexError):
                reply = str(result)[:200]
            return jsonify({
                "success": True,
                "message": f"连接成功！响应: {reply[:100]}",
            })
        else:
            return jsonify({
                "success": False,
                "message": f"HTTP {resp.status_code}: {resp.text[:300]}",
            })
    except requests.exceptions.ConnectTimeout:
        return jsonify({"success": False, "message": "连接超时"})
    except requests.exceptions.ConnectionError as e:
        return jsonify({"success": False, "message": f"连接失败: {e}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ---- Proxy test ----

@app.route("/api/proxy-test", methods=["POST"])
def api_proxy_test():
    cfg = _parse_config_py()
    proxy = cfg.get("PROXY", "")

    if not proxy:
        return jsonify({"success": False, "message": "代理地址未配置"})

    try:
        proxies = {
            "http": proxy,
            "https": proxy,
        }
        resp = requests.get(
            "http://httpbin.org/ip",
            proxies=proxies,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            ip = data.get("origin", "?")
            return jsonify({
                "success": True,
                "message": f"代理可用，出口 IP: {ip}",
                "ip": ip,
            })
        else:
            return jsonify({
                "success": False,
                "message": f"代理返回 HTTP {resp.status_code}",
            })
    except requests.exceptions.ConnectTimeout:
        return jsonify({"success": False, "message": "代理连接超时"})
    except requests.exceptions.ConnectionError as e:
        return jsonify({"success": False, "message": f"代理连接失败: {e}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ---- Logs ----

@app.route("/api/logs")
def api_logs():
    level = request.args.get("level", "")
    max_lines = int(request.args.get("lines", 200))
    if max_lines > 5000:
        max_lines = 5000
    logs = get_logs(max_lines=max_lines, level=level if level else None)
    return jsonify({"logs": logs})


@app.route("/api/logs/clear", methods=["POST"])
def api_logs_clear():
    log_file = os.path.join(LOG_DIR, "agent.log")
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("")
        return jsonify({"success": True, "message": "日志已清空"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ---- Conversations & Deliveries ----

@app.route("/api/conversations")
def api_conversations():
    limit = int(request.args.get("limit", 50))
    return jsonify(get_conversations(limit=limit))


@app.route("/api/deliveries")
def api_deliveries():
    limit = int(request.args.get("limit", 50))
    return jsonify(get_deliveries(limit=limit))


# ---------------------------------------------------------------------------
# HTML template (SPA with all tabs, GitHub Dark theme)
# ---------------------------------------------------------------------------

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BOSS 求职 Agent 控制面板</title>
<style>
/* === Reset & Base === */
*, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }
html { font-size:14px; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans SC', sans-serif;
  background:#0d1117; color:#c9d1d9; min-height:100vh;
}

/* === Layout === */
.app-layout { display:flex; min-height:100vh; }
.sidebar {
  width:260px; flex-shrink:0; background:#161b22;
  border-right:1px solid #30363d; display:flex; flex-direction:column;
  position:fixed; top:0; left:0; bottom:0; z-index:100;
}
.sidebar-header {
  padding:16px 20px; border-bottom:1px solid #30363d;
  display:flex; align-items:center; gap:10px;
}
.sidebar-header .logo { font-size:22px; }
.sidebar-header h1 { font-size:15px; font-weight:600; color:#e6edf3; }
.sidebar-header .version { font-size:10px; color:#8b949e; margin-left:auto; }
.sidebar-nav { flex:1; padding:8px 0; overflow-y:auto; }
.nav-item {
  display:flex; align-items:center; gap:10px; padding:10px 20px;
  color:#8b949e; cursor:pointer; font-size:13px; transition:all .15s;
  border-left:3px solid transparent; text-decoration:none;
}
.nav-item:hover { color:#e6edf3; background:#1c2128; }
.nav-item.active { color:#e6edf3; background:#1c2128; border-left-color:#58a6ff; font-weight:500; }
.nav-item .icon { font-size:16px; width:20px; text-align:center; }
.nav-item .badge {
  margin-left:auto; background:#30363d; color:#8b949e; font-size:10px;
  padding:1px 8px; border-radius:10px;
}

.main-area {
  flex:1; margin-left:260px; padding:24px 32px; min-width:0;
}

/* === Tab content === */
.tab-content { display:none; }
.tab-content.active { display:block; }

/* === Cards === */
.card {
  background:#161b22; border:1px solid #30363d; border-radius:8px;
  padding:20px; margin-bottom:16px;
}
.card-title {
  font-size:14px; font-weight:600; color:#e6edf3;
  margin-bottom:16px; display:flex; align-items:center; gap:8px;
}

/* === Status bar === */
.status-bar {
  display:flex; align-items:center; gap:20px; flex-wrap:wrap;
  margin-bottom:20px; padding:16px 20px; background:#161b22;
  border:1px solid #30363d; border-radius:8px;
}
.status-indicator { display:flex; align-items:center; gap:10px; }
.status-dot {
  width:12px; height:12px; border-radius:50%; display:inline-block; flex-shrink:0;
}
.dot-running { background:#3fb950; box-shadow:0 0 8px #3fb950aa; }
.dot-stopped { background:#da3633; }
.status-text { font-size:16px; font-weight:600; }
.pid-tag { font-size:11px; color:#8b949e; background:#21262d; padding:2px 8px; border-radius:4px; }
.mode-tag {
  display:inline-block; padding:2px 10px; border-radius:4px; font-size:11px;
  background:#1f6feb33; color:#58a6ff; border:1px solid #1f6feb66;
}
.status-actions { display:flex; gap:8px; flex-wrap:wrap; margin-left:auto; }

/* === Buttons === */
.btn {
  display:inline-flex; align-items:center; gap:6px; padding:7px 16px;
  border:none; border-radius:6px; font-size:13px; font-weight:500;
  cursor:pointer; transition:all .15s; text-decoration:none; white-space:nowrap;
}
.btn:hover { filter:brightness(1.15); }
.btn:active { filter:brightness(0.9); }
.btn:disabled { opacity:0.4; cursor:not-allowed; filter:none; }
.btn-start { background:#238636; color:#fff; }
.btn-mqtt { background:#1f6feb; color:#fff; }
.btn-stop { background:#da3633; color:#fff; }
.btn-restart { background:#d29922; color:#fff; }
.btn-primary { background:#238636; color:#fff; }
.btn-secondary { background:#21262d; color:#c9d1d9; border:1px solid #30363d; }
.btn-secondary:hover { background:#30363d; }
.btn-danger { background:#da3633; color:#fff; }
.btn-sm { font-size:11px; padding:4px 10px; }
.btn-xs { font-size:10px; padding:2px 8px; }
.btn-icon { background:transparent; border:none; color:#8b949e; cursor:pointer; padding:4px; }
.btn-icon:hover { color:#e6edf3; }

/* === Stat cards row === */
.stats-row { display:grid; grid-template-columns:repeat(auto-fill, minmax(140px,1fr)); gap:12px; margin-bottom:20px; }
.stat-card {
  background:#0d1117; border:1px solid #30363d; border-radius:6px;
  padding:14px 16px; text-align:center;
}
.stat-value { font-size:24px; font-weight:700; color:#58a6ff; line-height:1.2; }
.stat-label { font-size:11px; color:#8b949e; margin-top:4px; text-transform:uppercase; letter-spacing:.5px; }

/* === Tables === */
.table-wrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th {
  text-align:left; padding:8px 8px; border-bottom:2px solid #30363d;
  color:#8b949e; font-size:11px; text-transform:uppercase; letter-spacing:.5px;
  white-space:nowrap;
}
td { padding:8px 8px; border-bottom:1px solid #21262d; color:#c9d1d9; }
tr:hover td { background:#1c2128; }
.cell-msg { max-width:250px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.cell-time { color:#8b949e; font-size:11px; white-space:nowrap; }
.status-badge {
  display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:500;
}
.status-badge.active { background:#23863633; color:#3fb950; border:1px solid #23863666; }
.status-badge.done { background:#1f6feb33; color:#58a6ff; border:1px solid #1f6feb66; }
.status-badge.pending { background:#d2992233; color:#d29922; border:1px solid #d2992266; }
.status-badge.unknown { background:#30363d33; color:#8b949e; border:1px solid #30363d; }

/* === Forms === */
.form-group { margin-bottom:16px; }
.form-group label {
  display:block; font-size:12px; font-weight:500; color:#8b949e;
  margin-bottom:4px; text-transform:uppercase; letter-spacing:.5px;
}
.form-group label .hint { color:#484f58; text-transform:none; letter-spacing:0; margin-left:4px; font-size:11px; }
.form-control {
  width:100%; padding:8px 12px; background:#0d1117; border:1px solid #30363d;
  border-radius:6px; color:#c9d1d9; font-size:13px; font-family:inherit; transition:border-color .15s;
}
.form-control:focus { outline:none; border-color:#58a6ff; box-shadow:0 0 0 2px #58a6ff33; }
textarea.form-control { resize:vertical; min-height:80px; font-family:'Cascadia Code','Fira Code',monospace; font-size:12px; line-height:1.5; }
select.form-control { cursor:pointer; }
input[type="number"].form-control { font-family:monospace; }
.form-row { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.form-row-3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; }

/* === Checkbox grid === */
.checkbox-grid { display:flex; flex-wrap:wrap; gap:8px; }
.checkbox-grid label {
  display:flex; align-items:center; gap:6px; padding:4px 12px;
  background:#0d1117; border:1px solid #30363d; border-radius:6px;
  cursor:pointer; font-size:13px; color:#c9d1d9; transition:all .15s; user-select:none;
}
.checkbox-grid label:hover { border-color:#58a6ff; }
.checkbox-grid input:checked + span { color:#58a6ff; }
.checkbox-grid label:has(input:checked) { border-color:#1f6feb; background:#1f6feb11; }
.checkbox-grid input[type="checkbox"] { accent-color:#58a6ff; }

/* === Tag input === */
.tag-input-wrap {
  display:flex; flex-wrap:wrap; gap:6px; padding:6px 8px;
  background:#0d1117; border:1px solid #30363d; border-radius:6px; min-height:36px; cursor:text;
}
.tag-input-wrap:focus-within { border-color:#58a6ff; box-shadow:0 0 0 2px #58a6ff33; }
.tag { display:inline-flex; align-items:center; gap:4px; padding:2px 8px; background:#1f6feb33; border:1px solid #1f6feb66; border-radius:4px; font-size:12px; color:#58a6ff; }
.tag-remove { cursor:pointer; font-size:14px; line-height:1; color:#58a6ff88; }
.tag-remove:hover { color:#f85149; }
.tag-input-field { flex:1; min-width:80px; border:none; background:transparent; color:#c9d1d9; font-size:13px; outline:none; padding:2px 0; }

/* === Mode select cards === */
.mode-cards { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.mode-card {
  padding:16px; border:2px solid #30363d; border-radius:8px; cursor:pointer;
  transition:all .15s; text-align:center;
}
.mode-card:hover { border-color:#8b949e; }
.mode-card.active { border-color:#58a6ff; background:#1f6feb11; }
.mode-card .mode-icon { font-size:28px; margin-bottom:8px; }
.mode-card .mode-name { font-size:14px; font-weight:600; color:#e6edf3; }
.mode-card .mode-desc { font-size:11px; color:#8b949e; margin-top:4px; }

/* === Log viewer === */
.log-box {
  font-family:'Cascadia Code','Fira Code','SF Mono',monospace; font-size:12px;
  color:#8b949e; line-height:1.6; height:400px; overflow-y:auto;
  padding:12px; background:#0d1117; border-radius:6px; border:1px solid #30363d;
  white-space:pre-wrap; word-break:break-all;
}
.log-box .log-info { color:#8b949e; }
.log-box .log-warn { color:#d29922; }
.log-box .log-error { color:#f85149; }
.log-toolbar { display:flex; gap:8px; align-items:center; margin-bottom:12px; flex-wrap:wrap; }
.log-toolbar .spacer { flex:1; }

/* === Toast === */
.toast-container {
  position:fixed; top:16px; right:16px; z-index:9999; display:flex; flex-direction:column; gap:8px; pointer-events:none;
}
.toast {
  padding:10px 18px; border-radius:6px; color:#fff; font-size:13px;
  pointer-events:auto; animation:toastIn .25s ease-out;
  box-shadow:0 4px 12px rgba(0,0,0,.4); max-width:400px;
}
.toast-success { background:#238636; border:1px solid #3fb950; }
.toast-error { background:#da3633; border:1px solid #f85149; }
.toast-info { background:#1f6feb; border:1px solid #58a6ff; }
@keyframes toastIn { from{opacity:0;transform:translateY(-12px)} to{opacity:1;transform:translateY(0)} }
@keyframes toastOut { from{opacity:1;transform:translateY(0)} to{opacity:0;transform:translateY(-12px)} }

/* === Loading spinner === */
.spinner {
  display:inline-block; width:14px; height:14px; border:2px solid rgba(255,255,255,.2);
  border-radius:50%; border-top-color:#fff; animation:spin .6s linear infinite;
}
@keyframes spin { to{transform:rotate(360deg)} }

/* === Proxy status === */
.proxy-status { display:inline-flex; align-items:center; gap:6px; font-size:12px; padding:4px 10px; border-radius:4px; }
.proxy-status.ok { color:#3fb950; background:#23863622; border:1px solid #23863666; }
.proxy-status.fail { color:#f85149; background:#da363322; border:1px solid #da363366; }
.proxy-status.unknown { color:#8b949e; background:#30363d33; border:1px solid #30363d; }

/* === Misc === */
.text-muted { color:#8b949e; }
.text-success { color:#3fb950; }
.text-danger { color:#f85149; }
.text-warning { color:#d29922; }
.text-accent { color:#58a6ff; }
.flex { display:flex; }
.flex-center { align-items:center; }
.gap-8 { gap:8px; }
.gap-12 { gap:12px; }
.mt-8 { margin-top:8px; }
.mt-12 { margin-top:12px; }
.mb-8 { margin-bottom:8px; }
.mb-12 { margin-bottom:12px; }
.w-full { width:100%; }
.truncate { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

/* === Responsive === */
@media (max-width:900px) {
  .sidebar { width:200px; }
  .main-area { margin-left:200px; padding:16px; }
  .form-row, .form-row-3 { grid-template-columns:1fr; }
  .mode-cards { grid-template-columns:1fr; }
}
@media (max-width:640px) {
  .sidebar { display:none; }
  .main-area { margin-left:0; }
  .stats-row { grid-template-columns:repeat(2,1fr); }
  .status-actions { margin-left:0; width:100%; }
}
</style>
</head>
<body>

<div class="app-layout">
  <!-- Sidebar -->
  <aside class="sidebar">
    <div class="sidebar-header">
      <span class="logo">🤖</span>
      <h1>BOSS Agent</h1>
      <span class="version">v2.0</span>
    </div>
    <nav class="sidebar-nav" id="sidebarNav">
      <div class="nav-item active" data-tab="overview"><span class="icon">📊</span> 总览</div>
      <div class="nav-item" data-tab="job"><span class="icon">⚙️</span> 求职设置</div>
      <div class="nav-item" data-tab="llm"><span class="icon">🤖</span> LLM 设置</div>
      <div class="nav-item" data-tab="connection"><span class="icon">🔌</span> 连接设置</div>
      <div class="nav-item" data-tab="logs"><span class="icon">📋</span> 日志</div>
    </nav>
  </aside>

  <!-- Main -->
  <main class="main-area">
    <!-- Toast container -->
    <div class="toast-container" id="toastContainer"></div>

    <!-- ==================== TAB: OVERVIEW ==================== -->
    <div class="tab-content active" id="tab-overview">

      <div class="status-bar" id="statusBar">
        <div class="status-indicator">
          <span class="status-dot dot-stopped" id="statusDot"></span>
          <span class="status-text" id="statusText">检查中...</span>
          <span class="mode-tag" id="statusMode">-</span>
          <span class="pid-tag" id="statusPid"></span>
        </div>
        <div class="status-actions">
          <button class="btn btn-start" id="btnStart" onclick="doAction('start')">▶ 启动</button>
          <button class="btn btn-mqtt" id="btnMqtt" onclick="doAction('mqtt')">📡 MQTT启动</button>
          <button class="btn btn-stop" id="btnStop" onclick="doAction('stop')" disabled>⏹ 停止</button>
          <button class="btn btn-restart" onclick="doAction('restart')">🔄 重启</button>
        </div>
      </div>

      <div class="stats-row" id="statsRow">
        <div class="stat-card"><div class="stat-value" id="statConvs">-</div><div class="stat-label">对话数</div></div>
        <div class="stat-card"><div class="stat-value" id="statReplies">-</div><div class="stat-label">已回复</div></div>
        <div class="stat-card"><div class="stat-value" id="statDelivered">-</div><div class="stat-label">已投递</div></div>
        <div class="stat-card"><div class="stat-value" id="statSent">-</div><div class="stat-label">已发送消息</div></div>
        <div class="stat-card"><div class="stat-value" id="statLastActive" style="font-size:18px;">-</div><div class="stat-label">最后活跃</div></div>
      </div>

      <div class="card">
        <div class="card-title">📬 最近投递记录</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>时间</th><th>城市</th><th>职位</th><th>薪资</th><th>公司</th></tr></thead>
            <tbody id="deliveriesBody"><tr><td colspan="5" class="text-muted" style="text-align:center;padding:20px;">加载中...</td></tr></tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <div class="card-title">💬 对话列表</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>HR</th><th>公司</th><th>职位</th><th>状态</th><th>最后消息</th><th>时间</th></tr></thead>
            <tbody id="convsBody"><tr><td colspan="6" class="text-muted" style="text-align:center;padding:20px;">加载中...</td></tr></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ==================== TAB: JOB SETTINGS ==================== -->
    <div class="tab-content" id="tab-job">
      <div class="card">
        <div class="card-title">⚙️ 求职设置</div>
        <form id="jobForm" onsubmit="saveConfig(event)">
          <div class="form-row">
            <div class="form-group">
              <label>薪资底线 (SALARY_MIN)</label>
              <input type="number" class="form-control" id="cfg_SALARY_MIN" min="0" step="1000">
            </div>
            <div class="form-group">
              <label>回复风格 (REPLY_STYLE)</label>
              <select class="form-control" id="cfg_REPLY_STYLE">
                <option value="正式">正式</option>
                <option value="友好">友好</option>
                <option value="专业">专业</option>
                <option value="简洁">简洁</option>
              </select>
            </div>
          </div>

          <div class="form-group">
            <label>目标城市 (CITIES)</label>
            <div class="checkbox-grid" id="cfg_CITIES">
              <label><input type="checkbox" value="南通"><span>南通</span></label>
              <label><input type="checkbox" value="南京"><span>南京</span></label>
              <label><input type="checkbox" value="上海"><span>上海</span></label>
              <label><input type="checkbox" value="苏州"><span>苏州</span></label>
              <label><input type="checkbox" value="无锡"><span>无锡</span></label>
              <label><input type="checkbox" value="杭州"><span>杭州</span></label>
              <label><input type="checkbox" value="北京"><span>北京</span></label>
              <label><input type="checkbox" value="深圳"><span>深圳</span></label>
            </div>
            <div class="tag-input-wrapper" style="margin-top:8px;">
              <div class="tag-list" id="cfg_CITIES_custom"></div>
              <input type="text" class="tag-input" id="cfg_CITIES_input"
                     placeholder="输入自定义城市后按回车添加" style="width:100%;">
            </div>
          </div>

          <div class="form-group">
            <label>个人人设 (PERSONA)</label>
            <textarea class="form-control" id="cfg_PERSONA" rows="6"></textarea>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label>轮询间隔 (POLL_INTERVAL) <span class="hint">秒</span></label>
              <input type="number" class="form-control" id="cfg_POLL_INTERVAL" min="1" step="1">
            </div>
            <div class="form-group">
              <label>每小时最大回复 (MAX_REPLIES_PER_HOUR)</label>
              <input type="number" class="form-control" id="cfg_MAX_REPLIES_PER_HOUR" min="1" step="1">
            </div>
          </div>

          <div class="form-group">
            <label>敏感关键词 (SENSITIVE_KEYWORDS) <span class="hint">输入后按回车添加</span></label>
            <div class="tag-input-wrap" id="tagwrap_SENSITIVE_KEYWORDS" onclick="focusTagInput(this)">
              <div class="tags-list" id="tags_SENSITIVE_KEYWORDS"></div>
              <input type="text" class="tag-input-field" placeholder="输入关键词后回车..."
                     onkeydown="tagKeydown(event, 'SENSITIVE_KEYWORDS')">
            </div>
          </div>

          <div class="form-group">
            <label>轻松型岗位关键词 (EASY_KEYWORDS) <span class="hint">输入后按回车添加</span></label>
            <div class="tag-input-wrap" id="tagwrap_EASY_KEYWORDS" onclick="focusTagInput(this)">
              <div class="tags-list" id="tags_EASY_KEYWORDS"></div>
              <input type="text" class="tag-input-field" placeholder="输入关键词后回车..."
                     onkeydown="tagKeydown(event, 'EASY_KEYWORDS')">
            </div>
          </div>

          <button type="submit" class="btn btn-primary">💾 保存设置</button>
        </form>
      </div>
    </div>

    <!-- ==================== TAB: LLM SETTINGS ==================== -->
    <div class="tab-content" id="tab-llm">
      <div class="card">
        <div class="card-title">🤖 LLM 设置</div>
        <form id="llmForm" onsubmit="saveConfig(event)">
          <div class="form-group">
            <label>API 地址 (LLM_API_URL)</label>
            <input type="text" class="form-control" id="cfg_LLM_API_URL" placeholder="https://api.openai.com/v1/chat/completions">
          </div>
          <div class="form-group">
            <label>模型名 (LLM_MODEL)</label>
            <input type="text" class="form-control" id="cfg_LLM_MODEL" placeholder="gpt-3.5-turbo">
          </div>
          <div class="form-group">
            <label>API Key (LLM_API_KEY)</label>
            <div class="flex gap-8" style="position:relative;">
              <input type="password" class="form-control" id="cfg_LLM_API_KEY" placeholder="sk-..." style="padding-right:40px;">
              <button type="button" class="btn-icon" onclick="toggleKeyVisibility()" style="position:absolute;right:8px;top:50%;transform:translateY(-50%);font-size:16px;" title="显示/隐藏">👁</button>
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label>超时 (LLM_TIMEOUT) <span class="hint">秒</span></label>
              <input type="number" class="form-control" id="cfg_LLM_TIMEOUT" min="1" step="1">
            </div>
            <div class="form-group">
              <label>重试次数 (LLM_MAX_RETRIES)</label>
              <input type="number" class="form-control" id="cfg_LLM_MAX_RETRIES" min="0" step="1">
            </div>
          </div>
          <div class="flex gap-8">
            <button type="submit" class="btn btn-primary">💾 保存设置</button>
            <button type="button" class="btn btn-secondary" onclick="testLLM()">🔌 测试连接</button>
          </div>
          <div id="llmTestResult" class="mt-8" style="display:none;"></div>
        </form>
      </div>
    </div>

    <!-- ==================== TAB: CONNECTION ==================== -->
    <div class="tab-content" id="tab-connection">
      <div class="card">
        <div class="card-title">🔌 连接设置</div>
        <form id="connForm" onsubmit="saveConfig(event)">
          <div class="form-group">
            <label>工作模式</label>
            <div class="mode-cards" id="modeCards">
              <div class="mode-card" data-mode="browser" onclick="selectMode('browser')">
                <div class="mode-icon">🌐</div>
                <div class="mode-name">浏览器模式</div>
                <div class="mode-desc">使用真实 Chrome 浏览器操作，更隐蔽</div>
              </div>
              <div class="mode-card" data-mode="mqtt" onclick="selectMode('mqtt')">
                <div class="mode-icon">📡</div>
                <div class="mode-name">MQTT 模式</div>
                <div class="mode-desc">无需浏览器，通过 MQTT 直连，效率更高</div>
              </div>
            </div>
            <div style="font-size:11px;color:#8b949e;margin-top:6px;">切换模式后需重启 Agent 生效</div>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label>CDP 端口 (CHROME_CDP_PORT)</label>
              <input type="number" class="form-control" id="cfg_CHROME_CDP_PORT" min="0" max="65535" step="1">
            </div>
            <div class="form-group">
              <label>代理地址 (PROXY)</label>
              <input type="text" class="form-control" id="cfg_PROXY" placeholder="http://127.0.0.1:7890">
            </div>
          </div>

          <div class="form-group">
            <label>代理状态</label>
            <div class="flex gap-8 flex-center">
              <span class="proxy-status unknown" id="proxyStatus">⏳ 未检测</span>
              <button type="button" class="btn btn-secondary btn-sm" onclick="testProxy()">🔍 测试代理</button>
            </div>
          </div>

          <div class="flex gap-8">
            <button type="submit" class="btn btn-primary">💾 保存设置</button>
          </div>
          <div id="proxyTestResult" class="mt-8" style="display:none;"></div>
        </form>
      </div>
    </div>

    <!-- ==================== TAB: LOGS ==================== -->
    <div class="tab-content" id="tab-logs">
      <div class="card">
        <div class="card-title">📋 运行日志</div>
        <div class="log-toolbar">
          <label style="font-size:12px;color:#8b949e;display:flex;align-items:center;gap:6px;">
            级别:
            <select id="logLevel" onchange="loadLogs()" style="background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;padding:2px 8px;font-size:12px;">
              <option value="">全部</option>
              <option value="INFO">INFO</option>
              <option value="WARN">WARN</option>
              <option value="ERROR">ERROR</option>
            </select>
          </label>
          <span style="font-size:11px;color:#8b949e;" id="logLineCount"></span>
          <div class="spacer"></div>
          <button class="btn btn-secondary btn-sm" onclick="copyLogs()">📄 复制</button>
          <button class="btn btn-secondary btn-sm" onclick="clearLogs()">🗑 清空</button>
        </div>
        <div class="log-box" id="logBox">加载中...</div>
      </div>
    </div>
  </main>
</div>

<script>
// ============================================================
// State
// ============================================================
let logInterval = null;
let statusInterval = null;
let tagInputFields = {};

// ============================================================
// Tab switching
// ============================================================
document.querySelectorAll('.nav-item').forEach(el => {
  el.addEventListener('click', function() {
    const tab = this.dataset.tab;
    switchTab(tab);
  });
});

function switchTab(tab) {
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelector(`.nav-item[data-tab="${tab}"]`).classList.add('active');
  document.getElementById(`tab-${tab}`).classList.add('active');

  // Start/stop log polling
  if (tab === 'logs') {
    if (!logInterval) {
      loadLogs();
      logInterval = setInterval(loadLogs, 3000);
    }
  } else {
    if (logInterval) {
      clearInterval(logInterval);
      logInterval = null;
    }
  }
}

// ============================================================
// Toast
// ============================================================
function showToast(msg, type='info') {
  const container = document.getElementById('toastContainer');
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = msg;
  container.appendChild(t);
  setTimeout(() => {
    t.style.animation = 'toastOut .25s ease-out forwards';
    setTimeout(() => t.remove(), 300);
  }, 3500);
}

// ============================================================
// API helpers
// ============================================================
async function apiGet(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function apiPost(url, body={}) {
  const r = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ============================================================
// Overview — status + stats
// ============================================================
async function refreshStatus() {
  try {
    const data = await apiGet('/api/status');
    const s = data.stats;

    // Status
    const dot = document.getElementById('statusDot');
    const text = document.getElementById('statusText');
    const mode = document.getElementById('statusMode');
    const pid = document.getElementById('statusPid');

    if (s.running) {
      dot.className = 'status-dot dot-running';
      text.textContent = '运行中';
      mode.textContent = s.mode || '-';
      pid.textContent = `PID: ${s.pid || '-'}`;
    } else {
      dot.className = 'status-dot dot-stopped';
      text.textContent = '已停止';
      mode.textContent = '-';
      pid.textContent = '';
    }

    document.getElementById('btnStart').disabled = s.running;
    document.getElementById('btnMqtt').disabled = s.running;
    document.getElementById('btnStop').disabled = !s.running;

    // Stats
    document.getElementById('statConvs').textContent = s.conversations ?? 0;
    document.getElementById('statReplies').textContent = s.replies ?? 0;
    document.getElementById('statDelivered').textContent = s.delivered ?? 0;
    document.getElementById('statSent').textContent = s.sent_msgs ?? 0;
    document.getElementById('statLastActive').textContent = s.last_active || '-';

    // Deliveries table
    const deliveriesBody = document.getElementById('deliveriesBody');
    if (data.deliveries && data.deliveries.length > 0) {
      deliveriesBody.innerHTML = data.deliveries.slice(-10).map(d => {
        const time = d.time || d.timestamp || '';
        const city = d.city || '';
        const title = (d.title || d.job_title || '').substring(0,25);
        const salary = (d.salary || '').substring(0,12);
        const company = (d.company || d.company_name || '').substring(0,20);
        return `<tr><td class="cell-time">${escHtml(time)}</td><td>${escHtml(city)}</td><td class="cell-msg">${escHtml(title)}</td><td>${escHtml(salary)}</td><td class="cell-msg">${escHtml(company)}</td></tr>`;
      }).join('');
    } else {
      deliveriesBody.innerHTML = '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:20px;">暂无投递记录</td></tr>';
    }

    // Conversations table
    const convsBody = document.getElementById('convsBody');
    if (data.conversations && data.conversations.length > 0) {
      convsBody.innerHTML = data.conversations.map(c => {
        const status = c.status || 'unknown';
        const badgeClass = status === 'active' ? 'active' : (status === 'done' ? 'done' : 'unknown');
        return `<tr>
          <td>${escHtml(c.hr_name || '?')}</td>
          <td class="cell-msg">${escHtml(c.company || '?')}</td>
          <td class="cell-msg">${escHtml(c.position || '?')}</td>
          <td><span class="status-badge ${badgeClass}">${escHtml(status)}</span></td>
          <td class="cell-msg">${escHtml((c.last_msg || '').substring(0,60))}</td>
          <td class="cell-time">${escHtml(c.last_time || '')}</td>
        </tr>`;
      }).join('');
    } else {
      convsBody.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;padding:20px;">暂无对话记录</td></tr>';
    }
  } catch (e) {
    console.error('Status refresh failed:', e);
  }
}

function escHtml(s) {
  if (typeof s !== 'string') return String(s || '');
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ============================================================
// Agent control
// ============================================================
async function doAction(action) {
  const btn = event && event.target ? (event.target.closest('button') || event.target) : null;
  if (btn) {
    btn.disabled = true;
    const orig = btn.innerHTML;
    btn.innerHTML = '<span class="spinner"></span>';
    // restore after 10s safety
    setTimeout(() => { btn.innerHTML = orig; }, 10000);
  }

  try {
    const result = await apiPost(`/api/${action}`);
    showToast(result.message, result.success ? 'success' : 'error');
    await refreshStatus();
  } catch (e) {
    showToast('操作失败: ' + e.message, 'error');
  }
  if (btn) setTimeout(() => refreshStatus(), 500);
}

// ============================================================
// Config — load + save
// ============================================================
async function loadConfig() {
  try {
    const cfg = await apiGet('/api/config');
    console.log('Config loaded:', cfg);

    // --- JOB TAB ---
    setField('cfg_SALARY_MIN', cfg.SALARY_MIN);
    setField('cfg_REPLY_STYLE', cfg.REPLY_STYLE || '正式');
    setField('cfg_PERSONA', cfg.PERSONA || '');
    setField('cfg_POLL_INTERVAL', cfg.POLL_INTERVAL);
    setField('cfg_MAX_REPLIES_PER_HOUR', cfg.MAX_REPLIES_PER_HOUR);

    // Cities checkboxes + custom tags
    const cities = Array.isArray(cfg.CITIES) ? cfg.CITIES : [];
    const predefinedCities = ['南通','南京','上海','苏州','无锡','杭州','北京','深圳'];
    document.querySelectorAll('#cfg_CITIES input[type="checkbox"]').forEach(cb => {
      cb.checked = cities.includes(cb.value);
    });
    // Custom cities (not in predefined list)
    const customCities = cities.filter(c => !predefinedCities.includes(c));
    renderCitiesCustomTags(customCities);

    // Tag fields
    setTags('SENSITIVE_KEYWORDS', cfg.SENSITIVE_KEYWORDS || []);
    setTags('EASY_KEYWORDS', cfg.EASY_KEYWORDS || []);

    // --- LLM TAB ---
    setField('cfg_LLM_API_URL', cfg.LLM_API_URL || '');
    setField('cfg_LLM_MODEL', cfg.LLM_MODEL || '');
    setField('cfg_LLM_API_KEY', cfg.LLM_API_KEY || '');
    setField('cfg_LLM_TIMEOUT', cfg.LLM_TIMEOUT);
    setField('cfg_LLM_MAX_RETRIES', cfg.LLM_MAX_RETRIES);

    // --- CONNECTION TAB ---
    setField('cfg_CHROME_CDP_PORT', cfg.CHROME_CDP_PORT);
    setField('cfg_PROXY', cfg.PROXY || '');
    selectModeUI(cfg.MQTT_MODE === true ? 'mqtt' : 'browser');

  } catch (e) {
    showToast('加载配置失败: ' + e.message, 'error');
  }
}

function setField(id, val) {
  const el = document.getElementById(id);
  if (!el) return;
  if (val === null || val === undefined) val = '';
  el.value = val;
}

function getField(id) {
  const el = document.getElementById(id);
  return el ? el.value : '';
}

async function saveConfig(event) {
  event.preventDefault();

  const form = event.target;
  const updates = {};

  if (form.id === 'jobForm') {
    updates.SALARY_MIN = parseInt(getField('cfg_SALARY_MIN')) || 0;
    updates.REPLY_STYLE = getField('cfg_REPLY_STYLE');
    updates.PERSONA = getField('cfg_PERSONA');
    updates.POLL_INTERVAL = parseInt(getField('cfg_POLL_INTERVAL')) || 10;
    updates.MAX_REPLIES_PER_HOUR = parseInt(getField('cfg_MAX_REPLIES_PER_HOUR')) || 30;

    // Cities
    updates.CITIES = [];
    document.querySelectorAll('#cfg_CITIES input[type="checkbox"]:checked').forEach(cb => {
      updates.CITIES.push(cb.value);
    });
    // Custom cities from tags
    document.querySelectorAll('#cfg_CITIES_custom .tag-item').forEach(tag => {
      const city = tag.textContent.replace('\u00d7','').trim();
      if (city && !updates.CITIES.includes(city)) updates.CITIES.push(city);
    });

    // Tags
    updates.SENSITIVE_KEYWORDS = getTags('SENSITIVE_KEYWORDS');
    updates.EASY_KEYWORDS = getTags('EASY_KEYWORDS');
  } else if (form.id === 'llmForm') {
    updates.LLM_API_URL = getField('cfg_LLM_API_URL');
    updates.LLM_MODEL = getField('cfg_LLM_MODEL');
    updates.LLM_API_KEY = getField('cfg_LLM_API_KEY');
    updates.LLM_TIMEOUT = parseInt(getField('cfg_LLM_TIMEOUT')) || 30;
    updates.LLM_MAX_RETRIES = parseInt(getField('cfg_LLM_MAX_RETRIES')) || 3;
  } else if (form.id === 'connForm') {
    updates.CHROME_CDP_PORT = parseInt(getField('cfg_CHROME_CDP_PORT')) || 0;
    updates.PROXY = getField('cfg_PROXY');
    // MQTT_MODE is env-only, not saved to config.py — we show but don't save here
  }

  try {
    const result = await apiPost('/api/config', updates);
    showToast(result.message, result.success ? 'success' : 'error');
    // Reload config to reflect changes
    await loadConfig();
  } catch (e) {
    showToast('保存失败: ' + e.message, 'error');
  }
}

// ============================================================
// Tag input component
// ============================================================
function focusTagInput(wrap) {
  const input = wrap.querySelector('.tag-input-field');
  if (input) input.focus();
}

function tagKeydown(event, field) {
  if (event.key === 'Enter') {
    event.preventDefault();
    const input = event.target;
    const value = input.value.trim();
    if (value) {
      addTag(field, value);
      input.value = '';
    }
  } else if (event.key === 'Backspace' && event.target.value === '') {
    // Remove last tag
    removeLastTag(field);
  }
}

function addTag(field, value) {
  const container = document.getElementById(`tags_${field}`);
  if (!container) return;
  // Deduplicate
  const existing = container.querySelectorAll('.tag');
  for (const t of existing) {
    if (t.dataset.value === value) return;
  }
  const tag = document.createElement('span');
  tag.className = 'tag';
  tag.dataset.value = value;
  tag.innerHTML = `${escHtml(value)}<span class="tag-remove" onclick="removeTag('${field}', this)">×</span>`;
  container.appendChild(tag);
}

function removeTag(field, btn) {
  const tag = btn.closest('.tag');
  if (tag) tag.remove();
}

function removeLastTag(field) {
  const container = document.getElementById(`tags_${field}`);
  if (!container) return;
  const tags = container.querySelectorAll('.tag');
  if (tags.length > 0) tags[tags.length - 1].remove();
}

function setTags(field, values) {
  const container = document.getElementById(`tags_${field}`);
  if (!container) return;
  container.innerHTML = '';
  (values || []).forEach(v => addTag(field, v));
}

function getTags(field) {
  const container = document.getElementById(`tags_${field}`);
  if (!container) return [];
  const values = [];
  container.querySelectorAll('.tag').forEach(t => {
    if (t.dataset.value) values.push(t.dataset.value);
  });
  return values;
}

// ============================================================
// Custom cities tag input
// ============================================================
function renderCitiesCustomTags(cities) {
  const container = document.getElementById('cfg_CITIES_custom');
  if (!container) return;
  container.innerHTML = '';
  cities.forEach(city => {
    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.dataset.value = city;
    const rm = document.createElement('span');
    rm.className = 'tag-remove';
    rm.textContent = '\u00d7';
    rm.onclick = function() { this.closest('.tag').remove(); };
    tag.appendChild(document.createTextNode(city + ' '));
    tag.appendChild(rm);
    container.appendChild(tag);
  });
}

document.addEventListener('DOMContentLoaded', function() {
  const cityInput = document.getElementById('cfg_CITIES_input');
  if (!cityInput) return;
  cityInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      const value = this.value.trim();
      if (!value) return;
      const existing = Array.from(document.querySelectorAll('#cfg_CITIES_custom .tag')).map(t => t.dataset.value);
      if (existing.includes(value)) return;
      renderCitiesCustomTags([...existing, value]);
      this.value = '';
    }
  });
});

// ============================================================
// Mode selection
// ============================================================
function selectMode(mode) {
  // Save mode to config (MQTT_MODE bool)
  const isMqtt = mode === 'mqtt';
  selectModeUI(mode);
  apiPost('/api/config', {MQTT_MODE: isMqtt}).then(r => {
    showToast(r.message, r.success ? 'success' : 'error');
  }).catch(e => {
    showToast('保存模式失败: ' + e.message, 'error');
  });
}

function selectModeUI(mode) {
  document.querySelectorAll('.mode-card').forEach(c => c.classList.remove('active'));
  const card = document.querySelector(`.mode-card[data-mode="${mode}"]`);
  if (card) card.classList.add('active');
}

// ============================================================
// API Key visibility toggle
// ============================================================
function toggleKeyVisibility() {
  const input = document.getElementById('cfg_LLM_API_KEY');
  input.type = input.type === 'password' ? 'text' : 'password';
}

// ============================================================
// LLM test
// ============================================================
async function testLLM() {
  const resultDiv = document.getElementById('llmTestResult');
  resultDiv.style.display = 'block';
  resultDiv.innerHTML = '<span class="spinner"></span> 测试中...';

  try {
    const r = await apiPost('/api/llm-test');
    const cls = r.success ? 'text-success' : 'text-danger';
    resultDiv.innerHTML = `<span class="${cls}">${escHtml(r.message)}</span>`;
  } catch (e) {
    resultDiv.innerHTML = `<span class="text-danger">请求失败: ${escHtml(e.message)}</span>`;
  }
}

// ============================================================
// Proxy test
// ============================================================
async function testProxy() {
  const resultDiv = document.getElementById('proxyTestResult');
  resultDiv.style.display = 'block';
  resultDiv.innerHTML = '<span class="spinner"></span> 测试中...';

  try {
    const r = await apiPost('/api/proxy-test');
    const cls = r.success ? 'text-success' : 'text-danger';
    resultDiv.innerHTML = `<span class="${cls}">${escHtml(r.message)}</span>`;
    // Update proxy status indicator
    const statusEl = document.getElementById('proxyStatus');
    if (r.success) {
      statusEl.className = 'proxy-status ok';
      statusEl.innerHTML = `✅ 代理可用 (${escHtml(r.ip || '')})`;
    } else {
      statusEl.className = 'proxy-status fail';
      statusEl.innerHTML = `❌ ${escHtml(r.message)}`;
    }
  } catch (e) {
    resultDiv.innerHTML = `<span class="text-danger">请求失败: ${escHtml(e.message)}</span>`;
  }
}

// ============================================================
// Logs
// ============================================================
async function loadLogs() {
  const level = document.getElementById('logLevel').value;
  try {
    const url = level ? `/api/logs?level=${level}` : '/api/logs';
    const data = await apiGet(url);
    const logBox = document.getElementById('logBox');
    const logs = data.logs || '';
    const lines = logs.split('\n').filter(l => l.trim());
    const lineCount = document.getElementById('logLineCount');

    // Colorize by level
    const colored = logs.split('\n').map(line => {
      const upper = line.toUpperCase();
      let cls = 'log-info';
      if (upper.includes('ERROR') || upper.includes('FATAL') || upper.includes('CRITICAL')) {
        cls = 'log-error';
      } else if (upper.includes('WARN') || upper.includes('WARNING')) {
        cls = 'log-warn';
      }
      return `<span class="${cls}">${escHtml(line)}</span>`;
    }).join('\n');

    logBox.innerHTML = colored || '<span class="text-muted">（无日志）</span>';
    logBox.scrollTop = logBox.scrollHeight;
    lineCount.textContent = `${lines.length} 行`;
  } catch (e) {
    console.error('Log load failed:', e);
  }
}

async function copyLogs() {
  const text = document.getElementById('logBox').textContent;
  try {
    await navigator.clipboard.writeText(text);
    showToast('日志已复制', 'success');
  } catch (e) {
    showToast('复制失败: ' + e.message, 'error');
  }
}

async function clearLogs() {
  if (!confirm('确定清空日志文件？')) return;
  try {
    const r = await apiPost('/api/logs/clear');
    showToast(r.message, r.success ? 'success' : 'error');
    if (r.success) loadLogs();
  } catch (e) {
    showToast('清空失败: ' + e.message, 'error');
  }
}

// ============================================================
// Initialization
// ============================================================
async function init() {
  // Load config
  await loadConfig();

  // Initial status
  await refreshStatus();

  // Periodic status refresh (every 5s)
  statusInterval = setInterval(refreshStatus, 5000);
}

document.addEventListener('DOMContentLoaded', init);
</script>

</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════════════╗
║     🤖 BOSS 求职 Agent 控制面板               ║
║                                              ║
║     面板: http://0.0.0.0:9200                ║
║                                              ║
║     快捷键:                                   ║
║     📊 总览 → 状态监控 + 数据统计             ║
║     ⚙️ 求职设置 → 在线编辑所有配置项          ║
║     🤖 LLM 设置 → API + Key + 测试连接        ║
║     🔌 连接设置 → 模式切换 + 代理检测         ║
║     📋 日志 → 实时日志 + 级别过滤             ║
╚══════════════════════════════════════════════╝
""")
    app.run(host="0.0.0.0", port=9200, debug=False, use_reloader=False)
