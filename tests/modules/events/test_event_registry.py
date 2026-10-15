"""事件注册表：@register_event 装饰器契约、启动钩子幂等、与 CoreEvents 的一致性硬检查、上舰 payload"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from src.modules.events import list_registered_events, register_core_events, register_event
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import game_chat as _game_chat  # noqa: F401
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.events.payloads.tool_result import ToolResultPayload
from src.modules.events.registry import (
    DYNAMIC_EVENT_FAMILIES,
    EVENT_REGISTRY,
    EventRegistry,
    ensure_registry_consistency,
    get_registered_event,
)


class _Data(BaseModel):
    value: int = 0


@pytest.fixture(autouse=True)
def populated_registry():
    """确保注册表已填充，测试内的临时增删在结束后恢复进入时快照。

    game_chat 载荷由 Agent 包导入时才登记，register_core_events 不覆盖它，这里显式导入以便单独跑本文件。
    """
    register_core_events()
    saved = dict(EVENT_REGISTRY)
    yield
    EVENT_REGISTRY.clear()
    EVENT_REGISTRY.update(saved)


def test_registry_queries_and_copies() -> None:
    EVENT_REGISTRY["test.registry.one"] = _Data
    assert EventRegistry.is_registered("test.registry.one") and EventRegistry.get("test.registry.one") is _Data
    assert EventRegistry.get("nonexistent.event") is None and not EventRegistry.is_registered("test.unregistered")

    # 两个公开列表接口返回的都是副本，改动不回写注册表
    for events in (EventRegistry.list_all_events(), list_registered_events()):
        assert "test.registry.one" in events
        events["__dummy__"] = type  # type: ignore[assignment]
        assert not EventRegistry.is_registered("__dummy__")

    EVENT_REGISTRY.clear()
    assert EventRegistry.list_all_events() == {} and not EventRegistry.is_registered("room.message.danmaku")


def test_decorator_registers_sets_reverse_reference_and_returns_class_unchanged() -> None:
    @register_event("test.isolation.registers")
    class _Payload(BaseModel):
        value: int = 42

    assert EVENT_REGISTRY["test.isolation.registers"] is _Payload
    assert _Payload._registered_event_name == "test.isolation.registers"
    assert _Payload().value == 42  # 原样返回，不包装

    assert register_event("test.isolation.registers")(_Payload) is _Payload  # 同类重复注册幂等

    with pytest.raises(ValueError, match="已被注册"):  # 不同类占同一事件名
        register_event("test.isolation.registers")(type("_Other", (BaseModel,), {}))


def test_decorator_rejects_names_missing_from_core_events_except_test_prefix() -> None:
    with pytest.raises(ValueError, match="未在 CoreEvents"):  # 拼写漂移在注册期暴露

        @register_event("typo.room.mesage")
        class _Typo(BaseModel):
            pass

    @register_event("test.isolation.exempt")  # test. 前缀保留给测试合成事件名
    class _Synthetic(BaseModel):
        pass

    assert get_registered_event("test.isolation.exempt") is _Synthetic


def test_registered_payloads_are_wellformed_and_startup_hook_is_idempotent() -> None:
    snapshot = dict(EVENT_REGISTRY)
    assert snapshot and register_core_events() is None
    for _ in range(3):
        register_core_events()
    assert EVENT_REGISTRY == snapshot  # 多次调用既不增删也不替换类

    for name, cls in EVENT_REGISTRY.items():
        assert "." in name and not name.startswith("plugin."), name
        assert isinstance(cls, type) and issubclass(cls, BaseModel), name
        assert cls._registered_event_name == name, name  # 反向引用与注册键一致


def test_registry_consistent_with_core_events_and_reports_drift_by_name() -> None:
    ensure_registry_consistency()
    expected = {n for n in CoreEvents.get_all_events() if "*" not in n and "#" not in n}
    assert set(EVENT_REGISTRY) == expected
    assert {"tool.result.", "tool.health."} <= set(DYNAMIC_EVENT_FAMILIES)

    EVENT_REGISTRY["tool.result.some_tool"] = ToolResultPayload  # 动态族前缀下的名字不算多余
    ensure_registry_consistency()

    EVENT_REGISTRY["rogue.event.name"] = _Data
    with pytest.raises(RuntimeError, match="rogue.event.name"):
        ensure_registry_consistency()
    del EVENT_REGISTRY["rogue.event.name"]

    del EVENT_REGISTRY["game.milestone"]
    with pytest.raises(RuntimeError, match="game.milestone"):
        ensure_registry_consistency()


def test_guard_event_payload_type_literal() -> None:
    assert get_registered_event("room.message.guard") is RoomMessagePayload
    user = RoomMessageUser(id="u1", name="舰长君")
    assert RoomMessagePayload(message_type="guard", user=user, content="开通了舰长").message_type == "guard"
    with pytest.raises(ValidationError):
        RoomMessagePayload(message_type="guard_x", user=user)
