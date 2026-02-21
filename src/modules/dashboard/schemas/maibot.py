"""
MaiBot 控制 Schema

定义 MaiBot 插件控制相关的数据模型。
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from src.modules.types.intent import ActionType, EmotionType


class MaibotActionRequest(BaseModel):
    """MaiBot 控制请求"""

    action: Optional[ActionType] = Field(default=None, description="动作类型 (hotkey, expression, motion...)")
    action_params: Dict[str, Any] = Field(default_factory=dict, description="动作参数")
    emotion: Optional[EmotionType] = Field(default=None, description="情绪类型 (happy, neutral, sad...)")
    priority: int = Field(default=50, ge=0, le=100, description="动作优先级 (0-100)")
    text: Optional[str] = Field(default=None, description="可选的回复文本")


class MaibotActionResponse(BaseModel):
    """MaiBot 控制响应"""

    success: bool
    intent_id: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
