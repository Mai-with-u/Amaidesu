"""记忆渲染：把承诺与场次档案渲染成两档写法（Planner 档 / 说话档）。

所有通路共用一个渲染函数，两档的差别只有三处：

| 成分 | Planner 档 | 说话档（Replyer） |
|---|---|---|
| 编号 | 承诺（约#7）与档案行（档#41）都有，用于点名；别的存档折成的一行不带 | 没有 |
| 时间 | 跨场只用"上一场""之前" | 跨场只用"上次""之前" |
| 内容 | 不针对具体观众的承诺给标题加原话；对某位观众的承诺只给动作标题；档案给标题加原话 | 承诺只给动作标题；档案只给标题 |

对某位观众的承诺在 Planner 档也不给原话：原话可能夹着 ta 的私事（"阿杰别难过，
失恋而已！下次进 MC 给你盖个小房子"），进了交接稿她就会当众问起；找承诺时只截
答应的那半句是同一道防线的写入一侧。

任何档都不出现精确天数、场次数和日期——所以交接稿没有"剥离数字"这一步，Planner
手里根本没有这些数字。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, AbstractSet, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from src.modules.storage.models.memory import ArchiveLine, Commitment, SnapshotLine
from src.modules.storage.privacy import mask_names_in_text
from src.modules.types.game_save import GameSave

from .commitment_ttl import effective_mentioned_session_id
from .saves import SavePlace, canonical_save_names, place_of
from .zones import (
    MAX_BRIEF_COMMITMENTS,
    MAX_RECENT_EARLIER_LINES,
    MAX_RECENT_PREVIOUS_LINES,
    MAX_ROOM_LINES,
    MAX_WORLD_LINES,
    MAX_WORLD_NAMES,
    WORLD_NAMES_PER_ROW,
    BriefRenderContext,
    BriefZoneOutput,
    BriefZoneSpec,
    is_from_session,
    is_naming_title,
    name_table_row_count,
    select_recent_zone_items,
    select_room_zone_items,
    select_world_zone_items,
)

if TYPE_CHECKING:
    from src.modules.storage.models.game_players import GamePlayer, PlayerInteraction

__all__ = [
    "BRIEF_ZONES",
    "BriefRenderContext",
    "BriefZoneOutput",
    "BriefZoneSpec",
    "COMMITMENTS_ZONE",
    "MAX_BRIEF_ARCHIVE_LINES",
    "MAX_BRIEF_COMMITMENTS",
    "MAX_PRESENCE_VIEWERS",
    "MAX_RECENT_EARLIER_LINES",
    "MAX_RECENT_PREVIOUS_LINES",
    "MAX_ROOM_LINES",
    "MAX_WORLD_LINES",
    "MAX_WORLD_NAMES",
    "PRESENCE_RECENT_MS",
    "RECENT_ZONE",
    "ROOM_ZONE",
    "WORLD_NAMES_PER_ROW",
    "WORLD_ZONE",
    "brief_archive_ids",
    "format_memory_refs",
    "is_naming_title",
    "memory_ref_archive_numbers",
    "memory_ref_numbers",
    "name_table_row_count",
    "render_archive_block",
    "render_archive_card",
    "render_archive_number",
    "render_brief",
    "render_commitment_number",
    "render_commitment_text",
    "render_cue_block",
    "render_cue_header",
    "render_forget_notice",
    "render_game_card",
    "render_joke_card",
    "render_player_card",
    "render_presence_line",
    "render_recalled_commitment_card",
    "render_retract_notice",
    "render_save_heads",
    "render_spoken_archive",
    "render_spoken_memory",
    "render_viewer_card",
    "source_notes",
]

# 单块档案列表的兼容默认上限（供 render_archive_block 单独渲染时截断；常驻简报按 zones 各区名额计）
MAX_BRIEF_ARCHIVE_LINES = 12
# 老观众在场一行最多几个人，超出的折成"等 N 位"
MAX_PRESENCE_VIEWERS = 6
# 档位词分界：三天内来过写"前几天刚来过"，否则写"有阵子没来"
PRESENCE_RECENT_MS = 3 * 24 * 60 * 60 * 1000

# 两档共用的抬头与提示
_BRIEF_HEADER = "# 她记得的事（内部参考；以眼前为准，用不上就不提）"
# 线索块和常驻简报一样写明"以眼前为准"：卡片是代码按线索找的旧事，眼前的现场说了算
_CUE_HEADER = (
    "【这一批想起来的事】"
    "（代码按线索找的，以眼前为准；不提也行。要用就把编号写进 streamer_reply 的 use_memory，一次一条；"
    "一句台词最多带一件旧事）"
)
# 各分区抬头：新增分区只需往 BRIEF_ZONES 加一项，渲染骨架不动
_WORLD_ZONE_HEADER = "【这个世界里】"
_RECENT_ZONE_HEADER = "【最近几场】"
_ROOM_ZONE_HEADER = "【直播间里的事】"
_COMMITMENTS_HEADER = "【还惦记的事】（想做再做的事，不用赶）："
# 承诺段末尾固定的一行：做不到的事给一条收场的路（当众说清楚，下播整理才认得出是了结），
# 也防她为了交差说"画好了"。写"确实做不到"，免得她把做得到的事早早放弃。逐字节不变，前缀缓存照旧命中
_COMMITMENTS_HINT = "（确实做不到的，找个合适的时机当众说清楚；没做的别说成做完了）"
_PRESENCE_HEADER = "老观众在场（只知道 ta 常来、多久没来；ta 的往事你不记得，别编，想知道就问）："
_SPOKEN_HEADER = "## 你想起来的事（只用这里写到的，不补细节；不说日期和次数）"

# 跨场只用这两个时间词
_WHEN_PREVIOUS = "上一场"
_WHEN_EARLIER = "之前"
# 【最近几场】里两拨事件的小抬头；最近一场还没整理完时如实说明快照截在更早那一场，不冒充紧邻的上一场
_WHEN_PREVIOUS_HEADER = "上一场："
_WHEN_STALE_PREVIOUS_HEADER = "上次已整理的那场（上一场还没整理完）："
_WHEN_EARLIER_HEADER = "再往前："
_SPOKEN_WHEN_PREVIOUS = "上次"
_SPOKEN_WHEN_EARLIER = "之前"
# 档案行与承诺的编号前缀（与她"说过的话""答应过的事"对应）
_ARCHIVE_PREFIX = "档"
_COMMITMENT_PREFIX = "约"
# 本场她已经点名用过的档案行：行留着（她知道的事实不能一场里忘掉），只提醒别再主动提
_USED_THIS_SESSION = "（本场已提过）"
# 人批准的观众候选写成的档案行：库里不记是哪位观众定的，提醒她只说"大家定的"，别编出一个具体的人
VIEWER_SOURCE_NOTE = "和观众一起定的，不记得是哪一位，别点名"
# 观众来源的行里，那位观众的名字在渲染时换成的代称（库里的原话不改）
SOURCE_VIEWER_LABEL = "那位观众"


def source_notes(line: ArchiveLine) -> List[str]:
    """档案行来源的注记：观众来源的行注明"和观众一起定的"，她自己说的不加。"""
    return [VIEWER_SOURCE_NOTE] if line.source == "viewer" else []


def _line_texts(line: ArchiveLine) -> Tuple[str, str]:
    """档案行渲染用的标题与原话：观众来源的行把原话里那位观众的名字换成"那位观众"。

    她当场回应时常点名（"阿杰说得对，就叫团子"），原样进简报她就会当众说出是哪一位定的，和"不记得是
    哪一位"的注记自相矛盾，所以原话里的名字在渲染这一刻换掉，库里不改。标题不换：它是运营者亲眼看过
    才批的，留在标题里的名字说的是起的名字是什么，不是谁定的。原话里和观众定下的那个词相同的那一段
    也不换——观众叫"团子"、定下"给小猫取名团子"时，换掉就成了"取名那位观众"。
    """
    title = (line.text or "").strip()
    quote = (line.quote or "").strip()
    if line.source != "viewer" or not line.source_viewer_names:
        return title, quote
    names = line.source_viewer_names
    word = line.viewer_word
    if not word:
        return title, mask_names_in_text(quote, names, replacement=SOURCE_VIEWER_LABEL)
    # 按定下的词切开、各段分别换名字再拼回：词本身原样留着，词以外点到的名字照换
    pieces = [mask_names_in_text(piece, names, replacement=SOURCE_VIEWER_LABEL) for piece in quote.split(word)]
    return title, word.join(pieces)


def format_memory_refs(commitment_ids: Iterable[int]) -> str:
    """把承诺编号写成记忆引用列的文本（形如 ["约#7"]）；空集合写空串。"""
    refs = [render_commitment_number(int(item)) for item in commitment_ids]
    return json.dumps(refs, ensure_ascii=False) if refs else ""


def memory_ref_numbers(memory_refs: Optional[str]) -> Set[int]:
    """记忆引用文本里的承诺编号（形如 ["约#7"]；取不出就空集）。"""
    return _ref_numbers(memory_refs, _COMMITMENT_PREFIX)


def memory_ref_archive_numbers(memory_refs: Optional[str]) -> Set[int]:
    """记忆引用文本里的档案行编号（形如 ["档#41"]；取不出就空集）。"""
    return _ref_numbers(memory_refs, _ARCHIVE_PREFIX)


def _ref_numbers(memory_refs: Optional[str], prefix: str) -> Set[int]:
    """记忆引用文本（JSON 字符串列表）里某一类编号。"""
    if not memory_refs:
        return set()
    try:
        refs = json.loads(memory_refs)
    except json.JSONDecodeError:
        return set()
    if not isinstance(refs, list):
        return set()
    numbers: Set[int] = set()
    for ref in refs:
        if not isinstance(ref, str):
            continue
        head, _sep, tail = ref.partition("#")
        if head.strip().startswith(prefix) and tail.strip().isdigit():
            numbers.add(int(tail.strip()))
    return numbers


def render_commitment_number(commitment_id: int) -> str:
    """承诺编号的写法（约#7）；Planner 用它点名，说话档不用编号。"""
    return f"{_COMMITMENT_PREFIX}#{int(commitment_id)}"


def render_archive_number(archive_line_id: int) -> str:
    """档案行编号的写法（档#41）：简报与线索卡里标着，Planner 用它点名；说话档不用编号。"""
    return f"{_ARCHIVE_PREFIX}#{int(archive_line_id)}"


def _when_word(*, is_previous: bool, spoken: bool) -> str:
    """跨场时间词：Planner 档"上一场""之前"，说话档"上次""之前"。"""
    if is_previous:
        return _SPOKEN_WHEN_PREVIOUS if spoken else _WHEN_PREVIOUS
    return _SPOKEN_WHEN_EARLIER


def render_commitment_text(
    commitment: Commitment,
    *,
    spoken: bool = False,
    is_previous: bool = False,
) -> str:
    """一条承诺的正文：Planner 档给标题（不针对具体的人时加原话），说话档只给标题。"""
    what = (commitment.what or "").strip()
    if spoken:
        return what
    quote = (commitment.quote or "").strip()
    when = _when_word(is_previous=is_previous, spoken=False)
    if quote and not commitment.has_target:
        return f"你{when}说过「{quote}」"
    return what


def _canonical_names_for(lines: Sequence[ArchiveLine]) -> Dict[Tuple[str, str], str]:
    """按场次与行号从旧到新归并同一 ``(game, save_id)`` 的存档名，取最后一次非空的名字。"""
    ordered = sorted(lines, key=lambda item: (int(item.live_session_id), int(item.id)))
    return canonical_save_names((item.game, item.save_id, item.save_name) for item in ordered)


def _format_event_line(
    line: ArchiveLine,
    *,
    current_saves: Mapping[str, GameSave],
    used_ids: AbstractSet[int],
    when_label: str = "",
) -> str:
    """渲染单条普通事件档案行：带编号、标题、原话及必要的来源/存档/时间注记。"""
    title, quote = _line_texts(line)
    if not title:
        return ""
    place = place_of(line.game, line.save_id, current_saves)
    notes = [f"你说过「{quote}」"] if quote else []
    notes.extend(source_notes(line))
    if place is SavePlace.UNSURE and line.game:
        notes.append(f"不确定是 {line.game} 哪个存档里的事")
    if when_label:
        notes.append(when_label)
    head = f"- [{render_archive_number(line.id)}] {title}"
    text = f"{head}（{'；'.join(notes)}）" if notes else head
    return f"{text}{_USED_THIS_SESSION}" if int(line.id) in used_ids else text


def _format_lasting_item(
    line: ArchiveLine,
    *,
    current_saves: Mapping[str, GameSave],
    used_ids: AbstractSet[int],
    bullet: bool = True,
) -> str:
    """渲染单条稳定的事：只给编号与标题（及必要的来源/存档不明注记），不带原话以节省常驻预算。"""
    title, _quote = _line_texts(line)
    if not title:
        return ""
    place = place_of(line.game, line.save_id, current_saves)
    notes = list(source_notes(line))
    if place is SavePlace.UNSURE and line.game:
        notes.append(f"不确定是 {line.game} 哪个存档里的事")
    prefix = "- " if bullet else ""
    head = f"{prefix}[{render_archive_number(line.id)}] {title}"
    text = f"{head}（{'；'.join(notes)}）" if notes else head
    return f"{text}{_USED_THIS_SESSION}" if int(line.id) in used_ids else text


def render_archive_block(
    lines: Sequence[ArchiveLine],
    current_saves: Optional[Mapping[str, GameSave]] = None,
    used_ids: AbstractSet[int] = frozenset(),
) -> List[str]:
    """单块档案行渲染成简报行（档#N、标题、她的原话；别的存档按存档号归并折成一行）。

    每行带档案行编号，要提这件事就把编号写进 use_memory 点名。本场她已经点名用过的行
    （``used_ids``）留着、末尾标"（本场已提过）"：档案行是她知道的事实，撤掉她会在同一场里
    忘掉刚说过的名字和住处；标注只提醒她别再主动提。标注只在某一行第一次被用上时改一次文本。

    按游戏眼下所在的存档分（``current_saves``）：别的存档里的事按 ``(game, save_id)`` 归并名字并
    每个存档折成一行，只说做过几件、细节去回想里查；没记下存档的游戏行照列，注明不确定是哪个存档。
    """
    current = current_saves or {}
    limited = list(lines)[:MAX_BRIEF_ARCHIVE_LINES]
    save_names = _canonical_names_for(limited)
    rendered: List[str] = []
    # 别的存档：(游戏, 存档编号) → [折成的那一行在 rendered 里的位置, 件数]；那一行放在第一件的位置
    folded: Dict[Tuple[str, str], List[int]] = {}
    for line in limited:
        title, _quote = _line_texts(line)
        if not title:
            continue
        place = place_of(line.game, line.save_id, current)
        if place is SavePlace.OTHER and line.game:
            key = (line.game, line.save_id)
            if key not in folded:
                folded[key] = [len(rendered), 0]
                rendered.append("")
            folded[key][1] += 1
            resolved_name = save_names.get(key, line.save_name)
            rendered[folded[key][0]] = _render_other_save_line(
                game=line.game,
                save=GameSave(id=line.save_id, name=resolved_name),
                count=folded[key][1],
                current=current,
            )
            continue
        rendered.append(_format_event_line(line, current_saves=current, used_ids=used_ids))
    return rendered


def _render_world_zone(ctx: BriefRenderContext) -> BriefZoneOutput:
    """【这个世界里】：当前游戏世界的稳定事实——名字压成一两行名字表，其余每条一行、不带原话。"""
    naming, others = select_world_zone_items(ctx)
    if not naming and not others:
        return BriefZoneOutput()
    lines: List[str] = []
    cited: List[int] = []
    for start in range(0, len(naming), WORLD_NAMES_PER_ROW):
        chunk = naming[start : start + WORLD_NAMES_PER_ROW]
        parts: List[str] = []
        for item in chunk:
            formatted = _format_lasting_item(
                item.line, current_saves=ctx.current_saves, used_ids=ctx.used_archive_ids, bullet=False
            )
            if formatted:
                parts.append(formatted)
                cited.append(int(item.line.id))
        if parts:
            prefix = "- 起了名的：" if start == 0 else "- 起了名的（续）："
            lines.append(prefix + "；".join(parts))
    for item in others:
        formatted = _format_lasting_item(
            item.line, current_saves=ctx.current_saves, used_ids=ctx.used_archive_ids, bullet=True
        )
        if formatted:
            lines.append(formatted)
            cited.append(int(item.line.id))
    return BriefZoneOutput(lines=tuple(lines), archive_ids=tuple(cited))


def _render_recent_zone(ctx: BriefRenderContext) -> BriefZoneOutput:
    """【最近几场】：当前游戏世界的普通事件——上一场至多 6 行，再往前两场按最近被提起合计至多 6 行。"""
    prev_items, earlier_items = select_recent_zone_items(ctx)
    if not prev_items and not earlier_items:
        return BriefZoneOutput()
    lines: List[str] = []
    cited: List[int] = []
    if prev_items:
        lines.append(_WHEN_STALE_PREVIOUS_HEADER if ctx.previous_is_stale else _WHEN_PREVIOUS_HEADER)
        for item in prev_items:
            formatted = _format_event_line(item.line, current_saves=ctx.current_saves, used_ids=ctx.used_archive_ids)
            if formatted:
                lines.append(formatted)
                cited.append(int(item.line.id))
    if earlier_items:
        lines.append(_WHEN_EARLIER_HEADER)
        for item in earlier_items:
            formatted = _format_event_line(item.line, current_saves=ctx.current_saves, used_ids=ctx.used_archive_ids)
            if formatted:
                lines.append(formatted)
                cited.append(int(item.line.id))
    return BriefZoneOutput(lines=tuple(lines), archive_ids=tuple(cited))


def _render_room_zone(ctx: BriefRenderContext) -> BriefZoneOutput:
    """【直播间里的事】：不挂游戏的行，换任何游戏存档照样带着（稳定设定在前，最近聊过的事在后）。"""
    ranked = select_room_zone_items(ctx)
    if not ranked:
        return BriefZoneOutput()
    lines: List[str] = []
    cited: List[int] = []
    has_prev = False
    for item in ranked:
        is_prev = is_from_session(item.line, ctx.archive_session_id)
        if is_prev:
            has_prev = True
        if item.lasting:
            formatted = _format_lasting_item(
                item.line, current_saves=ctx.current_saves, used_ids=ctx.used_archive_ids, bullet=True
            )
        else:
            when = _WHEN_PREVIOUS if is_prev and not ctx.previous_is_stale else _WHEN_EARLIER
            formatted = _format_event_line(
                item.line, current_saves=ctx.current_saves, used_ids=ctx.used_archive_ids, when_label=when
            )
        if formatted:
            lines.append(formatted)
            cited.append(int(item.line.id))
    if lines and has_prev and ctx.unsaved_games:
        for game in ctx.unsaved_games:
            lines.append(f"（那场玩 {game} 时还没记存档，上面游戏里的事未必是现在这个存档的）")
    return BriefZoneOutput(lines=tuple(lines), archive_ids=tuple(cited))


def _render_commitments_zone(ctx: BriefRenderContext) -> BriefZoneOutput:
    """【还惦记的事】：最近三场内提起过的未了结承诺（想做再做的事），更早搁下的只给一句总数。"""
    pending: List[str] = []
    for commitment in ctx.commitments:
        what = (commitment.what or "").strip()
        quote = (commitment.quote or "").strip()
        ref_sid = effective_mentioned_session_id(commitment)
        is_previous = (
            not ctx.previous_is_stale
            and ctx.archive_session_id is not None
            and ref_sid is not None
            and int(ref_sid) == int(ctx.archive_session_id)
        )
        when = _when_word(is_previous=is_previous, spoken=False)
        notes: List[str] = []
        if commitment.game:
            notes.append(f"{commitment.game}·{when}")
            if place_of(commitment.game, commitment.save_id, ctx.current_saves) is SavePlace.UNSURE:
                notes.append("不确定是哪个存档")
        else:
            notes.append(when)
        body = (
            f"- [{render_commitment_number(commitment.id)}] 「{quote}」"
            if quote
            else f"- [{render_commitment_number(commitment.id)}] {what}"
        )
        pending.append(f"{body}（{'；'.join(notes)}）")
    extra = max(0, int(ctx.earlier_commitment_count))
    if not pending and not extra:
        return BriefZoneOutput()
    lines = [*pending]
    if extra:
        lines.append(f"- 另有 {extra} 件搁了很久，可以用 memory_recall 查")
    lines.append(_COMMITMENTS_HINT)
    return BriefZoneOutput(lines=tuple(lines))


WORLD_ZONE = BriefZoneSpec(key="world", header=_WORLD_ZONE_HEADER, render=_render_world_zone)
RECENT_ZONE = BriefZoneSpec(key="recent", header=_RECENT_ZONE_HEADER, render=_render_recent_zone)
ROOM_ZONE = BriefZoneSpec(key="room", header=_ROOM_ZONE_HEADER, render=_render_room_zone)
COMMITMENTS_ZONE = BriefZoneSpec(key="commitments", header=_COMMITMENTS_HEADER, render=_render_commitments_zone)

# 常驻简报的有序分区表：新增分区（如后续表达层"关于她自己"区）只需在此追加一项，不动下方渲染骨架
BRIEF_ZONES: Tuple[BriefZoneSpec, ...] = (
    WORLD_ZONE,
    RECENT_ZONE,
    ROOM_ZONE,
    COMMITMENTS_ZONE,
)


def _coerce_snapshot_lines(
    archive_lines: Sequence[ArchiveLine],
    snapshot_lines: Sequence[SnapshotLine],
) -> List[SnapshotLine]:
    """优先采用带稳定/再提起标注的 ``snapshot_lines``；仅传 ``archive_lines`` 时按普通事件行包装兼容。"""
    if snapshot_lines:
        return list(snapshot_lines)
    return [SnapshotLine(line=line, lasting=False, seen_at_ms=int(line.live_session_id)) for line in archive_lines]


def _build_render_context(
    *,
    archive_lines: Sequence[ArchiveLine] = (),
    snapshot_lines: Sequence[SnapshotLine] = (),
    commitments: Sequence[Commitment] = (),
    other_save_commitments: Sequence[Commitment] = (),
    previous_session_id: Optional[int] = None,
    previous_is_stale: bool = False,
    earliest_recent_ms: int = 0,
    earlier_commitment_count: int = 0,
    current_saves: Optional[Mapping[str, GameSave]] = None,
    unsaved_games: Sequence[str] = (),
    used_archive_ids: AbstractSet[int] = frozenset(),
) -> BriefRenderContext:
    """把简报原始输入归一化为各分区共享的只读上下文（同句台词既写档案又记承诺时只留承诺）。"""
    listed = _listed_commitments(commitments)
    promised_lines = {int(item.evidence_chat_id) for item in listed if item.evidence_chat_id is not None}
    raw_items = _coerce_snapshot_lines(archive_lines, snapshot_lines)
    filtered = [
        item
        for item in raw_items
        if item.line.evidence_chat_id is None or int(item.line.evidence_chat_id) not in promised_lines
    ]
    archive_session = previous_session_id
    if archive_session is None and filtered:
        archive_session = max(int(item.line.live_session_id) for item in filtered)
    return BriefRenderContext(
        snapshot_lines=tuple(filtered),
        commitments=tuple(listed),
        earlier_commitment_count=int(earlier_commitment_count),
        archive_session_id=archive_session,
        previous_is_stale=bool(previous_is_stale),
        earliest_recent_ms=int(earliest_recent_ms),
        current_saves=dict(current_saves or {}),
        unsaved_games=tuple(unsaved_games),
        used_archive_ids=used_archive_ids,
        other_save_commitments=tuple(other_save_commitments),
    )


def _render_folded_other_saves(ctx: BriefRenderContext) -> List[str]:
    """别的存档里的记忆按 ``(game, save_id)`` 全简报归并成每个存档一行，名字取最后一次非空记录。"""
    if not ctx.current_saves:
        return []
    name_records: List[Tuple[Optional[str], str, str]] = [
        (item.line.game, item.line.save_id, item.line.save_name)
        for item in sorted(ctx.snapshot_lines, key=lambda s: (int(s.line.live_session_id), int(s.line.id)))
    ]
    for item in sorted(ctx.other_save_commitments, key=lambda c: (int(c.created_session_id or 0), int(c.id))):
        name_records.append((item.game, item.save_id, item.save_name))
    save_names = canonical_save_names(name_records)
    counts: Dict[Tuple[str, str], int] = {}
    order: List[Tuple[str, str]] = []
    fallback_names: Dict[Tuple[str, str], str] = {}
    for item in ctx.snapshot_lines:
        line = item.line
        title, _quote = _line_texts(line)
        if not title or not line.game:
            continue
        if place_of(line.game, line.save_id, ctx.current_saves) is not SavePlace.OTHER:
            continue
        key = (line.game, line.save_id)
        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1
        fallback_names[key] = line.save_name
    for commitment in ctx.other_save_commitments:
        if not (commitment.what or "").strip() or not commitment.game:
            continue
        if place_of(commitment.game, commitment.save_id, ctx.current_saves) is not SavePlace.OTHER:
            continue
        key = (commitment.game, commitment.save_id)
        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1
        fallback_names.setdefault(key, commitment.save_name)
    return [
        _render_other_save_line(
            game=game,
            save=GameSave(id=save_id, name=save_names.get((game, save_id), fallback_names[(game, save_id)])),
            count=counts[(game, save_id)],
            current=ctx.current_saves,
        )
        for game, save_id in order
    ]


def brief_archive_ids(
    archive_lines: Sequence[ArchiveLine] = (),
    commitments: Sequence[Commitment] = (),
    current_saves: Optional[Mapping[str, GameSave]] = None,
    *,
    snapshot_lines: Sequence[SnapshotLine] = (),
    previous_session_id: Optional[int] = None,
    previous_is_stale: bool = False,
    earliest_recent_ms: int = 0,
    zones: Sequence[BriefZoneSpec] = BRIEF_ZONES,
) -> List[int]:
    """常驻简报里标着编号的档案行（Planner 点名的合法名单之一）：与 ``render_brief`` 跑同一套分区表。"""
    ctx = _build_render_context(
        archive_lines=archive_lines,
        snapshot_lines=snapshot_lines,
        commitments=commitments,
        previous_session_id=previous_session_id,
        previous_is_stale=previous_is_stale,
        earliest_recent_ms=earliest_recent_ms,
        current_saves=current_saves,
    )
    cited: List[int] = []
    for zone in zones:
        cited.extend(zone.render(ctx).archive_ids)
    return cited


def _listed_commitments(commitments: Sequence[Commitment]) -> List[Commitment]:
    """简报承诺段列出的那几条：至多 ``MAX_BRIEF_COMMITMENTS`` 条，没有标题的不列。"""
    return [item for item in list(commitments)[:MAX_BRIEF_COMMITMENTS] if (item.what or "").strip()]


def _render_other_save_line(*, game: str, save: GameSave, count: int, current: Mapping[str, GameSave]) -> str:
    """别的存档里的事折成的一行：在哪个存档、做过几件、眼下开着哪个存档、细节去哪查。"""
    now = current.get(game)
    where_now = f"现在开着「{now.label}」" if now is not None and now.known else f"现在还没进 {game} 的存档"
    return (
        f"- 还在 {game}「{save.label}」里做过 {count} 件事（{where_now}，那边的建筑和进度这里没有；"
        "要细节用 memory_recall 查）"
    )


def render_save_heads(current_saves: Mapping[str, GameSave]) -> List[str]:
    """简报开头的"眼下在哪个存档"：每个分存档的游戏一行；还没进存档的提醒别把游戏进度当成眼前的事。"""
    heads: List[str] = []
    for game in sorted(current_saves):
        save = current_saves[game]
        if save.known:
            heads.append(f"眼下 {game} 开着存档「{save.label}」。")
        else:
            heads.append(f"{game} 眼下还没进存档，先别把游戏里的进度当成眼前的事。")
    return heads


def render_brief(
    *,
    archive_lines: Sequence[ArchiveLine] = (),
    snapshot_lines: Sequence[SnapshotLine] = (),
    commitments: Sequence[Commitment] = (),
    other_save_commitments: Sequence[Commitment] = (),
    previous_session_id: Optional[int] = None,
    previous_is_stale: bool = False,
    earliest_recent_ms: int = 0,
    earlier_commitment_count: int = 0,
    current_saves: Optional[Mapping[str, GameSave]] = None,
    unsaved_games: Sequence[str] = (),
    used_archive_ids: AbstractSet[int] = frozenset(),
    zones: Sequence[BriefZoneSpec] = BRIEF_ZONES,
) -> str:
    """Planner 系统提示词末尾的常驻简报；所有分区与别的存档折叠行都空时返回空串（整段省略）。

    按 ``zones`` 定义的分区顺序渲染（默认依次为【这个世界里】【最近几场】【直播间里的事】【还惦记的事】），
    每个分区各自计额、空区自动省略；别的存档里的记忆按 ``(game, save_id)`` 归并名字并折成每个存档一行。
    存档没换、表没变时输出逐字节不变，前缀缓存照旧命中。
    """
    ctx = _build_render_context(
        archive_lines=archive_lines,
        snapshot_lines=snapshot_lines,
        commitments=commitments,
        other_save_commitments=other_save_commitments,
        previous_session_id=previous_session_id,
        previous_is_stale=previous_is_stale,
        earliest_recent_ms=earliest_recent_ms,
        earlier_commitment_count=earlier_commitment_count,
        current_saves=current_saves,
        unsaved_games=unsaved_games,
        used_archive_ids=used_archive_ids,
    )
    folded_other_saves = _render_folded_other_saves(ctx)
    rendered_zones: List[str] = []
    for zone in zones:
        output = zone.render(ctx)
        if output.lines:
            rendered_zones.append("\n".join([zone.header, *output.lines]) if zone.header else "\n".join(output.lines))
    if not folded_other_saves and not rendered_zones:
        return ""
    return "\n".join([_BRIEF_HEADER, *render_save_heads(ctx.current_saves), *folded_other_saves, *rendered_zones])


def render_cue_header() -> str:
    """参考段里线索块的抬头（有卡片或提示行时才整块出现）。"""
    return _CUE_HEADER


def render_game_card(commitment: Commitment, *, game: str, unsure_save: bool = False) -> str:
    """游戏承诺卡：挂在这个游戏上、不针对具体观众的承诺；主播正在玩这个游戏时推。

    卡面不写"现在开着"：线索推送只认"正在玩"（有进行中的委派任务或刚有游戏事件），
    但决策层自己看得见眼前现场，卡面替它断言反而容易说错。别的存档里答应的不推这张卡；
    ``unsure_save``：这个游戏分存档、但这件承诺没记下是哪个存档，卡面让她先对一对眼前的世界。
    """
    quote = (commitment.quote or "").strip() or (commitment.what or "").strip()
    unsure = "不确定是在哪个存档里答应的，先对一对眼前的世界；" if unsure_save else ""
    return (
        f"[{render_commitment_number(commitment.id)}·{game}] "
        f"你之前说过「{quote}」，截至上一场还没做。这件承诺挂在你玩的游戏 {game} 上；{unsure}时机对再提。"
    )


def render_archive_card(line: ArchiveLine, *, why: str, is_previous: bool = False, note: str = "") -> str:
    """档案卡（被动想起）：编号、时间词、标题与原话，末尾写为什么想起来。

    ``note`` 是"在哪个存档里"一类的补充（见 ``saves.place_note``）；``why`` 写触发它的那件事
    （弹幕提到了某个词、她刚说到、走到了某个地点）。
    """
    when = _when_word(is_previous=is_previous, spoken=False)
    # 观众来源的行：标题与原话里那位观众的名字换成"那位观众"
    title, quote = _line_texts(line)
    notes = [f"你说过「{quote}」"] if quote else []
    notes.extend(source_notes(line))
    if note:
        notes.append(note)
    body = f"{title}（{'；'.join(notes)}）" if notes else title
    return f"[{render_archive_number(line.id)}·{when}] {body}。想起它是因为{why}。"


def render_recalled_commitment_card(commitment: Commitment, *, why: str, unsure_save: bool = False) -> str:
    """按意思想起的承诺卡：不针对具体观众的未了结承诺，给原话，写为什么想起来。

    承诺做没做是下播整理时判的，本场做了的还没记上——写"截至上一场还没做"，不替她断言眼下没做。
    """
    quote = (commitment.quote or "").strip() or (commitment.what or "").strip()
    unsure = "不确定是在哪个存档里答应的，先对一对眼前的世界；" if unsure_save else ""
    return (
        f"[{render_commitment_number(commitment.id)}] 你之前说过「{quote}」，截至上一场还没做。"
        f"想起它是因为{why}；{unsure}时机对再提。"
    )


def render_joke_card(joke: str, *, message_id: str = "") -> str:
    """老梗卡：观众提到了老梗栏里的梗。梗本身已在老梗栏里，卡面只提醒"有人提了"。"""
    who = f"弹幕 {message_id} " if message_id else "观众"
    return f"[老梗] {who}提到了「{joke}」：老梗栏里有这个梗，接不接看眼前。"


def render_viewer_card(
    commitment: Commitment,
    *,
    viewer_label: str,
    message_id: str,
    is_previous: bool = False,
    place: str = "",
) -> str:
    """对某位观众的承诺卡：只给动作标题，不提当时的缘由与原话。

    ``place`` 是挂了分存档游戏的承诺"在哪个存档里答应的"（见 ``saves.place_note``）：ta 开口时
    照推，但她得知道那件事是不是在眼下这个世界里。

    ``is_previous`` 按承诺登记在不在"上一场"给：本场与更早的都写"之前"——一条三场前
    的承诺说成上一场，主播会照着卡面在观众面前把时间说错。
    """
    when = _when_word(is_previous=is_previous, spoken=False)
    target = f"{viewer_label}（弹幕 {message_id}）在" if message_id else f"{viewer_label} 在"
    detail = f"{when}；{place}" if place else when
    return (
        f"[{render_commitment_number(commitment.id)}·对{viewer_label}] "
        f"{target}。你答应过 ta：{(commitment.what or '').strip()}（{detail}）。"
        "只提答应的事，别提当时的缘由。"
    )


def render_player_card(
    player: "GamePlayer",
    history: Sequence["PlayerInteraction"],
    *,
    previous_session_id: Optional[int] = None,
    promises: Sequence[Tuple[Commitment, str]] = (),
    statements: Sequence[Tuple[str, str]] = (),
) -> str:
    """游戏玩家开口时的卡："这位是谁、和你有过什么"；以前没有往来、没答应过他什么、他也没说过什么的返回空串。

    卡面只写和她发生过的互动事实与他当时的名字：打过几次招呼、送过什么、切磋输赢、一起做过什么；
    不写他什么时间段上线、不写私聊。最近一次往来在上一场时写"上一场"，其余写"之前"。
    ``statements`` 是人批准过的他的自述（标题, 在哪个存档说的说明），写成"他跟你说过"：没批准的不写，
    自述也不进常驻简报，只在他找她时由这张卡提起。
    ``promises`` 是答应过他、还没了结的事（承诺, 在哪个存档答应的说明），带承诺编号写在卡尾：
    对玩家的承诺不进常驻简报，只在他露面时由这张卡提醒。
    """
    promise_text = ""
    if promises:
        items = []
        for commitment, place in promises:
            note = (
                f"{render_commitment_number(commitment.id)}；{place}"
                if place
                else render_commitment_number(commitment.id)
            )
            items.append(f"{(commitment.what or '').strip()}（{note}）")
        promise_text = f"你答应过他：{'；'.join(items)}。"
    # 他自己说过的事：别的存档里说的照样提，但写明是在哪个存档说的（那座房子不一定在眼下这个世界里）
    said = "；".join(f"{title}（{place}）" if place else title for title, place in statements if title.strip())
    said_text = f"他跟你说过：{said}。" if said else ""
    if not history and not promise_text and not said_text:
        return ""
    greets = [item for item in history if item.kind == "greet"]
    gifts = [str(item.fact.get("said") or "").strip() for item in history if item.kind == "gift"]
    spars = [str(item.fact.get("result") or "") for item in history if item.kind == "spar"]
    together = [str(item.fact.get("title") or "").strip() for item in history if item.kind in ("together", "helped")]
    parts: List[str] = []
    if greets:
        times = sum(int(item.fact.get("count") or 0) for item in greets)
        parts.append(f"冲你打过 {times} 次招呼" if times > 1 else "冲你打过招呼")
    gifts = [item for item in gifts if item][:2]
    if gifts:
        parts.append(f"送过你东西（{'；'.join(gifts)}）")
    if spars:
        won, lost = spars.count("won"), spars.count("lost")
        score = "，".join(part for part in (f"你赢 {won} 局" if won else "", f"你输 {lost} 局" if lost else "") if part)
        parts.append(f"和你切磋过 {len(spars)} 场" + (f"（{score}）" if score else ""))
    together = [item for item in together if item][:2]
    if together:
        parts.append(f"一起做过：{'；'.join(together)}")
    if not parts and not promise_text and not said_text:
        return ""
    past = ""
    if parts:
        latest = history[0].live_session_id
        when = "上一场" if previous_session_id is not None and latest == previous_session_id else "之前"
        past = f"{when}他{'、'.join(parts)}。"
    former = player.former_names()
    name = f"{player.name}（以前叫 {'、'.join(former[-2:])}）" if former else player.name
    return f"[玩家·{player.name}] {name} 在游戏里找你了。{past}{said_text}{promise_text}认得出他就好，别念清单。"


def render_presence_line(viewers: Sequence[Tuple[str, Optional[int]]], *, now_ms: int) -> str:
    """老观众在场一行：名字加档位词，最多 6 人，多的折成"等 N 位"。

    viewers 是（昵称, 本场之前最后一次出现的时刻）序列；档位按距今三天分界，
    没带上次时刻的按"有阵子没来"处理（宁可说得含糊，不编一个具体时长）。
    """
    entries = list(viewers)
    if not entries:
        return ""
    shown = entries[:MAX_PRESENCE_VIEWERS]
    parts = [f"{name}（{_presence_tier(prev_seen_ms, now_ms)}）" for name, prev_seen_ms in shown]
    rest = len(entries) - len(shown)
    text = "、".join(parts)
    if rest > 0:
        text += f"，等 {rest} 位"
    return _PRESENCE_HEADER + text


def _presence_tier(prev_seen_ms: Optional[int], now_ms: int) -> str:
    """档位词：三天以内来过写"前几天刚来过"，其余（含没有上次时刻）写"有阵子没来"。"""
    if prev_seen_ms is None:
        return "有阵子没来"
    elapsed = int(now_ms) - int(prev_seen_ms)
    return "前几天刚来过" if 0 <= elapsed <= PRESENCE_RECENT_MS else "有阵子没来"


def render_forget_notice(*, message_id: str) -> str:
    """回执行：本批有人发了"忘了我"。用弹幕编号指人，不写名字、不复述 ta 说过的事。"""
    return (
        f'【已按"忘了我"处理】弹幕 {message_id} 的发送者：从现在起不再记录 ta，'
        'ta 的记录在下播后删除；删除前 ta 发"开玩笑的""算了"可以撤回。'
        "答应一声就好，别叫 ta 的名字，别复述 ta 说过的事。"
    )


def render_retract_notice(*, message_id: str) -> str:
    """回执行：本批有人撤回了"忘了我"。"""
    return f'【已撤回"忘了我"】弹幕 {message_id} 的发送者：删除取消，照常记着 ta（撤回前那几条弹幕没记下）。'


def render_cue_block(
    *,
    cards: Sequence[str] = (),
    presence: str = "",
    notices: Sequence[str] = (),
) -> str:
    """参考段的线索块：卡片、老观众在场一行、回执行按序拼起来；全空时返回空串。"""
    body = [line for line in [*cards, presence, *notices] if line]
    if not body:
        return ""
    return _CUE_HEADER + "\n" + "\n".join(body)


def render_spoken_archive(line: ArchiveLine, *, is_previous: bool = False) -> str:
    """说话档：Planner 点名的那一行档案，交到 Replyer 手里（只给标题，不含编号与原话）。"""
    # 观众来源的行：标题里那位观众的名字换成"那位观众"
    title, _ = _line_texts(line)
    if not title:
        return ""
    when = _when_word(is_previous=is_previous, spoken=True)
    # 说话档只给标题，观众来源的再注一句：说"大家定的"，别编出一个具体的人
    source = "（和观众一起定的，别说成是哪一位定的）" if source_notes(line) else ""
    return f"{_SPOKEN_HEADER}\n- {when}的事：{title}{source}。"


def render_spoken_memory(*, target_label: str, commitment: Commitment, is_previous: bool = False) -> str:
    """说话档：Planner 点名的那一条，交到 Replyer 手里（至多一条，不含编号与原话）。"""
    what = render_commitment_text(commitment, spoken=True)
    if not what:
        return ""
    when = _when_word(is_previous=is_previous, spoken=True)
    return f"{_SPOKEN_HEADER}\n- 你{when}答应过{target_label}：{what}。"
