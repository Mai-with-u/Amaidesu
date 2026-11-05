"""炉子在烧的等待：obtain 走烧炼时一炉要烧很久，Mod 装好炉就走、自动挂一个回炉计时，当场以 partial 收场。

这种 partial 不是做不成：东西在炉子里烧着，到点 Mod 发 timer_due 叫醒模型回去取。模型在等的这段时间里
结束一轮、不调工具，和"有目标在后台跑"一样是在等——不该被当成没推进去催促、挂起上报主播，也不该被系统
兜底交付成"任务完成"（委派要的东西还在炉子里，没进背包）。

本类记下"哪几个回炉计时还没响"：

- obtain 结束、``status=partial``、``problem.kind=WRONG_TIME`` 时，按 ``details.left_cooking`` 里带 ``timer_id``
  的那几台记下；计时挂满了没挂上（没有 ``timer_id``）的不记——到点没人叫，不能让模型以为会被叫醒而干等；
- timer_due 到点（Mod 原话"计时 N 到点：…"）就划掉 N；
- timer 能力的结果带着做完这次之后还挂着的全部计时（``details.pending``）：不在里面的都划掉
  （模型自己取消了，或 Mod 回炉提前取走时自己摘了计时）；
- 换存档时 Mod 清空全部计时，这里也清空；新任务开始时，上个任务留下的也不再算这个任务在等；
- 兜底期限：每台按"约几秒烧好"记一个真实时间期限（两倍再加两分钟，容下服务器卡顿），过了还没响就不再算
  在等——计时可能随游戏重开被清掉，由 Agent 到期叫醒模型去看一眼，免得一直空等。

时刻用注入的单调时钟（秒）；非线程安全，只在 Agent 事件循环里用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

from src.agents.minecraft.maicraft import GoalRun

__all__ = ["FurnaceWait", "FurnaceWaits", "TIMER_ABILITY", "TIMER_DUE"]

#: 计时到点的身体事件种类
TIMER_DUE = "timer_due"

#: 计时能力在目标运行里的 ID：它的结果带着此刻还挂着的全部计时，拿来对账
TIMER_ABILITY = "maicraft:timer"

#: 装好炉先走时 Mod 写的问题种类（"时间不对"：现在拿不到，过一阵再来）
_LEFT_COOKING_PROBLEM = "WRONG_TIME"

#: 兜底期限 = 约几秒烧好 × 倍数 + 余量（秒）：计时按游戏时间走，服务器卡顿时比真实时间慢
_DEADLINE_FACTOR = 2.0
_DEADLINE_SLACK_S = 120.0

#: Mod 的到点原话开头："计时 7 到点：回 (10, 64, -3) 的熔炉取干海带 ×64（640 秒前设的；此刻白天）"
_TIMER_DUE_ID = re.compile(r"计时\s*(\d+)\s*到点")


@dataclass(frozen=True)
class FurnaceWait:
    """一台在烧、挂了回炉计时的炉子：计时编号、给模型看的一句话、兜底期限（单调时钟秒）。"""

    timer_id: int
    note: str
    deadline: float


def _int_of(value: Any) -> Optional[int]:
    """JSON 里的整数（布尔不算）；不是整数时为 None。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _note_of(cooking: Dict[str, Any], timer_id: int) -> str:
    """一台炉子的一句话：计时几号、炉子在哪、在烧几件什么。"""
    where = ", ".join(str(cooking.get(axis, "?")) for axis in ("x", "y", "z"))
    item = str(cooking.get("item") or "东西")
    loaded = _int_of(cooking.get("loaded"))
    amount = f" ×{loaded}" if loaded is not None else ""
    return f"计时 {timer_id}：({where}) 的炉子在烧 {item}{amount}"


class FurnaceWaits:
    """记下还没响的回炉计时；终止判定按它认"炉子在烧、到点会叫醒"，期限过了交给 Agent 叫醒模型。"""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._waits: Dict[int, FurnaceWait] = {}
        # 已经响过或对账划掉的计时：模型回头再读那个结束了的 obtain 目标时，不把它当成还在等重新记上
        self._closed: Set[int] = set()

    def absorb(self, run: GoalRun) -> None:
        """看一个结束了的目标：装好炉先走的 obtain 记下回炉计时，timer 能力按它带回的挂着的计时对账。"""
        result = run.result or {}
        details = result.get("details") if isinstance(result.get("details"), dict) else {}
        if run.ability == TIMER_ABILITY and isinstance(details.get("pending"), list):
            # 模型设、取消或列计时都会带回此刻还挂着的全部计时：不在里面的已经不会响了
            still = {_int_of(item.get("timer_id")) for item in details["pending"] if isinstance(item, dict)}
            for timer_id in [timer_id for timer_id in self._waits if timer_id not in still]:
                self._close(timer_id)
            return
        problem = result.get("problem") if isinstance(result.get("problem"), dict) else {}
        left = details.get("left_cooking")
        if (
            result.get("status") != "partial"
            or problem.get("kind") != _LEFT_COOKING_PROBLEM
            or not isinstance(left, list)
        ):
            return
        now = self._clock()
        for cooking in left:
            if not isinstance(cooking, dict):
                continue
            timer_id = _int_of(cooking.get("timer_id"))
            if timer_id is None or timer_id in self._closed or timer_id in self._waits:
                # 没挂上计时的不记（到点没人叫）；同一个计时只记一次，期限不因重读目标顺延
                continue
            ready = max(_int_of(cooking.get("ready_in_seconds")) or 0, 0)
            deadline = now + ready * _DEADLINE_FACTOR + _DEADLINE_SLACK_S
            self._waits[timer_id] = FurnaceWait(timer_id, _note_of(cooking, timer_id), deadline)

    def timer_due(self, message: str) -> None:
        """计时到点：按 Mod 原话里的编号划掉那一台（不是回炉计时的编号不在册，什么也不做）。"""
        matched = _TIMER_DUE_ID.search(message)
        if matched is not None:
            self._close(int(matched.group(1)))

    def waiting(self) -> List[FurnaceWait]:
        """还在等、没过兜底期限的回炉计时：按计时编号排。"""
        now = self._clock()
        return [wait for _, wait in sorted(self._waits.items()) if wait.deadline > now]

    def take_overdue(self) -> List[FurnaceWait]:
        """过了兜底期限还没响的回炉计时：取出并划掉（每台只交出去一次），按计时编号排。"""
        now = self._clock()
        overdue = [wait for _, wait in sorted(self._waits.items()) if wait.deadline <= now]
        for wait in overdue:
            self._close(wait.timer_id)
        return overdue

    def clear(self) -> None:
        """新任务开始：上个任务留下的炉子不再算这个任务在等（到点照样会叫醒，由那条通知自己说清）。"""
        self._waits.clear()

    def forget_world(self) -> None:
        """换了存档：Mod 已清空全部计时，在等的与划掉的一并清空（新存档的计时编号可能重新数起）。"""
        self._waits.clear()
        self._closed.clear()

    def _close(self, timer_id: int) -> None:
        self._waits.pop(timer_id, None)
        self._closed.add(timer_id)
