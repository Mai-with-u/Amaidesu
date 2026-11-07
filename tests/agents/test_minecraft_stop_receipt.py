"""本文件测叫停回执的文字：急停与撤单之后，停下了哪些目标、哪个没停下（附原因）、手上的活撤没撤。"""

from __future__ import annotations

from src.agents.minecraft.stop_receipt import GoalStopOutcome, cancel_receipt, stop_goal_label, stop_receipt


def test_goal_label_prefers_purpose_and_strips_the_ability_prefix() -> None:
    assert stop_goal_label("maicraft:gather", "挖铁矿") == "挖铁矿"
    assert stop_goal_label("maicraft:gather", "  ") == "gather"


def test_stop_receipt_says_all_stopped_and_that_the_job_is_still_on_the_books() -> None:
    """默认急停不撤活：目标都停下了，手上的活还在账上。"""
    outcome = GoalStopOutcome(stopped=["挖铁矿", "砍树"])
    assert stop_receipt(outcome, held_delegations=1, cancel_delegations=False) == (
        "操作员急停了游戏里的动作：在跑的目标都停下了（挖铁矿、砍树）；手上的活没撤，账面还在，等下一步安排"
    )


def test_stop_receipt_names_the_goal_that_did_not_stop_and_why() -> None:
    """有目标没叫停住：点名是哪个、为什么，并说它可能还在做。"""
    outcome = GoalStopOutcome(stopped=["砍树"], not_stopped=[("挖铁矿", "游戏连接还没就绪")])
    text = stop_receipt(outcome, held_delegations=1, cancel_delegations=True)
    assert text == (
        "操作员急停了游戏里的动作：停下了砍树；挖铁矿没停下（游戏连接还没就绪），可能还在做；手上的活一并撤了"
    )


def test_stop_receipt_without_goals_or_jobs_does_not_invent_a_job() -> None:
    assert stop_receipt(GoalStopOutcome(), held_delegations=0, cancel_delegations=False) == (
        "操作员急停了游戏里的动作：叫停时没有在跑的目标"
    )
    assert stop_receipt(GoalStopOutcome(), held_delegations=0, cancel_delegations=True).endswith("手上本来没有活")


def test_cancel_receipt_says_who_cancelled_and_reports_goals_even_when_not_found() -> None:
    """撤单回执：谁撤的、撤没撤成；目标在核对任务号前就叫停了，没撤成也照实写目标那一段。"""
    stopped = GoalStopOutcome(stopped=["挖铁矿"])
    assert cancel_receipt(stopped, cancelled=True, by_operator=False) == (
        "我撤掉了游戏里手上的活：在跑的目标都停下了（挖铁矿）"
    )
    failed = GoalStopOutcome(not_stopped=[("挖铁矿", "没有编号为 3 的目标")])
    assert cancel_receipt(failed, cancelled=False, by_operator=True) == (
        "操作员要撤的活游戏这边没找到，没撤成：挖铁矿没停下（没有编号为 3 的目标），可能还在做"
    )
