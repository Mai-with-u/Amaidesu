"""DeciderManager.outline_transitions 鸭子类型转发测试（T12）

覆盖：
- 只对实现了 outline_transitions 的 Decider 发起调用
- 未实现时返回 501 风格标记
- 异常隔离：单个 Decider 抛异常不影响后续
- 透传首个成功返回 dict 的 Decider 结果
- 多个 Decider 实现时,按 dict.items() 顺序取首个有效结果
"""

from __future__ import annotations

import src.modules.config.schemas  # noqa: F401  # isort:skip

from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.stages.decision.manager import DeciderManager


# ---------------------------------------------------------------------------
# 辅助 mock decider
# ---------------------------------------------------------------------------


class _OutlineTransitionsDecider:
    """正常实现 outline_transitions 的 mock decider"""

    def __init__(self, transitions: list[dict]) -> None:
        self._transitions = transitions
        self.calls: int = 0

    async def outline_transitions(self) -> Dict[str, Any]:
        self.calls += 1
        return {"loaded": True, "transitions": self._transitions}


class _RaisingOutlineTransitionsDecider:
    """outline_transitions 调用时抛异常的 mock decider"""

    def __init__(self) -> None:
        self.calls: int = 0

    async def outline_transitions(self) -> Dict[str, Any]:
        self.calls += 1
        raise RuntimeError("decider boom")


class _PlainDecider:
    """没实现 outline_transitions 的 mock decider（用于验证鸭子类型跳过）"""

    async def decide(self, message: Any) -> None:  # noqa: D401 - test stub
        return None


class _NotImplementedDecider:
    """实现 outline_transitions 但返回 not_implemented 标记"""

    def __init__(self) -> None:
        self.calls: int = 0

    async def outline_transitions(self) -> Dict[str, Any]:
        self.calls += 1
        return {
            "error": "not_implemented",
            "status_code": 501,
            "method": "outline_transitions",
            "message": "本 Decider 不支持",
        }


def _make_manager_with(deciders: Dict[str, Any]) -> DeciderManager:
    manager = DeciderManager(event_bus=EventBus())
    manager._deciders = deciders  # noqa: SLF001 - 测试直接注入
    return manager


# ---------------------------------------------------------------------------
# 正常转发
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outline_transitions_forwards_to_first_implementer() -> None:
    """实现 outline_transitions 的 Decider 被调用,返回 dict 透传"""
    transitions = [
        {"segment_id": "a", "title": "A", "reason": "start", "at_ms": 1000, "stayed_ms": None},
        {"segment_id": "b", "title": "B", "reason": "manual:skip", "at_ms": 2000, "stayed_ms": 1000},
    ]
    decider = _OutlineTransitionsDecider(transitions)
    manager = _make_manager_with({"amaidesu": decider})

    result = await manager.outline_transitions()

    assert decider.calls == 1
    assert result == {"loaded": True, "transitions": transitions}


@pytest.mark.asyncio
async def test_outline_transitions_skips_deciders_without_method() -> None:
    """未实现 outline_transitions 的 Decider 被跳过"""
    a = _OutlineTransitionsDecider([])
    b = _PlainDecider()
    manager = _make_manager_with({"a": a, "b": b})

    result = await manager.outline_transitions()

    assert a.calls == 1
    assert result == {"loaded": True, "transitions": []}


# ---------------------------------------------------------------------------
# not_implemented 跳过
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outline_transitions_skips_not_implemented_decider() -> None:
    """实现 outline_transitions 但返回 not_implemented → 跳过该 Decider,
    无更多 Decider 时返回 501 标记"""
    ni = _NotImplementedDecider()
    manager = _make_manager_with({"amaidesu": ni})

    result = await manager.outline_transitions()

    assert ni.calls == 1
    assert result["error"] == "not_implemented"
    assert result["status_code"] == 501
    assert result["method"] == "outline_transitions"


# ---------------------------------------------------------------------------
# 异常隔离
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outline_transitions_isolates_exception() -> None:
    """单个 Decider 抛异常不影响其他 Decider"""
    raising = _RaisingOutlineTransitionsDecider()
    healthy = _OutlineTransitionsDecider([])
    manager = _make_manager_with({"raising": raising, "healthy": healthy})

    result = await manager.outline_transitions()

    assert raising.calls == 1
    # healthy 仍被调用, 结果被返回
    assert result == {"loaded": True, "transitions": []}


@pytest.mark.asyncio
async def test_outline_transitions_returns_501_when_all_deciders_fail() -> None:
    """所有 Decider 都失败时返回 501 标记"""
    raising = _RaisingOutlineTransitionsDecider()
    manager = _make_manager_with({"raising": raising})

    result = await manager.outline_transitions()

    assert raising.calls == 1
    assert result["error"] == "not_implemented"
    assert result["status_code"] == 501


# ---------------------------------------------------------------------------
# 无 Decider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outline_transitions_with_no_deciders_returns_501() -> None:
    """无 Decider 时返回 501 标记"""
    manager = _make_manager_with({})

    result = await manager.outline_transitions()

    assert result["error"] == "not_implemented"
    assert result["status_code"] == 501


# ---------------------------------------------------------------------------
# 非 dict 返回值
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outline_transitions_skips_non_dict_returns() -> None:
    """Decider 返回非 dict → 跳过, 继续找下一个; 最终找不到返回 501"""
    bad = MagicMock()
    bad.outline_transitions = AsyncMock(return_value="not a dict")  # type: ignore[arg-type]
    manager = _make_manager_with({"bad": bad})

    result = await manager.outline_transitions()

    assert result["error"] == "not_implemented"
    assert result["status_code"] == 501
