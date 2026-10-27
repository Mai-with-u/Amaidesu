"""
事件注册表

提供两套互补的注册 API：

1. **装饰器 API（推荐）**：通过 ``@register_event("event.name")`` 把 Pydantic Payload
   类注册到模块级 ``EVENT_REGISTRY`` 字典。

   使用方式::

       from src.modules.events.registry import register_event
       from pydantic import BaseModel


       @register_event("room.message.danmaku")
       class RoomMessagePayload(BaseModel): ...

   装饰器是**幂等**的：不引入 import，不会因为 ``register_core_events()`` 未调用
   而失效。Payload 模块一旦被 import（任意路径），装饰器即生效。

调用方：

- ``register_core_events()`` 仅用于在应用启动时触发 Payload 子模块的 import，
  以保证 ``@register_event`` 装饰器被执行；其本身不再维护任何手写映射。
"""

from typing import Callable, Dict, FrozenSet, Optional, Type, TypeVar

from pydantic import BaseModel

from src.modules.events.names import CoreEvents

T = TypeVar("T", bound=Type[BaseModel])


# ==================== 模块级注册表 ====================

# 由 @register_event 装饰器填充。
# Key 是事件名（如 "room.message.danmaku"），Value 是 Pydantic Model 类型。
EVENT_REGISTRY: Dict[str, Type[BaseModel]] = {}

# 动态事件族登记表：族前缀 → payload 类型。
# 族内具体事件名（如 "tool.result.<工具名>"）由发布方在 emit 时即时构造，
# 不进 EVENT_REGISTRY；登记条目供一致性检查（族成员不算多余注册）与文档使用。
DYNAMIC_EVENT_FAMILIES: Dict[str, Type[BaseModel]] = {}


def register_event_family(prefix: str, model_class: Type[BaseModel]) -> None:
    """
    登记一个动态事件族（族前缀 + payload 类型）

    动态族的事件名在 emit 时由发布方拼接（如 ``tool.result.<name>``），
    无法用 ``@register_event`` 逐一注册；此处登记"前缀 → payload 类型"
    供启动一致性检查与文档检索。

    Args:
        prefix: 族前缀（以点号结尾，如 ``"tool.result."``）
        model_class: 该族事件统一使用的 payload 类型
    """
    if not prefix.endswith("."):
        raise ValueError(f"动态族前缀必须以点号结尾: '{prefix}'")
    DYNAMIC_EVENT_FAMILIES[prefix] = model_class


# ==================== 装饰器 API ====================


def register_event(event_name: str) -> Callable[[T], T]:
    """
    注册事件 Payload 类型的装饰器

    使用 ``@register_event("event.name")`` 把 Pydantic BaseModel 子类注册为
    对应事件的 Payload 类型，并在 ``cls._all_registered_names`` 集合上记录
    该类注册过的全部事件名（含多次装饰的累加）。

    Args:
        event_name: 事件名称（如 ``"room.message.danmaku"``）。必须是
            :class:`CoreEvents` 已登记的常量值——事件名以 ``names.py`` 为
            唯一事实源，拼写漂移在此处直接暴露。``"test."`` 前缀保留给
            测试合成事件名，豁免校验。

    Returns:
        装饰器函数。被装饰的类原样返回。

    Raises:
        ValueError: ``event_name`` 未在 :class:`CoreEvents` 登记，
            或已被注册为**不同的**类型。

    Example:
        ::

            @register_event(CoreEvents.ROOM_MESSAGE_DANMAKU)
            class RoomMessagePayload(BaseModel): ...


            assert "room.message.danmaku" in RoomMessagePayload._all_registered_names


            @register_event(CoreEvents.LIVE_STARTED)
            @register_event(CoreEvents.LIVE_ENDED)
            class LivePayload(BasePayload): ...
    """

    def decorator(cls: T) -> T:
        if event_name not in CoreEvents.ALL_EVENTS and not event_name.startswith("test."):
            raise ValueError(
                f"事件名 '{event_name}' 未在 CoreEvents（names.py）登记；"
                "事件名以 CoreEvents 常量为唯一事实源，请先在 names.py 增补常量"
            )
        existing = EVENT_REGISTRY.get(event_name)
        if existing is not None and existing is not cls:
            raise ValueError(f"事件 '{event_name}' 已被注册为 {existing.__name__}，不能再次注册为 {cls.__name__}")
        EVENT_REGISTRY[event_name] = cls
        # 记录该类注册过的全部事件名（同名重复注册幂等合并）
        existing_names: FrozenSet[str] = getattr(cls, "_all_registered_names", frozenset())
        cls._all_registered_names = existing_names | {event_name}  # type: ignore[attr-defined]
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


# ==================== 启动钩子 ====================


def register_core_events() -> None:
    """
    触发全部 Payload 模块导入以执行 ``@register_event`` 装饰器

    Payload 模块全集的唯一事实源是 ``payloads/__init__.py``：它顶层全量
    import 每个子模块，而 ``from 包 import 子模块`` 必先完整执行包的
    ``__init__``，故 import 该包任意入口即触发全部 ``@register_event``
    装饰器——本函数只做这一件事，不维护手写模块清单。同时登记动态
    事件族（``tool.result.*`` / ``tool.health.*``）。
    """
    # 仅为触发模块级 @register_event 执行；F401：导入本身即目的
    import src.modules.events.payloads as _payloads  # noqa: F401

    # 动态事件族登记（函数内 import 规避循环：payloads 子模块依赖本模块的
    # register_event 装饰器）
    from src.modules.events.payloads.tool_health import ToolHealthPayload
    from src.modules.events.payloads.tool_result import ToolResultPayload

    register_event_family("tool.result.", ToolResultPayload)
    register_event_family("tool.health.", ToolHealthPayload)


def _named_core_events() -> FrozenSet[str]:
    """
    ``CoreEvents`` 定义的具名事件集合（剔除 ``*`` / ``#`` 通配占位符）

    通配占位符（如 ``tool.result.#``）是订阅模式标识，不是被 emit 的具体
    事件名，不参与注册一致性比对。
    """
    return frozenset(name for name in CoreEvents.get_all_events() if "*" not in name and "#" not in name)


def ensure_registry_consistency() -> None:
    """
    启动硬检查：已注册事件集合必须与 ``CoreEvents`` 定义完全一致

    比对三方集合：
    - 期望：``CoreEvents`` 定义的具名事件
    - 已注册：``EVENT_REGISTRY``（由 ``@register_event`` 装饰器填充）
    - 动态族：``DYNAMIC_EVENT_FAMILIES`` 前缀下的注册名不视为多余

    缺失（定义了事件但没注册 payload）或多余（注册了 CoreEvents 未定义的
    事件）都视为契约漂移，直接抛错阻止启动。

    Raises:
        RuntimeError: 集合不一致，消息中列出全部缺失与多余事件名
    """
    expected = _named_core_events()
    registered = set(EVENT_REGISTRY.keys())

    missing = sorted(expected - registered)
    extra = sorted(
        name for name in registered - expected if not any(name.startswith(prefix) for prefix in DYNAMIC_EVENT_FAMILIES)
    )

    if missing or extra:
        lines = ["事件注册表与 CoreEvents 定义不一致:"]
        if missing:
            lines.append(f"  缺失注册: {', '.join(missing)}")
        if extra:
            lines.append(f"  多余注册: {', '.join(extra)}")
        raise RuntimeError("\n".join(lines))
