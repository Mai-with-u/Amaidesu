"""跨域共享的 Intent 相关类型

这些类型被 Input/Decision/Output Domain 共享，
因此放在 src/modules/types/ 中避免循环依赖。
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class EmotionType(str, Enum):
    """情感类型枚举"""

    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    LOVE = "love"
    SHY = "shy"
    EXCITED = "excited"
    CONFUSED = "confused"
    SCARED = "scared"

    @classmethod
    def get_default(cls) -> EmotionType:
        """获取默认情感类型"""
        return cls.NEUTRAL


class ActionType(str, Enum):
    """动作类型枚举"""

    EXPRESSION = "expression"  # 表情
    HOTKEY = "hotkey"  # 热键
    EMOJI = "emoji"  # emoji表情
    BLINK = "blink"  # 眨眼
    NOD = "nod"  # 点头
    SHAKE = "shake"  # 摇头
    WAVE = "wave"  # 挥手
    CLAP = "clap"  # 鼓掌
    STICKER = "sticker"  # 贴图
    MOTION = "motion"  # 动作
    CUSTOM = "custom"  # 自定义
    GAME_ACTION = "game_action"  # 游戏动作
    NONE = "none"  # 无动作

    @classmethod
    def get_default(cls) -> ActionType:
        """获取默认动作类型"""
        return cls.EXPRESSION


class IntentAction(BaseModel):
    """意图动作"""

    model_config = ConfigDict(use_enum_values=True)

    type: ActionType = Field(..., description="动作类型")
    params: Dict[str, Any] = Field(default_factory=dict, description="动作参数")
    priority: int = Field(default=50, ge=0, le=100, description="优先级（越高越优先）")


class SourceContext(BaseModel):
    """触发此 Intent 的输入事件上下文摘要"""

    source: str = Field(..., description="输入源类型（如 'console_input', 'danmaku'）")
    data_type: str = Field(..., description="数据类型（如 'text', 'gift'）")
    user_id: Optional[str] = Field(default=None, description="用户ID")
    user_nickname: Optional[str] = Field(default=None, description="用户昵称")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="重要性评分")
    extra: Dict[str, Any] = Field(default_factory=dict, description="额外上下文信息")


class Intent(BaseModel):
    """
    决策意图 - Decision Domain 的核心输出

    核心职责：
    - 表示AI的决策意图
    - 包含回复文本、情感、动作
    - 作为 Output Domain 的输入
    """

    id: str = Field(default_factory=lambda: str(uuid4()), description="唯一标识符")
    source_context: Optional[SourceContext] = Field(default=None, description="输入源上下文")
    original_text: str = Field(..., description="原始输入文本")
    response_text: str = Field(..., description="AI回复文本")
    emotion: EmotionType = Field(default=EmotionType.NEUTRAL, description="情感类型")
    actions: List[IntentAction] = Field(default_factory=list, description="动作列表")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    timestamp: float = Field(default_factory=time.time, description="时间戳")

    def __repr__(self) -> str:
        """字符串表示"""
        return (
            f"Intent("
            f"id={self.id[:8]}..., "
            f"emotion={self.emotion}, "
            f"actions={len(self.actions)}, "
            f"response_text='{self.response_text[:30]}...'"
            f")"
        )


__all__ = [
    "EmotionType",
    "ActionType",
    "IntentAction",
    "SourceContext",
    "Intent",
]
