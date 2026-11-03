"""记忆渲染：把承诺与场次档案渲染成两档写法（Planner 档 / 说话档）。

所有通路共用一个渲染函数，两档的差别只有三处：

| 成分 | Planner 档 | 说话档（Replyer） |
|---|---|---|
| 编号 | 有（约#7、档#41），用于点名 | 没有 |
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
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from src.modules.storage.models.memory import ArchiveLine, Commitment

__all__ = [
    "MAX_BRIEF_ARCHIVE_LINES",
    "format_memory_refs",
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
    "render_spoken_memory",
    "render_viewer_card",
]

# 常驻简报：上一场档案行最多几条、不针对具体观众的承诺最多几条
MAX_BRIEF_ARCHIVE_LINES = 8
MAX_BRIEF_COMMITMENTS = 5
# 老观众在场一行最多几个人，超出的折成"等 N 位"
MAX_PRESENCE_VIEWERS = 6
# 档位词分界：三天内来过写"前几天刚来过"，否则写"有阵子没来"
PRESENCE_RECENT_MS = 3 * 24 * 60 * 60 * 1000

# 两档共用的抬头与提示
_BRIEF_HEADER = "# 她记得的事（内部参考；以眼前为准，用不上就不提）"
_CUE_HEADER = (
    "【这一批想起来的事】"
    "（代码按线索找的；不提也行。要用就把编号写进 reply 的 use_memory，一次一条；"
    "一句台词最多带一件旧事）"
)
_PRESENCE_HEADER = "老观众在场（只知道 ta 常来、多久没来；ta 的往事你不记得，别编，想知道就问）："
_SPOKEN_HEADER = "## 你想起来的事（只用这里写到的，不补细节；不说日期和次数）"

# 跨场只用这两个时间词
_WHEN_PREVIOUS = "上一场"
_WHEN_EARLIER = "之前"
_SPOKEN_WHEN_PREVIOUS = "上次"
_SPOKEN_WHEN_EARLIER = "之前"
# 档案行与承诺的编号前缀（与她"说过的话""答应过的事"对应）
_ARCHIVE_PREFIX = "档"
_COMMITMENT_PREFIX = "约"


def format_memory_refs(commitment_ids: Iterable[int]) -> str:
    """把承诺编号写成记忆引用列的文本（形如 ["约#7"]）；空集合写空串。"""
    refs = [render_commitment_number(int(item)) for item in commitment_ids]
    return json.dumps(refs, ensure_ascii=False) if refs else ""


def memory_ref_numbers(memory_refs: Optional[str]) -> Set[int]:
    """记忆引用文本里的承诺编号（形如 ["约#7"]；取不出就空集）。"""
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
        if head.strip().startswith(_COMMITMENT_PREFIX) and tail.strip().isdigit():
            numbers.add(int(tail.strip()))
    return numbers


def render_commitment_number(commitment_id: int) -> str:
    """承诺编号的写法（约#7）；Planner 用它点名，说话档不用编号。"""
    return f"{_COMMITMENT_PREFIX}#{int(commitment_id)}"


def render_archive_number(archive_line_id: int) -> str:
    """档案行编号的写法（档#41）。"""
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


def render_archive_block(lines: Sequence[ArchiveLine]) -> List[str]:
    """上一场档案渲染成简报行（编号、标题、她的原话）。"""
    rendered: List[str] = []
    for line in list(lines)[:MAX_BRIEF_ARCHIVE_LINES]:
        title = (line.text or "").strip()
        quote = (line.quote or "").strip()
        if not title:
            continue
        head = f"- [{render_archive_number(line.id)}] {title}"
        rendered.append(f"{head}（你说过「{quote}」）" if quote else head)
    return rendered


def render_brief(
    *,
    archive_lines: Sequence[ArchiveLine] = (),
    commitments: Sequence[Commitment] = (),
    previous_session_id: Optional[int] = None,
    earlier_commitment_count: int = 0,
) -> str:
    """Planner 系统提示词末尾的常驻简报；两段都空时返回空串（整段省略）。

    上一场 = 最近一个已整理、有档案行的正式场；承诺只放不针对具体观众、不挂游戏的
    open 条，最老的在前至多 5 条，更多的折成一行指向回想工具。
    """
    sections: List[str] = []
    archive_rows = render_archive_block(archive_lines)
    if archive_rows:
        sections.append("上一场：\n" + "\n".join(archive_rows))
    pending: List[str] = []
    for commitment in list(commitments)[:MAX_BRIEF_COMMITMENTS]:
        what = (commitment.what or "").strip()
        if not what:
            continue
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
        block = ["还没兑现、你当众说过的事：", *pending]
        if extra:
            block.append(f"- 另有 {extra} 件，可以用 memory_recall 查")
        sections.append("\n".join(block))
    if not sections:
        return ""
    return _BRIEF_HEADER + "\n" + "\n".join(sections)


def render_cue_header() -> str:
    """参考段里线索块的抬头（有卡片或提示行时才整块出现）。"""
    return _CUE_HEADER


def render_game_card(commitment: Commitment, *, game: str) -> str:
    """游戏承诺卡：挂在这个游戏上、不针对具体观众的承诺；游戏开着时推。"""
    quote = (commitment.quote or "").strip() or (commitment.what or "").strip()
    return (
        f"[{render_commitment_number(commitment.id)}·{game}] "
        f"你之前说过「{quote}」，截至上一场还没做。现在 {game} 开着。"
    )


def render_viewer_card(
    commitment: Commitment,
    *,
    viewer_label: str,
    message_id: str,
    is_previous: bool = True,
) -> str:
    """对某位观众的承诺卡：只给动作标题，不提当时的缘由与原话。"""
    when = _when_word(is_previous=is_previous, spoken=False)
    target = f"{viewer_label}（弹幕 {message_id}）在" if message_id else f"{viewer_label} 在"
    return (
        f"[{render_commitment_number(commitment.id)}·对{viewer_label}] "
        f"{target}。你答应过 ta：{(commitment.what or '').strip()}（{when}）。"
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


def render_spoken_memory(*, target_label: str, commitment: Commitment, is_previous: bool = False) -> str:
    """说话档：Planner 点名的那一条，交到 Replyer 手里（至多一条，不含编号与原话）。"""
    what = render_commitment_text(commitment, spoken=True)
    if not what:
        return ""
    when = _when_word(is_previous=is_previous, spoken=True)
    return f"{_SPOKEN_HEADER}\n- 你{when}答应过{target_label}：{what}。"
