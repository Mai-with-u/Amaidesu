"""叫停回执：急停或撤单之后，游戏里哪些目标停下了、哪些没停下、手上的活撤没撤，写成一句给主播。

急停（``stop_actions``）和撤单（``cancel_task_now``）都先向游戏直连取消在跑的目标，再处理委派单。
直连取消会失败（游戏没连上、目标在游戏那边已经不在），过去失败只记日志，主播那边以为全停了，
对观众说"停下了"时角色可能还在挖。回执把每个目标的取消结果与委派单的去留写成一句，
由游戏 Agent 作为 ``attention_required`` 事件发出，进主播的时间线。

这里只拼文字，不碰游戏也不碰账面：取消与记账仍由 Agent 原路径完成，行为不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

__all__ = ["GoalStopOutcome", "cancel_receipt", "stop_goal_label", "stop_receipt"]

# 能力名的命名空间前缀：念给主播没有信息量
_ABILITY_PREFIX = "maicraft:"


@dataclass
class GoalStopOutcome:
    """一次直连取消在跑目标的结果：停下的目标、没停下的目标与原因（都按目标在做什么列出）。"""

    stopped: List[str] = field(default_factory=list)
    not_stopped: List[Tuple[str, str]] = field(default_factory=list)


def stop_goal_label(ability: str, purpose: str) -> str:
    """回执里怎么称呼一个目标：有目的写目的（"挖铁矿"），没有就写去掉前缀的能力名（"gather"）。"""
    return purpose.strip() or ability.removeprefix(_ABILITY_PREFIX)


def _goals_part(outcome: GoalStopOutcome) -> str:
    """目标那一段：都停下了 / 停下了哪些、哪些没停下（附原因、可能还在做）/ 本来就没有在跑的。"""
    if not outcome.stopped and not outcome.not_stopped:
        return "叫停时没有在跑的目标"
    if not outcome.not_stopped:
        return f"在跑的目标都停下了（{'、'.join(outcome.stopped)}）"
    parts: List[str] = []
    if outcome.stopped:
        parts.append(f"停下了{'、'.join(outcome.stopped)}")
    failed = "；".join(f"{label}没停下（{reason}）" for label, reason in outcome.not_stopped)
    parts.append(f"{failed}，可能还在做")
    return "；".join(parts)


def stop_receipt(outcome: GoalStopOutcome, *, held_delegations: int, cancel_delegations: bool) -> str:
    """急停回执：操作员叫停 → 目标停没停 → 手上的活撤没撤（默认不撤，账面还在）。

    ``held_delegations`` 是叫停前手上记着的委派单数：没有活就不提活的去留，
    免得她以为手上还有一件事。
    """
    text = f"操作员急停了游戏里的动作：{_goals_part(outcome)}"
    if cancel_delegations:
        text += "；手上的活一并撤了" if held_delegations else "；手上本来没有活"
    elif held_delegations:
        text += "；手上的活没撤，账面还在，等下一步安排"
    return text


def cancel_receipt(outcome: GoalStopOutcome, *, cancelled: bool, by_operator: bool) -> str:
    """撤单回执：手上的活撤没撤成（谁撤的）→ 目标停没停。

    目标在核对任务号之前就先叫停了（让游戏立刻停手），所以没撤成时目标那一段照样如实写。
    """
    who = "操作员" if by_operator else "我"
    head = f"{who}撤掉了游戏里手上的活" if cancelled else f"{who}要撤的活游戏这边没找到，没撤成"
    return f"{head}：{_goals_part(outcome)}"
