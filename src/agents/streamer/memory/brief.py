"""常驻简报：按台账快照渲染的一段记忆摘要。

两段料：上一场（最近一个整理过、时长够的同类型场次）的档案快照——那一场新写的事加上从
之前几场接住、仍然成立的事，分"上一场""之前"两块列；所有不针对具体观众、不挂游戏的
未了结承诺（最老的在前，至多五条，多出来的折成一行指向回想工具）。彩排场接彩排场、
正式场接正式场，调试场不会被当成正式直播的上一场；时长太短的场次（开错后马上重开的
那一小段）也不算上一场，往前找，它登记的承诺照常进承诺段。本场台词已经点名用过的
承诺不再进简报（与线索卡的"用上即停"同口径）：一件事一场提一次就够，不然她每个
决策窗都看到它，会一整场围着它转。点名用过的档案行留着、末尾标"（本场已提过）"：
档案行是她知道的事实，撤掉她会在同一场里忘掉刚说过的名字和住处。

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
from src.modules.storage.models.memory import Commitment, SnapshotLine
from src.modules.types.game_save import GameSave
from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind

from .commitment_ttl import partition_brief_commitments
from .render import (
    MAX_BRIEF_COMMITMENTS,
    brief_archive_ids,
    memory_ref_archive_numbers,
    memory_ref_numbers,
    render_brief,
)

if TYPE_CHECKING:
    from src.modules.storage.repos import ArchiveRepo, ChatRepo, CommitmentRepo, EventRepo

__all__ = ["MemoryBrief", "build_brief", "load_used_refs"]

logger = get_logger("MemoryBrief")

# 一次读多少条未了结承诺（够筛出不挂游戏、不针对人的那几条）
_CANDIDATE_LIMIT = 200


@dataclass(frozen=True)
class MemoryBrief:
    """常驻简报的成品：一段文本 + 其中的承诺与档案行编号（点名的合法名单之一）。

    ``previous_session_id`` / ``archive_line_count`` 只供观测（开播日志与测试核对
    "上一场是谁、带了几行档案"）；文本本身仍是唯一注入面。
    """

    text: str = ""
    commitment_ids: Tuple[int, ...] = field(default_factory=tuple)
    archive_ids: Tuple[int, ...] = field(default_factory=tuple)
    # 本场台词已经点名用过的档案行（简报里标"（本场已提过）"的那些）：Planner 据此拦住主动复提
    used_archive_ids: Tuple[int, ...] = field(default_factory=tuple)
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
    承诺不进这一份简报，点名过的档案行标"（本场已提过）"。``current_saves`` 是分存档的游戏眼下所在的存档；``event_repo`` 给了才查
    "上一场玩这些游戏时记没记下存档"（没记下的块尾注一句）。
    """
    previous_session_id, snapshot_lines = await _snapshot_lines(archive_repo, kind, min_session_ms)
    previous_is_stale, earliest_recent_ms, recent_session_ids = await _recent_window(
        archive_repo,
        commitments_repo=commitments_repo,
        kind=kind,
        min_session_ms=min_session_ms,
        live_session_id=live_session_id,
        snapshot_owner_id=previous_session_id,
    )
    used: Set[int] = set()
    used_archive: Set[int] = set()
    if chat_repo is not None and live_session_id is not None:
        used, used_archive = await load_used_refs(chat_repo, int(live_session_id))
    saves = dict(current_saves or {})
    taken, earlier, other_save_rows = await _pending_commitments(
        commitments_repo,
        used=used,
        current_saves=saves,
        recent_session_ids=recent_session_ids,
        earliest_recent_ms=earliest_recent_ms,
    )
    text = render_brief(
        snapshot_lines=snapshot_lines,
        commitments=taken,
        other_save_commitments=other_save_rows,
        previous_session_id=previous_session_id,
        previous_is_stale=previous_is_stale,
        earliest_recent_ms=earliest_recent_ms,
        earlier_commitment_count=earlier,
        current_saves=saves,
        unsaved_games=await _unsaved_games(event_repo, previous_session_id, saves),
        used_archive_ids=used_archive,
    )
    return MemoryBrief(
        text=text,
        commitment_ids=tuple(int(item.id) for item in taken),
        archive_ids=tuple(
            brief_archive_ids(
                commitments=taken,
                current_saves=saves,
                snapshot_lines=snapshot_lines,
                previous_session_id=previous_session_id,
                previous_is_stale=previous_is_stale,
                earliest_recent_ms=earliest_recent_ms,
            )
        ),
        used_archive_ids=tuple(sorted(used_archive)),
        previous_session_id=int(previous_session_id) if previous_session_id is not None else None,
        archive_line_count=len(snapshot_lines),
    )


async def _snapshot_lines(
    archive_repo: "ArchiveRepo", kind: SessionKind, min_session_ms: int
) -> Tuple[Optional[int], List[SnapshotLine]]:
    """上一场的场次主键与它的快照各行（带稳定、再提起与人放回标注）；没有上一场或读不到时为 (None, [])。"""
    try:
        snapshot = await archive_repo.latest_snapshot(kind=kind, min_duration_ms=min_session_ms)
    except Exception as exc:  # noqa: BLE001 - 读不到就当没有上一场，简报少一段
        logger.warning(f"取上一场的档案快照失败（简报不带档案段）: {exc}", exc=exc)
        return None, []
    if snapshot is None:
        return None, []
    return int(snapshot.owner_session_id), list(snapshot.lines)


async def _recent_window(
    archive_repo: "ArchiveRepo",
    *,
    commitments_repo: Optional["CommitmentRepo"] = None,
    kind: SessionKind,
    min_session_ms: int,
    live_session_id: Optional[int],
    snapshot_owner_id: Optional[int],
) -> Tuple[bool, int, List[int]]:
    """算三件事：快照是否落后于最近完结场、最近三场的开播时刻下界、最近三场的场次号（新到旧）。"""
    finder = getattr(archive_repo, "recent_ended_session_bounds", None) or getattr(
        commitments_repo, "recent_ended_session_bounds", None
    )
    if finder is None:
        return False, 0, []
    try:
        bounds = await finder(
            kind=kind,
            min_duration_ms=min_session_ms,
            exclude_session_id=live_session_id,
            limit=3,
        )
    except Exception as exc:  # noqa: BLE001 - 查不到场次边界时不挡简报，按未落后、不限三场渲染
        logger.warning(f"查最近已结束场次边界失败（简报按常规上一场渲染）: {exc}", exc=exc)
        return False, 0, []
    if not bounds:
        return False, 0, []
    recent_ids = [int(item[0]) for item in bounds]
    stale = snapshot_owner_id is not None and recent_ids[0] != int(snapshot_owner_id)
    earliest_ms = int(bounds[2][1]) if len(bounds) >= 3 else 0
    return stale, earliest_ms, recent_ids


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


async def load_used_refs(chat_repo: "ChatRepo", live_session_id: int) -> Tuple[Set[int], Set[int]]:
    """本场台词已经点名引用过的（承诺编号, 档案行编号）：用上即停的口径，确知没送达的台词不算。

    常驻简报与线索推送共用这一份。读不到就当都没用上：最多这一窗多列一次，不挡决策。
    """
    try:
        raw_rows = await chat_repo.list_session_memory_refs(live_session_id=int(live_session_id))
    except Exception as exc:  # noqa: BLE001 - 查不到就当没用上，最多多提一次
        logger.warning(f"读本场记忆引用失败（这一次不做用上即停）: {exc}", exc=exc)
        return set(), set()
    commitments: Set[int] = set()
    archive: Set[int] = set()
    for raw in raw_rows:
        commitments.update(memory_ref_numbers(raw))
        archive.update(memory_ref_archive_numbers(raw))
    return commitments, archive


async def _pending_commitments(
    commitments_repo: "CommitmentRepo",
    used: AbstractSet[int] = frozenset(),
    *,
    current_saves: Optional[Mapping[str, GameSave]] = None,
    recent_session_ids: Sequence[int] = (),
    earliest_recent_ms: int = 0,
) -> Tuple[Sequence[Commitment], int, Sequence[Commitment]]:
    """切出进【还惦记的事】的近期未了结非私向承诺、搁了很久件数，以及别的存档里的承诺。

    对某位观众、某位游戏玩家的承诺只经线索推送在那个人开口时出现，不进常驻简报；
    挂在当前世界或不挂游戏的公开承诺若最近三场内被提起过（且未过短期时间词时效）进【还惦记的事】，
    更早搁下的只汇总为一句件数提醒，不再靠轮播刷屏。
    """
    try:
        rows = await commitments_repo.list_commitments(statuses=["open"], limit=_CANDIDATE_LIMIT)
    except Exception as exc:  # noqa: BLE001 - 读不到就当没有待办，简报少一段
        logger.warning(f"读未了结承诺失败（简报不带承诺段）: {exc}", exc=exc)
        return [], 0, []
    return partition_brief_commitments(
        rows,
        used_ids=used,
        current_saves=current_saves,
        recent_session_ids=recent_session_ids,
        earliest_recent_ms=earliest_recent_ms,
        limit=MAX_BRIEF_COMMITMENTS,
    )
