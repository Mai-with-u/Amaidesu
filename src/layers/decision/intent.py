"""
Intent数据类型定义

Layer 3: Decision的输出格式，表示决策意图。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional
import time


class EmotionType(Enum):
    """情感类型枚举"""

    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    LOVE = "love"


class ActionType(Enum):
    """动作类型枚举"""

    EXPRESSION = "expression"  # 表情
    HOTKEY = "hotkey"          # 热键
    EMOJI = "emoji"            # emoji表情
    BLINK = "blink"            # 眨眼
    NOD = "nod"                # 点头
    SHAKE = "shake"            # 摇头
    WAVE = "wave"              # 挥手
    CLAP = "clap"              # 鼓掌
    NONE = "none"              # 无动作


@dataclass
class IntentAction:
    """
    意图动作

    表示一个具体的动作或表现。

    Attributes:
        type: 动作类型
        params: 动作参数（字典）
        priority: 优先级（0-100，越高越优先）
    """

    type: ActionType
    params: Dict[str, Any]
    priority: int = 50

    def __repr__(self) -> str:
        """字符串表示"""
        return f"IntentAction(type={self.type.value}, params={self.params}, priority={self.priority})"


@dataclass
class Intent:
    """
    意图对象（Layer 3: Decision的输出）

    核心职责：
    - 表示AI的决策意图
    - 包含回复文本、情感、动作
    - 作为Layer 4的输入

    Attributes:
        original_text: 原始输入文本
        response_text: AI回复文本
        emotion: 情感类型
        actions: 动作列表
        metadata: 元数据
        timestamp: 时间戳
    """

    original_text: str
    response_text: str
    emotion: EmotionType
    actions: List[IntentAction]
    metadata: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        """初始化后处理"""
        if self.metadata is None:
            self.metadata = {}
        else:
            self.metadata = self.metadata.copy()

    def to_dict(self) -> dict:
        """
        转换为字典

        Returns:
            Intent的字典表示
        """
        return {
            "original_text": self.original_text,
            "response_text": self.response_text,
            "emotion": self.emotion.value,
            "actions": [
                {
                    "type": action.type.value,
                    "params": action.params,
                    "priority": action.priority,
                }
                for action in self.actions
            ],
            "metadata": self.metadata.copy(),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Intent":
        """
        从字典创建Intent

        Args:
            data: 字典数据

        Returns:
            Intent实例
        """
        actions = []
        for action_data in data.get("actions", []):
            actions.append(
                IntentAction(
                    type=ActionType(action_data["type"]),
                    params=action_data["params"],
                    priority=action_data.get("priority", 50),
                )
            )

        return cls(
            original_text=data.get("original_text", ""),
            response_text=data.get("response_text", ""),
            emotion=EmotionType(data.get("emotion", "neutral")),
            actions=actions,
            metadata=data.get("metadata", {}),
            timestamp=data.get("timestamp", time.time()),
        )

    def __repr__(self) -> str:
        """字符串表示"""
        return (
            f"Intent("
            f"emotion={self.emotion.value}, "
            f"actions={len(self.actions)}, "
            f"response_text='{self.response_text[:30]}...'"
            f")"
        )
