"""
BOSS 直聘 - MQTT 聊天模块
纯 API + MQTT WebSocket，无需浏览器
"""
import json, logging, os, random, string, struct, time
from typing import Optional, Dict, List
from websocket import create_connection
import requests

import config

logger = logging.getLogger(__name__)

DATA_DIR = config.DATA_DIR
COOKIE_FILE = os.path.join(DATA_DIR, "cookie.txt")
MQTT_HOST = "ws.zhipin.com"
MQTT_PORT = 443
MQTT_PATH = "/chatws"


class BOSSChatMQTT:
    """纯 API/MQTT 聊天客户端 - 完全不需要浏览器"""

    def __init__(self):
        self.ws = None
        self._token = None
        self._wt2 = None
        self._connected = False
        self._msg_id = 1
        self._session = requests.Session()
        if config.PROXY:
            self._session.proxies = {"http": config.PROXY, "https": config.PROXY}
        self._session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'x-requested-with': 'XMLHttpRequest',
            'Referer': 'https://www.zhipin.com/',
        })
        self._load_cookie()

    def _load_cookie(self):
        """从文件加载 cookie"""
        if os.path.exists(COOKIE_FILE):
            with open(COOKIE_FILE) as f:
                for item in f.read().strip().split(';'):
                    item = item.strip()
                    if '=' in item:
                        n, v = item.split('=', 1)
                        self._session.cookies.set(n.strip(), v.strip(), domain='.zhipin.com')

    def _mqtt_encode(self, data: bytes) -> bytes:
        """构建 MQTT 包"""
        n = len(data)
        enc = b''
        while True:
            d = n % 128
            n //= 128
            if n > 0:
                d |= 0x80
            enc += bytes([d])
            if n == 0:
                break
        return enc

    def _mqtt_pkt(self, msg_type: int, remaining: bytes) -> bytes:
        return bytes([msg_type]) + self._mqtt_encode(remaining) + remaining

    def refresh_credentials(self) -> bool:
        """从 API 刷新 MQTT 凭证"""
        try:
            r = self._session.get(
                'https://www.zhipin.com/wapi/zpuser/wap/getUserInfo.json',
                timeout=10
            )
            data = r.json()
            if data.get('code') != 0:
                logger.error(f"获取用户信息失败: {data}")
                return False
            zp = data.get('zpData', {})
            self._token = zp.get('token', '')
            # 从 cookie 获取 wt2
            for cookie in self._session.cookies:
                if cookie.name == 'wt2':
                    self._wt2 = cookie.value
                    break
            if not self._token or not self._wt2:
                logger.error("缺少 MQTT 凭证")
                return False
            logger.info(f"凭证已刷新: token={self._token[:16]}...")
            return True
        except Exception as e:
            logger.error(f"刷新凭证失败: {e}")
            return False

    def connect(self) -> bool:
        """连接 MQTT WebSocket"""
        if not self.refresh_credentials():
            return False

        cid = 'ws-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))
        uname = self._token + '|0'

        try:
            self.ws = create_connection(
                f"wss://{MQTT_HOST}:{MQTT_PORT}{MQTT_PATH}",
                subprotocols=[self._wt2],
                origin="https://www.zhipin.com",
                timeout=15, suppress_origin=True,
                sslopt={"check_hostname": False}
            )
        except Exception as e:
            logger.error(f"WS 连接失败: {e}")
            return False

        # MQTT CONNECT
        pn = struct.pack('>H', 6) + b'MQIsdp'
        vh = pn + bytes([3, 0xC2]) + struct.pack('>H', 25)
        pl = struct.pack('>H', len(cid)) + cid.encode()
        pl += struct.pack('>H', len(uname)) + uname.encode()
        pl += struct.pack('>H', len(self._wt2)) + self._wt2.encode()
        self.ws.send(self._mqtt_pkt(0x10, vh + pl), opcode=2)

        time.sleep(2)
        self.ws.settimeout(5)
        try:
            r = self.ws.recv()
            if isinstance(r, bytes) and r[0] == 0x20 and r[3] == 0:
                self._connected = True
                logger.info("✅ MQTT 连接成功")
                return True
        except:
            pass
        logger.error("MQTT CONNECT 失败")
        return False

    def subscribe(self, topic: str = "chat") -> bool:
        """订阅话题"""
        if not self._connected:
            return False
        topic_bytes = topic.encode()
        sp = struct.pack('>H', 1) + struct.pack('>H', len(topic_bytes)) + topic_bytes + bytes([1])
        self.ws.send(self._mqtt_pkt(0x82, sp), opcode=2)
        time.sleep(1)
        self.ws.settimeout(3)
        try:
            r = self.ws.recv()
            if isinstance(r, bytes) and r[0] == 0x90:
                logger.info(f"✅ 已订阅 {topic}")
                return True
        except:
            pass
        return False

    def send_message(self, conversation_id: str, text: str) -> bool:
        """发送聊天消息"""
        if not self._connected:
            return False

        packet_id = self._msg_id
        self._msg_id += 1

        payload = json.dumps({
            "msgType": 1,
            "content": text,
        })

        topic = b'chat'
        pub_var = struct.pack('>H', len(topic)) + topic
        pub_var += struct.pack('>H', packet_id)
        pub_var += payload.encode()

        try:
            self.ws.send(self._mqtt_pkt(0x32, pub_var), opcode=2)
            logger.info(f"消息已发送: {text[:40]}...")
            return True
        except Exception as e:
            logger.error(f"发送失败: {e}")
            return False

    def poll_messages(self, timeout: int = 5) -> List[Dict]:
        """轮询新消息"""
        msgs = []
        self.ws.settimeout(timeout)
        while True:
            try:
                r = self.ws.recv()
                if isinstance(r, bytes):
                    if r[0] in (0x30, 0x32, 0x34, 0x36):
                        tlen = struct.unpack('>H', r[2:4])[0]
                        payload = r[4 + tlen + 2:] if r[0] & 2 else r[4 + tlen:]
                        msgs.append({"topic": r[4:4 + tlen].decode(), "payload": payload.decode()})
                    elif r[0] == 0x40:
                        pass  # PUBACK
            except:
                break
        return msgs

    def disconnect(self):
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
        self._connected = False

    def refresh_cookie_from_browser(self, cookie_str: str):
        """从浏览器刷新 cookie"""
        self._session.cookies.clear()
        for item in cookie_str.split(';'):
            item = item.strip()
            if '=' in item:
                n, v = item.split('=', 1)
                self._session.cookies.set(n.strip(), v.strip(), domain='.zhipin.com')
        with open(COOKIE_FILE, 'w') as f:
            f.write(cookie_str)
