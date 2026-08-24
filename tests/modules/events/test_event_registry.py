"""
测试 EventRegistry（pytest）

运行: uv run pytest tests/modules/events/test_event_registry.py -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from pydantic import BaseModel

from src.modules.events.registry import EVENT_REGISTRY, EventRegistry

# =============================================================================
# 测试数据模型
# =============================================================================


class CoreEventData(BaseModel):
    """核心事件测试数据"""

    message: str
    timestamp: float


# =============================================================================
# Fixture
# =============================================================================


@pytest.fixture(autouse=True)
def reset_registry():
    """每个测试前后保存并恢复 EVENT_REGISTRY 状态"""
    saved = dict(EVENT_REGISTRY)
    EVENT_REGISTRY.clear()
    yield
    EVENT_REGISTRY.clear()
    EVENT_REGISTRY.update(saved)


# =============================================================================
# 注册和查询测试
# =============================================================================


def test_register_and_query():
    """测试注册事件并查询"""
    EVENT_REGISTRY["test.event.one"] = CoreEventData

    assert EventRegistry.is_registered("test.event.one")
    assert EventRegistry.get("test.event.one") == CoreEventData


def test_multiple_events():
    """测试注册多个事件"""
    events = {
        "test.event.a": CoreEventData,
        "test.event.b": CoreEventData,
        "test.event.c": CoreEventData,
    }
    for name, cls in events.items():
        EVENT_REGISTRY[name] = cls

    for name in events:
        assert EventRegistry.is_registered(name)

    all_events = EventRegistry.list_all_events()
    for name in events:
        assert name in all_events


def test_get_nonexistent():
    """测试获取不存在的事件"""
    assert EventRegistry.get("nonexistent.event") is None


def test_is_registered():
    """测试检查事件是否已注册"""
    EVENT_REGISTRY["test.registered"] = CoreEventData

    assert EventRegistry.is_registered("test.registered") is True
    assert EventRegistry.is_registered("test.unregistered") is False


# =============================================================================
# 事件列表测试
# =============================================================================


def test_list_all_events():
    """测试列出所有事件"""
    EVENT_REGISTRY["test.list.one"] = CoreEventData
    EVENT_REGISTRY["test.list.two"] = CoreEventData

    all_events = EventRegistry.list_all_events()

    assert len(all_events) == 2
    assert "test.list.one" in all_events
    assert "test.list.two" in all_events

    # 返回的是副本，修改不应影响原数据
    all_events["new.event"] = CoreEventData
    assert EventRegistry.is_registered("new.event") is False


# =============================================================================
# 边界情况测试
# =============================================================================


def test_empty_registry():
    """测试空注册表的行为（清除装饰器注册后）"""
    # 此时 EVENT_REGISTRY 为空（reset_registry 已清空）
    assert EventRegistry.is_registered("any.event") is False
    assert EventRegistry.get("any.event") is None
    assert len(EventRegistry.list_all_events()) == 0


def test_mixed_operations():
    """测试混合操作（注册、查询、清空）"""
    EVENT_REGISTRY["test.mixed.one"] = CoreEventData
    EVENT_REGISTRY["test.mixed.two"] = CoreEventData

    assert len(EventRegistry.list_all_events()) == 2

    EVENT_REGISTRY.clear()
    assert len(EventRegistry.list_all_events()) == 0


def test_event_name_with_dots():
    """测试包含多个点的事件名"""
    EVENT_REGISTRY["room.message.danmaku"] = CoreEventData
    assert EventRegistry.is_registered("room.message.danmaku")

    EVENT_REGISTRY["core.test.nested.event"] = CoreEventData
    assert EventRegistry.is_registered("core.test.nested.event")


def test_list_all_events_returns_copy():
    """测试 list_all_events() 返回的是副本"""
    EVENT_REGISTRY["test.copy"] = CoreEventData

    events = EventRegistry.list_all_events()
    events["__test_dummy__"] = type  # type: ignore[assignment]

    assert EventRegistry.is_registered("__test_dummy__") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
