"""房间活跃度信号：观众进入增量（累计口径导数）+ 弹幕量，注入 planner 参考段。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import AUDIENCE_WINDOW_MS, RoomState


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
