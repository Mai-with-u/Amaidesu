"""下播整理核对"她说做好了"：本场游戏记录里撑得住"做成了"的那几条，以及引用的核对。

她嘴上说"门装好了"不等于游戏里真装好了：游戏那边没交付就停了、或者根本没去做，她照样会说做好了。
整理把这句写成档案里的事实，下一场她就带着一件没发生的事开播，越说越真。所以整理时把本场的游戏记录
编号列给模型——了结了的委派（只有交付了的能作证）、说明这一步做成了的里程碑与上报——档案里写游戏里
做成了什么、了结承诺时说做完了，都要引一条撑着它的记录。模型引了哪条说了不算，代码核对：引的那条
存在、是做成了的、同一个游戏、不晚于她说那句的时候。没核实到的完成说法不写进档案、不了结承诺，
连同原因进整理报告。

本场一条游戏记录都没有时（记录没接上、旧数据、游戏那边一件事都没报），无从核对，不拦。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Sequence

__all__ = [
    "GameRecord",
    "game_records",
    "is_done_claim",
    "proof_for",
    "render_records",
]

# 完成说法：游戏里做成了某件事的说法（搭好了、装上了、拿到了、做完了这类）。前面紧挨着"没、未、不、别"的
# 是没做成（"还没烤完""没装好"），不算
_DONE_CLAIM = re.compile(
    r"(?:做|盖|搭|建|装|修|弄|种|铺|围|造|摆|放|点|挖|砌|封|补|烤|拼|画|配|收拾|整理|布置|备|堆|垒|架|焊|刷|插|挂"
    r"|接|连|炼|熔|煮|炒|绑|圈|驯|立)好"
    r"|(?:做|合成|造|炼|打造)出"
    r"|(?:做|盖|搭|建|装|修|弄|种|铺|围|造|挖|砌|补|烤|炸|拆|砍|采|收|搬|清|填|堆|插|挂|炼|熔|煮|收拾|整理)完"
    r"|(?:凑|攒|配|摆|收|备)齐"
    r"|(?:挖|拿|找|采|收集|买|换|抓|钓|捡|弄)到"
    r"|(?:装|安|点|种|盖|铺|围|挂|插)上了"
    r"|种下|立起来|烤干|搞定|完成|到手|落成|竣工"
)
_NEGATION_CHARS = ("没", "未", "不", "别")

# 一场里列给模型的记录至多几条（取最近的）：第 58 场里程碑与上报合计六十来条，再多只占篇幅
_RECORDS_MAX = 80
_RECORD_TEXT_CHARS = 50
_INSTRUCTION_CHARS = 24
# 记录时刻允许比她那句晚一点：上报落库与台词落库各走各的，同一刻发生的两件事时刻会差几十秒
_LATE_SLACK_MS = 2 * 60 * 1000
# 里程碑、上报里写着没做成的：撑不了"做成了"，不列
_FAILED_WORDS = (
    "没有完成",
    "没完成",
    "未完成",
    "没做成",
    "没成功",
    "失败",
    "失效",
    "没交付",
    "没有开始",
    "没开始",
    "取消了",
    "停着没动",
    "放弃",
)
# 委派没交付的几种终态怎么写（列出来让模型知道这件事没做成，但不能作证）
_UNPROVEN_STATUS = {
    "undelivered": "停了没交付",
    "interrupted": "被重启打断，做到哪不确定",
    "cancelled": "撤掉了",
    "failed": "失败了",
    "timeout": "超时了",
}
_RECORD_REF = re.compile(r"(?:记录\s*)?#?\s*(\d+)")


@dataclass(frozen=True)
class GameRecord:
    """一条本场游戏记录：编号、哪个游戏、什么时候、什么记录、写的什么、能不能撑"做成了"。"""

    number: int
    game: str
    at_ms: int
    kind: str
    text: str
    proves: bool


def is_done_claim(text: str) -> bool:
    """这段话里有没有"游戏里做成了某件事"的说法；前面紧挨着否定字的（"还没烤完"）不算。"""
    for match in _DONE_CLAIM.finditer(text or ""):
        before = text[max(0, match.start() - 2) : match.start()]
        if any(char in before for char in _NEGATION_CHARS):
            continue
        return True
    return False


def _clip(text: str, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"


def game_records(events: Sequence[Any], delegations: Sequence[Any]) -> List[GameRecord]:
    """本场的里程碑、上报与了结了的委派 → 按时刻排好、编了号的游戏记录（只留最近 :data:`_RECORDS_MAX` 条）。

    委派只有交付了的（``succeeded``）能作证，其余终态照列、标明不能作证：模型看得到这件事没做成，
    就不会把她嘴上的"做好了"写成事实。还没了结的委派不列。里程碑与上报写着没做成的不列。
    """
    staged: List[tuple] = []
    for delegation in delegations:
        status = str(getattr(delegation, "status", "") or "")
        if status in ("", "accepted"):
            continue
        what = _clip(getattr(delegation, "instruction", "") or "（指令没记下）", _INSTRUCTION_CHARS)
        if status == "succeeded":
            summary = str(getattr(delegation, "summary", "") or "").strip()
            summary = summary.removeprefix("delivery:").strip()
            text = f"委派「{what}」交付了：{_clip(summary, _RECORD_TEXT_CHARS)}" if summary else f"委派「{what}」交付了"
        else:
            text = f"委派「{what}」{_UNPROVEN_STATUS.get(status, status)}，不能作证"
        at_ms = int(getattr(delegation, "ended_ms", 0) or 0)
        staged.append((at_ms, str(getattr(delegation, "game", "") or ""), "委派", text, status == "succeeded"))
    for event in events:
        kind = {"milestone": "里程碑", "report": "上报"}.get(str(getattr(event, "event_type", "") or ""))
        message = " ".join(str(getattr(event, "message", "") or "").split())
        if kind is None or not message or any(word in message for word in _FAILED_WORDS):
            continue
        at_ms = int(getattr(event, "timestamp_ms", 0) or 0)
        staged.append((at_ms, str(getattr(event, "game", "") or ""), kind, _clip(message, _RECORD_TEXT_CHARS), True))
    staged.sort(key=lambda item: item[0])
    return [
        GameRecord(number=index, game=game, at_ms=at_ms, kind=kind, text=text, proves=proves)
        for index, (at_ms, game, kind, text, proves) in enumerate(staged[-_RECORDS_MAX:], start=1)
    ]


def render_records(records: Sequence[GameRecord]) -> str:
    """给模型看的【游戏记录】一段；没有记录时为空串（整段省略）。"""
    if not records:
        return ""
    lines = [
        f"- 记录#{record.number} {datetime.fromtimestamp(max(0, record.at_ms) / 1000).strftime('%H:%M')}"
        f" {record.game} {record.kind}：{record.text}"
        for record in records
    ]
    return "【游戏记录（写游戏里做成了什么、了结时说做完了，都要引这里的编号）】\n" + "\n".join(lines)


def proof_for(ref: Any, records: Sequence[GameRecord], *, game: Optional[str], at_ms: int) -> Optional[GameRecord]:
    """模型引的那条记录撑不撑得住这句完成说法：存在、能作证、同一个游戏、不晚于她那句（容两分钟）；否则 None。"""
    match = _RECORD_REF.fullmatch(str(ref).strip()) if ref is not None else None
    if match is None:
        return None
    number = int(match.group(1))
    record = next((item for item in records if item.number == number), None)
    if record is None or not record.proves:
        return None
    if game and record.game and record.game.lower() != game.lower():
        return None
    if record.at_ms > at_ms + _LATE_SLACK_MS:
        return None
    return record
