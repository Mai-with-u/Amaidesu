"""本文件测 MinecraftAgent 收到别的玩家冲角色做的肢体动作（``player_body_language``）时怎么叫醒模型。

覆盖：
- 交付后闲着：照样叫醒，但不开新任务、不兜底交付；回的动作做完也不再叫醒
- 闲着时这一批里上报会被拒，不调工具也安静收尾
- 上报后等主播定夺：叫醒回应，等定夺的状态和委派账原样留着，不注入"继续原游戏任务"
- 任务进行中：插进原任务的历史，原任务照常交付
- 收工态不叫醒；肢体动作不记进随上报带给主播的身体事件
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

from src.modules.llm.payload import Response

from .minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _called,
    _reports,
    _resp,
    _texts,
    _wait_until,
    make_minecraft_agent,
)

_SQUAT = "玩家 Alex（e4）看着你蹲起 4 下；他在你前方 3 格"
_BODY_LANGUAGE = {"goal": {"ability": "body_language", "parameters": {"operation": "crouch", "count": 3}}}


def _tool_said(messages: List[Dict[str, Any]], text: str) -> bool:
    """最后一条工具回执里有没有这句话。"""
    return bool(messages) and messages[-1].get("role") == "tool" and text in str(messages[-1].get("content"))


def _answer_squat(messages: List[Dict[str, Any]]) -> Response:
    """被 [互动] 叫醒就蹲回去，蹲起在跑就让出；别的时候交付"看过周围了"。"""
    if "[互动]" in _texts(messages):
        if _tool_said(messages, "running"):
            return _resp(calls=[_call("minecraft_wait", {"reason": "蹲回去了"})])
        if not _called(messages, "maicraft_execute"):
            return _resp(calls=[_call("maicraft_execute", _BODY_LANGUAGE)])
    return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了"})])


async def test_an_idle_agent_answers_a_squat_without_a_new_task_or_a_delivery() -> None:
    """交付后有人冲她蹲起：叫醒模型回一个蹲起，不开新任务、不再交付；蹲起做完的通知只排队，不再叫醒。"""
    server = FakeMaicraft()
    agent, llm, bus = await make_minecraft_agent(server, _answer_squat)
    agent.receive_delegation(instruction="看一眼周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1 and agent.batch_active is False)

    server.body_event("player_body_language", _SQUAT)
    await _wait_until(lambda: agent.tracked_goal_ids == [1] and agent.batch_active is False and len(llm.seen) == 3)

    woken = _texts(llm.seen[1])
    assert f"[互动] {_SQUAT}。" in woken and "看一眼周围" in woken, "接着上个任务的历史读，没有清空重开"
    assert len(_reports(bus)) == 1, "回一个动作不交付，主播不会每蹲一下念一次"
    observed = agent.observation()
    assert observed["task_finished"] is True and observed["task_instructions"] == ["看一眼周围"]
    assert observed["batch_body_events"] == [], "肢体动作不挤进随上报带给主播的身体事件"

    server.finish(1, summary="蹲起 3 下")
    await _wait_until(lambda: any("目标 1" in str(content) for _, content in agent.queued_messages))
    await asyncio.sleep(0.2)
    assert len(llm.seen) == 3, "闲着时回的动作做完不再叫醒模型"
    await agent.stop()


async def test_reporting_is_refused_and_a_silent_turn_ends_quietly_while_only_answering() -> None:
    """只回应互动的一批：上报被拒（不是一件要交付的活）；不调工具就安静收尾，没有兜底交付。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 交付 -> 被 [互动] 叫醒先试着上报 -> 被拒后不调工具结束这一轮
        if "[互动]" in _texts(messages):
            if _tool_said(messages, "不是一件要交付的活"):
                return _resp("回个招呼就好")
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "跟 Alex 打了招呼"})])
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="看一眼周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1 and agent.batch_active is False)

    server.body_event("player_body_language", _SQUAT)
    await _wait_until(lambda: len(llm.seen) == 3 and agent.batch_active is False)

    assert [report.message for report in _reports(bus)] == ["看过周围了"], "被拒的上报和兜底交付都没有发出去"
    assert agent.observation()["task_finished"] is True
    await agent.stop()


class _RecordingLedger:
    """框架任务账替身：记下每次写入，查条目一律查不到。"""

    def __init__(self) -> None:
        self.updates: List[Dict[str, Any]] = []

    def update(self, task_id: str, status: str, **kwargs: Any) -> bool:
        self.updates.append({"task_id": task_id, "status": status})
        return True

    def get(self, task_id: str) -> None:
        return None


async def test_a_squat_while_waiting_for_the_streamer_keeps_the_task_waiting() -> None:
    """上报后等主播定夺时有人冲她蹲起：回一个动作，任务仍在等定夺，委派账不被改回进行中，也不续原任务。"""
    server = FakeMaicraft()
    ledger = _RecordingLedger()
    tracker = SimpleNamespace(ledger=ledger, track=lambda **kwargs: None)

    def script(messages: List[Dict[str, Any]]) -> Response:
        if "[互动]" in _texts(messages):
            return _answer_squat(messages)
        return _resp(calls=[_call("minecraft_report", {"kind": "escalation", "content": "缺燃料，要不要去砍树"})])

    agent, llm, bus = await make_minecraft_agent(server, script, tracker=tracker)
    agent.receive_delegation(instruction="烧点铁锭", task_id="d-1")
    await _wait_until(lambda: agent.observation()["task_suspended"] and agent.batch_active is False)
    waiting = [update["status"] for update in ledger.updates if update["task_id"] == "d-1"]
    assert waiting[-1] == "waiting_for_decision"

    server.body_event("player_body_language", _SQUAT)
    await _wait_until(lambda: agent.tracked_goal_ids == [1] and agent.batch_active is False and len(llm.seen) == 3)

    assert "[继续原游戏任务]" not in _texts(llm.seen[1]), "只回应互动，不把原任务再摊开一遍"
    assert [update["status"] for update in ledger.updates if update["task_id"] == "d-1"] == waiting
    observed = agent.observation()
    assert observed["task_suspended"] is True and observed["task_instructions"] == ["烧点铁锭"]
    assert [report.report_kind for report in _reports(bus)] == ["escalation"], "没有多出交付或上报"
    await agent.stop()


async def test_a_squat_during_a_running_task_cuts_into_its_history() -> None:
    """任务进行中有人冲她蹲起：在原任务里叫醒模型，回完接着等原目标，原目标结束照常交付。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 下砍树目标 -> 让出 -> 被 [互动] 叫醒蹲回去 -> 让出 -> 砍树结束交付
        if "砍了 5 块原木" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "砍到 5 块原木了"})])
        if _tool_said(messages, "running"):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等着"})])
        if "[互动]" in _texts(messages) and _texts(messages).count("maicraft:body_language") == 0:
            return _resp(calls=[_call("maicraft_execute", _BODY_LANGUAGE)])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1] and agent.batch_active is False and len(llm.seen) == 2)

    server.body_event("player_body_language", _SQUAT)
    await _wait_until(lambda: agent.tracked_goal_ids == [1, 2] and agent.batch_active is False and len(llm.seen) == 4)
    woken = _texts(llm.seen[2])
    assert f"[互动] {_SQUAT}" in woken and "去砍点木头" in woken, "插进原任务的历史"
    assert agent.observation()["task_instructions"] == ["去砍点木头"], "互动不记成原始指令"

    server.finish(2, summary="蹲起 3 下")
    server.finish(1, summary="砍了 5 块原木")
    await _wait_until(lambda: len(_reports(bus)) == 1)
    assert _reports(bus)[0].message == "砍到 5 块原木了"
    await agent.stop()


async def test_a_squat_is_dropped_while_off_air() -> None:
    """收工态不叫醒：下播后有人冲她蹲起没人看，开播时冒出来只是过时的事。"""
    agent, llm, _bus = await make_minecraft_agent(FakeMaicraft(), lambda messages: _resp("好"), live=False)
    await agent._on_body_event({"kind": "player_body_language", "message": _SQUAT, "cursor": 1})
    assert not agent.queued_messages and llm.seen == []
    await agent.stop()
