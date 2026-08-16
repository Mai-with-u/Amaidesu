"""OutlineState 推进历史（transitions）单元测试（T12 调试可观察性）。

覆盖：
- start() 写入首条 reason="start"，stayed_ms=None
- advance_to() 写入 reason 透传，stayed_ms 正确
- skip() 写入理由 "manual:skip"，可自定义 reason
- jump_to() 写入 reason "manual:jump"
- rewind() 写入 reason "manual:rewind"
- 末段 skip 时 segment_id=None
- 环形缓冲超 50 条时自动丢弃最早
- unload() 清空历史
- pause/resume 不记录（无环节切换）
- 拒绝路径（INACTIVE 状态）不记录
"""

from __future__ import annotations

# 先导入 config.schemas 种子，规避 deciders/__init__ 的预存在循环导入
import src.modules.config.schemas  # noqa: F401  # isort:skip

import pytest

from src.stages.decision.deciders.amaidesu.outline import OutlineSegment, StreamOutline
from src.stages.decision.deciders.amaidesu.outline_state import (
    TRANSITIONS_MAXLEN,
    OutlineState,
    OutlineStatus,
)


def _make_outline(*seg_ids: str) -> StreamOutline:
    """构造测试用 StreamOutline（每个 segment 默认 60s，无 min/branches）。"""
    return StreamOutline(
        outline_id="test_outline",
        title="测试大纲",
        segments=[
            OutlineSegment(
                id=seg_id,
                title=f"环节 {seg_id}",
                task_description=f"任务 {seg_id}",
                duration_ms=60_000,
            )
            for seg_id in seg_ids
        ],
    )


class TestStartTransition:
    """start() 写入首条历史"""

    def test_start_writes_initial_entry(self):
        state = OutlineState()
        outline = _make_outline("a", "b", "c")
        state.start(outline, now_ms=1000)

        transitions = state.get_transitions()
        assert len(transitions) == 1
        entry = transitions[0]
        assert entry["segment_id"] == "a"
        assert entry["title"] == "环节 a"
        assert entry["reason"] == "start"
        assert entry["at_ms"] == 1000
        assert entry["stayed_ms"] is None

    def test_start_clears_previous_history(self):
        """连续两次 start() (重载场景) → 历史仅保留第二次的首条"""
        state = OutlineState()
        outline1 = _make_outline("x", "y")
        outline2 = _make_outline("a", "b")
        state.start(outline1, now_ms=1000)
        state.advance_to("y", reason="outline:test", now_ms=1500)
        assert len(state.get_transitions()) == 2

        state.start(outline2, now_ms=2000)
        transitions = state.get_transitions()
        assert len(transitions) == 1
        assert transitions[0]["segment_id"] == "a"
        assert transitions[0]["reason"] == "start"


class TestAdvanceToTransition:
    """advance_to() 记录自动推进"""

    def test_advance_to_records_reason_and_stayed(self):
        state = OutlineState()
        outline = _make_outline("a", "b", "c")
        state.start(outline, now_ms=1000)
        # 1000ms 后自动推进
        state.advance_to("b", reason="outline:time", now_ms=2500)

        transitions = state.get_transitions()
        assert len(transitions) == 2
        last = transitions[-1]
        assert last["segment_id"] == "b"
        assert last["title"] == "环节 b"
        assert last["reason"] == "outline:time"
        assert last["at_ms"] == 2500
        assert last["stayed_ms"] == 1500  # 2500 - 1000

    def test_advance_to_same_segment_is_noop(self):
        """相同 segment_id → no-op，不应记录"""
        state = OutlineState()
        outline = _make_outline("a", "b")
        state.start(outline, now_ms=1000)
        state.advance_to("a", reason="outline:test", now_ms=2500)
        assert len(state.get_transitions()) == 1

    def test_advance_to_default_reason(self):
        """未显式传 reason → 默认 "advance" """
        state = OutlineState()
        outline = _make_outline("a", "b")
        state.start(outline, now_ms=1000)
        state.advance_to("b", now_ms=1500)
        assert state.get_transitions()[-1]["reason"] == "advance"


class TestSkipTransition:
    """skip() 记录手动跳过"""

    def test_skip_records_default_reason(self):
        state = OutlineState()
        outline = _make_outline("a", "b", "c")
        state.start(outline, now_ms=1000)
        state.skip(now_ms=3500)

        transitions = state.get_transitions()
        assert len(transitions) == 2
        last = transitions[-1]
        assert last["segment_id"] == "b"
        assert last["reason"] == "manual:skip"
        assert last["stayed_ms"] == 2500

    def test_skip_custom_reason(self):
        state = OutlineState()
        outline = _make_outline("a", "b", "c")
        state.start(outline, now_ms=1000)
        state.skip(reason="test:custom", now_ms=2000)
        assert state.get_transitions()[-1]["reason"] == "test:custom"

    def test_skip_last_segment_records_none(self):
        """末段 skip → segment_id=None 表示大纲已结束"""
        state = OutlineState()
        outline = _make_outline("a")
        state.start(outline, now_ms=1000)
        state.skip(now_ms=2000)

        last = state.get_transitions()[-1]
        assert last["segment_id"] is None
        assert last["title"] is None
        assert last["reason"] == "manual:skip"
        assert last["stayed_ms"] == 1000


class TestJumpTransition:
    """jump_to() 记录手动跳转"""

    def test_jump_records_default_reason(self):
        state = OutlineState()
        outline = _make_outline("a", "b", "c")
        state.start(outline, now_ms=1000)
        state.jump_to("c", now_ms=4000)

        transitions = state.get_transitions()
        assert len(transitions) == 2
        last = transitions[-1]
        assert last["segment_id"] == "c"
        assert last["reason"] == "manual:jump"
        assert last["stayed_ms"] == 3000

    def test_jump_custom_reason(self):
        state = OutlineState()
        outline = _make_outline("a", "b")
        state.start(outline, now_ms=1000)
        state.jump_to("b", reason="auto:jump", now_ms=2000)
        assert state.get_transitions()[-1]["reason"] == "auto:jump"


class TestRewindTransition:
    """rewind() 记录手动回退"""

    def test_rewind_records_default_reason(self):
        state = OutlineState()
        outline = _make_outline("a", "b", "c")
        state.start(outline, now_ms=1000)
        state.skip(now_ms=2000)  # a -> b
        state.skip(now_ms=3000)  # b -> c

        state.rewind(now_ms=4000)  # c -> b

        transitions = state.get_transitions()
        assert len(transitions) == 4
        last = transitions[-1]
        assert last["segment_id"] == "b"
        assert last["reason"] == "manual:rewind"
        assert last["stayed_ms"] == 1000  # 上一段(c)停留 1000ms


class TestCircularBuffer:
    """环形缓冲截断到 TRANSITIONS_MAXLEN"""

    def test_circular_buffer_maxlen(self):
        """连续 advance_to 超过 50 条时，最早的被丢弃"""
        assert TRANSITIONS_MAXLEN == 50
        state = OutlineState()
        # 51 个 seg，每次 advance_to 写一条
        seg_ids = [f"s{i}" for i in range(51)]
        outline = _make_outline(*seg_ids)
        state.start(outline, now_ms=0)
        # 现在历史: start -> seg s0
        # 推进 50 次，每次写一条
        for i in range(1, 51):
            state.advance_to(seg_ids[i], reason=f"step:{i}", now_ms=i * 100)
        transitions = state.get_transitions()
        # 最多 50 条（start + 50 advances = 51，应裁剪到 50，start 被裁掉）
        assert len(transitions) == TRANSITIONS_MAXLEN
        # 最旧的应该是 step:1（start 被裁掉）
        assert transitions[0]["reason"] == "step:1"
        assert transitions[-1]["reason"] == "step:50"


class TestUnloadClear:
    """unload() 清空历史"""

    def test_unload_clears_transitions(self):
        state = OutlineState()
        outline = _make_outline("a", "b")
        state.start(outline, now_ms=1000)
        state.skip(now_ms=2000)
        assert len(state.get_transitions()) > 0

        state.unload()
        assert state.get_transitions() == []


class TestRefusePathNoRecord:
    """拒绝路径不记录（INACTIVE + 暂停/resume）"""

    def test_skip_on_inactive_no_record(self):
        state = OutlineState()
        state.skip(now_ms=2000)
        assert state.get_transitions() == []

    def test_advance_to_on_inactive_no_record(self):
        state = OutlineState()
        state.advance_to("b", reason="test", now_ms=2000)
        assert state.get_transitions() == []

    def test_jump_to_on_inactive_raises(self):
        """INACTIVE 状态 jump_to 直接抛 ValueError（与现有契约一致）"""
        state = OutlineState()
        with pytest.raises(ValueError):
            state.jump_to("b", now_ms=2000)

    def test_rewind_on_empty_no_record(self):
        state = OutlineState()
        outline = _make_outline("a", "b")
        state.start(outline, now_ms=1000)
        state.rewind(now_ms=2000)  # completed_segment_ids 为空 → no-op
        assert len(state.get_transitions()) == 1  # 仅 start

    def test_pause_resume_no_record(self):
        """pause / resume 不属于环节切换，不应记录"""
        state = OutlineState()
        outline = _make_outline("a", "b")
        state.start(outline, now_ms=1000)
        state.pause(now_ms=2000)
        state.resume(now_ms=3000)
        # 仅 start 一条
        assert len(state.get_transitions()) == 1


class TestGetTransitionsReturns:
    """get_transitions() 返回值正确性"""

    def test_does_not_mutate_internal_state(self):
        """外部修改返回列表不应影响内部 deque"""
        state = OutlineState()
        outline = _make_outline("a", "b")
        state.start(outline, now_ms=1000)
        state.skip(now_ms=2000)

        original = state.get_transitions()
        original.clear()
        # 内部仍应保留
        assert len(state.get_transitions()) == 2

    def test_empty_state_returns_empty_list(self):
        state = OutlineState()
        assert state.get_transitions() == []
