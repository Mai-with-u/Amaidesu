"""MinecraftAgent 对接 MaiCraft v1：开局资料、后台目标跟踪与唤醒、交付门禁、提问、轮询并入等待、看一眼。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.maicraft import goal_run_of
from src.modules.events.payloads.game import GamePayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.mcp.config import McpServerConfig
from src.modules.skills import SkillLibrary
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


async def _wait_until(condition: Callable[[], bool], timeout: float = 3.0) -> None:
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
    """模型在这段历史里调用过这个工具没有（宿主代读的开局资料不算）。"""
    return any(
        call["function"]["name"] == tool
        for message in messages
        if message.get("role") == "assistant" and "[开局资料]" not in str(message.get("content"))
        for call in message.get("tool_calls") or []
    )


@pytest.mark.asyncio
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
    opening = [
        message
        for message in first
        if message.get("role") == "assistant" and "[开局资料]" in str(message.get("content"))
    ]
    assert opening, "新任务开局附上宿主代读的资料"
    read_tools = [call["function"]["name"] for call in opening[0]["tool_calls"]]
    assert read_tools == ["maicraft_observe", "maicraft_observe", "maicraft_lookup", "maicraft_task"]
    assert "minecraft:cow" in _texts(first) and "maicraft:remember" in _texts(first)
    assert all(tool["name"] != "maicraft_events" for tool in llm.tools_given[0]), "事件流不给模型"
    assert _reports(bus)[0].report_kind == "delivery"
    assert agent._goals.tracked_ids() == [], "当场完成的目标不跟踪"
    await agent.stop()


@pytest.mark.asyncio
async def test_a_background_goal_wakes_the_task_with_its_full_result() -> None:
    """要动手的目标在后台跑：模型让出后零推理等待，目标结束时带着完整结果唤醒，再交付。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        text = _texts(messages)
        if "结束了" in text:
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "砍到 5 块原木了"})])
        if any(
            message.get("role") == "tool"
            and "task_id" in str(message.get("content"))
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
    assert agent._goals.tracked_ids() == []
    await agent.stop()


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
                return _resp(calls=[_call("maicraft_task", {"operation": "answer", "task_id": 1, "answer": "b6"})])
            return _resp(calls=[_call("minecraft_wait", {"reason": "先等等"})])
        if "task_id" in text and "running" in text:
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
    assert "maicraft_task(operation=answer, task_id=1" in _texts(asked) and "b5" in _texts(asked)
    assert server.calls_to("task")[-1]["operation"] in ("get", "answer")
    assert any(call.get("operation") == "answer" for call in server.calls_to("task"))
    await agent.stop()


@pytest.mark.asyncio
async def test_polling_a_running_goal_is_folded_into_waiting() -> None:
    """一整轮只在查还在跑的目标：先提醒，第二次直接替它让出，不再推理。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if not any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather"}})])
        return _resp(calls=[_call("maicraft_task", {"operation": "get", "task_id": 1})])

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: len(llm.seen) == 3 and agent._batch_active is False)
    await asyncio.sleep(0.2)

    assert len(llm.seen) == 3, "第二次轮询后并入等待"
    assert "只在查询还在跑的目标" in _texts(llm.seen[2])
    assert _reports(bus) == []
    await agent.stop()


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_glance_shows_body_nearby_things_and_running_goals() -> None:
    server = FakeMaicraft()
    agent, llm, bus = await _start(server, lambda messages: _resp("好"))
    run = goal_run_of(
        {"task_id": 4, "ability": "maicraft:gather", "purpose": "砍树", "state": "running", "doing": "走向树"}
    )
    assert run is not None
    agent._track_goal(run)

    view = await agent._glance()

    assert view["body"]["position"] == [4, 64, -7] and view["body"]["held"] == "minecraft:stone_axe"
    assert view["inventory"] == ["minecraft:oak_log ×6"]
    assert [entity["type"] for entity in view["entities"]] == ["minecraft:cow"], "远处的苦力怕不算身边"
    assert view["facilities"][0]["block"] == "minecraft:chest"
    assert view["work"]["goals"] == [
        {"ability": "maicraft:gather", "purpose": "砍树", "state": "running", "doing": "走向树"}
    ]
    await agent.stop()


@pytest.mark.asyncio
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
    assert any("角色死了" in alert.message for alert in alerts)
    assert agent._goals.tracked_ids() == [1], "死亡不结束目标"
    await agent.stop()


@pytest.mark.asyncio
async def test_skill_catalog_follows_the_abilities_the_mod_has(tmp_path: Path) -> None:
    """技能目录按 Mod 的能力清单筛：用到的能力都在才进目录，为旧版 Mod 写的不进；正文按名读。"""
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


@pytest.mark.asyncio
async def test_a_death_recovery_question_wakes_the_task_to_answer_it() -> None:
    """角色死了：死亡通知说明要回答死亡恢复决策，决策提问带着选项唤醒任务，模型按选项回答。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if "Mod 挂出的决策" in _texts(messages):
            return _resp(calls=[_call("maicraft_task", {"operation": "answer", "task_id": -1, "answer": "respawn"})])
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等砍树"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])

    agent, llm, bus = await _start(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent._goals.tracked_ids() == [1])
    await _wait_until(lambda: agent._batch_active is False and len(llm.seen) == 2)

    server.die()
    await _wait_until(lambda: server.calls_to("task") and server.calls_to("task")[-1].get("operation") == "answer")

    woken = _texts(llm.seen[-1])
    assert "死亡恢复决策等你回答" in woken and "task_id=-1" in woken and "respawn" in woken
    assert server.calls_to("task")[-1] == {"operation": "answer", "task_id": -1, "answer": "respawn"}
    assert agent._goals.tracked_ids() == [1], "决策不进跟踪名单，原来的目标还在跟"
    await agent.stop()
