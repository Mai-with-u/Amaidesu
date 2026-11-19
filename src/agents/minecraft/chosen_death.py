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

from typing import Any, Callable, Dict, Optional, Set

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
#: suicide 目标刚结束后的短宽限窗（毫秒）：Mod 常先推目标 finished/step_finished 再推 character_died，
#: 落在宽限窗内的紧随死亡事件仍认作自己选的死，消费一次后清除防止连杀误伤
_SUICIDE_GRACE_MS = 15_000


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
    """注意流采集器用：从事件流认出正在跑或刚刚结束的 ``suicide`` 目标，死讯到时据此判断是不是自己要死的。"""

    def __init__(self, clock: Callable[[], int]) -> None:
        self._clock = clock
        # 目标编号 → 看到它开始的时刻（毫秒）
        self._running: Dict[int, int] = {}
        # 已经在运行期间匹配消费过一次死亡事件的 suicide 目标编号：这类目标随后结束时不再开启宽限窗，
        # 防止“死在目标内 -> 目标结束 -> 紧接着真被怪打死”被二次误判成自杀。
        self._death_seen_goals: Set[int] = set()
        # 最近一次尚未见过死亡的 suicide 目标/步骤结束时刻（毫秒）：给晚到几秒的 character_died 留短宽限窗，
        # 避免先收 finished/step_finished 后收死亡事件时把主动回家误判成意外事故；消费一次即清空。
        self._last_suicide_finished_ms: Optional[int] = None

    def observe(self, event: Dict[str, Any]) -> None:
        """看一条事件：suicide 开始或接着推进就记下；未见过死亡的目标结束时转入短宽限窗；改推进别的能力则划掉。"""
        started = suicide_goal_of(event)
        if started is not None:
            self._running[started] = self._clock()
            self._death_seen_goals.discard(started)
            self._last_suicide_finished_ms = None
            return
        goal_id = _goal_id_of(event)
        if goal_id is None:
            return
        kind = event.get("kind")
        ability = ability_of(event)
        if kind == "finished":
            removed = self._running.pop(goal_id, None)
            already_died = goal_id in self._death_seen_goals
            self._death_seen_goals.discard(goal_id)
            if not already_died and (removed is not None or (ability is not None and is_suicide_ability(ability))):
                # suicide 目标刚结束且此前尚未收到死亡事件：进入短宽限窗，接住随后几秒内到达的死亡事件
                self._last_suicide_finished_ms = self._clock()
        elif kind == "step_finished" and ability is not None and is_suicide_ability(ability):
            # sequence 里寻死的那一步做完了：未见过死亡时弹出在跑记录并进入短宽限窗，接住紧随的死亡事件
            self._running.pop(goal_id, None)
            already_died = goal_id in self._death_seen_goals
            self._death_seen_goals.discard(goal_id)
            if not already_died:
                self._last_suicide_finished_ms = self._clock()
        elif kind in ("started", "resumed") and ability is not None:
            # 同一个编号接着推进的已是别的能力（sequence 走到了下一步）：先前记下的寻死不再算数
            self._running.pop(goal_id, None)
            self._death_seen_goals.discard(goal_id)

    def chosen(self) -> bool:
        """此刻是不是有 suicide 在跑，或死亡事件落在 suicide 刚结束后的短宽限窗内（单次消费防连杀误伤）。"""
        now = self._clock()
        self._running = {goal_id: at for goal_id, at in self._running.items() if now - at < _SUICIDE_STALE_MS}
        if self._running:
            # 记录当前在跑的 suicide 目标已消费本次死亡，随后它结束时不再额外放行第二次死亡
            self._death_seen_goals.update(self._running.keys())
            return True
        if self._last_suicide_finished_ms is not None:
            elapsed = now - self._last_suicide_finished_ms
            # 无论命中还是超时，宽限窗消费一次即清零，防止紧接着的二次真意外死亡被误当自杀
            self._last_suicide_finished_ms = None
            if 0 <= elapsed < _SUICIDE_GRACE_MS:
                return True
        return False

    def clear(self) -> None:
        """连接拆掉、换了事件流：记下的编号、已消费记录与宽限窗都不算数了。"""
        self._running.clear()
        self._death_seen_goals.clear()
        self._last_suicide_finished_ms = None
