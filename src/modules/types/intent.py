"""跨阶段共享的 Intent 类型(结构化版本)。

设计原则:
- `IntentAction` / `IntentEmotion` 是 Pydantic 模型,不是字符串别名
- `Emotion.name` 通过 `field_validator` 强制从 `Emotion` 枚举中取值
- `IntentAction.name` 是全限定名 `<handler>.<local_action>`(Manager 也会加前缀)
- `IntentAction.parameters` 不限定 schema(handler 内部 Pydantic 校验)
- 移除 `context` / `structured_params` / `parser_type` / `llm_model` / `replay_count` /
  `extra` / `alias` / `model_config`(破坏性升级,无向后兼容)

放这里是为了避免 Input/Decision/Output 阶段之间循环依赖。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.modules.types.emotion_vocab import Emotion


def _new_intent_id() -> str:
    """生成 Intent 唯一 ID(UUID4 hex 前 8 位)。"""
    return uuid.uuid4().hex[:8]


class IntentMetadata(BaseModel):
    """意图元数据。

    除决策来源 + 决策时间外,新增 `intent_id` 作为跨阶段唯一标识,
    用于 OutputHandlerManager 的两层事件聚合(把 per-handler 完成事件关联回同一个 intent)。
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., description="决策来源标识,如 'maibot_api' / 'command' / 'dashboard_debug'")
    decision_time_ms: int = Field(..., description="决策时刻(Unix 毫秒)")
    source_message_id: Optional[str] = Field(default=None, description="来源 NormalizedMessage 的 message_id")
    intent_id: str = Field(
        default_factory=_new_intent_id,
        description="Intent 唯一标识 (UUID4 hex 前 8 位)。由 IntentMetadata 自动生成,"
        "跨阶段用于关联同一 intent 的多个 handler 完成事件。",
    )


class IntentAction(BaseModel):
    """意图中的动作(结构化)。

    `name` 形如 `<handler>.<local_action>`,例如 `warudo.wave`。
    `parameters` 不限定 schema,handler 内部用 Pydantic model 校验。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="全限定 action 名,格式 `<handler>.<local_action>`")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="动作参数(handler 内部 Pydantic 校验)")


class IntentEmotion(BaseModel):
    """意图中的情绪(结构化)。

    `name` 必须来自 `Emotion` 枚举(12 个值),`intensity` 范围 [0.0, 1.0],默认 0.5。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="情绪名,必须是 Emotion 枚举值之一")
    intensity: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="情绪强度,[0.0, 1.0],默认 0.5(中等)",
    )

    @field_validator("name")
    @classmethod
    def _validate_emotion_name(cls, v: str) -> str:
        """强制 `name` 必须是 `Emotion` 枚举里的小写字符串值。"""
        valid = {e.value for e in Emotion}
        if v not in valid:
            raise ValueError(f"emotion.name '{v}' 不在全局 Emotion 枚举中({sorted(valid)})")
        return v


class Intent(BaseModel):
    """决策意图(平台无关,核心数据结构)。

    字段:
    - speech: 要说的文本(可选)
    - metadata: 元数据(必填,来源 + 决策时间)
    - emotion: 情绪(可选)
    - action: 动作(可选)
    """

    model_config = ConfigDict(extra="forbid")

    speech: Optional[str] = Field(default=None, description="AI 要说的话")
    metadata: IntentMetadata = Field(..., description="意图元数据")
    emotion: Optional[IntentEmotion] = Field(default=None, description="情绪(可选)")
    action: Optional[IntentAction] = Field(default=None, description="动作(可选)")


__all__ = [
    "Intent",
    "IntentAction",
    "IntentEmotion",
    "IntentMetadata",
]
