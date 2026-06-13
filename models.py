"""
数据模型定义
定义对话记录、消息等核心数据结构
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from enum import Enum


class ConversationStatus(Enum):
    """对话状态枚举"""
    ACTIVE = "active"           # 进行中
    WAITING_REPLY = "waiting"   # 等待回复
    ENDED = "ended"             # 已结束
    MANUAL = "manual"           # 需人工介入


@dataclass
class Message:
    """
    单条消息模型
    记录每条对话的发送者、内容、时间
    """
    sender: str                 # 发送者: "hr" 或 "me"
    content: str                # 消息内容
    timestamp: datetime = field(default_factory=datetime.now)  # 发送时间

    def to_dict(self) -> dict:
        """转换为字典，便于JSON序列化"""
        return {
            "sender": self.sender,
            "content": self.content,
            "timestamp": self.timestamp.isoformat()
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        """从字典创建Message实例"""
        return cls(
            sender=data["sender"],
            content=data["content"],
            timestamp=datetime.fromisoformat(data["timestamp"])
        )

    def to_chat_role(self) -> dict:
        """转换为LLM聊天格式的角色-内容对"""
        role = "assistant" if self.sender == "me" else "user"
        return {"role": role, "content": self.content}


@dataclass
class Conversation:
    """
    对话记录模型
    包含对方信息、消息列表、对话状态
    """
    conversation_id: str                # 唯一标识（通常用对方用户名+公司名生成）
    hr_name: str                        # HR姓名
    company: str                        # 公司名称
    position: str                       # 职位名称
    salary: str = "面议"                # 薪资范围
    status: ConversationStatus = ConversationStatus.ACTIVE
    messages: List[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def add_message(self, sender: str, content: str) -> Message:
        """添加一条消息到对话中"""
        msg = Message(sender=sender, content=content)
        self.messages.append(msg)
        self.updated_at = datetime.now()
        return msg

    def get_history(self, max_turns: int = 20) -> str:
        """
        获取对话历史，格式化为可读文本
        max_turns: 最多返回最近多少轮对话
        """
        recent = self.messages[-max_turns:] if len(self.messages) > max_turns else self.messages
        lines = []
        for msg in recent:
            role = "HR" if msg.sender == "hr" else "我"
            lines.append(f"[{role}]: {msg.content}")
        return "\n".join(lines)

    def get_llm_history(self, max_turns: int = 20) -> List[dict]:
        """
        获取对话历史，格式化为LLM聊天格式
        """
        recent = self.messages[-max_turns:] if len(self.messages) > max_turns else self.messages
        return [msg.to_chat_role() for msg in recent]

    def to_dict(self) -> dict:
        """转换为字典，便于JSON序列化"""
        return {
            "conversation_id": self.conversation_id,
            "hr_name": self.hr_name,
            "company": self.company,
            "position": self.position,
            "salary": self.salary,
            "status": self.status.value,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Conversation":
        """从字典创建Conversation实例"""
        conv = cls(
            conversation_id=data["conversation_id"],
            hr_name=data["hr_name"],
            company=data["company"],
            position=data["position"],
            salary=data.get("salary", "面议"),
            status=ConversationStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"])
        )
        conv.messages = [Message.from_dict(m) for m in data.get("messages", [])]
        return conv
