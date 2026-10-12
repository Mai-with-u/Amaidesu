"""房间活跃度信号：观众进入增量（累计口径导数）+ 弹幕量，注入 planner 参考段。"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import AUDIENCE_WINDOW_MS, RoomState


class _Danmaku:
    """update() 只需 content 字段（duck-typed）。"""

    def __init__(self, text: str) -> None:
        self.content = text


def test_activity_line_unknown_without_pushes() -> None:
    """未收到观看数推送：增量如实报未知，不冒充 0。"""
    state = RoomState()
    line = state.activity_line(now_ms=1_000_000)
    assert "新增观看未知" in line
    assert "弹幕 0 条" in line


def test_activity_line_counts_window_increment() -> None:
    """窗口两端的累计人次之差 = 新增观看。"""
    state = RoomState()
    state.set_audience_total(10, now_ms=0)
    state.set_audience_total(12, now_ms=60_000)
    state.set_audience_total(18, now_ms=120_000)
    line = state.activity_line(now_ms=120_000)
    assert "新增观看 8" in line  # 18 - 10（窗口含全部样本）


def test_activity_line_zero_increment_means_nobody_in() -> None:
    """累计曲线平稳（增量 0）= 没人进来，事实照报，措辞交给提示词。"""
    state = RoomState()
    state.set_audience_total(10, now_ms=0)
    state.set_audience_total(10, now_ms=300_000)
    line = state.activity_line(now_ms=300_000)
    assert "新增观看 0" in line


def test_activity_line_ignores_samples_beyond_window() -> None:
    """观察窗口外的旧样本不参与增量：两端都落在窗口内才可测。"""
    state = RoomState()
    state.set_audience_total(10, now_ms=0)
    state.set_audience_total(40, now_ms=0 + AUDIENCE_WINDOW_MS + 60_000)
    line = state.activity_line(now_ms=AUDIENCE_WINDOW_MS + 60_000)
    assert "新增观看未知" in line  # 窗口内只有 1 条样本


def test_activity_line_counts_recent_danmaku() -> None:
    """近 1 分钟弹幕条数进入信号行。"""
    state = RoomState()
    state.update(_Danmaku("哈哈"), now_ms=1_000_000)
    state.update(_Danmaku("666"), now_ms=1_000_000 + 1_000)
    line = state.activity_line(now_ms=1_000_000 + 2_000)
    assert "弹幕 2 条" in line


async def test_planner_reference_contains_activity_section() -> None:
    """planner 参考段注入【房间活跃度】（增量语义由上面的单元用例覆盖）。"""
    state = RoomState()
    state.set_audience_total(5, now_ms=0)
    state.set_audience_total(7, now_ms=120_000)
    planner = Planner(
        config={},
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=state,
        context_enabled=False,  # 裸路径：情境段即整段参考文本
    )
    text = await planner._assemble_reference([], None, None, False, False, "")
    assert "【房间活跃度】" in text
    assert "近 10 分钟新增观看" in text
    assert "近 1 分钟弹幕" in text
