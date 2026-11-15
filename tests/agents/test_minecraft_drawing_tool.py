"""本文件测 minecraft_drawing 工具的端到端行为：当场回执+后台生成+完成注入、
串行拒绝、收手取消静默、图纸在跑时不判停了没交付、失败通知、enabled 门控。

装配说明：主循环模型经 make_minecraft_agent 的剧本入口传入（ScriptLlm）；
图纸生成用的 minecraft_builder 档调用走同一个模型入口（ScriptLlm.generate
收到 profile kwarg），剧本按 profile 分流即可——生成替身只在需要"卡住生成"
时才单独注入（DrawingStub，经 agent._drawing_builder._llm 换接，测试专用）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from src.modules.llm.payload import Response

from .minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _reports,
    _resp,
    _texts,
    _wait_until,
    make_minecraft_agent,
    offline_config,
)

# builder 档回的最小合法 drawing JSON（过了替身 Mod 的 create 校验）
_DRAWING_JSON = {
    "name": "营地新房",
    "coordinate_system": "minecraft_y_up",
    "materials": {"Stone": {"block_id": "minecraft:polished_andesite"}},
    "objects": [{"name": "Floor", "type": "MESH", "location": [4.5, 0.5, 3], "dimensions": [9, 1, 7]}],
}


def _ok_drawing(name: str = "营地新房") -> Response:
    return Response(success=True, content=json.dumps(_DRAWING_JSON | {"name": name}, ensure_ascii=False))


def _drawing_called(messages: List[Dict[str, Any]]) -> bool:
    return any(
        call["function"]["name"] == "minecraft_drawing"
        for message in messages
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
    )


def _drawing_calls(messages: List[Dict[str, Any]]) -> int:
    return sum(
        1
        for message in messages
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
        if call["function"]["name"] == "minecraft_drawing"
    )


class DrawingStub:
    """minecraft_builder 档的生成替身：可编排回复序列或卡住（gate 未 set 前不返回）。"""

    def __init__(self, replies: Optional[List[Any]] = None, *, gate: Optional[asyncio.Event] = None) -> None:
        self.replies = list(replies or [])
        self.gate = gate
        self.prompts: List[str] = []

    async def generate(self, messages: Any, **kwargs: Any) -> Response:
        if (kwargs.get("profile") or "") != "minecraft_builder":
            raise AssertionError("生成替身只应收到 minecraft_builder 档的调用")
        self.prompts.append(json.dumps(messages, ensure_ascii=False))
        if self.gate is not None:
            await self.gate.wait()
        if not self.replies:
            return _ok_drawing()
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def install_gate(agent) -> asyncio.Event:
    """给 agent 的生成器换上带闸门的替身，返回闸门（set 放行）。"""
    gate = asyncio.Event()
    agent._drawing_builder._llm = DrawingStub(gate=gate)  # noqa: SLF001  # 测试注入受闸生成替身
    return gate


async def test_drawing_tool_end_to_end() -> None:
    """调 minecraft_drawing 当场回执 → 后台生成 → [系统] 通知带 design_id → 模型 build。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]], profile: Optional[str] = None) -> Any:
        if profile == "minecraft_builder":
            return _ok_drawing()
        text = _texts(messages)
        if "图纸已画好" in text:
            return _resp(
                calls=[
                    _call(
                        "maicraft_execute",
                        {"goal": {"ability": "build", "parameters": {"design_id": "design-1"}}},
                        call_id="b1",
                    )
                ]
            )
        if _drawing_called(messages):
            return _resp("等图纸通知")
        return _resp(calls=[_call("minecraft_drawing", {"purpose": "老房子旁边盖一栋新房，要比老房子好看"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="老房子旁边盖一栋新房，要比老房子好看", task_id="d-1")
    # 只等最终态（build 下达=全链路走完）：中间态（发 Mod、通知进历史）由最终态蕴含，
    # 分段轮询在满负载下会放大竞速窗口
    await _wait_until(lambda: server.design_seq >= 1, timeout=10)
    assert server.design_seq >= 1, "图纸已发给 Mod 校验收档"
    (
        await _wait_until(
            lambda: any(
                call.get("goal", {}).get("parameters", {}).get("design_id") == "design-1"
                for call in server.calls_to("execute")
            ),
            timeout=10,
        ),
        "模型按通知里的 design_id 下达 build",
    )
    await agent.stop()


async def test_second_drawing_call_is_rejected_while_first_is_running() -> None:
    """串行上限：上一张还在画，第二个调用当场拒绝并说明。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]], profile: Optional[str] = None) -> Any:
        if profile == "minecraft_builder":
            return _ok_drawing()
        if "还在画" in _texts(messages):
            return _resp("好，等第一张")
        return _resp(
            calls=[_call("minecraft_drawing", {"purpose": "再画一张"}, call_id=f"c{_drawing_calls(messages) + 1}")]
        )

    agent, llm, bus = await make_minecraft_agent(server, script)
    gate = install_gate(agent)
    agent.receive_delegation(instruction="画两张图纸", task_id="d-1")
    await _wait_until(lambda: agent._drawing_builder.in_progress(), timeout=5)
    await _wait_until(lambda: any("还在画" in _texts(seen) and _drawing_called(seen) for seen in llm.seen), timeout=5)
    gate.set()
    await agent.stop()


async def test_drawing_failure_notifies_with_fallback_hint() -> None:
    """生成/校验用尽：注入失败通知（带重试次数与出路），提示自由处置。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]], profile: Optional[str] = None) -> Any:
        if profile == "minecraft_builder":
            return Response(success=True, content="这不是JSON")
        if "图纸生成失败" in _texts(messages):
            return _resp("收到，我自己画")
        return _resp(calls=[_call("minecraft_drawing", {"purpose": "画个亭子"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="画个亭子", task_id="d-1")
    await _wait_until(lambda: any("图纸生成失败" in _texts(seen) for seen in llm.seen), timeout=5)
    failures = [_texts(seen) for seen in llm.seen if "图纸生成失败" in _texts(seen)]
    assert any("次" in t for t in failures), "失败通知带重试次数"
    await agent.stop()


async def test_release_cancels_pending_drawing_silently() -> None:
    """委派收手：生成被取消，不注入任何通知。

    装配带 TaskTracker（委派账本的 framework ledger 来源）：无账本时 mark_running
    不入终态追踪清单，cancel_task 找不到在办单——那是测试装配形态，非生产行为。
    """
    from src.modules.tools.registry import ToolRegistry
    from src.modules.tools.tasks import TaskLedger, TaskTracker

    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]], profile: Optional[str] = None) -> Any:
        if profile == "minecraft_builder":
            return _ok_drawing()
        if _drawing_called(messages):
            return _resp("等图纸通知")
        return _resp(calls=[_call("minecraft_drawing", {"purpose": "画个仓库"})])

    tracker = TaskTracker(ToolRegistry(), TaskLedger())
    agent, llm, bus = await make_minecraft_agent(server, script, tracker=tracker)
    gate = install_gate(agent)
    agent.receive_delegation(instruction="画个仓库", task_id="d-1")
    await _wait_until(lambda: agent._drawing_builder.in_progress(), timeout=5)
    assert agent.cancel_task(task_id="d-1", reason="主播撤单"), "在办委派可撤"
    gate.set()
    await asyncio.sleep(0.15)
    assert not agent._drawing_builder.in_progress(), "生成协程已被取消"
    assert not any("图纸已画好" in _texts(seen) for seen in llm.seen), "收手后结果静默丢弃"
    await agent.stop()
    await tracker.stop()


async def test_drawing_in_progress_does_not_count_as_undelivered() -> None:
    """图纸在跑时模型让出：不代发"停了没交付"，通知到达后被唤醒继续。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]], profile: Optional[str] = None) -> Any:
        if profile == "minecraft_builder":
            return _ok_drawing()
        if _drawing_called(messages):
            return _resp("图纸在画，我先歇会")
        return _resp(calls=[_call("minecraft_drawing", {"purpose": "画个哨塔"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    gate = install_gate(agent)
    agent.receive_delegation(instruction="画个哨塔", task_id="d-1")
    await _wait_until(lambda: agent._drawing_builder.in_progress(), timeout=5)
    await asyncio.sleep(0.15)
    assert _reports(bus) == [], "没有代发停了没交付"
    gate.set()
    await _wait_until(lambda: any("图纸已画好" in _texts(seen) for seen in llm.seen), timeout=5)
    await agent.stop()


async def test_enabled_false_removes_the_tool() -> None:
    """配置门控：drawing_builder.enabled=false 时工具不进模型列表。"""
    from src.agents.minecraft.config import MinecraftDrawingBuilderConfig

    server = FakeMaicraft()
    config = offline_config()
    config.drawing_builder = MinecraftDrawingBuilderConfig(enabled=False)
    agent, llm, bus = await make_minecraft_agent(
        server, lambda messages: Response(success=True, content="好"), config=config
    )
    specs = [spec.full_name for spec in agent._tool_provider.list_tools()]
    assert "minecraft_drawing" not in specs
    await agent.stop()
