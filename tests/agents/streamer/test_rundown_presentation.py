"""流程单展示层：视图形状，以及控制动作翻译成状态机调用与拒绝话术"""

from __future__ import annotations

import pytest

from src.agents.streamer.rundown.presentation import apply_rundown_control, build_rundown_view
from src.agents.streamer.rundown.rundown_state import RundownState
from src.modules.storage.models.rundown import DEFAULT_RUNDOWN


def _loaded_state() -> RundownState:
    """已起算（running）的状态：load 只装载结构，控制用例需计时语义故显式 begin。"""
    state = RundownState()
    state.load(DEFAULT_RUNDOWN)
    state.begin(by="system")
    return state


# 视图拼装


def test_build_view_shape() -> None:
    # 未加载返回 None，dashboard 降级为不可用视图
    assert build_rundown_view(RundownState()) is None
    view = build_rundown_view(_loaded_state())
    assert set(view.keys()) == {"snapshot", "transitions", "segments"}
    assert view["segments"], "默认流程单应有环节"
    for seg in view["segments"]:
        assert set(seg.keys()) == {
            "id",
            "title",
            "task_description",
            "expected_ms",
            "min_duration_ms",
            "notes",
        }


# 控制翻译：拒绝话术逐字锁定（dashboard 端点原样透传给 UI）


@pytest.mark.parametrize(
    ("loaded", "action", "kwargs", "message", "has_snapshot"),
    [
        (False, "goto", {"segment_id": "x"}, "未加载流程单", True),
        (False, "pause", {}, "流程单未在运行", True),
        (True, "goto", {}, "goto 必须提供 segment_id", False),
        (True, "rewind", {}, "未知 action:", False),
        # 列出可用环节，但不回显输入的环节名
        (True, "goto", {"segment_id": "不存在的环节"}, "环节不存在（可用:", True),
    ],
)
def test_control_rejections(loaded: bool, action: str, kwargs: dict, message: str, has_snapshot: bool) -> None:
    state = _loaded_state() if loaded else RundownState()
    success, text, snapshot = apply_rundown_control(state, action, **kwargs)
    assert success is False and text.startswith(message) and "不存在的环节" not in text
    assert (snapshot is not None) is has_snapshot
    if not loaded:
        assert snapshot["status"] == "idle"


def test_control_blank_title_target_rejected_for_human() -> None:
    """human 通路同拦空标题目标：门禁收敛在状态机后 Dashboard 手动控制不再绕过。"""
    from src.modules.storage.models.rundown import Rundown, RundownSegment

    rundown = Rundown(
        rundown_id="blank_rd",
        title="空标题流程单",
        segments=[
            RundownSegment(id="a", title="开场", task_description="目标", expected_ms=60_000),
            RundownSegment(id="b", title="闲聊", task_description="目标", expected_ms=60_000),
        ],
    )
    rundown.segments[1].title = "   "  # 原地改脏，模拟绕过模型校验的存量数据
    state = _loaded_state()
    state.load(rundown)

    success, message, snapshot = apply_rundown_control(state, "goto", segment_id="b")
    assert success is False and message.startswith("目标环节标题为空")
    assert snapshot is not None and snapshot["current"]["id"] == "a"

    success, message, _ = apply_rundown_control(state, "next")
    assert success is False and message.startswith("目标环节标题为空")


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


# renew（续期）翻译


async def test_control_renew_resets_timer_by_human() -> None:
    """Dashboard 手动续期走 by="human"：成功后快照剩余时长回到完整周期。"""
    state = _loaded_state()
    first = DEFAULT_RUNDOWN.segments[0]

    success, message, snapshot = apply_rundown_control(state, "renew")
    assert (success, message) == (True, "已执行")
    assert snapshot is not None
    assert snapshot["status"] == "running"
    assert snapshot["current"]["id"] == first.id
    assert snapshot["current"]["remaining_ms"] == first.expected_ms
    assert state.get_transitions()[-1]["by"] == "human"


async def test_control_renew_rejected_when_paused() -> None:
    """暂停中续期被 not_running 话术拒绝（renew 仅 running 合法）。"""
    state = _loaded_state()
    apply_rundown_control(state, "pause")

    success, message, _ = apply_rundown_control(state, "renew")

    assert success is False
    assert message == "流程单未在运行"


# restart（整场重开）翻译：导演通道，agent 无权（工具层不暴露）


async def test_control_restart_resets_to_first_segment() -> None:
    """done 态 restart 走 by="human"：快照回首环节、状态回 running。"""
    state = _loaded_state()
    for _ in range(len(DEFAULT_RUNDOWN.segments)):
        apply_rundown_control(state, "next")
    assert state.status == "done"

    success, message, snapshot = apply_rundown_control(state, "restart")

    assert (success, message) == (True, "已执行")
    assert snapshot is not None
    assert (snapshot["status"], snapshot["index"]) == ("running", 0)
    assert snapshot["current"]["id"] == DEFAULT_RUNDOWN.segments[0].id
    assert state.get_transitions()[-1]["action"] == "restart"


async def test_control_restart_rejected_when_idle() -> None:
    success, message, snapshot = apply_rundown_control(RundownState(), "restart")

    assert success is False and message == "未加载流程单"
    assert snapshot is not None and snapshot["status"] == "idle"


async def test_control_restart_rejected_when_armed() -> None:
    """armed（已装载未开播）restart 被 not_started 话术拒绝——还没开始无从重开。"""
    state = RundownState()
    state.load(DEFAULT_RUNDOWN)
    assert state.status == "armed"

    success, message, snapshot = apply_rundown_control(state, "restart")

    assert success is False and message == "流程单尚未开始"
    assert snapshot is not None and snapshot["status"] == "armed"
