"""
配置文件 - 求职者端Agent
"""

import os

# ============================================================
# 用户求职配置
# ============================================================
SALARY_MIN = 8000
CITIES = ["南通", "南京", "上海"]
REPLY_STYLE = "正式"

PERSONA = """
我是丁雨阳，23年毕业于江苏联合职业技术学院机电一体化专业。
曾在洋口港港务有限公司负责船舶调度，后自主经营虾类养殖2年。
最近半年自学AI技术，掌握Python、Agent开发、AIGC应用。
求职意向：AI应用开发/Agent开发工程师
期望薪资：6K以上
工作城市：南通、上海
"""

# ============================================================
# LLM API 配置
# ============================================================
LLM_API_URL = "https://opencode.ai/zen/go/v1/chat/completions"
LLM_MODEL = "deepseek-v4-flash"
LLM_API_KEY = open(os.path.join(os.path.dirname(__file__), ".api_key")).read().strip() if os.path.exists(os.path.join(os.path.dirname(__file__), ".api_key")) else ""
LLM_TIMEOUT = 30
LLM_MAX_RETRIES = 3

# ============================================================
# 安全 & 频率控制
# ============================================================
REPLY_DELAY_MIN = 3
REPLY_DELAY_MAX = 8
MAX_REPLIES_PER_HOUR = 30
POLL_INTERVAL = 8  # 增大轮询间隔，减少被检测风险

SENSITIVE_KEYWORDS = [
    "转账", "汇款", "押金", "培训费", "体检费",
    "身份证号", "银行卡", "密码", "验证码",
    "加微信", "加QQ", "下载APP", "扫码",
]

# ============================================================
# CSS选择器 - 求职者端 (geek)
# ============================================================

# 左侧对话列表项
SELECTOR_CHAT_ITEM = '.chat-conversation, [class*="conversation"], [class*="session-item"]'

# 未读消息红点/标记
SELECTOR_UNREAD_BADGE = '[class*="unread"], [class*="badge"], [class*="dot"], .unread-num, [class*="red-dot"], [class*="msg-num"]'

# 右侧消息列表容器
SELECTOR_MESSAGE_LIST = '[class*="message-list"], [class*="chat-content"], .chat-message-list, [class*="msg-list"], .main-wrap'

# 单条消息气泡
SELECTOR_MESSAGE_BUBBLE = '[class*="message-content"], [class*="bubble"], .word-wrap, [class*="msg-content"], [class*="last-msg"]'

# 输入框（contenteditable 或 textarea）
SELECTOR_INPUT_BOX = '[contenteditable="true"], textarea[class*="input"], [class*="chat-input"], [class*="input-area"] [contenteditable], .chat-input [contenteditable], [role="textbox"]'

# 发送按钮
SELECTOR_SEND_BTN = '[class*="send-btn"], button[class*="send"], .btn-send, [class*="btn-send"]'

# HR 名称
SELECTOR_HR_NAME = '[class*="name"], [class*="nickname"], .info-name, [class*="user-name"], [class*="hr-name"]'

# 公司名称
SELECTOR_COMPANY = '[class*="company"], [class*="corp-name"], .company-name, [class*="corp"]'

# 职位名称
SELECTOR_POSITION = '[class*="position"], [class*="job-name"], .position-name, [class*="job-title"]'

# ============================================================
# 浏览器配置
# ============================================================
BOSS_CHAT_URL = "https://www.zhipin.com/web/geek/chat"

# 推荐：CDP 连接真实 Chrome（完全无法被反爬检测）
# 先手动启动 Chrome: "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
# 然后设置 CHROME_CDP_PORT=9222
# 留空则使用 Playwright 原生模式（兜底）
CHROME_CDP_PORT = 9333

# Chrome 用户数据目录（Playwright 模式用，保持登录状态）
# CDP 模式会自动使用当前 Chrome 的用户数据
CHROME_USER_DATA_DIR = ""

# ============================================================
# Selenium 配置
# ============================================================
SELENIUM_TIMEOUT = 10          # 元素查找超时（秒）
CHROME_VERSION = 149          # Chrome 主版本号，None 则自动检测

# ============================================================
# 反检测配置
# ============================================================
STEALTH_RANDOM_DELAY_MIN = 0.5   # 操作间最小随机延迟（秒）
STEALTH_RANDOM_DELAY_MAX = 2.0   # 操作间最大随机延迟（秒）

# ============================================================
# 数据存储
# ============================================================
DATA_DIR = "data"
CONVERSATIONS_DIR = "data/conversations"
LOGS_DIR = "data/logs"

# ============================================================
# 代理配置（IP被封时使用代理绕过）
# ============================================================
# 支持格式: http://user:pass@ip:port, socks5://127.0.0.1:1080
# 留空则不使用代理
PROXY = "http://127.0.0.1:7890"

# ============================================================
# 封禁/安全验证页面URL特征
# 当当前URL包含以下任一字符串时，认为已被重定向到安全验证页
# ============================================================
BLOCKED_URL_PATTERNS = [
    "zhipin.com/block",
    "zhipin.com/verify",
    "zhipin.com/captcha",
    "zhipin.com/safety",
    "zhipin.com/security",
    "zhipin.com/auth",
    "zhipin.com/login",
    "zhipin.com/web/passport",
    "/captcha",
    "/verify?",
    "safety-center",
    "risk-control",
    "403.html",
    "code=31",
    "code=30",
    "antispider",
    "blocked",
]
