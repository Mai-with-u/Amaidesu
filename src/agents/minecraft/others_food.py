"""拿了别人家的吃的：Mod 的 ``others_food_taken`` 事件读成记账条目和给主播的一句话。

饿到危险线、自己和自家箱子里都没吃的时，角色会从别人家的箱子里拿刚好够的吃的；每确认拿到一次，Mod 在
``topic=self`` 流里发一条 ``others_food_taken``（从自家箱子拿的不发）。主人回头在聊天里问"谁拿了我箱子里的面包"，
她得知道是自己拿的、坦然认下，不能装傻，也不能觉得委屈。所以这件事两头都要记：

- 主播这头：在游戏聊天里回话的是主播，注意流采集器把这件事连同回应口径转给主播（:func:`spoken_line`）；
- 游戏 Agent 这头：按世界记最近几笔（:class:`FoodTakenLog`），放进任务事实，主播转来问话时按名字、区域或位置对上号。

只有 ``whose=player`` 说得出主人是谁。记在某块区域里的、看着像玩家放的、说不清是谁的三种，主人来认领时只能
按区域名或箱子位置对号，所以条目里位置和区域照原样留着。
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

__all__ = ["OTHERS_FOOD_TAKEN", "FoodTaken", "FoodTakenLog", "food_taken_of", "spoken_line"]

#: Mod 事件种类：拿了别人家的吃的
OTHERS_FOOD_TAKEN = "others_food_taken"

#: 每个世界留几笔：够主人回头来问时对上号，多了只会让任务事实变长
_KEEP_PER_WORLD = 8

#: 括号里的坐标："（12, 64, -8）"；整句别的部分照留，坐标对观众和主播都没用
_PARENTHESIZED_COORDINATES = re.compile(
    r"\s*[（(]\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*[)）]"
)

#: 归属说法（Mod 原话缺失时自己拼一句用）
_WHOSE_PHRASES = {
    "region": "记住的区域「{region}」里的箱子",
    "looks_player_made": "看着像玩家放的箱子",
    "unknown": "不知道是谁的箱子",
}


@dataclass(frozen=True)
class FoodTaken:
    """一次拿了别人家的吃的。

    Attributes:
        whose: 东西算谁的：``player`` 某个玩家 / ``region`` 记住的某块区域 / ``looks_player_made`` 看着像玩家放的 /
            ``unknown`` 说不清
        owner: 主人（只在 ``whose=player`` 时有：在线写名字，不在线写编号）
        region: 区域名（只在 ``whose=region`` 时有）
        at: 箱子位置（``x``、``y``、``z``、``dimension``）
        items: 拿了什么、几个（``item``、``count``）
        food: 拿之前的饱食度（0–20，和 ``observe(self).food`` 同一个数；Mod 没给时为 None）
        losing_health: 当时是不是已经饿到在掉血
        message: Mod 那句话原文
        at_ms: 收到的时刻（毫秒）
    """

    whose: str
    owner: str = ""
    region: str = ""
    at: Dict[str, Any] = field(default_factory=dict)
    items: Tuple[Dict[str, Any], ...] = ()
    food: Optional[int] = None
    losing_health: bool = False
    message: str = ""
    at_ms: int = 0

    def view(self) -> Dict[str, Any]:
        """放进任务事实的样子：主人来问时按名字、区域或位置对号用，原话一并留着。"""
        view: Dict[str, Any] = {"whose": self.whose, "at": dict(self.at), "items": [dict(item) for item in self.items]}
        if self.owner:
            view["owner"] = self.owner
        if self.region:
            view["region"] = self.region
        if self.message:
            view["message"] = self.message
        return view


def food_taken_of(event: Dict[str, Any], at_ms: int) -> FoodTaken:
    """一条 ``others_food_taken`` 事件 → 条目；结构化字段缺了（旧版 Mod、写错）就只留原话，归属按说不清算。"""
    detail = event.get(OTHERS_FOOD_TAKEN)
    detail = detail if isinstance(detail, dict) else {}
    whose = str(detail.get("whose") or "unknown")
    at = detail.get("at") if isinstance(detail.get("at"), dict) else {}
    items = tuple(dict(item) for item in detail.get("items") or [] if isinstance(item, dict))
    food = detail.get("food")
    return FoodTaken(
        whose=whose,
        owner=str(detail.get("owner") or "") if whose == "player" else "",
        region=str(detail.get("region") or "") if whose == "region" else "",
        at=dict(at),
        items=items,
        food=food if isinstance(food, int) and not isinstance(food, bool) else None,
        losing_health=detail.get("losing_health") is True,
        message=str(event.get("message") or ""),
        at_ms=at_ms,
    )


def spoken_line(entry: FoodTaken) -> str:
    """给主播的一句话：拿了谁的什么，后面跟着主人来问时怎么接。

    事情本身用 Mod 的原话、去掉括号里的坐标（"饿到饱食度 3，从 Alex 家的箱子拿了 2 个面包"）；原话缺了才自己拼。
    回应口径跟着归属走：知道主人是谁的点名；说不清是谁的按地方对号，有人说那边的箱子少了吃的就是她拿的。
    """
    fact = _PARENTHESIZED_COORDINATES.sub("", entry.message).strip().rstrip("。") or _fact_from_fields(entry)
    if entry.whose == "player" and entry.owner:
        asker = f"{entry.owner} 来问"
    elif entry.whose == "region" and entry.region:
        asker = f"有人说「{entry.region}」那边箱子里少了吃的"
    else:
        asker = "有人说那附近箱子里少了吃的"
    return (
        f"{fact}。东西是别人的，饿急了才拿的：{asker}，就大方认下是我拿的，可以俏皮一点；"
        "对方真生气了就道个歉，说回头补上。说话人的名字没核实时也照样认，只是别把话说死"
    )


def _fact_from_fields(entry: FoodTaken) -> str:
    """Mod 原话缺失时按结构化字段拼一句："从 Alex 家的箱子拿了 2 个 minecraft:bread"。"""
    if entry.whose == "player" and entry.owner:
        where = f"{entry.owner} 家的箱子"
    else:
        where = _WHOSE_PHRASES.get(entry.whose, _WHOSE_PHRASES["unknown"]).format(region=entry.region or "?")
    things = "、".join(f"{item.get('count', '?')} 个 {item.get('item', '吃的')}" for item in entry.items) or "吃的"
    return f"饿急了，从{where}拿了{things}"


class FoodTakenLog:
    """按世界记最近几次拿了别人家的吃的；看的时候只给当前世界的，换了存档不把别处的账算到这里。"""

    def __init__(self, keep_per_world: int = _KEEP_PER_WORLD) -> None:
        self._keep = keep_per_world
        self._by_world: Dict[str, Deque[FoodTaken]] = {}

    def record(self, world_key: Optional[str], entry: FoodTaken) -> None:
        """记一笔；世界还没认出来（``None``）时记在空串名下。"""
        self._by_world.setdefault(world_key or "", deque(maxlen=self._keep)).append(entry)

    def view(self, world_key: Optional[str]) -> List[Dict[str, Any]]:
        """当前世界的几笔，按先后。"""
        return [entry.view() for entry in self._by_world.get(world_key or "", ())]
