"""三类候选校验器的门禁逐条核对：观众定下、她说欠着的、玩家自述，各覆盖通过与打回路径。"""

from __future__ import annotations

from typing import Callable, Optional

from src.agents.streamer.memory.saves import SaveTimeline
from src.agents.streamer.memory.settle_candidates import (
    validate_claimed_missing,
    validate_player_candidates,
    validate_viewer_candidates,
)
from src.agents.streamer.memory.settle_players import PlayerChat
from src.modules.storage.models.chat import SettleSpeechLine
from src.modules.storage.models.game_players import GameChatLine


def _line(line_id: int, content: str, **overrides: object) -> SettleSpeechLine:
    fields: dict = {
        "id": line_id,
        "timestamp_ms": 1_000 + line_id,
        "content": content,
        "reply_content": "这只猫以后就叫团子啦",
        "reply_viewer_id": 9,
        "reply_chat_id": 501,
        "reply_simulated": False,
    }
    fields.update(overrides)
    return SettleSpeechLine(**fields)


def _line_of(*lines: SettleSpeechLine) -> Callable[[object], Optional[SettleSpeechLine]]:
    by_key = {index + 1: line for index, line in enumerate(lines)}
    return lambda raw: by_key.get(raw)  # type: ignore[return-value]


def _game_of(raw: object) -> Optional[str]:
    return "Minecraft" if raw == "Minecraft" else None


_SAVES = SaveTimeline()


def _viewer_base(**kwargs: object) -> dict:
    kwargs.setdefault("game_of", _game_of)
    kwargs.setdefault("saves", _SAVES)
    kwargs.setdefault("taken_evidence", ())
    kwargs.setdefault("taken_titles", ())
    kwargs.setdefault("rejected_viewer_chats", ())
    kwargs.setdefault("rejected_titles", ())
    return kwargs


def test_viewer_defined_pass_and_reject() -> None:
    """观众定下的：核得上的建候选；引用的不是回真实弹幕那行的打回。"""
    good = _line(71, "好，那就叫团子了")
    check = validate_viewer_candidates(
        [
            {
                "kind": "viewer_defined",
                "title": "小猫取名团子",
                "quote": "好，那就叫团子了",
                "word": "团子",
                "viewer_quote": "这只猫以后就叫团子啦",
                "line": 1,
                "game": "Minecraft",
            },
            {
                "kind": "viewer_defined",
                "title": "另一件事",
                "quote": "台词",
                "word": "团子",
                "viewer_quote": "弹幕",
                "line": 99,
            },
        ],
        line_of=_line_of(good),
        **_viewer_base(),
    )
    assert len(check.candidates) == 1
    assert check.candidates[0].kind == "viewer_defined"
    assert check.candidates[0].title == "小猫取名团子"
    assert len(check.missed) == 1
    assert "不是她回复观众弹幕的那一行" in check.missed[0].reason


def test_viewer_defined_word_not_in_chat_is_rejected() -> None:
    """名字不在弹幕原文里：是她自己起的，不是观众定下的，打回。"""
    line = _line(72, "就叫团子")
    check = validate_viewer_candidates(
        [
            {
                "kind": "viewer_defined",
                "title": "小猫取名团子",
                "quote": "就叫团子",
                "word": "雪球",
                "viewer_quote": "这只猫以后就叫团子啦",
                "line": 1,
            }
        ],
        line_of=_line_of(line),
        **_viewer_base(),
    )
    assert check.candidates == []
    assert len(check.missed) == 1
    assert "不在标题里" in check.missed[0].reason


def test_claimed_missing_pass_and_reject() -> None:
    """她说欠着的：账上没有的建成候选；账上有同句证据的不列。"""
    line = _line(73, "上次答应过你们要把屋顶盖好", reply_content=None, reply_viewer_id=None, reply_chat_id=None)
    check = validate_claimed_missing(
        [
            {"what": "答应盖屋顶", "quote": "上次答应过你们要把屋顶盖好", "line": 1},
            {"what": "答应盖屋顶", "quote": "台词对不上", "line": 42},
        ],
        line_of=_line_of(line),
        game_of=_game_of,
        saves=_SAVES,
        ledger_titles=(),
        taken_lines=(73,),
        rejected_reply_chats=(),
        rejected_titles=(),
    )
    assert check.candidates == [], "那一行已作过新承诺的证据，不是账漏"
    assert len(check.missed) == 1  # 去重命中静默跳过；第二行引用对不上台词行，打回
    check2 = validate_claimed_missing(
        [{"what": "答应盖屋顶", "quote": "上次答应过你们要把屋顶盖好", "line": 1}],
        line_of=_line_of(line),
        game_of=_game_of,
        saves=_SAVES,
        ledger_titles=(),
        taken_lines=(),
        rejected_reply_chats=(),
        rejected_titles=(),
    )
    assert len(check2.candidates) == 1
    assert check2.candidates[0].kind == "claimed_missing"


def test_claimed_missing_bad_line_is_rejected() -> None:
    """引用的不是本场她的台词：打回并写明原因。"""
    line = _line(74, "台词", reply_content=None, reply_viewer_id=None, reply_chat_id=None)
    check = validate_claimed_missing(
        [{"what": "某事", "quote": "台词", "line": 7}],
        line_of=_line_of(line),
        game_of=_game_of,
        saves=_SAVES,
        ledger_titles=(),
        taken_lines=(),
        rejected_reply_chats=(),
        rejected_titles=(),
    )
    assert check.candidates == []
    assert len(check.missed) == 1
    assert "引用的不是本场她的台词" in check.missed[0].reason


def _player_chat() -> PlayerChat:
    her_line = GameChatLine(
        id=301, live_session_id=1, game="Minecraft", direction="streamer", at_ms=1_100, content="捡到好多钻石，发财了"
    )
    his_line = GameChatLine(
        id=302,
        live_session_id=1,
        game="Minecraft",
        direction="player",
        player_id=5,
        at_ms=900,
        content="我今天捡到好多钻石",
    )
    return PlayerChat(
        codes={5: "玩家1"},
        text="",
        lines_by_number={301: her_line},
        targets={301: frozenset({5})},
        player_lines=(his_line,),
    )


def test_player_candidates_pass_and_reject() -> None:
    """玩家自述：他原话里的关键词核得上的建候选；关键词是他没说过的打回。"""
    line = _line(
        75, "捡到好多钻石，发财了", reply_content=None, reply_viewer_id=None, reply_chat_id=None, reply_player_id=5
    )
    rows = [
        {
            "player": "玩家1",
            "title": "玩家捡到钻石",
            "quote": "捡到好多钻石，发财了",
            "word": "钻石",
            "player_quote": "我今天捡到好多钻石",
            "chat": 301,
        },
        {
            "player": "玩家1",
            "title": "玩家捡到钻石",
            "quote": "捡到好多钻石，发财了",
            "word": "绿宝石",
            "player_quote": "我今天捡到好多钻石",
            "chat": 301,
        },
    ]
    check = validate_player_candidates(
        rows,
        players=_player_chat(),
        line_of=_line_of(line),
        decided_titles={},
        rejected_player_chats=(),
    )
    assert len(check.candidates) == 1
    assert check.candidates[0].player_id == 5
    assert len(check.missed) == 1
    assert "不在标题里" in check.missed[0].reason
