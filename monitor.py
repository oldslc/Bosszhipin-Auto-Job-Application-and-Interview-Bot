"""
消息监听模块 - 求职者端
负责：
  - 定时轮询 Boss 直聘求职者聊天页面的左侧对话列表
  - 检测未读消息（红点/未读标记）
  - 点击进入有新消息的对话
  - 提取对方信息（HR名称、公司、职位）和最新消息内容
  - 将新消息交给 ChatHandler 处理
"""

import hashlib
import json
import logging
import random
import time
from typing import Optional, List, Dict


import config
from browser import BrowserController
from chat_handler import ChatHandler

logger = logging.getLogger(__name__)


class ChatMonitor:
    """
    聊天监控器 - 求职者端
    轮询 Boss 直聘求职者聊天页面，发现新消息后自动处理
    """

    def __init__(self, browser: BrowserController, chat_handler: ChatHandler):
        self.browser = browser
        self.chat_handler = chat_handler
        # 记录已经处理过的对话，避免重复回复
        self._processed: set = set()
        # 当前选中的对话 ID
        self._current_conv_id: Optional[str] = None

    # ----------------------------------------------------------
    # 生成对话唯一 ID
    # ----------------------------------------------------------
    @staticmethod
    def _make_conv_id(hr_name: str, company: str) -> str:
        """根据 HR 名称和公司名生成唯一对话 ID"""
        raw = f"{hr_name}_{company}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]

    # ----------------------------------------------------------
    # 检测未读消息
    # ----------------------------------------------------------
    def _find_unread_items(self) -> list:
        """
        扫描左侧对话列表，找到所有有未读标记的对话项
        返回元素信息列表
        """
        page = self.browser.get_page()
        if not page:
            return []

        # 尝试多种选择器（Boss 直聘页面可能更新 class 名）
        selectors = config.SELECTOR_CHAT_ITEM.split(",")
        for selector in selectors:
            selector = selector.strip()
            if not selector:
                continue
            items = page.find_elements(selector)
            if items:
                unread = []
                for item in items:
                    # 检查该项是否包含未读标记
                    has_unread = False
                    for badge_sel in config.SELECTOR_UNREAD_BADGE.split(","):
                        badge_sel = badge_sel.strip()
                        if not badge_sel:
                            continue
                        # 用 JS 检查该项是否包含未读类名
                        check = page.evaluate(f'''
                            (() => {{
                                const items = document.querySelectorAll({json.dumps(selector)});
                                for (const el of items) {{
                                    if (el.innerText?.includes({json.dumps(item.get('text', ''))}) && el.querySelector({json.dumps(badge_sel)})) {{
                                        return true;
                                    }}
                                }}
                                return false;
                            }})()
                        ''')
                        if check:
                            has_unread = True
                            break
                    if has_unread:
                        unread.append(item)
                if unread:
                    logger.debug(f"发现 {len(unread)} 个未读对话")
                    return unread
        return []

    # ----------------------------------------------------------
    # 提取对话信息
    # ----------------------------------------------------------
    def _extract_chat_info(self) -> Dict[str, str]:
        """
        从当前打开的对话中提取信息
        返回: {hr_name, company, position, salary, last_message}
        """
        info = {
            "hr_name": "未知HR",
            "company": "未知公司",
            "position": "未知职位",
            "salary": "面议",
            "last_message": ""
        }

        # 等待右侧内容加载
        time.sleep(1.5)

        page = self.browser.get_page()

        # 提取 HR 名称
        for sel in config.SELECTOR_HR_NAME.split(","):
            sel = sel.strip()
            if not sel:
                continue
            text = page.get_text(sel)
            if text and text.strip():
                info["hr_name"] = text.strip()
                break

        # 提取公司名称
        for sel in config.SELECTOR_COMPANY.split(","):
            sel = sel.strip()
            if not sel:
                continue
            text = page.get_text(sel)
            if text and text.strip():
                info["company"] = text.strip()
                break

        # 提取职位名称
        for sel in config.SELECTOR_POSITION.split(","):
            sel = sel.strip()
            if not sel:
                continue
            text = page.get_text(sel)
            if text and text.strip():
                info["position"] = text.strip()
                break

        # 提取最新一条消息
        for sel in config.SELECTOR_MESSAGE_BUBBLE.split(","):
            sel = sel.strip()
            if not sel:
                continue
            elements = page.find_all_by_css(sel)
            if elements:
                last = elements[-1]
                text = last.get('text', '') if isinstance(last, dict) else getattr(last, 'text', '')
                if text and text.strip():
                    info["last_message"] = text.strip()
                    break

        return info

    # ----------------------------------------------------------
    # 获取右侧最新一条 HR 消息
    # ----------------------------------------------------------
    def _get_latest_hr_message(self) -> Optional[str]:
        """
        获取右侧聊天区域中最后一条消息
        只返回 HR 发送的消息（非自己发送的）
        """
        page = self.browser.get_page()
        if not page:
            return None

        # 通过 JavaScript 获取所有消息
        try:
            result = page.evaluate("""
                (() => {
                    const messages = document.querySelectorAll(
                        '[class*="message-content"], [class*="bubble"], .word-wrap, [class*="chat-message"], [class*="msg-content"]'
                    );
                    if (!messages || messages.length === 0) return null;

                    const lastMsg = messages[messages.length - 1];
                    const text = lastMsg.innerText?.trim();
                    if (!text) return null;

                    const parent = lastMsg.closest('[class*="message"]') || lastMsg.parentElement;
                    const parentClass = parent?.className || '';
                    const isSelf = /self|mine|right|send|geek/i.test(parentClass);

                    return JSON.stringify({ text: text, isSelf: isSelf });
                })()
            """)
            if result:
                data = json.loads(result)
                if not data.get("isSelf"):
                    return data.get("text")
        except Exception as e:
            logger.debug(f"通过 JS 提取消息失败: {e}")

        # 备用方案
        for sel in config.SELECTOR_MESSAGE_BUBBLE.split(","):
            sel = sel.strip()
            if not sel:
                continue
            elements = page.find_all_by_css(sel)
            if elements:
                last = elements[-1]
                text = last.get('text', '') if isinstance(last, dict) else getattr(last, 'text', '')
                if text:
                    return text.strip()

        return None

    # ----------------------------------------------------------
    # 点击对话项
    # ----------------------------------------------------------
    def _click_chat_item(self, item) -> bool:
        """点击左侧对话列表中的某一项"""
        try:
            self.browser.simulate_mouse_move()
            # item 现在是 dict，用 CDP 点击
            page = self.browser.get_page()
            # 通过文本找元素并点击
            text = item.get('text', '')[:50]
            if text:
                page.evaluate(f'''
                    (() => {{
                        const items = document.querySelectorAll('.chat-conversation');
                        for (const el of items) {{
                            if (el.innerText?.includes({json.dumps(text)})) {{
                                const rect = el.getBoundingClientRect();
                                const evt = new MouseEvent('click', {{bubbles:true, cancelable:true, view:window, clientX: rect.x + 10, clientY: rect.y + 10}});
                                el.dispatchEvent(evt);
                                return true;
                            }}
                        }}
                        return false;
                    }})()
                ''')
            time.sleep(random.uniform(1.5, 3.0))
            return True
        except Exception as e:
            logger.debug(f"点击对话项失败: {e}")
            return False

    # ----------------------------------------------------------
    # 主轮询循环
    # ----------------------------------------------------------
    def poll_once(self) -> bool:
        """
        执行一次轮询：检测未读 → 点击 → 提取信息 → 处理
        返回是否有新消息被处理
        """
        try:
            # 找到未读对话
            unread_items = self._find_unread_items()
            if not unread_items:
                return False

            processed_any = False

            for item in unread_items:
                try:
                    # 点击进入对话
                    clicked = self._click_chat_item(item)
                    if not clicked:
                        continue

                    # 等待内容加载（更长时间）
                    time.sleep(random.uniform(1.5, 3.0))

                    # 提取对话信息
                    info = self._extract_chat_info()
                    conv_id = self._make_conv_id(info["hr_name"], info["company"])

                    # 获取最新 HR 消息
                    last_msg = self._get_latest_hr_message()
                    if not last_msg:
                        logger.debug(f"未获取到消息内容 [{conv_id}]")
                        continue

                    # 检查是否已处理过这条消息（用内容+对话ID做简单去重）
                    msg_key = f"{conv_id}_{last_msg}"
                    if msg_key in self._processed:
                        logger.debug(f"消息已处理过，跳过: {msg_key[:30]}")
                        continue

                    logger.info(f"📨 新消息 [{info['hr_name']} @ {info['company']}]: {last_msg[:50]}")
                    self._processed.add(msg_key)

                    # 交给 ChatHandler 处理
                    self.chat_handler.handle_new_message(
                        conv_id=conv_id,
                        hr_name=info["hr_name"],
                        company=info["company"],
                        position=info["position"],
                        salary=info["salary"],
                        new_message=last_msg
                    )
                    processed_any = True

                    # 处理完一个后等待更长时间，避免太快
                    time.sleep(random.uniform(3.0, 6.0))

                except Exception as e:
                    logger.error(f"处理对话项时出错: {e}")
                    continue

            return processed_any

        except Exception as e:
            logger.error(f"轮询出错: {e}")
            return False

    def run(self, interval: int = None):
        """
        持续轮询循环
        interval: 轮询间隔（秒），默认使用 config.POLL_INTERVAL
        """
        interval = interval or config.POLL_INTERVAL
        logger.info(f"🚀 求职者消息监听已启动，轮询间隔: {interval} 秒")

        while True:
            try:
                self.poll_once()
            except Exception as e:
                logger.error(f"轮询异常: {e}")
            # 在轮询间隔中添加随机浮动
            jitter = random.uniform(-1.0, 2.0)
            time.sleep(max(3, interval + jitter))
