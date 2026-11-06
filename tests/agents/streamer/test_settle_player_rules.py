"""对游戏玩家承诺的核对规则：她在公屏打的每一句能对上谁，给模型看的玩家代号与公屏段。"""

from __future__ import annotations

from typing import Any

from src.agents.streamer.memory.settle_players import build_player_chat, chat_targets
from src.modules.storage.models.game_players import GameChatLine, GamePlayer


def _line(index: int, direction: str, content: str = "", player_id: Any = None) -> GameChatLine:
    return GameChatLine(
        id=index,
        live_session_id=1,
        game="minecraft",
        direction=direction,
        player_id=player_id,
        content=content,
        at_ms=index,
    )


def _player(player_id: int, name: str, *former: str) -> GamePlayer:
    return GamePlayer(
        id=player_id,
        game="minecraft",
        uuid=f"uuid-{player_id}",
        name=name,
        names=[*former, name],
        first_seen_ms=0,
        last_seen_ms=0,
    )


def test_chat_targets_follow_window_and_names() -> None:
    """紧接着：3 句之内、中间只有她自己的话；点名：现在或以前的名字，单字名不算。"""
    players = {1: _player(1, "Alex", "Steve"), 2: _player(2, "Bo"), 3: _player(3, "Z")}
    lines = [
        _line(1, "player", "在吗", 1),
        _line(2, "streamer", "在的"),
        _line(3, "streamer", "等我一下"),
        _line(4, "streamer", "马上来"),
        _line(5, "streamer", "好了"),
        _line(6, "streamer", "steve 你的房子我记着呢"),
        _line(7, "streamer", "Z 你好"),
    ]
    targets = chat_targets(lines, players)
    assert targets[2] == targets[3] == targets[4] == frozenset({1}), "3 句之内、中间只有她的话：在回他"
    assert targets[5] == frozenset(), "超出 3 句不算接着他说"
    assert targets[6] == frozenset({1}), "以前的名字也算点名，不分大小写"
    assert targets[7] == frozenset(), "单字名不按点名认"


def test_follow_needs_his_line_within_sixty_seconds() -> None:
    """紧接着还要离他那句不超过 60 秒：隔了一分多钟、中间只有她自己的话，也不算在回他。"""
    players = {1: _player(1, "Alex")}
    lines = [
        GameChatLine(
            id=1, live_session_id=1, game="minecraft", direction="player", player_id=1, content="在吗", at_ms=0
        ),
        GameChatLine(id=2, live_session_id=1, game="minecraft", direction="streamer", content="在", at_ms=60_000),
        GameChatLine(id=3, live_session_id=1, game="minecraft", direction="streamer", content="好", at_ms=60_001),
    ]
    targets = chat_targets(lines, players)
    assert (targets[2], targets[3]) == (frozenset({1}), frozenset())


def test_short_names_need_a_whole_word_match() -> None:
    """三个字以上按子串认；两个字的名字前后不能紧挨字母、数字、下划线或汉字。"""
    players = {1: _player(1, "Bo"), 2: _player(2, "小鱼"), 3: _player(3, "Alex")}
    lines = [
        _line(1, "streamer", "Bob 你好"),
        _line(2, "streamer", "bo，明天见"),
        _line(3, "streamer", "猫爱吃小鱼干"),
        _line(4, "streamer", "在的，小鱼，等我一下"),
        _line(5, "streamer", "alexander 来了"),
    ]
    targets = chat_targets(lines, players)
    assert targets[1] == targets[3] == frozenset(), "两个字的名字撞上别的词不算点名"
    assert (targets[2], targets[4]) == (frozenset({1}), frozenset({2}))
    assert targets[5] == frozenset({3}), "三个字以上的名字照旧按子串认"


def test_player_chat_codes_numbers_and_lookup() -> None:
    """代号先给台词里回应过的；公屏只给模型看她打的字和前面 3 句；编号与代号都能换回原行。"""
    players = {1: _player(1, "Alex", "Steve"), 2: _player(2, "Bob")}
    lines = [
        _line(1, "player", "很早以前的话", 2),
        _line(2, "player", "麦麦", 2),
        _line(3, "player", "hi", 2),
        _line(4, "player", "在吗", 1),
        _line(5, "other"),
        _line(6, "streamer", "Alex 我在"),
    ]
    chat = build_player_chat(speech_player_ids=[1], lines=lines, players=players, clock_text=lambda ms: "20:00")
    assert chat.codes == {1: "玩家1", 2: "玩家2"}
    assert "玩家1 = Alex（以前叫 Steve）；玩家2 = Bob" in chat.text
    assert "很早以前的话" not in chat.text, "她那句之前 3 句以外的不给"
    assert "公屏#1 20:00 你：Alex 我在" in chat.text and "（认不出是谁的一句）" in chat.text
    assert chat.player_of("玩家2") == 2 and chat.player_of("2") == 2 and chat.player_of("玩家9") is None
    assert chat.chat_line("公屏#1") is lines[5] and chat.chat_line(1) is lines[5] and chat.chat_line("公屏#2") is None
    assert chat.targets[6] == frozenset({1}), "隔着认不出是谁的一句不算接着谁，点了名的算"
    empty = build_player_chat(
        speech_player_ids=[], lines=[_line(1, "player", "hi", 2)], players=players, clock_text=str
    )
    assert (empty.codes, empty.text) == ({}, ""), "她没回应玩家、也没在公屏打字：这一段不出现"
