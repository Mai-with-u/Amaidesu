"""本文件测 MinecraftAgent 的计时到点与天色主题：计时到点按任务状态开新任务/普通通知/续挂起任务，天色变化只随下次唤醒捎带。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from unittest.mock import MagicMock


from src.agents.minecraft.agent import MinecraftAgent
from src.modules.events.payloads.game import GamePayload
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


async def test_sky_changes_ride_along_with_the_next_wakeup_instead_of_waking_the_task() -> None:
    """天色变了不叫醒等着目标的模型：挂在队列里随目标结束一起带上，只留最新一条；任务交付后不再挂，也不上报主播。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 下砍树目标 -> 让出等砍树 -> 天黑、下雨都不唤醒 -> 砍树结束被唤醒，连同最新天色一起读到 -> 交付。
        if "结束了" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "砍到 5 块原木了"})])
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等砍树"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: agent.batch_active is False and len(llm.seen) == 2)

    server.body_event("sky_changed", "天黑了，到了能睡的时间（夜晚，距天亮约 8 分钟；晴）")
    server.body_event("sky_changed", "开始下雨了（夜晚；雨）")
    await _wait_until(lambda: any("开始下雨了" in str(content) for _, content in agent.queued_messages))
    await asyncio.sleep(0.2)
    assert len(llm.seen) == 2, "天色变了不唤醒等着目标的模型"
    assert len(agent.queued_messages) == 1, "队列里只留最新一条天色"

    server.finish(1, summary="砍了 5 块原木")
    await _wait_until(lambda: len(_reports(bus)) == 1)
    woken = _texts(llm.seen[-1])
    assert "[天色] 开始下雨了（夜晚；雨）" in woken and "目标 1" in woken
    assert "[天色] 天黑了" not in woken, "被新天色换掉的那条不再带给模型"

    server.body_event("sky_changed", "天亮了，过了能睡的时间（白天；雨）")
    await _wait_until(lambda: any("天亮了" in record["message"] for record in agent.observation()["batch_body_events"]))
    assert not agent.queued_messages, "任务交付后不挂天色，等到下个任务只会是过时的"
    alerts = [
        call.args[1]
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "attention_required"
    ]
    assert alerts == [], "给观众的天色叙述由注意流采集器出，游戏 Agent 不上报主播"
    await agent.stop()


_TIMER_DUE = "计时 3 到点：看炉子烧好没（600 秒前设的；此刻白天）"


def _alerts(bus: MagicMock) -> List[GamePayload]:
    return [
        call.args[1]
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "attention_required"
    ]


async def test_a_due_timer_wakes_an_idle_agent_as_a_new_task() -> None:
    """任务交付后闲着时计时到点：照样叫醒模型，由这条到点开一个新任务，Mod 那句话原样带上，不上报主播。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 看一眼周围交付 -> 闲着 -> 计时到点被叫醒（新任务）-> 按备注去看炉子后交付。
        if "[计时]" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "炉子烧好了"})])
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="看一眼周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1 and agent.batch_active is False)
    assert agent.observation()["task_finished"]

    server.body_event("timer_due", _TIMER_DUE)
    await _wait_until(lambda: len(_reports(bus)) == 2)

    woken = _texts(llm.seen[-1])
    assert f"[计时] {_TIMER_DUE}。" in woken
    assert "看一眼周围" not in woken, "交付过的任务不再续，由到点开新任务"
    assert "[开局资料]" in woken, "新任务照常附上开局资料"
    assert _alerts(bus) == [], "计时到点是模型自己的事，不上报主播"
    await agent.stop()


async def test_a_due_timer_during_a_running_task_is_a_plain_notice() -> None:
    """任务进行中计时到点：在原任务里叫醒模型，不开新任务；目标照常在跟。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 下砍树目标 -> 让出 -> 计时到点被叫醒后接着让出 -> 砍树结束交付。
        if "结束了" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "砍到 5 块原木了"})])
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等砍树"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: agent.batch_active is False and len(llm.seen) == 2)

    server.body_event("timer_due", _TIMER_DUE)
    await _wait_until(lambda: len(llm.seen) == 3 and agent.batch_active is False)
    woken = _texts(llm.seen[-1])
    assert f"[计时] {_TIMER_DUE}" in woken and "去砍点木头" in woken, "在原任务的历史里读到"
    assert agent.observation()["task_instructions"] == ["去砍点木头"], "到点不记成原始指令"
    assert agent.tracked_goal_ids == [1]

    server.finish(1, summary="砍了 5 块原木")
    await _wait_until(lambda: len(_reports(bus)) == 1)
    await agent.stop()


async def test_a_due_timer_resumes_a_task_suspended_for_the_streamer() -> None:
    """上报主播挂起后计时到点：模型自己要的叫醒，接着原任务做（历史还在），不当成新指令记进原始指令。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if "[计时]" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "铁锭拿到了"})])
        return _resp(calls=[_call("minecraft_report", {"kind": "escalation", "content": "缺燃料，要不要去砍树"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="烧点铁锭", task_id="d-1")
    await _wait_until(lambda: agent.observation()["task_suspended"] and agent.batch_active is False)

    server.body_event("timer_due", _TIMER_DUE)
    await _wait_until(lambda: len(_reports(bus)) == 2)
    woken = _texts(llm.seen[-1])
    assert "烧点铁锭" in woken and f"[计时] {_TIMER_DUE}" in woken
    assert agent.observation()["task_instructions"] == ["烧点铁锭"]
    await agent.stop()


async def test_a_timer_due_while_the_task_is_being_delivered_still_wakes() -> None:
    """到点那一刻任务还在跑、模型这一步刚好交付：这条叫醒不能留在队列里没人读，交付后由它开新任务。"""
    server = FakeMaicraft()
    holder: Dict[str, MinecraftAgent] = {}

    def script(messages: List[Dict[str, Any]]) -> Response:
        if "[计时]" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "炉子烧好了"})])
        # 模型还在想这一步时计时到点（任务进行中），这一步随即交付。
        # script 是同步回调，用事件真实入口的协程排到本步之后立刻执行。
        asyncio.get_running_loop().create_task(
            holder["agent"]._on_body_event({"kind": "timer_due", "message": _TIMER_DUE, "cursor": 1})
        )
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    holder["agent"] = agent
    agent.receive_delegation(instruction="看一眼周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 2)
    assert f"[计时] {_TIMER_DUE}" in _texts(llm.seen[-1])
    assert not agent.queued_messages
    await agent.stop()


async def test_a_due_timer_is_dropped_while_off_air() -> None:
    """收工态到点不排叫醒：下一场开播时冒出上一场的提醒只会让模型去看过时的东西。"""
    agent, llm, _bus = await make_minecraft_agent(FakeMaicraft(), lambda messages: _resp("好"), live=False)
    await agent._on_body_event({"kind": "timer_due", "message": _TIMER_DUE, "cursor": 1})
    assert not agent.queued_messages and llm.seen == []
    await agent.stop()
