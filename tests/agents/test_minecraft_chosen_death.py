"""自己选的死：从事件流认出正在跑的寻死目标，死讯落在它跑的时候就是回家，不是出事。

覆盖：
- 能力名认寻死（带不带命名空间都认）
- 目标开始、接着推进的事件认出寻死目标：先读 ability 字段，旧版 Mod 没有字段时解析消息开头；别的能力、别的事件不算
- 寻死目标结束就划掉；结束事件漏读时放久了作废；连接拆掉时清空
- sequence 里寻死那一步做完、同一编号改推进别的能力时划掉
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.agents.minecraft.chosen_death import SuicideWatch, is_suicide_ability, suicide_goal_of


def _event(kind: str, goal_id: int, message: str = "") -> Dict[str, Any]:
    return {"kind": kind, "goal_id": goal_id, "message": message}


def test_suicide_is_recognized_by_ability_name() -> None:
    assert is_suicide_ability("maicraft:suicide") and is_suicide_ability("suicide")
    assert not is_suicide_ability("maicraft:travel") and not is_suicide_ability("")


def _goal_event(kind: str, goal_id: int, ability: str, message: str = "") -> Dict[str, Any]:
    return {"kind": kind, "goal_id": goal_id, "ability": ability, "message": message}


def test_suicide_goal_is_read_from_the_ability_field_first() -> None:
    """新版 Mod 在目标事件上带 ability 字段：按字段认，不看消息开头写了什么。"""
    assert suicide_goal_of(_goal_event("started", 9, "maicraft:suicide", "开始：回家")) == 9
    assert suicide_goal_of(_goal_event("resumed", 9, "maicraft:suicide", "解除暂停，仍在等回答：选哪个")) == 9
    # 字段说的是别的能力时，就算消息开头像寻死也不算
    assert suicide_goal_of(_goal_event("started", 4, "maicraft:travel", "开始：maicraft:suicide（回家）")) is None
    assert suicide_goal_of(_goal_event("paused", 9, "maicraft:suicide", "暂停：maicraft:suicide")) is None


def test_suicide_goal_falls_back_to_start_and_resume_lines() -> None:
    """旧版 Mod 没有 ability 字段：目标开始写"开始：能力（目的）"，接着推进写"接着推进：能力"；能力是寻死才算。"""
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


def test_watch_drops_a_sequence_once_its_suicide_step_is_over() -> None:
    """sequence 的事件都记在整件的编号上、ability 写那一步的能力：寻死那一步做完、或改推进别的能力就不再算在寻死。"""
    watch = SuicideWatch(clock=lambda: 0)

    # 寻死那一步被暂停后接着推进 -> 记下；这一步做完 -> 划掉，整件 sequence 还在跑也不算
    watch.observe(_goal_event("resumed", 20, "maicraft:suicide", "接着推进：回家"))
    assert watch.chosen() is True
    watch.observe(_goal_event("step_finished", 20, "maicraft:suicide", "第 2 步（maicraft:suicide）：死了一次"))
    assert watch.chosen() is False

    # 记下后同一编号接着推进的是别的能力（走到了下一步）-> 划掉
    watch.observe(_goal_event("resumed", 21, "maicraft:suicide"))
    watch.observe(_goal_event("resumed", 21, "maicraft:travel", "接着推进：去矿洞"))
    assert watch.chosen() is False

    # 别的 sequence 的步骤做完不影响还在跑的寻死
    watch.observe(_goal_event("started", 22, "maicraft:suicide"))
    watch.observe(_goal_event("step_finished", 23, "maicraft:gather", "第 1 步（maicraft:gather）：砍好了"))
    assert watch.chosen() is True
