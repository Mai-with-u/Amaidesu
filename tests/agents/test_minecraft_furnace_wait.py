"""本文件测 obtain 装好炉先走（partial + WRONG_TIME）：不当成做不成上报主播、不催、不兜底交付，到点回炉取够才交付。"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.agents.minecraft.furnace_wait import FurnaceWaits
from src.agents.minecraft.maicraft import goal_run_of
from src.modules.llm.payload import Response

from .minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _reports,
    _resp,
    _texts,
    _wait_until,
    make_minecraft_agent,
)

_FURNACE = {
    "x": 10,
    "y": 64,
    "z": -3,
    "dimension": "minecraft:overworld",
    "block": "minecraft:furnace",
    "item": "minecraft:dried_kelp",
    "ingredient": "minecraft:kelp",
    "loaded": 64,
    "ready_in_seconds": 600,
    "ingredients_in_backpack": 0,
    "timer_id": 7,
}
_LEFT_COOKING_PROBLEM = {
    "kind": "WRONG_TIME",
    "message": "装好炉先走了：(10, 64, -3) 的熔炉在烧 64 个海带，约 10 分钟好",
    "suggestion": "计时 7 到点会在事件里叫你（timer_due），到时再 obtain 干海带",
}
_TIMER_DUE = "计时 7 到点：回 (10, 64, -3) 的熔炉取干海带 ×64（600 秒前设的；此刻白天）"


def _finished(result: Dict[str, Any], ability: str = "maicraft:obtain", goal_id: int = 1) -> Any:
    run = goal_run_of({"goal_id": goal_id, "ability": ability, "state": "finished", "result": result})
    assert run is not None
    return run


def _left_cooking(*furnaces: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "partial",
        "summary": "装好炉先走了",
        "problem": _LEFT_COOKING_PROBLEM,
        "details": {"left_cooking": list(furnaces)},
    }


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ---------------------------------------------------------------------------
# 回炉计时的簿记
# ---------------------------------------------------------------------------


def test_left_cooking_timer_counts_as_waiting_until_it_fires() -> None:
    """装好炉先走：带计时编号的那台算在等，没挂上计时的不算；到点划掉，重读同一个结束的目标也不再记上。"""
    waits = FurnaceWaits(_Clock())
    unscheduled = {**_FURNACE, "x": 20, "timer_id": None}
    run = _finished(_left_cooking(_FURNACE, unscheduled))

    waits.absorb(run)
    assert [wait.timer_id for wait in waits.waiting()] == [7]
    assert "(10, 64, -3)" in waits.waiting()[0].note and "minecraft:dried_kelp ×64" in waits.waiting()[0].note

    waits.timer_due(_TIMER_DUE)
    waits.absorb(run)
    assert waits.waiting() == [], "响过的计时不因模型回头读那个目标重新算在等"


@pytest.mark.parametrize(
    "result",
    [
        {"status": "done", "summary": "拿够了", "details": {"left_cooking": [_FURNACE]}},
        {**_left_cooking(_FURNACE), "problem": {"kind": "NEED_ITEM", "message": "缺燃料"}},
        {"status": "partial", "summary": "装好炉先走了", "problem": _LEFT_COOKING_PROBLEM},
        {"status": "failed", "summary": "一件都没拿到", "problem": _LEFT_COOKING_PROBLEM},
    ],
    ids=["done", "partial_need_item", "no_left_cooking", "failed"],
)
def test_only_partial_wrong_time_with_left_cooking_counts(result: Dict[str, Any]) -> None:
    """只认 partial + WRONG_TIME + left_cooking：别的结局不是在等炉子。"""
    waits = FurnaceWaits(_Clock())
    waits.absorb(_finished(result))
    assert waits.waiting() == []


def test_timer_results_reconcile_with_what_is_still_scheduled() -> None:
    """timer 能力的结果带着还挂着的全部计时：不在里面的（被取消、被 Mod 回炉时摘掉）不再算在等。"""
    waits = FurnaceWaits(_Clock())
    waits.absorb(_finished(_left_cooking(_FURNACE, {**_FURNACE, "x": 11, "timer_id": 8})))
    timer_list = {
        "status": "done",
        "summary": "还挂着 1 个计时",
        "details": {"pending": [{"timer_id": 8, "remaining_seconds": 300, "note": "回炉取"}]},
    }
    waits.absorb(_finished(timer_list, ability="maicraft:timer", goal_id=2))
    assert [wait.timer_id for wait in waits.waiting()] == [8]


def test_a_timer_that_never_fires_stops_counting_after_its_deadline() -> None:
    """过了兜底期限（约几秒烧好的两倍再加两分钟）还没响：不再算在等，交出去一次就划掉。"""
    clock = _Clock()
    waits = FurnaceWaits(clock)
    waits.absorb(_finished(_left_cooking(_FURNACE)))

    clock.now += 600 * 2 + 120 - 1
    assert [wait.timer_id for wait in waits.waiting()] == [7]
    assert waits.take_overdue() == []

    clock.now += 2
    assert waits.waiting() == []
    assert [wait.timer_id for wait in waits.take_overdue()] == [7]
    assert waits.take_overdue() == [], "每台只交出去一次"


def test_switching_worlds_forgets_timers_and_their_numbers() -> None:
    """换存档：Mod 清空了计时，在等的清掉；新存档的计时编号可能从头数，响过的编号也不再挡着。"""
    waits = FurnaceWaits(_Clock())
    run = _finished(_left_cooking(_FURNACE))
    waits.absorb(run)
    waits.timer_due(_TIMER_DUE)

    waits.forget_world()
    waits.absorb(run)
    assert [wait.timer_id for wait in waits.waiting()] == [7]


# ---------------------------------------------------------------------------
# 游戏 Agent：装好炉先走不是做不成
# ---------------------------------------------------------------------------


_OBTAIN = _call("maicraft_execute", {"goal": {"ability": "obtain", "purpose": "烤干海带"}})
_WAIT = _call("minecraft_wait", {"reason": "等它做完"})
_DELIVERY = _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "64 个干海带都拿到了"})])


def _last(messages: List[Dict[str, Any]]) -> tuple[str, str]:
    """剧本只看最后一条：工具回执就是刚调的工具的结果，用户消息就是这次唤醒读到的最新通知。"""
    return str(messages[-1].get("role")), str(messages[-1].get("content") or "")


def _furnace_script(*, with_todo: bool, tries_wait: bool) -> Any:
    """烤海带的剧本：（写待办）-> 下 obtain -> 让出 -> 装好炉先走被唤醒（试一次等待）-> 不调工具结束这一轮
    -> 计时到点被唤醒 -> 再 obtain 同一样东西 -> 让出 -> 拿够了交付。"""

    def todo(status: str) -> Response:
        return _resp(
            calls=[
                _call("minecraft_todo", {"action": "write", "todos": [{"content": "烤 64 个干海带", "status": status}]})
            ]
        )

    def script(messages: List[Dict[str, Any]]) -> Response:
        role, text = _last(messages)
        if role == "tool":
            if "炉子在烧" in text:
                return _resp("装好炉了，约 10 分钟后回来取。")
            if '"status": "done"' in text:
                return _DELIVERY
            return _resp(calls=[_WAIT]) if '"running"' in text else _resp(calls=[_OBTAIN])
        if "结束了：done" in text:
            # 拿够了：有待办先把它勾掉再交付（还有没做完的待办时交付会被拒）
            return todo("done") if with_todo else _DELIVERY
        if "[计时]" in text:
            return _resp(calls=[_OBTAIN])
        if "结束了：partial" in text:
            return _resp(calls=[_WAIT]) if tries_wait else _resp("装好炉了，约 10 分钟后回来取。")
        return todo("in_progress") if with_todo else _resp(calls=[_OBTAIN])

    return script


def _yielded_on_running(agent: Any, llm: Any) -> bool:
    """下了目标、看到它在跑就让出了：这一批已经结束。"""
    return agent.batch_active is False and '"running"' in _last(llm.seen[-1])[1]


@pytest.mark.parametrize(
    ("with_todo", "tries_wait"),
    [(False, True), (True, False)],
    ids=["no_todo_tries_wait", "unfinished_todo"],
)
async def test_left_cooking_is_waited_on_not_reported_and_resumes_on_the_timer(
    with_todo: bool, tries_wait: bool
) -> None:
    """装好炉先走后模型不调工具结束一轮：静默让出等计时——没有升级上报、没有"任务尚需行动"催促、
    没有兜底交付"任务完成"；计时到点在原任务里叫醒，再 obtain 取够后才交付一次。"""
    server = FakeMaicraft()
    agent, llm, bus = await make_minecraft_agent(server, _furnace_script(with_todo=with_todo, tries_wait=tries_wait))
    agent.receive_delegation(instruction="烤 64 个干海带收进背包", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: _yielded_on_running(agent, llm))

    server.finish(1, "partial", "装好炉先走了", problem=_LEFT_COOKING_PROBLEM, details={"left_cooking": [_FURNACE]})
    await _wait_until(lambda: "结束了：partial" in _texts(llm.seen[-1]) and agent.batch_active is False)

    seen = _texts(llm.seen[-1])
    assert _reports(bus) == [], "装好炉先走不是做不成：不升级上报，也不兜底交付"
    assert "[任务尚需行动]" not in seen, "在等炉子不算没推进"
    assert not agent.observation()["task_finished"] and not agent.observation()["task_suspended"]
    if tries_wait:
        refusal = _last(llm.seen[-1])[1]
        assert "炉子在烧" in refusal and "不用上报" in refusal, "等待工具说清不是卡住了，免得模型转去上报阻塞"

    server.body_event("timer_due", _TIMER_DUE)
    await _wait_until(lambda: agent.tracked_goal_ids == [2])
    woken = _texts(llm.seen[-1])
    assert f"[计时] {_TIMER_DUE}" in woken and "再 obtain 同一样东西" in woken
    assert agent.observation()["task_instructions"] == ["烤 64 个干海带收进背包"], "到点在原任务里接着做"

    await _wait_until(lambda: _yielded_on_running(agent, llm))
    server.finish(2, "done", "拿到 64 个干海带")
    await _wait_until(lambda: len(_reports(bus)) == 1)
    assert [report.report_kind for report in _reports(bus)] == ["delivery"]
    assert _reports(bus)[0].message == "64 个干海带都拿到了"
    await agent.stop()


async def test_a_furnace_timer_that_never_fires_wakes_the_task_after_its_deadline() -> None:
    """回炉计时过了兜底期限还没响（游戏重开清掉了）：叫醒还在等它的任务去看一眼，不让它一直空等。"""
    server = FakeMaicraft()
    clock = _Clock()

    def script(messages: List[Dict[str, Any]]) -> Response:
        role, text = _last(messages)
        if role == "tool":
            return _resp(calls=[_WAIT])
        if "过了预计时间还没响" in text:
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "回炉看过了"})])
        if "结束了：partial" in text:
            return _resp("装好炉了，约 10 分钟后回来取。")
        return _resp(calls=[_OBTAIN])

    agent, llm, bus = await make_minecraft_agent(server, script, clock=clock)
    agent.receive_delegation(instruction="烤 64 个干海带收进背包", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: _yielded_on_running(agent, llm))
    server.finish(1, "partial", "装好炉先走了", problem=_LEFT_COOKING_PROBLEM, details={"left_cooking": [_FURNACE]})
    await _wait_until(lambda: "结束了：partial" in _texts(llm.seen[-1]) and agent.batch_active is False)
    assert _reports(bus) == []

    clock.now += 600 * 2 + 120 + 1
    await _wait_until(lambda: len(_reports(bus)) == 1)
    woken = _texts(llm.seen[-1])
    assert "计时 7：(10, 64, -3) 的炉子在烧" in woken and "去看一眼炉子" in woken
    await agent.stop()
