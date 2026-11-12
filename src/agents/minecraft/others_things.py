"""动了别人放的东西：Mod 的 ``others_things_touched`` 事件读成按世界记账条目和给主播的一句话。

归属记录不拦动作（角色能开箱、能取材、能拆改），但透明是硬要求：只要角色从别人放的容器（``whose=player``）里取放物品、
或者拆改了别人放的方块，Mod 在确认发生后发一条 ``others_things_touched``。两头都要记清：

- 主播这头：注意流采集器把发生了什么连同俏皮认账口径转给主播，带放置者 ``player_uuid`` / ``player_name``；
- 游戏 Agent 这头：按世界记最近几笔（:class:`OthersThingsLog`），放进任务事实与新任务开局事实，主人问起或交付总结时对得上号。
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from .attention_matrix import item_name

__all__ = [
    "OTHERS_THINGS_TOUCHED",
    "OthersThingsLog",
    "OthersThingsTouched",
    "others_things_of",
    "spoken_line",
]

#: Mod 事件种类：动了别人放的容器或方块
OTHERS_THINGS_TOUCHED = "others_things_touched"
#: 事件里的明细字段名
_DETAIL_KEY = "others_things"
#: 每个世界保留最近几笔（够主人回头问起时对号，不让任务事实无界膨胀）
_KEEP_PER_WORLD = 12

#: 括号里的坐标："（-352, 65, 53）"，对主播与观众无意义，口播摘要前剥除
_PARENTHESIZED_COORDINATES = re.compile(
    r"\s*[（(]\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*[)）]"
)

_ACTION_WORDS = {
    "took": "拿了",
    "stored": "放进了",
    "broke": "拆掉了",
    "changed": "改动了",
}


@dataclass(frozen=True)
class OthersThingsTouched:
    """一次动了别人放的容器或方块。"""

    action: str
    owner: str = ""
    owner_uuid: str = ""
    block: str = ""
    count: Optional[int] = None
    at: Dict[str, Any] = field(default_factory=dict)
    items: Tuple[Dict[str, Any], ...] = ()
    ability: str = ""
    message: str = ""
    at_ms: int = 0

    def view(self) -> Dict[str, Any]:
        """放进任务事实的样子：主人是谁、在哪、动了什么，供对号与如实交代。"""
        out: Dict[str, Any] = {"action": self.action, "at": dict(self.at)}
        if self.owner:
            out["owner"] = self.owner
        if self.block:
            out["block"] = self.block
        if self.count is not None:
            out["count"] = self.count
        if self.items:
            out["items"] = [dict(item) for item in self.items]
        if self.ability:
            out["ability"] = self.ability
        if self.message:
            out["message"] = self.message
        return out


def others_things_of(event: Dict[str, Any], at_ms: int) -> OthersThingsTouched:
    """一条 ``others_things_touched`` 事件 → 条目；字段缺失时保留原话兜底。"""
    detail = event.get(_DETAIL_KEY)
    detail = detail if isinstance(detail, dict) else {}
    at = detail.get("at") if isinstance(detail.get("at"), dict) else {}
    items = tuple(dict(item) for item in detail.get("items") or [] if isinstance(item, dict))
    raw_count = detail.get("count")
    count = raw_count if isinstance(raw_count, int) and not isinstance(raw_count, bool) else None
    return OthersThingsTouched(
        action=str(detail.get("action") or "touched"),
        owner=str(detail.get("owner") or ""),
        owner_uuid=str(detail.get("owner_uuid") or ""),
        block=str(detail.get("block") or ""),
        count=count,
        at=dict(at),
        items=items,
        ability=str(event.get("ability") or ""),
        message=str(event.get("message") or ""),
        at_ms=at_ms,
    )


def _fallback_fact(entry: OthersThingsTouched) -> str:
    who = f" {entry.owner} 放的" if entry.owner else "别人放的"
    block = item_name(entry.block) if entry.block else "东西"
    verb = _ACTION_WORDS.get(entry.action, "动了")
    if entry.items:
        things = "、".join(
            f"{item.get('count', 1)} 个 {item_name(str(item.get('item') or '物品'))}" for item in entry.items
        )
        return f"在{who}{block}里{verb} {things}"
    if entry.count and entry.count > 1:
        return f"{verb}{who}{block}共 {entry.count} 格"
    return f"{verb}{who}{block}"


def spoken_line(entry: OthersThingsTouched) -> str:
    """给主播的一句话：动了谁放的什么，连同被问起时的认账口径（去掉坐标）。"""
    fact = _PARENTHESIZED_COORDINATES.sub("", entry.message).strip().rstrip("。") or _fallback_fact(entry)
    asker = f"{entry.owner} 问起" if entry.owner and entry.owner != "一位不在线的玩家" else "主人问起"
    return (
        f"{fact}。那是别人放的东西：{asker}就大方认下是我动的，可以俏皮一点；"
        "要是人家在意，就道个歉、说回头补上或恢复原样"
    )


class OthersThingsLog:
    """按世界记最近几次动了别人放的东西；换存档不串账。"""

    def __init__(self, keep_per_world: int = _KEEP_PER_WORLD) -> None:
        self._keep = keep_per_world
        self._by_world: Dict[str, Deque[OthersThingsTouched]] = {}

    def record(self, world_key: Optional[str], entry: OthersThingsTouched) -> None:
        """记一笔；世界还没认出时记在空串名下。"""
        self._by_world.setdefault(world_key or "", deque(maxlen=self._keep)).append(entry)

    def view(self, world_key: Optional[str]) -> List[Dict[str, Any]]:
        """当前世界按时间先后的记录。"""
        return [entry.view() for entry in self._by_world.get(world_key or "", ())]
