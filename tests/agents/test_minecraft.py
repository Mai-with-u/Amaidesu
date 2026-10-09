"""MinecraftAgent 测试：工具契约 / ReAct 循环 / 事件 / 递话 / handoff / 装配"""

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.state import MinecraftAgentState
from src.agents.minecraft.tools import MinecraftToolProvider
from src.modules.agents.factory import instantiate_agent
from src.modules.events.payloads.agents import AgentRepliedPayload
from src.modules.events.payloads.game import GamePayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.mcp.config import McpServerConfig
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry

if TYPE_CHECKING:
    # 注解用前向引用：真实导入留在用到它的辅助函数内，测试模块级不拉起任务基建
    from src.modules.tools.tasks import TaskTracker


def _offline_config() -> MinecraftConfig:
    """不连私有 MCP 的配置：本机正开着 MaiCraft 时，测试也不能连上真实的 8766 端口。"""
    return MinecraftConfig(mcp=McpServerConfig(enabled=False, url="http://127.0.0.1:8766/mcp"))


def _make_state() -> MinecraftAgentState:
    return MinecraftAgentState()


def _make_provider(state: MinecraftAgentState) -> MinecraftToolProvider:
    return MinecraftToolProvider(state=state)


def _invocation(name: str, arguments: dict) -> ToolInvocation:
    return ToolInvocation(tool_name=name, arguments=arguments, source="test")


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> ToolCall:
    """中立扁平形态 tool_call（id/name/arguments，arguments 已解析为 dict）。"""
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _resp(content: str = "", tool_calls: list | None = None) -> Response:
    return Response(success=True, content=content, tool_calls=tool_calls or [])


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
async def test_minecraft_get_work_log_three_fields() -> None:
    """minecraft_get_work_log 三元组：todo/notebook/recent_reports。"""
    state = _make_state()
    provider = _make_provider(state)
    reg = ToolRegistry()
    reg.register_provider(provider)

    state.add_report("delivery", "挖到钻石了！", "y=-12")
    state.set_notebook("矿脉在 Y=12")
    r = await reg.invoke(_invocation("minecraft_get_work_log", {}))
    assert r.success
    d = r.structured_content
    assert d["recent_reports"] == [{"kind": "delivery", "content": "挖到钻石了！", "scene": "y=-12"}]
    assert d["notebook"] == "矿脉在 Y=12"
    assert "todo" in d


@pytest.mark.asyncio
async def test_minecraft_get_work_log_reports_ring_buffer() -> None:
    """上报只保留最近 10 条（环形）。"""
    state = _make_state()
    for i in range(15):
        state.add_report("delivery", f"上报 {i}")
    assert len(state.reports) == 10
    assert state.reports[-1]["content"] == "上报 14"


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
        _offline_config(),
        event_bus=event_bus,
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
    llm.generate = AsyncMock(return_value=_resp("钻石挖完了，共 5 颗。"))
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="挖 3 个钻石", source="test")
    await _wait_until(lambda: llm.generate.await_count == 1)

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

    llm.generate = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=llm,
        event_bus=event_bus,
        tool_registry=ToolRegistry(),
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="干活", source="test")
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
    """情形 2：LLM 调 report(escalation) → 停止，静默等主播递话唤醒。"""
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=_resp(tool_calls=[_tool_call("minecraft_report", {"kind": "escalation", "content": "需要授权"})])
    )
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=llm,
        event_bus=event_bus,
        tool_registry=ToolRegistry(),
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="遇到困难的任务", source="test")
    await _wait_until(lambda: llm.generate.await_count == 1)
    await asyncio.sleep(0.15)
    assert llm.generate.await_count == 1

    emitted = [c.args[1] for c in event_bus.emit.await_args_list]
    reports = _reports(emitted)
    assert len(reports) == 1 and reports[0].report_kind == "escalation"

    # 主播回复（递话）唤醒新批次（新 mock 独立计数，避免与第一批混淆）
    resume_mock = AsyncMock(return_value=_resp("收到授权，继续"))
    llm.generate = resume_mock
    agent.receive_prompt(content="授权通过了，继续", source="test")
    await _wait_until(lambda: resume_mock.await_count == 1)
    await agent.stop()


@pytest.mark.asyncio
async def test_react_continues_past_fifty_steps_until_delivery() -> None:
    """角色连续记录施工进展超过五十轮后仍能交付，无需玩家追加继续指令。"""
    llm = MagicMock()
    llm.generate = AsyncMock(
        side_effect=[
            _resp(tool_calls=[_tool_call("minecraft_notebook", {"action": "write", "content": f"施工进展 {step}"})])
            for step in range(1, 61)
        ]
        + [_resp("施工验收完成")]
    )
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(mcp=McpServerConfig(enabled=False)),
        llm_manager=llm,
        event_bus=event_bus,
        tool_registry=ToolRegistry(),
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    try:
        agent.receive_prompt(content="完成施工并验收", source="test")
        await _wait_until(lambda: llm.generate.await_count == 61 and agent._task_finished)
        emitted = [c.args[1] for c in event_bus.emit.await_args_list]
        assert not [p for p in emitted if isinstance(p, GamePayload) and p.event_type == "attention_required"]
        assert agent._task_steps == 61 and not agent._task_suspended
        assert agent.get_state_snapshot()["notebook"] == "施工进展 60"
        reports = _reports(emitted)
        assert len(reports) == 1 and reports[0].report_kind == "delivery"
    finally:
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

    llm.generate = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="挖钻石", source="test")
    await _wait_until(lambda: len(captured) == 2)

    emitted = [c.args[1] for c in event_bus.emit.await_args_list]
    assert not [p for p in emitted if isinstance(p, GamePayload) and p.event_type == "milestone"]
    # 自然终止无 report → 系统兜底 delivery 仍在（主播必收到一次交付）
    reports = _reports(emitted)
    assert len(reports) == 1 and reports[0].report_kind == "delivery"
    await agent.stop()


@pytest.mark.asyncio
async def test_react_full_format_feedback_and_id_association() -> None:
    """完整 OpenAI 格式作为观察返回：assistant.tool_calls + tool role + tool_call_id 关联。"""
    captured: list[dict] = []

    async def fake(messages: list[dict], **kwargs: Any) -> Response:
        captured.append(messages.copy())
        # 第二轮读取已写入的探索坐标后结束本任务，协议检查只需要一次真实工具往返。
        if len(captured) > 1:
            return _resp("探索坐标已记录")
        return _resp(
            tool_calls=[_tool_call("minecraft_notebook", {"action": "write", "content": "坐标 (10,20)"}, "call_x")]
        )

    llm = MagicMock()
    llm.generate = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=llm,
        event_bus=event_bus,
        tool_registry=ToolRegistry(),
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="探索东侧", source="test")
    await _wait_until(lambda: len(captured) >= 2)

    # 第二轮请求的 messages 应含正确的作为观察返回结构
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
        name = "maicraft"

        def list_tools(self):
            return [
                ToolSpec(
                    name="perceive",
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

    # MCP 透传工具显式声明可见名单（默认名单仅主播，minecraft 需显式可见）
    registry.register_provider(FakeMcpProvider(), visible_to={"maicraft_perceive": ["minecraft"]})

    async def fake(messages, **kwargs):
        fake.calls += 1
        tools_given = kwargs.get("tools") or []
        first = not any(m.get("role") == "tool" for m in messages)
        if first:
            assert any(t["name"] == "maicraft_perceive" for t in tools_given)  # 动态工具列表含 MCP 工具
            assert any(t["name"] == "minecraft_todo" for t in tools_given)  # 局部工具列表是注册名
            return _resp(tool_calls=[_tool_call("maicraft_perceive", {"view": "situation"})])
        return _resp("感知完成")

    fake.calls = 0
    llm = MagicMock()
    llm.generate = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=llm,
        event_bus=event_bus,
        tool_registry=registry,
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="看看周围", source="test")
    await _wait_until(lambda: fake.calls == 2)

    assert registry.has("maicraft_perceive")
    await agent.stop()


@pytest.mark.asyncio
async def test_react_pause_suspends_loop() -> None:
    """平台 pause：任务循环在步骤间挂起（不调 LLM），resume 后继续。"""
    call_count = 0

    async def fake(messages: list[dict], **kwargs: Any) -> Response:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.005)  # 模拟推理等待，让平台暂停命令能在下一次游戏行动前到达。
        return _resp(tool_calls=[_tool_call("maicraft_perceive", {"view": "situation"})])

    async def observe(name: str, arguments: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        """持续变化的建材数量让任务保持推进，暂停测试不靠反复读同一待办制造空转。"""
        return {"inventory": {"minecraft:stone": call_count}}

    llm = MagicMock()
    llm.generate = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=llm,
        event_bus=event_bus,
    )
    agent._execute_tool = AsyncMock(side_effect=observe)
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="暂停任务", source="test")

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
async def test_instruction_injection_wakes_worker_full_chain() -> None:
    """指令注入全链路：递话（系统/测试通道）→ worker 唤醒 → 任务真实执行。

    递话与委派是并列原语：递话不进账本、任务号空串；跨 Agent 派活的
    工具链验证见 tests/modules/agents/test_delegation.py。
    """
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=_resp("收到，开始执行"))
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    registry = ToolRegistry()
    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=llm,
        event_bus=event_bus,
        tool_registry=registry,
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中

    agent.receive_prompt(content="工具链目标", source="test")
    await _wait_until(lambda: llm.generate.awaited)

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
    llm.generate = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中

    agent.receive_prompt(content="第一个任务", source="test")
    await _wait_until(lambda: calls == 1)
    agent.receive_prompt(content="第二个任务", source="test")
    await _wait_until(lambda: calls == 2)

    assert len(agent.get_state_snapshot()["recent_reports"]) >= 2  # 两次兜底交付
    await agent.stop()


@pytest.mark.asyncio
async def test_prompt_without_llm_fails_fast() -> None:
    """无 LLM 时收到命令立即报错退出，不进任务循环（不空转）。"""
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=None,
        event_bus=event_bus,
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="挖钻石", source="test")
    await _wait_until(lambda: bool(event_bus.emit.await_args_list))

    emitted = [c.args[1] for c in event_bus.emit.await_args_list]
    errors = [p for p in emitted if isinstance(p, GamePayload) and p.event_type == "error"]
    assert len(errors) == 1
    await agent.stop()


@pytest.mark.asyncio
async def test_idle_costs_nothing() -> None:
    """命令驱动：空闲（无命令）时不产生任何 LLM 调用。"""
    llm = MagicMock()
    llm.generate = AsyncMock()

    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=llm,
        event_bus=MagicMock(),
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    await asyncio.sleep(0.4)

    llm.generate.assert_not_awaited()
    await agent.stop()


@pytest.mark.asyncio
async def test_llm_call_failure_emits_error() -> None:
    """LLM 调用失败（success=False）→ game.error，任务退出。"""
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=False, error="rate limit"))
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="失败任务", source="test")
    await _wait_until(lambda: bool(event_bus.emit.await_args_list))

    emitted = [c.args[1] for c in event_bus.emit.await_args_list]
    errors = [p for p in emitted if isinstance(p, GamePayload) and p.event_type == "error"]
    assert len(errors) == 1
    assert "rate limit" in errors[0].message
    await agent.stop()


@pytest.mark.asyncio
async def test_react_small_history_keeps_old_observations() -> None:
    """超过十条观察仍保留已发送消息，只有体积超预算才集中整理。"""
    agent = MinecraftAgent(_offline_config(), event_bus=MagicMock())
    messages: list[dict] = [{"role": "system", "content": "玩家任务"}]
    for i in range(12):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": f"c{i}", "type": "function", "function": {"name": "observe", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": f"c{i}", "content": f"obs-{i}"},
            ]
        )
    before = [message.copy() for message in messages]
    assert await agent._prepare_context(messages, [])
    assert messages == before and agent._context_compactor.checkpoints == 0


@pytest.mark.asyncio
async def test_react_tool_failure_fed_back_to_llm() -> None:
    """MCP 工具失败（ok:false）作为观察作为观察返回 LLM，循环继续（ReAct 标准，LLM 自调整）。"""
    registry = ToolRegistry()

    class FailingMcp(BaseToolProvider):
        category = "game"
        name = "maicraft"

        def list_tools(self):
            return [
                ToolSpec(
                    name="execute",
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
    llm.generate = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=llm,
        event_bus=event_bus,
        tool_registry=registry,
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="挖矿", source="test")
    await _wait_until(lambda: len(seen_failure) >= 1)

    assert seen_failure == [True]  # 失败观察真实作为观察返回
    await agent.stop()


@pytest.mark.asyncio
async def test_react_multi_tool_calls_batch_execute() -> None:
    """同轮多个 tool_calls：串行执行，结果全部作为观察返回（消息顺序对应）。"""
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

    llm.generate = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=llm,
        event_bus=event_bus,
        tool_registry=ToolRegistry(),
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="批量任务", source="test")
    # 两个工具都执行后即可核对状态；仍有施工待办时，父循环可以继续提醒推进而不是虚报完成。
    await _wait_until(lambda: calls >= 2)

    snapshot = agent.get_state_snapshot()
    assert snapshot["notebook"] == "笔记 A"
    assert snapshot["todo"] == [{"content": "任务 B", "status": "in_progress"}]
    # 批量写入后模型可能已触发阻塞上报，异步时序不影响此处真正要禁止的虚假交付。
    assert all(report["kind"] == "escalation" for report in snapshot["recent_reports"])
    assert not agent._task_finished
    await agent.stop()


@pytest.mark.asyncio
async def test_command_after_task_reaches_messages() -> None:
    """执行中递话的消息进入对话（LLM 下一次推理吸收，系统不硬转向）。"""
    seen_messages: list[dict] = []

    async def fake(messages, **kwargs):
        seen_messages.append(messages.copy())
        return _resp(tool_calls=[])

    llm = MagicMock()
    llm.generate = fake
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        _offline_config(),
        llm_manager=llm,
        event_bus=event_bus,
    )
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="初始任务", source="test")
    await asyncio.sleep(0.15)
    agent.receive_prompt(content="追加指令", source="test")
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
    name = "maicraft"

    def __init__(self, task_states: Dict[str, Dict[str, Any]] | None = None) -> None:
        self.task_states: Dict[str, Dict[str, Any]] = task_states if task_states is not None else {}
        self.get_calls: List[str] = []
        self.notify_callbacks: List[Any] = []
        self.attention_unsubscribed = 0

    def list_tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name="maicraft_execute",
                description="执行（受理型）",
                parameters_schema={"type": "object"},
                kind="sync",
                provider="maicraft",
            ),
            ToolSpec(
                name="maicraft_task",
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

    # ----- 任务适配器（对齐真实 McpToolProvider 的绑定处声明形态） -----

    async def query_task(self, task_id: str):
        snap = self.task_states.get(task_id)
        if snap is None:
            return None
        raw = str(snap.get("state", ""))
        mapped = _MAICRAFT_TEST_STATUS_MAP.get(raw, raw)
        return {"status": mapped, "snapshot": dict(snap), "summary": raw}

    def subscribe_task_notifications(self, callback):
        self.notify_callbacks.append(callback)

        def _unsubscribe() -> None:
            if callback in self.notify_callbacks:
                self.notify_callbacks.remove(callback)
            self.attention_unsubscribed += 1

        return _unsubscribe

    def fire_attention(self) -> None:
        """测试模拟执行侧通知（举旗级，可丢）。"""
        for cb in list(self.notify_callbacks):
            cb("")


# MaiCraft 原始状态 → 任务词表（与 agent 绑定处 _MAICRAFT_TASK_STATUS_MAP 同款）
_MAICRAFT_TEST_STATUS_MAP = {
    "pending": "accepted",
    "running": "running",
    "waiting_for_decision": "waiting_for_decision",
    "success": "succeeded",
    "failed": "failed",
    "timeout": "timeout",
    "cancelled": "cancelled",
}


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

    async def generate(self, input: Any, **kwargs: Any) -> Response:
        self.captured.append([m.copy() for m in input])
        return await self._script(input, **kwargs)


def _make_task_agent(
    llm: Any,
    registry: ToolRegistry,
    *,
    wait_timeout_ms: int = 1_800_000,
    poll_interval_ms: int = 20,
) -> tuple[MinecraftAgent, "TaskTracker"]:
    """构造接入通用任务基建的 MinecraftAgent（真实 EventBus 驱动 task.changed 唤醒链）。"""
    from src.modules.events.event_bus import EventBus
    from src.modules.tools.tasks import TaskLedger, TaskTracker

    bus = EventBus(enable_stats=False)
    ledger = TaskLedger(event_bus=bus)
    tracker = TaskTracker(registry, ledger, poll_interval_ms=poll_interval_ms, wait_timeout_ms=wait_timeout_ms)
    agent = MinecraftAgent(
        MinecraftConfig(mcp=McpServerConfig(enabled=False)),
        llm_manager=llm,
        event_bus=bus,
        tool_registry=registry,
        task_tracker=tracker,
    )
    return agent, tracker


@pytest.mark.asyncio
async def test_unfinished_todos_block_natural_and_explicit_delivery() -> None:
    """模型停下或声称完成，都不能把仍在施工的待办伪装成成功；真实完成后才可交付。"""
    llm = MagicMock()
    llm.generate = AsyncMock(
        side_effect=[
            _resp(
                tool_calls=[
                    _tool_call(
                        "minecraft_todo", {"action": "write", "todos": [{"content": "施工", "status": "in_progress"}]}
                    )
                ]
            ),
            _resp("完成了"),
            _resp("仍然没有实际动作"),
        ]
    )
    bus = MagicMock()
    bus.emit = AsyncMock()
    agent = MinecraftAgent(_offline_config(), llm_manager=llm, event_bus=bus, tool_registry=ToolRegistry())
    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    try:
        agent.receive_prompt(content="建好房屋", source="test")
        await _wait_until(lambda: agent._task_suspended)
        # 停止自动行动必须上报阻塞；它仍不能冒充施工完成，也不能清除尚未完成的目标。
        reports = agent.get_state_snapshot()["recent_reports"]
        assert reports and all(report["kind"] == "escalation" for report in reports)
        assert not agent._task_finished
        assert "未完成待办" in await agent._handle_report("delivery", "已建好", "")
        agent._mc_state.set_todos([{"content": "施工", "status": "done"}])
        assert await agent._handle_report("delivery", "施工已验证完成", "") is None
        assert agent._task_finished
    finally:
        await agent.stop()


# ---------------------------------------------------------------------------
# 装配（factory 分派）
# ---------------------------------------------------------------------------


def test_factory_rejects_legacy_game_name() -> None:
    """分类层已移除，旧注册名 "game" 不再可实例化（防分类层复活）。"""

    agent = instantiate_agent(
        "game",
        {},
        llm_manager=None,
        prompt_manager=None,
        event_bus=MagicMock(),
        tool_registry=None,
    )
    assert agent is None


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
    """McpToolProvider 替身：setup() 返回指定工具数；list_tools 暴露缓存 specs。

    对齐真实 McpToolProvider 命名模型：name = provider（默认 server 名）；
    spec 声明名 = server 原始名，全名 = <provider>_<原始名> 派生。
    """

    category = "mcp"

    def __init__(
        self,
        *,
        client: Any,
        server_name: str,
        provider: str | None = None,
    ) -> None:
        self.client = client
        self.server_name = server_name
        self._provider = provider or server_name
        self._specs: list = []
        self._setup_called = False
        # 对齐真实 McpToolProvider：清单（重）同步成功后的绑定回调（绑定处赋值）
        self.on_tools_refreshed: Any = None
        # 强制构造一次工具列表：模拟 server 暴露 2 个 maicraft 工具
        self._tool_count = 2
        # 注意流适配（身体事件增量读取）：可配页、可注入读取异常、记录每次读取入参
        self.attention_pages: List[Dict[str, Any]] = []
        self.attention_reads: List[Dict[str, Any]] = []
        self.attention_callbacks: List[Any] = []
        self.attention_read_error: Exception | None = None

    @property
    def name(self) -> str:
        return self._provider

    async def setup(self) -> int:
        self._setup_called = True
        self._specs = [
            ToolSpec(
                name=tool_name,
                description=f"desc {tool_name}",
                kind="sync",
                provider=self._provider,
            )
            for tool_name in ("perceive", "execute")
        ]
        count = self._tool_count
        if count > 0 and self.on_tools_refreshed is not None:
            self.on_tools_refreshed(count)
        return count

    def list_tools(self):
        return list(self._specs)

    async def invoke(self, invocation: ToolInvocation) -> Any:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    async def read_attention(
        self, *, stream_id: str | None = None, after_cursor: int = 0, limit: int = 10
    ) -> Dict[str, Any] | None:
        self.attention_reads.append({"stream_id": stream_id, "after_cursor": after_cursor, "limit": limit})
        if self.attention_read_error is not None:
            raise self.attention_read_error
        return self.attention_pages.pop(0) if self.attention_pages else None

    def subscribe_task_notifications(self, callback) -> Any:
        self.attention_callbacks.append(callback)

        def _unsubscribe() -> None:
            self.attention_callbacks.remove(callback)

        return _unsubscribe

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
    agent._live_active = True  # 测试模拟直播中

    assert constructed_clients == [], "enabled=false 不应实例化 McpClient"
    assert constructed_providers == [], "enabled=false 不应实例化 McpToolProvider"
    assert registry.list_tools(provider="maicraft") == []
    assert agent._mcp_client is None
    await agent.stop()


@pytest.mark.asyncio
async def test_on_start_setup_failure_does_not_block_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """setup() 抛异常：装配被 try/except 兜底，Agent 仍正常 start（命令驱动降级）。

    新契约：失败不再丢弃——provider 以 0 工具降级登记（工具页可见、可手动
    重连），并启动后台恢复循环；stop() 取消循环并摘除 provider。
    """
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
    assert registry.list_tools(provider="maicraft") == [], "setup 抛异常时不注册任何工具"
    assert agent._running is True, "Agent 仍应进入运行态（MCP 不可用仅降级）"
    assert agent._mcp_client is not None, "降级保留 client 引用（恢复重试复用同一连接设施）"
    records = {r["name"]: r for r in registry.list_providers()}
    assert records["maicraft"]["tool_count"] == 0, "provider 以 0 工具降级登记"
    assert agent._mcp_recover_task is not None, "降级后启动后台恢复循环"
    await agent.stop()
    assert agent._mcp_recover_task is None, "stop() 取消恢复循环"
    assert all(r["name"] != "maicraft" for r in registry.list_providers()), "stop() 摘除降级 provider"


@pytest.mark.asyncio
async def test_on_start_zero_tools_registers_degraded_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """setup() 返回 0（连接失败 / server 无工具）：降级登记 + 后台退避重试装配。

    新契约：连接失败不再 close-and-drop——provider 常驻登记（0 工具，工具页
    可见、可手动重连），恢复循环连上后刷新 registry 工具集；stop() 取消循环
    并摘除 provider。
    """
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
    agent._live_active = True  # 测试模拟直播中
    assert registry.list_tools(provider="maicraft") == [], "count=0 时不注册任何工具"
    assert agent._mcp_client is not None, "降级保留 client 引用"
    assert agent._mcp_recover_task is not None, "降级后启动后台恢复循环"
    records = {r["name"]: r for r in registry.list_providers()}
    assert records["maicraft"]["tool_count"] == 0, "provider 以 0 工具降级登记（工具页可见）"
    await agent.stop()
    assert agent._mcp_recover_task is None, "stop() 取消恢复循环"
    assert all(r["name"] != "maicraft" for r in registry.list_providers()), "stop() 摘除降级 provider"


class _RecordingSink:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def on_thinking_delta(self, *, round_id: str, phase: str, step: int, seq: int, text_delta: str) -> None:
        self.calls.append({"round_id": round_id, "phase": phase, "step": step, "seq": seq, "text_delta": text_delta})


class _CapturingToolProvider(BaseToolProvider):
    """记录每次 ToolInvocation（用于断言任务内 round_id 透传到工具列表）。"""

    category = "minecraft"
    name = "minecraft"

    def __init__(self) -> None:
        self.invocations: List[ToolInvocation] = []

    def list_tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name="capture",
                description="cap",
                parameters_schema={"type": "object"},
                kind="sync",
                provider="minecraft",
            )
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        self.invocations.append(invocation)
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content={"ok": True})


def _build_sink_agent(
    llm: Any,
    sink: Optional[Any],
    capturing: _CapturingToolProvider,
    bus: Optional[Any] = None,
) -> MinecraftAgent:
    registry = ToolRegistry()
    registry.register_provider(capturing)

    return MinecraftAgent(
        MinecraftConfig(mcp=McpServerConfig(enabled=False)),
        llm_manager=llm,
        event_bus=bus or _make_event_bus(),
        tool_registry=registry,
        thinking_sink=sink,
    )


@pytest.mark.asyncio
async def test_thinking_sink_receives_reasoning_with_minecraft_phase() -> None:
    """sink 注入后：LLM reasoning delta → sink.on_thinking_delta，phase=minecraft，seq 跨步单调递增；content 增量不转发（响应正文走 agent.replied 事件）。"""
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
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="两步", source="test")
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
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="cap", source="test")
    await _wait_until(lambda: len(cap.invocations) >= 1 and len(sink.calls) >= 1)
    await agent.stop()

    inv = cap.invocations[0]
    assert inv.round_id == sink.calls[0]["round_id"]
    assert inv.round_id.startswith("mc_")


@pytest.mark.asyncio
async def test_agent_replied_emitted_for_intermediate_steps_only() -> None:
    """中间工具调用步骤且正文非空 → 发 agent.replied（round/step 齐全，与思考轮同键）；
    自然终止轮的正文走 game.report 交付卡，不再重复发。"""
    bus = _make_event_bus()
    sink = _RecordingSink()
    cap = _CapturingToolProvider()

    async def fake(messages, **kwargs):
        if not any(m.get("role") == "tool" for m in messages):
            return _resp(
                "目标解释：用切石机把石头加工成石砖",
                tool_calls=[_tool_call("minecraft_capture", {"v": 1}, "c1")],
            )
        return _resp("任务完成，石砖已交付")

    agent = _build_sink_agent(_RecordingLlm(fake), sink, cap, bus=bus)

    await agent.start()
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="做石砖", source="test")
    await _wait_until(lambda: any(isinstance(c.args[1], GamePayload) for c in bus.emit.await_args_list))
    await agent.stop()

    replied = [c.args[1] for c in bus.emit.await_args_list if isinstance(c.args[1], AgentRepliedPayload)]
    assert len(replied) == 1, "只有中间工具调用步骤发响应事实"
    payload = replied[0]
    assert payload.agent == "minecraft"
    assert payload.content == "目标解释：用切石机把石头加工成石砖"
    assert payload.step == 1
    assert payload.round_id.startswith("mc_"), "响应事实带思考轮关联键（sink 注入即生成）"


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
    agent._live_active = True  # 测试模拟直播中
    agent.receive_prompt(content="cap", source="test")
    await _wait_until(lambda: len(cap.invocations) >= 1)
    await agent.stop()

    assert cap.invocations[0].round_id == ""


@pytest.mark.asyncio
async def test_own_tool_invoke_emits_tool_result_event() -> None:
    """统一调用路径：minecraft 自工具（minecraft_todo）经 registry 调用产生 tool.result 观测。"""
    from src.modules.events.event_bus import EventBus
    from src.modules.events.payloads.tool_result import ToolResultPayload

    bus = EventBus(enable_stats=False)
    try:
        received: list[tuple[str, ToolResultPayload]] = []

        async def _on_result(event_name: str, payload: ToolResultPayload, source: str) -> None:
            received.append((event_name, payload))

        bus.on("tool.result.#", _on_result, ToolResultPayload)

        registry = ToolRegistry(event_bus=bus)
        registry.register_provider(_make_provider(_make_state()))

        agent = MinecraftAgent(
            MinecraftConfig(mcp=McpServerConfig(enabled=False)),
            llm_manager=MagicMock(),
            event_bus=MagicMock(),
            tool_registry=registry,
        )
        await agent.start()
        agent._live_active = True  # 测试模拟直播中
        try:
            obs = await agent._execute_tool("minecraft_todo", {"action": "read"})
            assert obs.get("tool") == "todo"
            await asyncio.sleep(0.05)  # emit 为 fire-and-forget
            assert any(name == "tool.result.minecraft_todo" and p.status == "success" for name, p in received), (
                f"应观测到 tool.result.minecraft_todo，实际: {[(n, p.status) for n, p in received]}"
            )
        finally:
            await agent.stop()
    finally:
        await bus.cleanup()


# ---------------------------------------------------------------------------
# S3：上报契约（任务上下文 + 发生时刻 + 是否已结束）
# ---------------------------------------------------------------------------


class _CapturingBus:
    """捕获 GamePayload 的 bus 替身（上报契约测试用）。"""

    def __init__(self) -> None:
        self.payloads: List[GamePayload] = []

    async def emit(self, event_name: str, payload: Any, **kwargs: Any) -> None:
        if isinstance(payload, GamePayload):
            self.payloads.append(payload)
