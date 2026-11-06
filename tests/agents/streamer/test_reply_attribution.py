"""谁说的话、谁做的事交到表达侧：玩家找她的条目从账本出账，Planner 留最近几分钟的随 reply 交给 Replyer。

复现的是第 58 场两处错：玩家扔来铁锭在上一窗入账、下一窗交接稿写成"凭空到手"，她说成天上掉的；
玩家在公屏说"我勒个"，她冲上一句刚点过名的女仆说。这里核对结构化的人和回应对象一路交到了表达侧，
模型会不会照着说由 scripts/replyer_eval/attribution.py 真调模型评测。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.game_ledger import GameLedger, keep_recent_notes, sole_speaker_name
from src.agents.streamer.plan import DecisionPlan
from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv
from src.agents.streamer.replyer import Replyer
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.timeline import GameEventBatch, PlayerNote, TimelineTurn
from src.agents.streamer.tools.reply_tool import ReplyToolProvider
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.llm.payload import Response, ToolCall
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.utils.time_utils import now_ms
from tests.modules.llm.fakes import FakeLLMManager

_HINT = "玩家之间蹲起多半是打招呼、扔过来的多半是送我的：开心地俏皮接一句，不提血量"
_GIFT = f"玩家 玩家甲 朝我扔了 1 个铁锭，落在我脚边，朝我扔了 1 个铁锭，落在我脚边。{_HINT}"


def _ts(hour: int, minute: int) -> int:
    return int(datetime(2026, 1, 1, hour, minute).timestamp() * 1000)


# 账本：玩家找她的条目


def test_ledger_lists_player_chat_and_actions_with_names() -> None:
    """玩家的公屏话与冲她做的动作列进本批的玩家条目；动作只留事实，不带后面怎么接的口径；系统消息不列。"""
    ledger = GameLedger()
    ledger.record_encounter(
        "minecraft", "player_body_language", _GIFT, True, _ts(23, 19), player="uuid-甲", who="玩家甲"
    )
    ledger.record_chat("minecraft", "系统消息：玩家甲被杀死了", _ts(23, 20))
    ledger.record_chat("minecraft", "玩家 玩家甲：我勒个", _ts(23, 21), speaker="uuid-甲", who="玩家甲")
    ledger.record_encounter("minecraft", "attacked", "被僵尸打了一下", True, _ts(23, 22))

    notes = ledger.drain().player_notes

    assert [note.line for note in notes] == [
        "[23:19 minecraft 动作] 玩家 玩家甲 朝我扔了 1 个铁锭，落在我脚边，朝我扔了 1 个铁锭，落在我脚边",
        "[23:21 minecraft 公屏] 玩家 玩家甲：我勒个",
    ]
    assert [(note.who, note.said) for note in notes] == [("玩家甲", False), ("玩家甲", True)]


def test_repeated_player_chat_folds_into_one_note_with_count() -> None:
    """同一句连发折叠成一条时，玩家条目也带计数，不占好几个位置。"""
    ledger = GameLedger()
    for _ in range(3):
        ledger.record_chat("minecraft", "玩家 玩家乙：在吗", _ts(20, 1), speaker="uuid-乙", who="玩家乙")

    (note,) = ledger.drain().player_notes
    assert note.line == "[20:01 minecraft 公屏] 玩家 玩家乙：在吗（另 2 次相同）"


def _note(who: str, said: bool, at_ms: int) -> PlayerNote:
    return PlayerNote(who=who, said=said, line=f"{who}-{at_ms}", timestamp_ms=at_ms)


def test_recent_notes_keep_last_ten_minutes_and_at_most_six() -> None:
    """上一窗留下的与本窗新到的合在一起：十分钟以前的去掉，最多留六条，旧的先出。"""
    now = _ts(21, 0)
    stale = _note("玩家甲", False, _ts(20, 49))
    kept = (stale, _note("玩家甲", False, _ts(20, 51)))
    fresh = tuple(_note("玩家乙", True, _ts(20, 59)) for _ in range(6))

    assert keep_recent_notes(kept, (), now) == kept[1:]
    assert keep_recent_notes(kept, fresh, now) == fresh


@pytest.mark.parametrize(
    ("notes", "expected"),
    [
        ((_note("玩家甲", True, 1), _note("玩家甲", True, 2)), "玩家甲"),
        # 冲她做动作的不算开口：回应对象只看公屏说话的人
        ((_note("玩家甲", True, 1), _note("玩家乙", False, 2)), "玩家甲"),
        # 两位玩家都开口了：不猜在回谁
        ((_note("玩家甲", True, 1), _note("玩家乙", True, 2)), ""),
        ((_note("玩家甲", False, 1),), ""),
        ((), ""),
    ],
    ids=["one-speaker", "action-is-not-speaking", "two-speakers", "only-action", "none"],
)
def test_sole_speaker_name(notes: Tuple[PlayerNote, ...], expected: str) -> None:
    assert sole_speaker_name(notes) == expected


# Replyer：交接稿里的回应对象与玩家条目段


def _replyer() -> Tuple[Replyer, MagicMock]:
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="好"))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    return Replyer(config={}, llm_service=llm, prompt_service=prompt), prompt


def _turn_kwargs(prompt: MagicMock) -> Dict[str, Any]:
    return {call.args[0]: call.kwargs for call in prompt.render.call_args_list}["amaidesu_replyer"]


async def test_reply_to_player_replaces_the_no_target_line() -> None:
    """没在回弹幕、有回应对象时，target 一行换成是哪位玩家；玩家条目逐行给出、不截短。"""
    replyer, prompt = _replyer()
    gift = PlayerNote(who="玩家甲", said=False, line="[23:19 minecraft 动作] " + "玩家 玩家甲 朝我扔了铁锭" * 10)
    chat = PlayerNote(who="玩家甲", said=True, line="[00:08 minecraft 公屏] 玩家 玩家甲：我勒个")
    plan = DecisionPlan(should_reply=True, topic_summary="玩家甲在公屏发了一句「我勒个」")

    await replyer.generate(plan, [], game_people=[gift, chat], reply_to="玩家甲")

    turn = _turn_kwargs(prompt)
    assert turn["plan"].startswith("回应对象: 游戏里的玩家 玩家甲（他刚在游戏公屏跟你说话）\n")
    assert "target" not in turn["plan"]
    section = turn["game_people_section"]
    assert section.startswith("\n\n## 游戏里最近找你的人（谁说的话、谁做的事以这里为准）\n\n")
    assert f"{gift.line}\n{chat.line}\n" in section and "别说成来路不明" in section


async def test_danmaku_target_wins_over_reply_to_and_people_section_omitted() -> None:
    """要回应的弹幕查到了：照旧给弹幕原文；没有玩家条目时整段省略，平常的决策窗不多占输入。"""
    replyer, prompt = _replyer()
    batch = [
        RoomMessagePayload(
            message_type="danmaku", user=RoomMessageUser(id="u1", name="观众甲"), content="几点下播", message_id="m1"
        )
    ]
    plan = DecisionPlan(should_reply=True, target="[id:m1]", topic_summary="观众问下播时间")

    await replyer.generate(plan, batch, reply_to="玩家甲")

    turn = _turn_kwargs(prompt)
    assert turn["plan"].startswith("target 原文: 观众甲: 几点下播\n")
    assert "回应对象" not in turn["plan"]
    assert turn["game_people_section"] == ""


# Planner：玩家条目跨窗留着，随 reply 交到表达侧


class _CaptureReplyer:
    """表达层桩：记下每次交来的玩家条目与回应对象。"""

    def __init__(self) -> None:
        self.calls: List[Tuple[List[str], str]] = []

    async def generate(self, *, game_people: List[PlayerNote], reply_to: str, **_: Any) -> Dict[str, Any]:
        self.calls.append(([note.line for note in game_people], reply_to))
        return {"speech": "收到啦", "emotion": {"name": "happy", "intensity": 0.5}, "metadata": {}}


def _planner(replyer: _CaptureReplyer, responses: List[Response]) -> Planner:
    provider = ReplyToolProvider(replyer=replyer)  # type: ignore[arg-type]
    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])
    registry.invoke = AsyncMock(side_effect=provider.invoke)
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=responses)
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    return Planner(
        config=PlannerConfig(planner_max_steps=2),
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        env=PlannerEnv(reply_provider_factory=lambda: provider),
    )


def _reply(target: Optional[str] = None) -> Response:
    arguments: Dict[str, Any] = {"topic_summary": "接话", "is_novel": True}
    if target:
        arguments["target"] = target
    return Response(
        success=True, content="", tool_calls=[ToolCall(id="c1", name="streamer_reply", arguments=arguments)]
    )


async def test_gift_from_the_previous_window_reaches_the_replyer() -> None:
    """第 58 场原样：铁锭在上一窗入账、那一窗没接；下一窗冷场开口时，表达侧仍看得到是玩家甲扔来的。"""
    replyer = _CaptureReplyer()
    planner = _planner(replyer, [Response(success=True, content="先不说"), _reply()])
    gift = PlayerNote(
        who="玩家甲", said=False, line="[23:19 minecraft 动作] 玩家 玩家甲 朝我扔了铁锭", timestamp_ms=now_ms()
    )

    await planner.plan([], proactive=True, game_batch=GameEventBatch(player_notes=(gift,)))
    outcome = await planner.plan([], proactive=True, game_batch=GameEventBatch())

    assert outcome.replied is True
    # 下一窗没有人开口：没有回应对象，但扔铁锭的那条还在
    assert replyer.calls == [([gift.line], "")]


@pytest.mark.parametrize(
    ("target", "expected_reply_to"),
    [
        # 第 58 场原样：玩家在公屏感叹、Planner 没填目标，回应对象是他
        (None, "玩家甲"),
        # 回应的是本窗那条弹幕：对象是观众，不是玩家
        ("m1", ""),
    ],
    ids=["game-chat", "answers-danmaku"],
)
async def test_sole_game_speaker_becomes_reply_to_unless_answering_danmaku(
    target: Optional[str], expected_reply_to: str
) -> None:
    replyer = _CaptureReplyer()
    planner = _planner(replyer, [_reply(target)])
    chat = PlayerNote(who="玩家甲", said=True, line="[00:08 minecraft 公屏] 玩家 玩家甲：我勒个", timestamp_ms=now_ms())
    batch = [
        RoomMessagePayload(
            message_type="danmaku", user=RoomMessageUser(id="u1", name="观众甲"), content="主播好", message_id="m1"
        )
    ]

    await planner.plan(batch, game_batch=GameEventBatch(player_notes=(chat,)))

    assert replyer.calls == [([chat.line], expected_reply_to)]


# 第 58 场 00:08:56 那次表达侧请求原样：账本 → Planner → 真实提示词渲染，名字换成占位

# 交接稿照抄当晚 Planner 写的（只换了名字）：话题摘要里的"她"指主播自己
_S58_SUMMARY = (
    "玩家 玩家甲 在游戏公屏上发了一句「我勒个」，没说明是在惊叹什么；她此刻站在营地棚子南面自己砌的那道墙边，"
    "手里拿着一扇做好的橡木门，墙上那格门洞还没凿开。"
)
_S58_GUIDANCE = "让她被这句没头没尾的感叹勾住，带着好奇去追问对方到底看见什么了，语气俏皮、有点被吊胃口的不服气。"
# 公屏那句之后、Planner 开口之前，她刚冲女仆说过的一句：当晚表达侧把"我勒个"安到了女仆头上
_S58_MAID_LINE = "哎小白你可算挪开了，画面终于清爽了……不过你走远了我还怪不习惯的，回来点？"


class _NoteCapture(_CaptureReplyer):
    """表达层桩：留下 Planner 交来的玩家条目原件，再交给真实的表达侧渲染。"""

    def __init__(self) -> None:
        super().__init__()
        self.notes: List[PlayerNote] = []

    async def generate(self, *, game_people: List[PlayerNote], reply_to: str, **kwargs: Any) -> Dict[str, Any]:
        self.notes = list(game_people)
        return await super().generate(game_people=game_people, reply_to=reply_to, **kwargs)


@pytest.fixture
def prompts() -> Any:
    reset_prompt_manager()
    yield get_prompt_manager()
    reset_prompt_manager()


async def test_s58_wolege_request_names_the_player_and_the_reply_target(prompts: Any) -> None:
    """当晚：玩家在公屏说"我勒个"，交接稿写"她此刻站在墙边"，表达侧把"她"当成上一句刚点名的女仆，说成
    "小白你'我勒个'啥呀"。这一层不靠模型：改动后表达侧的输入里，这句话明确记在玩家甲名下、来自游戏公屏，
    回应对象写明是玩家甲；刚点过名的女仆既不是说话人，也不是回应对象。
    """
    now = now_ms()
    ledger = GameLedger()
    ledger.record_encounter(
        "minecraft",
        "player_body_language",
        "玩家 玩家甲 看着我空出手，朝我扔了 8 个卷心菜，落在我脚边，朝我扔了 17 个卷心菜种子，落在我脚边。" + _HINT,
        True,
        now - 120_000,
        player="uuid-甲",
        who="玩家甲",
    )
    ledger.record_encounter(
        "minecraft",
        "player_body_language",
        f"玩家 玩家甲 看着我原地跳了 2 下。{_HINT}",
        True,
        now - 30_000,
        player="uuid-甲",
        who="玩家甲",
    )
    ledger.record_chat("minecraft", "玩家 玩家甲：我勒个", now - 20_000, speaker="uuid-甲", who="玩家甲")
    batch = ledger.drain()

    # Planner 当晚没填 target（面向全体）：本窗只有玩家甲在公屏开口，回应对象就是他
    capture = _NoteCapture()
    planner = _planner(capture, [_reply()])
    await planner.plan([], game_batch=batch)
    assert capture.calls and capture.calls[-1][1] == "玩家甲"

    llm = FakeLLMManager(Response(success=True, content="好耶", request_id="r"))
    replyer = Replyer(config={"bot_name": "麦麦"}, llm_service=llm, prompt_service=prompts)
    history = [
        TimelineTurn(role="assistant", content="门都做了三扇了，墙上一格没凿开，木板倒攒了一大堆，我这毛病真是。"),
        *batch.timeline_turns,
        TimelineTurn(role="assistant", content=_S58_MAID_LINE),
    ]
    plan = DecisionPlan(should_reply=True, topic_summary=_S58_SUMMARY, reply_guidance=_S58_GUIDANCE)
    await replyer.generate(
        plan,
        [],
        history=history,
        game_chat=list(batch.fresh_chat),
        game_people=capture.notes,
        reply_to=capture.calls[-1][1],
    )
    text = llm.calls[-1]["input"][0]["content"]

    # 说话人：玩家甲，在游戏公屏；回应对象：游戏里的玩家甲，原来的"无特定目标"一行换掉
    people = text.split("## 游戏里最近找你的人（谁说的话、谁做的事以这里为准）", 1)[1]
    assert re.search(r"\[\d\d:\d\d minecraft 公屏\] 玩家 玩家甲：我勒个", people)
    assert re.search(r"\[\d\d:\d\d minecraft 动作\] 玩家 玩家甲 看着我空出手，朝我扔了 8 个卷心菜", people)
    assert "回应对象: 游戏里的玩家 玩家甲（他刚在游戏公屏跟你说话）" in text
    assert "无特定目标" not in text
    assert '交接稿里的"她"就是你' in text and "不安到女仆、宠物或观众头上" in text
    # 输入里每一处"我勒个"都记在玩家甲名下，没有一处和女仆连在一起
    wolege = [line for line in text.splitlines() if "我勒个" in line and "「我勒个」" not in line]
    assert wolege and all("玩家甲" in line and "小白" not in line for line in wolege)
    # 女仆只出现在她自己那句台词里，不在玩家条目和回应对象里
    assert "小白" not in people.split("\n## ", 1)[0] and "小白" not in text.split("回应对象:", 1)[1].split("\n", 1)[0]
