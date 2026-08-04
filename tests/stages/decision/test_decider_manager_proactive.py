"""DeciderManager.trigger_proactive 转发方法单元测试

验证：
- 只对实现了 trigger_proactive 的 Decider 发起调用
- 未实现该接口的 Decider 静默跳过（不抛错、不计入返回列表）
- 单个 Decider 抛异常时隔离，不影响其他 Decider
- 返回的列表仅包含成功调用的 Decider 名称
- topic_hint 按值透传（None / 字符串）
- 异常路径下日志记录但不向上抛出

这些测试直接构造 DeciderManager 实例（跳过完整的 setup() / start() 生命周期），
只关注 trigger_proactive 的纯转发与异常隔离行为。
"""

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.stages.decision.manager import DeciderManager


# ---------------------------------------------------------------------------
# 辅助 mock decider
# ---------------------------------------------------------------------------


class _RecordingDecider:
    """记录 trigger_proactive 调用参数的 mock decider"""

    def __init__(self) -> None:
        self.calls: List[Optional[str]] = []

    async def trigger_proactive(self, topic_hint: Optional[str] = None) -> None:
        self.calls.append(topic_hint)


class _RaisingDecider:
    """trigger_proactive 调用时抛异常的 mock decider"""

    def __init__(self, exc: Optional[Exception] = None) -> None:
        self.calls: int = 0
        self._exc = exc or RuntimeError("decider boom")

    async def trigger_proactive(self, topic_hint: Optional[str] = None) -> None:
        self.calls += 1
        raise self._exc


class _PlainDecider:
    """没有 trigger_proactive 方法的 mock decider，用于验证鸭子类型跳过"""

    async def decide(self, message: Any) -> None:  # noqa: D401 - test stub
        return None


def _make_manager_with(deciders: Dict[str, Any]) -> DeciderManager:
    """构造一个不调用 setup() 的 DeciderManager，直接注入 mock deciders"""
    manager = DeciderManager(event_bus=EventBus())
    manager._deciders = deciders  # noqa: SLF001 - 测试直接注入，绕过 setup()
    return manager


# ---------------------------------------------------------------------------
# 转发语义
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_proactive_calls_only_deciders_with_method() -> None:
    """只对实现了 trigger_proactive 的 Decider 发起调用；未实现的跳过"""
    a = _RecordingDecider()
    b = _PlainDecider()  # 没有 trigger_proactive
    manager = _make_manager_with({"a": a, "b": b})

    triggered = await manager.trigger_proactive("测试话题")

    assert a.calls == ["测试话题"]
    assert triggered == ["a"]


@pytest.mark.asyncio
async def test_trigger_proactive_passes_none_topic_hint() -> None:
    """topic_hint=None 时正确透传"""
    a = _RecordingDecider()
    manager = _make_manager_with({"a": a})

    triggered = await manager.trigger_proactive()

    assert a.calls == [None]
    assert triggered == ["a"]


@pytest.mark.asyncio
async def test_trigger_proactive_collects_all_supported_deciders() -> None:
    """多个 Decider 都实现 trigger_proactive 时，全部被调用并加入返回列表"""
    a = _RecordingDecider()
    b = _RecordingDecider()
    manager = _make_manager_with({"a": a, "b": b})

    triggered = await manager.trigger_proactive("hello")

    assert a.calls == ["hello"]
    assert b.calls == ["hello"]
    assert sorted(triggered) == ["a", "b"]


# ---------------------------------------------------------------------------
# 异常隔离
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_proactive_isolates_exception() -> None:
    """单个 Decider 抛异常不影响其他 Decider 的调用与返回"""
    a = _RaisingDecider()
    b = _RecordingDecider()
    c = _RecordingDecider()
    manager = _make_manager_with({"a": a, "b": b, "c": c})

    triggered = await manager.trigger_proactive(None)

    # 异常的 a 仍然被调用了一次
    assert a.calls == 1
    # 后续 b / c 正常被调用，未被 a 的异常中断
    assert b.calls == [None]
    assert c.calls == [None]
    # 返回列表只包含成功执行的 Decider
    assert sorted(triggered) == ["b", "c"]


@pytest.mark.asyncio
async def test_trigger_proactive_does_not_propagate_exception() -> None:
    """异常被内部捕获，不向上抛出"""
    a = _RaisingDecider()
    manager = _make_manager_with({"a": a})

    # 不应该抛异常
    triggered = await manager.trigger_proactive(None)

    assert triggered == []


# ---------------------------------------------------------------------------
# 边界情况
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_proactive_with_no_deciders_returns_empty_list() -> None:
    """无 Decider 时返回空列表（不抛错）"""
    manager = _make_manager_with({})

    triggered = await manager.trigger_proactive("test")

    assert triggered == []


@pytest.mark.asyncio
async def test_trigger_proactive_with_no_supported_deciders_returns_empty_list() -> None:
    """所有 Decider 都没实现 trigger_proactive 时返回空列表"""
    a = _PlainDecider()
    b = _PlainDecider()
    manager = _make_manager_with({"a": a, "b": b})

    triggered = await manager.trigger_proactive("test")

    assert triggered == []
