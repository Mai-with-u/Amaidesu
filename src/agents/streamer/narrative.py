"""叙事缓冲——游戏叙事 / 身体近况 / 游戏聊天进 Planner 上下文前的暂存与回放。

每个决策窗把缓冲里的条目渲染成一段参考文本交给 Planner：行首标到达距今多久，
上一次交出之后才到的标"新"。缓冲有界：已经交给过决策窗、又超过保留期的旧条目
不再回放——否则整场的遭遇每窗全量重放，主播会把十分钟前讲过的掉血反复当新闻播。
没交出过的条目不论多久都保留，保证新事实至少被看到一次。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .planner_context import age_text

__all__ = ["NarrativeBuffer", "NarrativeEntry", "NarrativeView", "render_narrative"]


@dataclass(frozen=True)
class NarrativeEntry:
    """一条游戏叙事/身体近况/游戏聊天：到达时刻 + 叙事行（``[game·类型] 内容``）。"""

    received_ms: int
    line: str


@dataclass(frozen=True)
class NarrativeView:
    """一次回放的结果：交给 Planner 的全文，以及本次才新到的条目原文。"""

    text: str
    fresh: Tuple[str, ...] = ()


@dataclass
class _Slot:
    """缓冲里的一条：条目本身 + 是否已经交给过决策窗。"""

    entry: NarrativeEntry
    delivered: bool = False


def _mark_line(entry: NarrativeEntry, *, fresh: bool, now: int) -> str:
    """一行叙事：行首标到达距今多久，没交出过的另标"新"。"""
    age = age_text(now - entry.received_ms)
    return f"[{'新·' if fresh else ''}{age}] {entry.line}"


def render_narrative(entries: List[NarrativeEntry], *, seen_until_ms: int, now: int) -> str:
    """叙事条目按到达先后全部列出，行首标注到达距今多久；上次决策之后才到达的加"新"。"""
    return "\n".join(_mark_line(entry, fresh=entry.received_ms > seen_until_ms, now=now) for entry in entries)


class NarrativeBuffer:
    """有界叙事缓冲：按到达先后暂存，回放时丢掉已交出且过了保留期的旧条目。"""

    def __init__(self, *, ttl_ms: int, max_items: int) -> None:
        """
        Args:
            ttl_ms: 已交给过决策窗的条目，自到达起保留多久（毫秒）；过期后不再回放。
            max_items: 最多保留条数；超出时先丢最早的（已交出的条目总是更早到达）。
        """
        self._ttl_ms = ttl_ms
        self._max_items = max_items
        # 逐条记"是否已交出"：同一毫秒里先交出、后到达的条目也不会被误当成旧事
        self._slots: List[_Slot] = []

    def __len__(self) -> int:
        return len(self._slots)

    def add(self, line: str, now: int) -> None:
        """事件到达即入缓冲；同时按上限裁剪，没开播、长时间没有决策窗时也不会无限增长。"""
        self._slots.append(_Slot(NarrativeEntry(received_ms=now, line=line)))
        self._prune(now)

    def render(self, now: int) -> NarrativeView:
        """交给一个决策窗：先丢过期旧条目再渲染，并把本次交出的条目都记为"已交出"。"""
        self._prune(now)
        text = "\n".join(_mark_line(slot.entry, fresh=not slot.delivered, now=now) for slot in self._slots)
        fresh = tuple(slot.entry.line for slot in self._slots if not slot.delivered)
        for slot in self._slots:
            slot.delivered = True
        return NarrativeView(text=text, fresh=fresh)

    def _prune(self, now: int) -> None:
        """已交出且到达超过保留期的不再回放；没交出过的不论多久都留着，再按条数上限截尾。"""
        kept = [slot for slot in self._slots if not slot.delivered or now - slot.entry.received_ms <= self._ttl_ms]
        if len(kept) > self._max_items:
            kept = kept[-self._max_items :]
        self._slots = kept
