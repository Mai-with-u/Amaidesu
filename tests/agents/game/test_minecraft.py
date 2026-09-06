"""MinecraftAgent 测试：工具契约 / 决策循环骨架 / 事件 / set_goal / 装配"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agents.game.minecraft.agent import MinecraftAgent
from src.agents.game.minecraft.config import MinecraftConfig
from src.agents.game.minecraft.state import MinecraftAgentState
from src.agents.game.minecraft.tools import MinecraftToolProvider
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.models import ToolInvocation
from src.modules.events.payloads.game import GamePayload


def _make_state() -> MinecraftAgentState:
    return MinecraftAgentState()


def _make_provider(state: MinecraftAgentState) -> MinecraftToolProvider:
    return MinecraftToolProvider(state=state)


def _invocation(name: str, arguments: dict) -> ToolInvocation:
    return ToolInvocation(tool_name=name, arguments=arguments, source="test")


# ---------------------------------------------------------------------------
# mc_todo / mc_memo / mc_get_state 工具契约
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mc_todo_read_write_full_document() -> None:
    """mc_todo 全量读写：write 覆盖、read 返回全文、无 id。"""
    provider = _make_provider(_make_state())
    reg = ToolRegistry()
    reg.register_provider(provider)

    # 初始 read 空
    r = await reg.invoke(_invocation("mc_todo", {"action": "read"}))
    assert r.success
    assert r.structured_content["todos"] == []

    # write 全量
    r = await reg.invoke(
        _invocation(
            "mc_todo",
            {"action": "write", "todos": [{"content": "挖钻石", "status": "in_progress"}]},
        )
    )
    assert r.success
    assert r.structured_content["todos"][0]["content"] == "挖钻石"
    assert r.structured_content["todos"][0]["status"] == "in_progress"

    # read 返回新全文
    r = await reg.invoke(_invocation("mc_todo", {"action": "read"}))
    assert r.success
    assert len(r.structured_content["todos"]) == 1


@pytest.mark.asyncio
async def test_mc_todo_write_overwrites() -> None:
    """write 覆盖旧文档（批量，无 id 定位）。"""
    provider = _make_provider(_make_state())
    reg = ToolRegistry()
    reg.register_provider(provider)

    await reg.invoke(_invocation("mc_todo", {"action": "write", "todos": [{"content": "a"}]}))
    await reg.invoke(
        _invocation(
            "mc_todo",
            {"action": "write", "todos": [{"content": "b"}, {"content": "c", "status": "done"}]},
        )
    )
    r = await reg.invoke(_invocation("mc_todo", {"action": "read"}))
    assert [t["content"] for t in r.structured_content["todos"]] == ["b", "c"]


@pytest.mark.asyncio
async def test_mc_memo_full_document() -> None:
    """mc_memo 全量读写。"""
    provider = _make_provider(_make_state())
    reg = ToolRegistry()
    reg.register_provider(provider)

    r = await reg.invoke(_invocation("mc_memo", {"action": "read"}))
    assert r.success
    assert r.structured_content["content"] == ""

    r = await reg.invoke(_invocation("mc_memo", {"action": "write", "content": "东侧 Y=12 有钻石"}))
    assert r.success

    r = await reg.invoke(_invocation("mc_memo", {"action": "read"}))
    assert r.structured_content["content"] == "东侧 Y=12 有钻石"


@pytest.mark.asyncio
async def test_mc_get_state_four_fields() -> None:
    """mc_get_state 四元组：goal/todo/memo/recent_milestones。"""
    state = _make_state()
    provider = _make_provider(state)
    reg = ToolRegistry()
    reg.register_provider(provider)

    state.set_goal("挖钻石")
    state.add_milestone("挖到钻石了！")
    state.set_memo("矿脉在 Y=12")
    r = await reg.invoke(_invocation("mc_get_state", {}))
    assert r.success
    d = r.structured_content
    assert d["current_goal"] == "挖钻石"
    assert d["recent_milestones"] == ["挖到钻石了！"]
    assert d["memo"] == "矿脉在 Y=12"
    assert "todo" in d


@pytest.mark.asyncio
async def test_mc_get_state_milestones_ring_buffer() -> None:
    """里程碑只保留最近 10 条（环形）。"""
    state = _make_state()
    for i in range(15):
        state.add_milestone(f"里程碑 {i}")
    assert len(state.milestones) == 10
    assert state.milestones[-1] == "里程碑 14"
    assert state.milestones[0] == "里程碑 5"


@pytest.mark.asyncio
async def test_mc_set_goal_command_channel() -> None:
    """mc_set_goal 命令通道：主播经 ToolRegistry 调 → 写入 current_goal。"""
    provider = _make_provider(_make_state())
    reg = ToolRegistry()
    reg.register_provider(provider)

    r = await reg.invoke(_invocation("mc_set_goal", {"goal": "挖 3 个钻石"}))
    assert r.success
    assert r.structured_content["current_goal"] == "挖 3 个钻石"

    # 状态快照可见
    r = await reg.invoke(_invocation("mc_get_state", {}))
    assert r.structured_content["current_goal"] == "挖 3 个钻石"


# ---------------------------------------------------------------------------
# MinecraftAgent：事件 / set_goal / 循环骨架
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

    # 事件发出
    event_bus.emit.assert_awaited()
    payload = event_bus.emit.await_args[0][1]
    assert isinstance(payload, GamePayload)
    assert payload.game == "minecraft"
    assert payload.message == "挖到钻石了！"
    assert payload.event_type == "milestone"

    # 里程碑同步内存（mc_get_state 数据源）
    assert agent.get_state_snapshot()["recent_milestones"] == ["挖到钻石了！"]


@pytest.mark.asyncio
async def test_minecraft_agent_set_goal_stores_goal() -> None:
    """set_goal 命令写入 current_goal（三通道·命令）。"""
    agent = MinecraftAgent(MinecraftConfig(), event_bus=MagicMock())
    await agent.set_goal("去挖钻石")
    assert agent.get_state_snapshot()["current_goal"] == "去挖钻石"


@pytest.mark.asyncio
async def test_minecraft_agent_decision_loop_runs_without_maicraft() -> None:
    """无 maicraft 时决策循环降级运行（骨架不报错）。"""
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()
    agent = MinecraftAgent(
        MinecraftConfig(tick_seconds=0.5),
        event_bus=event_bus,
        tool_registry=None,  # 无 maicraft / registry —— 降级路径
    )
    await agent.start()
    await asyncio.sleep(0.2)  # 跑 1 轮
    # 状态可读（todo 空、goal 空），无异常即通过
    assert agent.get_state_snapshot()["todo"] == []
    await agent.stop()


# ---------------------------------------------------------------------------
# 装配（factory 分派）
# ---------------------------------------------------------------------------


def test_factory_instantiates_minecraft() -> None:
    """factory engine=minecraft 分派到 MinecraftAgent。"""
    from src.modules.agents.factory import instantiate_agent

    agent = instantiate_agent(
        "game",
        {"engine": "minecraft", "tick_seconds": 5.0},
        llm_manager=None,
        prompt_manager=None,
        event_bus=MagicMock(),
        tool_registry=None,
    )
    assert isinstance(agent, MinecraftAgent)
    assert agent.typed_config.tick_seconds == 5.0
    assert [s.name for s in agent.list_tools()] == ["mc_todo", "mc_memo", "mc_get_state", "mc_set_goal"]


def test_factory_defaults_to_minecraft_engine() -> None:
    """无 engine 键时默认 minecraft（与 agents_schemas GameAgentConfig.engine 默认一致）。"""
    from src.modules.agents.factory import instantiate_agent

    agent = instantiate_agent(
        "game",
        {},
        llm_manager=None,
        prompt_manager=None,
        event_bus=MagicMock(),
        tool_registry=None,
    )
    assert isinstance(agent, MinecraftAgent)


# ---------------------------------------------------------------------------
# LLM 决策接入（think 不再 noop）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_decision_executes_action_and_emits() -> None:
    """LLM 决策（minecraft_decide tool_call）→ 执行动作 + emit 里程碑。"""
    from src.modules.llm.manager import LLMResponse

    llm = MagicMock()
    llm.call_tools = AsyncMock(
        return_value=LLMResponse(
            success=True,
            content="",
            tool_calls=[
                {
                    "name": "minecraft_decide",
                    "arguments": '{"next_action": "mine", "action_goal": "挖 3 个钻石", "visible_message": "开始挖钻石了", "should_write_memo": false}',
                }
            ],
        )
    )
    prom = MagicMock()
    prom.render = MagicMock(return_value="渲染后的决策提示词")

    # maicraft mock（execute 成功）
    maicraft = MagicMock()
    maicraft.perceive = AsyncMock(return_value={"ok": True})
    maicraft.execute = AsyncMock(return_value={"ok": True, "task_id": "t1"})

    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    agent = MinecraftAgent(
        MinecraftConfig(tick_seconds=0.5),
        llm_manager=llm,
        prompt_manager=prom,
        event_bus=event_bus,
        tool_registry=None,
        maicraft=maicraft,
    )
    await agent.start()
    await asyncio.sleep(0.25)  # 跑 1 轮

    # LLM 决策被调用（call_tools 收到决策函数）
    llm.call_tools.assert_awaited()
    # maicraft.execute 被调用（执行动作）
    maicraft.execute.assert_awaited()
    assert maicraft.execute.await_args[0][0] == {"action": "mine", "goal": "挖 3 个钻石"}
    # 里程碑事件发出
    assert event_bus.emit.awaited  # 至少一次
    await agent.stop()


@pytest.mark.asyncio
async def test_llm_decision_failure_degrades_to_noop() -> None:
    """LLM 决策失败 → 降级 noop（不中断循环，不执行动作）。"""
    from src.modules.llm.manager import LLMResponse

    llm = MagicMock()
    llm.call_tools = AsyncMock(return_value=LLMResponse(success=False, error="llm 挂了"))
    maicraft = MagicMock()
    maicraft.perceive = AsyncMock(return_value={"ok": False})
    maicraft.execute = AsyncMock(return_value={"ok": True})

    agent = MinecraftAgent(
        MinecraftConfig(tick_seconds=0.5),
        llm_manager=llm,
        prompt_manager=MagicMock(),
        event_bus=MagicMock(),
        tool_registry=None,
        maicraft=maicraft,
    )
    await agent.start()
    await asyncio.sleep(0.25)
    # 失败 → 无 execute 调用（noop 降级）
    maicraft.execute.assert_not_awaited()
    await agent.stop()
