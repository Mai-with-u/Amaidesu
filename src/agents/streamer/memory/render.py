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
from typing import AbstractSet, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from src.modules.storage.models.memory import ArchiveLine, Commitment
from src.modules.types.game_save import GameSave

from .saves import SavePlace, place_of

__all__ = [
    "MAX_BRIEF_ARCHIVE_LINES",
    "brief_archive_ids",
    "format_memory_refs",
    "memory_ref_archive_numbers",
    "memory_ref_numbers",
    "MAX_BRIEF_COMMITMENTS",
    "MAX_PRESENCE_VIEWERS",
    "PRESENCE_RECENT_MS",
    "render_archive_block",
    "render_archive_number",
    "render_brief",
    "render_commitment_number",
    "render_commitment_text",
    "render_cue_block",
    "render_cue_header",
    "render_forget_notice",
    "render_game_card",
    "render_presence_line",
    "render_retract_notice",
    "render_save_heads",
    "render_spoken_archive",
    "render_spoken_memory",
    "render_viewer_card",
]

# 常驻简报：档案行最多几条（一份快照的上限，整理写入用同一个数）、不针对具体观众的承诺最多几条
MAX_BRIEF_ARCHIVE_LINES = 12
MAX_BRIEF_COMMITMENTS = 5
# 老观众在场一行最多几个人，超出的折成"等 N 位"
MAX_PRESENCE_VIEWERS = 6
# 档位词分界：三天内来过写"前几天刚来过"，否则写"有阵子没来"
PRESENCE_RECENT_MS = 3 * 24 * 60 * 60 * 1000

# 两档共用的抬头与提示
_BRIEF_HEADER = "# 她记得的事（内部参考；以眼前为准，用不上就不提）"
_CUE_HEADER = (
    "【这一批想起来的事】"
    "（代码按线索找的；不提也行。要用就把编号写进 streamer_reply 的 use_memory，一次一条；"
    "一句台词最多带一件旧事）"
)
_COMMITMENTS_HEADER = "还没兑现、你当众说过的事："
# 承诺段末尾固定的一行：做不到的事给一条收场的路（当众说清楚，下播整理才认得出是了结），
# 也防她为了交差说"画好了"。写"确实做不到"，免得她把做得到的事早早放弃。逐字节不变，前缀缓存照旧命中
_COMMITMENTS_HINT = "（确实做不到的，找个合适的时机当众说清楚；没做的别说成做完了）"
_PRESENCE_HEADER = "老观众在场（只知道 ta 常来、多久没来；ta 的往事你不记得，别编，想知道就问）："
_SPOKEN_HEADER = "## 你想起来的事（只用这里写到的，不补细节；不说日期和次数）"

# 跨场只用这两个时间词
_WHEN_PREVIOUS = "上一场"
_WHEN_EARLIER = "之前"
# 简报里档案快照的两块抬头：上一场新写的、从之前几场接住仍然成立的（只用"上一场""之前"两个时间词）
_WHEN_PREVIOUS_HEADER = "上一场："
_WHEN_EARLIER_HEADER = "之前："
_SPOKEN_WHEN_PREVIOUS = "上次"
_SPOKEN_WHEN_EARLIER = "之前"
# 档案行与承诺的编号前缀（与她"说过的话""答应过的事"对应）
_ARCHIVE_PREFIX = "档"
_COMMITMENT_PREFIX = "约"
# 本场她已经点名用过的档案行：行留着（她知道的事实不能一场里忘掉），只提醒别再主动提
_USED_THIS_SESSION = "（本场已提过）"


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
    """一条承诺的正文：Planner 档给标题（不针对具体观众时加原话），说话档只给标题。"""
    what = (commitment.what or "").strip()
    if spoken:
        return what
    quote = (commitment.quote or "").strip()
    when = _when_word(is_previous=is_previous, spoken=False)
    if quote and commitment.viewer_id is None:
        return f"你{when}说过「{quote}」"
    return what


def render_archive_block(
    lines: Sequence[ArchiveLine],
    current_saves: Optional[Mapping[str, GameSave]] = None,
    used_ids: AbstractSet[int] = frozenset(),
) -> List[str]:
    """上一场档案渲染成简报行（档#N、标题、她的原话）。

    每行带档案行编号，要提这件事就把编号写进 use_memory 点名。本场她已经点名用过的行
    （``used_ids``）留着、末尾标"（本场已提过）"：档案行是她知道的事实，撤掉她会在同一场里
    忘掉刚说过的名字和住处；标注只提醒她别再主动提。标注只在某一行第一次被用上时改一次文本。

    按游戏眼下所在的存档分（``current_saves``）：别的存档里的事每个存档折成一行，只说做过几件、
    细节去回想里查——逐行列出来，她会当成眼前这个世界里的建筑和进度；没记下存档的游戏行照列，
    注明不确定是哪个存档。
    """
    current = current_saves or {}
    rendered: List[str] = []
    # 别的存档：(游戏, 存档编号) → [折成的那一行在 rendered 里的位置, 件数]；那一行放在第一件的位置
    folded: Dict[Tuple[str, str], List[int]] = {}
    for line in list(lines)[:MAX_BRIEF_ARCHIVE_LINES]:
        title = (line.text or "").strip()
        quote = (line.quote or "").strip()
        if not title:
            continue
        place = place_of(line.game, line.save_id, current)
        if place is SavePlace.OTHER and line.game:
            key = (line.game, line.save_id)
            if key not in folded:
                folded[key] = [len(rendered), 0]
                rendered.append("")
            folded[key][1] += 1
            rendered[folded[key][0]] = _render_other_save_line(
                game=line.game,
                save=GameSave(id=line.save_id, name=line.save_name),
                count=folded[key][1],
                current=current,
            )
            continue
        notes = [f"你说过「{quote}」"] if quote else []
        if place is SavePlace.UNSURE:
            notes.append(f"不确定是 {line.game} 哪个存档里的事")
        head = f"- [{render_archive_number(line.id)}] {title}"
        text = f"{head}（{'；'.join(notes)}）" if notes else head
        rendered.append(f"{text}{_USED_THIS_SESSION}" if int(line.id) in used_ids else text)
    return rendered


def brief_archive_ids(
    archive_lines: Sequence[ArchiveLine],
    commitments: Sequence[Commitment] = (),
    current_saves: Optional[Mapping[str, GameSave]] = None,
) -> List[int]:
    """常驻简报里标着编号的档案行（Planner 点名的合法名单之一）：与 ``render_brief`` 同一口径。

    别的存档里的行折成一行、不编号；和列出的承诺出自同一句台词的行不列。
    """
    current = current_saves or {}
    return [
        int(line.id)
        for line in _shown_archive_lines(archive_lines, _listed_commitments(commitments))
        if (line.text or "").strip() and place_of(line.game, line.save_id, current) is not SavePlace.OTHER
    ]


def _listed_commitments(commitments: Sequence[Commitment]) -> List[Commitment]:
    """简报承诺段列出的那几条：至多 ``MAX_BRIEF_COMMITMENTS`` 条，没有标题的不列。"""
    return [item for item in list(commitments)[:MAX_BRIEF_COMMITMENTS] if (item.what or "").strip()]


def _shown_archive_lines(archive_lines: Sequence[ArchiveLine], listed: Sequence[Commitment]) -> List[ArchiveLine]:
    """简报里列出的档案行：与列出的承诺出自同一句台词的只留承诺，至多 ``MAX_BRIEF_ARCHIVE_LINES`` 行。

    同一件事在简报里出现两遍，她会当成两件事、一整场围着它转。
    """
    promised_lines = {int(item.evidence_chat_id) for item in listed if item.evidence_chat_id is not None}
    return [
        line
        for line in archive_lines
        if line.evidence_chat_id is None or int(line.evidence_chat_id) not in promised_lines
    ][:MAX_BRIEF_ARCHIVE_LINES]


def _is_from(line: ArchiveLine, archive_session: Optional[int]) -> bool:
    """这一行是不是上一场新写的（档案行所在场次就是写下它的那一场）。"""
    return archive_session is None or int(line.live_session_id) == int(archive_session)


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
    commitments: Sequence[Commitment] = (),
    previous_session_id: Optional[int] = None,
    earlier_commitment_count: int = 0,
    current_saves: Optional[Mapping[str, GameSave]] = None,
    unsaved_games: Sequence[str] = (),
    used_archive_ids: AbstractSet[int] = frozenset(),
) -> str:
    """Planner 系统提示词末尾的常驻简报；两段都空时返回空串（整段省略）。

    上一场 = 最近一个已整理、有档案行的正式场；承诺只放不针对具体观众、不挂游戏的
    open 条，最老的在前至多 5 条，更多的折成一行指向回想工具。

    ``current_saves`` 是分存档的游戏眼下所在的存档：简报开头写明，上一场的游戏行按它分
    （见 :func:`render_archive_block`）。``unsaved_games`` 是上一场玩过、但那时还没记存档的
    游戏：上一场那几行没标游戏，分不出是不是眼下这个世界的事，块尾注一句。存档没换、表没变时
    输出逐字节不变（前缀缓存照旧命中）。

    档案行来自上一场的快照：上一场新写的行列在"上一场："下，从之前几场接住、仍然成立的行
    列在"之前："下，按每行写在哪一场分（``previous_session_id`` 没给时取第一行的场次）。
    ``used_archive_ids`` 是本场她已经点名用过的档案行，这些行末尾标"（本场已提过）"。
    """
    sections: List[str] = []
    listed = _listed_commitments(commitments)
    shown_lines = _shown_archive_lines(archive_lines, listed)
    archive_session = previous_session_id
    if archive_session is None and shown_lines:
        archive_session = shown_lines[0].live_session_id
    recent = [line for line in shown_lines if _is_from(line, archive_session)]
    earlier = [line for line in shown_lines if not _is_from(line, archive_session)]
    recent_block = [_WHEN_PREVIOUS_HEADER, *render_archive_block(recent, current_saves, used_archive_ids)]
    # 上一场还没记存档的游戏：上一场那几行没标游戏的里可能有它的事，分不出是不是眼下这个世界
    if len(recent_block) > 1 and unsaved_games and any(not line.game for line in recent):
        for game in unsaved_games:
            recent_block.append(f"（那场玩 {game} 时还没记存档，上面游戏里的事未必是现在这个存档的）")
    blocks = [recent_block, [_WHEN_EARLIER_HEADER, *render_archive_block(earlier, current_saves, used_archive_ids)]]
    sections.extend("\n".join(block) for block in blocks if len(block) > 1)
    pending: List[str] = []
    for commitment in listed:
        what = (commitment.what or "").strip()
        quote = (commitment.quote or "").strip()
        is_previous = (
            previous_session_id is not None
            and commitment.created_session_id is not None
            and int(commitment.created_session_id) == int(previous_session_id)
        )
        when = _when_word(is_previous=is_previous, spoken=False)
        body = (
            f"- [{render_commitment_number(commitment.id)}] 「{quote}」"
            if quote
            else f"- [{render_commitment_number(commitment.id)}] {what}"
        )
        pending.append(f"{body}（{when}）")
    extra = max(0, int(earlier_commitment_count))
    if pending or extra:
        block = [_COMMITMENTS_HEADER, *pending]
        if extra:
            block.append(f"- 另有 {extra} 件，可以用 memory_recall 查")
        block.append(_COMMITMENTS_HINT)
        sections.append("\n".join(block))
    if not sections:
        return ""
    return "\n".join([_BRIEF_HEADER, *render_save_heads(current_saves or {}), *sections])


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
    title = (line.text or "").strip()
    if not title:
        return ""
    when = _when_word(is_previous=is_previous, spoken=True)
    return f"{_SPOKEN_HEADER}\n- {when}的事：{title}。"


def render_spoken_memory(*, target_label: str, commitment: Commitment, is_previous: bool = False) -> str:
    """说话档：Planner 点名的那一条，交到 Replyer 手里（至多一条，不含编号与原话）。"""
    what = render_commitment_text(commitment, spoken=True)
    if not what:
        return ""
    when = _when_word(is_previous=is_previous, spoken=True)
    return f"{_SPOKEN_HEADER}\n- 你{when}答应过{target_label}：{what}。"
