"""
Decision 阶段 事件 Payload 定义

定义 Decision 阶段 相关的事件 Payload 类型。
- IntentPayload: 意图生成事件
- IntentActionPayload: 意图动作 Payload
- ConnectedPayload: 组件连接事件
- DisconnectedPayload: 组件断开事件
"""

import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event

if TYPE_CHECKING:
    from src.modules.types import Intent


@register_event("decision.intent.action")
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


@register_event("decision.intent.generated")
class IntentPayload(BasePayload):
    """
    意图生成事件 Payload

    事件名：CoreEvents.DECISION_INTENT_GENERATED
    发布者：Decider（Decision 阶段）
    订阅者：OutputHandlerManager（Output 阶段）

    **3阶段架构说明**：
    - Decider.decide() 直接返回 Intent
    - Decider 通过 event_bus 发布此事件
    - OutputHandlerManager 订阅此事件并分发给 OutputHandlers

    **简化设计**：
    - 使用 Pydantic 的自动序列化（model_dump/model_validate）
    - Intent 数据存储为字典，避免重复字段定义
    - 提供便捷方法与 Intent 对象互转
    """

    intent_data: Dict[str, Any] = Field(..., description="Intent 序列化数据")
    name: str = Field(..., description="决策Decider名称")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "intent_data": {
                    "emotion": "开心",
                    "action": "脸红并挥手",
                    "speech": "你好！很高兴见到你~",
                    "context": "用户打招呼",
                    "metadata": {
                        "source_id": "msg_123",
                        "decision_time_ms": 1706745600000,
                        "parser_type": "llm",
                    },
                },
                "name": "maicore",
            }
        }
    )

    def __getattr__(self, name: str) -> Any:
        """代理访问 intent_data 中的字段，用于调试显示"""
        # 只代理已知的字段，其他字段抛出 AttributeError
        KNOWN_FIELDS = [
            "emotion",
            "action",
            "speech",
            "context",
            "metadata",
            # 向后兼容字段（已废弃但仍支持访问）
            "original_text",
            "response_text",
            "actions",
            "timestamp",
            "decision_time_ms",
        ]
        if name in KNOWN_FIELDS:
            # 向后兼容映射（顶层字段重命名）
            backward_compat_map = {
                "response_text": "speech",
                "actions": "action",
                "original_text": "context",
                "timestamp": "decision_time_ms",
            }
            if name == "timestamp":
                # timestamp 嵌套在 metadata 下，需要向下导航
                metadata = self.intent_data.get("metadata") or {}
                value = metadata.get("decision_time_ms", "")
            elif name in backward_compat_map:
                mapped_key = backward_compat_map[name]
                value = self.intent_data.get(mapped_key, "")
            else:
                value = self.intent_data.get(name, "")
            # metadata 默认返回空字典而非空字符串
            if name == "metadata" and value == "":
                return {}
            return value
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'. 可用字段: {KNOWN_FIELDS}")

    def __str__(self) -> str:
        """自定义字符串表示，显示 intent_data 中的关键字段"""
        parts = [
            f'name="{self.name}"',
            f'speech="{self.intent_data.get("speech", "")}"',
            f'emotion="{self.intent_data.get("emotion", "")}"',
            f'action="{self.intent_data.get("action", "")}"',
        ]
        return f"IntentPayload({', '.join(parts)})"

    @classmethod
    def from_intent(cls, intent: "Intent", name: str) -> "IntentPayload":
        """
        从 Intent 对象创建 Payload

        使用 Pydantic 的自动序列化，将 Intent 转换为字典存储。

        Args:
            intent: Intent 对象
            name: 决策Decider名称

        Returns:
            IntentPayload 实例
        """
        return cls(intent_data=intent.model_dump(mode="json"), name=name)

    def to_intent(self) -> "Intent":
        """
        转换为 Intent 对象

        使用 Pydantic 的自动反序列化，从字典还原 Intent。

        Returns:
            Intent 实例
        """
        from src.modules.types import Intent

        return Intent.model_validate(self.intent_data)


@register_event("decision.connected")
class ConnectedPayload(BasePayload):
    """
    Decider 连接成功事件 Payload

    事件名：CoreEvents.DECISION_CONNECTED
    发布者：Decider
    订阅者：任何需要监控 Decider 状态的组件

    表示 Decider 已成功连接到外部服务（如 MaiBot）。
    """

    name: str = Field(..., description="参与者名称")
    endpoint: Optional[str] = Field(default=None, description="连接端点")
    timestamp_ms: int = Field(
        default_factory=lambda: int(time.time() * 1000),
        alias="timestamp",
        description="连接时间（Unix 毫秒）",
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "name": "maicore",
                "endpoint": "ws://localhost:8000/ws",
                "timestamp_ms": 1706745600000,
                "metadata": {"reconnect_count": 0},
            }
        },
    )

    def __str__(self) -> str:
        """自定义字符串表示，只显示关键字段"""
        return f'ConnectedPayload(name="{self.name}", endpoint="{self.endpoint}")'


@register_event("decision.disconnected")
class DisconnectedPayload(BasePayload):
    """
    Decider 断开连接事件 Payload

    事件名：CoreEvents.DISCONNECTED
    发布者：Decider
    订阅者：任何需要监控 Decider 状态的组件

    表示 Decider 与外部服务断开连接。
    """

    name: str = Field(..., description="参与者名称")
    reason: str = Field(default="unknown", description="断开原因")
    will_retry: bool = Field(default=False, description="是否将重试连接")
    timestamp_ms: int = Field(
        default_factory=lambda: int(time.time() * 1000),
        alias="timestamp",
        description="断开时间（Unix 毫秒）",
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "name": "maicore",
                "reason": "connection_lost",
                "will_retry": True,
                "timestamp_ms": 1706745600000,
                "metadata": {"reconnect_attempt": 1},
            }
        },
    )

    def __str__(self) -> str:
        """自定义字符串表示，只显示关键字段"""
        return f'DisconnectedPayload(name="{self.name}", reason="{self.reason}", will_retry={self.will_retry})'


__all__ = [
    "IntentActionPayload",
    "IntentPayload",
    "ConnectedPayload",
    "DisconnectedPayload",
]
