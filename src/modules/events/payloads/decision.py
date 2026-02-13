"""
Decision Domain 事件 Payload 定义

定义 Decision Domain 相关的事件 Payload 类型。
- DecisionRequestPayload: 决策请求事件
- IntentPayload: 意图生成事件
- DecisionResponsePayload: 决策响应事件（MaiCore）
- ProviderConnectedPayload: Provider 连接事件
- ProviderDisconnectedPayload: Provider 断开事件
"""

import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload

if TYPE_CHECKING:
    from src.modules.types import Intent


class DecisionRequestPayload(BasePayload):
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

    def __str__(self) -> str:
        """简化格式：只显示关键字段"""
        class_name = self.__class__.__name__
        return f"{class_name}(priority={self.priority}, timestamp={self.timestamp:.0f})"


class IntentActionPayload(BasePayload):
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

    def __str__(self) -> str:
        """简化格式：显示动作类型和参数"""
        class_name = self.__class__.__name__
        return f'{class_name}(type="{self.type}", params={self._format_field_value(self.params)}, priority={self.priority})'


class IntentPayload(BasePayload):
    """
    意图生成事件 Payload

    事件名：CoreEvents.DECISION_INTENT
    发布者：DecisionManager（Decision Domain）
    订阅者：ExpressionGenerator（Output Domain）

    **3域架构说明**：
    - DecisionProvider.decide() 直接返回 Intent
    - DecisionManager 接收到 Intent 后发布此事件
    - ExpressionGenerator 订阅此事件并生成渲染参数

    **简化设计**：
    - 使用 Pydantic 的自动序列化（model_dump/model_validate）
    - Intent 数据存储为字典，避免重复字段定义
    - 提供便捷方法与 Intent 对象互转
    """

    intent_data: Dict[str, Any] = Field(..., description="Intent 序列化数据")
    provider: str = Field(..., description="决策Provider名称")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "intent_data": {
                    "original_text": "你好",
                    "response_text": "你好！很高兴见到你~",
                    "emotion": "happy",
                    "actions": [
                        {"type": "blink", "params": {"count": 2}, "priority": 30},
                        {"type": "wave", "params": {"duration": 1.0}, "priority": 50},
                    ],
                    "metadata": {"confidence": 0.95},
                    "timestamp": 1706745600.0,
                },
                "provider": "maicore",
            }
        }
    )

    def __getattr__(self, name: str) -> Any:
        """代理访问 intent_data 中的字段，用于调试显示"""
        # 只代理已知的字段，其他字段抛出 AttributeError
        KNOWN_FIELDS = [
            "original_text",
            "response_text",
            "emotion",
            "actions",
            "metadata",  # 添加缺失的字段
            "timestamp",  # 添加缺失的字段
        ]
        if name in KNOWN_FIELDS:
            value = self.intent_data.get(name, "")
            # metadata 默认返回空字典而非空字符串
            if name == "metadata" and value == "":
                return {}
            return value
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'. 可用字段: {KNOWN_FIELDS}")

    def __str__(self) -> str:
        """
        自定义字符串表示，显示 intent_data 中的关键字段。

        Returns:
            调试字符串
        """
        parts = [
            f'provider="{self.provider}"',
            f'original_text="{self.intent_data.get("original_text", "")}"',
            f'response_text="{self.intent_data.get("response_text", "")}"',
            f'emotion="{self.intent_data.get("emotion", "")}"',
        ]
        # 格式化 actions
        actions = self.intent_data.get("actions", [])
        if actions:
            types = [a.get("type", "?") for a in actions[:3]]
            if len(actions) > 3:
                types.append("...")
            parts.append(f"actions=[{', '.join(types)}]")
        return f"IntentPayload({', '.join(parts)})"

    @classmethod
    def from_intent(cls, intent: "Intent", provider: str) -> "IntentPayload":
        """
        从 Intent 对象创建 Payload

        使用 Pydantic 的自动序列化，将 Intent 转换为字典存储。

        Args:
            intent: Intent 对象
            provider: 决策Provider名称

        Returns:
            IntentPayload 实例
        """
        return cls(intent_data=intent.model_dump(mode="json"), provider=provider)

    def to_intent(self) -> "Intent":
        """
        转换为 Intent 对象

        使用 Pydantic 的自动反序列化，从字典还原 Intent。

        Returns:
            Intent 实例
        """
        from src.modules.types import Intent

        return Intent.model_validate(self.intent_data)


class DecisionResponsePayload(BasePayload):
    """
    决策响应事件 Payload

    事件名：CoreEvents.DECISION_RESPONSE_GENERATED
    发布者：MaiCoreDecisionProvider 内部
    订阅者：MaiCoreDecisionProvider 内部处理

    **注意**：此事件主要在 MaiCoreDecisionProvider 内部使用
    - MaiCore 返回的是 MessageBase 格式的响应
    - MaiCoreDecisionProvider 自己解析为 Intent（使用 _parse_intent_from_maicore_response）
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

    def __str__(self) -> str:
        """简化格式：显示响应信息"""
        class_name = self.__class__.__name__
        return f'{class_name}(provider="{self.provider}", latency_ms={self.latency_ms:.1f})'


class ProviderConnectedPayload(BasePayload):
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

    def __str__(self) -> str:
        """自定义字符串表示，只显示关键字段"""
        return f'ProviderConnectedPayload(provider="{self.provider}", endpoint="{self.endpoint}")'


class ProviderDisconnectedPayload(BasePayload):
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

    def __str__(self) -> str:
        """自定义字符串表示，只显示关键字段"""
        return f'ProviderDisconnectedPayload(provider="{self.provider}", reason="{self.reason}", will_retry={self.will_retry})'
