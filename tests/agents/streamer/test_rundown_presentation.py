"""rundown/presentation 单元测试：视图形状 + 控制翻译拒绝话术。

视图/控制逻辑从 StreamerAgent 下沉到 ``rundown/presentation.py`` 后，
本文件直接打两个函数（真实 ``RundownState`` + 内置默认流程单），
话术字符串逐字锁定——dashboard 端点原样透传给 UI。
"""

from __future__ import annotations

import pytest

from src.agents.streamer.rundown.presentation import apply_rundown_control, build_rundown_view
from src.agents.streamer.rundown.rundown_state import RundownState
from src.modules.storage.models.rundown import DEFAULT_RUNDOWN


def _loaded_state() -> RundownState:
    state = RundownState()
    state.load(DEFAULT_RUNDOWN)
    return state


# ---------------------------------------------------------------------------
# 视图拼装
# ---------------------------------------------------------------------------


def test_build_view_segment_keys() -> None:
    """加载后视图含三把 key，segments 每项字段形状完整。"""
    view = build_rundown_view(_loaded_state())
    assert view is not None
    assert set(view.keys()) == {"snapshot", "transitions", "segments"}
    assert view["segments"], "默认流程单应有环节"
    for seg in view["segments"]:
        assert set(seg.keys()) == {
            "id",
            "title",
            "task_description",
            "key_points",
            "expected_ms",
            "min_duration_ms",
            "notes",
        }


def test_build_view_none_when_not_loaded() -> None:
    """未加载流程单返回 None（dashboard 降级为不可用视图）。"""
    assert build_rundown_view(RundownState()) is None


# ---------------------------------------------------------------------------
# 控制翻译：拒绝话术逐字锁定
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_control_no_rundown_loaded_rejected() -> None:
    """未加载时 goto 被 no_rundown_loaded 拒绝（pause 走状态机 not_running）。"""
    success, message, snapshot = apply_rundown_control(RundownState(), "goto", segment_id="x")
    assert success is False
    assert message == "未加载流程单"
    assert snapshot is not None and snapshot["status"] == "idle"


@pytest.mark.asyncio
async def test_control_pause_rejected_when_not_running() -> None:
    success, message, snapshot = apply_rundown_control(RundownState(), "pause")
    assert success is False
    assert message == "流程单未在运行"
    assert snapshot is not None and snapshot["status"] == "idle"


@pytest.mark.asyncio
async def test_control_goto_requires_segment_id() -> None:
    success, message, snapshot = apply_rundown_control(_loaded_state(), "goto")
    assert success is False
    assert message == "goto 必须提供 segment_id"
    assert snapshot is None


@pytest.mark.asyncio
async def test_control_unknown_action() -> None:
    success, message, snapshot = apply_rundown_control(_loaded_state(), "rewind")
    assert success is False
    assert message.startswith("未知 action:")
    assert snapshot is None


@pytest.mark.asyncio
async def test_control_unknown_segment_id_lists_available() -> None:
    success, message, _ = apply_rundown_control(_loaded_state(), "goto", segment_id="不存在的环节")
    assert success is False
    assert message.startswith("环节不存在（可用:")
    assert "不存在的环节" not in message


@pytest.mark.asyncio
async def test_control_pause_then_resume_again_rejected() -> None:
    """pause → resume 正常链；重复 resume 被 not_paused 话术拒绝。"""
    state = _loaded_state()
    success, message, _ = apply_rundown_control(state, "pause")
    assert (success, message) == (True, "已执行")
    success, message, _ = apply_rundown_control(state, "resume")
    assert (success, message) == (True, "已执行")
    success, message, snapshot = apply_rundown_control(state, "resume")
    assert success is False
    assert message == "流程单未在暂停"
    assert snapshot is not None
