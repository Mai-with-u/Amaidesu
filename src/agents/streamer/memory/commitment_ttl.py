"""承诺过期判定与【还惦记的事】分区筛选（D2-3）。

承诺生命周期区分两档过期门槛（均为读时计算，不改库里的 ``status='open'``，回想工具照查）：
- **近期一次性时间词**（今晚、明天、下次等）：隔了一个时长达标的正式场仍未被再提起，即转为"搁了很久"；
- **一般承诺**：连续三个时长达标的正式场未被提起，转为"搁了很久"；一旦在后续场次被再次提起，
  ``last_mentioned_session_id`` 推进，自动回到活跃区。
"""

from __future__ import annotations

from typing import AbstractSet, List, Mapping, Optional, Sequence, Tuple

from src.modules.storage.models.memory import Commitment
from src.modules.types.game_save import GameSave

from .saves import SavePlace, place_of
from .zones import MAX_BRIEF_COMMITMENTS

__all__ = [
    "SHORT_TERM_TIME_WORDS",
    "effective_mentioned_session_id",
    "has_short_term_time_word",
    "is_commitment_dormant",
    "partition_brief_commitments",
]

# 明确指向当晚、次日或紧邻下一场的近期时间词：隔过一场正式场没再提就算已过时效
SHORT_TERM_TIME_WORDS: Tuple[str, ...] = (
    "今晚",
    "今夜",
    "今天",
    "待会",
    "待会儿",
    "一会",
    "一会儿",
    "明天",
    "明早",
    "明晚",
    "下次",
    "下回",
    "下一场",
)


def has_short_term_time_word(*texts: str) -> bool:
    """标题或原话中是否含有"今晚 / 明天 / 下次"等近期一次性时间词。"""
    combined = " ".join(text for text in texts if text)
    if not combined:
        return False
    return any(marker in combined for marker in SHORT_TERM_TIME_WORDS)


def effective_mentioned_session_id(commitment: Commitment) -> Optional[int]:
    """承诺最近一次被提起的场次号（未单独推进时取登记场次）。"""
    if commitment.last_mentioned_session_id is not None:
        return int(commitment.last_mentioned_session_id)
    if commitment.created_session_id is not None:
        return int(commitment.created_session_id)
    return None


def is_commitment_dormant(
    commitment: Commitment,
    *,
    recent_session_ids: Sequence[int],
    earliest_recent_ms: int = 0,
) -> bool:
    """判断一条未了结承诺是否已转为"搁了很久"（不常驻、不推卡，但回想可查）。

    ``recent_session_ids`` 为本场之前按时间从新到旧排列的、至多 3 个时长达标的已完结同型场次号。
    """
    if not recent_session_ids:
        return False
    latest_ended = int(recent_session_ids[0])
    recent_set = {int(sid) for sid in recent_session_ids[:3]}
    ref_sid = effective_mentioned_session_id(commitment)
    if ref_sid is not None:
        # 当前进行中场次刚登记或刚提起的（场次号大于最近完结场），不算过期
        if ref_sid > latest_ended:
            return False
        if has_short_term_time_word(commitment.what, commitment.quote):
            # 带近期时间词：不是在紧邻上一场提起的，说明至少隔过了一场正式场没再提
            return ref_sid != latest_ended
        if len(recent_session_ids) >= 3:
            return ref_sid not in recent_set
        return False
    # 运营手建且未绑场次的行：满三场且最后更新早于最近三场下界时转为搁了很久
    if len(recent_session_ids) >= 3 and earliest_recent_ms > 0:
        return int(commitment.updated_at_ms) < int(earliest_recent_ms)
    return False


def partition_brief_commitments(
    rows: Sequence[Commitment],
    *,
    used_ids: AbstractSet[int] = frozenset(),
    current_saves: Optional[Mapping[str, GameSave]] = None,
    recent_session_ids: Sequence[int] = (),
    earliest_recent_ms: int = 0,
    limit: int = MAX_BRIEF_COMMITMENTS,
) -> Tuple[List[Commitment], int, List[Commitment]]:
    """把未了结承诺切分为（进【还惦记的事】的活跃承诺, 搁了很久与超额件数, 别的存档里的承诺）。

    - 对某位观众或某位游戏玩家的私向承诺一律不进简报（仍由对象露面时推卡）；
    - 挂在别的存档上的承诺归入 ``other_save_rows``，由简报顶部存档折叠行统一计数；
    - 本场台词已点名引用过的承诺按"用上即停"跳过，既不重复展开也不计入搁置件数；
    - 活跃承诺按"最近被提起的场次"从新到旧排，取前 ``limit`` 条，多出的并入搁置件数。
    """
    saves = current_saves or {}
    active: List[Commitment] = []
    other_save_rows: List[Commitment] = []
    dormant_count = 0
    for row in rows:
        if row.status != "open" or row.has_target:
            continue
        if not (row.what or "").strip():
            continue
        if row.game and place_of(row.game, row.save_id, saves) is SavePlace.OTHER:
            other_save_rows.append(row)
            continue
        if int(row.id) in used_ids:
            continue
        if is_commitment_dormant(row, recent_session_ids=recent_session_ids, earliest_recent_ms=earliest_recent_ms):
            dormant_count += 1
            continue
        active.append(row)

    active.sort(
        key=lambda item: (effective_mentioned_session_id(item) or 0, int(item.id)),
        reverse=True,
    )
    taken = active[: max(0, int(limit))]
    overflow = max(0, len(active) - len(taken))
    return taken, dormant_count + overflow, other_save_rows
