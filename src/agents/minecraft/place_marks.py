"""地点动静：记住、忘掉一个地点，走近、离开一个记住的地点——发给存储，给下播整理认地点用。

下播整理要认出一句话是在哪个记住的地点说的，档案行才能挂上地点；主播走到地点跟前想起那里的
旧事时也拿它佐证。这里只认动静、不改游戏 Agent 怎么做事：

- 记住 / 忘掉：游戏 Agent 自己记地点、忘掉地点成功的那一步（圈地盘不是地点，不算）；
- 走近 / 离开：主播每次速览时，拿速览已读到的位置和地点缓存现算最近的记住地点（不多读游戏）。
  进到 ``PLACE_NEAR_BLOCKS`` 格内记一条"走近"，同一存档的同一个地点 ``PLACE_NEAR_REFRESH_S`` 秒内
  只记一条（一直待在营地附近也不刷屏）；离开所有地点的这个范围时记一条"离开"。

时间经注入的单调时钟（秒）走，测试推进假时钟验证。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.modules.events.payloads.game_place import GamePlaceKind
from src.modules.types.game_situation import GameSituation

# 离记住的地点多近算"走到跟前"（水平格）
PLACE_NEAR_BLOCKS = 24
# 一直待在同一个地点附近时，隔多久再记一条"走近"（秒）
PLACE_NEAR_REFRESH_S = 300.0

__all__ = ["PLACE_NEAR_BLOCKS", "PLACE_NEAR_REFRESH_S", "NearPlaceTracker", "PlaceMark", "place_change_of"]


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
        # 眼下在哪个地点跟前：(存档编号, 地点名)；不在任何地点跟前为 None
        self._near: Optional[Tuple[str, str]] = None
        self._noted_at = 0.0

    def observe(self, save_id: str, situation: Optional[GameSituation]) -> List[PlaceMark]:
        """看一次情境，返回该记的动静（多半为空）。

        顺序：情境不明（位置没读到、地点清单丢了）-> 什么都不记、也不改状态；最近的地点在范围内 ->
        换了地点、换了存档或同一处待满刷新间隔时记"走近"；范围内一个都没有 -> 刚才还在同一存档的
        某处跟前就记"离开"。换存档时旧存档的地点不记"离开"（那条会被记到新存档名下）。
        """
        if situation is None:
            return []
        nearest = situation.places[0] if situation.places else None
        if nearest is None or nearest.distance > PLACE_NEAR_BLOCKS:
            left = self._near
            self._near = None
            if left is not None and left[0] == save_id:
                return [PlaceMark(kind="left", place=left[1])]
            return []
        key = (save_id, nearest.name)
        now = self._clock()
        if key == self._near and now - self._noted_at < PLACE_NEAR_REFRESH_S:
            return []
        self._near, self._noted_at = key, now
        return [PlaceMark(kind="near", place=nearest.name, distance=nearest.distance)]
