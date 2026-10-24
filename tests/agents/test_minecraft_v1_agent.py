"""MinecraftAgent 对接 MaiCraft v1：开局资料、后台目标跟踪与唤醒、交付门禁、提问、轮询并入等待、看一眼。"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.glance import glance_brief, glance_scene
from src.agents.minecraft.goal_ledger import pause_notice
from src.agents.minecraft.maicraft import MaicraftReply, goal_run_of, reply_of
from src.agents.minecraft.tools import MaicraftMcpProvider
from src.modules.events.payloads.game import GamePayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.mcp.config import McpServerConfig
from src.modules.skills import SkillLibrary
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger, TaskTracker

from .minecraft_v1_fakes import FakeMaicraft

LlmScript = Callable[[List[Dict[str, Any]]], Response]


def _resp(content: str = "", calls: Optional[List[ToolCall]] = None) -> Response:
    return Response(success=True, content=content, tool_calls=calls or [])


def _call(name: str, arguments: Dict[str, Any], call_id: str = "c1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _texts(messages: List[Dict[str, Any]]) -> str:
    return "\n".join(str(message.get("content") or "") for message in messages)


async def _wait_until(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.02)
    raise TimeoutError("条件在时限内未成立")


class _Llm:
    """按剧本回复的模型替身：每次推理把当时的消息交给剧本，记下推理次数与看到的消息。"""

    def __init__(self, script: LlmScript) -> None:
        self.script = script
        self.seen: List[List[Dict[str, Any]]] = []
        self.tools_given: List[List[Dict[str, Any]]] = []

    async def generate(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Response:
        self.seen.append([dict(message) for message in messages])
        self.tools_given.append(list(kwargs.get("tools") or []))
        return self.script(messages)


async def _start(
    server: FakeMaicraft,
    script: LlmScript,
    *,
    tracker: Optional[TaskTracker] = None,
    skills: Optional[SkillLibrary] = None,
) -> tuple[MinecraftAgent, _Llm, MagicMock]:
    """启动一个接上 MaiCraft 替身的玩家：私有 MCP 不连真端口，provider 直接换成替身。"""
    registry = ToolRegistry()
    registry.register_provider(server, visible_to={spec.full_name: ["minecraft"] for spec in server.list_tools()})
    llm = _Llm(script)
    bus = MagicMock()
    bus.emit = AsyncMock()
    agent = MinecraftAgent(
        MinecraftConfig(mcp=McpServerConfig(enabled=False, url="http://127.0.0.1:8766/mcp"), events_wait_ms=1000),
        llm_manager=llm,
        event_bus=bus,
        tool_registry=registry,
        task_tracker=tracker,
        skill_library=skills,
    )
    agent._mcp_provider = server
    agent._mcp_client = SimpleNamespace(connected=True)
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    return agent, llm, bus


def _reports(bus: MagicMock) -> List[GamePayload]:
    return [
        call.args[1]
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "report"
    ]


def _called(messages: List[Dict[str, Any]], tool: str) -> bool:
    """模型在这段历史里调用过这个工具没有（开局资料是宿主消息，不含工具调用）。"""
    return any(
        call["function"]["name"] == tool
        for message in messages
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
    )


async def test_look_around_and_remember_a_place_end_to_end() -> None:
    """WP-2.6 验收：委派"看一眼周围并记住一个地点"，开局资料就位，记地点当场完成，交付一次。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if not any(message.get("role") == "tool" and "记住了" in str(message.get("content")) for message in messages):
            return _resp(
                "周围有牛和箱子，记下这里",
                [_call("maicraft_execute", {"goal": {"ability": "remember", "parameters": {"name": "家"}}})],
            )
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了，这里记成了家"})])

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="看一眼周围并记住一个地点", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    first = llm.seen[0]
    opening = [index for index, message in enumerate(first) if "[开局资料]" in str(message.get("content"))]
    assert opening, "新任务开局附上宿主代读的资料"
    message = first[opening[0]]
    assert message["role"] == "user" and "看一眼周围并记住一个地点" in str(first[opening[0] - 1]["content"])
    # 每项一段"### 调用名 参数"，读取顺序不变
    read_tools = [part["text"].lstrip("\n").split(" ")[1] for part in message["content"][1:]]
    assert read_tools == ["maicraft_observe", "maicraft_observe", "maicraft_lookup", "maicraft_goal"]
    # 官方 DeepSeek 核对工具调用 id、Gemini 3 核对签名：模型第一次响应前不能有宿主编出来的调用
    assert not any(m.get("role") == "assistant" or m.get("role") == "tool" for m in first)
    assert "minecraft:cow" in _texts(first) and "maicraft:remember" in _texts(first)
    assert all(tool["name"] != "maicraft_events" for tool in llm.tools_given[0]), "事件流不给模型"
    assert _reports(bus)[0].report_kind == "delivery"
    assert agent._goals.tracked_ids() == [], "当场完成的目标不跟踪"
    await agent.stop()


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

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent._goals.tracked_ids() == [1])
    await _wait_until(lambda: agent._batch_active is False and len(llm.seen) == 2)
    calls_while_waiting = len(llm.seen)
    await asyncio.sleep(0.2)
    assert len(llm.seen) == calls_while_waiting, "等待期间不调用模型"

    server.finish(1, summary="砍了 5 块原木")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    woken = _texts(llm.seen[-1])
    assert "目标 1" in woken and "done" in woken and "minecraft:oak_log" in woken
    # 结束事件没带自身摘要、结果也没折：通知里不凭空多出这两句。
    assert "结束时：" not in woken and "result.folded" not in woken
    assert agent._goals.tracked_ids() == []
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

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent._goals.tracked_ids() == [1])
    await _wait_until(lambda: agent._batch_active is False and len(llm.seen) == 2)

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
    agent, llm, bus = await _start(server, lambda messages: _resp("好"))
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

    agent, llm, bus = await _start(server, script)
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

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="把东西存起来", task_id="d-1")
    await _wait_until(lambda: agent._goals.tracked_ids() == [1])
    await _wait_until(lambda: agent._batch_active is False)
    server.ask(1)
    await _wait_until(lambda: bool(answered))
    await _wait_until(lambda: agent._batch_active is False)
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

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: len(llm.seen) == 3 and agent._batch_active is False)
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

    agent, llm, bus = await _start(server, script, tracker=tracker)
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

    agent, llm, bus = await _start(server, script, tracker=tracker)
    agent.receive_delegation(instruction="记一下家", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1)
    assert "maicraft-goal-1" not in ledger, "终态条目已按约定移除"

    changed = [
        call.args[1]
        for call in ledger_bus.emit.await_args_list
        if call.args and getattr(call.args[1], "task_id", "") == "maicraft-goal-1"
    ]
    assert changed and changed[0].status == "succeeded", "当场完成的终态广播 task.changed"
    assert agent._goals.tracked_ids() == [], "当场完成的目标不进跟踪名单"
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
    # 未连真 server（setup 没跑）时工具缓存为空；归属映射按注册后的样子补一个 execute 的 spec。
    provider._specs = [
        ToolSpec(
            name="execute",
            description="execute",
            parameters_schema={"type": "object"},
            kind="sync",
            provider="maicraft",
        )
    ]
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


async def test_glance_shows_body_nearby_things_and_running_goals() -> None:
    server = FakeMaicraft()
    agent, llm, bus = await _start(server, lambda messages: _resp("好"))
    run = goal_run_of(
        {"goal_id": 4, "ability": "maicraft:gather", "purpose": "砍树", "state": "running", "doing": "走向树"}
    )
    assert run is not None
    agent._track_goal(run)

    view = await agent._glance()

    assert view["body"]["position"] == [4, 64, -7] and view["body"]["held"] == "minecraft:stone_axe"
    assert view["inventory"] == ["minecraft:oak_log ×6"]
    assert [entity["type"] for entity in view["entities"]] == ["minecraft:cow"], "远处的苦力怕不算身边"
    assert view["distant_entities"] == [
        "minecraft:creeper ×1",
        "minecraft:squid ×30",
        "minecraft:bat ×12",
        "minecraft:player（Steve） ×1",
    ], "远处的按种类计数：逐只列出的与只计数的合在一起，敌对的在前，玩家带名字"
    assert view["facilities"][0]["block"] == "minecraft:chest"
    assert view["work"]["goals"] == [
        {"ability": "maicraft:gather", "purpose": "砍树", "state": "running", "doing": "走向树"}
    ]
    # 一句话现场排在最前面：主播的现场卡只放这一句白话，不带数字和坐标
    assert list(view)[:2] == ["tool", "brief"]
    assert view["brief"] == (
        "在主世界的森林。白天，晴。血是满的，有点饿。手上拿着石斧。背包里有oak_log。"
        "身边还有牛。附近有箱子。远处还有苦力怕、鱿鱼、蝙蝠等。手上在砍树，眼下走向树。"
    )
    await agent.stop()


def test_glance_brief_is_plain_speech_without_numbers() -> None:
    """一句话现场：敌对生物带方位排在前头，有名字的带名字，没连上游戏照实说，全程不带数字。"""
    view = {
        "body": {"dimension": "minecraft:the_nether", "health": 4, "food": 6, "in_water": True},
        "biome": "minecraft:soul_sand_valley",
        "inventory": ["create:wrench ×1", "minecraft:diamond_pickaxe ×1"],
        "entities": [
            {"type": "minecraft:skeleton", "direction": "左后方", "distance": 8, "hostile": True},
            {"type": "minecraft:wolf", "name": "小白", "direction": "右侧", "distance": 3},
        ],
        "facilities": [{"block": "minecraft:red_bed", "distance": 5}],
        "work": {"state": "卡住了，正等我拿主意", "todo": []},
    }
    brief = glance_brief(view)
    assert brief == (
        "在下界的灵魂沙峡谷，泡在水里。血快没了，很饿。背包里有create:wrench、钻石镐。"
        "身边有骷髅在左后方，是敌对的。身边还有狼（叫小白）。附近有床。手上：卡住了，正等我拿主意。"
    )
    assert not any(char.isdigit() for char in brief)
    assert glance_brief({"unavailable": "游戏连接还没就绪", "work": {"state": "空闲"}}) == "游戏连接还没就绪"


def test_glance_distant_entities_only_counts_what_is_known_to_be_far() -> None:
    """远处计数只收确实在远处的：没距离的不算，坏计数不算，远处什么都没有时不写这一栏。"""
    scene = {
        "entities": [
            {"type": "minecraft:zombie", "distance": 30, "hostile": True},
            {"type": "minecraft:zombie", "distance": 50, "hostile": True},
            {"type": "minecraft:sheep"},
            {"type": "minecraft:pig", "distance": 10},
        ],
        "distant_entities": {"minecraft:squid": 3, "minecraft:bat": 0, "minecraft:cod": "很多", "minecraft:fox": True},
    }
    view = glance_scene(MaicraftReply(ok=True, data=scene))
    assert view["distant_entities"] == ["minecraft:zombie ×2", "minecraft:squid ×3"]
    assert [entity["type"] for entity in view["entities"]] == ["minecraft:pig"]

    nearby_only = glance_scene(MaicraftReply(ok=True, data={"entities": [{"type": "minecraft:pig", "distance": 10}]}))
    assert "distant_entities" not in nearby_only, "远处没有东西不写这一栏，上游没给也不写"


async def test_character_death_tells_the_running_task_and_the_streamer() -> None:
    """角色死了：在跑的任务被唤醒，知道目标保留着、重生后接着做；同时上报主播。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等砍树"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent._goals.tracked_ids() == [1])
    await _wait_until(lambda: agent._batch_active is False and len(llm.seen) == 2)

    server.body_event("character_died", "角色死了，等重生；主任务停在原地，重生后接着做")
    await _wait_until(lambda: len(llm.seen) == 3)

    woken = _texts(llm.seen[-1])
    assert "[身体] 角色死了" in woken and "目标保留着" in woken
    alerts = [
        call.args[1]
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "attention_required"
    ]
    # 上报主播的是 Mod 原话（死亡属短事件直通；长排查文由素材调度器压缩）
    assert any("角色死了" in alert.message and "主任务停在原地" in alert.message for alert in alerts)
    assert agent._goals.tracked_ids() == [1], "死亡不结束目标"
    await agent.stop()


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

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent._goals.tracked_ids() == [1])
    await _wait_until(lambda: agent._batch_active is False and len(llm.seen) == 2)

    server.body_event("sky_changed", "天黑了，到了能睡的时间（夜晚，距天亮约 8 分钟；晴）")
    server.body_event("sky_changed", "开始下雨了（夜晚；雨）")
    await _wait_until(lambda: any("开始下雨了" in str(content) for _, content in agent._message_queue))
    await asyncio.sleep(0.2)
    assert len(llm.seen) == 2, "天色变了不唤醒等着目标的模型"
    assert len(agent._message_queue) == 1, "队列里只留最新一条天色"

    server.finish(1, summary="砍了 5 块原木")
    await _wait_until(lambda: len(_reports(bus)) == 1)
    woken = _texts(llm.seen[-1])
    assert "[天色] 开始下雨了（夜晚；雨）" in woken and "目标 1" in woken
    assert "[天色] 天黑了" not in woken, "被新天色换掉的那条不再带给模型"

    server.body_event("sky_changed", "天亮了，过了能睡的时间（白天；雨）")
    await _wait_until(lambda: any("天亮了" in record["message"] for record in agent._batch_body_events))
    assert not agent._message_queue, "任务交付后不挂天色，等到下个任务只会是过时的"
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

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="看一眼周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1 and agent._batch_active is False)
    assert agent._task_finished

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

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent._goals.tracked_ids() == [1])
    await _wait_until(lambda: agent._batch_active is False and len(llm.seen) == 2)

    server.body_event("timer_due", _TIMER_DUE)
    await _wait_until(lambda: len(llm.seen) == 3 and agent._batch_active is False)
    woken = _texts(llm.seen[-1])
    assert f"[计时] {_TIMER_DUE}" in woken and "去砍点木头" in woken, "在原任务的历史里读到"
    assert agent._task_instructions == ["去砍点木头"], "到点不记成原始指令"
    assert agent._goals.tracked_ids() == [1]

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

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="烧点铁锭", task_id="d-1")
    await _wait_until(lambda: agent._task_suspended and agent._batch_active is False)

    server.body_event("timer_due", _TIMER_DUE)
    await _wait_until(lambda: len(_reports(bus)) == 2)
    woken = _texts(llm.seen[-1])
    assert "烧点铁锭" in woken and f"[计时] {_TIMER_DUE}" in woken
    assert agent._task_instructions == ["烧点铁锭"]
    await agent.stop()


async def test_a_timer_due_while_the_task_is_being_delivered_still_wakes() -> None:
    """到点那一刻任务还在跑、模型这一步刚好交付：这条叫醒不能留在队列里没人读，交付后由它开新任务。"""
    server = FakeMaicraft()
    holder: Dict[str, MinecraftAgent] = {}

    def script(messages: List[Dict[str, Any]]) -> Response:
        if "[计时]" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "炉子烧好了"})])
        # 模型还在想这一步时计时到点（任务进行中），这一步随即交付。
        holder["agent"]._wake_for_timer(_TIMER_DUE)
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了"})])

    agent, llm, bus = await _start(server, script)
    holder["agent"] = agent
    agent.receive_delegation(instruction="看一眼周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 2)
    assert f"[计时] {_TIMER_DUE}" in _texts(llm.seen[-1])
    assert not agent._message_queue
    await agent.stop()


async def test_a_due_timer_is_dropped_while_off_air() -> None:
    """收工态到点不排叫醒：下一场开播时冒出上一场的提醒只会让模型去看过时的东西。"""
    agent, llm, _bus = await _start(FakeMaicraft(), lambda messages: _resp("好"))
    agent._live_active = False
    await agent._on_body_event({"kind": "timer_due", "message": _TIMER_DUE, "cursor": 1})
    assert not agent._message_queue and llm.seen == []
    await agent.stop()


# 饿了没吃上：每个来源没弄到的原因逐个列出，真实说明常有几千字
_HUNGRY_FAILURE = (
    "饿了但没吃上：还要 8 个能吃的（饿了，弄点吃的），问过各来源都拿不到；箱子：附近没有；"
    "打猎：附近没有动物；合成：身上没有材料；先回去继续干活，过一分钟再找吃的"
)


def _capture_injections(agent: MinecraftAgent) -> List[str]:
    """记下交给模型的通知正文，不真的叫醒模型；任务置为进行中，通知才会注入。"""
    captured: List[str] = []
    agent._inject_wakeup_message = captured.append  # type: ignore[method-assign]
    agent._task_finished = False
    return captured


def _need_unhandled(message: str, cursor: int) -> Dict[str, Any]:
    return {"kind": "need_unhandled", "message": message, "cursor": cursor}


async def test_a_repeated_need_unhandled_points_back_while_the_first_copy_is_in_history() -> None:
    """同样的没吃上说明再来一遍，前面那份还在历史里：只留一句指回去，不再整段抄。"""
    agent, _llm, _bus = await _start(FakeMaicraft(), lambda messages: _resp("好"))
    captured = _capture_injections(agent)

    await agent._on_body_event(_need_unhandled(_HUNGRY_FAILURE, 1))
    assert len(captured) == 1 and "箱子：附近没有" in captured[0], "第一次完整展示"

    # 第一份已经交给模型读过：它在历史里
    agent._messages.append({"role": "user", "content": captured[0]})
    await agent._on_body_event(_need_unhandled(_HUNGRY_FAILURE, 2))

    assert len(captured) == 2
    assert "箱子：附近没有" not in captured[1], captured[1]
    assert "饿了但没吃上" in captured[1] and "没有新变化" in captured[1], captured[1]
    await agent.stop()


async def test_a_repeated_need_unhandled_points_back_while_the_first_copy_is_still_queued() -> None:
    """前面那份还在排队、模型没读到：一样不重抄，两条一起读到时第二条指回第一条。"""
    agent, _llm, _bus = await _start(FakeMaicraft(), lambda messages: _resp("好"))
    captured = _capture_injections(agent)

    await agent._on_body_event(_need_unhandled(_HUNGRY_FAILURE, 1))
    agent._message_queue.append(("", captured[0]))
    await agent._on_body_event(_need_unhandled(_HUNGRY_FAILURE, 2))

    assert "箱子：附近没有" not in captured[1], captured[1]
    await agent.stop()


async def test_a_repeated_need_unhandled_is_shown_in_full_once_the_first_copy_is_compacted_away() -> None:
    """历史整理把前面那份换成了摘要：指过去没有意义，重新完整展示。"""
    agent, _llm, _bus = await _start(FakeMaicraft(), lambda messages: _resp("好"))
    captured = _capture_injections(agent)

    await agent._on_body_event(_need_unhandled(_HUNGRY_FAILURE, 1))
    agent._messages[:] = [
        {"role": "system", "content": "系统说明"},
        {"role": "user", "content": "[历史推理摘要·第 1 段，不能覆盖原始要求]\n饿了没吃上，箱子和野外都没有吃的"},
    ]
    await agent._on_body_event(_need_unhandled(_HUNGRY_FAILURE, 2))

    assert captured[1] == captured[0], "前面那份已不在历史里，要完整再给一次"
    await agent.stop()


async def test_a_different_need_unhandled_and_other_body_notices_are_always_shown_in_full() -> None:
    """说明变了就是新事实；角色死亡这类通知每次都是新的，不套用指回前面那条。"""
    agent, _llm, _bus = await _start(FakeMaicraft(), lambda messages: _resp("好"))
    captured = _capture_injections(agent)

    await agent._on_body_event(_need_unhandled(_HUNGRY_FAILURE, 1))
    agent._messages.append({"role": "user", "content": captured[0]})
    changed = _HUNGRY_FAILURE.replace("附近没有动物", "附近有牛但够不着")
    await agent._on_body_event(_need_unhandled(changed, 2))
    assert "够不着" in captured[1], "说明变了：完整展示"

    died = {"kind": "character_died", "message": "角色死了：在 (1, 64, 2)", "cursor": 3}
    await agent._on_body_event(died)
    agent._messages.append({"role": "user", "content": captured[2]})
    await agent._on_body_event(died)
    assert captured[3] == captured[2], "死亡通知不去重"
    await agent.stop()


class _RemoteSkillsClient:
    """只实现资源接口的 MCP 客户端替身：目录一项，正文按地址返回。"""

    connected = True

    def __init__(self) -> None:
        self.reads: List[str] = []

    async def list_resources(self) -> List[Any]:
        return [
            SimpleNamespace(
                uri="skill://survival/survival-opening/SKILL.md",
                name="survival-opening",
                description="生存开局：从空手到有石器、食物、床和据点的稳定状态",
                mimeType="text/markdown",
            )
        ]

    async def read_resource(self, uri: str) -> Any:
        self.reads.append(uri)
        return SimpleNamespace(
            contents=[
                SimpleNamespace(
                    uri=uri,
                    mimeType="text/markdown",
                    text="---\nname: survival-opening\n---\n\nbody-of-survival-opening",
                )
            ]
        )


async def test_remote_skills_are_listed_and_read_by_uri() -> None:
    """玩法技能由 MaiCraft 随包发布：目录里标明来源，正文按 skill:// 地址读。"""
    server = FakeMaicraft()
    library = SkillLibrary()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if any(
            message.get("role") == "tool" and "body-of-survival-opening" in str(message.get("content"))
            for message in messages
        ):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "读过远程技能了"})])
        return _resp(calls=[_call("minecraft_skill", {"name": "survival-opening"})])

    agent, llm, bus = await _start(server, script, skills=library)
    client = _RemoteSkillsClient()
    agent._mcp_client = client
    agent._skill_support._remote_skills_loaded = False
    agent.receive_delegation(instruction="先看看生存开局", task_id="d-remote")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    system = str(llm.seen[0][0]["content"])
    assert "survival-opening" in system
    assert "来自 MaiCraft" in system
    assert client.reads == ["skill://survival/survival-opening/SKILL.md"]
    await agent.stop()


async def test_skill_catalog_follows_the_abilities_the_mod_has(tmp_path: Path) -> None:
    """本地技能仍按能力清单筛（直播策略一般不用，机制保留）：用到的能力都在才进目录，为旧版 Mod 写的不进；正文按名读。"""
    for name, requires in (
        ("chop_trees", "abilities: [maicraft:gather]"),
        ("make_tools", "abilities: [maicraft:obtain]"),
        ("old_machines", "maicraft: [v0]"),
    ):
        (tmp_path / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: {name} guide\nagents: [minecraft]\ncategory: survival\n"
            f"requires:\n  {requires}\n---\nbody-of-{name}\n",
            encoding="utf-8",
        )
    library = SkillLibrary()
    library.register_scan_root(tmp_path)
    library.load_all()
    server = FakeMaicraft()  # 替身的能力清单只有 gather 与 remember

    def script(messages: List[Dict[str, Any]]) -> Response:
        if any(
            message.get("role") == "tool" and "body-of-chop_trees" in str(message.get("content"))
            for message in messages
        ):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "读过砍树的打法了"})])
        return _resp(calls=[_call("minecraft_skill", {"name": "chop_trees"})])

    agent, llm, bus = await _start(server, script, skills=library)
    agent.receive_delegation(instruction="先看看怎么砍树", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    system = str(llm.seen[0][0]["content"])
    assert "chop_trees" in system
    assert "make_tools" not in system, "Mod 没有 obtain，用到它的技能不进目录"
    assert "old_machines" not in system, "为旧版 Mod 写的技能不进目录"
    assert "minecraft_skill" in json.dumps(llm.tools_given[0]), "有技能库时提供按名读技能的工具"
    assert len(server.calls_to("lookup")) == 1, "能力清单每次连接只读一次，技能目录与开局资料共用"
    await agent.stop()


async def test_a_death_recovery_question_wakes_the_task_to_answer_it() -> None:
    """角色死了：死亡通知说明要回答死亡恢复决策，决策提问带着选项唤醒任务，模型按选项回答。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 下砍树目标 -> 等砍树 -> 角色死了被决策唤醒 -> 回答一次重生 -> 回去接着等砍树。
        # 答过就让出：决策提问一直留在历史里，若照着它一直答，替身模型与替身 Mod 都不让出事件循环，
        # 任务循环会空转到上下文超预算才停，测试等条件的轮询一直轮不上。
        if _called(messages, "maicraft_goal"):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等砍树"})])
        if "Mod 挂出的决策" in _texts(messages):
            return _resp(calls=[_call("maicraft_goal", {"operation": "answer", "goal_id": -1, "answer": "respawn"})])
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等砍树"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent._goals.tracked_ids() == [1])
    await _wait_until(lambda: agent._batch_active is False and len(llm.seen) == 2)

    server.die()
    await _wait_until(lambda: server.calls_to("goal") and server.calls_to("goal")[-1].get("operation") == "answer")

    woken = _texts(llm.seen[-1])
    assert "死亡恢复决策等你回答" in woken and "goal_id=-1" in woken and "respawn" in woken
    assert server.calls_to("goal")[-1] == {"operation": "answer", "goal_id": -1, "answer": "respawn"}
    assert agent._goals.tracked_ids() == [1], "决策不进跟踪名单，原来的目标还在跟"
    await agent.stop()


_ANSWER_RESPAWN = {"operation": "answer", "goal_id": -1, "answer": "respawn"}


async def test_a_death_decision_wakes_an_idle_agent_as_a_new_task() -> None:
    """任务交付后闲着时角色死了：死亡恢复决策照样叫醒模型，由它开一个新任务，角色不会一直停在死亡屏幕。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 看一眼周围交付 -> 闲着 -> 角色死了被决策叫醒（新任务）-> 回答重生 -> 交付。
        if _called(messages, "maicraft_goal"):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "复活了"})])
        if "Mod 挂出的决策" in _texts(messages):
            return _resp(calls=[_call("maicraft_goal", dict(_ANSWER_RESPAWN))])
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了"})])

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="看一眼周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1 and agent._batch_active is False)
    assert agent._task_finished

    server.die()
    await _wait_until(lambda: len(_reports(bus)) == 2)
    woken = _texts(llm.seen[-1])
    assert "Mod 挂出的决策" in woken and "goal_id=-1" in woken
    assert "看一眼周围" not in woken, "交付过的任务不再续，由决策开新任务"
    assert "[开局资料]" in woken, "新任务照常附上开局资料"
    assert server.calls_to("goal")[-1] == _ANSWER_RESPAWN
    await agent.stop()


async def test_a_death_decision_wakes_a_task_suspended_for_the_streamer() -> None:
    """上报主播挂起后角色死了：决策叫醒模型接着原任务答，不当成新指令；答完可以接着等主播拿主意。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 缺燃料上报主播挂起 -> 角色死了被决策叫醒（接着原任务）-> 回答重生 -> 再上报一次接着等主播。
        if _called(messages, "maicraft_goal"):
            return _resp(calls=[_call("minecraft_report", {"kind": "escalation", "content": "复活了，还是缺燃料"})])
        if "Mod 挂出的决策" in _texts(messages):
            return _resp(calls=[_call("maicraft_goal", dict(_ANSWER_RESPAWN))])
        return _resp(calls=[_call("minecraft_report", {"kind": "escalation", "content": "缺燃料，要不要去砍树"})])

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="烧点铁锭", task_id="d-1")
    await _wait_until(lambda: agent._task_suspended and agent._batch_active is False)

    server.die()
    await _wait_until(lambda: len(_reports(bus)) == 2)
    woken = _texts(llm.seen[-1])
    assert "烧点铁锭" in woken and "死亡恢复决策等你回答" in woken and "Mod 挂出的决策" in woken
    assert server.calls_to("goal")[-1] == _ANSWER_RESPAWN
    assert agent._task_instructions == ["烧点铁锭"], "决策不记成原始指令"
    assert agent._task_suspended, "答完再上报就接着等主播"
    await agent.stop()


async def test_a_death_while_the_task_is_being_delivered_still_gets_answered() -> None:
    """模型交付的那一步角色死了：决策不管排在交付前还是交付后，都不能留在队列里没人读，交付后由它开新任务。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if _called(messages, "maicraft_goal"):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "复活了"})])
        if "Mod 挂出的决策" in _texts(messages):
            return _resp(calls=[_call("maicraft_goal", dict(_ANSWER_RESPAWN))])
        # 模型还在想这一步时角色死了（任务进行中），这一步随即交付；只死一次。
        if not any(goal_id < 0 for goal_id in server.goals):
            server.die()
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了"})])

    agent, llm, bus = await _start(server, script)
    # 目标跟踪第一次读事件流只建游标、不补发之前的事件：等它读过一次（发出第二次读取）再派活，
    # 不然第一步就死的事件会被当成接手前的旧事件跳过。
    await _wait_until(lambda: len(server.calls_to("events")) >= 2)
    agent.receive_delegation(instruction="看一眼周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 2)
    assert "Mod 挂出的决策" in _texts(llm.seen[-1])
    assert server.calls_to("goal")[-1] == _ANSWER_RESPAWN
    assert not agent._message_queue
    await agent.stop()


async def test_a_death_decision_is_not_queued_while_off_air() -> None:
    """收工态不排决策叫醒：下一场开播时冒出上一场的死亡决策只会让模型去答过时的问题。"""
    agent, llm, _bus = await _start(FakeMaicraft(), lambda messages: _resp("好"))
    agent._live_active = False
    decision = goal_run_of(
        {"goal_id": -1, "ability": "death_recovery", "state": "awaiting_answer", "question": {"text": "角色死了"}}
    )
    assert decision is not None
    await agent._on_decision_asked(decision)
    assert not agent._message_queue and llm.seen == []
    await agent.stop()


_ESCAPED = "已撤离，甩开了追兵（血快见底，先撤离保命），离打点 31 格；主任务停在半路等下一步指示"


def _pause_script(messages: List[Dict[str, Any]]) -> Response:
    """下达砍树后让出等待；收到暂停通知就上报主播停下，不在被暂停的目标上反复等。"""
    if "被暂停了" in _texts(messages):
        return _resp(calls=[_call("minecraft_report", {"kind": "escalation", "content": "主任务停在半路"})])
    if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
        return _resp(calls=[_call("minecraft_wait", {"reason": "等砍树"})])
    return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])


async def _paused_after(server: FakeMaicraft, doing: str) -> str:
    """砍树途中撤离：Mod 先发急事收尾，再把主任务挂成暂停；返回游戏 Agent 收到的暂停通知所在的那次推理。"""
    agent, llm, _bus = await _start(server, _pause_script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent._goals.tracked_ids() == [1])
    await _wait_until(lambda: agent._batch_active is False and len(llm.seen) == 2)

    server.body_event("temporary_task_finished", _ESCAPED)
    server.goals[1]["doing"] = doing
    server.pause(1)
    await _wait_until(lambda: any("被暂停了" in _texts(seen) for seen in llm.seen))
    await agent.stop()
    return next(_texts(seen) for seen in llm.seen if "被暂停了" in _texts(seen))


async def test_a_pause_after_escaping_carries_the_urgent_event_instead_of_a_reconnect_story() -> None:
    """10-15 直播：撤离后主任务被挂暂停，提示写死"重启游戏或重进世界后……"，模型四次以为游戏重连。
    Mod 没写暂停原因时，暂停通知带上暂停前那条急事收尾的话，不提重启和重进世界。"""
    woken = await _paused_after(FakeMaicraft(), "目标 maicraft:gather 已暂停（第 0 步）")

    assert "目标 1（maicraft:gather，砍树）被暂停了" in woken
    assert f"暂停前身体刚处理完一件急事：{_ESCAPED}" in woken
    assert "重进" not in woken and "重启" not in woken


async def test_a_pause_reason_written_by_the_mod_comes_first() -> None:
    """Mod 在 doing 里写了暂停原因就照抄它，不再拿身体急事兜底。"""
    doing = "目标 maicraft:gather 已暂停（第 0 步）：撤离后离打点 31 格，主任务停在半路"
    woken = await _paused_after(FakeMaicraft(), doing)

    assert f"Mod 说：{doing}" in woken
    assert "暂停前身体刚处理完" not in woken


async def test_only_a_changed_event_stream_is_called_reentering_the_world() -> None:
    """事件流换了编号后查到的暂停才说重进世界；隔久了的急事不拿来解释这次暂停，也只用一次。"""
    agent = MinecraftAgent(
        MinecraftConfig(mcp=McpServerConfig(enabled=False, url="http://127.0.0.1:8766/mcp")),
        llm_manager=MagicMock(),
    )
    run = goal_run_of(
        {"goal_id": 3, "ability": "maicraft:build", "state": "paused", "doing": "目标 maicraft:build 已暂停（第 2 步）"}
    )
    assert run is not None

    agent._urgent_before_pause = (_ESCAPED, time.monotonic())
    urgent, agent._urgent_before_pause = agent._urgent_before_pause, None
    reentered = pause_notice(run, True, urgent, time.monotonic())
    assert "游戏重进了世界或重启过" in reentered and "已撤离" not in reentered
    # 恢复结论前先核对工作笔记：东西可能已放置或已消耗，"背包里没有"不等于丢了。
    assert "minecraft_get_work_log" in reentered and "背包里没有不等于丢了" in reentered
    assert agent._urgent_before_pause is None, "急事用过一次就清掉"

    stale = pause_notice(run, False, (_ESCAPED, time.monotonic() - 120), time.monotonic())
    assert "Mod 没写暂停原因" in stale and "已撤离" not in stale and "重进" not in stale


async def test_a_delegation_is_refused_when_the_game_is_not_connected() -> None:
    """10-15 15:30 那场 maicraft 一个工具都没有，游戏 Agent 照样接了委派，拿截屏翻四块显示器找游戏窗口。
    现在直接拒收，原因原样回给派活的一方；连上（有工具）后照常接。"""
    agent = MinecraftAgent(
        MinecraftConfig(mcp=McpServerConfig(enabled=False, url="http://127.0.0.1:8766/mcp")),
        llm_manager=MagicMock(),
    )
    agent._live_active = True

    agent._mcp_provider = SimpleNamespace(list_tools=lambda: [])
    refused = agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    assert refused is not None and refused.startswith("游戏没连上")
    agent._mcp_provider = None
    assert (agent.receive_delegation(instruction="去砍点木头", task_id="d-2") or "").startswith("游戏没连上")
    assert len(agent._message_queue) == 0

    agent._mcp_provider = FakeMaicraft()
    assert agent.receive_delegation(instruction="去砍点木头", task_id="d-3") is None
    assert len(agent._message_queue) == 1


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

    agent, llm, bus = await _start(server, script, tracker=tracker)
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

    agent._finish_delegated("succeeded", summary="delivery: 没做成，坑壁有缺口")
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

    agent, llm, bus = await _start(server, script, tracker=tracker)
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
    queued = "".join(str(item[-1]) for item in agent._message_queue)
    assert "停着没动静" not in queued and "结束了" not in queued, "停滞不唤醒模型（模型侧由账本停滞告警兜底）"
    await agent.stop()


def _attention_events(bus: MagicMock) -> List[GamePayload]:
    return [
        call.args[1]
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "attention_required"
    ]


class _SummaryLlm:
    """summary 档替身：记下收到的档位与提示，按剧本回复或抛错。"""

    def __init__(self, behavior: Callable[[List[Dict[str, Any]]], Response]) -> None:
        self.behavior = behavior
        self.calls: List[Dict[str, Any]] = []

    async def generate(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Response:
        self.calls.append({"messages": messages, "profile": kwargs.get("profile")})
        return self.behavior(messages)


async def test_long_body_event_is_summarized_before_reaching_the_streamer() -> None:
    """长文概述器：超过阈值的长文经 summary 档压缩后两栏发出，短文直通不调 LLM。"""
    server = FakeMaicraft()
    llm = _SummaryLlm(lambda messages: _resp("饿了：翻过箱子没有、合成缺干海带块、烧炼缺原料"))
    agent, _llm, bus = await _start(server, lambda messages: _resp("好"))
    agent._llm = llm

    short = "吃了一个苹果"
    await agent._on_body_event({"kind": "need_unhandled", "message": short})
    long_text = "饿了没吃上。" + "翻过箱子没有面包，合成台缺干海带块，烧炼炉缺原料，64 格内没有可猎动物。" * 20
    await agent._on_body_event({"kind": "need_unhandled", "message": long_text})
    await _wait_until(lambda: len(_attention_events(bus)) == 2)

    assert llm.calls and llm.calls[0]["profile"] == "summary", "概述走 summary 档"
    assert "只压缩不评价" in llm.calls[0]["messages"][0]["content"], "提示词只压缩不评价"
    first, summarized = _attention_events(bus)
    assert first.message == short and first.scene == "", "短文直通，不调概述"
    assert "干海带块" in summarized.message, "概述保信息"
    assert summarized.scene.startswith("原文留档：") and long_text in summarized.scene, "原文留档进载荷"
    await agent.stop()

    # 白话化只改说法不去内容：各不相同的排查分句重写后仍超阈值，压缩兜底照常触发
    # 互不重复的排查分句（去重逻辑压不短），白话化后仍超 300 字阈值
    heterogeneous = "，".join(f"挖了第{i}层都没有找到铁矿，背包里只剩{i}块圆石" for i in range(30))
    await agent._on_body_event({"kind": "need_unhandled", "message": heterogeneous})
    await _wait_until(lambda: len(_attention_events(bus)) == 3)
    assert llm.calls and llm.calls[0]["profile"] == "summary", "白话化仍超阈值的长文走保信息摘要"
    third = _attention_events(bus)[2]
    assert third.scene.startswith("原文留档："), "留档进 scene"
    await agent.stop()


async def test_summary_failure_falls_back_to_first_sentence() -> None:
    """压缩兜底仍有效：素材调度器直接收到超阈值长文时走 summary 档，概述失败降级首句斩尾。"""
    server = FakeMaicraft()

    def boom(messages: List[Dict[str, Any]]) -> Response:
        raise RuntimeError("summary 引擎不可用")

    llm = _SummaryLlm(boom)
    agent, _llm, bus = await _start(server, lambda messages: _resp("好"))
    agent._llm = llm

    long_text = "饿了没吃上。翻过箱子没有面包，合成台缺干海带块，烧炼炉缺原料。" + "细节" * 200
    # 直呼调度器（白话速览异常超长时的兜底路径）
    agent._schedule_attention_material(long_text)
    await _wait_until(lambda: len(_attention_events(bus)) == 1)

    emitted = _attention_events(bus)[0]
    assert emitted.message.startswith("饿了没吃上。"), "降级为首句斩尾"
    assert "细节" not in emitted.message, "斩尾不带后半长文"
    assert long_text in emitted.scene, "原文留档仍在"
    await agent.stop()
