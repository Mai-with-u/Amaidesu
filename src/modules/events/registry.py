"""
事件注册表

提供两套互补的注册 API：

1. **装饰器 API（推荐）**：通过 ``@register_event("event.name")`` 把 Pydantic Payload
   类注册到模块级 ``EVENT_REGISTRY`` 字典。

   使用方式::

       from src.modules.events.registry import register_event
       from pydantic import BaseModel


       @register_event("input.message.received")
       class MessageReadyPayload(BaseModel): ...

   装饰器是**幂等**的：不引入 import，不会因为 ``register_core_events()`` 未调用
   而失效。Payload 模块一旦被 import（任意路径），装饰器即生效。

2. **类 API（兼容层）**：``EventRegistry.register_core_event(...)`` 等方法。
   保留用于已有调用方，新代码应优先使用装饰器。

调用方：

- ``register_core_events()`` 仅用于在应用启动时触发 Payload 子模块的 import，
  以保证 ``@register_event`` 装饰器被执行；其本身不再维护任何手写映射。
"""

from typing import Dict, Optional, Type

from pydantic import BaseModel

from src.modules.logging import get_logger


# ==================== 模块级注册表 ====================

# 由 @register_event 装饰器填充。
# Key 是事件名（如 "input.message.received"），Value 是 Pydantic Model 类型。
EVENT_REGISTRY: Dict[str, Type[BaseModel]] = {}


# ==================== 装饰器 API ====================


def register_event(event_name: str):
    """
    注册事件 Payload 类型的装饰器

    使用 ``@register_event("event.name")`` 把 Pydantic BaseModel 子类注册为
    对应事件的 Payload 类型，并设置 ``cls._registered_event_name`` 反向引用。

    Args:
        event_name: 事件名称（如 ``"input.message.received"``）。

    Returns:
        装饰器函数。被装饰的类原样返回。

    Raises:
        ValueError: ``event_name`` 已被注册为不同的类型。

    Example:
        ::

            @register_event("input.message.received")
            class MessageReadyPayload(BaseModel): ...


            assert MessageReadyPayload._registered_event_name == "input.message.received"
    """

    def decorator(cls: Type[BaseModel]) -> Type[BaseModel]:
        existing = EVENT_REGISTRY.get(event_name)
        if existing is not None and existing is not cls:
            raise ValueError(f"事件 '{event_name}' 已被注册为 {existing.__name__}，不能再次注册为 {cls.__name__}")
        EVENT_REGISTRY[event_name] = cls
        # 反向引用：通过实例/类即可查回事件名，便于日志和调试
        cls._registered_event_name = event_name  # type: ignore[attr-defined]
        return cls

    return decorator


def get_registered_event(name: str) -> Optional[Type[BaseModel]]:
    """
    通过事件名获取已注册的 Payload 类型

    Args:
        name: 事件名称。

    Returns:
        已注册的 Pydantic Model 类型；未注册返回 ``None``。
    """
    return EVENT_REGISTRY.get(name)


def list_registered_events() -> Dict[str, Type[BaseModel]]:
    """
    列出所有通过 ``@register_event`` 注册的事件

    Returns:
        事件名到 Payload 类型的映射字典（副本），修改不影响内部注册表。
    """
    return EVENT_REGISTRY.copy()


# ==================== 兼容层：EventRegistry 类 ====================


class EventRegistry:
    """
    事件类型注册表（兼容层）

    保留旧 API（``register_core_event`` / ``get`` / ``is_registered`` /
    ``list_all_events`` / ``unregister_core_event``）以兼容既有调用方。

    新代码应使用 :func:`register_event` 装饰器和 :data:`EVENT_REGISTRY` 字典。
    """

    # 核心事件（只读）：由旧 register_core_event 调用方写入
    _core_events: Dict[str, Type[BaseModel]] = {}

    _logger = get_logger("EventRegistry")

    # ==================== 核心事件 API ====================

    @classmethod
    def register_core_event(cls, event_name: str, model: Type[BaseModel]) -> None:
        """
        注册核心事件

        Args:
            event_name: 事件名称（如 "input.message.received"）
            model: Pydantic Model 类型

        Raises:
            ValueError: 事件名不符合核心事件命名规范
        """
        # 验证命名规范
        valid_prefixes = (
            "input.",
            "decision.",
            "output.",
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


# ==================== 启动钩子 ====================


def register_core_events() -> None:
    """
    触发所有 Payload 模块导入以执行 ``@register_event`` 装饰器

    该函数本身不维护任何事件→Payload 映射。Payload 模块一旦被 import，
    其内部的 ``@register_event`` 装饰器即把对应类登记到 :data:`EVENT_REGISTRY`。
    """
    # noqa: F401 —— 仅为触发模块级 @register_event 执行
    from src.modules.events.payloads import (  # noqa: F401
        input as _input_payloads,  # noqa: F401
        decision as _decision_payloads,  # noqa: F401
        output as _output_payloads,  # noqa: F401
        connection as _connection_payloads,  # noqa: F401
    )
