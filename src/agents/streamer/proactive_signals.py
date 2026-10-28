"""一次性主动触发信号的统一账：置位 → 按 tick 读数 → 确认消费。

三路信号（外部点播 / 流程单环节切换 / 游戏待定夺）共用本模块的
"置位 → 按 tick 读数 → 确认消费"承载，各自的清零纪律在此显式声明
（游戏信号若在判定前被无条件清零，总开关关闭或被 reminder 抢先的 tick 里
会静默丢失，"信号保留到开播后生效"随之落空）：

- ``game`` / ``rundown``：确认消费——信号只在真正引发一次主动决策
  （触发原因枚举命中）后清零；被抢先或条件不满足时保留，条件重新满足时
  补触发（游戏待定夺、新环节开场白不能静默丢）。
- ``external``：读即消费——外部点播是尽力而为的推动，本 tick 条件
  不满足（防接龙/小时限额/话题缺失/被抢先）即弃，不补偿重试。

下播（``live=False``）时读数全 False 且不消费任何信号：信号保留到
开播后首个 tick 生效。

每 tick 的"读信号 → 折参 → 规则判定 → 确认消费"完整判定序由
:meth:`ProactiveSignals.evaluate_tick` 单点承担——消费纪律与判定序
同属信号账本一侧，调用方拿到判定结果只负责执行。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from .trigger_cause import TriggerKind

if TYPE_CHECKING:
    from .proactive_trigger import ProactiveTrigger

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

    def confirm(self, kind: Optional[TriggerKind], tick: ProactiveTickSignals) -> None:
        """决策结果回写：触发原因命中的信号确认消费（game / rundown）。

        原因与信号的对应：``TriggerKind.GAME``→game、``TriggerKind.RUNDOWN``→rundown。
        rundown 的原因也可能来自超时/就绪分支——仅当本 tick 读到
        rundown 信号才认是它引发的（优先级保证信号在场时必然走信号
        分支）。``None``（被公共前置拦下）或别的原因什么都不清：
        信号保留，条件重新满足时补触发。
        """
        if tick.game_pending and kind is TriggerKind.GAME:
            self._game = False
        if tick.rundown_pending and kind is TriggerKind.RUNDOWN:
            self._rundown = False

    # ----- 每 tick 判定（读信号 → 折参 → 判定 → 确认消费，单点序） -----

    def evaluate_tick(
        self,
        *,
        live: bool,
        trigger: ProactiveTrigger,
        room_state: Any,
        now_ms: int,
        rundown_overdue: bool,
        rundown_ready: bool,
        reminder_pending: bool,
    ) -> Optional[TriggerKind]:
        """执行一个 tick 的完整主动发言判定序列，返回触发原因（None = 不触发）。

        本方法是一个完整的主动发言判定序列，顺序约束由实现结构保证而非注释契约：

        1. ``read_for_tick(live=...)``：读本 tick 信号读数（下播全 False 不消费）；
        2. 下播时把 ``rundown_overdue`` / ``rundown_ready`` 强制 False（未开播
           不说开场白/推进环节，流程单协调器的状态在闸外不参与判定）；
        3. ``trigger.should_trigger(...)``：纯规则判定（reminder > rundown >
           game > external > ... 的优先级与各自限流豁免都在触发器内）；
        4. ``confirm(kind, tick)``：无论触发与否都回写确认（每个 tick
           恰好一次；game / rundown 仅在触发原因命中时消费，被 reminder
           抢先或总开关关闭时保留待补触发）。

        调用方拿到返回的触发原因后只负责执行：触发则发起一轮主动决策，
        不触发则本轮结束。"reminder 早退不消费 pending"属于调用方是否
        进入本判定的前置闸（递话必达优先于开播闸），不在本方法内。
        """
        tick = self.read_for_tick(live=live)
        kind = trigger.should_trigger(
            room_state,
            now_ms,
            external_pending=tick.external_pending,
            rundown_pending=tick.rundown_pending,
            rundown_overdue=False if not live else rundown_overdue,
            rundown_ready=False if not live else rundown_ready,
            game_pending=tick.game_pending,
            reminder_pending=reminder_pending,
        )
        self.confirm(kind, tick)
        return kind
