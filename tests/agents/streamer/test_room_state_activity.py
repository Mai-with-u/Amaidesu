"""房间活跃度信号：观众进入增量（累计口径导数）+ 弹幕量，注入 planner 参考段。"""

from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock

import pytest

from src.agents.streamer.planner import PlannerConfig, Planner
from src.agents.streamer.room_state import AUDIENCE_WINDOW_MS, RoomState
from src.modules.utils.time_utils import now_ms
from tests.agents.streamer.helpers import make_streamer_agent


class _Danmaku:
    """update() 只需 content 字段（duck-typed）。"""

    def __init__(self, text: str) -> None:
        self.content = text


@pytest.mark.parametrize(
    ("samples", "now", "expected"),
    [
        ([], 1_000_000, "新增观看未知"),  # 未收到推送：如实报未知，不冒充 0
        ([(10, 0), (12, 60_000), (18, 120_000)], 120_000, "新增观看 8"),  # 窗口两端累计人次之差
        ([(10, 0), (10, 300_000)], 300_000, "新增观看 0"),  # 曲线平稳 = 没人进来，事实照报
        # 窗口外的旧样本不参与：窗口内只剩 1 条样本，无从求差
        ([(10, 0), (40, AUDIENCE_WINDOW_MS + 60_000)], AUDIENCE_WINDOW_MS + 60_000, "新增观看未知"),
    ],
)
def test_activity_line_audience_increment(samples: list, now: int, expected: str) -> None:
    state = RoomState()
    for total, at in samples:
        state.set_audience_total(total, now_ms=at)
    assert expected in state.activity_line(now_ms=now)


def test_activity_line_counts_recent_danmaku() -> None:
    state = RoomState()
    assert "弹幕 0 条" in state.activity_line(now_ms=1_000_000)
    state.update(_Danmaku("哈哈"), now_ms=1_000_000)
    state.update(_Danmaku("666"), now_ms=1_001_000)
    assert "弹幕 2 条" in state.activity_line(now_ms=1_002_000)


async def test_planner_reference_contains_activity_section() -> None:
    """planner 参考段注入【房间活跃度】（增量语义由上面的单元用例覆盖）。"""
    state = RoomState()
    state.set_audience_total(5, now_ms=0)
    state.set_audience_total(7, now_ms=120_000)
    planner = Planner(
        config=PlannerConfig(),
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=state,
        context_enabled=False,  # 裸路径：情境段即整段参考文本
    )
    text, _ = await planner._assemble_reference([], None, None, False, False, "")
    assert "【房间活跃度】" in text
    assert "近 10 分钟新增观看" in text
    assert "近 1 分钟弹幕" in text


def test_activity_line_lists_recent_enters_within_window() -> None:
    """进房名单进活跃度行：窗口内的进房按先后点名，窗口外的旧记录不再出现。"""
    state = RoomState()
    state.record_enter("u1", "阿明", now_ms=1_000_000)
    state.record_enter("u2", "小麦", now_ms=1_001_000)
    line = state.activity_line(now_ms=1_002_000)
    assert "近 10 分钟进房：阿明、小麦" in line

    # 窗口外的进房不再渲染
    line_later = state.activity_line(now_ms=1_001_000 + 600_000 + 1)
    assert "进房" not in line_later


def test_enter_records_are_rate_limited_per_user() -> None:
    """同人限频：限频窗口内反复进房只记一次，不重复点名；过窗后再进重新记。"""
    state = RoomState()
    state.record_enter("u1", "阿明", now_ms=1_000_000)
    state.record_enter("u1", "阿明", now_ms=1_100_000)
    assert "近 10 分钟进房：阿明" in state.activity_line(now_ms=1_200_000)

    # 限频窗口（30 分钟）过后再进房：重新记录、重新可欢迎
    state.record_enter("u1", "阿明", now_ms=1_000_000 + 1_800_000)
    line = state.activity_line(now_ms=1_000_000 + 1_800_000)
    assert line.count("阿明") == 1, "窗口内旧记录已裁剪，同名只出现一次"


def test_enter_render_is_capped_to_recent_few() -> None:
    """渲染截取最近几位：进房很多时名单只保留最近的，不撑爆决策上下文。"""
    state = RoomState()
    for i in range(8):
        state.record_enter(f"u{i}", f"观众{i}", now_ms=1_000_000 + i)
    line = state.activity_line(now_ms=1_100_000)
    assert "观众0" not in line and "观众2" not in line
    assert "观众7" in line


async def _silence_reference(state: RoomState, cold_silence_ms: int) -> str:
    planner = Planner(
        config=PlannerConfig(cold_silence_ms=cold_silence_ms),
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=state,
        context_enabled=False,  # 裸路径：情境段即整段参考文本
    )
    text, _ = await planner._assemble_reference([], None, None, False, True, "")
    assert text is not None
    return text


async def test_planner_reference_marks_cold_after_long_silence() -> None:
    """她超过冷场阈值没开口，参考段写出已经沉默多久：线上回放里游戏一平静她就整段不说话。"""
    state = RoomState()
    state.record_speech(now_ms() - 60_000)
    text = await _silence_reference(state, 45_000)
    assert "【冷场】她已经" in text and "秒没开口（超过 45 秒算冷场）" in text


@pytest.mark.parametrize(
    ("silent_ms", "cold_silence_ms"),
    [
        (None, 45_000),  # 还没开过口：开场白由流程单负责，不算冷场
        (10_000, 45_000),  # 刚说过话
        (600_000, 0),  # 阈值 0 = 关闭提示
    ],
)
async def test_planner_reference_skips_cold_line(silent_ms: Optional[int], cold_silence_ms: int) -> None:
    """没开过口、刚说过话、或关掉提示时，不出现冷场行。"""
    state = RoomState()
    if silent_ms is not None:
        state.record_speech(now_ms() - silent_ms)
    text = await _silence_reference(state, cold_silence_ms)
    assert "【冷场】" not in text


def test_streamer_wires_proactive_cold_timeout_into_planner() -> None:
    """冷场提示复用主动发言的冷场阈值，同一个"多久算冷"只配一处。"""
    agent = make_streamer_agent({"proactive": {"cold_timeout_ms": 30_000}})
    assert agent._planner._cold_silence_ms == 30_000
