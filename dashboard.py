"""
BOSS 求职 Agent 监控面板 - Flask Web 版
"""
import json, os, glob
from datetime import datetime
from flask import Flask, render_template_string, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONV_DIR = os.path.join(DATA_DIR, "conversations")
LOG_DIR = os.path.join(DATA_DIR, "logs")
PID_FILE = os.path.join(BASE_DIR, "agent.pid")

app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BOSS 求职 Agent 面板</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: -apple-system, 'Segoe UI', sans-serif; background:#0d1117; color:#c9d1d9; padding:20px; }
.container { max-width:1200px; margin:0 auto; }
h1 { font-size:24px; margin-bottom:20px; color:#58a6ff; }
.card { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; margin-bottom:16px; }
.row { display:flex; gap:16px; flex-wrap:wrap; }
.stat { flex:1; min-width:140px; text-align:center; padding:16px; background:#0d1117; border-radius:6px; border:1px solid #30363d; }
.stat-num { font-size:32px; font-weight:bold; color:#58a6ff; }
.stat-label { font-size:12px; color:#8b949e; margin-top:4px; }
.badge { display:inline-block; padding:2px 8px; border-radius:12px; font-size:11px; font-weight:bold; }
.badge-running { background:#238636; color:#fff; }
.badge-stopped { background:#da3633; color:#fff; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:left; padding:8px 4px; border-bottom:2px solid #30363d; color:#8b949e; font-size:11px; text-transform:uppercase; }
td { padding:8px 4px; border-bottom:1px solid #21262d; }
.msg { max-width:400px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.time { color:#8b949e; font-size:11px; }
.log-line { font-family: 'Cascadia Code', 'Fira Code', monospace; font-size:11px; color:#8b949e; line-height:1.6; white-space:pre-wrap; word-break:break-all; }
.agent-status { display:flex; align-items:center; gap:8px; }
.agent-dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
.dot-running { background:#3fb950; box-shadow: 0 0 8px #3fb950; }
.dot-stopped { background:#da3633; }
pre { margin:0; }
</style>
</head>
<body>
<div class="container">
<h1>🤖 BOSS 求职 Agent 状态</h1>

<div class="row">
  <div class="stat">
    <div class="stat-num">{{ stats.running_status }}</div>
    <div class="stat-label">Agent 状态</div>
  </div>
  <div class="stat">
    <div class="stat-num">{{ stats.conversations }}</div>
    <div class="stat-label">对话数</div>
  </div>
  <div class="stat">
    <div class="stat-num">{{ stats.replies }}</div>
    <div class="stat-label">已回复</div>
  </div>
  <div class="stat">
    <div class="stat-num">{{ stats.pending }}</div>
    <div class="stat-label">待处理</div>
  </div>
  <div class="stat">
    <div class="stat-num">{{ stats.delivered }}</div>
    <div class="stat-label">已投递</div>
  </div>
  <div class="stat">
    <div class="stat-num">{{ stats.last_active }}</div>
    <div class="stat-label">最后活跃</div>
  </div>
</div>

{% if deliveries %}
<div class="card">
  <h2 style="font-size:16px; margin-bottom:12px; color:#58a6ff;">📬 投递记录</h2>
  <table>
  <tr><th>时间</th><th>城市</th><th>职位</th><th>薪资</th><th>公司</th></tr>
  {% for d in deliveries %}
  <tr>
    <td class="time">{{ d.time }}</td>
    <td>{{ d.city }}</td>
    <td>{{ d.title[:25] }}</td>
    <td>{{ d.salary[:12] }}</td>
    <td class="msg">{{ d.company[:20] }}</td>
  </tr>
  {% endfor %}
  </table>
</div>
{% endif %}

<div class="card">
  <h2 style="font-size:16px; margin-bottom:12px; color:#58a6ff;">💬 对话列表</h2>
  {% if conversations %}
  <table>
  <tr><th>HR</th><th>公司</th><th>职位</th><th>状态</th><th>最后消息</th><th>时间</th></tr>
  {% for c in conversations %}
  <tr>
    <td>{{ c.hr_name }}</td>
    <td>{{ c.company }}</td>
    <td>{{ c.position }}</td>
    <td><span class="badge {% if c.status == 'active' %}badge-running{% else %}badge-stopped{% endif %}">{{ c.status }}</span></td>
    <td class="msg">{{ c.last_msg[:60] }}</td>
    <td class="time">{{ c.last_time }}</td>
  </tr>
  {% endfor %}
  </table>
  {% else %}
  <p style="color:#8b949e;">暂无对话记录</p>
  {% endif %}
</div>

<div class="card">
  <h2 style="font-size:16px; margin-bottom:12px; color:#58a6ff;">📋 实时日志</h2>
  <div class="log-line">{{ logs }}</div>
</div>

<div class="card" style="text-align:center; color:#8b949e; font-size:12px;">
  每 5 秒自动刷新 · {{ now }}
</div>
</div>

<script>
setTimeout(function(){ location.reload(); }, 5000);
</script>
</body>
</html>"""


def get_agent_status():
    """检查 agent 是否在运行"""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            # 检查进程是否存在
            if os.name == 'posix':
                import signal
                try:
                    os.kill(pid, 0)
                    return "运行中", "running"
                except:
                    return "已停止", "stopped"
            else:
                return "未知", "stopped"
        except:
            pass
    return "已停止", "stopped"


def get_conversations():
    """读取对话记录"""
    convs = []
    if os.path.exists(CONV_DIR):
        for fname in sorted(os.listdir(CONV_DIR), reverse=True)[:20]:
            if fname.endswith('.json'):
                try:
                    with open(os.path.join(CONV_DIR, fname), encoding='utf-8') as f:
                        data = json.load(f)
                    messages = data.get('messages', [])
                    last_msg = messages[-1] if messages else {}
                    convs.append({
                        'hr_name': data.get('hr_name', '?'),
                        'company': data.get('company', '?'),
                        'position': data.get('position', '?'),
                        'status': data.get('status', 'unknown'),
                        'last_msg': last_msg.get('content', '') if isinstance(last_msg, dict) else str(last_msg),
                        'last_time': last_msg.get('timestamp', '') if isinstance(last_msg, dict) else '',
                    })
                except:
                    pass
    return convs


def get_logs():
    """读取最近日志"""
    if not os.path.exists(LOG_DIR):
        return "（无日志）"
    log_files = sorted(glob.glob(os.path.join(LOG_DIR, "*.log")), reverse=True)
    if not log_files:
        return "（无日志）"
    try:
        with open(log_files[0], encoding='utf-8') as f:
            lines = f.readlines()
        # 返回最后 20 行
        return "".join(lines[-20:])
    except:
        return "（读取日志失败）"


def get_stats():
    """统计数据"""
    convs = get_conversations()
    deliveries = get_deliveries()
    status_text, status_class = get_agent_status()
    
    total = len(convs)
    replies = sum(1 for c in convs if c['last_msg'])
    pending = sum(1 for c in convs if c['status'] in ['manual', 'waiting_reply'])
    
    last_active = convs[0]['last_time'][:16] if convs else '-'
    
    return {
        'running_status': status_text,
        'status_class': status_class,
        'conversations': total,
        'replies': replies,
        'pending': pending,
        'delivered': len(deliveries),
        'last_active': last_active,
    }


def get_deliveries():
    """读取投递记录"""
    path = os.path.join(DATA_DIR, "delivered.json")
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return []


@app.route('/')
def index():
    stats = get_stats()
    convs = get_conversations()
    logs = get_logs()
    deliveries = get_deliveries()[-50:]  # 最近50条
    return render_template_string(
        HTML,
        stats=stats,
        conversations=convs,
        deliveries=deliveries,
        logs=logs or "（无日志）",
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )


@app.route('/api/status')
def api_status():
    return jsonify({
        'stats': get_stats(),
        'conversations': get_conversations(),
        'deliveries': get_deliveries()[-50:],
        'logs': get_logs()[-500:],
    })


if __name__ == '__main__':
    print(f"BOSS Agent 监控面板: http://127.0.0.1:9200")
    app.run(host='127.0.0.1', port=9200, debug=False)
