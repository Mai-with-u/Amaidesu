"""常驻简报：开播时拼好、整场不变的一段记忆摘要。

两段料：最近一个已整理、有档案行的同类型场次的档案；所有不针对具体观众、不挂游戏的
未了结承诺（最老的在前，至多五条，多出来的折成一行指向回想工具）。彩排场接彩排场、
正式场接正式场，调试场不会被当成正式直播的上一场。

拼好之后整场不再重算：前缀缓存才命中，Planner 每个决策窗的输入逐字节相同。运营者
在直播中判误记或删一条，要等下一场开播才从简报里消失（参考段的卡片不受影响）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Sequence, Tuple

from src.modules.logging import get_logger
from src.modules.storage.models.memory import Commitment
from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind

from .render import MAX_BRIEF_COMMITMENTS, render_brief

if TYPE_CHECKING:
    from src.modules.storage.repos import ArchiveRepo, CommitmentRepo

__all__ = ["MemoryBrief", "build_brief"]

logger = get_logger("MemoryBrief")

# 一次读多少条未了结承诺（够筛出不挂游戏、不针对人的那几条）
_CANDIDATE_LIMIT = 200


@dataclass(frozen=True)
class MemoryBrief:
    """常驻简报的成品：一段文本 + 其中的承诺编号（点名的合法名单之一）。"""

    text: str = ""
    commitment_ids: Tuple[int, ...] = field(default_factory=tuple)


async def build_brief(
    *,
    archive_repo: "ArchiveRepo",
    commitments_repo: "CommitmentRepo",
    kind: SessionKind = SESSION_KIND_LIVE,
) -> MemoryBrief:
    """拼一份常驻简报；两段料都没取到时返回空简报（系统提示词里整段省略）。"""
    archive_lines = await _archive_lines(archive_repo, kind)
    taken, earlier = await _pending_commitments(commitments_repo)
    previous_session_id = archive_lines[0].live_session_id if archive_lines else None
    text = render_brief(
        archive_lines=archive_lines,
        commitments=taken,
        previous_session_id=previous_session_id,
        earlier_commitment_count=earlier,
    )
    return MemoryBrief(text=text, commitment_ids=tuple(int(item.id) for item in taken))


async def _archive_lines(archive_repo: "ArchiveRepo", kind: SessionKind) -> List:
    """最近一个已整理、有档案行的同类型场次的档案行；没有就空。"""
    try:
        session_id = await archive_repo.get_last_archived_session_id(kind=kind)
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


async def _pending_commitments(
    commitments_repo: "CommitmentRepo",
) -> Tuple[Sequence[Commitment], int]:
    """不针对具体观众、不挂游戏的未了结承诺（最老的在前）与更多条数。

    对某位观众的承诺只经线索推送在 ta 开口时出现，挂游戏的只在该游戏开着时出现，
    都不进常驻简报——否则她每场都对着全场念叨不在场的人。
    """
    try:
        rows = await commitments_repo.list_commitments(statuses=["open"], limit=_CANDIDATE_LIMIT)
    except Exception as exc:  # noqa: BLE001 - 读不到就当没有待办，简报少一段
        logger.warning(f"读未了结承诺失败（简报不带承诺段）: {exc}", exc=exc)
        return [], 0
    candidates = [row for row in rows if row.viewer_id is None and not row.game]
    # 仓储按新的在前给，简报要最老的在前
    candidates.reverse()
    taken = candidates[:MAX_BRIEF_COMMITMENTS]
    return taken, max(0, len(candidates) - len(taken))
