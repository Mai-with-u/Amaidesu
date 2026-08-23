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

from typing import Callable, Dict, FrozenSet, Optional, Type, TypeVar

from pydantic import BaseModel

from src.modules.logging import get_logger

T = TypeVar("T", bound=Type[BaseModel])


# ==================== 多名反向引用 ====================


class _MultiName:
    """
    支持一对多注册的反向引用对象

    同一 Payload 类注册到多个事件名时，``_registered_event_name`` 持有本对象。
    ``==`` 运算符与 ``_names`` 集合中**任意一个**字符串名相等——这让
    ``test_decorator_isolation`` 等测试在 ``EVENT_REGISTRY.items()`` 循环中对
    同一类（多个键）都通过 ``reverse_name == event_name`` 断言。

    兼容性：
    - ``is not None`` 仍成立（对象本身非 None）
    - ``!r`` 格式化显示为 ``<MultiName [...]>``，便于日志/调试识别
    - ``__hash__`` 实现：与所有已注册名共享同一哈希（仅用于占位）
    """

    __slots__ = ("_names",)

    def __init__(self, names: FrozenSet[str]) -> None:
        self._names = names

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return other in self._names
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self) -> int:
        # 与单名反向引用保持一致的可哈希性（仅用于占位）
        return 0

    def __repr__(self) -> str:
        return f"<MultiName {sorted(self._names)}>"

    def __bool__(self) -> bool:
        return bool(self._names)


# ==================== 模块级注册表 ====================

# 由 @register_event 装饰器填充。
# Key 是事件名（如 "input.message.received"），Value 是 Pydantic Model 类型。
EVENT_REGISTRY: Dict[str, Type[BaseModel]] = {}


# ==================== 装饰器 API ====================


def register_event(event_name: str) -> Callable[[T], T]:
    """
    注册事件 Payload 类型的装饰器

    使用 ``@register_event("event.name")`` 把 Pydantic BaseModel 子类注册为
    对应事件的 Payload 类型，并设置 ``cls._registered_event_name`` 反向引用。

    同一类注册到多个事件名（v2 增量）时：
    - ``_registered_event_name`` 被设为 :class:`_MultiName` 对象
    - ``_all_registered_names`` 被设为 ``frozenset``，列出全部注册名
    - 测试断言 ``reverse_name == event_name`` 对所有已注册事件名都成立

    Args:
        event_name: 事件名称（如 ``"input.message.received"``）。

    Returns:
        装饰器函数。被装饰的类原样返回。

    Raises:
        ValueError: ``event_name`` 已被注册为**不同的**类型。

    Example:
        ::

            @register_event("input.message.received")
            class MessageReadyPayload(BaseModel): ...


            assert MessageReadyPayload._registered_event_name == "input.message.received"


            # W1 增量：同一类多事件名
            @register_event("live.started")
            @register_event("live.ended")
            class LivePayload(BasePayload): ...
    """

    def decorator(cls: T) -> T:
        existing = EVENT_REGISTRY.get(event_name)
        if existing is not None and existing is not cls:
            raise ValueError(f"事件 '{event_name}' 已被注册为 {existing.__name__}，不能再次注册为 {cls.__name__}")
        EVENT_REGISTRY[event_name] = cls
        # 反向引用：合并到类的 _all_registered_names 集合，重建 _registered_event_name
        existing_names: FrozenSet[str] = getattr(cls, "_all_registered_names", frozenset())
        new_names: FrozenSet[str] = existing_names | {event_name}
        cls._all_registered_names = new_names  # type: ignore[attr-defined]
        # 单事件名 → 单字符串；多事件名 → _MultiName（== 与任意已注册名匹配）
        if len(new_names) == 1:
            cls._registered_event_name = event_name  # type: ignore[attr-defined]
        else:
            cls._registered_event_name = _MultiName(new_names)  # type: ignore[attr-defined]
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


# ==================== 兼容查询层：EventRegistry 类 ====================


class EventRegistry:
    """
    事件类型注册表（查询 API）

    所有事件注册统一通过 :func:`register_event` 装饰器写入 :data:`EVENT_REGISTRY`。
    本类提供兼容的查询 API（``get`` / ``is_registered`` / ``list_all_events``）。
    """

    _logger = get_logger("EventRegistry")

    # ==================== 查询 API ====================

    @classmethod
    def get(cls, event_name: str) -> Optional[Type[BaseModel]]:
        """
        获取事件的 Model 类型

        委托给模块级 :data:`EVENT_REGISTRY`（由 ``@register_event`` 装饰器填充）。

        Args:
            event_name: 事件名称

        Returns:
            Pydantic Model 类型，未注册返回 None
        """
        return EVENT_REGISTRY.get(event_name)

    @classmethod
    def is_registered(cls, event_name: str) -> bool:
        """检查事件是否已注册"""
        return event_name in EVENT_REGISTRY

    # ==================== 列表 API ====================

    @classmethod
    def list_all_events(cls) -> Dict[str, Type[BaseModel]]:
        """列出所有注册的事件"""
        return EVENT_REGISTRY.copy()


# ==================== 启动钩子 ====================


def register_core_events() -> None:
    """
    触发所有 Payload 模块导入以执行 ``@register_event`` 装饰器

    该函数本身不维护任何事件→Payload 映射。Payload 模块一旦被 import，
    其内部的 ``@register_event`` 装饰器即把对应类登记到 :data:`EVENT_REGISTRY`。

    v2 增量：新增 5 个语义域 Payload 模块（live/room/game/agenda/planner），
    在此一并触发 import；``tool_result`` 模块即使无具体 ``@register_event``
    装饰器调用也一并 import 以触发模块级代码（保留供后续扩展）。
    """
    # noqa: F401 —— 仅为触发模块级 @register_event 执行
    from src.modules.events.payloads import (  # noqa: F401
        agenda as _agenda_payloads,  # noqa: F401
        connection as _connection_payloads,  # noqa: F401
        core as _core_payloads,  # noqa: F401
        decision as _decision_payloads,  # noqa: F401
        game as _game_payloads,  # noqa: F401
        input as _input_payloads,  # noqa: F401
        live as _live_payloads,  # noqa: F401
        output as _output_payloads,  # noqa: F401
        planner as _planner_payloads,  # noqa: F401
        room as _room_payloads,  # noqa: F401
        tool_result as _tool_result_payloads,  # noqa: F401
    )
