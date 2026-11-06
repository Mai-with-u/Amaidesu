"""下播整理里对游戏玩家的承诺：给模型看的玩家代号与公屏段，以及"对谁"的代码核对。

她答应某位玩家的事，证据二选一，都必须是她自己说出去的：

- 她播出的那句台词，而且落库时记下了在回应哪位玩家（那一窗只有他一位在游戏里开过口）；
- 她在游戏公屏打出去的那句：点了他的名字，或者在他上一句之后 3 句之内、中间没有别人说话。

模型写的"对谁"必须按上面两条之一对上那位玩家，对不上的不登记、进未登记清单。和对观众的承诺
同一个口径：宁可漏登记、让运营者在整理报告里看见，也不把答应 A 的事记到 B 头上。提示词里玩家
只用本场代号（玩家1、玩家2），名字写在代号旁边，供模型对照她台词里的称呼。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set

from src.modules.storage.models.game_players import (
    GAME_CHAT_PLAYER,
    GAME_CHAT_STREAMER,
    GameChatLine,
    GamePlayer,
)

__all__ = [
    "CHAT_NUMBER_PREFIX",
    "FOLLOW_WINDOW",
    "PLAYER_CODE_PREFIX",
    "PlayerChat",
    "build_player_chat",
    "chat_targets",
]

PLAYER_CODE_PREFIX = "玩家"
CHAT_NUMBER_PREFIX = "公屏#"
# 她这句在他上一句之后几句之内（中间只能是她自己的话），才算接着他说
FOLLOW_WINDOW = 3
# 公屏段至多给模型看多少行（她打的字连同前面几句）：刷屏的场次只留最后这些
MAX_CHAT_LINES = 120
# 短于这个长度的名字不按点名认：单字名太容易撞上普通的字
_MIN_NAME_CHARS = 2


@dataclass(frozen=True)
class PlayerChat:
    """整理原料里和游戏玩家有关的部分：代号、给模型看的那一段、她在公屏打的每一句能对上谁。"""

    # 玩家主键 → 本场代号（玩家1、玩家2，按在原料里先出现的先后）
    codes: Dict[int, str] = field(default_factory=dict)
    # 给模型看的段落；本场既没有回应玩家的台词、也没在公屏打过字时为空串
    text: str = ""
    # 公屏编号 → 她打的那一行
    lines_by_number: Dict[int, GameChatLine] = field(default_factory=dict)
    # 她那一行（game_chat 主键）→ 代码认得出的对象玩家：点了名的、或者她紧接着回的那位
    targets: Dict[int, FrozenSet[int]] = field(default_factory=dict)

    def player_of(self, raw: Any) -> Optional[int]:
        """模型写的玩家代号换成玩家主键；"玩家1"、"1" 都认，认不出返回 None。"""
        text = str(raw or "").strip()
        if not text:
            return None
        if text.isdigit():
            text = f"{PLAYER_CODE_PREFIX}{text}"
        text = text.replace(" ", "")
        for player_id, code in self.codes.items():
            if code == text:
                return player_id
        return None

    def chat_line(self, raw: Any) -> Optional[GameChatLine]:
        """模型写的公屏编号（"公屏#3"、"#3"、3）换成她打的那一行；不是她打的、认不出的返回 None。"""
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return self.lines_by_number.get(raw)
        text = str(raw or "").strip().replace(" ", "")
        for prefix in (CHAT_NUMBER_PREFIX, "#"):
            if text.startswith(prefix):
                text = text[len(prefix) :]
                break
        return self.lines_by_number.get(int(text)) if text.isdigit() else None


def _names_of(player: GamePlayer) -> List[str]:
    """按点名认人用的名字：现在的名字和以前用过的，太短的不算。"""
    names = {player.name, *player.names}
    return [name for name in names if len(name.strip()) >= _MIN_NAME_CHARS]


def chat_targets(lines: Sequence[GameChatLine], players: Mapping[int, GamePlayer]) -> Dict[int, FrozenSet[int]]:
    """她在公屏打的每一句能算作对谁说的：点了名的玩家，加上她紧接着回的那位。

    点名：他现在或以前的名字出现在她这句里（不分大小写）。紧接着：从她这句往前数，3 句之内
    遇到的第一句别人的话是他说的，中间只有她自己的话；先遇到认不出是谁的一句就不算接着谁。
    """
    targets: Dict[int, FrozenSet[int]] = {}
    for index, line in enumerate(lines):
        if line.direction != GAME_CHAT_STREAMER:
            continue
        content = (line.content or "").lower()
        found: Set[int] = {
            player_id
            for player_id, player in players.items()
            if any(name.lower() in content for name in _names_of(player))
        }
        # 往前找她这句接着的是谁：跳过她自己的话，碰到第一句别人的话就停
        for back in range(index - 1, max(-1, index - FOLLOW_WINDOW - 1), -1):
            previous = lines[back]
            if previous.direction == GAME_CHAT_STREAMER:
                continue
            if previous.direction == GAME_CHAT_PLAYER and previous.player_id is not None:
                found.add(int(previous.player_id))
            break
        targets[int(line.id)] = frozenset(found)
    return targets


def _shown_indices(lines: Sequence[GameChatLine]) -> List[int]:
    """给模型看哪几行：她打的每一句，连同它前面 3 句（判断她在回谁要看得到）；超出上限只留最后的。"""
    shown: Set[int] = set()
    for index, line in enumerate(lines):
        if line.direction == GAME_CHAT_STREAMER:
            shown.update(range(max(0, index - FOLLOW_WINDOW), index + 1))
    return sorted(shown)[-MAX_CHAT_LINES:]


def build_player_chat(
    *,
    speech_player_ids: Sequence[int],
    lines: Sequence[GameChatLine],
    players: Mapping[int, GamePlayer],
    clock_text: Callable[[int], str],
) -> PlayerChat:
    """拼出整理原料里和玩家有关的那一段。

    ``speech_player_ids`` 是她台词里回应过的玩家（按台词先后），``lines`` 是本场全部公屏聊天
    （按时间先后，含认不出是谁的占位行），``players`` 是这些玩家的当前记录。代号先给台词里回应
    过的、再给公屏上出现的；玩家记录读不到（已被删）的不给代号，他的话按认不出是谁处理。
    """
    codes: Dict[int, str] = {}

    def code_of(player_id: Optional[int]) -> Optional[str]:
        if player_id is None or int(player_id) not in players:
            return None
        return codes.setdefault(int(player_id), f"{PLAYER_CODE_PREFIX}{len(codes) + 1}")

    for player_id in speech_player_ids:
        code_of(player_id)
    shown = _shown_indices(lines)
    for index in shown:
        if lines[index].direction == GAME_CHAT_PLAYER:
            code_of(lines[index].player_id)
    if not codes and not shown:
        return PlayerChat()

    rendered: List[str] = []
    numbered: Dict[int, GameChatLine] = {}
    for index in shown:
        line = lines[index]
        clock = clock_text(int(line.at_ms))
        content = " ".join((line.content or "").split())
        code = code_of(line.player_id) if line.direction == GAME_CHAT_PLAYER else None
        if line.direction == GAME_CHAT_STREAMER:
            number = len(numbered) + 1
            numbered[number] = line
            rendered.append(f"{CHAT_NUMBER_PREFIX}{number} {clock} 你：{content}")
        elif code is not None:
            rendered.append(f"    {clock} {code}：{content}")
        else:
            rendered.append(f"    {clock} （认不出是谁的一句）")

    roster = []
    for player_id, code in codes.items():
        player = players[player_id]
        former = player.former_names()
        roster.append(f"{code} = {player.name}" + (f"（以前叫 {'、'.join(former[-2:])}）" if former else ""))
    parts = ["【游戏里的玩家】\n" + "；".join(roster)]
    if rendered:
        parts.append("【你在游戏公屏打出去的字（前面附着玩家说的话）】\n" + "\n".join(rendered))
    known = {player_id: players[player_id] for player_id in codes}
    return PlayerChat(
        codes=codes,
        text="\n\n".join(parts),
        lines_by_number=numbered,
        targets=chat_targets(lines, known),
    )
