"""常驻简报：按台账快照渲染的一段记忆摘要。

两段料：最近一个已整理、有档案行的同类型场次的档案；所有不针对具体观众、不挂游戏的
未了结承诺（最老的在前，至多五条，多出来的折成一行指向回想工具）。彩排场接彩排场、
正式场接正式场，调试场不会被当成正式直播的上一场；时长太短的场次（开错后马上重开的
那一小段）也不算上一场，往前找，它登记的承诺照常进承诺段。本场台词已经点名用过的
承诺不再进简报（与线索卡的"用上即停"同口径）：一件事一场提一次就够，不然她每个
决策窗都看到它，会一整场围着它转。

分存档的游戏（工具面报得出眼下在哪个存档）：简报开头写明眼下在哪个存档，上一场在别的
存档里做的事折成一行，没记存档的游戏行注明不确定（判定见 ``saves``）。

渲染只依赖表快照、不含时刻：表没变时每个决策窗的输出逐字节相同，Planner 的系统
提示词前缀缓存仍然命中；表变了（运营者判误记、了结、删一行，或上一场整理刚写完）
下一窗就反映出来——每窗重渲正是为了让直播中的纠错"下一窗少一行"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AbstractSet, List, Mapping, Optional, Sequence, Set, Tuple

from src.modules.logging import get_logger
from src.modules.storage.models.memory import Commitment
from src.modules.types.game_save import GameSave
from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind

from .render import MAX_BRIEF_COMMITMENTS, memory_ref_numbers, render_brief

if TYPE_CHECKING:
    from src.modules.storage.repos import ArchiveRepo, ChatRepo, CommitmentRepo, EventRepo

__all__ = ["MemoryBrief", "build_brief", "load_used_commitment_ids"]

logger = get_logger("MemoryBrief")

# 一次读多少条未了结承诺（够筛出不挂游戏、不针对人的那几条）
_CANDIDATE_LIMIT = 200


@dataclass(frozen=True)
class MemoryBrief:
    """常驻简报的成品：一段文本 + 其中的承诺编号（点名的合法名单之一）。

    ``previous_session_id`` / ``archive_line_count`` 只供观测（开播日志与测试核对
    "上一场是谁、带了几行档案"）；文本本身仍是唯一注入面。
    """

    text: str = ""
    commitment_ids: Tuple[int, ...] = field(default_factory=tuple)
    previous_session_id: Optional[int] = None
    archive_line_count: int = 0


async def build_brief(
    *,
    archive_repo: "ArchiveRepo",
    commitments_repo: "CommitmentRepo",
    kind: SessionKind = SESSION_KIND_LIVE,
    min_session_ms: int = 0,
    chat_repo: Optional["ChatRepo"] = None,
    live_session_id: Optional[int] = None,
    event_repo: Optional["EventRepo"] = None,
    current_saves: Optional[Mapping[str, GameSave]] = None,
) -> MemoryBrief:
    """拼一份常驻简报；两段料都没取到时返回空简报（系统提示词里整段省略）。

    ``min_session_ms``：时长不到它的场次不当"上一场"（开错后马上重开的那一小段），往前找。
    ``chat_repo`` 与 ``live_session_id`` 都给了（正在直播）才做"用上即停"：本场台词点名过的
    承诺不进这一份简报。``current_saves`` 是分存档的游戏眼下所在的存档；``event_repo`` 给了才查
    "上一场玩这些游戏时记没记下存档"（没记下的块尾注一句）。
    """
    archive_lines = await _archive_lines(archive_repo, kind, min_session_ms)
    used: Set[int] = set()
    if chat_repo is not None and live_session_id is not None:
        used = await load_used_commitment_ids(chat_repo, int(live_session_id))
    taken, earlier = await _pending_commitments(commitments_repo, used)
    previous_session_id = archive_lines[0].live_session_id if archive_lines else None
    saves = dict(current_saves or {})
    text = render_brief(
        archive_lines=archive_lines,
        commitments=taken,
        previous_session_id=previous_session_id,
        earlier_commitment_count=earlier,
        current_saves=saves,
        unsaved_games=await _unsaved_games(event_repo, previous_session_id, saves),
    )
    return MemoryBrief(
        text=text,
        commitment_ids=tuple(int(item.id) for item in taken),
        previous_session_id=int(previous_session_id) if previous_session_id is not None else None,
        archive_line_count=len(archive_lines),
    )


async def _archive_lines(archive_repo: "ArchiveRepo", kind: SessionKind, min_session_ms: int) -> List:
    """最近一个已整理、有档案行、时长够的同类型场次的档案行；没有就空。"""
    try:
        session_id = await archive_repo.get_last_archived_session_id(kind=kind, min_duration_ms=min_session_ms)
    except Exception as exc:  # noqa: BLE001 - 读不到就当没有上一场，简报少一段
        logger.warning(f"取上一场的编号失败（简报不带档案段）: {exc}", exc=exc)
        return []
    if session_id is None:
        return []
    try:
        return await archive_repo.list_lines(live_session_id=int(session_id))
    except Exception as exc:  # noqa: BLE001 - 同上
        logger.warning(f"取上一场的档案行失败（简报不带档案段）: {exc}", exc=exc)
        return []


async def _unsaved_games(
    event_repo: Optional["EventRepo"], session_id: Optional[int], current: Mapping[str, GameSave]
) -> List[str]:
    """上一场玩过、眼下分存档、但那场一条带存档的事件都没记下的游戏（升级前的旧场次、旧版游戏）。"""
    if event_repo is None or session_id is None or not current:
        return []
    try:
        played = await event_repo.list_games_by_session(live_session_ids=[int(session_id)])
        spans = await event_repo.list_session_save_spans(live_session_id=int(session_id))
    except Exception as exc:  # noqa: BLE001 - 查不到就不加注，简报照常
        logger.warning(f"查上一场玩游戏时记没记存档失败（简报不加注）: {exc}", exc=exc)
        return []
    saved = {span.game for span in spans}
    games = played.get(int(session_id), set())
    return [game for game in sorted(current) if game in games and game not in saved]


async def load_used_commitment_ids(chat_repo: "ChatRepo", live_session_id: int) -> Set[int]:
    """本场台词已经点名引用过的承诺编号（用上即停；确知没送达的台词不算）。

    常驻简报与线索推送共用这一份口径。读不到就当没用上：最多这一窗多列一次，不挡决策。
    """
    try:
        raw_rows = await chat_repo.list_session_memory_refs(live_session_id=int(live_session_id))
    except Exception as exc:  # noqa: BLE001 - 查不到就当没用上，最多多提一次
        logger.warning(f"读本场记忆引用失败（这一次不做用上即停）: {exc}", exc=exc)
        return set()
    used: Set[int] = set()
    for raw in raw_rows:
        used.update(memory_ref_numbers(raw))
    return used


async def _pending_commitments(
    commitments_repo: "CommitmentRepo",
    used: AbstractSet[int] = frozenset(),
) -> Tuple[Sequence[Commitment], int]:
    """不针对具体观众、不挂游戏、本场还没点名用过的未了结承诺（最老的在前）与更多条数。

    对某位观众的承诺只经线索推送在 ta 开口时出现，挂游戏的只在该游戏开着时出现，
    都不进常驻简报——否则她每场都对着全场念叨不在场的人。
    """
    try:
        rows = await commitments_repo.list_commitments(statuses=["open"], limit=_CANDIDATE_LIMIT)
    except Exception as exc:  # noqa: BLE001 - 读不到就当没有待办，简报少一段
        logger.warning(f"读未了结承诺失败（简报不带承诺段）: {exc}", exc=exc)
        return [], 0
    candidates = [row for row in rows if row.viewer_id is None and not row.game and int(row.id) not in used]
    # 仓储按新的在前给，简报要最老的在前
    candidates.reverse()
    taken = candidates[:MAX_BRIEF_COMMITMENTS]
    return taken, max(0, len(candidates) - len(taken))
