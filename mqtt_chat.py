"""
BOSS 直聘 - MQTT 聊天模块
纯 API + MQTT WebSocket，无需浏览器

关键发现:
- MQTT v3.1 (MQIsdp)
- 连接后必须立即发送 PUBLISH，不能先 SUBSCRIBE（否则服务器推送队列消息后断开）
- BOSS MQTT 使用短连接模式：连接→发消息→收ACK→服务器推送队列消息→断开
- 消息体是 protobuf (TechwolfChatProtocol)
"""
import logging, os, random, string, struct, time
from typing import Optional, Dict, List
from websocket import create_connection, WebSocketConnectionClosedException
import requests

import config
from boss_mqtt_pb2 import TechwolfChatProtocol, TechwolfMessage

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
        self._uid = 0
        self._encrypt_uid = ''
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
        """编码 MQTT Remaining Length"""
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

    def _build_publish_packet(self, payload_bytes: bytes, packet_id: int = None) -> bytes:
        """构建 MQTT PUBLISH 包 (QoS=1, retained)"""
        if packet_id is None:
            packet_id = self._msg_id
            self._msg_id += 1
        topic = b'chat'
        pub = struct.pack('>H', len(topic)) + topic
        pub += struct.pack('>H', packet_id)
        pub += payload_bytes
        return self._mqtt_pkt(0x33, pub)  # 0x33 = QoS1 + retained

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
            self._uid = zp.get('userId', 0) or 0
            self._encrypt_uid = zp.get('encryptUserId', '') or ''
            for cookie in self._session.cookies:
                if cookie.name == 'wt2':
                    self._wt2 = cookie.value
                    break
            if not self._token or not self._wt2:
                logger.error("缺少 MQTT 凭证")
                return False
            logger.info(f"凭证已刷新: uid={self._uid}, token={self._token[:16]}...")
            return True
        except Exception as e:
            logger.error(f"刷新凭证失败: {e}")
            return False

    def _create_connect_packet(self) -> bytes:
        """构建 MQTT CONNECT 包"""
        cid = 'ws-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))
        uname = self._token + '|0'
        pn = struct.pack('>H', 6) + b'MQIsdp'
        vh = pn + bytes([3, 0xC2]) + struct.pack('>H', 25)
        pl = struct.pack('>H', len(cid)) + cid.encode()
        pl += struct.pack('>H', len(uname)) + uname.encode()
        pl += struct.pack('>H', len(self._wt2)) + self._wt2.encode()
        return self._mqtt_pkt(0x10, vh + pl)

    def _build_protobuf_message(self, to_uid: int, to_encrypt_uid: str,
                                 text: str, to_source: int = 0) -> bytes:
        """构建 protobuf 消息体"""
        temp_id = int(time.time() * 1000)
        msg = TechwolfMessage()
        msg.type = 1
        msg.mid = temp_id
        msg.cmid = temp_id

        # from - 注意: from.name 不设置（与 JS 行为一致）
        from_field = getattr(msg, 'from')
        from_field.uid = self._uid
        from_field.source = 0

        # to
        msg.to.uid = to_uid
        msg.to.name = to_encrypt_uid
        msg.to.source = to_source

        # body
        msg.body.type = 1
        msg.body.templateId = 1
        msg.body.text = text

        protocol = TechwolfChatProtocol()
        protocol.type = 1
        protocol.messages.append(msg)
        return protocol.SerializeToString()

    def _open_ws(self) -> bool:
        """建立 WebSocket 连接"""
        try:
            self.ws = create_connection(
                f"wss://{MQTT_HOST}:{MQTT_PORT}{MQTT_PATH}",
                subprotocols=[self._wt2],
                origin="https://www.zhipin.com",
                timeout=15, suppress_origin=True,
                sslopt={"check_hostname": False}
            )
            return True
        except Exception as e:
            logger.error(f"WS 连接失败: {e}")
            return False

    def connect(self) -> bool:
        """连接 MQTT（一连接就准备好发送，不订阅）"""
        if not self.refresh_credentials():
            return False

        if not self._open_ws():
            return False

        # CONNECT
        self.ws.send(self._create_connect_packet(), opcode=2)

        # 等待 CONNACK
        time.sleep(1)
        self.ws.settimeout(5)
        try:
            r = self.ws.recv()
            if isinstance(r, bytes) and r[0] == 0x20 and r[3] == 0:
                self._connected = True
                logger.info("✅ MQTT 连接成功")
                return True
            else:
                logger.error(f"CONNACK 异常: {r.hex() if isinstance(r, bytes) else r}")
        except Exception as e:
            logger.error(f"等待 CONNACK 失败: {e}")

        self.disconnect()
        return False

    def connect_and_send(self, to_uid: int, to_encrypt_uid: str, text: str,
                         to_source: int = 0) -> tuple:
        """一体化操作: 连接 + 发送 + 等待确认后断开

        返回: (success: bool, puback_id: Optional[int], queued: List)
        """
        if not self.refresh_credentials():
            return False, None, []

        if not self._open_ws():
            return False, None, []

        # 预先构建好 PUBLISH 包
        payload_bytes = self._build_protobuf_message(to_uid, to_encrypt_uid, text, to_source)
        packet_id = self._msg_id
        self._msg_id += 1
        pub_packet = self._build_publish_packet(payload_bytes, packet_id)

        # 发送 CONNECT + PUBLISH（连续发送，不等待）
        connect_pkt = self._create_connect_packet()
        self.ws.send(connect_pkt, opcode=2)
        self.ws.send(pub_packet, opcode=2)

        # 读取响应
        time.sleep(1.5)
        self.ws.settimeout(5)
        responses = {
            'connack': False,
            'puback': None,
            'queued_publishes': [],
        }
        start = time.time()
        while time.time() - start < 8:
            try:
                r = self.ws.recv()
                if isinstance(r, bytes):
                    ptype = r[0]
                    if ptype == 0x20 and r[3] == 0:
                        responses['connack'] = True
                    elif ptype == 0x40:
                        responses['puback'] = struct.unpack('>H', r[2:4])[0]
                    elif ptype & 0xF0 == 0x30:
                        # 服务器推送的排队消息
                        tlen = struct.unpack('>H', r[2:4])[0]
                        pl_start = 4 + tlen + (2 if r[0] & 0x02 else 0)
                        responses['queued_publishes'].append(r[pl_start:])
                else:
                    # TEXT frame = WebSocket CLOSE
                    break
            except:
                break

        self._connected = responses['connack']
        if self._connected and responses['puback'] is not None:
            logger.info(f"✅ 消息发送成功 (pid={responses['puback']})")
        else:
            logger.warning(f"发送结果: connack={responses['connack']}, puback={responses['puback']}")

        try:
            self.ws.close()
        except:
            pass
        self._connected = False

        return (responses['connack'] and responses['puback'] is not None,
                responses['puback'], responses['queued_publishes'])

    def send_message(self, to_uid: int, to_encrypt_uid: str, text: str,
                     to_source: int = 0) -> bool:
        """发送聊天消息（短连接模式：连一次发一条）"""
        success, puback_id, _ = self.connect_and_send(
            to_uid, to_encrypt_uid, text, to_source)
        return success

    def subscribe_and_listen(self, timeout: int = 10) -> List[bytes]:
        """连接后订阅并收取推送消息"""
        if not self.refresh_credentials():
            return []

        if not self._open_ws():
            return []

        # CONNECT
        self.ws.send(self._create_connect_packet(), opcode=2)
        time.sleep(1)
        self.ws.settimeout(5)
        try:
            r = self.ws.recv()
            if not (isinstance(r, bytes) and r[0] == 0x20 and r[3] == 0):
                logger.error("CONNACK 失败")
                self.ws.close()
                return []
        except:
            logger.error("CONNACK 超时")
            self.ws.close()
            return []

        # SUBSCRIBE
        topic = b'chat'
        sp = struct.pack('>H', 1) + struct.pack('>H', len(topic)) + topic + bytes([1])
        self.ws.send(self._mqtt_pkt(0x82, sp), opcode=2)

        # 收集所有消息
        messages = []
        self.ws.settimeout(timeout)
        start = time.time()
        while time.time() - start < timeout + 2:
            try:
                r = self.ws.recv()
                if isinstance(r, bytes):
                    if r[0] & 0xF0 == 0x30:
                        tlen = struct.unpack('>H', r[2:4])[0]
                        pl_start = 4 + tlen + (2 if r[0] & 0x02 else 0)
                        messages.append(r[pl_start:])
                    elif r[0] == 0x90:
                        pass  # SUBACK
            except:
                break

        try:
            self.ws.close()
        except:
            pass
        self._connected = False
        return messages

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
