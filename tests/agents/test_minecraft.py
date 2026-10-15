"""MinecraftAgent 测试：工具契约 / ReAct 循环 / 事件 / 装配 / 思考流"""

import asyncio
import json
from dataclasses import replace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.state import MinecraftAgentState
from src.agents.minecraft.tools import MinecraftToolProvider
from src.modules.agents.factory import instantiate_agent
from src.modules.events.event_bus import EventBus
from src.modules.events.payloads.agents import AgentRepliedPayload
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.tool_result import ToolResultPayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.mcp.config import McpServerConfig
from src.modules.tools.models import ResultBlock, ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry
from src.modules.vision.image_store import VisionImageStore, set_image_store


def _offline_config() -> MinecraftConfig:
    """不连私有 MCP 的配置：本机正开着 MaiCraft 时，测试也不能连上真实的 8766 端口。"""
    return MinecraftConfig(mcp=McpServerConfig(enabled=False, url="http://127.0.0.1:8766/mcp"))


def _invocation(name: str, arguments: dict) -> ToolInvocation:
    return ToolInvocation(tool_name=name, arguments=arguments, source="test")


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _resp(content: str = "", tool_calls: list | None = None) -> Response:
    return Response(success=True, content=content, tool_calls=tool_calls or [])


def _bus() -> MagicMock:
    bus = MagicMock()
    bus.emit = AsyncMock()
    return bus


def _llm(generate: Any) -> MagicMock:
    llm = MagicMock()
    llm.generate = generate
    return llm


def _game_events(bus: MagicMock, event_type: str) -> List[GamePayload]:
    """按发射顺序筛出指定类型的 GamePayload。"""
    return [
        c.args[1]
        for c in bus.emit.await_args_list
        if isinstance(c.args[1], GamePayload) and c.args[1].event_type == event_type
    ]


def _has_tool_message(messages: list[dict]) -> bool:
    return any(m.get("role") == "tool" for m in messages)


def _registry_with_own_tools(state: MinecraftAgentState | None = None, **kwargs: Any) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_provider(MinecraftToolProvider(state=state or MinecraftAgentState(), **kwargs))
    return reg


async def _wait_until(condition, timeout: float = 10.0, interval: float = 0.02) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(interval)
    raise TimeoutError("条件在时限内未成立")


@pytest.fixture
async def start_agent():
    """启动处于直播中的 Agent，测试结束统一 stop。"""
    started: list[MinecraftAgent] = []

    async def _start(llm: Any, *, bus: Any = None, registry: ToolRegistry | None = None, **kwargs: Any):
        agent = MinecraftAgent(
            _offline_config(), llm_manager=llm, event_bus=bus or _bus(), tool_registry=registry, **kwargs
        )
        await agent.start()
        agent._live_active = True
        started.append(agent)
        return agent

    yield _start
    for agent in started:
        await agent.stop()


# 工具契约（注册名 = minecraft_*）


async def test_todo_and_notebook_are_full_document_read_write() -> None:
    reg = _registry_with_own_tools()

    r = await reg.invoke(_invocation("minecraft_todo", {"action": "read"}))
    assert r.success and r.structured_content["todos"] == []
    await reg.invoke(_invocation("minecraft_todo", {"action": "write", "todos": [{"content": "a"}]}))
    two = [{"content": "b"}, {"content": "c", "status": "done"}]
    await reg.invoke(_invocation("minecraft_todo", {"action": "write", "todos": two}))
    r = await reg.invoke(_invocation("minecraft_todo", {"action": "read"}))
    assert [t["content"] for t in r.structured_content["todos"]] == ["b", "c"]

    r = await reg.invoke(_invocation("minecraft_notebook", {"action": "read"}))
    assert r.success and r.structured_content["content"] == ""
    await reg.invoke(_invocation("minecraft_notebook", {"action": "write", "content": "东侧 Y=12 有钻石"}))
    r = await reg.invoke(_invocation("minecraft_notebook", {"action": "read"}))
    assert r.structured_content["content"] == "东侧 Y=12 有钻石"


async def test_work_log_returns_todo_notebook_and_recent_reports_ring() -> None:
    state = MinecraftAgentState()
    reg = _registry_with_own_tools(state)
    state.add_report("delivery", "挖到钻石了！", "y=-12")
    state.set_notebook("矿脉在 Y=12")

    d = (await reg.invoke(_invocation("minecraft_get_work_log", {}))).structured_content
    assert d["recent_reports"] == [{"kind": "delivery", "content": "挖到钻石了！", "scene": "y=-12"}]
    assert d["notebook"] == "矿脉在 Y=12" and "todo" in d

    for i in range(15):
        state.add_report("delivery", f"上报 {i}")
    assert len(state.reports) == 10 and state.reports[-1]["content"] == "上报 14"


async def test_report_tool_validates_and_forwards_to_callback() -> None:
    received: list[tuple[str, str, str]] = []

    async def cb(kind: str, content: str, scene: str) -> None:
        received.append((kind, content, scene))

    reg = _registry_with_own_tools(report_callback=cb)
    r = await reg.invoke(_invocation("minecraft_report", {"kind": "delivery", "content": "全部挖完"}))
    assert r.structured_content["reported"] is True
    assert received == [("delivery", "全部挖完", "")]

    # kind 非法 / content 空白 → 内容级失败观察（LLM 读 error 自纠）
    r = await reg.invoke(_invocation("minecraft_report", {"kind": "chat", "content": "x"}))
    assert r.structured_content["success"] is False and "kind" in r.structured_content["error"]
    r = await reg.invoke(_invocation("minecraft_report", {"kind": "delivery", "content": "  "}))
    assert r.structured_content["success"] is False

    # 无 callback（脱离 Agent）：降级成功
    bare = _registry_with_own_tools()
    r = await bare.invoke(_invocation("minecraft_report", {"kind": "escalation", "content": "需要授权"}))
    assert r.success


async def test_report_gate_rejection_reason_reaches_llm() -> None:
    async def gate(kind: str, content: str, scene: str) -> str | None:
        return "仍有 1 个后台任务未决"

    reg = _registry_with_own_tools(report_callback=gate)
    r = await reg.invoke(_invocation("minecraft_report", {"kind": "delivery", "content": "提前交付"}))
    assert r.structured_content["success"] is False
    assert "仍有 1 个后台任务未决" in r.structured_content["error"]


# 事件 / ReAct 循环


async def test_emit_report_payload_and_snapshot() -> None:
    bus = _bus()
    agent = MinecraftAgent(_offline_config(), event_bus=bus)
    await agent._emit_report("delivery", "挖到钻石了，交付！", scene="y=-12")

    payload = bus.emit.await_args[0][1]
    assert (payload.game, payload.event_type, payload.report_kind) == ("minecraft", "report", "delivery")
    assert (payload.message, payload.scene) == ("挖到钻石了，交付！", "y=-12")
    assert agent.get_state_snapshot()["recent_reports"][0]["kind"] == "delivery"


async def test_natural_termination_falls_back_to_single_delivery_without_milestone(start_agent) -> None:
    """自然终止且未 report：兜底包装终止文本为一次 delivery；todo 转 done 不发 milestone。"""

    async def fake(messages, **kwargs):
        if not _has_tool_message(messages):
            done = {"action": "write", "todos": [{"content": "挖钻石", "status": "done"}]}
            return _resp(tool_calls=[_tool_call("minecraft_todo", done)])
        return _resp("钻石挖完了，共 5 颗。")

    bus = _bus()
    agent = await start_agent(_llm(fake), bus=bus, registry=ToolRegistry())
    agent.receive_prompt(content="挖 3 个钻石", source="test")
    await _wait_until(lambda: agent._task_finished and _game_events(bus, "report"))

    reports = _game_events(bus, "report")
    assert len(reports) == 1 and reports[0].report_kind == "delivery"
    assert "钻石挖完了" in reports[0].message
    assert _game_events(bus, "milestone") == []


async def test_report_delivery_stops_batch_after_same_round_tools(start_agent) -> None:
    calls = 0

    async def fake(messages, **kwargs):
        nonlocal calls
        calls += 1
        if not _has_tool_message(messages):
            return _resp(
                tool_calls=[
                    _tool_call("minecraft_report", {"kind": "delivery", "content": "任务完成汇报"}),
                    _tool_call("minecraft_notebook", {"action": "write", "content": "收尾笔记"}),
                ]
            )
        return _resp("不该再推理")

    bus = _bus()
    agent = await start_agent(_llm(fake), bus=bus, registry=ToolRegistry())
    agent.receive_prompt(content="干活", source="test")
    await _wait_until(lambda: calls == 1)
    await asyncio.sleep(0.15)

    assert calls == 1
    assert [r.report_kind for r in _game_events(bus, "report")] == ["delivery"]
    assert agent.get_state_snapshot()["notebook"] == "收尾笔记"


async def test_report_escalation_stops_and_waits_for_next_prompt(start_agent) -> None:
    escalate = _tool_call("minecraft_report", {"kind": "escalation", "content": "需要授权"})
    llm = _llm(AsyncMock(return_value=_resp(tool_calls=[escalate])))
    bus = _bus()
    agent = await start_agent(llm, bus=bus, registry=ToolRegistry())
    agent.receive_prompt(content="遇到困难的任务", source="test")
    await _wait_until(lambda: llm.generate.await_count == 1)
    await asyncio.sleep(0.15)
    assert llm.generate.await_count == 1
    assert [r.report_kind for r in _game_events(bus, "report")] == ["escalation"]

    resume = AsyncMock(return_value=_resp("收到授权，继续"))
    llm.generate = resume
    agent.receive_prompt(content="授权通过了，继续", source="test")
    await _wait_until(lambda: resume.await_count == 1)


async def test_continues_past_fifty_steps_until_delivery(start_agent) -> None:
    """连续记录施工进展超过五十轮后仍能交付，无需玩家追加继续指令。"""
    steps = [
        _resp(tool_calls=[_tool_call("minecraft_notebook", {"action": "write", "content": f"施工进展 {step}"})])
        for step in range(1, 61)
    ]
    llm = _llm(AsyncMock(side_effect=steps + [_resp("施工验收完成")]))
    bus = _bus()
    agent = await start_agent(llm, bus=bus, registry=ToolRegistry())
    agent.receive_prompt(content="完成施工并验收", source="test")
    await _wait_until(lambda: llm.generate.await_count == 61 and agent._task_finished)

    assert _game_events(bus, "attention_required") == []
    assert agent._task_steps == 61 and not agent._task_suspended
    assert agent.get_state_snapshot()["notebook"] == "施工进展 60"
    assert [r.report_kind for r in _game_events(bus, "report")] == ["delivery"]


async def test_tool_round_trip_keeps_openai_format_and_ids(start_agent) -> None:
    captured: list[list[dict]] = []

    async def fake(messages: list[dict], **kwargs: Any) -> Response:
        captured.append(messages.copy())
        if len(captured) > 1:
            return _resp("探索坐标已记录")
        write = _tool_call("minecraft_notebook", {"action": "write", "content": "坐标 (10,20)"}, "call_x")
        return _resp(tool_calls=[write])

    agent = await start_agent(_llm(fake), registry=ToolRegistry())
    agent.receive_prompt(content="探索东侧", source="test")
    await _wait_until(lambda: len(captured) >= 2)

    assistant = [m for m in captured[1] if m.get("role") == "assistant"]
    tools = [m for m in captured[1] if m.get("role") == "tool"]
    assert assistant[0]["tool_calls"][0]["id"] == "call_x"
    assert tools[0]["tool_call_id"] == "call_x" and "坐标 (10,20)" in tools[0]["content"]
    assert agent.get_state_snapshot()["notebook"] == "坐标 (10,20)"


def _single_tool_provider(tool: str, result: ToolExecutionResult) -> BaseToolProvider:
    class _Provider(BaseToolProvider):
        category = "game"
        name = "maicraft"

        def list_tools(self):
            schema = {"type": "object"}
            return [ToolSpec(name=tool, description=tool, parameters_schema=schema, kind="sync", provider="maicraft")]

        async def invoke(self, invocation: ToolInvocation):
            return replace(result, tool_name=invocation.tool_name)

    return _Provider()


async def test_mcp_tool_is_listed_and_invoked_via_registry(start_agent) -> None:
    registry = ToolRegistry()
    ok = ToolExecutionResult(tool_name="", success=True, structured_content={"ok": True, "view": "situation"})
    # MCP 透传工具默认只对主播可见，minecraft 需显式授权
    registry.register_provider(_single_tool_provider("perceive", ok), visible_to={"maicraft_perceive": ["minecraft"]})
    calls = 0

    async def fake(messages, **kwargs):
        nonlocal calls
        calls += 1
        if not _has_tool_message(messages):
            names = {t["name"] for t in kwargs.get("tools") or []}
            assert {"maicraft_perceive", "minecraft_todo"} <= names
            return _resp(tool_calls=[_tool_call("maicraft_perceive", {"view": "situation"})])
        return _resp("感知完成")

    agent = await start_agent(_llm(fake), registry=registry)
    agent.receive_prompt(content="看看周围", source="test")
    await _wait_until(lambda: calls == 2)


async def test_tool_failure_is_fed_back_as_observation(start_agent) -> None:
    registry = ToolRegistry()
    failed = ToolExecutionResult(tool_name="", success=False, error_message="world not loaded")
    registry.register_provider(_single_tool_provider("execute", failed))
    seen_failure: list[bool] = []

    async def fake(messages, **kwargs):
        if not _has_tool_message(messages):
            return _resp(tool_calls=[_tool_call("maicraft_execute", {"goal": "mine"})])
        tool_texts = [m.get("content", "") for m in messages if m.get("role") == "tool"]
        seen_failure.append(any("world not loaded" in text for text in tool_texts))
        return _resp("接受错误，尝试重试")

    agent = await start_agent(_llm(fake), registry=registry)
    agent.receive_prompt(content="挖矿", source="test")
    await _wait_until(lambda: len(seen_failure) >= 1)
    assert seen_failure == [True]


async def test_pause_freezes_loop_and_resume_continues(start_agent) -> None:
    call_count = 0

    async def fake(messages: list[dict], **kwargs: Any) -> Response:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.005)  # 让暂停命令能在下一次游戏行动前到达
        return _resp(tool_calls=[_tool_call("maicraft_perceive", {"view": "situation"})])

    async def observe(name: str, arguments: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        # 建材数量持续变化，任务保持推进而不是空转
        return {"inventory": {"minecraft:stone": call_count}}

    agent = await start_agent(_llm(fake))
    agent._execute_tool = AsyncMock(side_effect=observe)
    agent.receive_prompt(content="暂停任务", source="test")

    await _wait_until(lambda: call_count >= 1)
    await agent.pause()
    frozen = call_count
    await asyncio.sleep(0.3)
    assert call_count == frozen
    await agent.resume()
    await _wait_until(lambda: call_count > frozen)


async def test_agent_reusable_after_goal_completes(start_agent) -> None:
    calls = 0

    async def fake(messages, **kwargs):
        nonlocal calls
        calls += 1
        return _resp(f"汇报 {calls}")

    agent = await start_agent(_llm(fake))
    agent.receive_prompt(content="第一个任务", source="test")
    await _wait_until(lambda: calls == 1)
    agent.receive_prompt(content="第二个任务", source="test")
    await _wait_until(lambda: calls == 2)
    assert len(agent.get_state_snapshot()["recent_reports"]) >= 2


@pytest.mark.parametrize("error", [None, "rate limit"], ids=["no_llm", "llm_failure"])
async def test_unusable_llm_emits_single_error(start_agent, error: str | None) -> None:
    llm = None if error is None else _llm(AsyncMock(return_value=Response(success=False, error=error)))
    bus = _bus()
    agent = await start_agent(llm, bus=bus)
    agent.receive_prompt(content="挖钻石", source="test")
    await _wait_until(lambda: bool(_game_events(bus, "error")))

    errors = _game_events(bus, "error")
    assert len(errors) == 1 and (error or "") in errors[0].message


async def test_idle_costs_nothing(start_agent) -> None:
    llm = _llm(AsyncMock())
    await start_agent(llm)
    await asyncio.sleep(0.2)
    llm.generate.assert_not_awaited()


async def test_small_history_keeps_old_observations() -> None:
    """超过十条观察仍保留已发送消息，只有体积超预算才集中整理。"""
    agent = MinecraftAgent(_offline_config(), event_bus=MagicMock())
    messages: list[dict] = [{"role": "system", "content": "玩家任务"}]
    for i in range(12):
        call = {"id": f"c{i}", "type": "function", "function": {"name": "observe", "arguments": "{}"}}
        messages += [
            {"role": "assistant", "tool_calls": [call]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": f"obs-{i}"},
        ]
    before = [message.copy() for message in messages]
    assert await agent._prepare_context(messages, [])
    assert messages == before and agent._context_compactor.checkpoints == 0


async def test_multi_tool_calls_execute_without_false_delivery(start_agent) -> None:
    calls = 0

    async def fake(messages, **kwargs):
        nonlocal calls
        calls += 1
        if not _has_tool_message(messages):
            todo = {"action": "write", "todos": [{"content": "任务 B", "status": "in_progress"}]}
            return _resp(
                tool_calls=[
                    _tool_call("minecraft_notebook", {"action": "write", "content": "笔记 A"}, "c1"),
                    _tool_call("minecraft_todo", todo, "c2"),
                ]
            )
        return _resp("done")

    agent = await start_agent(_llm(fake), registry=ToolRegistry())
    agent.receive_prompt(content="批量任务", source="test")
    await _wait_until(lambda: calls >= 2)

    snapshot = agent.get_state_snapshot()
    assert snapshot["notebook"] == "笔记 A"
    assert snapshot["todo"] == [{"content": "任务 B", "status": "in_progress"}]
    # 仍有施工待办：可以上报阻塞，但不能虚报交付
    assert all(report["kind"] == "escalation" for report in snapshot["recent_reports"])
    assert not agent._task_finished


async def test_prompt_during_task_reaches_next_inference(start_agent) -> None:
    seen: list[list[dict]] = []

    async def fake(messages, **kwargs):
        seen.append(messages.copy())
        return _resp()

    agent = await start_agent(_llm(fake))
    agent.receive_prompt(content="初始任务", source="test")
    await asyncio.sleep(0.15)
    agent.receive_prompt(content="追加指令", source="test")
    await _wait_until(lambda: len(seen) >= 2)
    assert any("追加指令" in m["content"] for m in seen[1] if m["role"] == "user")


async def test_unfinished_todos_block_natural_and_explicit_delivery(start_agent) -> None:
    """模型停下或声称完成，都不能把仍在施工的待办伪装成成功；真实完成后才可交付。"""
    in_progress = {"action": "write", "todos": [{"content": "施工", "status": "in_progress"}]}
    script = [_resp(tool_calls=[_tool_call("minecraft_todo", in_progress)]), _resp("完成了"), _resp("仍然没有实际动作")]
    agent = await start_agent(_llm(AsyncMock(side_effect=script)), registry=ToolRegistry())
    agent.receive_prompt(content="建好房屋", source="test")
    await _wait_until(lambda: agent._task_suspended)

    reports = agent.get_state_snapshot()["recent_reports"]
    assert reports and all(report["kind"] == "escalation" for report in reports)
    assert not agent._task_finished
    assert "未完成待办" in await agent._handle_report("delivery", "已建好", "")
    agent._mc_state.set_todos([{"content": "施工", "status": "done"}])
    assert await agent._handle_report("delivery", "施工已验证完成", "") is None
    assert agent._task_finished


# 装配


def test_factory_rejects_legacy_game_name() -> None:
    """分类层已移除，旧注册名 "game" 不再可实例化。"""
    agent = instantiate_agent(
        "game", {}, llm_manager=None, prompt_manager=None, event_bus=MagicMock(), tool_registry=None
    )
    assert agent is None


class _FakeMcpClient:
    constructed: List["_FakeMcpClient"] = []

    def __init__(self, name: str, config: Any) -> None:
        self.name = name
        self.config = config
        _FakeMcpClient.constructed.append(self)

    async def connect(self) -> bool:
        return True

    async def close(self) -> None:
        pass


class _ZeroToolProvider:
    """McpToolProvider 替身：setup() 返回 0（连不上或 server 无工具）。"""

    category = "mcp"

    def __init__(self, *, client: Any, server_name: str, provider: str | None = None) -> None:
        self.client = client
        self.name = provider or server_name
        self.on_tools_refreshed: Any = None

    async def setup(self) -> int:
        return 0

    def list_tools(self):
        return []

    async def invoke(self, invocation: ToolInvocation) -> Any:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    def subscribe_task_notifications(self, callback) -> Any:
        return lambda: None

    async def close(self) -> None:
        await self.client.close()


class _RaisingProvider(_ZeroToolProvider):
    async def setup(self) -> int:
        raise ConnectionError("mock connect failure")


def _patch_mcp(monkeypatch: pytest.MonkeyPatch, provider_cls: type) -> None:
    _FakeMcpClient.constructed = []
    monkeypatch.setattr("src.modules.mcp.McpClient", _FakeMcpClient)
    monkeypatch.setattr("src.modules.mcp.McpToolProvider", provider_cls)
    monkeypatch.setattr("src.modules.mcp.client.McpClient", _FakeMcpClient)
    monkeypatch.setattr("src.modules.mcp.provider.McpToolProvider", provider_cls)


def _mcp_agent(enabled: bool, registry: ToolRegistry) -> MinecraftAgent:
    config = MinecraftConfig(mcp=McpServerConfig(enabled=enabled))
    return MinecraftAgent(config, llm_manager=MagicMock(), event_bus=_bus(), tool_registry=registry)


async def test_disabled_mcp_skips_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_mcp(monkeypatch, _ZeroToolProvider)
    registry = ToolRegistry()
    agent = _mcp_agent(False, registry)
    await agent.start()
    assert _FakeMcpClient.constructed == []
    assert registry.list_tools(provider="maicraft") == [] and agent._mcp_client is None
    await agent.stop()


@pytest.mark.parametrize("provider_cls", [_RaisingProvider, _ZeroToolProvider], ids=["setup_raises", "zero_tools"])
async def test_mcp_setup_failure_registers_degraded_and_retries(
    monkeypatch: pytest.MonkeyPatch, provider_cls: type
) -> None:
    """连接失败不阻断启动：provider 以 0 工具降级登记（工具页可见），后台恢复循环重试；stop 摘除。"""
    _patch_mcp(monkeypatch, provider_cls)
    registry = ToolRegistry()
    agent = _mcp_agent(True, registry)

    await agent.start()
    assert agent._running is True and agent._mcp_client is not None
    assert registry.list_tools(provider="maicraft") == []
    assert {r["name"]: r for r in registry.list_providers()}["maicraft"]["tool_count"] == 0
    assert agent._mcp_recover_task is not None
    await agent.stop()
    assert agent._mcp_recover_task is None
    assert all(r["name"] != "maicraft" for r in registry.list_providers())


# 思考流 sink / 响应事实


class _RecordingSink:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def on_thinking_delta(self, *, round_id: str, phase: str, step: int, seq: int, text_delta: str) -> None:
        self.calls.append({"round_id": round_id, "phase": phase, "step": step, "seq": seq, "text_delta": text_delta})


class _CapturingToolProvider(BaseToolProvider):
    category = "minecraft"
    name = "minecraft"

    def __init__(self) -> None:
        self.invocations: List[ToolInvocation] = []

    def list_tools(self) -> List[ToolSpec]:
        schema = {"type": "object"}
        return [
            ToolSpec(name="capture", description="cap", parameters_schema=schema, kind="sync", provider="minecraft")
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.invocations.append(invocation)
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content={"ok": True})


async def _run_capture_task(start_agent, fake: Any, sink: Any, bus: Any = None) -> _CapturingToolProvider:
    """先调一次 minecraft_capture 再自然终止的两步任务。"""
    cap = _CapturingToolProvider()
    registry = ToolRegistry()
    registry.register_provider(cap)
    llm = _llm(AsyncMock(side_effect=fake))
    agent = await start_agent(llm, bus=bus, registry=registry, thinking_sink=sink)
    agent.receive_prompt(content="两步", source="test")
    await _wait_until(lambda: llm.generate.await_count == 2 and agent._task_finished)
    return cap


async def test_thinking_sink_receives_reasoning_and_round_id_reaches_tools(start_agent) -> None:
    """reasoning 增量按步进 sink（phase=minecraft，seq 单调），content 增量不转发；工具调用带同一 round_id。"""
    sink = _RecordingSink()

    async def fake(messages, **kwargs):
        on_delta = kwargs["on_delta"]
        if not _has_tool_message(messages):
            on_delta("reasoning", "step1 A")
            on_delta("content", "step1 content x")
            on_delta("reasoning", "step1 B")
            return _resp(tool_calls=[_tool_call("minecraft_capture", {"v": 1}, "c1")])
        on_delta("reasoning", "step2")
        return _resp("done")

    cap = await _run_capture_task(start_agent, fake, sink)

    assert {c["text_delta"]: c["step"] for c in sink.calls} == {"step1 A": 1, "step1 B": 1, "step2": 2}
    (rid,) = {c["round_id"] for c in sink.calls}
    assert rid.startswith("mc_") and len(rid) == 3 + 12
    assert {c["phase"] for c in sink.calls} == {"minecraft"}
    seqs = [c["seq"] for c in sink.calls]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert cap.invocations[0].round_id == rid


async def test_without_sink_no_delta_callback_and_empty_round_id(start_agent) -> None:
    async def fake(messages, **kwargs):
        assert kwargs.get("on_delta") is None
        if not _has_tool_message(messages):
            return _resp(tool_calls=[_tool_call("minecraft_capture", {"k": "v"}, "c1")])
        return _resp("done")

    cap = await _run_capture_task(start_agent, fake, None)
    assert cap.invocations[0].round_id == ""


async def test_agent_replied_only_for_intermediate_steps(start_agent) -> None:
    """中间工具步骤的非空正文发 agent.replied；终止轮正文走 game.report，不重复发。"""

    async def fake(messages, **kwargs):
        if not _has_tool_message(messages):
            return _resp("目标解释：用切石机把石头加工成石砖", tool_calls=[_tool_call("minecraft_capture", {"v": 1})])
        return _resp("任务完成，石砖已交付")

    bus = _bus()
    await _run_capture_task(start_agent, fake, _RecordingSink(), bus=bus)

    replied = [c.args[1] for c in bus.emit.await_args_list if isinstance(c.args[1], AgentRepliedPayload)]
    assert len(replied) == 1
    assert (replied[0].agent, replied[0].step) == ("minecraft", 1)
    assert replied[0].content == "目标解释：用切石机把石头加工成石砖"
    assert replied[0].round_id.startswith("mc_")


# _execute_tool


async def test_own_tool_invoke_emits_tool_result_event() -> None:
    bus = EventBus(enable_stats=False)
    received: list[tuple[str, str]] = []

    async def _on_result(event_name: str, payload: ToolResultPayload, source: str) -> None:
        received.append((event_name, payload.status))

    bus.on("tool.result.#", _on_result, ToolResultPayload)
    registry = ToolRegistry(event_bus=bus)
    registry.register_provider(MinecraftToolProvider(state=MinecraftAgentState()))
    agent = MinecraftAgent(_offline_config(), llm_manager=MagicMock(), event_bus=MagicMock(), tool_registry=registry)
    try:
        obs = await agent._execute_tool("minecraft_todo", {"action": "read"})
        assert obs.get("tool") == "todo"
        await _wait_until(lambda: ("tool.result.minecraft_todo", "success") in received)
    finally:
        await bus.cleanup()


def _agent_with_result(result: ToolExecutionResult, *, visual: bool = False) -> MinecraftAgent:
    llm = MagicMock()
    llm.is_profile_visual = MagicMock(return_value=visual)
    agent = MinecraftAgent(_offline_config(), llm_manager=llm, event_bus=MagicMock(), tool_registry=MagicMock())
    agent._tool_registry.invoke = AsyncMock(return_value=result)
    return agent


async def test_execute_tool_forwards_caller_profile_and_hides_base64() -> None:
    image = "A" * 2048  # 纯 base64 字符集、超 1KB
    agent = _agent_with_result(
        ToolExecutionResult(tool_name="vision_look_at_screen", success=True, structured_content={"image": image})
    )
    observation = await agent._execute_tool("vision_look_at_screen", {"monitor_index": 0})

    assert observation["image"] == "[图像 1 KB 已省略]"
    invocation = agent._tool_registry.invoke.call_args[0][0]
    assert (invocation.caller_profile, invocation.source) == ("minecraft", "minecraft-react")


async def test_visual_caller_queues_image_as_tail_message() -> None:
    """视觉调用方：观察只留文字，图句柄挂到下一次推理请求尾部（不改写历史，前缀缓存不受影响）。"""
    store = VisionImageStore()
    set_image_store(store)
    image = "A" * 2048
    try:
        agent = _agent_with_result(
            ToolExecutionResult(
                tool_name="vision_look_at_screen",
                success=True,
                blocks=[ResultBlock(kind="image", data=image, mime_type="image/png")],
                structured_content={"text": "", "image": image},
            ),
            visual=True,
        )
        observation = await agent._execute_tool("vision_look_at_screen", {})

        assert image not in json.dumps(observation, ensure_ascii=False)
        tail = agent._tail_images.drain()
        refs = [part["ref"] for part in tail["content"] if part["type"] == "image_ref"]
        assert tail["role"] == "user" and len(refs) == 1
        assert store.get(refs[0]) == f"data:image/png;base64,{image}"
    finally:
        store.clear()


async def test_visual_caller_without_image_keeps_payload() -> None:
    result = ToolExecutionResult(tool_name="minecraft_glance", success=True, structured_content={"ok": True})
    agent = _agent_with_result(result, visual=True)
    assert await agent._execute_tool("minecraft_glance", {}) == {"ok": True}
    assert agent._tail_images.drain() is None
