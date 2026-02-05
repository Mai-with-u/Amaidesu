"""
Decision Domain 事件 Payload 定义

定义 Decision Domain 相关的事件 Payload 类型。
- DecisionRequestPayload: 决策请求事件
- IntentPayload: 意图生成事件
- DecisionResponsePayload: 决策响应事件（MaiCore）
- ProviderConnectedPayload: Provider 连接事件
- ProviderDisconnectedPayload: Provider 断开事件
"""

from typing import Any, Dict, List, Optional, TYPE_CHECKING
from pydantic import BaseModel, Field, ConfigDict
import time

if TYPE_CHECKING:
    from src.domains.decision.intent import Intent


class DecisionRequestPayload(BaseModel):
    """
    决策请求事件 Payload

    事件名：CoreEvents.DECISION_REQUEST
    发布者：任何需要决策的组件
    订阅者：DecisionManager

    用于显式请求对某个消息进行决策处理。
    """

    normalized_message: Dict[str, Any] = Field(..., description="标准化消息（NormalizedMessage 序列化）")
    context: Dict[str, Any] = Field(default_factory=dict, description="上下文信息")
    priority: int = Field(default=100, ge=0, le=1000, description="优先级（数字越小越优先）")
    timestamp: float = Field(default_factory=time.time, description="时间戳")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "normalized_message": {
                    "text": "你好",
                    "source": "console_input",
                    "data_type": "text",
                    "importance": 0.5,
                },
                "context": {"conversation_id": "conv_456"},
                "priority": 100,
                "timestamp": 1706745600.0,
            }
        }
    )


class IntentActionPayload(BaseModel):
    """
    意图动作 Payload

    表示 Intent 中的单个动作。
    """

    type: str = Field(..., description="动作类型（如 'expression', 'hotkey', 'blink'）")
    params: Dict[str, Any] = Field(default_factory=dict, description="动作参数")
    priority: int = Field(default=50, ge=0, le=100, description="优先级（越高越优先）")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "blink",
                "params": {"count": 2},
                "priority": 30,
            }
        }
    )


class IntentPayload(BaseModel):
    """
    意图生成事件 Payload

    事件名：CoreEvents.DECISION_INTENT_GENERATED
    发布者：DecisionManager（Layer 3）
    订阅者：ExpressionGenerator（Layer 4）

    **5层架构说明**：
    - DecisionProvider.decide() 直接返回 Intent
    - DecisionManager 接收到 Intent 后发布此事件
    - ExpressionGenerator 订阅此事件并生成渲染参数

    **废弃说明**：
    - 旧架构（7层）中，此事件名为 understanding.intent_generated
    - 由 UnderstandingLayer 发布，现已废弃
    """

    original_text: str = Field(..., description="原始输入文本")
    response_text: str = Field(..., description="AI回复文本")
    emotion: str = Field(default="neutral", description="情感类型（如 'happy', 'sad', 'angry'）")
    actions: List[IntentActionPayload] = Field(default_factory=list, description="动作列表")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    timestamp: float = Field(default_factory=time.time, description="时间戳")
    provider: str = Field(..., description="决策Provider名称")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "original_text": "你好",
                "response_text": "你好！很高兴见到你~",
                "emotion": "happy",
                "actions": [
                    {"type": "blink", "params": {"count": 2}, "priority": 30},
                    {"type": "wave", "params": {"duration": 1.0}, "priority": 50},
                ],
                "metadata": {"confidence": 0.95},
                "timestamp": 1706745600.0,
                "provider": "maicore",
            }
        }
    )

    @classmethod
    def from_intent(cls, intent: "Intent", provider: str) -> "IntentPayload":
        """
        从 Intent 对象创建 Payload

        Args:
            intent: Intent 对象
            provider: 决策Provider名称

        Returns:
            IntentPayload 实例
        """
        actions = [
            IntentActionPayload(
                type=action.type.value,
                params=action.params,
                priority=action.priority,
            )
            for action in intent.actions
        ]

        return cls(
            original_text=intent.original_text,
            response_text=intent.response_text,
            emotion=intent.emotion.value,
            actions=actions,
            metadata=intent.metadata.copy(),
            timestamp=intent.timestamp,
            provider=provider,
        )


class DecisionResponsePayload(BaseModel):
    """
    决策响应事件 Payload

    事件名：CoreEvents.DECISION_RESPONSE_GENERATED
    发布者：MaiCoreDecisionProvider 内部
    订阅者：MaiCoreDecisionProvider 内部处理

    **注意**：此事件主要在 MaiCoreDecisionProvider 内部使用
    - MaiCore 返回的是 MessageBase 格式的响应
    - MaiCoreDecisionProvider 通过 IntentParser 转换为 Intent
    - 然后发布 decision.intent_generated 事件

    此事件用于 MaiCoreDecisionProvider 内部的异步响应处理。
    """

    response: Dict[str, Any] = Field(..., description="决策响应（MessageBase 格式）")
    provider: str = Field(..., description="决策Provider名称")
    latency_ms: float = Field(default=0, ge=0, description="决策延迟（毫秒）")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    timestamp: float = Field(default_factory=time.time, description="时间戳")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "response": {
                    "message_text": "你好！很高兴见到你~",
                    "emotion": "happy",
                },
                "provider": "maicore",
                "latency_ms": 150.5,
                "metadata": {"model": "gpt-3.5-turbo"},
                "timestamp": 1706745600.0,
            }
        }
    )


class ProviderConnectedPayload(BaseModel):
    """
    Provider 连接成功事件 Payload

    事件名：CoreEvents.DECISION_PROVIDER_CONNECTED
    发布者：DecisionProvider
    订阅者：任何需要监控 Provider 状态的组件

    表示 DecisionProvider 已成功连接到外部服务（如 MaiCore）。
    """

    provider: str = Field(..., description="Provider 名称")
    endpoint: Optional[str] = Field(default=None, description="连接端点")
    timestamp: float = Field(default_factory=time.time, description="连接时间")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider": "maicore",
                "endpoint": "ws://localhost:8000/ws",
                "timestamp": 1706745600.0,
                "metadata": {"reconnect_count": 0},
            }
        }
    )


class ProviderDisconnectedPayload(BaseModel):
    """
    Provider 断开连接事件 Payload

    事件名：CoreEvents.DECISION_PROVIDER_DISCONNECTED
    发布者：DecisionProvider
    订阅者：任何需要监控 Provider 状态的组件

    表示 DecisionProvider 与外部服务断开连接。
    """

    provider: str = Field(..., description="Provider 名称")
    reason: str = Field(default="unknown", description="断开原因")
    will_retry: bool = Field(default=False, description="是否将重试连接")
    timestamp: float = Field(default_factory=time.time, description="断开时间")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider": "maicore",
                "reason": "connection_lost",
                "will_retry": True,
                "timestamp": 1706745600.0,
                "metadata": {"reconnect_attempt": 1},
            }
        }
    )
