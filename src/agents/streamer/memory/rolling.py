"""滚动档案快照：整理一场时，模型对上一份快照逐行挑"留 / 去掉 / 替换"，代码凑成本场快照。

常驻简报只读一份快照。快照只引用原来的档案行：模型只挑、不改写，留下的行标题、原话、证据台词、
出自哪一场、存档一个字节都不变——不让一件事经一场又一场的转写慢慢走样。

1. 上一份快照（:meth:`ArchiveRepo.snapshot_before`）编上"旧N"交给模型；
2. 模型对旧行给出操作（没列出的行一律保留）：
   - 保留：可以注明本场哪一行又提到它（算"再提起"）、重标是否稳定的事；
   - 去掉：理由要以了结、不成立、记错、被取代四类之一开头，并指出本场哪一行台词是证据（代码核对
     这一行确实是本场播出的台词）；"这场没提到""一次性的事"都不算理由；同一件事在上一份
     里写了两行时，可以注明与哪一行重复（``same_as``）去掉其中一行：事实由留下的那一行接着；
     那一行本场被替换或带证据去掉了，这一行跟着它走（替换出的新行接着这件事，去掉用的是同一份
     证据）；那一行自己也是按重复去掉的（互指、绕成一圈），这一条不采纳，免得几行一起没了；
   - 替换：给本场一行台词里的逐字原话，过与新事同一个校验器，通过才换掉旧行；被换掉的是稳定的
     事，新行也算稳定（模型明确写了不稳定的除外）；
   不合规的操作不采纳、旧行照留，连同理由写进整理报告——既没被合格地去掉、也没被替换的旧行
   一律留着，不靠提示词让模型自觉"宁多勿少"；
3. 代码按固定规则取舍（:func:`select_snapshot`），至多 :data:`MAX_SNAPSHOT_LINES` 行：
   运营者放回或新增的行（pinned）排第 0 档、一行不挤 → 稳定的事先占至多 :data:`MAX_LASTING_LINES`
   行（本场新写的在前，其余按最近被提起）→ 本场其余新事 → 其余稳定的事 → 其余旧行（按最近被
   提起）。同一档里挤出去的是最久没被再提的。模型不凭感觉删。

人改过的行（``human_locked``）只有人能动：模型对它的去掉、替换、按重复去掉一律不采纳，记进整理
报告；它是不是稳定的事也按人标的算，模型重标不算，别的行按重复去掉、指向它时也不替它改成稳定的事。
只有"最近被提起"跟着本场台词证据更新：那是代码按播出的台词记的，不是人标的，也让它少被挤出去。

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

from .saves import canonical_save_names
from .zones import (
    MAX_RECENT_EARLIER_LINES,
    MAX_RECENT_PREVIOUS_LINES,
    MAX_ROOM_LINES,
    MAX_WORLD_LINES,
    MAX_WORLD_NAMES,
    is_naming_title,
    name_table_row_count,
)

__all__ = [
    "MAX_BASE_LINES",
    "MAX_LASTING_LINES",
    "MAX_ROOM_LASTING_QUOTA",
    "MAX_SNAPSHOT_LINES",
    "RollingPlan",
    "SnapshotSelection",
    "plan_from_ops",
    "render_base",
    "select_snapshot",
]

logger = get_logger("RollingArchive")

# 单存档或跨存档快照的总安全上限（各分区独立计额后再以此兜底，防止存档极多时快照无限膨胀）
MAX_SNAPSHOT_LINES = 40
# 单个游戏世界里稳定事实的展示行名额（名字表占 0～2 行，其余稳定占剩余名额，合计 ≤ 12）
MAX_LASTING_LINES = MAX_WORLD_LINES
# 不挂游戏的直播间稳定事实名额（与直播间普通事件 MAX_ROOM_LINES 分开计，互不挤占）
MAX_ROOM_LASTING_QUOTA = 6
# 交给模型的旧行至多几行（大于等于 MAX_SNAPSHOT_LINES，整份快照都能给模型核对；超出的尾部原样保留）
MAX_BASE_LINES = 48
# 旧行编号前缀：与台词编号（#N）、承诺编号（约#N）分开，模型不会把它们混成一类
_OLD_PREFIX = "旧"
# 证据台词进整理报告时的截断长度
_EVIDENCE_CHARS = 80
# 去掉旧行时不算理由的说法："这场没提到"不能把前几场的事清掉
_NOT_A_REASON = ("没提", "未提", "没有提", "没再提")
# 去掉旧行只认这四类理由：做完了、不成立了、当初记错了、被本场的新事取代了。理由必须以其中一类开头，
# 只查包含的话"还没了结"这种恰恰相反的说法也能混过去
_DROP_KINDS = ("了结", "不成立", "记错", "被取代")

# 模型给错操作时写进整理报告的固定措辞（旧行一律照留）
_REFUSED_UNKNOWN_NUMBER = "旧行编号不存在"
_REFUSED_REPEATED = "同一行给了不止一个操作，只认第一个"
_REFUSED_UNKNOWN_OP = "看不懂的操作，旧行保留"
_REFUSED_DROP_EVIDENCE = "去掉旧行要写理由并指出本场哪一行台词作证据，旧行保留"
_REFUSED_NOT_MENTIONED = "这场没提到不算去掉的理由，旧行保留"
_REFUSED_DROP_KIND = "去掉的理由要以了结、不成立、记错或被取代开头，旧行保留"
_REFUSED_HUMAN_LOCKED = "人改过的行只有人能动"
_REFUSED_REPLACE = "替换的新行没通过校验，旧行保留"
_REFUSED_SAME_AS = "注明重复的那一行编号不对，旧行保留"
_REFUSED_SAME_AS_GONE = "注明重复的那一行也按重复去掉了，旧行保留"
_EVICTED_OLD = "超出本分区快照名额，最久没被再提的先出"
_EVICTED_NEW = "本场新事超出本分区名额：写成本场档案行，不进快照"


def render_base(snapshot: Optional[ArchiveSnapshot]) -> str:
    """上一份快照渲染成给模型看的旧行清单；没有时为空串。

    每行：编号、出自上一场还是之前、标题、原话，挂着游戏的写上游戏与存档，稳定的事标出来。
    "上一场"是这份快照所属的那一场，其余行出自更早的场次。同一存档号出现过多个名字时按存档号归并成
    最后一次非空名字，免得整理模型把改过名的同一个世界当成两个存档。
    """
    if snapshot is None:
        return ""
    shown = snapshot.lines[:MAX_BASE_LINES]
    ordered_lines = sorted((item.line for item in shown), key=lambda row: (int(row.live_session_id), int(row.id)))
    save_names = canonical_save_names((row.game, row.save_id, row.save_name) for row in ordered_lines)
    rows: List[str] = []
    for index, item in enumerate(shown, start=1):
        line = item.line
        when = "上一场" if int(line.live_session_id) == int(snapshot.owner_session_id) else "之前"
        notes = [f"原话「{line.quote}」"]
        if line.game:
            resolved_name = save_names.get((line.game, line.save_id), line.save_name)
            save = resolved_name or (line.save_id[:8] if line.save_id else "")
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
    unshown_tail = list(base)[MAX_BASE_LINES:]
    if raw_ops is None:
        return RollingPlan(kept=[*shown, *unshown_tail])
    if not isinstance(raw_ops, list):
        raise ValueError("模型输出的 previous 不是列表")
    updated: Dict[int, SnapshotLine] = {}
    removed: Set[int] = set()
    # 本场被替换或带证据去掉的行：按重复去掉的行只能"跟着"这些行走，不能跟着另一条重复去掉的行
    settled: Set[int] = set()
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
        locked = bool(item.line.human_locked)
        if op in ("drop", "replace") and locked:
            # 人改过的行只有人能动：去掉、替换、按重复去掉都不采纳，旧行照留
            changes.append(SnapshotChange(action="refused", what=title, reason=_REFUSED_HUMAN_LOCKED))
            continue
        if op == "keep":
            # 保留：本场哪一行又提到它就算再提起；重标是否稳定的事（人改过的行按人标的算）
            lasting = raw.get("lasting")
            updated[index] = SnapshotLine(
                line=item.line,
                lasting=lasting if isinstance(lasting, bool) and not locked else item.lasting,
                seen_at_ms=max(item.seen_at_ms, int(session_started_at_ms))
                if evidence is not None
                else item.seen_at_ms,
                pinned=item.pinned,
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
            elif not reason.startswith(_DROP_KINDS):
                changes.append(SnapshotChange(action="refused", what=title, reason=_REFUSED_DROP_KIND))
            else:
                removed.add(index)
                settled.add(index)
                changes.append(SnapshotChange(action="drop", what=title, reason=reason, quote=_evidence(evidence)))
        elif op == "replace":
            # 替换：新行带本场原话、过新事校验器，通过才换掉旧行
            written, missed = validate_new(raw)
            misses.extend(missed)
            if written:
                removed.add(index)
                settled.add(index)
                replacement = written[0]
                # 被换掉的是稳定的事（名字、住处换了说法仍是稳定的事），新行也算稳定；模型明确写了不稳定的照它
                if item.lasting and raw.get("lasting") is not False and not replacement.lasting:
                    replacement = replacement.model_copy(update={"lasting": True})
                replacements.append(replacement)
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
        if other in removed and other not in settled:
            # 那一行自己也是按重复去掉的（互指或绕成一圈）：再跟着去掉，几行会一起没了，这一行留着
            changes.append(SnapshotChange(action="refused", what=shown[index].line.text, reason=_REFUSED_SAME_AS_GONE))
            continue
        removed.add(index)
        if other in settled:
            # 重复的那一行本场被替换或带证据去掉了：这件事已由替换出的新行接着，或已由同一份证据了结
            reason = f"{reason}（{_OLD_PREFIX}{other + 1}本场已被替换或去掉）"
        else:
            # 重复的两行里有一行标了稳定，留下的那一行也算稳定；留下的那一行是人改过的，
            # 是不是稳定的事按人标的算，模型借"按重复去掉"也改不了它
            survivor = updated.get(other, shown[other])
            if updated.get(index, shown[index]).lasting and not survivor.lasting and not survivor.line.human_locked:
                updated[other] = survivor.model_copy(update={"lasting": True})
        changes.append(SnapshotChange(action="drop", what=shown[index].line.text, reason=reason))
    kept = [*(updated.get(index, item) for index, item in enumerate(shown) if index not in removed), *unshown_tail]
    return RollingPlan(kept=kept, replacements=replacements, changes=changes, misses=misses)


@dataclass(frozen=True)
class SnapshotSelection:
    """取舍结果：本场要写的档案行（带是否进快照）、接住的旧行引用、被挤出的行。"""

    lines: List[PendingArchiveLine] = field(default_factory=list)
    kept: List[KeptSnapshotLine] = field(default_factory=list)
    evicted: List[SnapshotChange] = field(default_factory=list)


@dataclass(frozen=True)
class _Entry:
    """参与取舍的一行：本场新写的（new）或接住的（kept），连同所属游戏存档与是否名字类。"""

    new: bool
    index: int
    lasting: bool
    seen_at_ms: int
    order: int
    game: Optional[str] = None
    save_id: str = ""
    is_name: bool = False
    pinned: bool = False


def select_snapshot(
    new_lines: Sequence[PendingArchiveLine],
    kept: Sequence[SnapshotLine],
    *,
    session_started_at_ms: int,
) -> SnapshotSelection:
    """按分区各自的名额凑本场快照，让稳定取名、最近事件与直播间记忆互不抢位置：

    0. 运营者放回或新增的行（pinned）：排最前、一行不挤，且不占下面各区的名额；
    1. 游戏世界稳定的事（按 ``(game, save_id)`` 各自计额）：名字类至多 12 条（渲染成 1～2 行名字表），
       其余稳定（住处、关系、规矩）至多 ``12 - 名字表行数`` 条，本场新写在前、其余按最近被提起；
    2. 游戏世界普通事件：本场新写的至多 6 行（按模型给出的先后），前几场接住的至多 6 行（按最近被提起）；
    3. 直播间里的事（不挂游戏的行）：稳定的事至多 6 行，普通事件至多 6 行（本场新写在前、其余按最近被提起）。

    多存档累计超过总安全上限 ``MAX_SNAPSHOT_LINES`` 时，再按"稳定优先 → 本场新事 → 最近被提起"兜底裁剪。
    被挤出的本场新事照样写成本场档案行（回想工具查得到），只是不进快照。
    """
    entries = [
        _Entry(
            new=True,
            index=index,
            lasting=line.lasting,
            seen_at_ms=int(session_started_at_ms),
            order=index,
            game=line.game,
            save_id=line.save_id or "",
            is_name=bool(line.game and line.lasting and is_naming_title(line.text)),
        )
        for index, line in enumerate(new_lines)
    ] + [
        _Entry(
            new=False,
            index=index,
            lasting=item.lasting,
            seen_at_ms=item.seen_at_ms,
            order=len(new_lines) + index,
            game=item.line.game,
            save_id=item.line.save_id or "",
            is_name=bool(item.line.game and item.lasting and is_naming_title(item.line.text)),
            pinned=item.pinned,
        )
        for index, item in enumerate(kept)
    ]
    by_recency = sorted(entries, key=lambda e: (not e.new, -e.seen_at_ms, e.order))
    pinned = [entry for entry in by_recency if entry.pinned]
    unpinned = [entry for entry in by_recency if not entry.pinned]

    bucketed: List[_Entry] = []
    # 1. 游戏世界稳定的事：按 (game, save_id) 分世界，名字类与其余稳定各自计额，取名再多也不挤掉住处和事件
    world_keys = list(dict.fromkeys((e.game, e.save_id) for e in unpinned if e.game and e.lasting))
    for w_game, w_save in world_keys:
        world_lasting = [e for e in unpinned if e.game == w_game and e.save_id == w_save and e.lasting]
        names = [e for e in world_lasting if e.is_name][:MAX_WORLD_NAMES]
        other_cap = max(0, MAX_LASTING_LINES - name_table_row_count(len(names)))
        others = [e for e in world_lasting if not e.is_name][:other_cap]
        bucketed.extend(names)
        bucketed.extend(others)

    # 2. 游戏世界普通事件：本场新事至多 6 行（保模型先后），前几场接住的至多 6 行（按最近被提起）
    game_new_events = sorted(
        (e for e in unpinned if e.game and not e.lasting and e.new),
        key=lambda e: e.order,
    )[:MAX_RECENT_PREVIOUS_LINES]
    game_kept_events = [e for e in unpinned if e.game and not e.lasting and not e.new][:MAX_RECENT_EARLIER_LINES]
    bucketed.extend(game_new_events)
    bucketed.extend(game_kept_events)

    # 3. 直播间里的事（不挂游戏）：稳定与普通事件分开计额，换任何游戏存档都不丢
    room_lasting = [e for e in unpinned if not e.game and e.lasting][:MAX_ROOM_LASTING_QUOTA]
    room_events = [e for e in unpinned if not e.game and not e.lasting][:MAX_ROOM_LINES]
    bucketed.extend(room_lasting)
    bucketed.extend(room_events)

    # 多存档总行数超过安全上限时，稳定优先、本场新事其次、再按最近被提起兜底裁剪
    safety_ranked = sorted(bucketed, key=lambda e: (not e.lasting, not e.new, -e.seen_at_ms, e.order))
    chosen = pinned + safety_ranked[: max(0, MAX_SNAPSHOT_LINES - len(pinned))]
    chosen_new = {entry.index for entry in chosen if entry.new}
    chosen_kept = {entry.index for entry in chosen if not entry.new}
    lines = [line.model_copy(update={"in_snapshot": index in chosen_new}) for index, line in enumerate(new_lines)]
    kept_refs = [
        KeptSnapshotLine(
            archive_line_id=int(item.line.id), lasting=item.lasting, seen_at_ms=item.seen_at_ms, pinned=item.pinned
        )
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
        logger.info(f"档案快照按分区名额挤出 {len(evicted)} 行（最久没被再提的先出）")
    return SnapshotSelection(lines=lines, kept=kept_refs, evicted=evicted)
