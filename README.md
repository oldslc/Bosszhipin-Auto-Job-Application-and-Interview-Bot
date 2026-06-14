# 🤖 BOSS 直聘全自动投递面试智能体

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

> 纯 API + MQTT 直连 BOSS 直聘，无需浏览器也能自动聊天、投递简历。
> 支持双模式：**MQTT 模式**（无浏览器）和 **浏览器 CDP 模式**。

---

## ✨ 功能

| 功能 | 说明 |
|------|------|
| 🎯 **自动投递简历** | 按城市、薪资、关键词筛选职位，自动沟通 |
| 💬 **AI 自动回复** | LLM 生成回复，模拟真实求职者对话 |
| 📡 **MQTT 直连** | 通过 protobuf 直连 BOSS 聊天服务器，零检测风险 |
| 🖥️ **浏览器模式** | 通过 CDP 控制真实 Chrome，完全无法被反爬检测 |
| 📊 **控制面板** | 内置 Web UI（:9200），在线配置、启停、监控 |
| ⚙️ **在线配置** | 薪资、城市、人设、LLM、代理等全部可在线编辑 |
| 📝 **对话记录** | 自动持久化，支持查看历史 |
| 📦 **单文件打包** | PyInstaller 打包为 standalone 可执行文件 |

---

## 🚀 快速开始

### 方式一：直接运行（推荐）

```bash
# 1. 安装依赖
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. 配置
#    编辑 config.py 或启动后在控制面板中配置

# 3. 启动浏览器模式（需要 Chrome 以 --remote-debugging-port=9333 启动）
python main.py

# 或 MQTT 模式（无需浏览器，需先导出 cookie）
BOSS_MQTT_MODE=true python main.py
```

### 方式二：打包后运行

```bash
bash build.sh      # 构建单文件可执行程序
./dist/boss-agent  # 直接运行
```

### 方式三：控制脚本

```bash
./agent.sh start   # 后台启动（浏览器模式）
./agent.sh mqtt    # 后台启动（MQTT 模式）
./agent.sh stop    # 停止
./agent.sh status  # 查看状态
./agent.sh logs    # 实时日志
```

启动后访问 **http://localhost:9200** 打开控制面板。

---

## 🏗️ 架构

```
main.py                    # 主入口
├── dashboard.py           # Flask 控制面板（:9200）
├── mqtt_chat.py           # MQTT 直连客户端（protobuf）
├── mqtt_monitor.py        # MQTT 版消息监控
├── chat_handler.py        # 对话处理 + LLM 回复
├── llm_client.py          # LLM API 客户端
├── browser.py             # CDP 浏览器控制
├── monitor.py             # 浏览器版消息监控
├── job_hunter.py          # 自动投递简历
├── config.py              # 配置（可在面板中在线编辑）
├── boss_mqtt.proto        # Protobuf schema
└── boss_mqtt_pb2.py       # 编译后的 protobuf 类
```

### 通信流程

```
MQTT 模式:
  main.py → mqtt_monitor.poll_once()
              → mqtt_chat.subscribe_and_listen()  # 接收消息
              → chat_handler.generate_reply()      # LLM 生成回复
              → mqtt_chat.send_message()           # 发送回复

浏览器模式:
  main.py → browser (CDP) → BOSS 页面
              → monitor.poll_once()                # 检测未读消息
              → chat_handler.generate_reply()      # LLM 生成回复
              → browser 输入框 → 点击发送           # 浏览器操作
```

---

## ⚙️ 配置

所有配置均可通过控制面板 **http://localhost:9200** 在线编辑：

| 配置 | 说明 |
|------|------|
| 薪资底线 | 低于此薪资自动过滤 |
| 目标城市 | 多选 + 自定义输入 |
| 回复风格 | 正式 / 友好 / 专业 / 简洁 |
| 个人人设 | 求职者自我介绍（LLM 上下文） |
| LLM API | 地址、模型、Key |
| 代理 | HTTP/SOCKS5 代理 |
| CDP 端口 | Chrome 远程调试端口 |
| 工作模式 | 浏览器 / MQTT |
| 敏感词 | 自动过滤的敏感内容 |

---

## 🔧 技术细节

### MQTT 协议

BOSS 直聘使用 **MQTT v3.1** 通过 WebSocket 传输聊天消息，消息体为 **Protocol Buffers**（dcodeIO/ProtoBuf.js）。

通过反编译 BOSS 前端 `app.dbd0e2ad.js` 提取了完整的 protobuf schema：

```
TechwolfChatProtocol (type=1)
  └─ TechwolfMessage[]
     ├─ from: TechwolfUser (uid, name, source)
     ├─ to:   TechwolfUser (uid, name, source)
     ├─ type, mid, cmid
     └─ body: TechwolfMessageBody (type, templateId, text)
```

### 反检测策略

- **MQTT 模式**: 纯 WebSocket 直连，零浏览器特征
- **浏览器模式**: CDP 连接真实 Chrome，不注入自动化框架
- **代理支持**: HTTP/SOCKS5 代理绕过 IP 限制

---

## 📝 注意事项

1. **浏览器模式**：需要 Google Chrome 以 `--remote-debugging-port=9333` 启动
2. **MQTT 模式**：需要从 Chrome 导出 BOSS 直聘 cookie 到 `data/cookie.txt`
3. **首次使用**：建议先用浏览器模式登录一次 BOSS，确认 cookie 有效
4. **频率控制**：默认每小时最多 30 次回复，可在面板中调整

---

## 📄 License

MIT
