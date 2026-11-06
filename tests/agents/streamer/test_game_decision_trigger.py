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
from src.modules.events.payloads.game_chat import GameChatPayload, GameSelfChattedPayload
from src.modules.events.payloads.game_place import GamePlacePayload
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


async def test_place_marks_never_reach_the_timeline_or_wake_her(live_agent) -> None:
    """记住了地点、走近了地点只落库给整理用：主播不订阅，不进时间线、不促发决策，免得她念叨。"""
    agent, bus = live_agent
    for kind in ("remembered", "near", "left"):
        payload = GamePlacePayload(game="minecraft", kind=kind, place="家", distance=3 if kind == "near" else None)
        await bus.emit(CoreEvents.GAME_PLACE_NOTED, payload, source="TestGameAgent")
    await bus.wait_until_drained()

    assert agent._proactive_signals.game_pending is False
    materials = await agent._scope.open_window()
    assert materials.timeline_notes == [] and materials.game_batch.events == ()


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


async def test_game_chat_note_follows_the_speaker(live_agent) -> None:
    """采集器给的归属说明跟在发言人后面：模组借我名义发的坐标不再像是又一个玩家在说话（审计 P2-6）。"""
    agent, bus = live_agent
    machine = GameChatPayload(
        game="minecraft",
        kind="system",
        content='[name:"winefox_momo", x:-356, y:63, z:36, dim:minecraft:overworld]',
        note="模组借你的名义发的消息，不是谁说的话",
        timestamp_ms=1_000,
    )
    await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, machine, source="maicraft_chat")
    await _wait_until(lambda: len(agent._game_ledger._pending) == 1)
    assert agent._proactive_signals.game_pending is False, "机器消息不是有人在跟我说话，不唤醒决策"
    line = agent._scope.drain_window().events[0]
    assert '系统消息（模组借你的名义发的消息，不是谁说的话）：[name:"winefox_momo"' in line


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


class _NameBook:
    """线索推送替身：只记下主播交来的已知玩家名。"""

    def __init__(self) -> None:
        self.names: list[str] = []

    def note_name(self, name: str) -> None:
        self.names.append(name)


async def test_game_chat_speakers_become_known_names_for_masking() -> None:
    """游戏里说过话的玩家记成已知名字：游戏情境查询发给嵌入服务前把它换成"玩家"；系统消息不算。"""
    bus = EventBus()
    book = _NameBook()
    agent = make_streamer_agent({}, event_bus=bus, memory_cues=book)
    agent._subscribe_events()
    agent._live_active = True
    try:
        system = GameChatPayload(game="minecraft", kind="system", content="Alex 请求传送到你这里", timestamp_ms=1_000)
        player = GameChatPayload(game="minecraft", kind="player", sender="Steve", content="在吗", timestamp_ms=1_000)
        await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, system, source="maicraft_chat")
        await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, player, source="maicraft_chat")
        await bus.wait_until_drained()

        assert book.names == ["Steve"]
    finally:
        await bus.cleanup()


async def test_game_chat_remembers_who_spoke_for_the_window(live_agent) -> None:
    """玩家聊天入账时记下是谁说的：核对过的写 UUID，没核对过的写 "?"，系统消息不算；本窗出账时一并列出。"""
    agent, bus = live_agent
    for payload in (
        GameChatPayload(
            game="minecraft", kind="player", sender="Alex", sender_id="uuid-a", sender_online=True, content="来切磋"
        ),
        GameChatPayload(game="minecraft", kind="player", sender="Server", content="[Server] 重启"),
        GameChatPayload(game="minecraft", kind="system", content="Steve 加入了游戏"),
    ):
        await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, payload, source="TestCollector")
        await bus.wait_until_drained()

    await bus.emit(
        CoreEvents.GAME_BODY_PLAYER_BODY_LANGUAGE,
        _body("player_body_language", "玩家 Steve 看着我蹲起 2 下", player_uuid="uuid-s"),
        source="TestCollector",
    )
    # 她跟着的玩家只是透传：他没来找她，不算找过她、不推卡
    await bus.emit(
        CoreEvents.GAME_BODY_FOLLOW_STATUS,
        _body("follow_status", "跟着人走时：他回到我身边了", player_uuid="uuid-f", player_name="Fay"),
        source="TestCollector",
    )
    await bus.wait_until_drained()

    materials = await agent._scope.open_window()
    assert materials.game_batch.chat_speakers == (("minecraft", "uuid-a"), ("minecraft", "?"))
    # 冲她做动作的也算找过她：玩家卡据此推
    assert materials.game_batch.addressed_players == (("minecraft", "uuid-a"), ("minecraft", "uuid-s"))


async def test_her_own_public_chat_enters_timeline_without_waking_her(live_agent) -> None:
    """游戏那一面自己在公屏开口（第 58 场问人要蒲公英）：进时间线让她知道自己问过什么；不促发决策、不算别人的聊天。"""
    agent, bus = live_agent
    said = GameSelfChattedPayload(game="minecraft", message="你那儿还有蒲公英吗", timestamp_ms=1_000)
    await bus.emit(CoreEvents.GAME_SELF_CHATTED, said, source="TestGameAgent")
    await bus.wait_until_drained()

    assert agent._proactive_signals.game_pending is False
    materials = await agent._scope.open_window()
    batch = materials.game_batch
    assert [turn.content.split("] ", 1)[1] for turn in materials.timeline_notes] == ["你那儿还有蒲公英吗"]
    assert "我在 minecraft 公屏说]" in materials.timeline_notes[0].content
    # 她自己的话不交表达侧当"游戏里刚有人说的话"，也不算谁在公屏开口
    assert batch.fresh_chat == () and batch.chat_speakers == () and batch.player_notes == ()


async def test_player_chat_and_actions_reach_the_batch_with_names(live_agent) -> None:
    """玩家在公屏说的话、冲她做的动作带着名字列进本窗的玩家条目；系统消息与没人做的遭遇不列。"""
    agent, bus = live_agent
    gift = _body(
        "player_body_language",
        "玩家 玩家甲 朝我扔了 1 个铁锭，落在我脚边。扔过来的多半是送我的",
        player_uuid="uuid-甲",
        player_name="玩家甲",
    )
    chat = GameChatPayload(game="minecraft", kind="player", sender="玩家甲", content="我勒个", timestamp_ms=1_000)
    system = GameChatPayload(game="minecraft", kind="system", content="玩家甲被杀死了", timestamp_ms=1_000)
    await bus.emit(CoreEvents.GAME_BODY_PLAYER_BODY_LANGUAGE, gift, source="t")
    await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, chat, source="maicraft_chat")
    await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, system, source="maicraft_chat")
    await bus.wait_until_drained()

    notes = agent._scope.drain_window().player_notes
    assert [(note.who, note.said) for note in notes] == [("玩家甲", False), ("玩家甲", True)]
    assert notes[0].line.endswith("玩家 玩家甲 朝我扔了 1 个铁锭，落在我脚边")
    assert notes[1].line.endswith("玩家 玩家甲：我勒个")
