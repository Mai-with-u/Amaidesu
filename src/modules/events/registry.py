"""
事件注册表

支持核心事件注册和验证。
"""

from typing import Dict, Optional, Type

from pydantic import BaseModel

from src.modules.logging import get_logger


class EventRegistry:
    """
    事件类型注册表

    支持核心事件的注册和验证。
    """

    # 核心事件（只读）
    _core_events: Dict[str, Type[BaseModel]] = {}

    _logger = get_logger("EventRegistry")

    # ==================== 核心事件 API ====================

    @classmethod
    def register_core_event(cls, event_name: str, model: Type[BaseModel]) -> None:
        """
        注册核心事件

        Args:
            event_name: 事件名称（如 "perception.raw_data.generated"）
            model: Pydantic Model 类型

        Raises:
            ValueError: 事件名不符合核心事件命名规范
        """
        # 验证命名规范
        valid_prefixes = (
            "data.",
            "perception.",
            "normalization.",
            "decision.",
            "output.",
            "expression.",
            "render.",
            "core.",
        )
        if not any(event_name.startswith(prefix) for prefix in valid_prefixes):
            raise ValueError(f"核心事件名必须以 {valid_prefixes} 之一开头，收到: {event_name}")

        if event_name in cls._core_events:
            # 只有当类型不同时才发出警告，相同类型不警告（这是安全的重复注册）
            existing_model = cls._core_events[event_name]
            if existing_model != model:
                cls._logger.warning(
                    f"核心事件已存在，将覆盖: {event_name} (旧: {existing_model.__name__}, 新: {model.__name__})"
                )
            else:
                cls._logger.debug(f"核心事件已存在（类型相同，跳过）: {event_name}")

        cls._core_events[event_name] = model
        cls._logger.debug(f"注册核心事件: {event_name} -> {model.__name__}")

    @classmethod
    def unregister_core_event(cls, event_name: str) -> bool:
        """
        移除核心事件注册

        Args:
            event_name: 事件名称

        Returns:
            是否成功移除（False 表示事件未注册）
        """
        if event_name in cls._core_events:
            del cls._core_events[event_name]
            cls._logger.debug(f"移除核心事件: {event_name}")
            return True
        cls._logger.debug(f"尝试移除未注册的事件: {event_name}")
        return False

    # ==================== 查询 API ====================

    @classmethod
    def get(cls, event_name: str) -> Optional[Type[BaseModel]]:
        """
        获取事件的 Model 类型

        Args:
            event_name: 事件名称

        Returns:
            Pydantic Model 类型，未注册返回 None
        """
        return cls._core_events.get(event_name)

    @classmethod
    def is_registered(cls, event_name: str) -> bool:
        """检查事件是否已注册"""
        return event_name in cls._core_events

    # ==================== 列表 API ====================

    @classmethod
    def list_all_events(cls) -> Dict[str, Type[BaseModel]]:
        """列出所有注册的事件"""
        return cls._core_events.copy()


def register_core_events() -> None:
    """注册所有核心事件名与 Payload 类型的映射，供 EventBus 校验使用。"""
    from src.modules.events.names import CoreEvents
    from src.modules.events.payloads import (
        DecisionRequestPayload,
        DecisionResponsePayload,
        IntentPayload,
        MessageReadyPayload,
        ProviderConnectedPayload,
        ProviderDisconnectedPayload,
        RenderCompletedPayload,
        RenderFailedPayload,
    )
    from src.modules.events.payloads.system import ErrorPayload, ShutdownPayload, StartupPayload

    # 核心事件注册
    EventRegistry.register_core_event(CoreEvents.DATA_MESSAGE, MessageReadyPayload)
    EventRegistry.register_core_event(CoreEvents.DECISION_REQUEST, DecisionRequestPayload)
    EventRegistry.register_core_event(CoreEvents.DECISION_INTENT, IntentPayload)
    EventRegistry.register_core_event(CoreEvents.DECISION_RESPONSE_GENERATED, DecisionResponsePayload)
    EventRegistry.register_core_event(CoreEvents.DECISION_PROVIDER_CONNECTED, ProviderConnectedPayload)
    EventRegistry.register_core_event(CoreEvents.DECISION_PROVIDER_DISCONNECTED, ProviderDisconnectedPayload)
    # Output Domain 事件
    EventRegistry.register_core_event(CoreEvents.OUTPUT_INTENT, IntentPayload)  # 过滤后的 Intent
    EventRegistry.register_core_event(CoreEvents.RENDER_COMPLETED, RenderCompletedPayload)
    EventRegistry.register_core_event(CoreEvents.RENDER_FAILED, RenderFailedPayload)
    EventRegistry.register_core_event(CoreEvents.CORE_STARTUP, StartupPayload)
    EventRegistry.register_core_event(CoreEvents.CORE_SHUTDOWN, ShutdownPayload)
    EventRegistry.register_core_event(CoreEvents.CORE_ERROR, ErrorPayload)
