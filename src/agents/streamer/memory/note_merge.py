"""长期笔记的合并操作与按场次的生命周期（纯函数，无 I/O）。

合并不让模型重写整份笔记：模型只给出对编号条目的操作（新增、续上、改写、
兑现、放弃、挪节、删除），由这里逐条执行，没被操作到的行一字不变。指向人工
条目的操作不执行，只记进改动清单留给人看。

记下来的东西默认会淡出：一条笔记只有在观众或游戏里再次出现时才算"续上"
（主播自己又提了一遍不算——否则她越念叨记得越牢），连续若干场没有新证据就
挪进"搁置"一节，不再注入，以后有人再提还能挪回来。已兑现或放弃的约定留一场
让主播知道结果，之后也搁置。笔记总长超过上限时，先搁置最久没被提起的条目；
人写的条目从不被搁置。

条目格式与各种视图见 ``notes``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Collection, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import json_repair

from .notes import (
    ACTIVE_SECTIONS,
    ADDABLE_SECTIONS,
    SECTION_PROMISES,
    SECTION_SHELVED,
    SECTION_UNCERTAIN,
    STATUS_DONE,
    STATUS_DROPPED,
    STATUS_OPEN,
    NoteDocument,
    NoteItem,
    render_for_injection,
)

__all__ = ["NoteOp", "apply_lifecycle", "apply_ops", "parse_ops", "resolve_ops"]

SHELVE_STALE = "过期"
SHELVE_CLOSED = "已了结"
SHELVE_BUDGET = "篇幅"


# ---------------------------------------------------------------------------
# 合并操作
# ---------------------------------------------------------------------------

_OPS = ("add", "seen", "edit", "done", "drop", "move", "remove")


@dataclass
class NoteOp:
    """一条已对上条目的合并操作；``target`` 为被操作条目的身份（新增为 None）。"""

    op: str
    target: Optional[Tuple[str, str]] = None
    section: str = ""
    text: str = ""
    viewer_id: Optional[int] = None
    game: str = ""
    reason: str = ""


def parse_ops(content: str) -> Optional[List[Dict[str, Any]]]:
    """从模型输出里取出操作列表（容忍代码块包裹、前后多话与轻微格式错误）；读不出返回 None。

    认三种形状：``{"ops": [...]}``、操作数组、单个操作对象。
    """
    text = (content or "").strip()
    candidates = [text]
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            payload = json_repair.loads(candidate)
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("ops"), list):
            payload = payload["ops"]
        elif isinstance(payload, dict) and "op" in payload:
            payload = [payload]
        if isinstance(payload, list) and any(isinstance(op, dict) for op in payload):
            return [op for op in payload if isinstance(op, dict)]
        if isinstance(payload, (dict, list)) and not payload:
            return []
    return None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def resolve_ops(raw_ops: Iterable[Mapping[str, Any]], numbered: Sequence[NoteItem]) -> Tuple[List[NoteOp], List[str]]:
    """把模型给的编号操作对到条目身份上；编号不存在、操作不认识的记进说明后丢掉。"""
    resolved: List[NoteOp] = []
    rejected: List[str] = []
    for raw in raw_ops:
        name = str(raw.get("op", "")).strip().lower()
        if name not in _OPS:
            rejected.append(f"没采纳：不认识的操作 {name or '（空）'}")
            continue
        op = NoteOp(
            op=name,
            section=str(raw.get("section", "") or "").strip(),
            text=str(raw.get("text", "") or "").strip(),
            viewer_id=_as_int(raw.get("viewer_id")),
            game=str(raw.get("game", "") or "").strip(),
            reason=str(raw.get("reason", "") or "").strip(),
        )
        if name != "add":
            number = _as_int(raw.get("id"))
            if number is None or not 1 <= number <= len(numbered):
                rejected.append(f"没采纳：{name} 指向不存在的第 {raw.get('id')} 条")
                continue
            op.target = numbered[number - 1].key
        resolved.append(op)
    return resolved, rejected


def apply_ops(
    doc: NoteDocument,
    ops: Sequence[NoteOp],
    *,
    current: int,
    viewers: Mapping[int, str],
    games: Collection[str] = (),
) -> List[str]:
    """在笔记上逐条执行合并操作，返回改动说明（含没采纳的原因）。

    人工条目只读：指向它的操作不执行，只记一笔"模型提议"留给人看。新增给的观众
    编号不在本场观众名单里时去掉对象标签（宁可当成全场的事，也不挂错人）；新增与
    已有条目正文相同时按"续上"处理，不重复记。
    """
    changes: List[str] = []
    for op in ops:
        if op.op == "add":
            changes.extend(_add(doc, op, current=current, viewers=viewers, games=games))
            continue
        item = next((candidate for candidate in doc.items if candidate.key == op.target), None)
        if item is None:
            changes.append(f"没采纳：{op.op} 的目标已经不在笔记里（{op.target[1] if op.target else ''}）")
            continue
        if item.manual:
            changes.append(f"没采纳（人工条目只读）：模型想对「{item.text}」做 {op.op} {op.text or op.reason}".rstrip())
            continue
        changes.extend(_apply_one(doc, item, op, current=current))
    return changes


def _add(
    doc: NoteDocument, op: NoteOp, *, current: int, viewers: Mapping[int, str], games: Collection[str]
) -> List[str]:
    if op.section not in ADDABLE_SECTIONS or not op.text:
        return [f"没采纳：新增条目的节「{op.section}」不可用或正文为空"]
    duplicate = next((item for item in doc.items if item.key == (op.section, op.text)), None)
    if duplicate is not None:
        if not duplicate.manual:
            duplicate.seen = current
        return [f"续上（新增与已有条目相同）：{op.text}"]
    item = NoteItem(text=op.text, section=op.section, since=current, seen=current)
    if op.section == SECTION_PROMISES:
        item.status = STATUS_OPEN
    notes: List[str] = []
    if op.viewer_id is not None:
        if op.viewer_id in viewers:
            item.viewer_id, item.viewer_name = op.viewer_id, viewers[op.viewer_id]
            notes.append(f"对 {item.viewer_name}#{item.viewer_id}")
        else:
            notes.append(f"观众 #{op.viewer_id} 不在本场名单里，没挂对象")
    if op.game:
        if not games or op.game in games:
            item.game = op.game
            notes.append(f"游戏 {op.game}")
        else:
            notes.append(f"游戏 {op.game} 没接入，没挂游戏")
    doc.items.append(item)
    return [f"新增〔{op.section}〕{op.text}" + (f"（{'，'.join(notes)}）" if notes else "")]


def _apply_one(doc: NoteDocument, item: NoteItem, op: NoteOp, *, current: int) -> List[str]:
    if op.op == "seen":
        if item.section == SECTION_SHELVED:
            item.section = item.origin if item.origin in ADDABLE_SECTIONS else SECTION_UNCERTAIN
            item.origin, item.shelved_at, item.shelve_reason = "", None, ""
            item.seen = current
            return [f"从搁置挪回〔{item.section}〕：{item.text}"]
        item.seen = current
        return [f"续上：{item.text}"]
    if op.op == "edit":
        if not op.text:
            return [f"没采纳：改写「{item.text}」没给新正文"]
        old = item.text
        item.text, item.seen = op.text, current
        return [f"改写：{old} → {op.text}"]
    if op.op in ("done", "drop"):
        if item.section != SECTION_PROMISES:
            return [f"没采纳：只有约定能标兑现或放弃（「{item.text}」在〔{item.section}〕）"]
        item.status, item.seen = (STATUS_DONE if op.op == "done" else STATUS_DROPPED), current
        return [f"标为{item.status}：{item.text}" + (f"（{op.reason}）" if op.reason else "")]
    if op.op == "move":
        if op.section not in ADDABLE_SECTIONS or op.section == item.section:
            return [f"没采纳：不能把「{item.text}」挪到〔{op.section}〕"]
        old_section = item.section
        item.section, item.seen = op.section, current
        if op.section == SECTION_PROMISES and not item.status:
            item.status = STATUS_OPEN
        if op.section != SECTION_PROMISES:
            item.status = ""
        return [f"挪节：〔{old_section}〕→〔{op.section}〕{item.text}"]
    doc.items.remove(item)
    return [f"删除：{item.text}" + (f"（{op.reason}）" if op.reason else "")]


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


def apply_lifecycle(
    doc: NoteDocument,
    *,
    current: int,
    settled_ids: Sequence[int],
    stale_sessions: int,
    max_chars: int,
    shelved_keep: int = 30,
) -> List[str]:
    """收播合并之后跑一遍：该淡出的挪进搁置、超篇幅的按最久没提起的先搁置，返回改动说明。

    "隔了几场"只数同类型、写过回顾的场次（``settled_ids``，含本场）。旧格式读进来、
    还没有"最近"标签的条目从本场起算。人工条目不参与。
    """
    changes: List[str] = []
    later = sorted(int(sid) for sid in settled_ids)

    def gap(item: NoteItem) -> int:
        seen = item.seen if item.seen is not None else current
        return sum(1 for sid in later if sid > seen)

    for item in list(doc.items):
        if item.manual or item.section == SECTION_SHELVED:
            continue
        if item.seen is None:
            item.seen = current
        if item.section == SECTION_PROMISES and item.status in (STATUS_DONE, STATUS_DROPPED):
            if gap(item) >= 1:
                changes.append(_shelve(doc, item, current, SHELVE_CLOSED))
        elif gap(item) >= max(1, stale_sessions):
            changes.append(_shelve(doc, item, current, SHELVE_STALE))

    while len(render_for_injection(doc, include_viewer_items=True)) > max_chars:
        candidates = [item for item in doc.items if not item.manual and item.section in ACTIVE_SECTIONS]
        if not candidates:
            changes.append("注意：人工条目本身已超出篇幅上限，没有可搁置的条目")
            break
        oldest = min(candidates, key=lambda item: item.seen if item.seen is not None else current)
        changes.append(_shelve(doc, oldest, current, SHELVE_BUDGET))

    shelved = doc.section_items(SECTION_SHELVED)
    if len(shelved) > shelved_keep:
        shelved.sort(key=lambda item: item.shelved_at or 0)
        for item in shelved[: len(shelved) - shelved_keep]:
            doc.items.remove(item)
            changes.append(f"清出搁置区：{item.text}")
    return changes


def _shelve(doc: NoteDocument, item: NoteItem, current: int, reason: str) -> str:
    """挪进搁置区（排到搁置区末尾，搁置区按搁置先后排），记下原来在哪一节。"""
    origin = item.section
    item.origin, item.section = origin, SECTION_SHELVED
    item.shelved_at, item.shelve_reason = current, reason
    doc.items.remove(item)
    doc.items.append(item)
    return f"搁置（{reason}）：〔{origin}〕{item.text}"
