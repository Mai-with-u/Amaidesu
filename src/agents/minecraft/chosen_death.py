"""自己选的死：认出这次死亡是角色用 ``suicide`` 回重生点，不是出了事。

死亡不掉落开着时，寻死是回家、回满血和饱食度的正常办法（卡住出不来、饿了没吃的也一样）。Mod 的
``character_died`` 不分是不是自己要死的，所以宿主按"死的时候手上是不是正在跑 ``suicide``"来认：

- 游戏 Agent 自己下的目标都在跟踪名单里，名单里有 ``suicide`` 就是自己要死的；
- 注意流采集器不下目标，只读事件流：看到 ``suicide`` 目标开始（或接着推进）就记下它的编号，结束了划掉；
  死讯落在这段时间里就是自己要死的。结束事件万一漏读，记下的编号放久了也作废，免得以后真出事被当成回家。

事件说的是哪个能力，新版 Mod 写在事件的 ``ability`` 字段里（``sequence`` 的一步记在整件 sequence 的编号上，
字段写那一步自己的能力）；旧版 Mod 没有这个字段，才从消息开头"开始：/接着推进：<能力>"里解析。
因为同一个 sequence 编号会先后推进不同的能力，寻死那一步做完、或同一编号接着推进的已是别的能力时也划掉，
不让整件 sequence 剩下的路程都被当成在寻死。

认出来以后，给主播的话和给模型的通知都换成"回家"的说法：主播先向观众说清这次为什么死、
让大家安心东西没丢，之后再轻松带过或开个玩笑，不当事故讲，也不当伤心事记进记忆；
模型照常选重生，复活后接着做手上的事。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

__all__ = [
    "CHOSEN_DEATH_LINE",
    "CHOSEN_DEATH_NOTICE",
    "SuicideWatch",
    "ability_of",
    "is_suicide_ability",
    "suicide_goal_of",
]

#: 给主播的一句话（注意流采集器转出的死讯换成这句）。只求轻松带过会让观众不知道她为什么突然死，
#: 所以先交代再带过；死亡不掉落、状态回满是"不慌"的事实依据，保留
CHOSEN_DEATH_LINE = (
    "开着死亡不掉落，自己死了一次回重生点，这是回家的办法，不是出事。"
    "先用一句话按当时的实情向观众说清这次为什么死（被卡住走不出去、要回重生点这类原因），"
    "顺带让大家安心东西没丢、血和饱食度也回满了；之后再轻松带过或开个玩笑"
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


def ability_of(event: Dict[str, Any]) -> Optional[str]:
    """一条目标事件说的是哪个能力；读不出返回 None。

    先读事件的 ``ability`` 字段（新版 Mod 在开始、接着推进、暂停、提问、停滞、结束、步进完成上都带）；
    字段没有时（旧版 Mod）再从开始、接着推进那句话的开头"开始：maicraft:suicide（回家）"解析，
    别的事件旧版 Mod 的消息里不写能力，就当读不出。
    """
    ability = event.get("ability")
    if isinstance(ability, str) and ability.strip():
        return ability.strip()
    if event.get("kind") not in ("started", "resumed"):
        return None
    message = str(event.get("message") or "")
    lead = next((lead for lead in _GOAL_LEADS if message.startswith(lead)), None)
    if lead is None:
        return None
    return message[len(lead) :].split("（", 1)[0].split("(", 1)[0] or None


def suicide_goal_of(event: Dict[str, Any]) -> Optional[int]:
    """一条目标开始或接着推进的事件，若推进的是 ``suicide``，返回它的目标编号；不是返回 None。"""
    if event.get("kind") not in ("started", "resumed"):
        return None
    goal_id = _goal_id_of(event)
    ability = ability_of(event)
    if goal_id is None or ability is None:
        return None
    return goal_id if is_suicide_ability(ability) else None


def _goal_id_of(event: Dict[str, Any]) -> Optional[int]:
    """事件挂在哪个目标编号上；没带或不是整数时为 None（布尔值不算编号）。"""
    goal_id = event.get("goal_id")
    if not isinstance(goal_id, int) or isinstance(goal_id, bool):
        return None
    return goal_id


class SuicideWatch:
    """注意流采集器用：从事件流认出正在跑的 ``suicide`` 目标，死讯到时据此判断是不是自己要死的。"""

    def __init__(self, clock: Callable[[], int]) -> None:
        self._clock = clock
        # 目标编号 → 看到它开始的时刻（毫秒）
        self._running: Dict[int, int] = {}

    def observe(self, event: Dict[str, Any]) -> None:
        """看一条事件：suicide 开始或接着推进就记下；目标结束、寻死那一步做完、同一编号改推进别的能力就划掉。"""
        started = suicide_goal_of(event)
        if started is not None:
            self._running[started] = self._clock()
            return
        goal_id = _goal_id_of(event)
        if goal_id is None:
            return
        kind = event.get("kind")
        ability = ability_of(event)
        if kind == "finished":
            self._running.pop(goal_id, None)
        elif kind == "step_finished" and ability is not None and is_suicide_ability(ability):
            # sequence 里寻死的那一步做完了：整件 sequence 还在跑，但后面的路程已经不是在寻死
            self._running.pop(goal_id, None)
        elif kind in ("started", "resumed") and ability is not None:
            # 同一个编号接着推进的已是别的能力（sequence 走到了下一步）：先前记下的寻死不再算数
            self._running.pop(goal_id, None)

    def chosen(self) -> bool:
        """此刻是不是有 suicide 在跑（放太久的作废）。"""
        now = self._clock()
        self._running = {goal_id: at for goal_id, at in self._running.items() if now - at < _SUICIDE_STALE_MS}
        return bool(self._running)

    def clear(self) -> None:
        """连接拆掉、换了事件流：记下的编号都不算数了。"""
        self._running.clear()
