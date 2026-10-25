"""一次性主动触发信号的统一账：置位 → 按 tick 读数 → 确认消费。

三路信号（外部点播 / 流程单环节切换 / 游戏待定夺）此前是三个散装布尔、
清零纪律各写一套：游戏信号在判定前被无条件清零——总开关关闭、被
reminder 抢先的 tick 里静默丢失，"信号保留到开播后生效"的注释意图落空；
流程单信号确认后才清；外部信号读即清。本模块把三路状态与各自的消费
纪律收到一处显式声明：

- ``game`` / ``rundown``：确认消费——信号只在真正引发一次主动决策
  （reason 命中）后清零；被抢先或条件不满足时保留，条件重新满足时
  补触发（游戏待定夺、新环节开场白不能静默丢）。
- ``external``：读即消费——外部点播是尽力而为的推动，本 tick 条件
  不满足（防接龙/小时限额/话题缺失/被抢先）即弃，不补偿重试。

下播（``live=False``）时读数全 False 且不消费任何信号：信号保留到
开播后首个 tick 生效。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

__all__ = ["ProactiveSignals", "ProactiveTickSignals"]


@dataclass(frozen=True, slots=True)
class ProactiveTickSignals:
    """一个 tick 参与主动发言判定的信号读数（直播闸后）。"""

    external_pending: bool
    rundown_pending: bool
    game_pending: bool


class ProactiveSignals:
    """一次性主动触发信号的状态与消费纪律。非线程安全；Agent 事件循环内使用。"""

    def __init__(self) -> None:
        self._external = False
        self._rundown = False
        self._game = False

    # ----- 置位（事件回调侧） -----

    def set_external(self) -> None:
        """外部点播（Dashboard / API）请求一轮主动发言。"""
        self._external = True

    def set_rundown(self) -> None:
        """流程单环节切换：新环节开场白要说。"""
        self._rundown = True

    def set_game(self) -> None:
        """游戏待定夺：游戏里有事需要主播拿主意。"""
        self._game = True

    # ----- 观测（非消费；测试与诊断用） -----

    @property
    def external_pending(self) -> bool:
        return self._external

    @property
    def rundown_pending(self) -> bool:
        return self._rundown

    @property
    def game_pending(self) -> bool:
        return self._game

    # ----- 消费（决策循环侧） -----

    def read_for_tick(self, *, live: bool) -> ProactiveTickSignals:
        """读本 tick 参与判定的信号读数。

        下播时全 False 且不消费。开播时 ``external`` 读即消费（尽力而为），
        ``game`` / ``rundown`` 待 ``confirm`` 确认。
        """
        if not live:
            return ProactiveTickSignals(external_pending=False, rundown_pending=False, game_pending=False)
        external, self._external = self._external, False
        return ProactiveTickSignals(external_pending=external, rundown_pending=self._rundown, game_pending=self._game)

    def confirm(self, reason: Optional[str], tick: ProactiveTickSignals) -> None:
        """决策结果回写：reason 命中的信号确认消费（game / rundown）。

        reason 与信号的对应：``game``→"game"、``rundown``→"rundown"。
        rundown 的 reason 也可能来自超时/就绪分支——仅当本 tick 读到
        rundown 信号才认是它引发的（优先级保证信号在场时必然走信号
        分支）。``None``（被公共前置拦下）或别的 reason 什么都不清：
        信号保留，条件重新满足时补触发。
        """
        if tick.game_pending and reason == "game":
            self._game = False
        if tick.rundown_pending and reason == "rundown":
            self._rundown = False
