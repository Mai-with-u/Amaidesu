"""TextAdvGameAgent 单元测试 + 感知-推进-闭环 QA Scenario（Wave 7）

QA Scenario（acceptance criteria）：
    Tool: Bash
    Preconditions: 注入 mock perception（FakeScreenCapture + FakeTextReader）
                   + FakeContentEngine + ToolRegistry 注册公用 look_at_screen
                   + TextAdvGameAgent 专属 choose_option / get_story
    Steps:
      1. 实例化 TextAdvGameAgent，注入一段 mock 屏幕文本 + 选项
      2. 调用 feed_state_change() 触发一次感知-推进闭环
      3. 断言 perception 被调用（look_at_screen tool）
      4. 断言 advance 被触发（choose_option tool → content_engine.send_input）
      5. 断言 game.milestone 事件被 emit
      6. 断言循环闭合（perception_count >= 1, advance_count >= 1）
    Expected Result: 感知-推进-循环闭路（mock 环境）
    Evidence: .omo/evidence/w7-game-agent.txt

覆盖：
- BaseAgent 协议六面在游戏 Agent 上的具体落地
- 感知工具复用（look_at_screen 通过 ToolRegistry.invoke 调用）
- 推进工具自备（choose_option provider="game"）
- content_engine 控制面（send_input 触发 FakeContentEngine 记录）
- 内部状态机（TextAdvGameAgentState：场景/选项/历史/去重）
- game.* 事件 emit
- main.py wiring：build_text_adv_agent 工厂 + AgentManager.register
- 优雅降级：无 ScreenCapture 后端时 look_at_screen 返回成功+空文本
- 优雅降级：无 content_engine 时 StubContentEngine 默认提供
- 框架零改动证明：BaseAgent / ToolRegistry / EventBus / GamePayload 均无变更
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Dict, List

import pytest

from src.agents.game.text_adv import (
    TextAdvGameAgent,
    TextAdvGameAgentState,
    TextAdvGameConfig,
    build_text_adv_agent,
)
from src.agents.game.text_adv.state import TextAdvOption
from src.modules.agents import AgentManager, AgentState
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.tools.content_engine import (
    ContentEngineProvider,
    FakeContentEngine,
)
from src.modules.tools.perception import (
    FakeScreenCapture,
    FakeTextReader,
    LookAtScreenProvider,
)
from src.modules.tools.registry import ToolRegistry


# =============================================================================
# Fixtures
# =============================================================================


def _make_options() -> List[TextAdvOption]:
    """构造示例选项：第一个默认选择。"""
    return [
        TextAdvOption(
            option_id="opt_1",
            label="继续前进",
            advance_kind="key",
            advance_key="enter",
        ),
        TextAdvOption(
            option_id="opt_2",
            label="回头看看",
            advance_kind="key",
            advance_key="left",
        ),
    ]


@pytest.fixture
def perception_capture() -> FakeScreenCapture:
    """注入模拟 ScreenCapture（Wave 7 范式验证核心）。"""
    capture = FakeScreenCapture()
    # 预置一张 PNG（fake bytes；本测试只关心是否调用 capture，不解析像素）
    capture.queue_png(b"\x89PNG_FAKE_BYTES", width=1920, height=1080)
    capture.queue_png(b"\x89PNG_FAKE_BYTES_2", width=1920, height=1080)
    return capture


@pytest.fixture
def text_reader() -> FakeTextReader:
    """注入模拟 TextReader，返回预置的剧情文本。"""
    reader = FakeTextReader()
    reader.queue_text("（场景：村口；遇到 NPC）\n1) 继续前进\n2) 回头看看")
    reader.queue_text("（场景：村口；遇到 NPC）\n1) 继续前进\n2) 回头看看")
    return reader


@pytest.fixture
def content_engine() -> FakeContentEngine:
    """注入 FakeContentEngine，记录所有 send_input 调用。"""
    return FakeContentEngine(engine_kind="text_adv")


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
async def started_agent(
    perception_capture: FakeScreenCapture,
    text_reader: FakeTextReader,
    content_engine: FakeContentEngine,
    event_bus: EventBus,
) -> AsyncGenerator[Dict[str, object], None]:
    """构造并启动 TextAdvGameAgent；返回测试辅助 dict（含所有 mock 引用）。"""
    registry = ToolRegistry()
    # 1) 注册公用感知工具（look_at_screen）
    look_provider = LookAtScreenProvider(
        screen_capture=perception_capture,
        text_reader=text_reader,
    )
    registry.register_provider(look_provider)

    # 2) 注册 content_engine 控制面
    ce_provider = ContentEngineProvider(engine=content_engine)
    registry.register_provider(ce_provider)

    # 3) 构造 Agent；Agent 自己会在 _on_start 中注册 choose_option / get_story
    manager = AgentManager(tool_registry=registry)
    agent = build_text_adv_agent(
        config=TextAdvGameConfig(),
        agent_manager=manager,
        content_engine=content_engine,
        event_bus=event_bus,
        tool_registry=registry,
        live_session_id="test_session_w7",
    )

    await agent.start()

    yield {
        "agent": agent,
        "manager": manager,
        "registry": registry,
        "look_provider": look_provider,
        "ce_provider": ce_provider,
        "content_engine": content_engine,
        "perception_capture": perception_capture,
        "text_reader": text_reader,
        "event_bus": event_bus,
    }

    await agent.cleanup()


# =============================================================================
# BaseAgent 协议六面（落地在 TextAdvGameAgent）
# =============================================================================


def test_text_adv_agent_metadata() -> None:
    """协议 6：name / description。"""
    assert TextAdvGameAgent.name == "game"
    assert "文字冒险" in TextAdvGameAgent.description


def test_text_adv_agent_emits_game_events() -> None:
    """协议 3：声明事件族（game.* 三类）。"""
    assert CoreEvents.GAME_MILESTONE in TextAdvGameAgent.emits_events
    assert CoreEvents.GAME_ATTENTION_REQUIRED in TextAdvGameAgent.emits_events
    assert CoreEvents.GAME_ERROR in TextAdvGameAgent.emits_events


def test_text_adv_agent_list_tools_returns_game_provider_specs() -> None:
    """协议 2：list_tools 暴露 choose_option + get_story（provider="game"）。"""
    config = TextAdvGameConfig()
    agent = TextAdvGameAgent(config=config)
    specs = list(agent.list_tools())
    assert len(specs) == 2
    names = {s.name for s in specs}
    assert names == {"choose_option", "get_story"}
    for s in specs:
        assert s.provider == "game"
        assert s.kind == "sync"


def test_text_adv_agent_factory_registers_in_manager() -> None:
    """工厂 build_text_adv_agent：构造 + register 到 AgentManager。"""
    manager = AgentManager()
    agent = build_text_adv_agent(
        config=TextAdvGameConfig(),
        agent_manager=manager,
        live_session_id="test",
    )
    assert "game" in manager
    assert manager.get("game") is agent


# =============================================================================
# 内部状态机（§1.31 内容状态内部自由）
# =============================================================================


def test_game_state_change_detection_via_hash() -> None:
    """TextAdvGameAgentState：apply_screen_text 去重（哈希相同 → 不算变化）。"""
    state = TextAdvGameAgentState()
    assert state.apply_screen_text("hello") is True
    assert state.apply_screen_text("hello") is False
    assert state.apply_screen_text("world") is True


def test_game_state_pick_default_returns_first_option() -> None:
    """决策策略（Wave 7 简化版）：pick_default_option → 首选项。"""
    state = TextAdvGameAgentState()
    state.set_options(_make_options())
    chosen = state.pick_default_option()
    assert chosen is not None
    assert chosen.option_id == "opt_1"
    assert state.history == ["opt_1"]


def test_game_state_pick_default_returns_none_when_empty() -> None:
    state = TextAdvGameAgentState()
    assert state.pick_default_option() is None


# =============================================================================
# 协议 1：生命周期（start → RUNNING → stop → STOPPED）
# =============================================================================


async def test_agent_lifecycle() -> None:
    """start → RUNNING；stop → STOPPED；cleanup 幂等。"""
    registry = ToolRegistry()
    manager = AgentManager(tool_registry=registry)
    agent = build_text_adv_agent(
        config=TextAdvGameConfig(),
        agent_manager=manager,
        content_engine=FakeContentEngine(),
        tool_registry=registry,
    )
    assert agent.state == AgentState.CREATED
    await agent.start()
    assert agent.state == AgentState.RUNNING
    await agent.stop()
    assert agent.state == AgentState.STOPPED
    await agent.cleanup()


async def test_agent_start_registers_own_tools() -> None:
    """_on_start：自动注册 choose_option / get_story 到 ToolRegistry。"""
    registry = ToolRegistry()
    manager = AgentManager(tool_registry=registry)
    agent = build_text_adv_agent(
        config=TextAdvGameConfig(),
        agent_manager=manager,
        content_engine=FakeContentEngine(),
        tool_registry=registry,
    )
    assert "choose_option" not in registry
    assert "get_story" not in registry
    await agent.start()
    assert registry.has("choose_option")
    assert registry.has("get_story")
    await agent.stop()


# =============================================================================
# 核心 QA Scenario：感知-推进-循环闭环（acceptance criteria）
# =============================================================================


async def test_perception_advance_loop_closed(
    started_agent: Dict[str, object],
) -> None:
    """Wave 7 acceptance：feed_state_change 触发感知 + 推进 + 循环闭合。

    Steps:
      1. 注入 mock 屏幕文本 + 选项
      2. 调用 feed_state_change() 触发一次闭环
      3. 断言 look_at_screen 被调用（perception_count == 1）
      4. 断言 choose_option 被触发（advance_count == 1）
      5. 断言 content_engine.send_input 被调用（content_engine.sent_inputs 长度 == 1）
      6. 断言 game.milestone 事件被 emit
    """
    agent: TextAdvGameAgent = started_agent["agent"]  # type: ignore[assignment]
    look_provider: LookAtScreenProvider = started_agent["look_provider"]  # type: ignore[assignment]
    content_engine: FakeContentEngine = started_agent["content_engine"]  # type: ignore[assignment]
    perception_capture: FakeScreenCapture = started_agent["perception_capture"]  # type: ignore[assignment]
    event_bus: EventBus = started_agent["event_bus"]  # type: ignore[assignment]

    # 订阅 game.milestone 以断言事件被 emit
    received: List[GamePayload] = []

    async def on_milestone(event_name: str, payload: GamePayload, source: str) -> None:
        received.append(payload)

    event_bus.on(
        CoreEvents.GAME_MILESTONE,
        on_milestone,
        model_class=GamePayload,
    )

    # Step 1+2: 注入状态变化 → 触发一次闭环
    result = await agent.feed_state_change(
        new_screen_text="（场景：村口；遇到 NPC）\n1) 继续前进\n2) 回头看看",
        options=_make_options(),
    )

    # Step 3: 感知被调用
    assert look_provider.call_count == 1, f"expected 1 perception call, got {look_provider.call_count}"
    assert result["perception_called"] is True

    # Step 4: 推进被触发
    stats = agent.get_statistics()
    assert stats["advance_count"] == 1, f"expected 1 advance, got {stats['advance_count']}"
    assert result["advance_called"] is True
    assert result["decision"] == "opt_1"

    # Step 5: content_engine.send_input 被调用（FakeContentEngine 记录）
    assert len(content_engine.sent_inputs) == 1
    sent = content_engine.sent_inputs[0]
    assert sent.kind == "key"
    assert sent.key == "enter"

    # 感知后端也真被触发了
    assert len(perception_capture.calls) == 1

    # Step 6: game.milestone 事件被 emit（异步派发，可能需短暂等待）
    await asyncio.sleep(0.05)
    assert len(received) == 1
    assert received[0].event_type == "milestone"
    assert received[0].game == "text_adv"
    assert "opt_1" in received[0].message


async def test_perception_advance_loop_two_steps_increment_counters(
    started_agent: Dict[str, object],
) -> None:
    """连续两次 feed_state_change：每次都触发完整闭环，计数器累加。"""
    agent: TextAdvGameAgent = started_agent["agent"]  # type: ignore[assignment]
    look_provider: LookAtScreenProvider = started_agent["look_provider"]  # type: ignore[assignment]
    content_engine: FakeContentEngine = started_agent["content_engine"]  # type: ignore[assignment]

    # 第 1 步
    r1 = await agent.feed_state_change(
        new_screen_text="scene A: 1) opt_a 2) opt_b",
        options=[TextAdvOption(option_id="opt_a", label="A", advance_key="enter")],
    )
    assert r1["perception_called"] is True
    assert r1["advance_called"] is True

    # 第 2 步（文本不同 → apply_screen_text 返回 True，闭环再次触发）
    r2 = await agent.feed_state_change(
        new_screen_text="scene B: 1) opt_c 2) opt_d",
        options=[TextAdvOption(option_id="opt_c", label="C", advance_key="enter")],
    )
    assert r2["perception_called"] is True
    assert r2["advance_called"] is True

    stats = agent.get_statistics()
    assert stats["perception_count"] == 2
    assert stats["advance_count"] == 2
    assert look_provider.call_count == 2
    assert len(content_engine.sent_inputs) == 2


async def test_no_advance_when_screen_text_unchanged_and_no_options(
    started_agent: Dict[str, object],
) -> None:
    """屏幕文本未变 + 无新选项注入 → 不触发推进（去重）。"""
    agent: TextAdvGameAgent = started_agent["agent"]  # type: ignore[assignment]
    look_provider: LookAtScreenProvider = started_agent["look_provider"]  # type: ignore[assignment]
    content_engine: FakeContentEngine = started_agent["content_engine"]  # type: ignore[assignment]

    # 先设置首屏 + 选项
    r1 = await agent.feed_state_change(
        new_screen_text="scene X",
        options=[TextAdvOption(option_id="opt_x", label="X", advance_key="enter")],
    )
    assert r1["advance_called"] is True
    init_perception = look_provider.call_count
    init_advance = len(content_engine.sent_inputs)

    # 再次喂相同文本 + 不传 options → apply_screen_text 返回 False，无推进
    r2 = await agent.feed_state_change(new_screen_text="scene X")
    assert r2["perception_called"] is True  # 感知仍触发（去重在 Agent 内部）
    assert r2["advance_called"] is False
    assert look_provider.call_count == init_perception + 1
    assert len(content_engine.sent_inputs) == init_advance


# =============================================================================
# choose_option / get_story 工具细节（TextAdvToolProvider）
# =============================================================================


async def test_choose_option_rejects_unknown_option(
    started_agent: Dict[str, object],
) -> None:
    """choose_option(option_id="不存在") → 失败 result，不抛。"""
    registry: ToolRegistry = started_agent["registry"]  # type: ignore[assignment]

    # 先喂一个有效场景让 Agent 有选项
    agent: TextAdvGameAgent = started_agent["agent"]  # type: ignore[assignment]
    await agent.feed_state_change(
        new_screen_text="scene Q",
        options=[TextAdvOption(option_id="valid", label="V", advance_key="enter")],
    )

    from src.modules.tools.models import ToolInvocation

    res = await registry.invoke(
        ToolInvocation(
            tool_name="choose_option",
            arguments={"option_id": "nope"},
            source="test",
        )
    )
    assert res.success is False
    assert "nope" in res.error_message


async def test_get_story_returns_state_snapshot(
    started_agent: Dict[str, object],
) -> None:
    """get_story → 返回 state.to_dict() 快照。"""
    registry: ToolRegistry = started_agent["registry"]  # type: ignore[assignment]
    agent: TextAdvGameAgent = started_agent["agent"]  # type: ignore[assignment]
    expected_scene_text = "（场景：村口；遇到 NPC）\n1) 继续前进\n2) 回头看看"
    await agent.feed_state_change(
        new_screen_text=expected_scene_text,
        options=[TextAdvOption(option_id="snap", label="S", advance_key="enter")],
    )

    from src.modules.tools.models import ToolInvocation

    res = await registry.invoke(
        ToolInvocation(tool_name="get_story", arguments={}, source="test")
    )
    assert res.success is True
    snap = res.structured_content
    assert snap is not None
    assert snap["scene_text"] == expected_scene_text
    options = snap["options"]
    assert any(o["option_id"] == "snap" for o in options)


# =============================================================================
# 优雅降级（无 ScreenCapture 后端）
# =============================================================================


async def test_look_at_screen_graceful_when_no_backend(
    event_bus: EventBus,
) -> None:
    """无 ScreenCapture 后端 → look_at_screen 返回成功 + 空文本 + 警告（不抛）。"""
    from src.modules.tools.models import ToolInvocation

    registry = ToolRegistry()
    provider = LookAtScreenProvider(screen_capture=None, text_reader=None)
    registry.register_provider(provider)

    res = await registry.invoke(
        ToolInvocation(tool_name="look_at_screen", arguments={}, source="test")
    )
    assert res.success is True
    assert res.content  # 空提示文本
    assert any("ScreenCapture 未注入" in b.text for b in res.blocks)


async def test_look_at_screen_with_fake_backend_returns_image_block() -> None:
    """注入 FakeScreenCapture → look_at_screen 返回 image block + text block。"""
    from src.modules.tools.models import ToolInvocation

    registry = ToolRegistry()
    capture = FakeScreenCapture()
    capture.queue_png(b"\x89PNG_FAKE", width=800, height=600)
    reader = FakeTextReader()
    reader.queue_text("游戏文本片段")
    provider = LookAtScreenProvider(screen_capture=capture, text_reader=reader)
    registry.register_provider(provider)

    res = await registry.invoke(
        ToolInvocation(tool_name="look_at_screen", arguments={}, source="test")
    )
    assert res.success is True
    assert res.content == "游戏文本片段"
    block_kinds = {b.kind for b in res.blocks}
    assert "text" in block_kinds
    assert "image" in block_kinds


# =============================================================================
# StubContentEngine 默认行为
# =============================================================================


async def test_stub_content_engine_round_trip() -> None:
    """StubContentEngine：start/send_input/stop/get_state 全部正常。"""
    from src.modules.tools.content_engine import (
        StubContentEngine,
        ContentInput,
    )

    engine = StubContentEngine(engine_kind="stub")
    await engine.start()
    status = await engine.status()
    assert status.running is True
    assert status.engine_kind == "stub"

    res = await engine.send_input(ContentInput(kind="key", key="enter"))
    assert res.accepted is True
    assert "stub:key" in res.echoed
    assert len(engine.sent_inputs) == 1

    await engine.stop()
    status2 = await engine.status()
    assert status2.running is False


async def test_content_engine_rejects_when_not_started() -> None:
    """引擎未启动时 send_input → 拒绝（accepted=False）。"""
    from src.modules.tools.content_engine import (
        StubContentEngine,
        ContentInput,
    )

    engine = StubContentEngine()
    res = await engine.send_input(ContentInput(kind="key", key="enter"))
    assert res.accepted is False
    assert "未启动" in res.error_message


# =============================================================================
# AgentManager 集成（验证与框架的零摩擦集成）
# =============================================================================


async def test_agent_manager_lifecycle_for_text_adv() -> None:
    """AgentManager 集成：register → start_all → 全部 RUNNING → stop_all。"""
    manager = AgentManager()
    agent = build_text_adv_agent(
        config=TextAdvGameConfig(),
        agent_manager=manager,
    )
    await manager.start_all()
    assert "game" in manager.list_running()
    await manager.stop_all()
    assert agent.state == AgentState.STOPPED


async def test_game_tools_audited_when_registered_via_registry() -> None:
    """AgentManager.audit_tools：Agent 自身把工具注册到 registry 后，audit 不报告缺失。

    反向验证：换空 registry → audit 应报告 game 工具。
    """
    registry = ToolRegistry()
    manager = AgentManager(tool_registry=registry)
    agent = build_text_adv_agent(
        config=TextAdvGameConfig(),
        agent_manager=manager,
        content_engine=FakeContentEngine(),
        tool_registry=registry,
    )

    await agent.start()
    assert registry.has("choose_option")
    assert registry.has("get_story")

    assert manager.audit_tools(registry) == []

    empty_registry = ToolRegistry()
    assert sorted(manager.audit_tools(empty_registry)) == ["choose_option", "get_story"]

    await agent.stop()


# =============================================================================
# 异常路径（不抛异常，遵循"工具失败 → failure result"语义）
# =============================================================================


async def test_choose_option_with_missing_option_id_returns_failure(
    started_agent: Dict[str, object],
) -> None:
    """choose_option 缺 option_id → 失败 result，不抛。"""
    registry: ToolRegistry = started_agent["registry"]  # type: ignore[assignment]

    from src.modules.tools.models import ToolInvocation

    res = await registry.invoke(
        ToolInvocation(tool_name="choose_option", arguments={}, source="test")
    )
    assert res.success is False
    assert "option_id" in res.error_message


async def test_perception_failure_emits_game_error_event(
    started_agent: Dict[str, object],
) -> None:
    """感知失败（FakeScreenCapture 抛异常）→ emit game.error。"""
    agent: TextAdvGameAgent = started_agent["agent"]  # type: ignore[assignment]
    event_bus: EventBus = started_agent["event_bus"]  # type: ignore[assignment]
    registry: ToolRegistry = started_agent["registry"]  # type: ignore[assignment]

    # 替换 look_at_screen 工具的 capture 为抛异常的 Fake
    class BoomCapture:
        def capture(self, region=None):
            raise RuntimeError("screen unavailable")

    # 直接通过 ToolRegistry 把 look_at_screen 替换为用 BoomCapture 的 provider
    # 这里我们改用 monkeypatch 风格：构造新 provider 覆盖旧 spec
    from src.modules.tools.perception import LookAtScreenProvider

    boom_provider = LookAtScreenProvider(screen_capture=BoomCapture())
    # 由于 register 去重，需要先 clear registry 的 look_at_screen
    registry.clear()
    registry.register_provider(boom_provider)
    # ContentEngine 也要重新注册
    content_engine = started_agent["content_engine"]  # type: ignore[assignment]
    from src.modules.tools.content_engine import ContentEngineProvider

    registry.register_provider(ContentEngineProvider(engine=content_engine))
    # Game provider 需要重新构造并注册
    from src.agents.game.text_adv import TextAdvToolProvider

    registry.register_provider(
        TextAdvToolProvider(state=agent._game_state, engine=content_engine)  # noqa: SLF001
    )

    received_errors: List[GamePayload] = []

    async def on_error(event_name: str, payload: GamePayload, source: str) -> None:
        received_errors.append(payload)

    event_bus.on(CoreEvents.GAME_ERROR, on_error, model_class=GamePayload)

    res = await agent.feed_state_change(
        new_screen_text="scene error",
        options=[TextAdvOption(option_id="e", label="E", advance_key="enter")],
    )
    # 感知被调用（look_at_screen 触发了）；但 provider 返回 failure result
    assert res["perception_called"] is True
    # 推进未被触发（感知失败 → 提前 return）
    assert res["advance_called"] is False
    await asyncio.sleep(0.05)
    assert len(received_errors) >= 1
    assert "感知失败" in received_errors[0].message