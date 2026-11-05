"""自己选的死：从事件流认出正在跑的寻死目标，死讯落在它跑的时候就是回家，不是出事。

覆盖：
- 能力名认寻死（带不带命名空间都认）
- 目标开始、接着推进的事件认出寻死目标；别的能力、别的事件不算
- 寻死目标结束就划掉；结束事件漏读时放久了作废；连接拆掉时清空
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.agents.minecraft.chosen_death import SuicideWatch, is_suicide_ability, suicide_goal_of


def _event(kind: str, goal_id: int, message: str = "") -> Dict[str, Any]:
    return {"kind": kind, "goal_id": goal_id, "message": message}


def test_suicide_is_recognized_by_ability_name() -> None:
    assert is_suicide_ability("maicraft:suicide") and is_suicide_ability("suicide")
    assert not is_suicide_ability("maicraft:travel") and not is_suicide_ability("")


def test_suicide_goal_is_read_from_start_and_resume_lines() -> None:
    """目标开始写"开始：能力（目的）"，接着推进写"接着推进：能力"；能力是寻死才算。"""
    assert suicide_goal_of(_event("started", 9, "开始：maicraft:suicide（回家）")) == 9
    assert suicide_goal_of(_event("resumed", 9, "接着推进：maicraft:suicide")) == 9
    assert suicide_goal_of(_event("started", 4, "开始：maicraft:travel（回家）")) is None
    assert suicide_goal_of(_event("finished", 9, "开始：maicraft:suicide")) is None
    assert suicide_goal_of(_event("started", -1, "")) is None


def test_watch_tracks_running_suicide_until_it_finishes_or_goes_stale() -> None:
    """寻死开始后死讯算自己选的；结束了不算；结束事件漏读的放十分钟作废；连接拆掉清空。"""
    now: List[int] = [0]
    watch = SuicideWatch(clock=lambda: now[0])
    assert watch.chosen() is False

    watch.observe(_event("started", 9, "开始：maicraft:suicide（回家）"))
    watch.observe(_event("started", 4, "开始：maicraft:travel（去矿洞）"))
    assert watch.chosen() is True
    watch.observe(_event("finished", 4))
    assert watch.chosen() is True, "别的目标结束不影响"
    watch.observe(_event("finished", 9))
    assert watch.chosen() is False

    watch.observe(_event("started", 10, "开始：maicraft:suicide"))
    now[0] = 10 * 60 * 1000
    assert watch.chosen() is False, "结束事件漏读的放久了作废，免得以后真出事被当成回家"

    watch.observe(_event("started", 11, "开始：maicraft:suicide"))
    watch.clear()
    assert watch.chosen() is False
