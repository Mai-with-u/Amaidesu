"""
MaiBot 控制 Schema

定义 MaiBot 插件控制相关的数据模型。

结构化 payload(IntentEmotionPayload / IntentActionApiPayload)替代旧的字符串字段。
- `IntentActionApiPayload` 故意区别于 events 模块的 `IntentActionPayload`(避免名字冲突)
- 不包含 `priority` 字段(EventBus fire-and-forget 并发分发,handler 错误隔离开箱即用)
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class IntentEmotionPayload(BaseModel):
    """HTTP 端的情绪子 payload。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="情绪名,必须来自全局 Emotion 枚举")
    intensity: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="情绪强度 [0.0, 1.0],默认 0.5(中等)",
    )


class IntentActionApiPayload(BaseModel):
    """HTTP 端的动作子 payload(全限定名 + 任意 parameters)。

    命名上带 `Api` 后缀,避免与 `src/modules/events/payloads/decision.py` 中的
    `IntentActionPayload`(用于 decision.intent.action 事件的 LLM 风格 type/params/priority
    payload)冲突。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="全限定 action 名,格式 `<handler>.<local_action>`")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="动作参数(handler 内部 Pydantic 校验)")


class MaibotActionRequest(BaseModel):
    """MaiBot 控制请求(结构化版本)。"""

    model_config = ConfigDict(extra="forbid")

    text: Optional[str] = Field(default=None, description="可选的回复文本(speech)")
    emotion: Optional[IntentEmotionPayload] = Field(default=None, description="情绪子 payload")
    action: Optional[IntentActionApiPayload] = Field(default=None, description="动作子 payload")


class MaibotActionResponse(BaseModel):
    """MaiBot 控制响应"""

    success: bool
    intent_id: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None


__all__ = [
    "IntentEmotionPayload",
    "IntentActionApiPayload",
    "MaibotActionRequest",
    "MaibotActionResponse",
]
