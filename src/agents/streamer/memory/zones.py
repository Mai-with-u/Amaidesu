"""常驻简报分区定义与名额规则（D2-1）：四个分区各自计额，并留出新增分区的扩展槽位。

四个默认分区（区与区之间互不挤占）：
- 【这个世界里】：当前游戏存档里的稳定事实（名字压进 1～2 行名字表，其余每条一行、不带原话，共 ≤12 行）
- 【最近几场】：当前游戏存档里的普通事件（上一场 ≤6 行，再往前两场按最近被提起合计 ≤6 行）
- 【直播间里的事】：不挂游戏的行（换任何游戏存档照样带着，≤6 行）
- 【还惦记的事】：最近三场内提起过的未了结承诺（≤5 条，更早搁下的折成一句总数）
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import AbstractSet, Callable, List, Mapping, Optional, Sequence, Tuple

from src.modules.storage.models.memory import ArchiveLine, Commitment, SnapshotLine
from src.modules.types.game_save import GameSave

from .saves import SavePlace, place_of

__all__ = [
    "MAX_BRIEF_COMMITMENTS",
    "MAX_RECENT_EARLIER_LINES",
    "MAX_RECENT_PREVIOUS_LINES",
    "MAX_ROOM_LINES",
    "MAX_WORLD_LINES",
    "MAX_WORLD_NAMES",
    "WORLD_NAMES_PER_ROW",
    "BriefRenderContext",
    "BriefZoneOutput",
    "BriefZoneSpec",
    "is_from_session",
    "is_naming_title",
    "name_table_row_count",
    "pick_within_quota",
    "select_recent_zone_items",
    "select_room_zone_items",
    "select_world_zone_items",
]

# 四个分区各自的名额：取名与稳定事实不挤占最近事件，游戏事件不挤占直播间里的事
MAX_WORLD_LINES = 12  # 【这个世界里】总展示行数名额（1～2 行名字表 + 其余稳定行 ≤ 12）
MAX_WORLD_NAMES = 12  # 【这个世界里】名字类最多收几条（压进 1～2 行名字表）
WORLD_NAMES_PER_ROW = 6  # 名字表每行最多并列几个名字项
MAX_RECENT_PREVIOUS_LINES = 6  # 【最近几场】上一场事件至多几行
MAX_RECENT_EARLIER_LINES = 6  # 【最近几场】再往前两场事件合计至多几行（按最近被提起）
MAX_ROOM_LINES = 6  # 【直播间里的事】不挂游戏的行至多几行（换存档照样带着）
MAX_BRIEF_COMMITMENTS = 5  # 【还惦记的事】最近三场内提起过的未了结承诺至多几条

# 稳定的事里认"起名字"这一类的词：压进名字表，不逐条占行，也不挤掉住处、关系和最近事件
_NAMING_MARKERS = ("取名", "起名", "改名", "命名", "定名", "封号", "名为", "外号", "绰号")
# 含"叫"但实际是发声、呼唤或条件句的固定搭配：先剥掉再看还有没有表命名的"叫"
_VOCAL_CALL_WORDS = (
    "大叫",
    "惨叫",
    "尖叫",
    "吼叫",
    "乱叫",
    "直叫",
    "怪叫",
    "喊叫",
    "叫唤",
    "叫声",
    "叫醒",
    "叫停",
    "叫好",
    "叫苦",
    "叫卖",
    "叫板",
    "叫阵",
    "叫门",
    "叫屈",
    "叫冤",
    "叫绝",
    "叫座",
    "叫嚣",
    "叫嚷",
    "叫喊",
    "叫我",
)
# 口号与打卡规矩虽常含称呼，但属于互动规矩而非对象名字表
_RULE_SLOGAN_WORDS = ("口号", "打卡", "喊")


def is_naming_title(title: str) -> bool:
    """判断一条稳定事实的标题是不是"给对象或地点起名字"。

    只在已标为稳定的事上用来分流：名字类合并进一两行名字表，不再一条占一行把住处、关系挤掉；
    口号、打卡规矩与日常发声词（大叫、叫醒等）先剥除，避免把互动规矩或状态误归进名字表。
    """
    body = (title or "").strip()
    if not body:
        return False
    if any(marker in body for marker in _NAMING_MARKERS):
        return True
    if any(word in body for word in _RULE_SLOGAN_WORDS):
        return False
    cleaned = body
    for word in _VOCAL_CALL_WORDS:
        cleaned = cleaned.replace(word, "")
    return "叫" in cleaned


def name_table_row_count(name_count: int) -> int:
    """名字表会占几行展示名额（0～2 行）：快照取舍与简报渲染共用同一换算，两边名额严丝合缝。"""
    capped = max(0, min(int(name_count), MAX_WORLD_NAMES))
    return 0 if capped == 0 else math.ceil(capped / WORLD_NAMES_PER_ROW)


def is_from_session(line: ArchiveLine, archive_session: Optional[int]) -> bool:
    """这一行是不是快照所属场次新写的（档案行所在场次就是写下它的那一场）。"""
    return archive_session is None or int(line.live_session_id) == int(archive_session)


@dataclass(frozen=True)
class BriefZoneOutput:
    """一个常驻简报分区的渲染结果：区正文各行与其中标了编号、可供点名的档案行主键。"""

    lines: Tuple[str, ...] = ()
    archive_ids: Tuple[int, ...] = ()


@dataclass(frozen=True)
class BriefRenderContext:
    """常驻简报各分区共享的只读上下文：去重并归一化后的一次快照数据。"""

    snapshot_lines: Tuple[SnapshotLine, ...]
    commitments: Tuple[Commitment, ...]
    earlier_commitment_count: int
    archive_session_id: Optional[int]
    previous_is_stale: bool
    earliest_recent_ms: int
    current_saves: Mapping[str, GameSave]
    unsaved_games: Tuple[str, ...]
    used_archive_ids: AbstractSet[int]
    other_save_commitments: Tuple[Commitment, ...] = ()
    archive_corrections: Mapping[int, str] = field(default_factory=dict)
    operator_notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BriefZoneSpec:
    """常驻简报分区槽位：新增分区只需往 ``BRIEF_ZONES`` 追加一项，渲染与点名收集骨架无需改动。"""

    key: str
    header: str
    render: Callable[[BriefRenderContext], BriefZoneOutput]


def pick_within_quota(items: Sequence[SnapshotLine], quota: int) -> List[SnapshotLine]:
    """超出名额时先按人放回与最近提起挑出保留集合，再按原快照位置排回，保证表不变时字节稳定。"""
    cap = max(0, int(quota))
    if cap == 0 or not items:
        return []
    if len(items) <= cap:
        return list(items)
    ranked = sorted(enumerate(items), key=lambda pair: (not pair[1].pinned, -int(pair[1].seen_at_ms), pair[0]))
    kept_indices = {pair[0] for pair in ranked[:cap]}
    return [item for idx, item in enumerate(items) if idx in kept_indices]


def select_world_zone_items(ctx: BriefRenderContext) -> Tuple[List[SnapshotLine], List[SnapshotLine]]:
    """筛出【这个世界里】的名字类列表与其余稳定事实列表（各自按名额裁剪）。"""
    candidates = [
        item
        for item in ctx.snapshot_lines
        if item.line.game
        and item.lasting
        and place_of(item.line.game, item.line.save_id, ctx.current_saves) is not SavePlace.OTHER
        and (item.line.text or "").strip()
    ]
    if not candidates:
        return [], []
    naming = pick_within_quota([item for item in candidates if is_naming_title(item.line.text)], MAX_WORLD_NAMES)
    other_cap = max(0, MAX_WORLD_LINES - name_table_row_count(len(naming)))
    others = pick_within_quota([item for item in candidates if not is_naming_title(item.line.text)], other_cap)
    return naming, others


def select_recent_zone_items(ctx: BriefRenderContext) -> Tuple[List[SnapshotLine], List[SnapshotLine]]:
    """筛出【最近几场】的上一场事件（≤6）与再往前两场事件（按最近被提起，≤6）。"""
    candidates = [
        item
        for item in ctx.snapshot_lines
        if item.line.game
        and not item.lasting
        and place_of(item.line.game, item.line.save_id, ctx.current_saves) is not SavePlace.OTHER
        and (item.line.text or "").strip()
    ]
    if not candidates:
        return [], []
    prev_items = [item for item in candidates if is_from_session(item.line, ctx.archive_session_id)][
        :MAX_RECENT_PREVIOUS_LINES
    ]
    earlier_pool = [
        item
        for item in candidates
        if not is_from_session(item.line, ctx.archive_session_id)
        and (ctx.earliest_recent_ms <= 0 or item.pinned or int(item.seen_at_ms) >= ctx.earliest_recent_ms)
    ]
    ranked = sorted(enumerate(earlier_pool), key=lambda pair: (not pair[1].pinned, -int(pair[1].seen_at_ms), pair[0]))
    return prev_items, [item for _idx, item in ranked[:MAX_RECENT_EARLIER_LINES]]


def select_room_zone_items(ctx: BriefRenderContext) -> List[SnapshotLine]:
    """筛出【直播间里的事】的不挂游戏行（人放回 → 稳定 → 上一场 → 最近被提起，≤6）。"""
    candidates = [item for item in ctx.snapshot_lines if not item.line.game and (item.line.text or "").strip()]
    if not candidates:
        return []
    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: (
            not pair[1].pinned,
            not pair[1].lasting,
            not is_from_session(pair[1].line, ctx.archive_session_id),
            -int(pair[1].seen_at_ms),
            pair[0],
        ),
    )
    return [item for _idx, item in ranked[:MAX_ROOM_LINES]]
