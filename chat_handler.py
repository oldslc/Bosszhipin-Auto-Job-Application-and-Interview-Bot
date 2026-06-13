"""
对话处理模块 - 求职者端
负责：
  - 维护每个对话的上下文（对话历史）
  - 构造 LLM prompt（求职者人设 + 岗位信息 + 历史 + 当前问题）
  - 调用 LLM 生成回复
  - 安全检查（敏感词、风险检测）
  - 通过 browser 发送回复
  - 持久化对话记录到 data/conversations/
"""

import json
import logging
import os
import random
import time
from datetime import datetime
from typing import Optional, Dict


import config
from models import Conversation, Message, ConversationStatus
from llm_client import LLMClient
from browser import BrowserController

logger = logging.getLogger(__name__)


class ChatHandler:
    """
    对话处理器 - 求职者端
    管理所有活跃对话，负责生成回复和发送消息
    """

    def __init__(self, browser: BrowserController, llm_client: LLMClient):
        self.browser = browser
        self.llm = llm_client
        # 活跃对话字典: conversation_id -> Conversation
        self.conversations: Dict[str, Conversation] = {}
        # 频率控制：最近一小时的回复次数
        self.reply_timestamps: list = []
        # 确保数据目录存在
        os.makedirs(config.CONVERSATIONS_DIR, exist_ok=True)
        # 加载已有对话
        self._load_conversations()

    # ----------------------------------------------------------
    # 对话管理
    # ----------------------------------------------------------
    def get_or_create_conversation(self, conv_id: str,
                                   hr_name: str, company: str,
                                   position: str, salary: str = "面议") -> Conversation:
        """获取已有对话或创建新对话"""
        if conv_id not in self.conversations:
            conv = Conversation(
                conversation_id=conv_id,
                hr_name=hr_name,
                company=company,
                position=position,
                salary=salary
            )
            self.conversations[conv_id] = conv
            logger.info(f"新对话: {hr_name} @ {company} - {position}")
        return self.conversations[conv_id]

    # ----------------------------------------------------------
    # 频率控制
    # ----------------------------------------------------------
    def _can_reply(self) -> bool:
        """
        检查是否可以回复（频率控制）
        每小时最多 MAX_REPLIES_PER_HOUR 次
        """
        now = datetime.now().timestamp()
        # 清除一小时前的记录
        self.reply_timestamps = [t for t in self.reply_timestamps if now - t < 3600]
        if len(self.reply_timestamps) >= config.MAX_REPLIES_PER_HOUR:
            logger.warning(f"达到每小时回复上限 ({config.MAX_REPLIES_PER_HOUR})，跳过")
            return False
        return True

    def _record_reply(self):
        """记录一次回复的时间戳"""
        self.reply_timestamps.append(datetime.now().timestamp())

    # ----------------------------------------------------------
    # 安全检查
    # ----------------------------------------------------------
    def _check_safety(self, text: str) -> tuple:
        """
        检查消息是否包含敏感内容
        返回: (is_safe: bool, reason: str)
        """
        for keyword in config.SENSITIVE_KEYWORDS:
            if keyword in text:
                return False, f"包含敏感词: {keyword}"
        return True, ""

    # ----------------------------------------------------------
    # LLM 回复生成
    # ----------------------------------------------------------
    def _build_prompt(self, conversation: Conversation, new_message: str) -> str:
        """
        构造 LLM 用户提示词
        求职者端：包含人设、岗位信息、对话历史、当前问题
        """
        history_text = conversation.get_history(max_turns=15)
        if not history_text:
            history_text = "（暂无对话历史）"

        prompt = f"""你是一个求职者，正在Boss直聘求职端上与HR对话。

【你的身份】
{config.PERSONA.strip()}

【当前岗位】
公司：{conversation.company}
职位：{conversation.position}
薪资：{conversation.salary}

【对话历史】
{history_text}

【HR最新消息】
{new_message}

请生成一条合适的求职者回复。要求：
1. 回复风格：{config.REPLY_STYLE}，像真实求职者一样自然
2. 简洁专业，不要太长（一般1-3句话）
3. 展示你对岗位的兴趣和匹配度
4. 薪资底线是{config.SALARY_MIN}元/月，低于此可以礼貌拒绝
5. 如果HR问到技术问题，结合你的AI自学经历回答
6. 如果不确定如何回复，回复"需要确认"（系统会通知人工介入）
7. 不要暴露自己是AI，要像真人求职者一样回复
8. 注意识别诈骗信息（要求缴费、提供敏感信息等）"""
        return prompt

    def generate_reply(self, conversation: Conversation,
                              new_message: str) -> Optional[str]:
        """
        调用 LLM 生成回复
        返回回复文本，失败返回 None
        """
        user_prompt = self._build_prompt(conversation, new_message)
        system_prompt = (
            "你是一个正在找工作的求职者，正在Boss直聘求职端上与HR进行在线对话。"
            "你叫丁雨阳，有机电一体化背景和AI自学经历，"
            "正在寻找AI应用开发/Agent开发相关岗位。"
            "请用自然、专业但不失亲切的语气回复，"
            "展现求职者的积极性和学习能力。"
        )

        # 直接调用 LLM（同步调用）
        reply = self.llm.ask(system_prompt, user_prompt)
        return reply

    # ----------------------------------------------------------
    # 发送回复到页面
    # ----------------------------------------------------------
    def send_reply(self, text: str) -> bool:
        """
        通过浏览器在 Boss 直聘求职者页面发送消息
        返回是否成功
        """
        try:
            page = self.browser.get_page()
            if not page:
                logger.error("浏览器未连接")
                return False

            # 随机延迟，模拟真人
            delay = random.uniform(config.REPLY_DELAY_MIN, config.REPLY_DELAY_MAX)
            logger.debug(f"延迟 {delay:.1f} 秒后发送...")
            time.sleep(delay)

            # 模拟鼠标移动
            self.browser.simulate_mouse_move()

            # 获取输入框选择器
            input_selector = config.SELECTOR_INPUT_BOX.split(",")[0].strip()
            input_box = self.browser.find_by_css(input_selector)
            if input_box:
                # 聚焦输入框
                input_box.click()
                time.sleep(random.uniform(0.3, 0.8))

                # 使用 JavaScript 设置文本（兼容 contenteditable）
                # 使用 JavaScript 设置文本（兼容 contenteditable）
                page.evaluate(
                    f"""
                    (() => {{
                        const el = document.querySelector({json.dumps(input_selector)});
                        if (el) {{
                            el.focus();
                            el.textContent = '';
                            const p = document.createElement('p');
                            p.textContent = {json.dumps(text)};
                            el.appendChild(p);
                            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                    }})()
                    """
                )
                time.sleep(random.uniform(0.5, 1.0))

                # 点击发送按钮
                sent = self.browser.click_element(config.SELECTOR_SEND_BTN.split(",")[0].strip())
                if sent:
                    logger.info(f"已发送回复: {text[:50]}...")
                    self._record_reply()
                    return True

                # 如果发送按钮点击失败，尝试按 Enter
                time.sleep(0.3)
                page.keyboard.press("Enter")
                logger.info(f"已通过 Enter 发送回复: {text[:50]}...")
                self._record_reply()
                return True
            else:
                logger.error("未找到消息输入框")
                return False

        except Exception as e:
            logger.error(f"发送回复失败: {e}")
            return False

    # ----------------------------------------------------------
    # 核心流程：处理新消息
    # ----------------------------------------------------------
    def handle_new_message(self, conv_id: str,
                                  hr_name: str, company: str,
                                  position: str, salary: str,
                                  new_message: str) -> bool:
        """
        处理一条新消息的完整流程：
        1. 获取/创建对话
        2. 安全检查
        3. 记录消息
        4. 频率控制
        5. 生成回复
        6. 发送回复
        7. 保存对话记录
        """
        # 获取对话
        conv = self.get_or_create_conversation(conv_id, hr_name, company, position, salary)

        # 安全检查（检查 HR 发来的消息）
        is_safe, reason = self._check_safety(new_message)
        if not is_safe:
            logger.warning(f"安全警告 [{conv_id}]: {reason}")
            conv.add_message("hr", new_message)
            conv.status = ConversationStatus.MANUAL
            self._save_conversation(conv)
            # 通知人工介入
            print(f"\n⚠️  人工介入提醒: {hr_name} @ {company}")
            print(f"   原因: {reason}")
            print(f"   消息: {new_message}\n")
            return False

        # 记录 HR 消息
        conv.add_message("hr", new_message)

        # 频率控制
        if not self._can_reply():
            conv.status = ConversationStatus.WAITING_REPLY
            self._save_conversation(conv)
            return False

        # 生成回复
        logger.info(f"正在为 [{hr_name} @ {company}] 生成回复...")
        reply = self.generate_reply(conv, new_message)

        if not reply:
            logger.error("LLM 生成回复失败")
            conv.status = ConversationStatus.MANUAL
            self._save_conversation(conv)
            return False

        # 检查是否需要人工介入（LLM 回复了"需要确认"）
        if "需要确认" in reply:
            logger.info(f"LLM 建议人工介入 [{conv_id}]")
            conv.status = ConversationStatus.MANUAL
            conv.add_message("me", "[待人工确认]")
            self._save_conversation(conv)
            print(f"\n🔔 人工介入提醒: {hr_name} @ {company}")
            print(f"   HR消息: {new_message}")
            print(f"   LLM建议: {reply}\n")
            return False

        # 检查生成的回复是否安全
        is_safe, reason = self._check_safety(reply)
        if not is_safe:
            logger.warning(f"生成的回复包含敏感内容: {reason}")
            conv.status = ConversationStatus.MANUAL
            self._save_conversation(conv)
            return False

        # 发送回复
        success = self.send_reply(reply)
        if success:
            conv.add_message("me", reply)
            conv.status = ConversationStatus.ACTIVE
            logger.info(f"✅ 回复成功 [{hr_name}]: {reply[:50]}...")
        else:
            logger.error(f"发送回复失败 [{conv_id}]")
            conv.status = ConversationStatus.WAITING_REPLY
            conv.add_message("me", f"[发送失败] {reply}")

        # 保存对话
        self._save_conversation(conv)
        return success

    # ----------------------------------------------------------
    # 持久化
    # ----------------------------------------------------------
    def _save_conversation(self, conv: Conversation):
        """保存单个对话到 JSON 文件"""
        filepath = os.path.join(config.CONVERSATIONS_DIR, f"{conv.conversation_id}.json")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(conv.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存对话失败 [{conv.conversation_id}]: {e}")

    def _load_conversations(self):
        """启动时加载已有的对话记录"""
        conv_dir = config.CONVERSATIONS_DIR
        if not os.path.exists(conv_dir):
            return
        count = 0
        for filename in os.listdir(conv_dir):
            if filename.endswith(".json"):
                filepath = os.path.join(conv_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    conv = Conversation.from_dict(data)
                    self.conversations[conv.conversation_id] = conv
                    count += 1
                except Exception as e:
                    logger.warning(f"加载对话文件失败 [{filename}]: {e}")
        if count:
            logger.info(f"已加载 {count} 个历史对话")
