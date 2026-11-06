"""地点动静：记住、忘掉一个地点，走近、离开一个记住的地点——发给存储，给下播整理认地点用。

下播整理要认出一句话是在哪个记住的地点说的，档案行才能挂上地点；主播走到地点跟前想起那里的
旧事时也拿它佐证。这里只认动静、不改游戏 Agent 怎么做事：

- 记住 / 忘掉：游戏 Agent 自己记地点、忘掉地点成功的那一步（圈地盘不是地点，不算）；
- 走近 / 离开：主播每次速览时，拿速览已读到的位置和地点缓存现算最近的记住地点（不多读游戏）。
  最近的地点进到 ``PLACE_NEAR_BLOCKS`` 格内记一条"走近"；走出 ``PLACE_LEAVE_BLOCKS`` 格才记"离开"
  （进出两道线，在 24 格边上来回走不会一进一出地记）。同一存档的同一个地点 ``PLACE_NEAR_REFRESH_S``
  秒内最多记一条"走近"，中间离开过也一样——一直待在营地附近、在两个挨着的地点之间来回走都不刷屏，
  待着不走时每满这个间隔补记一条，下播整理据此知道她还在那里。

时间经注入的单调时钟（秒）走，测试推进假时钟验证。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from src.modules.events.payloads.game_place import GamePlaceKind
from src.modules.types.game_situation import GameSituation

# 离记住的地点多近算"走到跟前"（水平格）
PLACE_NEAR_BLOCKS = 24
# 走出多远才算离开（水平格）
PLACE_LEAVE_BLOCKS = 48
# 一直待在同一个地点附近时，隔多久再记一条"走近"（秒）
PLACE_NEAR_REFRESH_S = 300.0

__all__ = [
    "PLACE_LEAVE_BLOCKS",
    "PLACE_NEAR_BLOCKS",
    "PLACE_NEAR_REFRESH_S",
    "NearPlaceTracker",
    "PlaceMark",
    "place_change_of",
]


@dataclass(frozen=True)
class PlaceMark:
    """一条地点动静：种类（remembered / forgotten / near / left）、地点名、走近时的距离。"""

    kind: GamePlaceKind
    place: str
    distance: Optional[int] = None


def place_change_of(arguments: Dict[str, Any]) -> Optional[PlaceMark]:
    """记地点、忘掉地点成功的那一步（``execute`` 的参数）对应的动静；圈地盘、没写名字的不算。"""
    goal = arguments.get("goal") if isinstance(arguments.get("goal"), dict) else {}
    parameters = goal.get("parameters") if isinstance(goal.get("parameters"), dict) else {}
    place = str(parameters.get("name") or "").strip()
    if not place:
        return None
    operation = str(parameters.get("operation") or "remember").strip()
    if operation == "remember":
        return PlaceMark(kind="remembered", place=place)
    if operation == "forget":
        return PlaceMark(kind="forgotten", place=place)
    return None


class NearPlaceTracker:
    """记着角色眼下在哪个记住的地点跟前，按速览一次次报来的情境认出"走近""离开"。"""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        # 眼下在哪些地点跟前：(存档编号, 地点名)；进到 24 格内算进来，走出 48 格（或清单里没了它）才算离开
        self._inside: Set[Tuple[str, str]] = set()
        # 每个（存档编号, 地点名）上一次记"走近"的时刻：5 分钟内最多记一次，中间离开过也一样
        self._noted_at: Dict[Tuple[str, str], float] = {}

    def observe(self, save_id: str, situation: Optional[GameSituation]) -> List[PlaceMark]:
        """看一次情境，返回该记的动静（多半为空）。

        顺序：情境不明（位置没读到、地点清单丢了）-> 什么都不记、也不改状态；换了存档 -> 旧存档的地点
        直接作废，不记"离开"（那条会被记到新存档名下）；还在跟前的地点走出 48 格或不在清单里了 -> 记
        "离开"；最近的地点在 24 格内 -> 算进来，这个地点 5 分钟内没记过"走近"就记一条。
        """
        if situation is None:
            return []
        marks: List[PlaceMark] = []
        distances = {place.name: place.distance for place in situation.places}
        self._inside = {key for key in self._inside if key[0] == save_id}
        for key in sorted(self._inside):
            distance = distances.get(key[1])
            if distance is None or distance > PLACE_LEAVE_BLOCKS:
                self._inside.discard(key)
                marks.append(PlaceMark(kind="left", place=key[1]))
        nearest = situation.places[0] if situation.places else None
        if nearest is not None and nearest.distance <= PLACE_NEAR_BLOCKS:
            key = (save_id, nearest.name)
            self._inside.add(key)
            now = self._clock()
            noted = self._noted_at.get(key)
            if noted is None or now - noted >= PLACE_NEAR_REFRESH_S:
                self._noted_at[key] = now
                marks.append(PlaceMark(kind="near", place=nearest.name, distance=nearest.distance))
        return marks
