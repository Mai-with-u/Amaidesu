"""滚动档案：整理一场时把上一份档案里仍然成立的行接进本场档案。

常驻简报只读最近一份档案。档案原来一场一份、只写本场的事，碰上一场只播了十来分钟、中途
崩了几次的短场，前几场的名字、关系、住处就全从简报里掉出去。改成滚动以后：

1. 下播整理时，上一份档案（:meth:`ArchiveRepo.list_rolling_base`）编上"旧N"交给模型；
2. 模型在 ``keep`` 里列出仍然成立的旧行，被这一场改写了的不留旧行、在新行里按这一场的原话
   重写，了结或过时的不列；名字、关系、住处这类长期有效的事标 ``lasting``；
3. 代码按下面的取舍凑成本场档案，接过来的行保留原来的原话、游戏、存档和"出自哪一场"。

取舍（总数至多 :data:`MAX_ROLLING_LINES` 行）：先给长期有效的事占位（至多
:data:`MAX_LASTING_LINES` 行，接过来的在前、本场新写的在后，各按模型给的先后）→ 再放本场
新写的行 → 最后放接过来的其余行。长期的事因此不会被一场里的新事挤掉；占位装不下的长期新事
仍按新事排。写入顺序是本场新写的在前、接过来的在后，简报照这个顺序列。

模型输出里没有 ``keep`` 这个键（不是空列表）时，按"上一份原样接住"处理：宁可多带几行旧事，
也不能因为输出格式出岔子把前面几场一下子全忘掉。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from src.modules.logging import get_logger
from src.modules.storage.models.memory import ArchiveLine, PendingArchiveLine, UnregisteredItem

from .render import MAX_BRIEF_ARCHIVE_LINES

__all__ = [
    "MAX_BASE_LINES",
    "MAX_LASTING_LINES",
    "MAX_ROLLING_LINES",
    "keep_from_base",
    "render_base",
    "select_rolling",
]

logger = get_logger("RollingArchive")

# 一份滚动档案至多几行：整理写入与简报列出用同一个数
MAX_ROLLING_LINES = MAX_BRIEF_ARCHIVE_LINES
# 长期有效的事至多先占几行：名字、关系、住处不被新事挤掉，又不至于把新事全挤出去
MAX_LASTING_LINES = 6
# 交给模型的旧行至多几行（开始滚动的第一场会连着几份旧档案一起给）
MAX_BASE_LINES = 24
# 旧行编号前缀：与台词编号（#N）、承诺编号（约#N）分开，模型不会把它们混成一类
_OLD_PREFIX = "旧"


def _when(line: ArchiveLine, previous_session_id: Optional[int]) -> str:
    """旧行出自哪一场：上一份档案所在那一场写"上一场"，更早的写"更早"。"""
    if previous_session_id is not None and line.origin_session_id == int(previous_session_id):
        return "上一场"
    return "更早"


def render_base(base: Sequence[ArchiveLine]) -> str:
    """上一份档案渲染成给模型看的旧行清单；没有旧行时为空串。

    每行：编号、出自哪一场、标题、原话，挂着游戏的写上游戏与存档，长期有效的标出来。
    """
    if not base:
        return ""
    previous_session_id = base[0].live_session_id
    rows: List[str] = []
    for index, line in enumerate(base[:MAX_BASE_LINES], start=1):
        notes = [f"原话「{line.quote}」"]
        if line.game:
            where = f"{line.game}「{line.save_name or line.save_id[:8]}」" if line.save_id else line.game
            notes.append(f"在 {where}")
        if line.lasting:
            notes.append("长期")
        rows.append(f"{_OLD_PREFIX}{index} [{_when(line, previous_session_id)}] {line.text}（{'；'.join(notes)}）")
    return "\n".join(rows)


def _as_old_number(raw: Any) -> Optional[int]:
    """模型给的旧行编号：整数、纯数字或"旧N"才认，其余当没给。"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.startswith(_OLD_PREFIX):
        text = text[len(_OLD_PREFIX) :].strip()
    return int(text) if text.isdigit() else None


def keep_from_base(
    data: Dict[str, Any], base: Sequence[ArchiveLine]
) -> Tuple[List[PendingArchiveLine], List[UnregisteredItem]]:
    """读模型输出里的 ``keep``，换成要接进本场档案的行；编号认不出的连同原样写进未登记清单。

    ``keep`` 缺失时上一份原样接住（见模块说明）；``keep`` 不是列表时按输出读不懂处理，
    由调用方抛整理失败。每条可以是编号本身，也可以是 ``{"old": 编号, "lasting": 真假}``；
    没给 ``lasting`` 的沿用这一行原来的标记。同一个编号只接一次。
    """
    shown = list(base[:MAX_BASE_LINES])
    if "keep" not in data:
        if shown:
            logger.info(f"模型输出里没有 keep：上一份档案 {len(shown)} 行原样接住")
        return [_carried(line, line.lasting) for line in shown], []
    raw_keep = data.get("keep")
    if raw_keep is None:
        return [], []
    if not isinstance(raw_keep, list):
        raise ValueError("模型输出的 keep 不是列表")
    kept: List[PendingArchiveLine] = []
    missed: List[UnregisteredItem] = []
    taken: Set[int] = set()
    for raw in raw_keep:
        entry = raw if isinstance(raw, dict) else {"old": raw}
        number = _as_old_number(entry.get("old"))
        if number is None or not 1 <= number <= len(shown):
            missed.append(UnregisteredItem(kind="archive", what=str(entry.get("old")), reason="接着记的旧行编号不存在"))
            continue
        if number in taken:
            continue
        taken.add(number)
        line = shown[number - 1]
        lasting = entry.get("lasting")
        kept.append(_carried(line, lasting if isinstance(lasting, bool) else line.lasting))
    return kept, missed


def _carried(line: ArchiveLine, lasting: bool) -> PendingArchiveLine:
    """接过来的一行：原话、游戏、存档照旧，记着这件事最初出自哪一场。"""
    return PendingArchiveLine(
        text=line.text,
        quote=line.quote,
        evidence_chat_id=int(line.evidence_chat_id),
        game=line.game,
        save_id=line.save_id,
        save_name=line.save_name,
        from_session_id=line.origin_session_id,
        lasting=bool(lasting),
    )


def select_rolling(
    new_lines: Sequence[PendingArchiveLine], kept: Sequence[PendingArchiveLine]
) -> List[PendingArchiveLine]:
    """按取舍规则凑成本场档案（见模块说明）；写入顺序为本场新写的在前、接过来的在后。

    同一句原话（证据台词行）只留一行：本场新写的优先，接过来的同一句不再占位。
    """
    new_evidence = {int(line.evidence_chat_id) for line in new_lines}
    carried = [line for line in kept if int(line.evidence_chat_id) not in new_evidence]
    chosen: Set[int] = set()
    lasting_pool = [line for line in carried if line.lasting] + [line for line in new_lines if line.lasting]
    for line in lasting_pool[:MAX_LASTING_LINES]:
        chosen.add(id(line))
    for pool in (new_lines, carried):
        for line in pool:
            if len(chosen) >= MAX_ROLLING_LINES:
                break
            chosen.add(id(line))
    selected = [line for line in new_lines if id(line) in chosen] + [line for line in carried if id(line) in chosen]
    dropped = len(new_lines) + len(carried) - len(selected)
    if dropped:
        logger.info(f"滚动档案超过 {MAX_ROLLING_LINES} 行：按取舍规则少记 {dropped} 行")
    return selected
