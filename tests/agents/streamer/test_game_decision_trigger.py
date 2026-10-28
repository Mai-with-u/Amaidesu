"""主播侧游戏上下文：游戏事件在决策窗边界批量追加进时间线，哪些事件促发一轮主动决策"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.body import BodyEventPayload
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.game_chat import GameChatPayload
from src.agents.streamer.trigger_cause import TriggerKind
from tests.agents.streamer.helpers import make_streamer_agent


async def _wait_until(cond: Any, timeout_s: float = 2.0) -> None:
    """EventBus emit 为并发扇出、不等订阅者完成，轮询等条件成立。"""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("等待条件超时")
        await asyncio.sleep(0.01)


def _report(event_type: str, message: str) -> GamePayload:
    return GamePayload(game="minecraft", event_type=event_type, message=message, timestamp_ms=1_000)


def _body(kind: str, summary: str, **kwargs: Any) -> BodyEventPayload:
    return BodyEventPayload(game="minecraft", kind=kind, summary=summary, timestamp_ms=1_000, **kwargs)


@pytest.fixture
async def live_agent():
    """订阅了事件、处于直播中的主播（主动发言开启）。"""
    bus = EventBus()
    agent = make_streamer_agent({"proactive": {"enabled": True}}, event_bus=bus)
    agent._subscribe_events()
    agent._live_active = True
    yield agent, bus
    await bus.cleanup()


async def test_game_lines_carry_event_type_and_only_report_sets_pending() -> None:
    """game.* 事件先入待入账队列；读历史（决策窗边界）整批追加进时间线，行带类型标签。"""
    agent = make_streamer_agent()
    await agent._on_game_event(CoreEvents.GAME_MILESTONE, _report("milestone", "挖到钻石了！"), "Test")
    await agent._on_game_event(CoreEvents.GAME_REPORT, _report("report", "迷宫尽头有两个门：左或右？"), "Test")
    assert agent._proactive_signals.game_pending is True
    # 决策窗边界之前不进时间线：纯读历史里看不到事件行
    assert "游戏·minecraft" not in "\n".join(
        getattr(turn, "content", "") for turn in (await agent._read_history() or [])
    )

    materials = await agent._scope.open_window()
    batch = materials.game_batch
    assert [turn.message_type for turn in materials.timeline_notes] == ["game", "game"]
    assert "游戏·minecraft·milestone" in materials.timeline_notes[0].content
    assert "游戏·minecraft·report" in materials.timeline_notes[1].content
    assert len(batch.events) == 2 and len(batch.fresh_chat) == 0

    # 已入账的条目不再出现在下一窗批次里（追加一次，不逐窗重放）
    again = await agent._scope.open_window()
    assert again.game_batch.events == () and len(again.timeline_notes) == 2


async def test_game_report_pending_is_consumed_only_while_live(live_agent) -> None:
    agent, bus = live_agent
    rounds = AsyncMock()
    rounds.execute = AsyncMock(return_value={"success": True})
    agent._rounds = rounds
    agent._live_active = False

    await bus.emit(CoreEvents.GAME_REPORT, _report("report", "血量过低，先撤退还是硬拼？"), source="TestGameAgent")
    await _wait_until(lambda: agent._proactive_signals.game_pending)
    # 离线 tick 不消费
    await agent._maybe_flush()
    rounds.execute.assert_not_awaited()
    assert agent._proactive_signals.game_pending is True

    # 开播后首个空缓冲 tick 消费并促发一轮主动决策
    agent._live_active = True
    await agent._maybe_flush()
    rounds.execute.assert_awaited_once()
    call = rounds.execute.call_args
    assert call.args[0] == []
    assert (
        call.kwargs["proactive"],
        call.kwargs["trigger_cause"].kind,
    ) == (True, TriggerKind.GAME)
    assert agent._proactive_signals.game_pending is False


async def test_body_events_append_with_own_label_and_only_death_wakes(live_agent) -> None:
    agent, bus = live_agent
    await bus.emit(
        CoreEvents.GAME_BODY_ATTACKED,
        _body("attacked", "正在被僵尸攻击（已命中 2 次）", attacker="minecraft:zombie", hits=2),
        source="maicraft_attention",
    )
    await bus.emit(
        CoreEvents.GAME_BODY_ATTACK_ENDED,
        _body("attack_ended", "摆脱了僵尸的攻击（共命中 2 次）", resolved=True),
        source="maicraft_attention",
    )
    await _wait_until(lambda: len(agent._game_ledger._pending) == 2)

    batch = agent._scope.drain_window()
    text = "\n".join(batch.events)
    assert "遭遇·minecraft·attacked" in text and "正在被僵尸攻击（已命中 2 次）" in text
    assert "遭遇·minecraft·attack_ended" in text and "摆脱了僵尸的攻击（共命中 2 次）（已结束）" in text
    # 身体事件只是素材，不促发决策
    assert agent._proactive_signals.game_pending is False

    await bus.emit(CoreEvents.GAME_BODY_DIED, _body("died", "角色倒下了：被苦力怕炸死"), source="maicraft_attention")
    await _wait_until(lambda: agent._proactive_signals.game_pending is True)
    assert "遭遇·minecraft·died" in agent._scope.drain_window().events[0]


async def test_identical_lines_within_fold_window_merge_with_count(live_agent) -> None:
    """同摘要 90 秒窗内连发的相同条目入账时合并为一条带计数说明的行。"""
    agent, bus = live_agent
    for _ in range(3):
        await bus.emit(CoreEvents.GAME_BODY_REFLEX_STARTED, _body("reflex_started", "紧急反应接管"), source="t")
    await _wait_until(lambda: len(agent._game_ledger._pending) == 1)

    materials = await agent._scope.open_window()
    assert len(materials.game_batch.events) == 1 and "另 2 次相同" in materials.game_batch.events[0]
    assert len(materials.timeline_notes) == 1


async def test_game_chat_appends_and_player_wakes_and_fresh_lines_hand_to_replyer(live_agent) -> None:
    agent, bus = live_agent
    system = GameChatPayload(game="minecraft", kind="system", content="Alex 请求传送到你这里", timestamp_ms=1_000)
    await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, system, source="maicraft_chat")
    await _wait_until(lambda: len(agent._game_ledger._pending) == 1)
    # 系统消息只作参考，不单独唤醒
    assert agent._proactive_signals.game_pending is False

    player = GameChatPayload(
        game="minecraft", kind="player", sender="Steve", content="麦麦你在干嘛呀", suppressed=2, timestamp_ms=1_000
    )
    await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, player, source="maicraft_chat")
    await _wait_until(lambda: len(agent._game_ledger._pending) == 2)
    # 玩家是在跟游戏里的我说话：像弹幕一样值得及时接住
    assert agent._proactive_signals.game_pending is True

    batch = agent._scope.drain_window()
    assert "系统消息：Alex 请求传送到你这里" in batch.events[0]
    assert "玩家 Steve：麦麦你在干嘛呀（之前还有 2 条重复或刷屏的没转过来）" in batch.events[1]
    # 聊天原话随本窗说话交给表达侧
    assert batch.fresh_chat == (batch.events[0], batch.events[1])


async def test_private_game_chat_reaches_planner_batch_but_never_timeline(live_agent) -> None:
    """私聊：随本窗批次交给决策层，但不进时间线（表达侧与历史都看不到）。"""
    agent, bus = live_agent
    secret = GameChatPayload(
        game="minecraft", kind="player", sender="Steve", content="悄悄跟你说个事", private=True, timestamp_ms=1_000
    )
    public = GameChatPayload(game="minecraft", kind="player", sender="Alex", content="大家好", timestamp_ms=1_000)
    await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, secret, source="maicraft_chat")
    await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, public, source="maicraft_chat")
    await _wait_until(lambda: agent._proactive_signals.game_pending is True)

    materials = await agent._scope.open_window()
    batch = materials.game_batch
    assert batch.private_chat == ("[minecraft] 玩家 Steve（私聊，只发给我）：悄悄跟你说个事",)
    assert "悄悄跟你说个事" not in "\n".join(turn.content for turn in materials.timeline_notes)
    assert batch.fresh_chat == (batch.events[0],)  # 私聊不算可交给表达侧的原话
    # 私聊只带一窗：下一窗不再携带
    assert agent._scope.drain_window().private_chat == ()
