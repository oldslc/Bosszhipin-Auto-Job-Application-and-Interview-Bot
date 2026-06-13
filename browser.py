"""
浏览器控制 - 纯 CDP WebSocket 直连
完全无 Playwright 依赖，0 自动化痕迹
"""
import json, logging, os, random, time, urllib.request
from typing import Optional

import config

logger = logging.getLogger(__name__)

def _random_delay(min_s=0.3, max_s=1.0):
    time.sleep(random.uniform(min_s, max_s))


class BrowserController:
    """
    纯 CDP WebSocket 浏览器控制器
    不依赖 Playwright/Selenium，直接通过 Chrome DevTools Protocol 控制
    连接真实用户 Chrome，无法被任何反爬检测
    """

    def __init__(self):
        self._ws = None
        self._page_id = None
        self._tab_id = None
        self._msg_id = 1
        self._mode = 'cdp'
        self._is_blocked = False

    # ----------------------------------------------------------
    # CDP 连接
    # ----------------------------------------------------------
    def _get_cdp_tabs(self):
        """获取所有 CDP 标签页"""
        cdp_url = f"http://127.0.0.1:{config.CHROME_CDP_PORT}/json"
        try:
            req = urllib.request.Request(cdp_url)
            resp = urllib.request.urlopen(req, timeout=5)
            return json.loads(resp.read())
        except Exception as e:
            raise ConnectionError(f"无法连接 CDP 端口 {config.CHROME_CDP_PORT}: {e}")

    def _find_or_create_page(self):
        """找已有 BOSS 页面，没有则新建"""
        tabs = self._get_cdp_tabs()

        # 先找已有的聊天页
        for t in tabs:
            url = t.get('url', '')
            if '/web/geek/chat' in url:
                return t['webSocketDebuggerUrl'], t['id']

        # 再找其他 BOSS 页面
        for t in tabs:
            url = t.get('url', '')
            if 'zhipin.com' in url and 'socket-worker' not in url:
                return t['webSocketDebuggerUrl'], t['id']

        # 没有就新建一个聊天页
        import urllib.parse
        new_url = f"http://127.0.0.1:{config.CHROME_CDP_PORT}/json/new?" + urllib.parse.quote(
            'https://www.zhipin.com/web/geek/chat', safe=''
        )
        req = urllib.request.Request(new_url, method='PUT')
        resp = urllib.request.urlopen(req, timeout=10)
        tab = json.loads(resp.read())
        return tab['webSocketDebuggerUrl'], tab['id']

    def connect(self):
        """连接 CDP WebSocket"""
        from websocket import create_connection

        ws_url, tab_id = self._find_or_create_page()
        logger.info(f"连接 CDP: {ws_url[:60]}...")

        self._ws = create_connection(ws_url, timeout=15, suppress_origin=True)
        self._tab_id = tab_id
        self._msg_id = 1

        # 启用必要 CDP 域
        self._cmd('Page.enable')
        self._cmd('Runtime.enable')
        self._cmd('DOM.enable')

        # 绑定页面加载事件
        self._cmd('Page.enable')

        logger.info("CDP 直连模式 — 已连接")
        print(f"  CDP 模式: 纯 WebSocket 直连 (端口 {config.CHROME_CDP_PORT})")
        return True

    def _cmd(self, method, params=None):
        """发送 CDP 命令并等待响应"""
        if not self._ws:
            return None
        m = {'id': self._msg_id, 'method': method}
        if params:
            m['params'] = params
        self._msg_id += 1
        try:
            self._ws.send(json.dumps(m))
        except Exception:
            return None

        # 等待对应 id 的响应
        while True:
            try:
                self._ws.settimeout(10)
                r = json.loads(self._ws.recv())
                if r.get('id') == self._msg_id - 1:
                    return r.get('result')
            except:
                return None

    def disconnect(self):
        """断开连接（不关 Chrome）"""
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass
        self._ws = None
        logger.info("已断开 CDP 连接")

    # ----------------------------------------------------------
    # 页面操作
    # ----------------------------------------------------------
    def navigate(self, url):
        """导航到 URL"""
        self._cmd('Page.navigate', {'url': url})
        time.sleep(2)

    def navigate_to_chat(self):
        """打开聊天页面"""
        self.navigate(config.BOSS_CHAT_URL)
        time.sleep(3)
        # 检查是否被封
        self._check_blocked()

    def get_page(self):
        """返回 self（兼容原有接口）"""
        return self

    def get_user_data_dir(self):
        return "CDP 模式 (使用用户 Chrome 数据)"

    def refresh_page(self):
        self._cmd('Page.reload')
        time.sleep(3)

    # ----------------------------------------------------------
    # 页面内容提取
    # ----------------------------------------------------------
    def evaluate(self, js_code):
        """执行 JS 代码"""
        r = self._cmd('Runtime.evaluate', {
            'expression': js_code,
            'returnByValue': True,
            'awaitPromise': False
        })
        if r:
            return r.get('result', {}).get('value')
        return None

    def get_text(self, selector):
        """获取元素文本"""
        return self.evaluate(f'''
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                return el ? el.innerText?.trim() || '' : '';
            }})()
        ''')

    def find_elements(self, selector):
        """获取匹配选择器的所有元素信息"""
        r = self.evaluate(f'''
            (() => {{
                const els = document.querySelectorAll({json.dumps(selector)});
                return JSON.stringify(Array.from(els).map(e => ({{
                    text: e.innerText?.trim()?.slice(0, 200) || '',
                    visible: e.offsetParent !== null,
                    className: (e.className || '').slice(0, 60)
                }})));
            }})()
        ''')
        if r:
            try:
                return json.loads(r)
            except:
                pass
        return []

    def find_by_css(self, selector):
        """兼容原接口：返回元素是否存在"""
        r = self.evaluate(f'''
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return null;
                const rect = el.getBoundingClientRect();
                return JSON.stringify({{
                    exists: true,
                    x: rect.x, y: rect.y,
                    w: rect.width, h: rect.height,
                    text: (el.innerText || el.value || '').slice(0, 100)
                }});
            }})()
        ''')
        if r and r != 'null':
            try:
                return ElementProxy(json.loads(r), self)
            except:
                pass
        return None

    def find_all_by_css(self, selector):
        """兼容原接口"""
        return self.find_elements(selector)

    # ----------------------------------------------------------
    # 点击 & 输入
    # ----------------------------------------------------------
    def click_element(self, selector):
        """用 CDP 输入事件真实点击"""
        r = self.evaluate(f'''
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return null;
                el.scrollIntoView({{behavior: "instant", block: "center"}});
                const rect = el.getBoundingClientRect();
                return JSON.stringify({{x: rect.x + rect.width/2, y: rect.y + rect.height/2}});
            }})()
        ''')
        if not r or r == 'null':
            return False
        try:
            pos = json.loads(r)
            self._cmd('Input.dispatchMouseEvent', {
                'type': 'mousePressed', 'x': pos['x'], 'y': pos['y'],
                'button': 'left', 'clickCount': 1
            })
            self._cmd('Input.dispatchMouseEvent', {
                'type': 'mouseReleased', 'x': pos['x'], 'y': pos['y'],
                'button': 'left', 'clickCount': 1
            })
            return True
        except:
            return False

    def type_text(self, selector, text):
        """在输入框填入文本（兼容原接口）"""
        self.evaluate(f'''
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return;
                el.focus();
                el.textContent = '';
                const p = document.createElement('p');
                p.textContent = {json.dumps(text)};
                el.appendChild(p);
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
            }})()
        ''')

    def simulate_mouse_move(self):
        """模拟鼠标移动"""
        self._cmd('Input.dispatchMouseEvent', {
            'type': 'mouseMoved',
            'x': random.randint(100, 800),
            'y': random.randint(100, 600)
        })

    # ----------------------------------------------------------
    # 封禁检测
    # ----------------------------------------------------------
    def _check_blocked(self):
        url = self.evaluate('window.location.href') or ''
        self._is_blocked = any(p in url for p in config.BLOCKED_URL_PATTERNS)
        if self._is_blocked:
            logger.error(f"被重定向到安全页: {url}")
        return self._is_blocked

    @property
    def is_blocked(self):
        return self._is_blocked

    def navigate_and_handle_verification(self):
        """兼容原接口：导航到聊天页并处理验证"""
        self.navigate_to_chat()
        if self._is_blocked:
            print("\n安全验证页面已打开，请手动完成验证...")
            for i in range(60):
                time.sleep(1)
                self._check_blocked()
                if not self._is_blocked:
                    print("验证完成，继续运行")
                    return True
                if i % 10 == 9:
                    print(f"   等待验证中... ({i+1}s)")
            print("验证超时，请重新启动程序")
            return False
        return True


class ElementProxy:
    """模拟 Playwright 元素对象"""
    def __init__(self, info, browser):
        self._info = info
        self._browser = browser

    @property
    def text(self):
        return self._info.get('text', '')

    def click(self):
        x = self._info['x'] + self._info['w'] / 2
        y = self._info['y'] + self._info['h'] / 2
        self._browser._cmd('Input.dispatchMouseEvent', {
            'type': 'mousePressed', 'x': x, 'y': y,
            'button': 'left', 'clickCount': 1
        })
        self._browser._cmd('Input.dispatchMouseEvent', {
            'type': 'mouseReleased', 'x': x, 'y': y,
            'button': 'left', 'clickCount': 1
        })
