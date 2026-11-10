"""下播整理第四次调用的原料与校验：更新游戏社交事实，顺带记下有原话作证的"一起做的事"与"帮过忙"。

原料是紧凑集，不含她的台词全量与观察回包：

- 本场玩家在游戏公屏说的、存了原话的那些句子（编号"公屏#N"，只在这一次调用里有意义）；
- 本场记下的互动（打招呼、送东西、切磋）；
- 本场游戏记录（交付了的委派、做成了的里程碑与上报，与档案那次同一份）；
- 本场记住的地点；
- 本场玩过的游戏里现有的现行社交事实（编号"事实#N"即行号，运营者锁定的标出来）。

校验全部是代码，与场内工具同一套口径：

- kind 是六类之一，text 非空且不超长；
- 非归纳类必须引一句公屏（chat），quote 是那句原文一字不差的片段（压掉连续空白的写法也认）；
  归纳类（她的角色、待弄清）可以不引，引了就照样核对；
- 取代目标须是给模型看过的现行行、同类、没被运营者锁定；
- 每类在一个游戏存档里的现行行数加上本场新写的不超过上限（取代不占新名额）；
- 互动只收 together / helped，必须引那位玩家自己说的一句公屏。

没通过的写进整理报告的未登记清单。事实挂的游戏与存档：引了公屏的取那句公屏的游戏与存档，没引的
取本场最后一句玩家公屏所在的游戏与存档（都没有时取本场玩过的第一个游戏、按开播时刻认存档）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from src.modules.storage.models.game_players import (
    GAME_CHAT_PLAYER,
    GAME_CHAT_STREAMER,
    INTERACTION_HELPED,
    INTERACTION_TOGETHER,
    GameChatLine,
    PendingPlayerInteraction,
    PlayerInteraction,
)
from src.modules.storage.models.memory import UnregisteredItem
from src.modules.storage.models.social_facts import (
    MAX_ACTIVE_FACTS_PER_KIND,
    QUOTE_OPTIONAL_KINDS,
    SOCIAL_FACT_KINDS,
    NewSocialFact,
    SocialFact,
)

from .saves import SaveTimeline
from .textmatch import verbatim_quote

__all__ = [
    "MAX_FACTS_PER_KIND",
    "SocialCheck",
    "SocialMaterial",
    "build_social_material",
    "validate_social_output",
]

# 每类在一个存档里至多几行现行事实（与场内工具同一个数）
MAX_FACTS_PER_KIND = MAX_ACTIVE_FACTS_PER_KIND
# 一场至多给模型看多少句玩家公屏（刷屏的场次只留最后这些）
_MAX_PLAYER_LINES = 120
# 公屏对话双侧行的总量上限：她的行进对话流后行数随场次增长，截到最近的这些行
_MAX_DIALOG_LINES = 160
_TEXT_MAX_CHARS = 60
_QUOTE_MAX_CHARS = 80
_WHAT_MAX_CHARS = 30
_CHAT_PREFIX = "公屏#"
_FACT_PREFIX = "事实#"
_PLAYER_PREFIX = "玩家"

_KIND_LABELS: Dict[str, str] = {
    "setting": "环境形态",
    "members": "成员关系",
    "sharing": "共享惯例",
    "works": "进行中的事",
    "her_role": "她的角色",
    "open_question": "待弄清",
}
_INTERACTION_LABELS: Dict[str, str] = {"greet": "打招呼", "gift": "送东西", "spar": "切磋"}


@dataclass(frozen=True)
class SocialMaterial:
    """第四次调用的原料：给模型看的一整段，与核对时要用的编号表。"""

    text: str = ""
    # 公屏编号 → 那一句玩家公屏
    chat_by_number: Dict[int, GameChatLine] = field(default_factory=dict)
    # 玩家主键 → 本次调用里的代号
    player_codes: Dict[int, str] = field(default_factory=dict)
    # 玩家主键 → 现在的名字（代号还原回真名用；读不到的玩家不在表里）
    player_names: Dict[int, str] = field(default_factory=dict)
    # 给模型看过的现行事实（行号 → 行）
    facts: Dict[int, SocialFact] = field(default_factory=dict)
    # 没引公屏的归纳行挂在哪：(游戏, 存档编号, 存档名)
    default_scope: Tuple[str, str, str] = ("", "", "")

    @property
    def empty(self) -> bool:
        """没有任何能据以更新的原料（没有玩家公屏、没有互动、没有现有事实）时为真，第四次调用不必跑。"""
        return not self.text

    def restore_names(self, text: str) -> str:
        """把模型写的玩家代号换回真名：代号只在这一个调用里有效，落库的正文得让下一次直播认得出是谁，
        也只有真名才能被"忘了我"的删除按名字匹配到。"""
        for player_id, code in self.player_codes.items():
            name = self.player_names.get(player_id)
            if name:
                text = text.replace(code, name)
        return text


@dataclass
class SocialCheck:
    """校验结果：要写进社交事实表的行、要写进互动表的行、没通过的条目。"""

    facts: List[NewSocialFact] = field(default_factory=list)
    interactions: List[PendingPlayerInteraction] = field(default_factory=list)
    missed: List[UnregisteredItem] = field(default_factory=list)


def _clip(text: str) -> str:
    return " ".join((text or "").split())


def build_social_material(
    *,
    chat_lines: Sequence[GameChatLine],
    interactions: Sequence[PlayerInteraction],
    facts: Sequence[SocialFact],
    player_names: Mapping[int, str],
    records_text: str,
    places_text: str,
    games: Sequence[str],
    saves: SaveTimeline,
    started_at_ms: int,
    clock_text: Callable[[int], str],
) -> SocialMaterial:
    """拼第四次调用的原料。``player_names`` 是玩家主键 → 现在的名字（读不到的玩家不给代号）。

    公屏按时间序双侧行编排：玩家的行带编号（作证据），她的行标"她"不带编号（引不了，
    证据必须是别人的话）——只给玩家的行，一句回答看不出是在回答什么（她问小百合去哪了、
    玩家答"在菜地旁边"，模型会把这句回答嫁接到别的话题上编出缝合事实）。
    """
    spoken = [
        line
        for line in chat_lines
        if line.direction == GAME_CHAT_PLAYER and line.player_id is not None and (line.content or "").strip()
    ][-_MAX_PLAYER_LINES:]
    active = [fact for fact in facts if fact.status == "active"]
    if not spoken and not interactions and not active:
        return SocialMaterial()

    codes: Dict[int, str] = {}

    def code_of(player_id: int) -> str:
        return codes.setdefault(int(player_id), f"{_PLAYER_PREFIX}{len(codes) + 1}")

    chat_by_number: Dict[int, GameChatLine] = {}
    chat_rows: List[str] = []
    if spoken:
        window_start, window_end = int(spoken[0].at_ms), int(spoken[-1].at_ms)
        dialog = [
            line
            for line in chat_lines
            if (line.content or "").strip()
            and line.direction in (GAME_CHAT_PLAYER, GAME_CHAT_STREAMER)
            and window_start <= int(line.at_ms) <= window_end
        ][-_MAX_DIALOG_LINES:]
        for line in dialog:
            clock = clock_text(int(line.at_ms))
            content = _clip(line.content)
            if line.direction == GAME_CHAT_PLAYER and line.player_id is not None:
                number = len(chat_by_number) + 1
                chat_by_number[number] = line
                chat_rows.append(f"{_CHAT_PREFIX}{number} {clock} {code_of(int(line.player_id or 0))}：{content}")
            else:
                chat_rows.append(f"{clock} 她：{content}")
    interaction_rows = [
        f"- {clock_text(int(item.at_ms))} {code_of(item.player_id)}：{_INTERACTION_LABELS.get(item.kind, item.kind)}"
        for item in interactions
    ]
    fact_rows = [
        f"{_FACT_PREFIX}{fact.id}（{_KIND_LABELS.get(fact.kind, fact.kind)}{'，运营者定的' if fact.human_locked else ''}）"
        f"{fact.text}"
        for fact in active
    ]
    roster = "；".join(f"{code} = {player_names.get(player_id, '（名字未知）')}" for player_id, code in codes.items())
    parts = [
        "【现有的社交事实】\n" + ("\n".join(fact_rows) if fact_rows else "没有"),
        "【游戏里的玩家】\n" + (roster or "没有"),
        "【本场游戏公屏对话】（她的行没有编号，引证据只引玩家行）\n" + ("\n".join(chat_rows) if chat_rows else "没有"),
        "【本场记下的互动】\n" + ("\n".join(interaction_rows) if interaction_rows else "没有"),
    ]
    if records_text:
        parts.append(records_text)
    parts.append(places_text)

    if spoken:
        last = spoken[-1]
        scope = (last.game, last.save_id, last.save_name)
    elif games:
        save = saves.save_at(games[0], int(started_at_ms))
        scope = (games[0], save.id, save.name)
    elif active:
        scope = (active[0].game, active[0].save_id, active[0].save_name)
    else:
        scope = ("", "", "")
    return SocialMaterial(
        text="\n\n".join(parts),
        chat_by_number=chat_by_number,
        player_codes=codes,
        player_names={int(pid): str(name) for pid, name in player_names.items() if name},
        facts={int(fact.id): fact for fact in active},
        default_scope=scope,
    )


def _number(raw: Any, prefix: str) -> Optional[int]:
    """模型写的编号（"公屏#3"、"#3"、3）换成数字；认不出返回 None。"""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw).strip().replace(" ", "")
    for head in (prefix, "#"):
        if text.startswith(head):
            text = text[len(head) :]
            break
    return int(text) if text.isdigit() else None


def _miss(check: SocialCheck, what: str, quote: str, reason: str) -> None:
    check.missed.append(UnregisteredItem(kind="social_fact", what=what, quote=quote, reason=reason))


def validate_social_output(data: Mapping[str, Any], material: SocialMaterial, *, live_session_id: int) -> SocialCheck:
    """把模型的输出逐条核成要写的行；没通过的进未登记清单。"""
    check = SocialCheck()
    rows = data.get("facts")
    counts: Dict[Tuple[str, str, str], int] = {}
    for fact in material.facts.values():
        key = (fact.game, fact.save_id, fact.kind)
        counts[key] = counts.get(key, 0) + 1
    superseded: set[int] = set()
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip()
        text = str(raw.get("text") or "").strip()
        quote = str(raw.get("quote") or "").strip()
        if kind not in SOCIAL_FACT_KINDS or not text or len(text) > _TEXT_MAX_CHARS:
            _miss(check, text, quote, f"类别不是六类之一，或事实为空、超过 {_TEXT_MAX_CHARS} 字")
            continue
        chat_number = _number(raw.get("chat"), _CHAT_PREFIX)
        line = material.chat_by_number.get(chat_number) if chat_number is not None else None
        if raw.get("chat") not in (None, "") and line is None:
            _miss(check, text, quote, "引的不是给出的玩家公屏")
            continue
        if line is None and kind not in QUOTE_OPTIONAL_KINDS:
            _miss(check, text, quote, "这一类必须引一句玩家公屏作依据")
            continue
        verified = ""
        if line is not None:
            checked = verbatim_quote(quote, line.content, _QUOTE_MAX_CHARS)
            if checked is None:
                _miss(check, text, quote, f"原话不是{_CHAT_PREFIX}{chat_number}那句一字不差的片段")
                continue
            verified = checked
        elif quote:
            _miss(check, text, quote, "写了原话却没引是哪句公屏")
            continue
        supersedes = _number(raw.get("supersedes"), _FACT_PREFIX)
        if raw.get("supersedes") not in (None, "") and supersedes is None:
            _miss(check, text, quote, "取代的编号认不出")
            continue
        if line is not None:
            scope = (line.game, line.save_id, line.save_name)
        elif supersedes is not None and supersedes in material.facts:
            old_fact = material.facts[supersedes]
            scope = (old_fact.game, old_fact.save_id, old_fact.save_name)
        else:
            scope = material.default_scope
        if supersedes is not None:
            old = material.facts.get(supersedes)
            if old is None or supersedes in superseded:
                _miss(check, text, quote, f"{_FACT_PREFIX}{supersedes} 不是给出的现行事实（或这一场已经取代过）")
                continue
            if old.human_locked:
                _miss(check, text, quote, f"{_FACT_PREFIX}{supersedes} 是运营者定的，不能取代")
                continue
            if old.kind != kind:
                _miss(check, text, quote, f"{_FACT_PREFIX}{supersedes} 不是同一类，取代只在同类之间")
                continue
            superseded.add(supersedes)
        else:
            key = (scope[0], scope[1], kind)
            if counts.get(key, 0) >= MAX_FACTS_PER_KIND:
                _miss(check, text, quote, f"{_KIND_LABELS[kind]}已经有 {MAX_FACTS_PER_KIND} 条，要先取代旧的")
                continue
            counts[key] = counts.get(key, 0) + 1
        check.facts.append(
            NewSocialFact(
                kind=kind,
                text=material.restore_names(text),
                quote=verified,
                game=scope[0],
                save_id=scope[1],
                save_name=scope[2],
                evidence_game_chat_id=int(line.id) if line is not None else None,
                supersedes_id=supersedes,
                created_session_id=int(live_session_id),
                updated_session_id=int(live_session_id),
            )
        )

    codes = {code: player_id for player_id, code in material.player_codes.items()}
    interactions = data.get("interactions")
    for raw in interactions if isinstance(interactions, list) else []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip()
        what = str(raw.get("what") or "").strip()[:_WHAT_MAX_CHARS]
        quote = str(raw.get("quote") or "").strip()
        player_raw = str(raw.get("player") or "").strip().replace(" ", "")
        if player_raw.isdigit():
            player_raw = f"{_PLAYER_PREFIX}{player_raw}"
        player_id = codes.get(player_raw)
        chat_number = _number(raw.get("chat"), _CHAT_PREFIX)
        line = material.chat_by_number.get(chat_number) if chat_number is not None else None
        if kind not in (INTERACTION_TOGETHER, INTERACTION_HELPED) or not what:
            _miss(check, what, quote, "互动只收一起做的事（together）与帮过忙（helped），且要写做了什么")
            continue
        if player_id is None or line is None or line.player_id != player_id:
            _miss(check, what, quote, "互动要引那位玩家自己在公屏说的一句")
            continue
        verified = verbatim_quote(quote, line.content, _QUOTE_MAX_CHARS)
        if verified is None:
            _miss(check, what, quote, f"原话不是{_CHAT_PREFIX}{chat_number}那句一字不差的片段")
            continue
        check.interactions.append(
            PendingPlayerInteraction(
                player_id=player_id,
                kind=kind,
                fact={"what": material.restore_names(what), "quote": verified},
                save_id=line.save_id,
                save_name=line.save_name,
                at_ms=int(line.at_ms),
            )
        )
    return check
