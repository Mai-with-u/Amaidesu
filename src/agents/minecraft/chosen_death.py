"""自己选的死：认出这次死亡是角色用 ``suicide`` 回重生点，不是出了事。

死亡不掉落开着时，寻死是回家、回满血和饱食度的正常办法（卡住出不来、饿了没吃的也一样）。Mod 的
``character_died`` 不分是不是自己要死的，所以宿主按"死的时候手上是不是正在跑 ``suicide``"来认：

- 游戏 Agent 自己下的目标都在跟踪名单里，名单里有 ``suicide`` 就是自己要死的；
- 注意流采集器不下目标，只读事件流：看到 ``suicide`` 目标开始（或接着推进）就记下它的编号，结束了划掉；
  死讯落在这段时间里就是自己要死的。结束事件万一漏读，记下的编号放久了也作废，免得以后真出事被当成回家。

认出来以后，给主播的话和给模型的通知都换成"回家"的说法：主播轻松带过或开个玩笑，不当事故讲，
也不当伤心事记进记忆；模型照常选重生，复活后接着做手上的事。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

__all__ = [
    "CHOSEN_DEATH_LINE",
    "CHOSEN_DEATH_NOTICE",
    "SuicideWatch",
    "is_suicide_ability",
    "suicide_goal_of",
]

#: 给主播的一句话（注意流采集器转出的死讯换成这句）
CHOSEN_DEATH_LINE = (
    "开着死亡不掉落，自己死了一次回重生点：东西都还在，血和饱食度也回满了。"
    "这是回家的办法，不是出事，轻松带过或开个玩笑就好"
)

#: 给模型的通知模板（``{message}`` 是 Mod 原话）
CHOSEN_DEATH_NOTICE = (
    "[身体] {message}。这是你自己用 suicide 死的，回重生点的办法，不是出事。"
    "Mod 挂出死亡恢复决策时选重生；复活后先 maicraft_observe(what=self) 看一眼在哪，接着做手上的事。"
)

#: 目标开始、接着推进时 Mod 那句话的开头："开始：maicraft:suicide（回家）"
_GOAL_LEADS = ("开始：", "接着推进：")

#: 记下的 suicide 目标放多久作废（毫秒）：寻死自己有时限，十分钟还没结束只可能是漏读了结束事件
_SUICIDE_STALE_MS = 10 * 60 * 1000


def is_suicide_ability(ability: str) -> bool:
    """能力名是不是寻死：``suicide``，或带命名空间的 ``maicraft:suicide``。"""
    name = str(ability or "").strip()
    return name == "suicide" or name.endswith(":suicide")


def suicide_goal_of(event: Dict[str, Any]) -> Optional[int]:
    """一条目标开始或接着推进的事件，若推进的是 ``suicide``，返回它的目标编号；不是返回 None。"""
    if event.get("kind") not in ("started", "resumed"):
        return None
    message = str(event.get("message") or "")
    lead = next((lead for lead in _GOAL_LEADS if message.startswith(lead)), None)
    goal_id = event.get("goal_id")
    if lead is None or not isinstance(goal_id, int) or isinstance(goal_id, bool):
        return None
    ability = message[len(lead) :].split("（", 1)[0].split("(", 1)[0]
    return goal_id if is_suicide_ability(ability) else None


class SuicideWatch:
    """注意流采集器用：从事件流认出正在跑的 ``suicide`` 目标，死讯到时据此判断是不是自己要死的。"""

    def __init__(self, clock: Callable[[], int]) -> None:
        self._clock = clock
        # 目标编号 → 看到它开始的时刻（毫秒）
        self._running: Dict[int, int] = {}

    def observe(self, event: Dict[str, Any]) -> None:
        """看一条事件：suicide 开始就记下，任何目标结束就划掉。"""
        started = suicide_goal_of(event)
        if started is not None:
            self._running[started] = self._clock()
            return
        goal_id = event.get("goal_id")
        if event.get("kind") == "finished" and isinstance(goal_id, int):
            self._running.pop(goal_id, None)

    def chosen(self) -> bool:
        """此刻是不是有 suicide 在跑（放太久的作废）。"""
        now = self._clock()
        self._running = {goal_id: at for goal_id, at in self._running.items() if now - at < _SUICIDE_STALE_MS}
        return bool(self._running)

    def clear(self) -> None:
        """连接拆掉、换了事件流：记下的编号都不算数了。"""
        self._running.clear()
