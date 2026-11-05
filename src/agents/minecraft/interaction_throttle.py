"""互动叫醒按人节流：同一个玩家叫醒过一次，20 秒内他再来的肢体动作先攒着，不一条条叫醒模型。

别的玩家冲角色蹲起、挥手，Mod 按人归成一段发一条，一个人一直蹲能每 6 秒来一条，一分钟十来条，人多了成倍涨。
每条都叫醒模型就要调一次模型，提示词又让她先回个动作，有人就能故意刷她；她每回一次，Mod 那边"连着几次没理他
就两分钟不理他"的保护也被清零，一直触发不了。所以按人限住叫醒的次数：

- 同一个人第一条照常交给模型，记下这一刻；20 秒内他再来的先攒着，不单独叫醒；
- 20 秒到了，攒下的合成一条交出去（之后重新计 20 秒），一个人一分钟最多叫醒三次；
- 正在干活的批次跑着的时候，通知照常跟着下一步一起读（本来就不多调模型），只记下这一刻，不攒；
  闲着时只为回应互动开起来的那一批不算，那一批里再来的照样攒着，不然他一直蹲就一直续着调模型；
- 别的事把模型叫醒了（指令、目标结束、计时到点），攒着的跟着那一批一起读，不等到点。

认人按 Mod 事件上的 ``player`` 字段：先按 ``uuid``，没有再按名字；两样都没有的所有人共用一个桶。
拍一下、扔东西过来也一样节流：拍一下 Mod 那边已经让她回头看了。

本类只做簿记（时刻用注入的时钟，单位秒）；什么时候来问、交出去以后怎么叫醒，由游戏 Agent 决定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

__all__ = ["INTERACTION_WAKE_WINDOW_S", "InteractionThrottle", "player_key_of"]

#: 同一个人两次叫醒至少隔多久（秒）：一分钟最多三次
INTERACTION_WAKE_WINDOW_S = 20.0

#: 认不出是谁时共用的桶
_SHARED = ""


def player_key_of(event: Dict[str, Any]) -> str:
    """一条肢体动作事件是谁做的：``player.uuid``，没有就 ``player.name``，都没有为空串（共用一个桶）。"""
    player = event.get("player")
    if not isinstance(player, dict):
        return _SHARED
    for field_name in ("uuid", "name"):
        value = player.get(field_name)
        if isinstance(value, str) and value.strip():
            return f"{field_name}:{value.strip()}"
    return _SHARED


@dataclass
class _Bucket:
    """一个人的节流账：上次交给模型的时刻，和之后攒着还没交出去的 Mod 原话（按先后）。"""

    delivered_at: float
    held: List[str] = field(default_factory=list)


class InteractionThrottle:
    """按人记上次交给模型的时刻与攒着的通知；非线程安全，只在 Agent 事件循环里用。"""

    def __init__(self, clock: Callable[[], float], window_s: float = INTERACTION_WAKE_WINDOW_S) -> None:
        self._clock = clock
        self._window_s = window_s
        self._buckets: Dict[str, _Bucket] = {}

    def offer(self, key: str, message: str, *, reading_now: bool) -> Optional[List[str]]:
        """来了一条：返回现在就交给模型的原话（按先后，可能连带之前攒着的）；要攒着时返回 None。

        ``reading_now``：批次正在跑，这条会跟着下一步一起读，不多调模型，所以不攒，只记下这一刻。
        """
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is not None and not reading_now and now - bucket.delivered_at < self._window_s:
            bucket.held.append(message)
            return None
        held = bucket.held if bucket is not None else []
        self._buckets[key] = _Bucket(delivered_at=now)
        return [*held, message]

    def due(self) -> List[List[str]]:
        """到点了的：攒着通知、离上次交出去已满 20 秒的人，各合成一批交出去，并从这一刻重新计时。"""
        now = self._clock()
        ready: List[List[str]] = []
        for key, bucket in self._buckets.items():
            if bucket.held and now - bucket.delivered_at >= self._window_s:
                ready.append(bucket.held)
                self._buckets[key] = _Bucket(delivered_at=now)
        return ready

    def drain(self) -> List[List[str]]:
        """别的事把模型叫醒了：攒着的不等到点，全部跟着这一批交出去，各自从这一刻重新计时。"""
        now = self._clock()
        ready: List[List[str]] = []
        for key, bucket in self._buckets.items():
            if bucket.held:
                ready.append(bucket.held)
                self._buckets[key] = _Bucket(delivered_at=now)
        return ready

    def held_count(self) -> int:
        """眼下一共攒着几条（观测与测试用）。"""
        return sum(len(bucket.held) for bucket in self._buckets.values())

    def clear(self) -> None:
        """收工：攒着的不再交出去，计时也作废（下一场开播时冒出上一场的蹲起只是过时的事）。"""
        self._buckets.clear()
