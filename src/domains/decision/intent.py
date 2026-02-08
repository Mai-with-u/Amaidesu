"""
Intent数据类型定义

Decision Domain (决策域) 的输出格式，表示决策意图。
"""

from enum import Enum
from typing import List, Dict, Any, Optional
from uuid import uuid4
import time

from pydantic import BaseModel, Field


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


class ActionType(str, Enum):
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


class SourceContext(BaseModel):
    """触发此 Intent 的输入事件上下文摘要"""

    source: str = Field(..., description="输入源类型（如 'console_input', 'danmaku'）")
    data_type: str = Field(..., description="数据类型（如 'text', 'gift'）")
    user_id: Optional[str] = Field(default=None, description="用户ID")
    user_nickname: Optional[str] = Field(default=None, description="用户昵称")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="重要性评分")
    extra: Dict[str, Any] = Field(default_factory=dict, description="额外上下文信息")


class IntentAction(BaseModel):
    """意图动作"""

    type: ActionType = Field(..., description="动作类型")
    params: Dict[str, Any] = Field(default_factory=dict, description="动作参数")
    priority: int = Field(default=50, ge=0, le=100, description="优先级（越高越优先）")


class ActionSuggestion(BaseModel):
    """MaiBot Action 建议"""

    action_name: str = Field(..., description="动作名称")
    priority: int = Field(default=50, ge=0, le=100, description="优先级")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="动作参数")
    reason: str = Field(default="", description="建议原因")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="置信度")


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
    suggested_actions: Optional[List[ActionSuggestion]] = Field(default=None, description="建议的动作列表")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    timestamp: float = Field(default_factory=time.time, description="时间戳")

    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典

        Returns:
            Intent的字典表示
        """
        result = {
            "id": self.id,
            "original_text": self.original_text,
            "response_text": self.response_text,
            "emotion": self.emotion.value if isinstance(self.emotion, EmotionType) else self.emotion,
            "actions": [
                {
                    "type": action.type.value if isinstance(action.type, ActionType) else action.type,
                    "params": action.params,
                    "priority": action.priority,
                }
                for action in self.actions
            ],
            "metadata": self.metadata.copy(),
            "timestamp": self.timestamp,
        }
        if self.source_context:
            result["source_context"] = self.source_context.model_dump()
        if self.suggested_actions:
            result["suggested_actions"] = [
                {
                    "action_name": sa.action_name,
                    "priority": sa.priority,
                    "parameters": sa.parameters,
                    "reason": sa.reason,
                    "confidence": sa.confidence,
                }
                for sa in self.suggested_actions
            ]
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Intent":
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

        source_context = None
        if "source_context" in data and data["source_context"]:
            source_context = SourceContext(**data["source_context"])

        suggested_actions = None
        if "suggested_actions" in data and data["suggested_actions"]:
            suggested_actions = [ActionSuggestion(**sa) for sa in data["suggested_actions"]]

        kwargs = {
            "source_context": source_context,
            "original_text": data.get("original_text", ""),
            "response_text": data.get("response_text", ""),
            "emotion": EmotionType(data.get("emotion", "neutral")),
            "actions": actions,
            "suggested_actions": suggested_actions,
            "metadata": data.get("metadata", {}),
            "timestamp": data.get("timestamp", time.time()),
        }

        # 只有当 id 存在时才传递，否则使用 default_factory
        if "id" in data and data["id"] is not None:
            kwargs["id"] = data["id"]

        return cls(**kwargs)

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
