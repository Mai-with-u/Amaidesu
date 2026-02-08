"""
测试 EventRegistry（pytest）

运行: uv run pytest tests/core/events/test_event_registry.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from pydantic import BaseModel

from src.core.events.registry import EventRegistry


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
    """每个测试前后重置 EventRegistry 状态"""
    # 测试前清理
    EventRegistry._core_events.clear()

    yield

    # 测试后清理
    EventRegistry._core_events.clear()


# =============================================================================
# 核心事件注册测试
# =============================================================================


def test_register_core_event_valid():
    """测试注册有效的核心事件"""
    EventRegistry.register_core_event("perception.raw_data.generated", CoreEventData)

    assert EventRegistry.is_registered("perception.raw_data.generated")
    assert EventRegistry.get("perception.raw_data.generated") == CoreEventData


def test_register_core_event_multiple_valid_prefixes():
    """测试注册所有有效前缀的核心事件"""
    valid_events = [
        ("perception.raw_data.generated", CoreEventData),
        ("normalization.message_ready", CoreEventData),
        ("decision.intent_generated", CoreEventData),
        ("expression.parameters_generated", CoreEventData),
        ("render.frame_ready", CoreEventData),
        ("core.system_started", CoreEventData),
    ]

    for event_name, model in valid_events:
        EventRegistry.register_core_event(event_name, model)

    # 验证所有事件都已注册
    for event_name, _ in valid_events:
        assert EventRegistry.is_registered(event_name)


def test_register_core_event_invalid_prefix():
    """测试注册无效前缀的核心事件（应失败）"""
    with pytest.raises(ValueError, match="核心事件名必须以"):
        EventRegistry.register_core_event("invalid.event.name", CoreEventData)

    with pytest.raises(ValueError, match="核心事件名必须以"):
        EventRegistry.register_core_event("plugin.test.event", CoreEventData)

    with pytest.raises(ValueError, match="核心事件名必须以"):
        EventRegistry.register_core_event("custom.event", CoreEventData)

    with pytest.raises(ValueError, match="核心事件名必须以"):
        EventRegistry.register_core_event("understanding.intent_generated", CoreEventData)


def test_register_core_event_duplicate():
    """测试重复注册核心事件（应覆盖并警告）"""
    EventRegistry.register_core_event("perception.test_event", CoreEventData)

    # 重复注册应该覆盖（记录警告但不抛出异常）
    EventRegistry.register_core_event("perception.test_event", CoreEventData)

    # 验证仍然可以获取
    assert EventRegistry.get("perception.test_event") == CoreEventData


# =============================================================================
# 事件查询测试
# =============================================================================


def test_get_event():
    """测试获取已注册的事件"""
    EventRegistry.register_core_event("core.event1", CoreEventData)

    # 获取核心事件
    assert EventRegistry.get("core.event1") == CoreEventData

    # 获取不存在的事件
    assert EventRegistry.get("nonexistent.event") is None


def test_is_registered():
    """测试检查事件是否已注册"""
    EventRegistry.register_core_event("core.registered", CoreEventData)

    # 已注册的核心事件
    assert EventRegistry.is_registered("core.registered") is True

    # 未注册的事件
    assert EventRegistry.is_registered("core.unregistered") is False


# =============================================================================
# 事件列表测试
# =============================================================================


def test_list_all_events():
    """测试列出所有事件"""
    EventRegistry.register_core_event("core.event1", CoreEventData)
    EventRegistry.register_core_event("core.event2", CoreEventData)

    all_events = EventRegistry.list_all_events()

    # 应该包含所有事件
    assert len(all_events) == 2
    assert "core.event1" in all_events
    assert "core.event2" in all_events

    # 返回的是副本，修改不应影响原数据
    all_events["new.event"] = CoreEventData
    assert EventRegistry.is_registered("new.event") is False


# =============================================================================
# 边界情况测试
# =============================================================================


def test_empty_registry():
    """测试空注册表的行为"""
    # 所有查询方法在空注册表上应该正常工作
    assert EventRegistry.is_registered("any.event") is False
    assert EventRegistry.get("any.event") is None
    assert len(EventRegistry.list_all_events()) == 0


def test_mixed_operations():
    """测试混合操作（注册、查询）"""
    # 初始状态：注册一些事件
    EventRegistry.register_core_event("core.event1", CoreEventData)
    EventRegistry.register_core_event("core.event2", CoreEventData)

    assert len(EventRegistry.list_all_events()) == 2

    # 清空
    EventRegistry._core_events.clear()
    assert len(EventRegistry.list_all_events()) == 0


def test_event_name_with_dots():
    """测试包含多个点的事件名"""
    # 核心事件
    EventRegistry.register_core_event("perception.raw_data.generated", CoreEventData)
    assert EventRegistry.is_registered("perception.raw_data.generated")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
