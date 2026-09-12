"""MinecraftAgent 测试：工具契约 / ReAct 循环 / 事件 / send_prompt / handoff / 装配"""

import asyncio
from typing import Any, Dict, List, Optional

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.state import MinecraftAgentState
from src.agents.minecraft.tools import MinecraftToolProvider
from src.modules.events.payloads.game import GamePayload
from src.modules.llm.manager import LLMResponse
from src.modules.mcp.config import McpServerConfig
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry


def _make_state() -> MinecraftAgentState:
    return MinecraftAgentState()


def _make_provider(state: MinecraftAgentState) -> MinecraftToolProvider:
    return MinecraftToolProvider(state=state)


def _invocation(name: str, arguments: dict) -> ToolInvocation:
    return ToolInvocation(tool_name=name, arguments=arguments, source="test")


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    """完整 OpenAI 形态 tool_call（id/type/function）。"""
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _resp(content: str = "", tool_calls: list | None = None) -> LLMResponse:
    return LLMResponse(success=True, content=content, tool_calls=tool_calls or [])


def _reports(emitted: list) -> List[GamePayload]:
    """从 emit 记录中筛出 game.report payload（按发射顺序）。"""
    return [p for p in emitted if isinstance(p, GamePayload) and p.event_type == "report"]


async def _wait_until(condition, timeout: float = 3.0, interval: float = 0.02) -> None:
    """轮询等待条件成立（事件驱动时序断言用；超时抛 TimeoutError）。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(interval)
    raise TimeoutError("条件在时限内未成立")


# ---------------------------------------------------------------------------
# 工具契约（注册名 = minecraft_*）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minecraft_todo_read_write_full_document() -> None:
    """minecraft_todo 全量读写：write 覆盖、read 返回全文、无 id。"""
    provider = _make_provider(_make_state())
    reg = ToolRegistry()
    reg.register_provider(provider)

    r = await reg.invoke(_invocation("minecraft_todo", {"action": "read"}))
    assert r.success
    assert r.structured_content["todos"] == []

    r = await reg.invoke(
        _invocation(
            "minecraft_todo",
            {"action": "write", "todos": [{"content": "挖钻石", "status": "in_progress"}]},
        )
    )
    assert r.success
    assert r.structured_content["todos"][0]["content"] == "挖钻石"

    r = await reg.invoke(_invocation("minecraft_todo", {"action": "read"}))
    assert len(r.structured_content["todos"]) == 1


@pytest.mark.asyncio
async def test_minecraft_todo_write_overwrites() -> None:
    """write 覆盖旧文档（批量，无 id 定位）。"""
    provider = _make_provider(_make_state())
    reg = ToolRegistry()
    reg.register_provider(provider)

    await reg.invoke(_invocation("minecraft_todo", {"action": "write", "todos": [{"content": "a"}]}))
    await reg.invoke(
        _invocation(
            "minecraft_todo",
            {"action": "write", "todos": [{"content": "b"}, {"content": "c", "status": "done"}]},
        )
    )
    r = await reg.invoke(_invocation("minecraft_todo", {"action": "read"}))
    assert [t["content"] for t in r.structured_content["todos"]] == ["b", "c"]


@pytest.mark.asyncio
async def test_minecraft_notebook_full_document() -> None:
    """minecraft_notebook 全量读写。"""
    provider = _make_provider(_make_state())
    reg = ToolRegistry()
    reg.register_provider(provider)

    r = await reg.invoke(_invocation("minecraft_notebook", {"action": "read"}))
    assert r.success
    assert r.structured_content["content"] == ""

    r = await reg.invoke(_invocation("minecraft_notebook", {"action": "write", "content": "东侧 Y=12 有钻石"}))
    assert r.success

    r = await reg.invoke(_invocation("minecraft_notebook", {"action": "read"}))
    assert r.structured_content["content"] == "东侧 Y=12 有钻石"


@pytest.mark.asyncio
async def test_minecraft_get_state_three_fields() -> None:
    """minecraft_get_state 三元组：todo/notebook/recent_reports。"""
    state = _make_state()
    provider = _make_provider(state)
    reg = ToolRegistry()
    reg.register_provider(provider)

    state.add_report("delivery", "挖到钻石了！", "y=-12")
    state.set_notebook("矿脉在 Y=12")
    r = await reg.invoke(_invocation("minecraft_get_state", {}))
    assert r.success
    d = r.structured_content
    assert d["recent_reports"] == [{"kind": "delivery", "content": "挖到钻石了！", "scene": "y=-12"}]
    assert d["notebook"] == "矿脉在 Y=12"
    assert "todo" in d


@pytest.mark.asyncio
async def test_minecraft_get_state_reports_ring_buffer() -> None:
    """上报只保留最近 10 条（环形）。"""
    state = _make_state()
    for i in range(15):
        state.add_report("delivery", f"上报 {i}")
    assert len(state.reports) == 10
    assert state.reports[-1]["content"] == "上报 14"


@pytest.mark.asyncio
async def test_minecraft_send_prompt_delivers_message() -> None:
    """minecraft_send_prompt 提示词通道：消息投递给 callback（不写 todo——分解是 LLM 的事）。"""
    received: list[str] = []

    async def cb(content: str) -> None:
        received.append(content)

    provider = MinecraftToolProvider(state=_make_state(), send_prompt_callback=cb)
    reg = ToolRegistry()
    reg.register_provider(provider)

    r = await reg.invoke(_invocation("minecraft_send_prompt", {"content": "挖 3 个钻石"}))
    assert r.success
    assert received == ["挖 3 个钻石"]
    # 系统不代写 todo（目标分解是 LLM 的行为）
    assert provider.state.todos == []


@pytest.mark.asyncio
async def test_minecraft_send_prompt_without_callback_degrades() -> None:
    """send_prompt 无 callback：降级成功返回（消息丢失记日志，不阻断调用方）。"""
    provider = _make_provider(_make_state())
    reg = ToolRegistry()
    reg.register_provider(provider)

    r = await reg.invoke(_invocation("minecraft_send_prompt", {"content": "建房子"}))
    assert r.success
    assert r.structured_content["delivered"] is True


@pytest.mark.asyncio
async def test_minecraft_report_emits_and_records() -> None:
    """minecraft_report：回调受理 → success；kind/content 校验；无 callback 降级。"""
    received: list[tuple[str, str, str]] = []

    async def cb(kind: str, content: str, scene: str) -> None:
        received.append((kind, content, scene))

    provider = MinecraftToolProvider(state=_make_state(), report_callback=cb)
    reg = ToolRegistry()
    reg.register_provider(provider)

    r = await reg.invoke(_invocation("minecraft_report", {"kind": "delivery", "content": "全部挖完"}))
    assert r.success
    assert r.structured_content["reported"] is True
    assert received == [("delivery", "全部挖完", "")]

    # kind 非法 / content 缺失 → 内容级失败观察（LLM 读 error 自纠；工具本身执行成功）
    r = await reg.invoke(_invocation("minecraft_report", {"kind": "chat", "content": "x"}))
    assert r.structured_content["success"] is False
    assert "kind" in r.structured_content["error"]
    r = await reg.invoke(_invocation("minecraft_report", {"kind": "delivery", "content": "  "}))
    assert r.structured_content["success"] is False

    # 无 callback（脱离 Agent 单测）：降级成功
    bare = _make_provider(_make_state())
    reg2 = ToolRegistry()
    reg2.register_provider(bare)
    r = await reg2.invoke(_invocation("minecraft_report", {"kind": "escalation", "content": "需要授权"}))
    assert r.success


@pytest.mark.asyncio
async def test_minecraft_report_gate_rejection_fails_tool() -> None:
    """交付门禁：回调拒绝（返回原因）→ 内容级失败观察，原因透传给 LLM 自纠。"""

    async def gate(kind: str, content: str, scene: str) -> str | None:
        return "仍有 1 个后台任务未决"

    provider = MinecraftToolProvider(state=_make_state(), report_callback=gate)
    reg = ToolRegistry()
    reg.register_provider(provider)

    r = await reg.invoke(_invocation("minecraft_report", {"kind": "delivery", "content": "提前交付"}))
    assert r.structured_content["success"] is False
    assert "仍有 1 个后台任务未决" in r.structured_content["error"]


# ---------------------------------------------------------------------------
# MinecraftAgent：事件 / ReAct 循环
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minecraft_agent_emits_game_report_with_payload() -> None:
    """emit game.report：payload 字段完整（report_kind/game）+ recent_reports 同步。"""
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()
    agent = MinecraftAgent(
        MinecraftConfig(),
        event_bus=event_bus,
        live_session_id="ls_test",
    )
    await agent._emit_report("delivery", "挖到钻石了，交付！", scene="y=-12")

    payload = event_bus.emit.await_args[0][1]
    assert isinstance(payload, GamePayload)
    assert payload.game == "minecraft"
    assert payload.event_type == "report"
    assert payload.report_kind == "delivery"
    assert payload.message == "挖到钻石了，交付！"
    assert payload.scene == "y=-12"

    snapshot = agent.get_state_snapshot()
    assert snapshot["recent_reports"][0]["kind"] == "delivery"


@pytest.mark.asyncio
async def test_react_natural_termination_fallback_delivery() -> None:
    """情形 3：自然终止、无 report、无 handoff → 系统兜底包装终止文本为一次 delivery。"""
    llm = MagicMock()
    llm.chat_messages = AsyncMock(return_value=_resp("钻石挖完了，共 5 颗。"))
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(max_steps=10),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    await agent.send_prompt("挖 3 个钻石")
    await _wait_until(lambda: llm.chat_messages.await_count == 1)

    emitted = [c.args[1] for c in event_bus.emit.await_args_list]
    reports = _reports(emitted)
    assert len(reports) == 1  # 主播必收到一次且仅一次交付
    assert reports[0].report_kind == "delivery"
    assert "钻石挖完了" in reports[0].message
    await agent.stop()


@pytest.mark.asyncio
async def test_react_report_delivery_stops_batch() -> None:
    """情形 1：LLM 调 report(delivery) → 本轮工具执行完后批次停止（不再推理）。"""
    llm = MagicMock()
    calls = 0

    async def fake(messages, **kwargs):
        nonlocal calls
        calls += 1
        if not any(m.get("role") == "tool" for m in messages):
            return _resp(
                tool_calls=[
                    _tool_call("minecraft_report", {"kind": "delivery", "content": "任务完成汇报"}),
                    _tool_call("minecraft_notebook", {"action": "write", "content": "收尾笔记"}),
                ]
            )
        return _resp("不该再推理")

    llm.chat_messages = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(max_steps=10),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    await agent.send_prompt("干活")
    await _wait_until(lambda: calls == 1)
    await asyncio.sleep(0.15)
    assert calls == 1  # 上报后停止，不开新推理

    emitted = [c.args[1] for c in event_bus.emit.await_args_list]
    reports = _reports(emitted)
    assert len(reports) == 1 and reports[0].report_kind == "delivery"
    # 同轮其余工具照常执行（结果不丢）
    assert agent.get_state_snapshot()["notebook"] == "收尾笔记"
    await agent.stop()


@pytest.mark.asyncio
async def test_react_report_escalation_stops_and_waits() -> None:
    """情形 2：LLM 调 report(escalation) → 停止，静默等主播 send_prompt 唤醒。"""
    llm = MagicMock()
    llm.chat_messages = AsyncMock(
        return_value=_resp(tool_calls=[_tool_call("minecraft_report", {"kind": "escalation", "content": "需要授权"})])
    )
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(max_steps=10),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    await agent.send_prompt("遇到困难的任务")
    await _wait_until(lambda: llm.chat_messages.await_count == 1)
    await asyncio.sleep(0.15)
    assert llm.chat_messages.await_count == 1

    emitted = [c.args[1] for c in event_bus.emit.await_args_list]
    reports = _reports(emitted)
    assert len(reports) == 1 and reports[0].report_kind == "escalation"

    # 主播回复（send_prompt）唤醒新批次（新 mock 独立计数，避免与第一批混淆）
    resume_mock = AsyncMock(return_value=_resp("收到授权，继续"))
    llm.chat_messages = resume_mock
    await agent.send_prompt("授权通过了，继续")
    await _wait_until(lambda: resume_mock.await_count == 1)
    await agent.stop()


@pytest.mark.asyncio
async def test_react_max_steps_emits_attention() -> None:
    """情形 5：LLM 恒调用工具 → 步数超上限 → attention_required 挂起。"""
    llm = MagicMock()
    llm.chat_messages = AsyncMock(return_value=_resp(tool_calls=[_tool_call("minecraft_todo", {"action": "read"})]))
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(max_steps=3),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    await agent.send_prompt("循环任务")
    await _wait_until(lambda: llm.chat_messages.await_count == 3)

    emitted = [c.args[1] for c in event_bus.emit.await_args_list]
    attention = [p for p in emitted if isinstance(p, GamePayload) and p.event_type == "attention_required"]
    assert len(attention) == 1
    assert "上限" in attention[0].message
    await agent.stop()


@pytest.mark.asyncio
async def test_react_todo_done_no_longer_emits_milestone() -> None:
    """里程碑语义改造：todo 项 → done 不再自动发 game.milestone（叙事防刷屏）。"""
    llm = MagicMock()
    captured: list[list[dict]] = []

    async def fake(messages, **kwargs):
        captured.append([m.copy() for m in messages])
        if not any(m.get("role") == "tool" for m in messages):
            return _resp(
                tool_calls=[
                    _tool_call(
                        "minecraft_todo", {"action": "write", "todos": [{"content": "挖钻石", "status": "done"}]}
                    )
                ]
            )
        return _resp("全部完成")

    llm.chat_messages = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(max_steps=10),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    await agent.send_prompt("挖钻石")
    await _wait_until(lambda: len(captured) == 2)

    emitted = [c.args[1] for c in event_bus.emit.await_args_list]
    assert not [p for p in emitted if isinstance(p, GamePayload) and p.event_type == "milestone"]
    # 自然终止无 report → 系统兜底 delivery 仍在（主播必收到一次交付）
    reports = _reports(emitted)
    assert len(reports) == 1 and reports[0].report_kind == "delivery"
    await agent.stop()


@pytest.mark.asyncio
async def test_react_full_format_feedback_and_id_association() -> None:
    """完整 OpenAI 格式喂回：assistant.tool_calls + tool role + tool_call_id 关联。"""
    captured: list[dict] = []

    async def fake(messages, **kwargs):
        captured.append(messages.copy())
        return _resp(
            tool_calls=[_tool_call("minecraft_notebook", {"action": "write", "content": "坐标 (10,20)"}, "call_x")]
        )

    llm = MagicMock()
    llm.chat_messages = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(max_steps=5),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    await agent.send_prompt("探索东侧")
    await _wait_until(lambda: len(captured) >= 2)

    # 第二轮请求的 messages 应含正确的喂回结构
    second = captured[1]
    assistant = [m for m in second if m.get("role") == "assistant"]
    tools = [m for m in second if m.get("role") == "tool"]
    assert assistant and assistant[0]["tool_calls"][0]["id"] == "call_x"  # 完整形态保留
    assert tools and tools[0]["tool_call_id"] == "call_x"  # id 关联
    assert "坐标 (10,20)" in tools[0]["content"]
    # 第一轮工具调用真的执行了（notebook 落状态）
    assert agent.get_state_snapshot()["notebook"] == "坐标 (10,20)"
    await agent.stop()


@pytest.mark.asyncio
async def test_react_mcp_tool_via_registry_passthrough() -> None:
    """MCP 工具（maicraft_*）经 registry 透传——agent 不感知 maicraft 接口。"""
    registry = ToolRegistry()

    class FakeMcpProvider(BaseToolProvider):
        category = "game"
        name = "FakeMcp"

        def list_tools(self):
            return [
                ToolSpec(
                    name="maicraft_perceive",
                    description="感知",
                    parameters_schema={"type": "object"},
                    kind="sync",
                    provider="maicraft",
                )
            ]

        async def invoke(self, invocation: ToolInvocation):
            return ToolExecutionResult(
                tool_name=invocation.tool_name, success=True, structured_content={"ok": True, "view": "situation"}
            )

    registry.register_provider(FakeMcpProvider())

    async def fake(messages, **kwargs):
        fake.calls += 1
        tools_given = kwargs.get("tools") or []
        first = not any(m.get("role") == "tool" for m in messages)
        if first:
            assert any(t["name"] == "maicraft_perceive" for t in tools_given)  # 动态工具面含 MCP 工具
            assert any(t["name"] == "minecraft_todo" for t in tools_given)  # 局部工具面是注册名
            return _resp(tool_calls=[_tool_call("maicraft_perceive", {"view": "situation"})])
        return _resp("感知完成")

    fake.calls = 0
    llm = MagicMock()
    llm.chat_messages = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(max_steps=5),
        llm_manager=llm,
        event_bus=event_bus,
        tool_registry=registry,
    )
    await agent.start()
    await agent.send_prompt("看看周围")
    await _wait_until(lambda: fake.calls == 2)

    assert registry.has("maicraft_perceive")
    await agent.stop()


@pytest.mark.asyncio
async def test_react_pause_suspends_loop() -> None:
    """平台 pause：任务循环在步骤间挂起（不调 LLM），resume 后继续。"""
    call_count = 0

    async def fake(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.005)  # 每步微延迟：pause 窗口内不跑满 max_steps
        return _resp(tool_calls=[_tool_call("minecraft_todo", {"action": "read"})])

    llm = MagicMock()
    llm.chat_messages = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(max_steps=1000),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    await agent.send_prompt("暂停任务")

    # 暂停循环：等待一步真实执行后挂起，计数冻结
    await _wait_until(lambda: call_count >= 1)
    await agent.pause()
    frozen = call_count
    await asyncio.sleep(0.3)
    assert call_count == frozen  # 挂起后不再推理

    await agent.resume()
    await _wait_until(lambda: call_count > frozen)
    await agent.stop()


@pytest.mark.asyncio
async def test_send_prompt_wakes_worker_full_chain() -> None:
    """send_prompt 全链路：registry 调 minecraft_send_prompt → worker 唤醒 → 任务真实执行。"""
    llm = MagicMock()
    llm.chat_messages = AsyncMock(return_value=_resp("收到，开始执行"))
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    registry = ToolRegistry()
    agent = MinecraftAgent(
        MinecraftConfig(max_steps=5),
        llm_manager=llm,
        event_bus=event_bus,
        tool_registry=registry,
    )
    await agent.start()

    r = await registry.invoke(_invocation("minecraft_send_prompt", {"content": "工具链目标"}))
    assert r.success
    await _wait_until(lambda: llm.chat_messages.awaited)

    await agent.stop()


@pytest.mark.asyncio
async def test_agent_reusable_after_goal_completes() -> None:
    """任务完成回空闲后，新命令可再次唤醒（worker 循环复用）。"""
    calls = 0

    async def fake(messages, **kwargs):
        nonlocal calls
        calls += 1
        return _resp(f"汇报 {calls}")

    llm = MagicMock()
    llm.chat_messages = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(max_steps=5),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()

    await agent.send_prompt("第一个任务")
    await _wait_until(lambda: calls == 1)
    await agent.send_prompt("第二个任务")
    await _wait_until(lambda: calls == 2)

    assert len(agent.get_state_snapshot()["recent_reports"]) >= 2  # 两次兜底交付
    await agent.stop()


@pytest.mark.asyncio
async def test_prompt_without_llm_fails_fast() -> None:
    """无 LLM 时收到命令立即报错退出，不进任务循环（不空转）。"""
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(),
        llm_manager=None,
        event_bus=event_bus,
    )
    await agent.start()
    await agent.send_prompt("挖钻石")
    await _wait_until(lambda: bool(event_bus.emit.await_args_list))

    emitted = [c.args[1] for c in event_bus.emit.await_args_list]
    errors = [p for p in emitted if isinstance(p, GamePayload) and p.event_type == "error"]
    assert len(errors) == 1
    await agent.stop()


@pytest.mark.asyncio
async def test_idle_costs_nothing() -> None:
    """命令驱动：空闲（无命令）时不产生任何 LLM 调用。"""
    llm = MagicMock()
    llm.chat_messages = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(),
        llm_manager=llm,
        event_bus=MagicMock(),
    )
    await agent.start()
    await asyncio.sleep(0.4)

    llm.chat_messages.assert_not_awaited()
    await agent.stop()


@pytest.mark.asyncio
async def test_llm_call_failure_emits_error() -> None:
    """LLM 调用失败（success=False）→ game.error，任务退出。"""
    llm = MagicMock()
    llm.chat_messages = AsyncMock(return_value=LLMResponse(success=False, error="rate limit"))
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    await agent.send_prompt("失败任务")
    await _wait_until(lambda: bool(event_bus.emit.await_args_list))

    emitted = [c.args[1] for c in event_bus.emit.await_args_list]
    errors = [p for p in emitted if isinstance(p, GamePayload) and p.event_type == "error"]
    assert len(errors) == 1
    assert "rate limit" in errors[0].message
    await agent.stop()


@pytest.mark.asyncio
async def test_react_observation_compaction() -> None:
    """旧观察压缩：超过保留条数的 tool 消息替换为占位符，保留最近 N 条。"""
    agent = MinecraftAgent(MinecraftConfig(), event_bus=MagicMock())
    messages: list[dict] = [{"role": "tool", "tool_call_id": f"c{i}", "content": f"obs-{i}"} for i in range(12)]
    agent._compact_observations(messages)

    tools = [m for m in messages if m["role"] == "tool"]
    assert len(tools) == 12
    # 最早的 2 条（12-10）被压缩
    compressed = [m for m in tools if m["content"] == "[观察已压缩]"]
    assert len(compressed) == 2
    assert compressed[0]["tool_call_id"] == "c0"
    # 最近 10 条保留原文
    assert tools[2]["content"] == "obs-2"
    assert tools[-1]["content"] == "obs-11"


@pytest.mark.asyncio
async def test_react_tool_failure_fed_back_to_llm() -> None:
    """MCP 工具失败（ok:false）作为观察喂回 LLM，循环继续（ReAct 标准，LLM 自调整）。"""
    registry = ToolRegistry()

    class FailingMcp(BaseToolProvider):
        category = "game"
        name = "FailingMcp"

        def list_tools(self):
            return [
                ToolSpec(
                    name="maicraft_execute",
                    description="执行",
                    parameters_schema={"type": "object"},
                    kind="sync",
                    provider="maicraft",
                )
            ]

        async def invoke(self, invocation: ToolInvocation):
            return ToolExecutionResult(tool_name=invocation.tool_name, success=False, error_message="world not loaded")

    registry.register_provider(FailingMcp())
    seen_failure: list[bool] = []

    async def fake(messages, **kwargs):
        # 第一轮：调用 MCP 工具 → 失败观察；第二轮：读到失败 → 自然终止
        if not any(m.get("role") == "tool" for m in messages):
            return _resp(tool_calls=[_tool_call("maicraft_execute", {"goal": "mine"})])
        seen_failure.append(
            any("world not loaded" in m.get("content", "") for m in messages if m.get("role") == "tool")
        )
        return _resp("接受错误，尝试重试")

    llm = MagicMock()
    llm.chat_messages = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(max_steps=5),
        llm_manager=llm,
        event_bus=event_bus,
        tool_registry=registry,
    )
    await agent.start()
    await agent.send_prompt("挖矿")
    await _wait_until(lambda: len(seen_failure) >= 1)

    assert seen_failure == [True]  # 失败观察真实喂回
    await agent.stop()


@pytest.mark.asyncio
async def test_react_multi_tool_calls_batch_execute() -> None:
    """同轮多个 tool_calls：串行执行，结果全部喂回（消息顺序对应）。"""
    llm = MagicMock()
    calls = 0

    async def fake(messages, **kwargs):
        nonlocal calls
        calls += 1
        if not any(m.get("role") == "tool" for m in messages):
            return _resp(
                tool_calls=[
                    _tool_call("minecraft_notebook", {"action": "write", "content": "笔记 A"}, "c1"),
                    _tool_call(
                        "minecraft_todo",
                        {"action": "write", "todos": [{"content": "任务 B", "status": "in_progress"}]},
                        "c2",
                    ),
                ]
            )
        return _resp("done")

    llm.chat_messages = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(max_steps=5),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    await agent.send_prompt("批量任务")
    await _wait_until(lambda: calls == 2)

    snapshot = agent.get_state_snapshot()
    assert snapshot["notebook"] == "笔记 A"
    assert snapshot["todo"] == [{"content": "任务 B", "status": "in_progress"}]
    await agent.stop()


@pytest.mark.asyncio
async def test_command_after_task_reaches_messages() -> None:
    """执行中 send_prompt 的消息进入对话（LLM 下一次推理吸收，系统不硬转向）。"""
    seen_messages: list[dict] = []

    async def fake(messages, **kwargs):
        seen_messages.append(messages.copy())
        return _resp(tool_calls=[])

    llm = MagicMock()
    llm.chat_messages = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(max_steps=10),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    await agent.send_prompt("初始任务")
    await asyncio.sleep(0.15)
    await agent.send_prompt("追加指令")
    await _wait_until(lambda: len(seen_messages) >= 2)

    # 第二轮 messages 含追加指令（user 消息）
    user_msgs = [m for m in seen_messages[1] if m["role"] == "user"]
    assert any("追加指令" in m["content"] for m in user_msgs)
    await agent.stop()


# ---------------------------------------------------------------------------
# handoff 跟踪（execute 受理 → 订阅/兜底核实 → 真迁移注入唤醒）
# ---------------------------------------------------------------------------


class _FakeSubscribableClient:
    """带资源订阅的 McpClient 替身（handoff 测试用；装配路径自动持有）。"""

    def __init__(self, name: str, config: Any) -> None:
        self.name = name
        self.config = config
        self.connected = False
        self.closed = False
        self.subscriptions: Dict[str, Any] = {}
        self.unsubscribed: List[str] = []
        self.subscribe_calls: List[str] = []

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def close(self) -> None:
        self.closed = True
        self.connected = False

    async def subscribe_resource(self, uri: str, callback: Any) -> Any:
        self.subscribe_calls.append(uri)
        self.subscriptions[uri] = callback

        async def unsubscribe() -> None:
            self.subscriptions.pop(uri, None)
            self.unsubscribed.append(uri)

        return unsubscribe


class _FakeMaiCraftProvider(BaseToolProvider):
    """MaiCraft 工具替身（双前缀注册名形态，对齐真实 mapper 无条件加前缀）。

    execute 返回受理回执（accepted + task_id）；task 支持 get（按 task_states
    快照应答）/ answer（切回 running）。get 到未知 task_id 返回失败（业务错误）。
    """

    category = "game"
    name = "FakeMaiCraft"

    def __init__(self, task_states: Dict[str, Dict[str, Any]] | None = None) -> None:
        self.task_states: Dict[str, Dict[str, Any]] = task_states if task_states is not None else {}
        self.get_calls: List[str] = []

    def list_tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name="maicraft_maicraft_execute",
                description="执行（受理型）",
                parameters_schema={"type": "object"},
                kind="sync",
                provider="maicraft",
            ),
            ToolSpec(
                name="maicraft_maicraft_task",
                description="任务查询/应答",
                parameters_schema={"type": "object"},
                kind="sync",
                provider="maicraft",
            ),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        name = invocation.tool_name
        args = invocation.arguments or {}
        if name.endswith("maicraft_execute"):
            task_id = str(args.get("task_id", "task-1"))
            self.task_states[task_id] = {"task_id": task_id, "state": "running"}
            return ToolExecutionResult(
                tool_name=name,
                success=True,
                structured_content={"accepted": True, "task_id": task_id},
            )
        if name.endswith("maicraft_task"):
            action = str(args.get("action", ""))
            task_id = str(args.get("task_id", ""))
            if action == "get":
                self.get_calls.append(task_id)
                snapshot = self.task_states.get(task_id)
                if snapshot is None:
                    return ToolExecutionResult(
                        tool_name=name, success=False, error_message=f"invalid task_id: {task_id}"
                    )
                return ToolExecutionResult(tool_name=name, success=True, structured_content=dict(snapshot))
            if action == "answer":
                if task_id in self.task_states:
                    self.task_states[task_id]["state"] = "running"
                return ToolExecutionResult(
                    tool_name=name, success=True, structured_content={"ok": True, "answered": True}
                )
        return ToolExecutionResult(tool_name=name, success=False, error_message=f"unknown call: {name}")


def _make_event_bus() -> MagicMock:
    bus = MagicMock()
    bus.emit = AsyncMock()
    return bus


def _emitted_payloads(bus: MagicMock) -> List[GamePayload]:
    return [c.args[1] for c in bus.emit.await_args_list if isinstance(c.args[1], GamePayload)]


class _RecordingLlm:
    """记录每轮 messages 的 LLM 替身：统一调用计数（len(captured)）与内容断言（captured[i]）。"""

    def __init__(self, script: Any) -> None:
        self.captured: List[List[Dict[str, Any]]] = []
        self._script = script

    async def chat_messages(self, messages: List[Dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.captured.append([m.copy() for m in messages])
        return await self._script(messages, **kwargs)


def _make_handoff_agent(
    llm: Any,
    registry: ToolRegistry,
    config: MinecraftConfig | None = None,
) -> MinecraftAgent:
    # mcp.enabled=False：跳过装配路径（订阅替身在 start 后注入 _mcp_client）
    return MinecraftAgent(
        config or MinecraftConfig(max_steps=10, mcp=McpServerConfig(enabled=False)),
        llm_manager=llm,
        event_bus=_make_event_bus(),
        tool_registry=registry,
    )


@pytest.mark.asyncio
async def test_execute_receipt_registers_handoff_and_subscribes() -> None:
    """execute 受理回执 → handoff 登记 + attention 资源订阅发起。"""
    provider = _FakeMaiCraftProvider()
    registry = ToolRegistry()
    registry.register_provider(provider)

    async def script(messages, **kwargs):
        if not any(m.get("role") == "tool" for m in messages):
            return _resp(tool_calls=[_tool_call("maicraft_maicraft_execute", {"goal": "挖矿"})])
        return _resp("已受理，先干别的")

    llm = _RecordingLlm(script)

    agent = _make_handoff_agent(llm, registry)
    fake_client = _FakeSubscribableClient("maicraft", None)
    await agent.start()
    agent._mcp_client = fake_client  # start 后注入：接管订阅接线
    await agent.send_prompt("挖矿")
    await _wait_until(lambda: len(llm.captured) == 2)

    assert "task-1" in agent._handoffs  # 回执登记
    assert fake_client.subscribe_calls == ["maicraft://attention"]  # 订阅发起
    # 受理回执照常喂回 LLM（回合继续，不被折叠）
    second_tool_msgs = [m for m in llm.captured[1] if m.get("role") == "tool"]
    assert any("accepted" in m["content"] and "task-1" in m["content"] for m in second_tool_msgs)

    # 情形 4：有未决 handoff 自然终止 → 静默让出（无交付）
    assert not _reports(_emitted_payloads(agent._event_bus))
    await agent.stop()


@pytest.mark.asyncio
async def test_handoff_terminal_notification_wakes_worker_with_snapshot() -> None:
    """全链路：通知举旗 → task get 核实 → 终态注入快照 + worker 唤醒 + 跟踪移除 + 退订。"""
    provider = _FakeMaiCraftProvider()
    registry = ToolRegistry()
    registry.register_provider(provider)

    async def script(messages, **kwargs):
        user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
        if any("状态迁移" in u for u in user_msgs):
            return _resp("任务完成了，交付")  # 被注入唤醒的第二批
        if not any(m.get("role") == "tool" for m in messages):
            return _resp(tool_calls=[_tool_call("maicraft_maicraft_execute", {"goal": "挖矿"})])
        return _resp("已受理，静默等通知")  # 第一批自然终止（有 handoff，静默让出）

    llm = _RecordingLlm(script)

    agent = _make_handoff_agent(llm, registry)
    fake_client = _FakeSubscribableClient("maicraft", None)
    await agent.start()
    agent._mcp_client = fake_client  # start 后注入：接管订阅接线
    await agent.send_prompt("挖矿")
    await _wait_until(lambda: len(llm.captured) == 2)
    assert "task-1" in agent._handoffs

    # 模拟 MaiCraft 任务完成 + server 推送更新通知（回调举旗）
    provider.task_states["task-1"]["state"] = "success"
    fake_client.subscriptions["maicraft://attention"]("maicraft://attention")

    # watcher 核实 → 注入 → worker 第二批 → LLM 看到快照
    await _wait_until(lambda: len(llm.captured) >= 3)
    injected = [m["content"] for m in llm.captured[2] if m.get("role") == "user"]
    assert any("state=success" in u and "task-1" in u for u in injected)

    # 终态后跟踪移除 + 退订
    await _wait_until(lambda: not agent._handoffs)
    await _wait_until(lambda: fake_client.unsubscribed == ["maicraft://attention"])
    # task get 核实真实发生（事实源）：登记首查 + 通知后核实
    assert provider.get_calls.count("task-1") >= 2
    await agent.stop()


@pytest.mark.asyncio
async def test_handoff_no_state_change_no_injection() -> None:
    """通知但状态无真迁移（仍 running）→ 不注入，LLM 零消耗继续睡。"""
    provider = _FakeMaiCraftProvider()
    registry = ToolRegistry()
    registry.register_provider(provider)

    async def script(messages, **kwargs):
        if not any(m.get("role") == "tool" for m in messages):
            return _resp(tool_calls=[_tool_call("maicraft_maicraft_execute", {"goal": "长任务"})])
        return _resp("已受理，静默等通知")

    llm = _RecordingLlm(script)

    agent = _make_handoff_agent(llm, registry)
    fake_client = _FakeSubscribableClient("maicraft", None)
    await agent.start()
    agent._mcp_client = fake_client  # start 后注入：接管订阅接线
    await agent.send_prompt("长任务")
    await _wait_until(lambda: len(llm.captured) == 2)

    # 任务仍 running：通知到达（虚假/无关提示）→ 核实无迁移 → 不唤醒
    fake_client.subscriptions["maicraft://attention"]("maicraft://attention")
    await asyncio.sleep(0.4)
    assert len(llm.captured) == 2  # 无新推理（零空耗）
    assert "task-1" in agent._handoffs  # 跟踪保持
    await agent.stop()


@pytest.mark.asyncio
async def test_handoff_decision_point_injects_and_keeps_tracking() -> None:
    """决策点（waiting_for_decision）注入快照但保留跟踪；answer 后任务继续后台跑。"""
    provider = _FakeMaiCraftProvider()
    registry = ToolRegistry()
    registry.register_provider(provider)

    async def script(messages, **kwargs):
        user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
        if not any(m.get("role") == "tool" for m in messages):
            return _resp(tool_calls=[_tool_call("maicraft_maicraft_execute", {"goal": "需要选择的任务"})])
        if not any("状态迁移" in u for u in user_msgs):
            return _resp("已受理，静默等通知")
        # 被决策点唤醒：应答后自然终止（handoff 仍在 → 静默）
        return _resp(
            tool_calls=[_tool_call("maicraft_maicraft_task", {"action": "answer", "task_id": "task-1", "choice": "A"})]
        )

    llm = _RecordingLlm(script)

    agent = _make_handoff_agent(llm, registry)
    fake_client = _FakeSubscribableClient("maicraft", None)
    await agent.start()
    agent._mcp_client = fake_client  # start 后注入：接管订阅接线
    await agent.send_prompt("需要选择的任务")
    await _wait_until(lambda: len(llm.captured) == 2)

    provider.task_states["task-1"]["state"] = "waiting_for_decision"
    fake_client.subscriptions["maicraft://attention"]("maicraft://attention")

    # 决策点注入唤醒，LLM 用 task answer 应答
    await _wait_until(lambda: len(llm.captured) >= 3)
    injected = [m["content"] for m in llm.captured[2] if m.get("role") == "user"]
    assert any("state=waiting_for_decision" in u for u in injected)

    # 应答后 handoff 保持跟踪（任务恢复后台跑），不退订
    await _wait_until(lambda: len(llm.captured) >= 4)
    await asyncio.sleep(0.15)
    assert "task-1" in agent._handoffs
    assert fake_client.unsubscribed == []
    await agent.stop()


@pytest.mark.asyncio
async def test_handoff_wait_timeout_alerts_without_killing_task() -> None:
    """wait_timeout：长期无进展注入告警（不杀任务），跟踪保留等待后续。"""
    provider = _FakeMaiCraftProvider()
    registry = ToolRegistry()
    registry.register_provider(provider)

    async def script(messages, **kwargs):
        user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
        if not any(m.get("role") == "tool" for m in messages):
            return _resp(tool_calls=[_tool_call("maicraft_maicraft_execute", {"goal": "卡住的任务"})])
        if not any("wait_timeout" in u for u in user_msgs):
            return _resp("已受理，静默等通知")
        return _resp("收到告警，去查一下任务")

    llm = _RecordingLlm(script)

    agent = _make_handoff_agent(llm, registry)
    await agent.start()
    await agent.send_prompt("卡住的任务")
    await _wait_until(lambda: len(llm.captured) == 2)
    assert "task-1" in agent._handoffs

    # 截止时刻拨到过去 → 下一轮兜底核实触发告警
    agent._handoffs["task-1"].deadline_ms = 0
    agent._handoff_signal.set()

    await _wait_until(lambda: len(llm.captured) >= 3)
    injected = [m["content"] for m in llm.captured[2] if m.get("role") == "user"]
    assert any("wait_timeout" in u and "task-1" in u for u in injected)
    assert "task-1" in agent._handoffs  # 不杀任务：跟踪保留（deadline 已顺延）
    await agent.stop()


@pytest.mark.asyncio
async def test_delivery_gate_rejects_with_pending_handoff() -> None:
    """交付门禁：有未决 handoff 时 LLM 调 report(delivery) 被拒——错误观察喂回自纠。"""
    provider = _FakeMaiCraftProvider()
    registry = ToolRegistry()
    registry.register_provider(provider)

    async def script(messages, **kwargs):
        tool_msgs = [m["content"] for m in messages if m.get("role") == "tool"]
        if not tool_msgs:
            return _resp(tool_calls=[_tool_call("maicraft_maicraft_execute", {"goal": "挖矿"})])
        if not any("不能交付" in m for m in tool_msgs):
            # 提前交付 → 门禁拒绝，错误观察喂回
            return _resp(tool_calls=[_tool_call("minecraft_report", {"kind": "delivery", "content": "应该完成了"})])
        return _resp("知道了，继续等后台任务")  # 自纠后静默让出

    llm = _RecordingLlm(script)

    agent = _make_handoff_agent(llm, registry)
    await agent.start()
    await agent.send_prompt("挖矿")
    await _wait_until(lambda: len(llm.captured) >= 3)

    emitted = _emitted_payloads(agent._event_bus)
    assert not _reports(emitted)  # 提前交付被拦截，主播零骚扰
    # 拒绝原因作为失败观察喂回 LLM
    tool_msgs = [m["content"] for m in llm.captured[2] if m.get("role") == "tool"]
    assert any("不能交付" in m for m in tool_msgs)
    await agent.stop()


# ---------------------------------------------------------------------------
# 装配（factory 分派）
# ---------------------------------------------------------------------------


def test_factory_instantiates_minecraft() -> None:
    """factory 按顶级名分派 minecraft → MinecraftAgent（声明确认 + max_steps 透传）。"""
    from src.modules.agents.factory import instantiate_agent

    agent = instantiate_agent(
        "minecraft",
        {"max_steps": 9},
        llm_manager=None,
        prompt_manager=None,
        event_bus=MagicMock(),
        tool_registry=None,
    )
    assert isinstance(agent, MinecraftAgent)
    assert agent.typed_config.max_steps == 9
    assert [s.name for s in agent.list_tools()] == ["todo", "notebook", "get_state", "send_prompt", "report"]


def test_factory_rejects_legacy_game_name() -> None:
    """分类层已移除，旧注册名 "game" 不再可实例化（防分类层复活）。"""
    from src.modules.agents.factory import instantiate_agent

    agent = instantiate_agent(
        "game",
        {},
        llm_manager=None,
        prompt_manager=None,
        event_bus=MagicMock(),
        tool_registry=None,
    )
    assert agent is None


def test_factory_minecraft_schema_defaults() -> None:
    """空配置实例化 minecraft，行为参数取 Schema 默认值。

    执行节奏参数（poll_interval_ms / wait_timeout_ms）保留在 MinecraftConfig
    上以保 handoff 跟踪循环运行期可读；T26 工具线合流时改消费 `[tools.tasks]`
    段同名字段（默认 2000 / 1_800_000 与本字段一致）。
    """
    from src.modules.agents.factory import instantiate_agent

    agent = instantiate_agent(
        "minecraft",
        {},
        llm_manager=None,
        prompt_manager=None,
        event_bus=MagicMock(),
        tool_registry=None,
    )
    assert isinstance(agent, MinecraftAgent)
    assert agent.typed_config.max_steps == 50
    assert agent.typed_config.poll_interval_ms == 2000
    assert agent.typed_config.wait_timeout_ms == 1_800_000


# ---------------------------------------------------------------------------
# Agent 私有 MCP（owner_agent="minecraft"）装配契约
# ---------------------------------------------------------------------------


class _FakeMcpClient:
    """McpClient 替身：暴露 Agent 装配路径上用到的 connect/close 即可。"""

    def __init__(self, name: str, config: Any) -> None:
        self.name = name
        self.config = config
        self.connected = False
        self.closed = False

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def close(self) -> None:
        self.closed = True
        self.connected = False


class _FakeMcpProvider:
    """McpToolProvider 替身：setup() 返回指定工具数；list_tools 暴露缓存 specs。"""

    category = "mcp"

    def __init__(
        self,
        *,
        client: Any,
        server_name: str,
        prefix: str | None = None,
        provider: str | None = None,
    ) -> None:
        self.client = client
        self.server_name = server_name
        self.prefix = prefix if prefix is not None else f"{server_name}_"
        self._provider = provider or server_name
        self._specs: list = []
        self._setup_called = False
        # 强制构造一次工具列表：模拟 server 暴露 2 个 maicraft 工具
        self._tool_count = 2

    @property
    def name(self) -> str:
        return f"McpProvider:{self.server_name}"

    async def setup(self) -> int:
        self._setup_called = True
        self._specs = [
            ToolSpec(
                name=f"{self.prefix}{tool_name}",
                description=f"desc {tool_name}",
                kind="sync",
                provider=self._provider,
            )
            for tool_name in ("perceive", "execute")
        ]
        return self._tool_count

    def list_tools(self):
        return list(self._specs)

    async def invoke(self, invocation: ToolInvocation) -> Any:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    async def close(self) -> None:
        await self.client.close()


class _ZeroToolProvider(_FakeMcpProvider):
    """setup() 返回 0（连接失败或 server 无工具）的替身——不应被注册。"""

    async def setup(self) -> int:
        self._setup_called = True
        self._specs = []
        return 0


class _RaisingProvider(_FakeMcpProvider):
    """setup() 抛异常的替身——装配应被兜底，不阻断 Agent 启动。"""

    async def setup(self) -> int:
        self._setup_called = True
        raise ConnectionError("mock connect failure")


def _patch_mcp(monkeypatch: pytest.MonkeyPatch, provider_cls: type) -> Dict[str, Any]:
    """替换 src.modules.mcp 内的 McpClient 与 McpToolProvider 类。"""
    import src.modules.mcp as mcp_module
    from src.modules.mcp.client import McpClient
    from src.modules.mcp.provider import McpToolProvider

    monkeypatch.setattr(mcp_module, "McpClient", _FakeMcpClient)
    monkeypatch.setattr(mcp_module, "McpToolProvider", provider_cls)
    # 也覆盖真实类的导入路径（agent._bind_agent_owned_mcp 走模块引用）
    monkeypatch.setattr("src.modules.mcp.client.McpClient", _FakeMcpClient)
    monkeypatch.setattr("src.modules.mcp.provider.McpToolProvider", provider_cls)
    return {
        "McpClient": McpClient,
        "McpToolProvider": McpToolProvider,
    }


@pytest.mark.asyncio
async def test_on_start_binds_agent_owned_mcp_with_owner_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """_on_start 启用 mcp 时：McpClient/McpToolProvider 被实例化、setup 调用、
    工具以 owner_agent='minecraft' 注册进 ToolRegistry；域内查询可见、一般面默认排除。"""
    _patch_mcp(monkeypatch, _FakeMcpProvider)

    from src.modules.mcp.config import McpServerConfig

    registry = ToolRegistry()
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()
    agent = MinecraftAgent(
        MinecraftConfig(mcp=McpServerConfig(enabled=True, url="http://127.0.0.1:8766/mcp")),
        llm_manager=MagicMock(),
        event_bus=event_bus,
        tool_registry=registry,
    )

    await agent.start()

    # 域内查询（provider="maicraft"）：可见
    scoped_names = {s.name for s in registry.list_tools(provider="maicraft")}
    assert scoped_names == {"maicraft_perceive", "maicraft_execute"}
    # 一般面：默认排除归属限定的 maicraft_*（minecraft_* 本地工具无归属限定，仍在一般面）
    default_names = {s.name for s in registry.list_tools()}
    assert "maicraft_perceive" not in default_names
    assert "maicraft_execute" not in default_names
    # 归属查询：每个 maicraft 工具都标 owner_agent=minecraft
    assert registry.scoped_owner_of("maicraft_perceive") == "minecraft"
    assert registry.scoped_owner_of("maicraft_execute") == "minecraft"
    # 运营面：include_scoped=True 含一切
    all_names = {s.name for s in registry.list_tools(include_scoped=True)}
    assert {"maicraft_perceive", "maicraft_execute"}.issubset(all_names)
    # 装配成功：client 引用留给 handoff 订阅接线
    assert agent._mcp_client is not None

    await agent.stop()


@pytest.mark.asyncio
async def test_on_start_disabled_mcp_skips_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    """mcp.enabled=false：不构造 McpClient、不调 setup、不注册——零装配。"""
    constructed_clients: list = []
    constructed_providers: list = []

    class CountingClient(_FakeMcpClient):
        def __init__(self, name: str, config: Any) -> None:
            super().__init__(name, config)
            constructed_clients.append(self)

    class CountingProvider(_FakeMcpProvider):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            constructed_providers.append(self)

    _patch_mcp(monkeypatch, CountingProvider)
    monkeypatch.setattr("src.modules.mcp.client.McpClient", CountingClient)
    monkeypatch.setattr("src.modules.mcp.provider.McpToolProvider", CountingProvider)

    from src.modules.mcp.config import McpServerConfig

    registry = ToolRegistry()
    agent = MinecraftAgent(
        MinecraftConfig(mcp=McpServerConfig(enabled=False)),
        llm_manager=MagicMock(),
        event_bus=MagicMock(),
        tool_registry=registry,
    )
    await agent.start()

    assert constructed_clients == [], "enabled=false 不应实例化 McpClient"
    assert constructed_providers == [], "enabled=false 不应实例化 McpToolProvider"
    assert registry.list_tools(provider="maicraft") == []
    assert agent._mcp_client is None
    await agent.stop()


@pytest.mark.asyncio
async def test_on_start_setup_failure_does_not_block_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """setup() 抛异常：装配被 try/except 兜底，Agent 仍正常 start（命令驱动降级）。"""
    _patch_mcp(monkeypatch, _RaisingProvider)

    from src.modules.mcp.config import McpServerConfig

    registry = ToolRegistry()
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()
    agent = MinecraftAgent(
        MinecraftConfig(mcp=McpServerConfig(enabled=True)),
        llm_manager=MagicMock(),
        event_bus=event_bus,
        tool_registry=registry,
    )

    await agent.start()  # 不抛即通过
    assert registry.list_tools(provider="maicraft") == [], "setup 抛异常时不应有工具被注册"
    assert agent._running is True, "Agent 仍应进入运行态（MCP 不可用仅降级）"
    assert agent._mcp_client is None, "装配失败的 client 引用应被撤回"
    await agent.stop()


@pytest.mark.asyncio
async def test_on_start_zero_tools_closes_client_and_skips_register(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """setup() 返回 0（连接失败 / server 无工具）：close client、不注册——对齐通用通道的隔离风格。"""
    _patch_mcp(monkeypatch, _ZeroToolProvider)

    from src.modules.mcp.config import McpServerConfig

    registry = ToolRegistry()
    agent = MinecraftAgent(
        MinecraftConfig(mcp=McpServerConfig(enabled=True)),
        llm_manager=MagicMock(),
        event_bus=MagicMock(),
        tool_registry=registry,
    )

    await agent.start()
    assert registry.list_tools(provider="maicraft") == [], "count=0 时不应注册到 registry"
    assert agent._mcp_client is None, "装配失败的 client 引用应被撤回"
    await agent.stop()


class _RecordingSink:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def on_thinking_delta(self, *, round_id: str, phase: str, step: int, seq: int, text_delta: str) -> None:
        self.calls.append({"round_id": round_id, "phase": phase, "step": step, "seq": seq, "text_delta": text_delta})


class _CapturingToolProvider(BaseToolProvider):
    """记录每次 ToolInvocation（用于断言任务内 round_id 透传到工具面）。"""

    category = "minecraft"
    name = "CapturingToolProvider"

    def __init__(self) -> None:
        self.invocations: List[ToolInvocation] = []

    def list_tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name="minecraft_capture",
                description="cap",
                parameters_schema={"type": "object"},
                kind="sync",
                provider="minecraft",
            )
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.invocations.append(invocation)
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content={"ok": True})


def _build_sink_agent(llm: Any, sink: Optional[Any], capturing: _CapturingToolProvider) -> MinecraftAgent:
    registry = ToolRegistry()
    registry.register_provider(capturing)

    from src.modules.mcp.config import McpServerConfig

    return MinecraftAgent(
        MinecraftConfig(max_steps=5, mcp=McpServerConfig(enabled=False)),
        llm_manager=llm,
        event_bus=_make_event_bus(),
        tool_registry=registry,
        thinking_sink=sink,
    )


@pytest.mark.asyncio
async def test_thinking_sink_receives_reasoning_with_minecraft_phase() -> None:
    """sink 注入后：LLM reasoning delta → sink.on_thinking_delta，phase=minecraft，seq 跨步单调递增；content delta 不转发。"""
    sink = _RecordingSink()
    cap = _CapturingToolProvider()

    async def fake(messages, **kwargs):
        on_delta = kwargs.get("on_delta")
        if not any(m.get("role") == "tool" for m in messages):
            if on_delta is not None:
                on_delta("reasoning", "step1 A")
                on_delta("content", "step1 content x")
                on_delta("reasoning", "step1 B")
            return _resp(tool_calls=[_tool_call("minecraft_capture", {"v": 1}, "c1")])
        if on_delta is not None:
            on_delta("reasoning", "step2")
        return _resp("done")

    llm = _RecordingLlm(fake)
    agent = _build_sink_agent(llm, sink, cap)

    await agent.start()
    await agent.send_prompt("两步")
    await _wait_until(lambda: len(sink.calls) >= 3)
    await agent.stop()

    by_text = {c["text_delta"]: c for c in sink.calls}
    assert set(by_text) == {"step1 A", "step1 B", "step2"}
    assert not any("content" in k for k in by_text)
    round_ids = {c["round_id"] for c in sink.calls}
    assert len(round_ids) == 1
    (rid,) = round_ids
    assert rid.startswith("mc_") and len(rid) == 3 + 12
    assert {c["phase"] for c in sink.calls} == {"minecraft"}
    seqs = [c["seq"] for c in sink.calls]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert {c["step"] for c in sink.calls} == {1, 2}
    assert by_text["step1 A"]["step"] == 1 and by_text["step1 B"]["step"] == 1 and by_text["step2"]["step"] == 2


@pytest.mark.asyncio
async def test_thinking_sink_round_id_propagates_to_tool_invocations() -> None:
    """任务内 ToolInvocation.round_id 与 sink 收到的 round_id 一致——工具卡可关联思考轮。"""
    sink = _RecordingSink()
    cap = _CapturingToolProvider()

    async def fake(messages, **kwargs):
        on_delta = kwargs.get("on_delta")
        if on_delta is not None:
            on_delta("reasoning", "思考")
        if not any(m.get("role") == "tool" for m in messages):
            return _resp(tool_calls=[_tool_call("minecraft_capture", {"k": "v"}, "c1")])
        return _resp("done")

    llm = _RecordingLlm(fake)
    agent = _build_sink_agent(llm, sink, cap)

    await agent.start()
    await agent.send_prompt("cap")
    await _wait_until(lambda: len(cap.invocations) >= 1 and len(sink.calls) >= 1)
    await agent.stop()

    inv = cap.invocations[0]
    assert inv.round_id == sink.calls[0]["round_id"]
    assert inv.round_id.startswith("mc_")


@pytest.mark.asyncio
async def test_thinking_sink_none_keeps_existing_behavior() -> None:
    """sink=None 时：on_delta 为 None（不触发任何 sink 方法），ToolInvocation.round_id 保持空串。"""
    cap = _CapturingToolProvider()

    async def fake(messages, **kwargs):
        assert kwargs.get("on_delta") is None
        if not any(m.get("role") == "tool" for m in messages):
            return _resp(tool_calls=[_tool_call("minecraft_capture", {"k": "v"}, "c1")])
        return _resp("done")

    llm = _RecordingLlm(fake)
    agent = _build_sink_agent(llm, None, cap)

    await agent.start()
    await agent.send_prompt("cap")
    await _wait_until(lambda: len(cap.invocations) >= 1)
    await agent.stop()

    assert cap.invocations[0].round_id == ""
