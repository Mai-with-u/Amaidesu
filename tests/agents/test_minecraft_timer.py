"""本文件测 MinecraftAgent 的计时到点与天色主题：计时到点按任务状态开新任务/普通通知/续挂起任务，天色变化只随下次唤醒捎带。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock


from src.agents.minecraft.agent import DELIVERY_REMINDER, DELIVERY_REMINDER_WHILE_WAITING, MinecraftAgent
from src.modules.events.payloads.game import GamePayload
from src.modules.llm.payload import Response
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger, TaskTracker

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
            return _resp("等目标事件通知")
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


async def test_a_task_woken_after_delivery_that_stops_unreported_leaves_the_delivery_alone() -> None:
    """交付后计时到点开的新任务，模型取完东西只说了句话就停：这一批不在做委派，不代发"停了没交付"，
    已经交付的那件委派账面不被改写。"""
    ledger_bus = SimpleNamespace(emit=AsyncMock())
    ledger = TaskLedger(event_bus=ledger_bus)
    ledger.register(
        task_id="d-1",
        provider="framework",
        tool="framework_delegate",
        initiator="streamer",
        executor="minecraft",
        source="agent",
        snapshot={"instruction": "烧点铁锭"},
    )
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 交付 -> 闲着 -> 计时到点被叫醒（新任务）-> 取完铁锭只说一句就停
        if "[计时]" in _texts(messages):
            return _resp("铁锭拿到了")
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "铁锭在炉子里烧着"})])

    agent, llm, bus = await make_minecraft_agent(server, script, tracker=TaskTracker(ToolRegistry(), ledger))
    agent.receive_delegation(instruction="烧点铁锭", task_id="d-1")
    await _wait_until(
        lambda: "d-1" not in ledger
        and agent.batch_active is False
        and _ledger_statuses(ledger_bus)[-1:] == ["succeeded"]
    )
    settled = _ledger_statuses(ledger_bus)

    server.body_event("timer_due", _TIMER_DUE)
    await _wait_until(lambda: len(llm.seen) == 2 and agent.batch_active is False)

    [delivery] = _reports(bus)
    assert (delivery.report_kind, delivery.delegation_ids) == ("delivery", ["d-1"])
    assert [call.args[1].status for call in ledger_bus.emit.await_args_list] == settled, "交付过的委派账面不再改"
    assert agent.observation()["task_finished"]
    await agent.stop()


async def test_a_due_timer_during_a_running_task_is_a_plain_notice() -> None:
    """任务进行中计时到点：在原任务里叫醒模型，不开新任务；目标照常在跟。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 下砍树目标 -> 让出 -> 计时到点被叫醒后接着让出 -> 砍树结束交付。
        if "结束了" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "砍到 5 块原木了"})])
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp("等目标事件通知")
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


def _ledger_statuses(ledger_bus: Any) -> List[str]:
    """已发出的账本变化事件里的状态，按发出先后。账本是在后台发这些事件的，断言前先等到想要的那条发出来。"""
    return [call.args[1].status for call in ledger_bus.emit.await_args_list]


def _escalated_ledger() -> tuple[TaskLedger, Any]:
    """登记一件她派的委派（d-1），返回账本与收 task.changed 的替身总线。"""
    ledger_bus = SimpleNamespace(emit=AsyncMock())
    ledger = TaskLedger(event_bus=ledger_bus)
    ledger.register(
        task_id="d-1",
        provider="framework",
        tool="framework_delegate",
        initiator="streamer",
        executor="minecraft",
        source="agent",
        snapshot={"instruction": "烧点铁锭"},
    )
    return ledger, ledger_bus


async def test_a_timer_after_escalation_that_stops_unreported_goes_back_to_waiting() -> None:
    """报卡住后计时到点、接着做的这一批提醒过还是没交付就停：问她的问题还没人答，重新挂回等定夺，
    账面回到 waiting_for_decision、不记 undelivered，也不再发上报（问题她已经收到过）。"""
    ledger, ledger_bus = _escalated_ledger()
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 报卡住 -> 计时到点被叫醒接着做 -> 只说一句就停 -> 被提醒 -> 还是只说一句
        if "[计时]" in _texts(messages):
            return _resp("炉子还在烧")
        return _resp(calls=[_call("minecraft_report", {"kind": "escalation", "content": "缺燃料，要不要去砍树"})])

    agent, llm, bus = await make_minecraft_agent(server, script, tracker=TaskTracker(ToolRegistry(), ledger))
    agent.receive_delegation(instruction="烧点铁锭", task_id="d-1")
    await _wait_until(lambda: agent.observation()["task_suspended"] and agent.batch_active is False)

    server.body_event("timer_due", _TIMER_DUE)
    await _wait_until(
        lambda: len(llm.seen) == 3
        and agent.batch_active is False
        and _ledger_statuses(ledger_bus)[-2:] == ["running", "waiting_for_decision"]
    )

    assert DELIVERY_REMINDER_WHILE_WAITING in _texts(llm.seen[-1]), "提醒里说明还在等回答就直接停"
    assert [report.report_kind for report in _reports(bus)] == ["escalation"], "不代发停了没交付，也不重报卡住"
    record = ledger.get("d-1")
    assert record is not None and record.status == "waiting_for_decision"
    assert record.snapshot["reason"] == "escalation: 缺燃料，要不要去砍树", "等的还是原来那个问题"
    last = ledger_bus.emit.await_args_list[-1].args[1]
    assert (last.status, last.summary) == ("waiting_for_decision", "escalation: 缺燃料，要不要去砍树")
    assert agent.observation()["task_suspended"]
    await agent.stop()


async def test_timers_stop_resuming_after_the_same_question_is_held_twice() -> None:
    """同一个问题没人答、接着做又挂回等定夺满两次后：计时到点不再续原任务（不再调模型），这条到点留在队列里，
    主播回答时一起读到。"""
    ledger, _ledger_bus = _escalated_ledger()
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 报卡住 -> 计时两次，每次只说一句、提醒后还是停 -> 第三次到点不再叫醒 -> 主播回答后交付
        if "去砍树吧" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "砍了树，铁锭烧好了"})])
        if "[计时]" in _texts(messages):
            return _resp("炉子还在烧")
        return _resp(calls=[_call("minecraft_report", {"kind": "escalation", "content": "缺燃料，要不要去砍树"})])

    agent, llm, bus = await make_minecraft_agent(server, script, tracker=TaskTracker(ToolRegistry(), ledger))
    agent.receive_delegation(instruction="烧点铁锭", task_id="d-1")
    await _wait_until(lambda: agent.observation()["task_suspended"] and agent.batch_active is False)
    for calls in (3, 5):
        server.body_event("timer_due", _TIMER_DUE)
        await _wait_until(
            lambda expected=calls: (
                len(llm.seen) == expected and agent.observation()["task_suspended"] and not agent.batch_active
            )
        )

    server.body_event("timer_due", "计时 4 到点：再看一眼炉子")
    await _wait_until(lambda: any("计时 4 到点" in str(item[1]) for item in agent.queued_messages))
    assert len(llm.seen) == 5 and agent.batch_active is False, "第三次到点不再续原任务"

    agent.receive_prompt(content="去砍树吧", source="test")
    await _wait_until(lambda: "d-1" not in ledger)
    assert "计时 4 到点" in _texts(llm.seen[-1]), "到点那条随主播的回答一起读到"
    assert [report.report_kind for report in _reports(bus)] == ["escalation", "delivery"]
    await agent.stop()


async def test_a_task_stopping_after_my_answer_is_undelivered_not_waiting() -> None:
    """报卡住后我回答了（递话），接着做的这一批提醒过还是没交付就停：问题已经答过，记停了没交付。"""
    ledger, _ledger_bus = _escalated_ledger()
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if "去砍树吧" in _texts(messages):
            return _resp("好的")
        return _resp(calls=[_call("minecraft_report", {"kind": "escalation", "content": "缺燃料，要不要去砍树"})])

    agent, llm, bus = await make_minecraft_agent(server, script, tracker=TaskTracker(ToolRegistry(), ledger))
    agent.receive_delegation(instruction="烧点铁锭", task_id="d-1")
    await _wait_until(lambda: agent.observation()["task_suspended"] and agent.batch_active is False)

    agent.receive_prompt(content="去砍树吧", source="test")
    await _wait_until(lambda: "d-1" not in ledger)

    assert DELIVERY_REMINDER in _texts(llm.seen[-1])
    assert [report.report_kind for report in _reports(bus)] == ["escalation", "undelivered"]
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
