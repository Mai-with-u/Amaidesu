"""本文件测 MinecraftAgent 的目标生命周期与账本主题：后台目标唤醒与折叠证据、轮询并入等待、提问唤醒、目标镜像进共享任务账本、查询适配器、批次委派账与停滞告警。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, List
from unittest.mock import AsyncMock


from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.maicraft import GoalRun, MaicraftReply, goal_run_of, ledger_task_id, reply_of
from src.agents.minecraft.tools import MaicraftMcpProvider
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.llm.payload import Response
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger, TaskTracker

from .minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _called,
    _reports,
    _resp,
    _texts,
    _wait_until,
    assemble_minecraft_agent,
    make_minecraft_agent,
)


async def test_a_background_goal_wakes_the_task_with_its_result() -> None:
    """要动手的目标在后台跑：模型让出后零推理等待，目标结束时带着结果唤醒，再交付。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        text = _texts(messages)
        if "结束了" in text:
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "砍到 5 块原木了"})])
        if any(
            message.get("role") == "tool"
            and "goal_id" in str(message.get("content"))
            and "running" in str(message.get("content"))
            for message in messages
        ):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等砍树"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: agent.batch_active is False and len(llm.seen) == 2)
    calls_while_waiting = len(llm.seen)
    await asyncio.sleep(0.2)
    assert len(llm.seen) == calls_while_waiting, "等待期间不调用模型"

    server.finish(1, summary="砍了 5 块原木")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    woken = _texts(llm.seen[-1])
    assert "目标 1" in woken and "done" in woken and "minecraft:oak_log" in woken
    # 结束事件没带自身摘要、结果也没折：通知里不凭空多出这两句。
    assert "结束时：" not in woken and "result.folded" not in woken
    assert agent.tracked_goal_ids == []
    await agent.stop()


async def test_the_finish_notice_carries_the_self_brief_and_how_to_read_folded_evidence() -> None:
    """目标结束的通知带上结束那一刻的自身摘要；证据折起来时说清怎么读回，模型按 path 读证据不算轮询。"""
    server = FakeMaicraft()
    brief = "在 minecraft:overworld 12, 64, -8；血 18，饱食 15；手上 minecraft:iron_axe"

    def script(messages: List[Dict[str, Any]]) -> Response:
        text = _texts(messages)
        if "被河挡住" in text:
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "绕过河砍到原木了"})])
        if "结束了" in text:
            # 读通知里说的折起来那段：试过的办法。
            return _resp(calls=[_call("maicraft_goal", {"operation": "get", "goal_id": 1, "path": "/attempts"})])
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等砍树"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: agent.batch_active is False and len(llm.seen) == 2)

    server.finish(1, self_brief=brief, attempts=[{"tried": "从北边靠近树", "result": "被河挡住"}])
    await _wait_until(lambda: len(_reports(bus)) == 1)

    notice = next(
        str(message["content"])
        for message in llm.seen[-1]
        if message.get("role") == "user" and "结束了" in str(message.get("content"))
    )
    assert f"结束时：{brief}。" in notice
    assert "result.folded" in notice and "maicraft_goal(operation=get, goal_id=1, path=" in notice
    assert "完整结果" not in notice and "被河挡住" not in notice, "折起来的证据不进通知"
    assert {"operation": "get", "goal_id": 1, "path": "/attempts"} in server.calls_to("goal")
    assert "只在查询还在跑的目标" not in _texts(llm.seen[-1])
    await agent.stop()


async def test_reading_folded_evidence_by_path_is_never_taken_for_polling() -> None:
    """带 path 的 get 读的是结束了的目标里折起来的证据：哪怕名单里还挂着这个编号，也不当成轮询并入等待。"""
    server = FakeMaicraft()
    agent, llm, bus = await make_minecraft_agent(server, lambda messages: _resp("好"))
    run = goal_run_of({"goal_id": 4, "ability": "maicraft:gather", "state": "running"})
    assert run is not None
    agent._track_goal(run)

    assert agent._polls_running_goal("maicraft_goal", {"operation": "get", "goal_id": 4})
    assert not agent._polls_running_goal("maicraft_goal", {"operation": "get", "goal_id": 4, "path": "/attempts"})
    await agent.stop()


async def test_delivery_is_refused_while_a_goal_is_still_running() -> None:
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if not _called(messages, "maicraft_execute"):
            return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather"}})])
        if not _called(messages, "minecraft_report"):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "做完了"})])
        return _resp(calls=[_call("minecraft_wait", {"reason": "等目标"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: len(llm.seen) >= 3)

    refused = [message for message in llm.seen[2] if message.get("role") == "tool"][-1]
    assert "还有 1 个目标在跑" in refused["content"]
    assert _reports(bus) == []
    await agent.stop()


async def test_a_question_wakes_the_task_and_waiting_is_refused_until_answered() -> None:
    """目标提问：唤醒任务并说清怎么回答；没回答之前不允许让出等待。"""
    server = FakeMaicraft()
    answered: List[bool] = []

    def script(messages: List[Dict[str, Any]]) -> Response:
        text = _texts(messages)
        if "结束了" in text:
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "存好了"})])
        if "在等你回答" in text and not answered:
            if any("等待不会推进" in str(message.get("content")) for message in messages):
                answered.append(True)
                return _resp(calls=[_call("maicraft_goal", {"operation": "answer", "goal_id": 1, "answer": "b6"})])
            return _resp(calls=[_call("minecraft_wait", {"reason": "先等等"})])
        if "goal_id" in text and "running" in text:
            return _resp(calls=[_call("minecraft_wait", {"reason": "等存东西"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "deposit"}})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="把东西存起来", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: agent.batch_active is False)
    server.ask(1)
    await _wait_until(lambda: bool(answered))
    await _wait_until(lambda: agent.batch_active is False)
    server.finish(1, summary="存进了屋里的箱子")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    asked = next(seen for seen in llm.seen if "在等你回答" in _texts(seen))
    assert "maicraft_goal(operation=answer, goal_id=1" in _texts(asked) and "b5" in _texts(asked)
    assert server.calls_to("goal")[-1]["operation"] in ("get", "answer")
    assert any(call.get("operation") == "answer" for call in server.calls_to("goal"))
    await agent.stop()


async def test_polling_a_running_goal_is_folded_into_waiting() -> None:
    """一整轮只在查还在跑的目标：先提醒，第二次直接替它让出，不再推理。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if not any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather"}})])
        return _resp(calls=[_call("maicraft_goal", {"operation": "get", "goal_id": 1})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: len(llm.seen) == 3 and agent.batch_active is False)
    await asyncio.sleep(0.2)

    assert len(llm.seen) == 3, "第二次轮询后并入等待"
    assert "只在查询还在跑的目标" in _texts(llm.seen[2])
    assert _reports(bus) == []
    await agent.stop()


async def test_goals_are_mirrored_into_the_shared_task_ledger() -> None:
    """后台目标同时记进通用账本：主播查任务看得到，结束时写终态。"""
    server = FakeMaicraft()
    ledger = TaskLedger()
    tracker = TaskTracker(ToolRegistry(), ledger)

    def script(messages: List[Dict[str, Any]]) -> Response:
        if "结束了" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "好了"})])
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather"}})])

    agent, llm, bus = await make_minecraft_agent(server, script, tracker=tracker)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: "maicraft-goal-1" in ledger)
    assert ledger.get("maicraft-goal-1").initiator == "minecraft"

    server.finish(1, status="partial", summary="只砍到 2 块")
    await _wait_until(lambda: len(_reports(bus)) == 1)
    assert "maicraft-goal-1" not in ledger, "结束后账本条目移除"
    await agent.stop()


async def test_execute_finishing_inline_still_lands_in_the_ledger() -> None:
    """execute 当场返回终态的目标也要记账：先登记再写终态，终态变化广播得出来。

    回归形态（场次40 goal-489/490）：当场完成的路径过去只记进最近结束清单，
    账本从未登记，终态写入落空，主播与任务面看不到任何 task.changed。
    """
    server = FakeMaicraft()
    ledger = TaskLedger()
    ledger_bus = SimpleNamespace(emit=AsyncMock())
    ledger._event_bus = ledger_bus
    tracker = TaskTracker(ToolRegistry(), ledger)

    def script(messages: List[Dict[str, Any]]) -> Response:
        if any(message.get("role") == "tool" and "记住了" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "记好了"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "remember", "parameters": {"name": "家"}}})])

    agent, llm, bus = await make_minecraft_agent(server, script, tracker=tracker)
    agent.receive_delegation(instruction="记一下家", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1)
    assert "maicraft-goal-1" not in ledger, "终态条目已按约定移除"

    changed = [
        call.args[1]
        for call in ledger_bus.emit.await_args_list
        if call.args and getattr(call.args[1], "task_id", "") == "maicraft-goal-1"
    ]
    assert changed and changed[0].status == "succeeded", "当场完成的终态广播 task.changed"
    assert agent.tracked_goal_ids == [], "当场完成的目标不进跟踪名单"
    await agent.stop()


def _goal_lookup_of(server: FakeMaicraft) -> Callable[[str, Dict[str, Any]], Awaitable[MaicraftReply]]:
    """替身服务器的宿主读取通道：和真实 Agent 一样经 invoke + reply_of 走。"""

    async def lookup(name: str, arguments: Dict[str, Any]) -> MaicraftReply:
        result = await server.invoke(ToolInvocation(tool_name=name, arguments=arguments))
        return reply_of(result)

    return lookup


async def test_goal_query_adapter_maps_goal_get_to_ledger_shapes() -> None:
    """查询适配器：goal(get) 的运行状态映射成账本词表；不是目标编号或查不到时无新事实。"""
    server = FakeMaicraft()
    await server.invoke(ToolInvocation(tool_name="maicraft_execute", arguments={"goal": {"ability": "gather"}}))
    provider = MaicraftMcpProvider(
        client=SimpleNamespace(), server_name="maicraft", goal_lookup=_goal_lookup_of(server)
    )

    running = await provider.query_task("maicraft-goal-1")
    assert running is not None and running["status"] == "running"

    server.ask(1)
    asked = await provider.query_task("maicraft-goal-1")
    assert asked is not None and asked["status"] == "waiting_for_decision"

    server.finish(1, status="partial", summary="只砍到 2 块")
    finished = await provider.query_task("maicraft-goal-1")
    assert finished is not None and finished["status"] == "failed", "部分完成按账本词表记失败"
    assert finished["summary"] == "只砍到 2 块"
    assert finished["snapshot"]["goal_id"] == 1

    assert await provider.query_task("deleg_1_1") is None, "委派编号不是目标编号"
    assert await provider.query_task("maicraft-goal-99") is None, "查不到的目标按无新事实处理"


async def test_tracker_fallback_verification_uses_the_goal_query_adapter() -> None:
    """跟踪循环的周期兜底：经适配器核实目标状态并写账，事件流通知缺了也能对上账。"""
    server = FakeMaicraft()
    await server.invoke(ToolInvocation(tool_name="maicraft_execute", arguments={"goal": {"ability": "gather"}}))
    provider = MaicraftMcpProvider(
        client=SimpleNamespace(), server_name="maicraft", goal_lookup=_goal_lookup_of(server)
    )
    # 未连真 server（setup 没跑）时工具缓存为空；归属映射按注册后的样子补一个
    # execute 的 spec。工具登记发生在连接态（生产侧已连接才有工具清单），故先标记连接。
    provider._specs = [
        ToolSpec(
            name="execute",
            description="execute",
            parameters_schema={"type": "object"},
            kind="sync",
            provider="maicraft",
        )
    ]
    provider.mark_connected()
    registry = ToolRegistry()
    registry.register_provider(provider)
    ledger = TaskLedger()
    tracker = TaskTracker(registry, ledger)
    ledger.register(
        task_id="maicraft-goal-1",
        provider="maicraft",
        tool="maicraft_execute",
        initiator="minecraft",
        executor="maicraft",
        source="provider",
    )

    await tracker.step()
    assert ledger.get("maicraft-goal-1").status == "running"

    server.finish(1, status="done", summary="砍了 5 块原木")
    await tracker.step()
    assert "maicraft-goal-1" not in ledger, "周期兜底核实出终态，条目移除"


async def test_a_failed_goal_settles_the_batch_delegations_with_a_dry_summary() -> None:
    """批级关联：批内目标终态直写本批委派账（干巴保底），LLM 后到的交付改不动状态。"""
    server = FakeMaicraft()
    ledger = TaskLedger()
    ledger_bus = SimpleNamespace(emit=AsyncMock())
    ledger._event_bus = ledger_bus
    tracker = TaskTracker(ToolRegistry(), ledger)

    def script(messages: List[Dict[str, Any]]) -> Response:
        if "目标 1" in _texts(messages) or "结束了" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "没做成，坑壁有缺口"})])
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather"}})])

    agent, llm, bus = await make_minecraft_agent(server, script, tracker=tracker)
    # 委派单由框架受理时登记（framework provider 的职责），这里按生产形态预登记
    ledger.register(
        task_id="d-1",
        provider="framework",
        tool="framework_delegate",
        initiator="streamer",
        executor="minecraft",
        source="agent",
    )
    agent.receive_delegation(instruction="去挖坑壁后面的矿", task_id="d-1")
    await _wait_until(lambda: "maicraft-goal-1" in ledger)
    await _wait_until(lambda: ledger.get("d-1") is not None and ledger.get("d-1").status == "running")

    server.finish(1, status="failed", summary="坑壁有缺口，够不着")
    await _wait_until(lambda: "maicraft-goal-1" not in ledger)
    await _wait_until(lambda: ledger.get("d-1") is None)

    changed = [
        call.args[1]
        for call in ledger_bus.emit.await_args_list
        if call.args and getattr(call.args[1], "task_id", "") == "d-1"
    ]
    assert changed and changed[0].status == "failed"
    assert "坑壁有缺口" in changed[0].summary, "保底摘要是干巴但真实的机器摘要"

    agent._delegations.finish("succeeded", summary="delivery: 没做成，坑壁有缺口")
    assert ledger.get("d-1") is None, "终态粘滞：LLM 后到的交付不复活也不回退账面状态"
    await agent.stop()


async def test_a_stalled_goal_lands_in_ledgers_and_reaches_the_streamer() -> None:
    """STALLED 消费：账本中间态（快照带停滞标记）+ 委派账打标 + 叙事转发；不唤醒模型。"""
    server = FakeMaicraft()
    ledger = TaskLedger()
    tracker = TaskTracker(ToolRegistry(), ledger)

    def script(messages: List[Dict[str, Any]]) -> Response:
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather"}})])

    agent, llm, bus = await make_minecraft_agent(server, script, tracker=tracker)
    ledger.register(
        task_id="d-1",
        provider="framework",
        tool="framework_delegate",
        initiator="streamer",
        executor="minecraft",
        source="agent",
    )
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: "maicraft-goal-1" in ledger and ledger.get("d-1") is not None)

    server._append("stalled", 1, "目标很久没有新事件")
    await _wait_until(
        lambda: any(
            isinstance(call.args[1], GamePayload) and call.args[1].event_type == "attention_required"
            for call in bus.emit.await_args_list
        )
    )
    attention = [
        call.args[1]
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "attention_required"
    ][-1]
    assert "停着没动静" in attention.message

    goal_record = ledger.get("maicraft-goal-1")
    assert goal_record is not None and goal_record.status == "running", "停滞是中间态不是终态"
    assert goal_record.snapshot.get("stalled") is True
    assert ledger.get("d-1").snapshot.get("stalled_goals", {}).get("1") == "maicraft:gather", "批级委派账同步打标"
    queued = "".join(str(item[-1]) for item in agent.queued_messages)
    assert "停着没动静" not in queued and "结束了" not in queued, "停滞不唤醒模型（模型侧由账本停滞告警兜底）"
    await agent.stop()


def _track_goal(agent: MinecraftAgent, goal_id: int) -> None:
    """把一个进行中的目标登记进跟踪名单（模拟 execute 已下达、目标还在跑）。"""
    agent._goals.track(
        GoalRun(
            goal_id=goal_id,
            ability="travel",
            purpose="回家",
            state="running",
            question=None,
            result=None,
            doing="",
            data={},
        )
    )


async def test_stall_alert_through_task_changed_wakes_tracked_goal() -> None:
    """停滞告警接缝：真事件总线 emit task.changed 驱动，在跟踪的目标收到注入并置位唤醒。"""
    bus = EventBus(enable_stats=False)
    agent = assemble_minecraft_agent(bus=bus, live=False)
    # 只挂订阅、不起 worker（start 尾部的订阅动作；避免 worker 竞争消费队列消息）
    agent._subscribe_task_wakeup()
    _track_goal(agent, goal_id=7)
    try:
        await bus.emit(
            CoreEvents.TASK_CHANGED,
            TaskChangedPayload(
                task_id=ledger_task_id(7),
                status="running",
                initiator="minecraft",
                summary="很久没挪窝了",
                alert=True,
            ),
            source="TaskLedger",
        )
        await _wait_until(lambda: bool(agent.queued_messages))
        injected = list(agent.queued_messages)
        assert len(injected) == 1 and injected[0][0] == ""
        assert "7 很久没有新进展" in injected[0][1], "注入停滞告警正文并点名目标"
        assert "maicraft_goal" in injected[0][1], "正文里给出去查目标的出口"
        assert agent._wake_event.is_set(), "唤醒事件置位"
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_task_changed_without_alert_or_from_other_initiator_stays_silent() -> None:
    """反例：非告警通知与别人的任务变化都不注入、不唤醒。"""
    bus = EventBus(enable_stats=False)
    agent = assemble_minecraft_agent(bus=bus, live=False)
    agent._subscribe_task_wakeup()
    _track_goal(agent, goal_id=7)
    try:
        # 自己的目标、但不是告警（普通状态变化走事件流送达，不重复叫醒）
        await bus.emit(
            CoreEvents.TASK_CHANGED,
            TaskChangedPayload(task_id=ledger_task_id(7), status="running", initiator="minecraft", alert=False),
            source="TaskLedger",
        )
        # 告警、但发起方是别人（只唤醒发起方）
        await bus.emit(
            CoreEvents.TASK_CHANGED,
            TaskChangedPayload(
                task_id=ledger_task_id(7), status="running", initiator="streamer", summary="很久没挪窝了", alert=True
            ),
            source="TaskLedger",
        )
        await bus.wait_until_drained()
        assert not agent.queued_messages, "两种情形都不注入"
        assert not agent._wake_event.is_set(), "两种情形都不唤醒"
    finally:
        await agent.stop()
        await bus.cleanup()
