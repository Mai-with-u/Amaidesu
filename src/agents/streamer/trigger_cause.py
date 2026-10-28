"""触发原因值对象：一次决策轮"为什么被触发"的类型化描述。

触发原因贯穿 Agent → 决策执行器 → Planner → 主动信号对账这条接缝。
词表由本模块的封闭枚举单点定义；若以自由字符串传递（``"proactive:rundown"``
/ ``"window_expired"`` 等），同一份词表要在多个组件里各抄一份、靠约定保持
一致。生产方（Agent）构造 :class:`TriggerCause`，消费方（文案翻译、豁免判断、
信号确认）按 :class:`TriggerKind` 判定。

对外可观测形态不变：``planner.decision.trigger_reason`` 等事件/结果视图里
的字符串由 :meth:`TriggerCause.to_reason_string` 派生，格式与既有字符串
逐字一致，Dashboard 与时间线消费方零改动。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict

__all__ = [
    "NO_INPUT_PROACTIVE_KINDS",
    "PROACTIVE_KINDS",
    "TriggerCause",
    "TriggerKind",
    "TriggerSource",
    "cause_for_batch_flush",
]


class TriggerKind(str, Enum):
    """触发原因种类（封闭枚举，成员覆盖现有全部触发词表）。

    - 批次刷新类（MessageBuffer.should_flush 产出）：弹幕聚合窗的四种刷新原因。
    - 主动发言类（ProactiveTrigger.should_trigger 产出 + 下播道别）：主动决策窗的触发源。
    - 控制台调试类（Dashboard 调试门面）：手动驱动一次决策。
    """

    # 批次刷新类
    FORCED = "forced"  # 批内含付费触发消息（SC / 礼物 / 上舰）
    BATCH_FULL = "batch_full"  # 弹幕攒满一批
    WINDOW_EXPIRED = "window_expired"  # 聚合窗口到期
    IDLE_COMPENSATION = "idle_compensation"  # 冷场补足一批
    # 主动发言类
    EXTERNAL = "external"  # 外部点播唤醒（Dashboard / API）
    SCHEDULE = "schedule"  # 定时主动开麦
    COLD = "cold"  # 冷场主动开麦
    REMINDER = "reminder"  # 运营递话催醒
    RUNDOWN = "rundown"  # 流程单推进（环节切换 / 超时提醒 / 持续产出）
    GAME = "game"  # 游戏 Agent 待定夺
    SIGNOFF = "signoff"  # 收播道别
    # 控制台调试类
    DASHBOARD_DEBUG = "dashboard_debug"  # 控制台手动决策测试


class TriggerSource(str, Enum):
    """触发入口（这个触发原因从哪条链路来）。"""

    TICK = "tick"  # 后台 flush 循环 tick：批次聚合与主动发言判定的常规入口（默认）
    LIVE_END = "live_end"  # live.ended 回调：下播道别窗
    DASHBOARD = "dashboard"  # Dashboard 调试门面：手动驱动一次决策


#: 主动发言类触发种类：对外字符串带 ``proactive:`` 前缀的原因集合。
PROACTIVE_KINDS: frozenset[TriggerKind] = frozenset(
    {
        TriggerKind.EXTERNAL,
        TriggerKind.SCHEDULE,
        TriggerKind.COLD,
        TriggerKind.REMINDER,
        TriggerKind.RUNDOWN,
        TriggerKind.GAME,
        TriggerKind.SIGNOFF,
    }
)

#: 没有任何新外部输入的主动触发种类（流程单推进、定时、冷场、收播道别）：
#: 这些决策窗不给手上的游戏活补充要求（Planner 豁免判断用）。
NO_INPUT_PROACTIVE_KINDS: frozenset[TriggerKind] = frozenset(
    {
        TriggerKind.RUNDOWN,
        TriggerKind.SCHEDULE,
        TriggerKind.COLD,
        TriggerKind.SIGNOFF,
    }
)

#: 批次刷新原因（MessageBuffer.should_flush 触发分支的返回值）→ 触发种类。
#: MessageBuffer 的判定返回值保持字符串形态（不触发分支附带折算明细等诊断
#: 文本），触发分支经此映射收成类型；词表唯一出处即此映射的键。
_BATCH_FLUSH_KINDS: Dict[str, TriggerKind] = {
    TriggerKind.FORCED.value: TriggerKind.FORCED,
    TriggerKind.BATCH_FULL.value: TriggerKind.BATCH_FULL,
    TriggerKind.WINDOW_EXPIRED.value: TriggerKind.WINDOW_EXPIRED,
    TriggerKind.IDLE_COMPENSATION.value: TriggerKind.IDLE_COMPENSATION,
}


@dataclass(frozen=True)
class TriggerCause:
    """一次决策轮的触发原因（kind × source 的不可变值对象）。"""

    kind: TriggerKind
    source: TriggerSource = TriggerSource.TICK

    def to_reason_string(self, *, proactive: bool) -> str:
        """派生对外观测字符串（``planner.decision.trigger_reason`` 等字段的原格式）。

        ``proactive`` 为本轮决策的主动发言标志；控制台调试原因的字符串形态
        随它分化（主动调试窗带前缀，普通测试窗用 ``dashboard:`` 前缀），
        其余种类与 proactive 无关。
        """
        if self.kind is TriggerKind.DASHBOARD_DEBUG:
            return "proactive:dashboard_debug" if proactive else "dashboard:debug_test"
        if self.kind in PROACTIVE_KINDS:
            return f"proactive:{self.kind.value}"
        return self.kind.value


def cause_for_batch_flush(reason: str) -> TriggerCause:
    """批次刷新原因字符串 → 触发原因（Agent 在 flush 分支构造 TriggerCause 用）。

    未知名抛错而非兜底：批次刷新原因是 MessageBuffer 与本模块的封闭内部
    契约，出现映射外的值即词表漂移，应当场暴露而不是静默落进错误文案。
    """
    kind = _BATCH_FLUSH_KINDS.get(reason)
    if kind is None:
        raise ValueError(f"未知的批次刷新触发原因: {reason!r}（合法值：{sorted(_BATCH_FLUSH_KINDS)}）")
    return TriggerCause(kind=kind)
