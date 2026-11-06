"""下播整理给档案行写的触发词与地点：模型提名，代码逐条核对，对不上的丢掉。

被动想起靠这两样把旧事推回给她：观众提到触发词时推这一行，她走到挂着的地点跟前时推这一行。
模型只能提名，写进去的必须经得起核对，不靠模型自由发挥：

- **触发词**：至多 ``MAX_KEYWORDS`` 个，每个都要在标题或原话里出现（归一化后的子串），或是其中
  一段三个字以上的同音写法（观众常打同音字）；一个字的、重复的、对不上的丢掉。
- **地点**：只能照抄【本场记住的地点】里的名字，而且要么那句台词或标题里说到了这个地名，要么
  她说这句话时就在这个地点跟前（游戏 Agent 记下的"走近"在 ``PLACE_NEAR_WINDOW_MS`` 之内、同一个
  存档、中间没有"离开"）。只有挂了游戏的行才挂地点。

本场记住的地点来自游戏 Agent 记下的地点动静（记住、走近；之后又忘掉的不算）。
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from src.modules.storage.models.events import GamePlaceMark

from .textmatch import KEYWORD_MIN_CHARS, KeywordMatcher, normalize

__all__ = [
    "MAX_KEYWORDS",
    "PLACE_NEAR_WINDOW_MS",
    "remembered_places",
    "render_places",
    "verified_keywords",
    "verified_place",
]

# 一行档案至多几个触发词
MAX_KEYWORDS = 4
# "走近"之后多久内说的话算在那里说的：游戏 Agent 待在同一处时每 5 分钟补记一条走近，留 1 分钟余量
PLACE_NEAR_WINDOW_MS = 360_000


def verified_keywords(raw: Any, *texts: str) -> List[str]:
    """模型给的触发词里核得上的那些（按给出的先后，至多 ``MAX_KEYWORDS`` 个）。"""
    if not isinstance(raw, list):
        return []
    source = "".join(texts)
    normalized_source = normalize(source)
    kept: List[str] = []
    seen: set[str] = set()
    for item in raw:
        if len(kept) >= MAX_KEYWORDS:
            break
        word = str(item or "").strip()
        normalized = normalize(word)
        if len(normalized) < KEYWORD_MIN_CHARS or normalized in seen:
            continue
        # 原样出现在标题或原话里；否则看是不是其中一段的同音写法（拼音层只给三个字以上的词）
        found = normalized in normalized_source or any(
            hit.how == "homophone" for hit in KeywordMatcher({0: [word]}).match(source)
        )
        if found:
            seen.add(normalized)
            kept.append(word)
    return kept


def remembered_places(marks: Sequence[GamePlaceMark]) -> List[str]:
    """本场记住的地点名（按第一次出现的先后）：记住或走近过的，最后一次动静是"忘掉"的不算。"""
    last_kind: dict[str, str] = {}
    order: List[str] = []
    for mark in marks:
        if mark.kind not in ("remembered", "near", "forgotten"):
            continue
        if mark.place not in last_kind:
            order.append(mark.place)
        last_kind[mark.place] = mark.kind
    return [place for place in order if last_kind[place] != "forgotten"]


def render_places(places: Sequence[str]) -> str:
    """给模型看的【本场记住的地点】一节；本场一个都没有时写"没有"（模型据此一律不挂地点）。"""
    return "【本场记住的地点】\n" + ("、".join(places) if places else "没有")


def verified_place(
    raw: Any,
    *,
    places: Sequence[str],
    texts: Sequence[str],
    marks: Sequence[GamePlaceMark],
    at_ms: int,
    save_id: str,
) -> str:
    """模型给的地点核得上就返回地点名，否则空串。

    顺序：不在本场记住的地点里 -> 不挂；台词或标题里说到了这个地名 -> 挂；否则看说这句话时她在
    不在这个地点跟前（同一存档里、说话之前最近的一条走近或离开是"走近这里"，且还在时效内）。
    """
    place = str(raw or "").strip()
    if not place or place not in places:
        return ""
    normalized = normalize(place)
    if normalized and any(normalized in normalize(text) for text in texts):
        return place
    latest = _latest_position_mark(marks, at_ms=at_ms, save_id=save_id)
    if latest is not None and latest.kind == "near" and latest.place == place:
        return place if at_ms - latest.timestamp_ms <= PLACE_NEAR_WINDOW_MS else ""
    return ""


def _latest_position_mark(marks: Sequence[GamePlaceMark], *, at_ms: int, save_id: str) -> Optional[GamePlaceMark]:
    """同一存档里、某个时刻之前最近的一条走近或离开（标明她当时在不在某个地点跟前）。"""
    latest: Optional[GamePlaceMark] = None
    for mark in marks:
        if mark.kind not in ("near", "left") or mark.save_id != save_id or mark.timestamp_ms > at_ms:
            continue
        if latest is None or mark.timestamp_ms >= latest.timestamp_ms:
            latest = mark
    return latest
