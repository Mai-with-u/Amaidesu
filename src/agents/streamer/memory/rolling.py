"""滚动档案快照：整理一场时，模型对上一份快照逐行挑"留 / 去掉 / 替换"，代码凑成本场快照。

常驻简报只读一份快照。快照只引用原来的档案行：模型只挑、不改写，留下的行标题、原话、证据台词、
出自哪一场、存档一个字节都不变——不让一件事经一场又一场的转写慢慢走样。

1. 上一份快照（:meth:`ArchiveRepo.snapshot_before`）编上"旧N"交给模型；
2. 模型对旧行给出操作（没列出的行一律保留）：
   - 保留：可以注明本场哪一行又提到它（算"再提起"）、重标是否稳定的事；
   - 去掉：理由要写明是了结、不成立、记错还是被取代，并指出本场哪一行台词是证据（代码核对
     这一行确实是本场播出的台词）；"这场没提到""一次性的事"都不算理由；同一件事在上一份
     里写了两行时，可以注明与哪一行重复（``same_as``）去掉其中一行：事实由留下的那一行接着；
     那一行本场被替换或去掉了，这一行跟着它走（替换出的新行接着这件事，去掉用的是同一份证据）；
   - 替换：给本场一行台词里的逐字原话，过与新事同一个校验器，通过才换掉旧行；
   不合规的操作不采纳、旧行照留，连同理由写进整理报告——既没被合格地去掉、也没被替换的旧行
   一律留着，不靠提示词让模型自觉"宁多勿少"；
3. 代码按固定规则取舍（:func:`select_snapshot`），至多 :data:`MAX_SNAPSHOT_LINES` 行：
   稳定的事先占至多 :data:`MAX_LASTING_LINES` 行（本场新写的在前，其余按最近被提起）→ 本场
   其余新事 → 其余稳定的事 → 其余旧行（按最近被提起）。同一档里挤出去的是最久没被再提的。
   模型不凭感觉删。

模型输出里没有 ``previous``（或整个输出为空）时上一份原样保留；``previous`` 不是列表按整理
失败处理，上一份快照同样原样留着。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from src.modules.logging import get_logger
from src.modules.storage.models.chat import SettleSpeechLine
from src.modules.storage.models.memory import (
    ArchiveSnapshot,
    KeptSnapshotLine,
    PendingArchiveLine,
    SnapshotChange,
    SnapshotLine,
    UnregisteredItem,
)

from .render import MAX_BRIEF_ARCHIVE_LINES

__all__ = [
    "MAX_BASE_LINES",
    "MAX_LASTING_LINES",
    "MAX_SNAPSHOT_LINES",
    "RollingPlan",
    "SnapshotSelection",
    "plan_from_ops",
    "render_base",
    "select_snapshot",
]

logger = get_logger("RollingArchive")

# 一份快照至多几行：整理写入与简报列出用同一个数
MAX_SNAPSHOT_LINES = MAX_BRIEF_ARCHIVE_LINES
# 稳定的事至多先占几行：名字、住处、关系不被新事和前几场的零碎进展挤掉，又给本场新事留出位置
MAX_LASTING_LINES = 8
# 交给模型的旧行至多几行（升级后第一次滚动会连着几场旧档案一起给）
MAX_BASE_LINES = 24
# 旧行编号前缀：与台词编号（#N）、承诺编号（约#N）分开，模型不会把它们混成一类
_OLD_PREFIX = "旧"
# 证据台词进整理报告时的截断长度
_EVIDENCE_CHARS = 80
# 去掉旧行时不算理由的说法："这场没提到"不能把前几场的事清掉
_NOT_A_REASON = ("没提", "未提", "没有提", "没再提")
# 去掉旧行只认这四类理由：做完了、不成立了、当初记错了、被本场的新事取代了
_DROP_KINDS = ("了结", "不成立", "记错", "被取代")

# 模型给错操作时写进整理报告的固定措辞（旧行一律照留）
_REFUSED_UNKNOWN_NUMBER = "旧行编号不存在"
_REFUSED_REPEATED = "同一行给了不止一个操作，只认第一个"
_REFUSED_UNKNOWN_OP = "看不懂的操作，旧行保留"
_REFUSED_DROP_EVIDENCE = "去掉旧行要写理由并指出本场哪一行台词作证据，旧行保留"
_REFUSED_NOT_MENTIONED = "这场没提到不算去掉的理由，旧行保留"
_REFUSED_DROP_KIND = "去掉的理由要写明是了结、不成立、记错还是被取代，旧行保留"
_REFUSED_REPLACE = "替换的新行没通过校验，旧行保留"
_REFUSED_SAME_AS = "注明重复的那一行编号不对，旧行保留"
_EVICTED_OLD = f"超过 {MAX_SNAPSHOT_LINES} 行，最久没被再提的先出"
_EVICTED_NEW = "本场新事超出上限：写成本场档案行，不进快照"


def render_base(snapshot: Optional[ArchiveSnapshot]) -> str:
    """上一份快照渲染成给模型看的旧行清单；没有时为空串。

    每行：编号、出自上一场还是之前、标题、原话，挂着游戏的写上游戏与存档，稳定的事标出来。
    "上一场"是这份快照所属的那一场，其余行出自更早的场次。
    """
    if snapshot is None:
        return ""
    rows: List[str] = []
    for index, item in enumerate(snapshot.lines[:MAX_BASE_LINES], start=1):
        line = item.line
        when = "上一场" if int(line.live_session_id) == int(snapshot.owner_session_id) else "之前"
        notes = [f"原话「{line.quote}」"]
        if line.game:
            save = line.save_name or (line.save_id[:8] if line.save_id else "")
            notes.append(f"在 {line.game}「{save}」" if save else f"在 {line.game}")
        if item.lasting:
            notes.append("稳定")
        rows.append(f"{_OLD_PREFIX}{index} [{when}] {line.text}（{'；'.join(notes)}）")
    return "\n".join(rows)


def _as_old_number(raw: Any) -> Optional[int]:
    """旧行编号：接受 2、"2"、"旧2"；其余返回 None。"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw or "").strip()
    if text.startswith(_OLD_PREFIX):
        text = text[len(_OLD_PREFIX) :]
    return int(text) if text.isdigit() else None


@dataclass(frozen=True)
class RollingPlan:
    """模型的操作落地后的结果：留下的旧行、替换出的新行、改动与没登记的条目。"""

    kept: List[SnapshotLine] = field(default_factory=list)
    replacements: List[PendingArchiveLine] = field(default_factory=list)
    changes: List[SnapshotChange] = field(default_factory=list)
    misses: List[UnregisteredItem] = field(default_factory=list)


def _evidence(line: SettleSpeechLine) -> str:
    """作证据的那句台词原文（截断）。"""
    return " ".join((line.content or "").split())[:_EVIDENCE_CHARS]


def plan_from_ops(
    raw_ops: Any,
    base: Sequence[SnapshotLine],
    *,
    session_started_at_ms: int,
    line_of: Callable[[Any], Optional[SettleSpeechLine]],
    validate_new: Callable[[Dict[str, Any]], Tuple[List[PendingArchiveLine], List[UnregisteredItem]]],
) -> RollingPlan:
    """把模型对旧行的操作落成计划；``raw_ops`` 不是列表抛 ValueError（整理按失败处理）。

    ``raw_ops`` 为 None（输出里没有这一项）时全部保留。``line_of`` 把模型给的行号换成本场台词，
    ``validate_new`` 是新事的校验器（替换出的新行与新事同一个口径）。
    """
    shown = list(base)[:MAX_BASE_LINES]
    if raw_ops is None:
        return RollingPlan(kept=shown)
    if not isinstance(raw_ops, list):
        raise ValueError("模型输出的 previous 不是列表")
    updated: Dict[int, SnapshotLine] = {}
    removed: Set[int] = set()
    handled: Set[int] = set()
    # (要去掉的行, 与它重复的行, 理由)：等别的操作都落地后再定，才知道那一行是留着还是本场被换掉了
    duplicates: List[Tuple[int, int, str]] = []
    replacements: List[PendingArchiveLine] = []
    changes: List[SnapshotChange] = []
    misses: List[UnregisteredItem] = []
    for raw in raw_ops:
        if not isinstance(raw, dict):
            continue
        number = _as_old_number(raw.get("old"))
        if number is None or not 1 <= number <= len(shown):
            changes.append(
                SnapshotChange(action="refused", what=f"{_OLD_PREFIX}{raw.get('old')}", reason=_REFUSED_UNKNOWN_NUMBER)
            )
            continue
        index = number - 1
        item = shown[index]
        title = item.line.text
        if index in handled:
            changes.append(SnapshotChange(action="refused", what=title, reason=_REFUSED_REPEATED))
            continue
        handled.add(index)
        op = str(raw.get("op") or "keep").strip().lower()
        evidence = line_of(raw.get("line"))
        if op == "keep":
            # 保留：本场哪一行又提到它就算再提起；重标是否稳定的事
            lasting = raw.get("lasting")
            updated[index] = SnapshotLine(
                line=item.line,
                lasting=lasting if isinstance(lasting, bool) else item.lasting,
                seen_at_ms=max(item.seen_at_ms, int(session_started_at_ms))
                if evidence is not None
                else item.seen_at_ms,
            )
        elif op == "drop" and raw.get("same_as") is not None:
            # 与另一行重复：事实由那一行留着，不需要本场证据
            other = _as_old_number(raw.get("same_as"))
            if other is None or not 1 <= other <= len(shown) or other == number:
                changes.append(SnapshotChange(action="refused", what=title, reason=_REFUSED_SAME_AS))
            else:
                reason = str(raw.get("reason") or "").strip() or f"与{_OLD_PREFIX}{other}重复"
                duplicates.append((index, other - 1, reason))
        elif op == "drop":
            # 去掉：要有本场台词作证据、理由属于四类之一；"这场没提到""一次性的事"一律不采纳，旧行照留
            reason = str(raw.get("reason") or "").strip()
            if not reason or evidence is None:
                changes.append(SnapshotChange(action="refused", what=title, reason=_REFUSED_DROP_EVIDENCE))
            elif any(marker in reason for marker in _NOT_A_REASON):
                changes.append(SnapshotChange(action="refused", what=title, reason=_REFUSED_NOT_MENTIONED))
            elif not any(kind in reason for kind in _DROP_KINDS):
                changes.append(SnapshotChange(action="refused", what=title, reason=_REFUSED_DROP_KIND))
            else:
                removed.add(index)
                changes.append(SnapshotChange(action="drop", what=title, reason=reason, quote=_evidence(evidence)))
        elif op == "replace":
            # 替换：新行带本场原话、过新事校验器，通过才换掉旧行
            written, missed = validate_new(raw)
            misses.extend(missed)
            if written:
                removed.add(index)
                replacements.append(written[0])
                changes.append(
                    SnapshotChange(
                        action="replace", what=title, reason=f"换成：{written[0].text}", quote=written[0].quote
                    )
                )
            else:
                changes.append(SnapshotChange(action="refused", what=title, reason=_REFUSED_REPLACE))
        else:
            changes.append(SnapshotChange(action="refused", what=title, reason=_REFUSED_UNKNOWN_OP))
    for index, other, reason in duplicates:
        if index in removed:
            continue
        removed.add(index)
        if other in removed:
            # 重复的那一行本场被替换或去掉了：这件事已由替换出的新行接着，或已由同一份证据了结
            reason = f"{reason}（{_OLD_PREFIX}{other + 1}本场已被替换或去掉）"
        else:
            # 重复的两行里有一行标了稳定，留下的那一行也算稳定
            survivor = updated.get(other, shown[other])
            if updated.get(index, shown[index]).lasting and not survivor.lasting:
                updated[other] = survivor.model_copy(update={"lasting": True})
        changes.append(SnapshotChange(action="drop", what=shown[index].line.text, reason=reason))
    kept = [updated.get(index, item) for index, item in enumerate(shown) if index not in removed]
    return RollingPlan(kept=kept, replacements=replacements, changes=changes, misses=misses)


@dataclass(frozen=True)
class SnapshotSelection:
    """取舍结果：本场要写的档案行（带是否进快照）、接住的旧行引用、被挤出的行。"""

    lines: List[PendingArchiveLine] = field(default_factory=list)
    kept: List[KeptSnapshotLine] = field(default_factory=list)
    evicted: List[SnapshotChange] = field(default_factory=list)


@dataclass(frozen=True)
class _Entry:
    """参与取舍的一行：本场新写的（new）或接住的（kept），连同它在各自列表里的下标。"""

    new: bool
    index: int
    lasting: bool
    seen_at_ms: int
    order: int


def select_snapshot(
    new_lines: Sequence[PendingArchiveLine],
    kept: Sequence[SnapshotLine],
    *,
    session_started_at_ms: int,
) -> SnapshotSelection:
    """按固定规则凑本场快照，至多 12 行，按下面四档依次放：

    1. 稳定的事，至多 8 行：本场新写的在前，其余按最近被提起；
    2. 本场其余新事（模型给出的先后）；
    3. 其余稳定的事，按最近被提起；
    4. 其余旧行，按最近被提起。

    同样新的按给出的先后（新事在前、接住的按原位置）。前几场的零碎进展因此排在稳定的事后面，
    同一档里挤出去的是最久没被再提的。被挤出的本场新事照样写成本场档案行（回想工具查得到），
    只是不进快照。
    """
    entries = [
        _Entry(new=True, index=index, lasting=line.lasting, seen_at_ms=int(session_started_at_ms), order=index)
        for index, line in enumerate(new_lines)
    ] + [
        _Entry(new=False, index=index, lasting=item.lasting, seen_at_ms=item.seen_at_ms, order=len(new_lines) + index)
        for index, item in enumerate(kept)
    ]
    by_recency = sorted(entries, key=lambda e: (not e.new, -e.seen_at_ms, e.order))
    lasting = [entry for entry in by_recency if entry.lasting]
    ranked = [
        *lasting[:MAX_LASTING_LINES],
        *(entry for entry in by_recency if entry.new and not entry.lasting),
        *lasting[MAX_LASTING_LINES:],
        *(entry for entry in by_recency if not entry.new and not entry.lasting),
    ]
    chosen = ranked[:MAX_SNAPSHOT_LINES]
    chosen_new = {entry.index for entry in chosen if entry.new}
    chosen_kept = {entry.index for entry in chosen if not entry.new}
    lines = [line.model_copy(update={"in_snapshot": index in chosen_new}) for index, line in enumerate(new_lines)]
    kept_refs = [
        KeptSnapshotLine(archive_line_id=int(item.line.id), lasting=item.lasting, seen_at_ms=item.seen_at_ms)
        for index, item in enumerate(kept)
        if index in chosen_kept
    ]
    evicted = [
        SnapshotChange(action="evicted", what=line.text, reason=_EVICTED_NEW)
        for index, line in enumerate(new_lines)
        if index not in chosen_new
    ] + [
        SnapshotChange(action="evicted", what=item.line.text, reason=_EVICTED_OLD)
        for index, item in enumerate(kept)
        if index not in chosen_kept
    ]
    if evicted:
        logger.info(f"档案快照超过 {MAX_SNAPSHOT_LINES} 行，挤出 {len(evicted)} 行（最久没被再提的先出）")
    return SnapshotSelection(lines=lines, kept=kept_refs, evicted=evicted)
