"""MinecraftAgent 测试：工具契约 / ReAct 循环 / 事件 / assign / 装配"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.state import MinecraftAgentState
from src.agents.minecraft.tools import MinecraftToolProvider
from src.modules.events.payloads.game import GamePayload
from src.modules.llm.manager import LLMResponse
from src.modules.tools.models import ToolInvocation
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
    """minecraft_get_state 三元组：todo/notebook/recent_milestones。"""
    state = _make_state()
    provider = _make_provider(state)
    reg = ToolRegistry()
    reg.register_provider(provider)

    state.add_milestone("挖到钻石了！")
    state.set_notebook("矿脉在 Y=12")
    r = await reg.invoke(_invocation("minecraft_get_state", {}))
    assert r.success
    d = r.structured_content
    assert d["recent_milestones"] == ["挖到钻石了！"]
    assert d["notebook"] == "矿脉在 Y=12"
    assert "todo" in d


@pytest.mark.asyncio
async def test_minecraft_get_state_milestones_ring_buffer() -> None:
    """里程碑只保留最近 10 条（环形）。"""
    state = _make_state()
    for i in range(15):
        state.add_milestone(f"里程碑 {i}")
    assert len(state.milestones) == 10
    assert state.milestones[-1] == "里程碑 14"


@pytest.mark.asyncio
async def test_minecraft_assign_delivers_message() -> None:
    """minecraft_assign 命令通道：消息投递给 callback（不写 todo——分解是 LLM 的事）。"""
    received: list[str] = []

    async def cb(content: str) -> None:
        received.append(content)

    provider = MinecraftToolProvider(state=_make_state(), assign_callback=cb)
    reg = ToolRegistry()
    reg.register_provider(provider)

    r = await reg.invoke(_invocation("minecraft_assign", {"content": "挖 3 个钻石"}))
    assert r.success
    assert received == ["挖 3 个钻石"]
    # 系统不代写 todo（目标分解是 LLM 的行为）
    assert provider.state.todos == []


@pytest.mark.asyncio
async def test_minecraft_assign_without_callback_degrades() -> None:
    """assign 无 callback：降级成功返回（消息丢失记日志，不阻断调用方）。"""
    provider = _make_provider(_make_state())
    reg = ToolRegistry()
    reg.register_provider(provider)

    r = await reg.invoke(_invocation("minecraft_assign", {"content": "建房子"}))
    assert r.success
    assert r.structured_content["delivered"] is True


# ---------------------------------------------------------------------------
# MinecraftAgent：事件 / ReAct 循环
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minecraft_agent_emits_game_milestone_with_minecraft_tag() -> None:
    """emit game.milestone：payload.game == 'minecraft' + 里程碑进内存。"""
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()
    agent = MinecraftAgent(
        MinecraftConfig(),
        event_bus=event_bus,
        live_session_id="ls_test",
    )
    await agent.emit_milestone("挖到钻石了！")

    payload = event_bus.emit.await_args[0][1]
    assert isinstance(payload, GamePayload)
    assert payload.game == "minecraft"
    assert payload.message == "挖到钻石了！"
    assert payload.event_type == "milestone"

    assert agent.get_state_snapshot()["recent_milestones"] == ["挖到钻石了！"]


@pytest.mark.asyncio
async def test_react_natural_termination_emits_delivery() -> None:
    """LLM 无 tool_calls → 自然终止，最终文本作为里程碑交付汇报。"""
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
    await agent.assign("挖 3 个钻石")
    await asyncio.sleep(0.3)

    emitted = [c.args[1] for c in event_bus.emit.await_args_list]
    milestones = [p.message for p in emitted if isinstance(p, GamePayload) and p.event_type == "milestone"]
    assert any("钻石挖完了" in m for m in milestones)
    assert llm.chat_messages.await_count == 1  # 单轮自然终止
    await agent.stop()


@pytest.mark.asyncio
async def test_react_todo_write_emits_milestone_diff() -> None:
    """todo 项 → done 触发"完成任务：X"里程碑；再次写同状态不重复。"""
    llm = MagicMock()

    async def fake(messages, **kwargs):
        if not any(m.get("role") == "tool" for m in messages):
            # 第一轮：写一个 done 项
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
    await agent.assign("挖钻石")
    await asyncio.sleep(0.3)

    emitted = [c.args[1] for c in event_bus.emit.await_args_list]
    milestones = [p.message for p in emitted if isinstance(p, GamePayload) and p.event_type == "milestone"]
    completed = [m for m in milestones if "完成任务" in m]
    assert len(completed) == 1
    assert "挖钻石" in completed[0]
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
    await agent.assign("探索东侧")
    await asyncio.sleep(0.3)

    # 第一轮请求后，第二轮 messages 应含正确的喂回结构
    assert len(captured) >= 2
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
            from src.modules.tools.models import ToolSpec

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
            from src.modules.tools.models import ToolExecutionResult

            return ToolExecutionResult(
                tool_name=invocation.tool_name, success=True, structured_content={"ok": True, "view": "situation"}
            )

    registry.register_provider(FakeMcpProvider())

    async def fake(messages, **kwargs):
        tools_given = kwargs.get("tools") or []
        first = not any(m.get("role") == "tool" for m in messages)
        if first:
            assert any(t["name"] == "maicraft_perceive" for t in tools_given)  # 动态工具面含 MCP 工具
            assert any(t["name"] == "minecraft_todo" for t in tools_given)  # 局部工具面是注册名
            return _resp(tool_calls=[_tool_call("maicraft_perceive", {"view": "situation"})])
        return _resp("感知完成")

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
    await agent.assign("看看周围")
    await asyncio.sleep(0.3)

    assert registry.has("maicraft_perceive")
    await agent.stop()


@pytest.mark.asyncio
async def test_react_max_steps_emits_attention() -> None:
    """LLM 恒调用工具 → 步数超上限 → attention_required 挂起。"""
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
    await agent.assign("循环任务")
    await asyncio.sleep(0.5)

    emitted = [c.args[1] for c in event_bus.emit.await_args_list]
    attention = [p for p in emitted if isinstance(p, GamePayload) and p.event_type == "attention_required"]
    assert len(attention) == 1
    assert "上限" in attention[0].message
    assert llm.chat_messages.await_count == 3  # 恰好 max_steps 步
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
    await agent.assign("暂停任务")

    # 暂停循环：等待一步真实执行后挂起，计数冻结
    await asyncio.sleep(0.2)
    await agent.pause()
    frozen = call_count
    await asyncio.sleep(0.3)
    assert call_count == frozen  # 挂起后不再推理

    await agent.resume()
    await asyncio.sleep(0.3)
    assert call_count > frozen  # 恢复后继续
    await agent.stop()


@pytest.mark.asyncio
async def test_assign_wakes_worker_full_chain() -> None:
    """assign 全链路：registry 调 minecraft_assign → worker 唤醒 → 任务真实执行。"""
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

    r = await registry.invoke(_invocation("minecraft_assign", {"content": "工具链目标"}))
    assert r.success
    await asyncio.sleep(0.3)

    assert llm.chat_messages.awaited  # worker 被唤醒、任务真实执行
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

    await agent.assign("第一个任务")
    await asyncio.sleep(0.3)
    await agent.assign("第二个任务")
    await asyncio.sleep(0.3)

    assert calls == 2
    query = [m for m in agent._mc_state.milestones]
    assert len(query) >= 2  # 两次交付汇报
    await agent.stop()


@pytest.mark.asyncio
async def test_goal_without_llm_fails_fast() -> None:
    """无 LLM 时收到命令立即报错退出，不进任务循环（不空转）。"""
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(),
        llm_manager=None,
        event_bus=event_bus,
    )
    await agent.start()
    await agent.assign("挖钻石")
    await asyncio.sleep(0.2)

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
    await agent.assign("失败任务")
    await asyncio.sleep(0.2)

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
            from src.modules.tools.models import ToolSpec

            return [ToolSpec(name="maicraft_execute", description="执行", parameters_schema={"type": "object"}, kind="sync", provider="maicraft")]

        async def invoke(self, invocation: ToolInvocation):
            from src.modules.tools.models import ToolExecutionResult

            return ToolExecutionResult(tool_name=invocation.tool_name, success=False, error_message="world not loaded")

    registry.register_provider(FailingMcp())
    seen_failure: list[bool] = []

    async def fake(messages, **kwargs):
        # 第一轮：调用 MCP 工具 → 失败观察；第二轮：读到失败 → 自然终止
        if not any(m.get("role") == "tool" for m in messages):
            return _resp(tool_calls=[_tool_call("maicraft_execute", {"goal": "mine"})])
        seen_failure.append(any("world not loaded" in m.get("content", "") for m in messages if m.get("role") == "tool"))
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
    await agent.assign("挖矿")
    await asyncio.sleep(0.3)

    assert seen_failure == [True]  # 失败观察真实喂回
    await agent.stop()


@pytest.mark.asyncio
async def test_react_multi_tool_calls_batch_execute() -> None:
    """同轮多个 tool_calls：串行执行，结果全部喂回（消息顺序对应）。"""
    llm = MagicMock()

    async def fake(messages, **kwargs):
        if not any(m.get("role") == "tool" for m in messages):
            return _resp(
                tool_calls=[
                    _tool_call("minecraft_notebook", {"action": "write", "content": "笔记 A"}, "c1"),
                    _tool_call("minecraft_todo", {"action": "write", "todos": [{"content": "任务 B", "status": "in_progress"}]}, "c2"),
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
    await agent.assign("批量任务")
    await asyncio.sleep(0.3)

    snapshot = agent.get_state_snapshot()
    assert snapshot["notebook"] == "笔记 A"
    assert snapshot["todo"] == [{"content": "任务 B", "status": "in_progress"}]
    await agent.stop()


@pytest.mark.asyncio
async def test_command_after_task_reaches_messages() -> None:
    """执行中 assign 的消息进入对话（LLM 下一次推理吸收，系统不硬转向）。"""
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
    await agent.assign("初始任务")
    await asyncio.sleep(0.15)
    await agent.assign("追加指令")
    await asyncio.sleep(0.3)

    # 第二轮 messages 含追加指令（user 消息）
    assert len(seen_messages) >= 2
    user_msgs = [m for m in seen_messages[1] if m["role"] == "user"]
    assert any("追加指令" in m["content"] for m in user_msgs)
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
    assert [s.name for s in agent.list_tools()] == ["todo", "notebook", "get_state", "assign"]


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
    """空配置实例化 minecraft，行为参数取 Schema 默认值。"""
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
