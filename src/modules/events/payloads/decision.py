"""
Decision 阶段 事件 Payload 定义

定义 Decision 阶段 相关的事件 Payload 类型。
- IntentPayload: 意图生成事件(承载新结构化 Intent)
- IntentActionPayload: 意图动作 Payload(用于 decision.intent.action 事件)
- ConnectedPayload: 组件连接事件
- DisconnectedPayload: 组件断开事件
"""

from typing import TYPE_CHECKING, Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.types import Intent


@register_event("decision.intent.action")
class IntentActionPayload(BasePayload):
    """
    意图动作 Payload

    表示 Intent 中的单个动作(用于 decision.intent.action 事件,
    即 LLM 风格的"动作-参数-优先级"输出)。
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
    - Intent 数据存储为字典,通过 `from_intent` / `to_intent` 与 Intent 对象互转
    - 旧版 `__getattr__` 代理白名单(context/parser_type/original_text/response_text/actions/timestamp)已删除:
        调用方应直接访问 `intent_data` 或 `to_intent()` 后访问结构化字段
    """

    intent_data: Dict[str, Any] = Field(..., description="Intent 序列化数据")
    name: str = Field(..., description="决策Decider名称")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "intent_data": {
                    "speech": "你好！很高兴见到你~",
                    "emotion": {"name": "happy", "intensity": 0.5},
                    "action": {"name": "warudo.wave", "parameters": {"duration_ms": 1500}},
                    "metadata": {
                        "source_id": "msg_123",
                        "decision_time_ms": 1706745600000,
                    },
                },
                "name": "maibot",
            }
        }
    )

    def __str__(self) -> str:
        """自定义字符串表示,显示 intent_data 中的关键字段"""
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
        从 Intent 对象创建 Payload（纯工厂，无副作用）

        使用 Pydantic 的自动序列化,将 Intent 转换为字典存储。

        注意：意图摘要日志由 Decision → Output 的单一汇聚点
        `OutputHandlerManager._on_decision_intent` 统一打印（每个被发布的意图
        仅经过该处一次），本工厂不承担日志职责，避免被 Output 转发等场景重复打印。

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

        使用 Pydantic 的自动反序列化,从字典还原 Intent。

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
        default_factory=lambda: now_ms(),
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

    事件名：CoreEvents.DECISION_DISCONNECTED
    发布者：Decider
    订阅者：任何需要监控 Decider 状态的组件

    表示 Decider 与外部服务断开连接。
    """

    name: str = Field(..., description="参与者名称")
    reason: str = Field(default="unknown", description="断开原因")
    will_retry: bool = Field(default=False, description="是否将重试连接")
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
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
