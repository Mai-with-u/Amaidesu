"""D1 第 9 条：MinecraftAgent 端到端委派状态机测试（真实 MinecraftAgent worker + FakeMaicraft 假 Mod + TaskLedger + DelegationCore）。

覆盖清单要求的四个端到端场景：
1. 在做 X → 换成 U 且 X 先搁下 → U 交付 → X 按新任务带进度重新送达并做到终态 → 待办下一件 Y 送达；
2. 换活时新件被目标拒收（如游戏突断连接）：X 连同收手进度一并回滚恢复；
3. 撤一件待办不影响正在做的另一件（正在跑的游戏目标不被误停）；
4. 刚受理还没开跑的委派在换活时被搁下或撤掉，绝不会被并进新任务那一批误写成 succeeded。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.modules.agents.control import DelegationCore, build_agent_control_provider
from src.modules.agents.delegation_board import read_board
from src.modules.agents.manager import AgentManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.llm.payload import Response
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger, TaskTracker

from .minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _called,
    _resp,
    _texts,
    _wait_until,
    make_minecraft_agent,
)


async def _setup_e2e(
    server: FakeMaicraft, script: Any
) -> Tuple[Any, ToolRegistry, TaskLedger, EventBus, List[TaskChangedPayload]]:
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    ledger = TaskLedger(event_bus=bus)
    tracker = TaskTracker(registry, ledger, poll_interval_ms=20)
    changed: List[TaskChangedPayload] = []

    async def _on_changed(name: str, payload: TaskChangedPayload, source: str) -> None:
        del name, source
        changed.append(payload)

    bus.on(CoreEvents.TASK_CHANGED, _on_changed, model_class=TaskChangedPayload)

    agent, _llm, _ = await make_minecraft_agent(
        server,
        script,
        tracker=tracker,
        registry=registry,
        bus=bus,
    )
    manager = AgentManager()
    manager.register(agent)
    core = DelegationCore(manager, ledger, event_bus=bus)
    registry.register_provider(build_agent_control_provider(manager, ledger, event_bus=bus, delegation_core=core))
    return agent, registry, ledger, bus, changed


async def _invoke(registry: ToolRegistry, tool: str, args: Dict[str, Any]) -> Any:
    return await registry.invoke(ToolInvocation(tool_name=tool, arguments=args, source="planner-react"))


async def test_e2e_shelve_in_hand_for_urgent_then_redeliver_to_completion_and_drain_next() -> None:
    """在做 X（立起后台目标）→ 排待办 Y → 换成急件 U 且 X 先搁下（旧目标被取消、保留进度）→
    U 交付 → X 自动按新任务带之前进度重新送达并交付 → 待办 Y 自动送达并交付。"""
    server = FakeMaicraft()
    redelivered_prompts: List[str] = []

    def script(messages: List[Dict[str, Any]]) -> Response:
        text = _texts(messages)
        # 阶段 3：待办 Y「开一块菜地」送达后直接交付
        if "开一块菜地" in text:
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "菜地开好了"})])
        # 阶段 2：急件 U「跟玩家去看新家」送达后执行一步并交付
        if "跟玩家去看新家" in text:
            if "结束了" in text:
                return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "新家看完了"})])
            if _called(messages, "maicraft_execute"):
                return _resp("等跟随结束")
            return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "follow", "purpose": "跟着玩家甲"}})])
        # 阶段 1b：X「盖一座木屋」被搁下后按新任务重新送达（带「当时做到」）
        if "当时做到：" in text and "盖一座木屋" in text:
            redelivered_prompts.append(text)
            if "结束了" in text:
                return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "木屋全部盖好了"})])
            if _called(messages, "maicraft_execute"):
                return _resp("等盖房收尾")
            return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "build", "purpose": "补完木屋屋顶"}})])
        # 阶段 1a：X「盖一座木屋」首次送达，先记待办再起后台目标挂起
        if not _called(messages, "minecraft_todo"):
            return _resp(
                calls=[
                    _call(
                        "minecraft_todo",
                        {
                            "action": "write",
                            "todos": [
                                {"content": "铺好木屋地基", "status": "done"},
                                {"content": "搭木屋屋顶", "status": "in_progress"},
                            ],
                        },
                    )
                ]
            )
        if not _called(messages, "maicraft_execute"):
            return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "build", "purpose": "砌木屋四面墙"}})])
        return _resp("等砌墙目标完成")

    agent, registry, ledger, bus, changed = await make_minecraft_agent_e2e(server, script)
    try:
        # 1. 派 X「盖一座木屋」，等它记完待办并挂起后台目标 1
        res_x = await _invoke(registry, "framework_delegate", {"instruction": "盖一座木屋"})
        assert res_x.success
        id_x = res_x.structured_content["task_id"]
        await _wait_until(lambda: agent.tracked_goal_ids == [1] and not agent.batch_active)

        # 2. 派普通件 Y「开一块菜地」，排进待办1（还没开工）
        res_y = await _invoke(registry, "framework_delegate", {"instruction": "开一块菜地"})
        assert res_y.success and res_y.structured_content["queued"] is True
        id_y = res_y.structured_content["task_id"]

        # 3. 换成急件 U「跟玩家去看新家」，X 先搁下
        res_u = await _invoke(
            registry,
            "framework_delegate",
            {"instruction": "跟玩家去看新家", "when_busy": "replace", "current": "shelve"},
        )
        assert res_u.success and not res_u.structured_content.get("queued")
        id_u = res_u.structured_content["task_id"]

        # 断言：X 的在跑目标 1 已被直连取消，X 在待办最前且带已完成待办进度，Y 仍在待办后面
        assert {"operation": "cancel", "goal_id": 1} in server.calls_to("goal")
        board_mid = read_board(ledger, "minecraft")
        assert board_mid.in_hand is not None and board_mid.in_hand.task_id == id_u
        assert [item.task_id for item in board_mid.backlog] == [id_x, id_y]
        assert board_mid.backlog[0].shelved is True
        assert "铺好木屋地基" in board_mid.backlog[0].shelved_progress

        # 4. 推进 U 的跟随目标（goal 2）完成，让 U 正式交付
        await _wait_until(lambda: 2 in agent.tracked_goal_ids)
        server.finish(2, summary="到达河对岸新家")
        await _wait_until(lambda: any(p.task_id == id_u and p.status == "succeeded" for p in changed))

        # 5. U 终态后，搁下的 X 自动按新任务重新送达（挂出 goal 3），绝不卡死在假恢复态
        await _wait_until(lambda: 3 in agent.tracked_goal_ids)
        assert redelivered_prompts, "搁下的 X 应带进度按新任务重新送达"
        assert "铺好木屋地基" in redelivered_prompts[0]
        server.finish(3, summary="木屋屋顶盖好")
        await _wait_until(lambda: any(p.task_id == id_x and p.status == "succeeded" for p in changed))

        # 6. X 终态后，待办里的 Y「开一块菜地」自动送达并做到 succeeded
        await _wait_until(lambda: any(p.task_id == id_y and p.status == "succeeded" for p in changed))
        await bus.wait_until_drained()
        assert len(ledger) == 0
    finally:
        await agent.stop()


async def make_minecraft_agent_e2e(server: FakeMaicraft, script: Any):
    return await _setup_e2e(server, script)


async def test_e2e_rollback_when_replacement_delegation_is_refused() -> None:
    """换活时若新委派被目标拒收（模拟收手后 MCP 工具掉线导致新单被拒，随后重连，或目标拒收特定新单）：
    原委派不会丢失也不会悬置，连同收手进度一并回滚。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        text = _texts(messages)
        if "当时做到：" in text:
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "回滚后继续做完了"})])
        if _called(messages, "maicraft_execute"):
            return _resp("等目标")
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍橡木"}})])

    agent, registry, ledger, _bus, changed = await _setup_e2e(server, script)
    try:
        res_x = await _invoke(registry, "framework_delegate", {"instruction": "收集十根橡木"})
        id_x = res_x.structured_content["task_id"]
        await _wait_until(lambda: agent.tracked_goal_ids == [1] and not agent.batch_active)

        # 换单时让 receive_delegation 对含「断线重派」的新单拒收，但对原单「收集十根橡木」正常放行回滚
        orig_receive = agent.receive_delegation

        def selective_receive(*, instruction: str, task_id: str):
            if "坏单" in instruction:
                return "当前无法执行该指令"
            return orig_receive(instruction=instruction, task_id=task_id)

        agent.receive_delegation = selective_receive  # type: ignore[method-assign]
        refused = await _invoke(
            registry,
            "framework_delegate",
            {"instruction": "坏单", "when_busy": "replace", "current": "shelve"},
        )
        assert refused.success is False and "已送回继续执行" in refused.error_message
        board = read_board(ledger, "minecraft")
        assert board.in_hand is not None and board.in_hand.task_id == id_x

        # 回滚重送达的 X 带着「收手时停下：砍橡木」继续跑完并交付
        await _wait_until(lambda: any(p.task_id == id_x and p.status == "succeeded" for p in changed))
    finally:
        await agent.stop()


async def test_e2e_cancelling_backlog_item_does_not_stop_in_hand_goals() -> None:
    """审查 1.4 回归：撤掉待办里的一件，绝不能把当前【在做】正在跑的游戏目标一起停掉。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if "结束了" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "矿道挖通了"})])
        if _called(messages, "maicraft_execute"):
            return _resp("等挖矿目标")
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "mine", "purpose": "挖铁矿"}})])

    agent, registry, ledger, _bus, changed = await _setup_e2e(server, script)
    try:
        res_x = await _invoke(registry, "framework_delegate", {"instruction": "去矿洞挖铁矿"})
        id_x = res_x.structured_content["task_id"]
        await _wait_until(lambda: agent.tracked_goal_ids == [1] and not agent.batch_active)

        res_y = await _invoke(registry, "framework_delegate", {"instruction": "去补种蒲公英"})
        id_y = res_y.structured_content["task_id"]
        assert res_y.structured_content["item"] == "待办1"

        # 按短编号撤掉待办1：不调 Mod cancel，目标 1 仍在跑
        settled = await _invoke(registry, "framework_task_settle", {"item": "待办1", "outcome": "drop"})
        assert settled.success and settled.structured_content["task_id"] == id_y
        assert [c for c in server.calls_to("goal") if c.get("operation") == "cancel"] == [], (
            "撤待办件不取消正在跑的游戏目标"
        )
        assert agent.tracked_goal_ids == [1]

        server.finish(1, summary="挖到 12 块铁矿")
        await _wait_until(lambda: any(p.task_id == id_x and p.status == "succeeded" for p in changed))
        assert len(ledger) == 0
    finally:
        await agent.stop()


async def test_e2e_unstarted_delegation_is_never_swept_into_replacement_succeeded() -> None:
    """审查 1.3 回归：刚派下去还在消息队列里没开跑的单 X，立刻被换成 U（先搁下）——
    U 交付时绝不能把还没开跑的 X 一起写成 succeeded；U 完成后 X 才真正开跑并独立完成。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        text = _texts(messages)
        if "急事救火" in text:
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "火扑灭了"})])
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "慢活也做完了"})])

    agent, registry, ledger, _bus, changed = await _setup_e2e(server, script)
    try:
        # 先暂停 Agent 让 X 停在消息队列里尚未被 worker 吸收开跑
        await agent.pause()
        res_x = await _invoke(registry, "framework_delegate", {"instruction": "还没开跑的慢活"})
        id_x = res_x.structured_content["task_id"]

        res_u = await _invoke(
            registry,
            "framework_delegate",
            {"instruction": "急事救火", "when_busy": "replace", "current": "shelve"},
        )
        id_u = res_u.structured_content["task_id"]
        # 此时 U 在做，X 在待办1（还没开跑），绝未混在一起
        board = read_board(ledger, "minecraft")
        assert board.in_hand is not None and board.in_hand.task_id == id_u
        assert [b.task_id for b in board.backlog] == [id_x]

        # 恢复运行：U 先单独交付，那一刻 X 绝不能被提前写成 succeeded，随后 X 被重送达并独立交付
        await agent.resume()
        await _wait_until(lambda: any(p.task_id == id_u and p.status == "succeeded" for p in changed))
        await _wait_until(lambda: any(p.task_id == id_x and p.status == "succeeded" for p in changed))
        succeeded_order = [p.task_id for p in changed if p.status == "succeeded"]
        assert succeeded_order == [id_u, id_x]
    finally:
        await agent.stop()
