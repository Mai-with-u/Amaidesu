"""解说前提守卫：``framework_commentary`` 提交解说前的两条件校验。

问题：游戏 Agent 解说的依据是目标的"已受理"，而目标结果晚一步才回来；解说在结果
回来前写好出口，结果与解说不符时观众听到过时说法。守卫在解说提交处做两条件校验，
命中即立即返回校验错误（不阻塞、不等待，拒绝作为工具错误回到模型，下一步带着已
入队的结果回执重写）：

- 条件①（刚下达瞬态窗）：存在受守卫的"已下达、未收到终态"且下达时刻距今不足暂扣
  窗 N（默认 2 秒）的目标 → 拒绝；收到 ``[直播间静默提醒]`` 后的首次解说豁免本条件。
- 条件②（写作期间依据变了）：本步推理开始时处于受守卫"已下达未终态"名单里的目标，
  在本步推理期间变为终态 → 拒绝（快照在每步推理开始排空唤醒队列后拍）；静默提醒不
  豁免本条件，确保终态回执优先于过时解说。
- 非世界状态变更辅助能力（``maicraft:chat`` / ``maicraft:find`` / ``maicraft:body_language``
  / ``maicraft:timer``）不纳入守卫，既不触发条件①也不进入条件②快照。
- 豁免：``correction=True``（纠偏）或 ``urgency=high``（突发高优）的调用全豁免。

超过 N 仍无结果的目标放行（保住长任务期间的实况）；这道门只管时机，不管措辞。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, List, Literal, Optional, Tuple

GuardCondition = Literal["recently_issued", "settled_during_step"]

#: 默认条件①瞬态暂扣窗（毫秒）：仅拦截刚下达尚未落稳的极短窗口
DEFAULT_GUARD_WINDOW_MS = 2_000
#: 条件①与条件②的结构化标识（供可观测事件与复盘统计消费）
CONDITION_RECENTLY_ISSUED: GuardCondition = "recently_issued"
CONDITION_SETTLED_DURING_STEP: GuardCondition = "settled_during_step"

#: 不触发解说前提守卫的非世界状态变更辅助能力（规范全名集合）
NON_GUARDED_ABILITIES: frozenset[str] = frozenset(
    {
        "maicraft:chat",
        "maicraft:find",
        "maicraft:body_language",
        "maicraft:timer",
    }
)

__all__ = [
    "CONDITION_RECENTLY_ISSUED",
    "CONDITION_SETTLED_DURING_STEP",
    "CommentaryPremiseGuard",
    "DEFAULT_GUARD_WINDOW_MS",
    "GuardCondition",
    "GuardRejection",
    "NON_GUARDED_ABILITIES",
    "ability_short_name",
    "is_exempt",
    "is_guarded_ability",
]


def ability_short_name(ability: str) -> str:
    """提取去掉命名空间前缀的能力短名（如 ``maicraft:chat`` → ``chat``）。"""
    return str(ability or "").strip().rsplit(":", 1)[-1].lower()


_NON_GUARDED_SHORT_NAMES: frozenset[str] = frozenset(ability_short_name(name) for name in NON_GUARDED_ABILITIES)


def is_guarded_ability(ability: str) -> bool:
    """判断能力是否纳入解说前提守卫：排除发游戏公屏、查找、肢体动作与设倒计时等辅助能力。"""
    return ability_short_name(ability) not in _NON_GUARDED_SHORT_NAMES


def is_exempt(arguments: Mapping[str, Any]) -> bool:
    """纠偏（correction=True）与突发高优（urgency=high）的解说不受守卫拦截。"""
    if arguments.get("correction") in (True, "true", "True", 1):
        return True
    return str(arguments.get("urgency", "") or "").strip().lower() == "high"


@dataclass(frozen=True, slots=True)
class GuardRejection:
    """解说前提守卫拒回的结构化结果：同时用于返回模型的错误文案与发布可观测事件。"""

    condition: GuardCondition
    blocking_goal_ids: Tuple[int, ...]
    window_ms: int
    message: str


class CommentaryPremiseGuard:
    """按两条件判断本次解说提交是否该被拒回；快照与判定共用一个单调毫秒时钟。"""

    def __init__(self, *, window_ms: int = DEFAULT_GUARD_WINDOW_MS, clock_ms: Callable[[], int]) -> None:
        self._window_ms = max(0, int(window_ms))
        self._clock_ms = clock_ms
        # 本步推理开始时受守卫的"已下达未终态"目标编号快照；begin_step 每步推理开始时刷新
        self._step_start_pending: frozenset[int] = frozenset()

    @property
    def window_ms(self) -> int:
        """当前生效的条件①瞬态暂扣窗（毫秒）。"""
        return self._window_ms

    def begin_step(self, pending_ids: Iterable[int]) -> None:
        """每步推理开始（排空唤醒队列后）给当前受守卫的未决目标编号名单拍快照。"""
        self._step_start_pending = frozenset(pending_ids)

    def evaluate(
        self,
        pending: Mapping[int, int],
        *,
        skip_condition1: bool = False,
    ) -> Optional[GuardRejection]:
        """判定本次解说提交：命中条件返回结构化拒回结果，放行返回 None。

        ``pending`` 是当前受守卫的在跟踪目标（已下达、未收到终态，且已排除辅助能力）
        的编号 → 下达时刻表（单调毫秒，来自 ``GoalWatch.pending_issuance``）。
        当 ``skip_condition1`` 为真时（例如刚收到 ``[直播间静默提醒]`` 的首次解说），
        豁免条件①的瞬态暂扣窗，但仍严格检查条件②（本步推理期间刚变为终态必须重写）。
        """
        now_ms_value = self._clock_ms()
        if not skip_condition1:
            # 条件①：目标刚下达尚未过极短瞬态窗 -> 拒回，提示可在发起目标前交代打算或稍等落稳
            unsettled: List[int] = sorted(
                goal_id for goal_id, issued_at in pending.items() if now_ms_value - issued_at < self._window_ms
            )
            if unsettled:
                window_s = self._window_ms / 1000
                window_text = f"{int(window_s)} 秒" if window_s == int(window_s) else f"{window_s:.1f} 秒"
                ids_text = "、".join(f"#{goal_id}" for goal_id in unsettled)
                message = (
                    f"解说暂不受理：目标 {ids_text} 刚下达不足 {window_text}，尚未落稳。"
                    f"若要向观众交代行动打算，可在发起目标前提交解说（或同一步把解说放在执行之前）；"
                    f"也可稍等该目标落地或返回结果回执后再提交解说。"
                )
                return GuardRejection(
                    condition=CONDITION_RECENTLY_ISSUED,
                    blocking_goal_ids=tuple(unsettled),
                    window_ms=self._window_ms,
                    message=message,
                )
        # 条件②：本步推理开始时未决、此刻已离开未决表的受守卫目标（含完成/失败/取消/换世界消失）-> 必须重写
        settled_this_step: List[int] = sorted(self._step_start_pending - set(pending))
        if settled_this_step:
            ids_text = "、".join(f"#{goal_id}" for goal_id in settled_this_step)
            message = (
                f"解说暂不受理：目标 {ids_text} 在你写这段解说期间刚变为终态，"
                f"解说写作时的依据已经变了。下一步会收到它们的结果回执，"
                f"看清结果后重写解说再提交；也可先让出等待。"
            )
            return GuardRejection(
                condition=CONDITION_SETTLED_DURING_STEP,
                blocking_goal_ids=tuple(settled_this_step),
                window_ms=self._window_ms,
                message=message,
            )
        return None

    def check(self, pending: Mapping[int, int], *, skip_condition1: bool = False) -> Optional[str]:
        """判定本次解说提交：命中条件返回拒绝文案（作为工具错误回到模型），放行返回 None。"""
        rejection = self.evaluate(pending, skip_condition1=skip_condition1)
        return rejection.message if rejection is not None else None
