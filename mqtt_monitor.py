"""
MQTT 版聊天监控器 - 求职者端
通过 MQTT 直连 BOSS 聊天服务器，接收消息并自动回复。
替代原来的浏览器版 monitor.py。

短连接模式：每次 poll 就新连一次 MQTT，收到队列消息后连接自动断开。
"""

import hashlib
import logging
from typing import Optional, Dict, List

import config
from chat_handler import ChatHandler
from mqtt_chat import BOSSChatMQTT
from boss_mqtt_pb2 import TechwolfChatProtocol, TechwolfMessage

logger = logging.getLogger(__name__)


class MqttChatMonitor:
    """
    通过 MQTT 直连 BOSS 聊天服务器，接收消息并自动回复。
    短连接模式：每次 poll 就新连一次，收到队列消息后连接自动断开。
    """

    def __init__(self, chat_handler: ChatHandler):
        self.chat_handler = chat_handler
        # 记录已处理的 message mid，避免重复回复
        self._processed: set = set()
        # MQTT 客户端实例（每次 poll 内部新建短连接，复用此实例的 cookie/凭证）
        self._mqtt = BOSSChatMQTT()

    # ----------------------------------------------------------
    # 对话 ID 生成（基于 uid）
    # ----------------------------------------------------------
    @staticmethod
    def _make_conv_id(uid: int) -> str:
        """
        基于 uid 生成对话 ID

        在 MQTT 模式下，使用对方的 uid 作为唯一标识，
        比浏览器版基于 hr_name+company 更可靠（uid 不会重复）。
        """
        raw = str(uid)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]

    # ----------------------------------------------------------
    # 好友信息获取（扩展点）
    # ----------------------------------------------------------
    def _get_friend_info(self, uid: int) -> dict:
        """
        从 BOSS 直聘 API 获取对方信息（公司、职位等），补充 conversation 上下文。

        目前 BOSS 没有公开的根据 uid 获取对方详细信息的 API，
        这是一个扩展点，后续可以通过以下方式补充：
        1. 解析聊天历史中对方发送的卡片消息（往往包含公司/职位）
        2. 从 BOSS 前端 API 中找到 friend detail 接口
        3. 从 monitor.py 的浏览器模式中抓取页面信息

        返回: {'company': str, 'position': str, 'salary': str}
              空字典表示未能获取到额外信息
        """
        # 扩展点：后续可在此处调用 BOSS API 补充信息
        return {}

    # ----------------------------------------------------------
    # protobuf 消息解码
    # ----------------------------------------------------------
    def _decode_incoming(self, payload: bytes) -> Optional[dict]:
        """
        解码 protobuf 消息。

        从 MQTT PUBLISH 的 payload 中解析 TechwolfChatProtocol，
        提取消息内容、发送者信息、消息 mid 等关键字段。

        TechwolfChatProtocol 结构：
            type = 1 (聊天消息)
            messages[] = TechwolfMessage {
                type = 3 (incoming 类型，服务器推送的消息)
                mid  = int64 (唯一消息 ID，用于去重)
                cmid = int64
                from { uid, name(encryptUid), source }
                to   { uid, name, source }
                body { type, templateId, text }
            }

        返回:
            {
                'from_uid': int,          # 发送者用户 ID
                'from_name': str,         # 发送者名称（实际是 encryptUid 或昵称）
                'from_encrypt': str,      # 发送者 encryptUid（from.name）
                'text': str,              # 消息正文
                'mid': int,               # 消息 ID（用于去重）
                'cmid': int,              # 客户端消息 ID
                'type': int,              # 消息类型
            }
            或 None（解码失败或消息类型不正确）
        """
        try:
            protocol = TechwolfChatProtocol()
            protocol.ParseFromString(payload)

            if protocol.type != 1:
                logger.debug(f"忽略非聊天协议消息: type={protocol.type}")
                return None

            for msg in protocol.messages:
                # type=3 表示服务器推送的 incoming 消息
                # type=1 是自己发送的消息（不需要处理）
                if msg.type != 3:
                    continue

                from_field = getattr(msg, 'from')
                result = {
                    'from_uid': from_field.uid,
                    'from_name': from_field.name or str(from_field.uid),
                    'from_encrypt': from_field.name or '',
                    'text': msg.body.text,
                    'mid': msg.mid,
                    'cmid': msg.cmid,
                    'type': msg.type,
                }
                return result

            return None

        except Exception as e:
            logger.error(f"解码 protobuf 消息失败: {e}")
            return None

    # ----------------------------------------------------------
    # 消息去重
    # ----------------------------------------------------------
    def _record_message(self, mid: int) -> bool:
        """
        检查/记录已处理消息，防止重复回复。

        按消息的 mid（服务端唯一 ID）去重。
        返回 True 表示新消息（未处理过），False 表示已处理过。
        """
        if mid in self._processed:
            return False
        self._processed.add(mid)
        # 限制集合大小，防止内存泄漏
        if len(self._processed) > 10000:
            # 只保留最近 5000 条
            self._processed = set(list(self._processed)[-5000:])
        return True

    # ----------------------------------------------------------
    # 主轮询：一次 poll
    # ----------------------------------------------------------
    def poll_once(self) -> int:
        """
        执行一次 MQTT 轮询。

        流程：
        1. 调用 BOSSChatMQTT.subscribe_and_listen() 连接 MQTT 并接收推送消息
        2. 解码每条 protobuf 消息
        3. 按消息 mid 去重
        4. 对每条新消息：
           a. 生成对话 ID (from uid)
           b. 获取或创建 Conversation
           c. 安全检查 + 频率控制
           d. 调用 chat_handler.generate_reply() 生成回复
           e. 通过 chat_handler.send_reply(mqtt_client=...) 发送回复
        5. 返回本次处理的消息数量

        返回:
            int: 本次 poll 处理的消息数量
        """
        try:
            # 订阅并接收推送的队列消息（短连接：连一次收完就断）
            raw_messages = self._mqtt.subscribe_and_listen(timeout=10)
            if not raw_messages:
                return 0

            processed_count = 0

            for raw in raw_messages:
                try:
                    processed_count += self._process_one_message(raw)
                except Exception as e:
                    logger.error(f"处理单条 MQTT 消息时出错: {e}")
                    continue

            return processed_count

        except Exception as e:
            logger.error(f"MQTT 轮询出错: {e}")
            return 0

    # ----------------------------------------------------------
    # 处理单条 MQTT 消息
    # ----------------------------------------------------------
    def _process_one_message(self, raw_payload: bytes) -> int:
        """
        处理一条原始 MQTT 消息的完整流程。

        返回 1 表示处理了该消息，0 表示跳过。
        """
        # 1. 解码 protobuf
        decoded = self._decode_incoming(raw_payload)
        if not decoded:
            return 0

        # 2. 按 mid 去重
        if not self._record_message(decoded['mid']):
            logger.debug(f"消息已处理过，跳过: mid={decoded['mid']}")
            return 0

        from_uid = decoded['from_uid']
        from_encrypt = decoded['from_encrypt']
        from_name = decoded['from_name']
        text = decoded['text']
        mid = decoded['mid']

        logger.info(f"📨 [MQTT] 新消息 uid={from_uid} name={from_name} mid={mid}: {text[:80]}")

        # 3. 生成对话 ID
        conv_id = self._make_conv_id(from_uid)

        # 4. 尝试补充好友信息（公司、职位等）
        friend_info = self._get_friend_info(from_uid)
        company = friend_info.get('company', '未知公司')
        position = friend_info.get('position', '未知职位')
        salary = friend_info.get('salary', '面议')

        # 5. 获取或创建对话
        conv = self.chat_handler.get_or_create_conversation(
            conv_id, from_name, company, position, salary
        )

        # 6. 安全检查（检查收到的消息是否含敏感内容）
        is_safe, reason = self.chat_handler._check_safety(text)
        if not is_safe:
            logger.warning(f"安全警告 [{conv_id}]: {reason}")
            conv.add_message("hr", text)
            conv.status = 'manual'
            self.chat_handler._save_conversation(conv)
            print(f"\n⚠️  人工介入提醒: {from_name} (uid={from_uid})")
            print(f"   原因: {reason}")
            print(f"   消息: {text}\n")
            return 1

        # 7. 记录 HR 消息
        conv.add_message("hr", text)

        # 8. 频率控制
        if not self.chat_handler._can_reply():
            conv.status = 'waiting'
            self.chat_handler._save_conversation(conv)
            logger.info(f"频率限制跳过 [{conv_id}]")
            return 1

        # 9. 调用 LLM 生成回复
        logger.info(f"正在为 [{from_name} (uid={from_uid})] 生成回复...")
        reply = self.chat_handler.generate_reply(conv, text)

        if not reply:
            logger.error("LLM 生成回复失败")
            conv.status = 'manual'
            self.chat_handler._save_conversation(conv)
            return 1

        # 10. 检查是否需要人工介入
        if "需要确认" in reply:
            logger.info(f"LLM 建议人工介入 [{conv_id}]")
            conv.status = 'manual'
            conv.add_message("me", "[待人工确认]")
            self.chat_handler._save_conversation(conv)
            print(f"\n🔔 人工介入提醒: {from_name} (uid={from_uid})")
            print(f"   HR消息: {text}")
            print(f"   LLM建议: {reply}\n")
            return 1

        # 11. 检查生成的回复是否安全
        is_safe, reason = self.chat_handler._check_safety(reply)
        if not is_safe:
            logger.warning(f"生成的回复包含敏感内容: {reason}")
            conv.status = 'manual'
            self.chat_handler._save_conversation(conv)
            return 1

        # 12. 通过 MQTT 发送回复
        logger.info(f"正在通过 MQTT 发送回复 [{from_name}]...")
        success = self.chat_handler.send_reply(
            reply,
            to_uid=from_uid,
            to_encrypt_uid=from_encrypt,
            mqtt_client=self._mqtt,
        )

        if success:
            conv.add_message("me", reply)
            conv.status = 'active'
            logger.info(f"✅ [MQTT] 回复成功 [{from_name}]: {reply[:60]}...")
        else:
            logger.error(f"[MQTT] 发送回复失败 [{conv_id}]")
            conv.status = 'waiting'
            conv.add_message("me", f"[发送失败] {reply}")

        # 13. 保存对话
        self.chat_handler._save_conversation(conv)
        logger.info(f"  对话已保存 [{conv_id}]")
        return 1
