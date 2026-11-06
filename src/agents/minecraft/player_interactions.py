"""别的玩家和角色的互动：从肢体动作事件、切磋结果里读出结构化的一条，交给存储台账记账。

只多读事件、多发一条只落库的 ``game.player.interacted``：不改游戏 Agent 怎么做事，也不改主播当场说什么。
主播不订阅这条事件——同一个动作她已经从肢体动作那条叙事知道了；玩家记忆只给她的下一场用，不给游戏 Agent。

- 肢体动作：Mod 的 ``player_body_language`` 事件另带结构化的 ``player``（``name``、``uuid``）。每条事件记一次
  打招呼，连同这一段里做了哪几种动作；里面有朝她扔东西的一段，再记一次送东西，事实是那半句话
  （"朝我扔了 3 个面包"）。动作只从 Mod 原话的固定说法里认，数量照原话留着，不另解析。
- 切磋：``fight(spar=true)`` 的结果细节 ``spar`` 写着怎么收的手，Mod 认出了点名的玩家时还带
  ``spar.opponent``（``name``、``uuid``）。收手时有它就按它的 UUID 记一笔，按观察编号点的对手也认得出；
  旧版 Mod 不带它，退回下令那一刻按名字点的对手，由存储台账按"本场见过的、叫这个名字的只有一位"去对。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from src.agents.minecraft.maicraft import GoalRun
from src.agents.minecraft.player_body_language import spoken_fact
from src.modules.events.payloads.game_player import GamePlayerInteractedPayload

__all__ = ["SelfChatLog", "SparOpponents", "body_language_interactions", "player_of", "spar_interaction"]

#: Mod 肢体动作原话里的固定说法 → 记账用的动作名（按出现与否计，次数看打招呼的条数）
_ACTIONS: Tuple[Tuple[str, str], ...] = (
    ("蹲起", "蹲起"),
    ("挥", "挥手"),
    ("点头", "点头"),
    ("摇头", "摇头"),
    ("跳", "原地跳"),
    ("拿出", "拿出东西"),
    ("扔", "扔东西"),
    ("拍了", "拍一下"),
    ("回头", "回头看"),
)

#: 切磋怎么收的手 → 谁赢：他到了停手线、她收住了最后一刀算她赢；她先到停手线算她输；其余没分出胜负
_SPAR_RESULTS: Dict[str, str] = {
    "they_at_line": "won",
    "finishing_blow": "won",
    "me_at_line": "lost",
    "they_stopped": "even",
    "they_left": "even",
    "no_hits": "even",
}

#: 记着多少场还没收手的切磋（下令后没等到结束就换世界、断线的，最早的先丢）
_MAX_PENDING_SPARS = 16

#: 记着最近多少个已经报过的公屏说话目标（同一个目标从回复和事件流两头都可能看到结束）
_MAX_ANNOUNCED_CHATS = 64


def player_of(event: Dict[str, Any]) -> Tuple[str, str]:
    """肢体动作事件上的玩家：(UUID, 名字)；没带结构化字段时都是空串。"""
    player = event.get("player")
    if not isinstance(player, dict):
        return "", ""
    return str(player.get("uuid") or "").strip(), str(player.get("name") or "").strip()


def _tossed(fact: str, name: str) -> str:
    """动作那句里朝她扔东西的半句（"朝我扔了 3 个面包"）；没有扔给她的为空串。"""
    for clause in re.split(r"[，；]", fact):
        if "扔" not in clause or "我" not in clause:
            continue
        # 去掉句首的"玩家 某某"：卡片上名字另写，事实只留动作
        clause = re.sub(rf"^\s*玩家\s*{re.escape(name)}\s*", "", clause) if name else clause
        return clause.strip()
    return ""


def body_language_interactions(
    event: Dict[str, Any], *, save_id: str = "", save_name: str = ""
) -> List[GamePlayerInteractedPayload]:
    """一条肢体动作事件 → 打招呼一条，里面有朝她扔东西的再加送东西一条；没带玩家 UUID 的不记。"""
    uuid, name = player_of(event)
    if not uuid:
        return []
    message = str(event.get("message") or "")
    actions = [label for word, label in _ACTIONS if word in message]
    common = {"game": "minecraft", "player_uuid": uuid, "player_name": name, "save_id": save_id, "save_name": save_name}
    found = [GamePlayerInteractedPayload(kind="greet", actions=actions, **common)]
    tossed = _tossed(spoken_fact(message), name)
    if tossed:
        found.append(GamePlayerInteractedPayload(kind="gift", fact={"said": tossed}, **common))
    return found


class SelfChatLog:
    """她在公屏打出去的话：公屏说话的目标结束、那句交出去了，取一次原话；同一个目标只取一次。

    ``chat`` 结果细节带 ``message``（发了哪句）与 ``submitted``（交出去没有）；打字中途被替换、取消的
    ``submitted=false``，不算说过。对一只生物说话（细节带 ``to``）不进公屏，以 ``/`` 开头的是游戏命令，都不算。
    """

    def __init__(self) -> None:
        self._seen: Dict[int, None] = {}

    def take(self, run: GoalRun) -> str:
        """结束了的公屏说话目标 → 交出去的那句话；不是、没交出去或已经取过的为空串。"""
        ability = run.ability
        if not run.finished or not (ability == "chat" or ability.endswith(":chat")) or run.goal_id in self._seen:
            return ""
        details = (run.result or {}).get("details")
        if not isinstance(details, dict) or details.get("submitted") is not True or "to" in details:
            return ""
        message = str(details.get("message") or "").strip()
        if not message or message.startswith("/"):
            return ""
        self._seen[run.goal_id] = None
        while len(self._seen) > _MAX_ANNOUNCED_CHATS:
            self._seen.pop(next(iter(self._seen)))
        return message


class SparOpponents:
    """下令切磋时记下目标编号对应的对手名字，收手时取出来（旧版 Mod 的结果不带对手时才用它）。"""

    def __init__(self) -> None:
        self._names: Dict[int, str] = {}

    def note(self, goal_id: int, arguments: Dict[str, Any]) -> None:
        """``execute`` 下了一个目标：是按名字点了对手的切磋就记下名字。"""
        goal = arguments.get("goal")
        if not isinstance(goal, dict):
            return
        ability = str(goal.get("ability") or "")
        parameters = goal.get("parameters") if isinstance(goal.get("parameters"), dict) else {}
        target = goal.get("target") if isinstance(goal.get("target"), dict) else {}
        if not (ability == "fight" or ability.endswith(":fight")) or parameters.get("spar") is not True:
            return
        name = str(target.get("name") or "").strip() if target.get("kind") == "player" else ""
        if not name:
            return
        self._names[goal_id] = name
        while len(self._names) > _MAX_PENDING_SPARS:
            self._names.pop(next(iter(self._names)))

    def pop(self, goal_id: int) -> str:
        """收手了：取出并忘掉这场切磋的对手名字；没记过为空串。"""
        return self._names.pop(goal_id, "")


def _spar_opponent(spar: Dict[str, Any]) -> Tuple[str, str]:
    """结果里 Mod 认出的对手：(UUID, 名字)；没带（旧版 Mod、没认出点名的玩家）时都是空串。"""
    opponent = spar.get("opponent")
    if not isinstance(opponent, dict):
        return "", ""
    return str(opponent.get("uuid") or "").strip(), str(opponent.get("name") or "").strip()


def spar_interaction(
    run: GoalRun, named: str, *, save_id: str = "", save_name: str = ""
) -> Optional[GamePlayerInteractedPayload]:
    """一场切磋收手 → 一条切磋互动（谁赢、各打中几下）；不知道对手、没打起来或被取消的不记。

    对手先认结果里 Mod 给的 ``spar.opponent``（按 UUID 记，按观察编号点的也认得出）；没带时退回
    下令那一刻按名字点的对手 ``named``，只有名字、由存储台账去对。
    """
    details = (run.result or {}).get("details")
    spar = details.get("spar") if isinstance(details, dict) else None
    if not isinstance(spar, dict):
        return None
    uuid, name = _spar_opponent(spar)
    # 打完一局：Mod 认出的对手优先；旧版 Mod 不带时用下令点的名字，两样都没有就不知道和谁打的
    if not uuid:
        name = named
    if not uuid and not name:
        return None
    result = _SPAR_RESULTS.get(str(spar.get("ended_by") or ""))
    if result is None:
        return None
    fact: Dict[str, Any] = {"result": result}
    for key, value in (("my_hits", details.get("hits")), ("their_hits", spar.get("hits_taken"))):
        if isinstance(value, int) and not isinstance(value, bool):
            fact[key] = value
    return GamePlayerInteractedPayload(
        game="minecraft",
        kind="spar",
        player_uuid=uuid,
        player_name=name or named,
        fact=fact,
        save_id=save_id,
        save_name=save_name,
    )
