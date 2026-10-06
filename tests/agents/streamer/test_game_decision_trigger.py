"""主播侧游戏叙事上下文标记与待定夺触发测试。

覆盖：
- L1：``_on_game_event`` 产出的叙事行带事件类型标记，message 本体不变
- L2 端到端：直播态 ``game.report`` → 空缓冲 tick 消费 pending → 促发一轮
  proactive 决策（trigger_reason = "proactive:game"）；非直播态 pending 不被
  消费，开播后首个 tick 消费
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.body import BodyEventPayload
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.game_chat import GameChatPayload
from src.modules.llm.client import LLMResponse
from src.modules.llm.payload import Response


def _make_agent_config(**overrides: Any) -> StreamerConfig:
    """构造测试用 StreamerConfig（主动发言开启，便于 L2 链路验证）。"""
    defaults: Dict[str, Any] = {
        "proactive": {"enabled": True},
        "word_filter": {"enabled": False},
        "batch": {
            "batch_window_ms": 100,
            "tick_interval_ms": 50,
        },
    }
    defaults.update(overrides)
    return StreamerConfig.from_dict(defaults)


def _build_streamer_agent(event_bus: Optional[EventBus] = None) -> StreamerAgent:
    """构造最小化 StreamerAgent：mock LLM/Prompt；EventBus 可注入真实实例。"""
    llm = MagicMock()
    llm.call_tools = AsyncMock(return_value=LLMResponse(success=False, error="not used"))
    llm.generate = AsyncMock(return_value=Response(success=False, error="not used"))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")

    return StreamerAgent(
        config=_make_agent_config(),
        llm_manager=llm,
        prompt_manager=prompt,
        event_bus=event_bus,
    )


def _make_payload(event_type: str, message: str) -> GamePayload:
    return GamePayload(game="minecraft", event_type=event_type, message=message)  # type: ignore[arg-type]


async def _wait_until(cond: Any, timeout_s: float = 2.0) -> None:
    """轮询等待异步条件成立（EventBus emit 为并发扇出、不等订阅者完成）。"""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待条件超时")


# ---------------------------------------------------------------------------
# L1：叙事行带事件类型标记
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrative_line_contains_event_type_and_message_unchanged():
    """叙事行形如 [到达距今] [game·event_type] message；message 本体原样保留。"""
    agent = _build_streamer_agent()

    await agent._on_game_event(CoreEvents.GAME_REPORT, _make_payload("report", "迷宫尽头有两个门：左或右？"), "Test")
    await agent._on_game_event(CoreEvents.GAME_MILESTONE, _make_payload("milestone", "挖到钻石了！"), "Test")

    lines = agent._game_narrative_text().splitlines()
    assert lines[0] == "[新·刚刚] [minecraft·report] 迷宫尽头有两个门：左或右？"
    assert lines[1] == "[新·刚刚] [minecraft·milestone] 挖到钻石了！"
    # 交给过一次决策窗后不再标"新"，内容仍完整保留
    assert agent._game_narrative_text().splitlines()[0] == "[刚刚] [minecraft·report] 迷宫尽头有两个门：左或右？"


@pytest.mark.asyncio
async def test_only_report_sets_decision_pending():
    """仅 report 类型置位待定夺信号；milestone 等推进类不置位。"""
    agent = _build_streamer_agent()

    await agent._on_game_event(CoreEvents.GAME_MILESTONE, _make_payload("milestone", "挖到钻石了！"), "Test")
    assert agent._game_decision_pending is False

    await agent._on_game_event(CoreEvents.GAME_REPORT, _make_payload("report", "交付总结：得到 3 个钻石"), "Test")
    assert agent._game_decision_pending is True


# ---------------------------------------------------------------------------
# L2 端到端：game.report 促发一轮决策轮 / 离线 pending 保留
# ---------------------------------------------------------------------------


def _spy_rounds(agent: StreamerAgent) -> AsyncMock:
    """把 Agent 内部决策执行器替换为 spy mock（假 Planner，录制调用参数）。"""
    spy = AsyncMock()
    spy.execute = AsyncMock(return_value={"success": True})
    agent._rounds = spy
    return spy


@pytest.mark.asyncio
async def test_game_report_triggers_proactive_round_when_live():
    """直播态：emit game.report → 空缓冲 tick 消费 pending → 促发一轮决策。"""
    bus = EventBus()
    agent = _build_streamer_agent(event_bus=bus)
    agent._subscribe_events()
    rounds_spy = _spy_rounds(agent)
    agent._live_active = True

    await bus.emit(
        CoreEvents.GAME_REPORT,
        _make_payload("report", "迷宫尽头有两个门：左或右？"),
        source="TestGameAgent",
    )
    await _wait_until(lambda: agent._game_decision_pending)
    await agent._maybe_flush()

    rounds_spy.execute.assert_awaited_once()
    call = rounds_spy.execute.call_args
    assert call.args[0] == []  # 主动发言无弹幕批次
    kwargs = call.kwargs
    assert kwargs["proactive"] is True
    assert kwargs["trigger_reason"] == "proactive:game"
    # L1：促发后的上下文数据源里含带类型标记的叙事行
    assert "[minecraft·report] 迷宫尽头有两个门：左或右？" in agent._game_narrative_text()
    # pending 一次性消费
    assert agent._game_decision_pending is False

    await bus.cleanup()


@pytest.mark.asyncio
async def test_body_event_feeds_the_body_narrative_line() -> None:
    """game.body.* → 独立"身体近况"缓冲（与游戏叙事分两条线）。"""
    bus = EventBus()
    agent = _build_streamer_agent(event_bus=bus)
    agent._subscribe_events()
    agent._live_active = True

    await bus.emit(
        CoreEvents.GAME_BODY_ATTACKED,
        BodyEventPayload(
            game="minecraft",
            kind="attacked",
            summary="正在被僵尸攻击（已命中 2 次）",
            source_event_type="agent.damaged",
            attacker="minecraft:zombie",
            hits=2,
        ),
        source="maicraft_attention",
    )
    await bus.emit(
        CoreEvents.GAME_BODY_ATTACK_ENDED,
        BodyEventPayload(
            game="minecraft",
            kind="attack_ended",
            summary="摆脱了僵尸的攻击（共命中 2 次）",
            source_event_type="agent.damaged",
            resolved=True,
        ),
        source="maicraft_attention",
    )
    await _wait_until(lambda: len(agent._body_narrative) == 2)

    text = agent._body_narrative_text()
    assert "[minecraft·attacked] 正在被僵尸攻击（已命中 2 次）" in text
    assert "[minecraft·attack_ended] 摆脱了僵尸的攻击（共命中 2 次）（已结束）" in text
    # 两条线互不混排：身体近况不写进游戏叙事缓冲，反之亦然
    assert agent._game_narrative_text() == ""
    # 身体事件不触发决策轮（它只是素材；是否开口由决策窗自己判断）
    assert agent._game_decision_pending is False

    await bus.cleanup()


@pytest.mark.asyncio
async def test_game_chat_feeds_its_own_block_and_player_wakes_streamer() -> None:
    """game.chat.* → 独立"游戏里的聊天"缓冲；玩家搭话促发决策，系统消息只作参考。"""
    bus = EventBus()
    agent = _build_streamer_agent(event_bus=bus)
    agent._subscribe_events()
    agent._live_active = True

    await bus.emit(
        CoreEvents.GAME_CHAT_RECEIVED,
        GameChatPayload(game="minecraft", kind="system", content="Alex 请求传送到你这里"),
        source="maicraft_chat",
    )
    await _wait_until(lambda: len(agent._game_chat) == 1)
    # 系统消息是信息，不单独唤醒主播
    assert agent._game_decision_pending is False

    await bus.emit(
        CoreEvents.GAME_CHAT_RECEIVED,
        GameChatPayload(game="minecraft", kind="player", sender="Steve", content="麦麦你在干嘛呀", suppressed=2),
        source="maicraft_chat",
    )
    await _wait_until(lambda: len(agent._game_chat) == 2)
    # 玩家是在跟游戏里的我说话：像弹幕一样值得及时接住
    assert agent._game_decision_pending is True

    text = agent._game_chat_text()
    assert "[minecraft] 系统消息：Alex 请求传送到你这里" in text
    assert "[minecraft] 玩家 Steve：麦麦你在干嘛呀（之前还有 2 条重复或刷屏的没转过来）" in text
    assert "新·" in text and "新·" not in agent._game_chat_text()
    # 与游戏叙事、身体近况互不混排
    assert agent._game_narrative_text() == "" and agent._body_narrative_text() == ""

    await bus.cleanup()


@pytest.mark.asyncio
async def test_body_narrative_keeps_all_events() -> None:
    """还没交给决策窗的连续身体事件在条数上限内全部保留，主播能看到最初遇险与最新反应。"""
    bus = EventBus()
    agent = _build_streamer_agent(event_bus=bus)
    agent._subscribe_events()

    for index in range(8):
        await bus.emit(
            CoreEvents.GAME_BODY_REFLEX_STARTED,
            BodyEventPayload(game="minecraft", kind="reflex_started", summary=f"紧急反应接管（{index}）"),
            source="maicraft_attention",
        )
    await _wait_until(lambda: len(agent._body_narrative) == 8)
    assert len(agent._body_narrative) == 8
    assert all(f"（{i}）" in agent._body_narrative_text() for i in range(8))

    await bus.cleanup()


@pytest.mark.asyncio
async def test_game_report_pending_preserved_when_offline():
    """非直播态：pending 不被消费；开播后首个 tick 消费并促发决策。"""
    bus = EventBus()
    agent = _build_streamer_agent(event_bus=bus)
    agent._subscribe_events()
    rounds_spy = _spy_rounds(agent)
    agent._live_active = False

    await bus.emit(
        CoreEvents.GAME_REPORT, _make_payload("report", "血量过低，先撤退还是硬拼？"), source="TestGameAgent"
    )
    await _wait_until(lambda: agent._game_decision_pending)

    # 离线 tick：直接 return，不消费 pending、不触发决策
    await agent._maybe_flush()
    rounds_spy.execute.assert_not_awaited()
    assert agent._game_decision_pending is True

    # 开播后首个 tick：消费 pending 并促发
    agent._live_active = True
    await agent._maybe_flush()
    rounds_spy.execute.assert_awaited_once()
    assert rounds_spy.execute.call_args.kwargs["trigger_reason"] == "proactive:game"
    assert agent._game_decision_pending is False

    await bus.cleanup()
