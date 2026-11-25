"""解说前提守卫：``framework_commentary`` 提交解说前的两条件校验。

问题：游戏 Agent 解说的依据是目标的"已受理"，而目标结果晚一步才回来；解说在结果
回来前写好出口，结果与解说不符时观众听到过时说法。守卫在解说提交处做两条件校验，
命中即立即返回校验错误（不阻塞、不等待，拒绝作为工具错误回到模型，下一步带着已
入队的结果回执重写）：

- 条件①（依据未落定）：存在"已下达、未收到终态"且下达时刻距今不足暂扣窗 N 的
  目标 → 拒绝。
- 条件②（写作期间依据变了）：本步推理开始时处于"已下达未终态"名单里的目标，在
  本步推理期间变为终态 → 拒绝（快照在每步推理开始排空唤醒队列后拍）。
- 豁免：``correction=True``（纠偏）或 ``urgency=high``（突发高优）的调用不拦。

超过 N 仍无结果的目标放行（保住长任务期间的实况）；这道门只管时机，不管措辞。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, List, Optional

__all__ = ["CommentaryPremiseGuard", "is_exempt"]


def is_exempt(arguments: Mapping[str, Any]) -> bool:
    """纠偏（correction=True）与突发高优（urgency=high）的解说不受守卫拦截。"""
    if arguments.get("correction") in (True, "true", "True", 1):
        return True
    return str(arguments.get("urgency", "") or "").strip().lower() == "high"


class CommentaryPremiseGuard:
    """按两条件判断本次解说提交是否该被拒回；快照与判定共用一个单调毫秒时钟。"""

    def __init__(self, *, window_ms: int, clock_ms: Callable[[], int]) -> None:
        self._window_ms = max(0, int(window_ms))
        self._clock_ms = clock_ms
        # 本步推理开始时"已下达未终态"的目标编号快照；begin_step 每步推理开始时刷新
        self._step_start_pending: frozenset = frozenset()

    def begin_step(self, pending_ids: frozenset | set) -> None:
        """每步推理开始（排空唤醒队列后）给当前未决目标编号名单拍快照。"""
        self._step_start_pending = frozenset(pending_ids)

    def check(self, pending: Mapping[int, int]) -> Optional[str]:
        """判定本次解说提交：命中条件返回拒绝文案（作为工具错误回到模型），放行返回 None。

        ``pending`` 是当前在跟踪目标（已下达、未收到终态）的编号 → 下达时刻表（单调毫秒，
        来自 ``GoalWatch.pending_issuance``）。快照里已有、此刻不在表里的编号即本步
        推理期间变为终态的目标（含被取消、换世界消失）。
        """
        now_ms_value = self._clock_ms()
        unsettled: List[int] = sorted(
            goal_id for goal_id, issued_at in pending.items() if now_ms_value - issued_at < self._window_ms
        )
        if unsettled:
            window_s = self._window_ms / 1000
            window_text = f"{int(window_s)} 秒" if window_s == int(window_s) else f"{window_s:.1f} 秒"
            ids_text = "、".join(f"#{goal_id}" for goal_id in unsettled)
            return (
                f"解说暂不受理：目标 {ids_text} 刚下达还没回结果（距下达不足 {window_text}），"
                f"这时解说的事实依据还没落定。要么等这些目标返回结果（回来会叫醒你），"
                f"要么先让出等待；有了结果再提交解说，把结果照实说进去。"
            )
        settled_this_step: List[int] = sorted(self._step_start_pending - set(pending))
        if settled_this_step:
            ids_text = "、".join(f"#{goal_id}" for goal_id in settled_this_step)
            return (
                f"解说暂不受理：目标 {ids_text} 在你写这段解说期间刚变为终态，"
                f"解说写作时的依据已经变了。下一步会收到它们的结果回执，"
                f"看清结果后重写解说再提交；也可先让出等待。"
            )
        return None
