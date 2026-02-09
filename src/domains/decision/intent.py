"""
Intent数据类型定义

Decision Domain (决策域) 的输出格式，表示决策意图。
"""

from typing import List, Dict, Any, Optional
from uuid import uuid4
import time

from pydantic import BaseModel, Field

# 从 core.types 导入共享类型
from src.core.types import EmotionType, ActionType, IntentAction


class SourceContext(BaseModel):
    """触发此 Intent 的输入事件上下文摘要"""

    source: str = Field(..., description="输入源类型（如 'console_input', 'danmaku'）")
    data_type: str = Field(..., description="数据类型（如 'text', 'gift'）")
    user_id: Optional[str] = Field(default=None, description="用户ID")
    user_nickname: Optional[str] = Field(default=None, description="用户昵称")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="重要性评分")
    extra: Dict[str, Any] = Field(default_factory=dict, description="额外上下文信息")


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
