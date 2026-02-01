"""
Intent数据结构 - Layer 5: 表现理解层

职责:
- 定义Intent数据类
- 定义ActionType和EmotionType枚举
- 支持情感判断
- 支持动作提取
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List
from enum import Enum
import time


class EmotionType(str, Enum):
    """情感类型"""

    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    EXCITED = "excited"
    CONFUSED = "confused"
    LOVE = "love"


class ActionType(str, Enum):
    """动作类型"""

    TEXT = "text"  # 文本回复
    EMOJI = "emoji"  # 表情符号
    HOTKEY = "hotkey"  # 热键
    TTS = "tts"  # 语音合成
    SUBTITLE = "subtitle"  # 字幕
    EXPRESSION = "expression"  # VTS表情
    MOTION = "motion"  # 动作
    CUSTOM = "custom"  # 自定义动作


@dataclass
class IntentAction:
    """
    Intent动作

    Attributes:
        type: 动作类型
        params: 动作参数
        priority: 优先级(数字越小越优先,默认100)
        timestamp: 时间戳(Unix时间戳,秒)
    """

    type: ActionType
    params: Dict[str, Any] = field(default_factory=dict)
    priority: int = 100
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        """初始化后处理"""
        if self.params is None:
            self.params = {}


@dataclass
class Intent:
    """
    Intent意图(Layer 5: 表现理解层)

    核心职责:
    - 解析决策结果(MessageBase)为意图
    - 提取情感类型
    - 提取回复文本
    - 提取动作列表(表情、热键、TTS等)

    Attributes:
        original_text: 原始文本
        emotion: 情感类型
        response_text: 回复文本
        actions: 动作列表
        metadata: 扩展元数据
        timestamp: 时间戳(Unix时间戳,秒)
    """

    original_text: str
    emotion: EmotionType = EmotionType.NEUTRAL
    response_text: str = ""
    actions: List[IntentAction] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        """初始化后处理"""
        if self.metadata is None:
            self.metadata = {}

    def add_action(
        self,
        action_type: ActionType,
        params: Dict[str, Any],
        priority: int = 100,
    ) -> None:
        """
        添加动作

        Args:
            action_type: 动作类型
            params: 动作参数
            priority: 优先级
        """
        action = IntentAction(
            type=action_type,
            params=params,
            priority=priority,
            timestamp=time.time(),
        )
        self.actions.append(action)

    def to_dict(self) -> dict:
        """
        序列化为字典

        Returns:
            序列化后的字典
        """
        return {
            "original_text": self.original_text,
            "emotion": self.emotion.value,
            "response_text": self.response_text,
            "actions": [
                {
                    "type": action.type.value,
                    "params": action.params,
                    "priority": action.priority,
                    "timestamp": action.timestamp,
                }
                for action in self.actions
            ],
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Intent":
        """
        从字典反序列化

        Args:
            data: 字典数据

        Returns:
            Intent实例
        """
        # 反序列化emotion
        emotion = EmotionType(data.get("emotion", "neutral"))

        # 反序列化actions
        actions = []
        for action_data in data.get("actions", []):
            action_type = ActionType(action_data.get("type", "text"))
            params = action_data.get("params", {})
            priority = action_data.get("priority", 100)
            timestamp = action_data.get("timestamp", time.time())

            actions.append(
                IntentAction(
                    type=action_type,
                    params=params,
                    priority=priority,
                    timestamp=timestamp,
                )
            )

        return cls(
            original_text=data.get("original_text", ""),
            emotion=emotion,
            response_text=data.get("response_text", ""),
            actions=actions,
            metadata=data.get("metadata", {}),
            timestamp=data.get("timestamp", time.time()),
        )

    def __repr__(self) -> str:
        """字符串表示"""
        action_count = len(self.actions)
        emotion_str = self.emotion.value
        return f"Intent(text='{self.original_text[:30]}...', emotion={emotion_str}, actions={action_count})"
