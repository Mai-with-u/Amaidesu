"""下播整理核对"她说做好了"：本场游戏记录里撑得住"做成了"的那几条，以及引用的核对。

她嘴上说"门装好了"不等于游戏里真装好了：游戏那边没交付就停了、或者根本没去做，她照样会说做好了。
整理把这句写成档案里的事实，下一场她就带着一件没发生的事开播，越说越真。所以整理时把本场的游戏记录
编号列给模型——了结了的委派（只有交付了的能作证）、说明这一步做成了的里程碑与上报——档案里写游戏里
做成了什么、了结承诺时说做完了，都要引撑着它的记录（可以引几条，有一条撑得住就算）。模型引了哪条
说了不算，代码核对：引的那条存在、是做成了的、同一个游戏、不晚于她说那句的时候。没核实到的完成说法
照样写进档案，但写成"她说……（游戏记录里没核实到）"、不当事实：她顺手做成却没留下记录的事不会被
悄悄略掉，人审时也看得见；了结承诺不认没核实到的"做完了"，连同原因进整理报告。

记录给全本场的，不截最近几条：长场前半段的完成说法也引得到。条数多时把同一次执行里连续的几步
里程碑并成一行，免得撑爆输入。

本场一条游戏记录都没有时（记录没接上、旧数据、游戏那边一件事都没报），无从核对，不拦。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Sequence, Tuple

__all__ = [
    "UNVERIFIED_MARK",
    "GameRecord",
    "game_records",
    "is_done_claim",
    "mark_unverified",
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

# 记录超过这么多条时，同一次执行里连续的几步里程碑并成一行（第 54、58 场各四五十条，不用并）；
# 每步只留这么多字。不截条数：截掉的是长场前半段，那时说的"做好了"就引不到记录、被误判成没核实到
_FOLD_AFTER = 100
_STEP_CHARS = 30
_STEP_HEAD = re.compile(r"^第\s*(\d+)\s*步")
# 并成一行时去掉的工具名（"maicraft:obtain，"）与物品 ID 前缀，只留这一步做了什么
_STEP_NOISE = re.compile(r"maicraft:[a-z_]+，?|minecraft:")
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
# 委派没交付的几种终态怎么写（列出来让模型知道这件事没做成，但不能作证；主播单方结单而游戏那边没交付的也不算证据）
_UNPROVEN_STATUS = {
    "undelivered": "停了没交付",
    "closed_by_initiator": "她认定做完了，游戏那边没交付",
    "interrupted": "被重启打断，做到哪不确定",
    "cancelled": "撤掉了",
    "failed": "失败了",
    "timeout": "超时了",
}
_RECORD_REF = re.compile(r"(?:记录)?#?(\d+)")
# evidence 里几个编号之间的分隔（"记录#3、记录#5""#3 #5"），也可以是 JSON 列表
_REF_SPLIT = re.compile(r"[、,，;；/\s]+|和|与|及")
# 没核实到的完成说法写进档案时的标记：代码写的，整理报告按它数这一场有几条
UNVERIFIED_MARK = "（游戏记录里没核实到）"
# 档案标题里游戏名那一截（"Minecraft：……"）至多这么长，"她说"插在它后面
_GAME_HEAD_CHARS = 12


@dataclass(frozen=True)
class GameRecord:
    """一条本场游戏记录：编号、哪个游戏、什么时候、什么记录、写的什么、能不能撑"做成了"。

    几步并成一行时 ``at_ms`` 是第一步的时刻（核对"不晚于她那句"按它），``until_ms`` 是最后一步的；
    单条记录 ``until_ms`` 为 0。
    """

    number: int
    game: str
    at_ms: int
    kind: str
    text: str
    proves: bool
    until_ms: int = 0


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


def mark_unverified(title: str) -> str:
    """没核实到的完成说法写进档案的样子："Minecraft：她说门装好了（游戏记录里没核实到）"。

    标题以游戏名开头时"她说"插在游戏名后面，读起来还是那个游戏里的一件事。
    """
    head, sep, body = title.partition("：")
    if sep and body and len(head) <= _GAME_HEAD_CHARS:
        return f"{head}：她说{body}{UNVERIFIED_MARK}"
    return f"她说{title}{UNVERIFIED_MARK}"


def game_records(events: Sequence[Any], delegations: Sequence[Any]) -> List[GameRecord]:
    """本场的里程碑、上报与了结了的委派 → 按时刻排好、编了号的游戏记录（全本场，不截条数）。

    委派只有交付了的（``succeeded``）能作证，其余终态照列、标明不能作证：模型看得到这件事没做成，
    就不会把她嘴上的"做好了"写成事实。还没了结的委派不列。里程碑与上报写着没做成的不列。
    超过 :data:`_FOLD_AFTER` 条时，同一次执行里连续的几步里程碑并成一行。
    """
    staged: List[_Staged] = []
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
        staged.append(_Staged(at_ms, str(getattr(delegation, "game", "") or ""), "委派", text, status == "succeeded"))
    for event in events:
        kind = {"milestone": "里程碑", "report": "上报"}.get(str(getattr(event, "event_type", "") or ""))
        message = " ".join(str(getattr(event, "message", "") or "").split())
        if kind is None or not message or any(word in message for word in _FAILED_WORDS):
            continue
        at_ms = int(getattr(event, "timestamp_ms", 0) or 0)
        staged.append(_Staged(at_ms, str(getattr(event, "game", "") or ""), kind, message, True))
    staged.sort(key=lambda item: item.at_ms)
    if len(staged) > _FOLD_AFTER:
        staged = _fold_steps(staged)
    return [
        GameRecord(
            number=index,
            game=item.game,
            at_ms=item.at_ms,
            kind=item.kind,
            text=item.text if item.until_ms else _clip(item.text, _RECORD_TEXT_CHARS),
            proves=item.proves,
            until_ms=item.until_ms,
        )
        for index, item in enumerate(staged, start=1)
    ]


@dataclass(frozen=True)
class _Staged:
    """编号前的一条记录（并步时 ``until_ms`` 记最后一步的时刻）。"""

    at_ms: int
    game: str
    kind: str
    text: str
    proves: bool
    until_ms: int = 0


def _fold_steps(staged: Sequence[_Staged]) -> List[_Staged]:
    """同一次执行里连续的几步里程碑（"第 1 步""第 2 步"……中间没夹别的记录）并成一行。

    步号往上走就算同一次执行；又从小号开始、换了游戏、中间夹了上报或委派，就是另一次。
    并成的一行时刻取第一步（核对按它，宽一点：同一次执行几步之间只差几分钟），每步只留做了什么。
    """
    folded: List[_Staged] = []
    run: List[Tuple[int, _Staged]] = []

    def flush() -> None:
        if len(run) == 1:
            folded.append(run[0][1])
        elif run:
            steps = "；".join(_clip(_STEP_NOISE.sub("", item.text), _STEP_CHARS) for _, item in run)
            first, last = run[0][1], run[-1][1]
            folded.append(_Staged(first.at_ms, first.game, "里程碑", f"连续 {len(run)} 步：{steps}", True, last.at_ms))
        run.clear()

    for item in staged:
        step = _STEP_HEAD.match(item.text) if item.kind == "里程碑" else None
        if step is None:
            flush()
            folded.append(item)
            continue
        number = int(step.group(1))
        if run and (number <= run[-1][0] or item.game != run[-1][1].game):
            flush()
        run.append((number, item))
    flush()
    return folded


def _clock(at_ms: int) -> str:
    return datetime.fromtimestamp(max(0, at_ms) / 1000).strftime("%H:%M")


def render_records(records: Sequence[GameRecord]) -> str:
    """给模型看的【游戏记录】一段；没有记录时为空串（整段省略）。"""
    if not records:
        return ""
    lines = []
    for record in records:
        when = _clock(record.at_ms)
        if record.until_ms and _clock(record.until_ms) != when:
            when = f"{when}–{_clock(record.until_ms)}"
        lines.append(f"- 记录#{record.number} {when} {record.game} {record.kind}：{record.text}")
    return "【游戏记录（写游戏里做成了什么、了结时说做完了，都要引这里的编号）】\n" + "\n".join(lines)


def _record_numbers(ref: Any) -> List[int]:
    """evidence 里引的编号：一个（"记录#12"）、几个（"记录#12、记录#15"）或 JSON 列表；认不出的那截不算。"""
    if ref is None:
        return []
    parts = [str(item) for item in ref] if isinstance(ref, (list, tuple)) else _REF_SPLIT.split(str(ref))
    numbers: List[int] = []
    for part in parts:
        match = _RECORD_REF.fullmatch(part.strip())
        if match is not None:
            numbers.append(int(match.group(1)))
    return numbers


def proof_for(ref: Any, records: Sequence[GameRecord], *, game: Optional[str], at_ms: int) -> Optional[GameRecord]:
    """模型引的记录里有没有一条撑得住这句完成说法：存在、能作证、同一个游戏、不晚于她那句（容两分钟）。

    引了几条时有一条撑得住就算；都撑不住为 None。
    """
    by_number = {record.number: record for record in records}
    for number in _record_numbers(ref):
        record = by_number.get(number)
        if record is None or not record.proves:
            continue
        if game and record.game and record.game.lower() != game.lower():
            continue
        if record.at_ms > at_ms + _LATE_SLACK_MS:
            continue
        return record
    return None
