"""挂起等待目标事件期间的进展监视。

游戏 Agent 让出回合等后台目标时，宿主在站桩期间监视目标是否仍在推进：
- 目标状态事件（提问、暂停、恢复、结束）、目标自身步数或动作阶段变化、角色在非急事抢占下的
  实际位移（三维移动不少于 2 格或跨维度），都算有效进展，刷新计时并重置退避；
- 游戏 Agent 主动用 ``maicraft:timer`` 设了未到期的计时器、且在跑目标并非卡在第 0 步被生存急事
  堵死时，尊重它设定的查看间隔，不在闹钟到点前抢先叫醒；
- 同一组目标连续无进展被叫醒后继续等待的，无进展阈值按指数退避拉长（30s → 60s → 120s → 240s），
  一旦目标集合改变或出现任何有效进展即恢复初始阈值。

叫醒只是请模型核对现场的机会：长途游泳、大范围作业仍在正常推进时可继续等待，宿主不替它取消目标。
时刻用注入的单调时钟（秒）；非线程安全，只在 Agent 事件循环里用。
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from src.agents.minecraft.furnace_wait import TIMER_ABILITY
from src.agents.minecraft.maicraft import GoalRun

__all__ = [
    "POSITION_PROGRESS_BLOCKS",
    "USER_TIMER_SLACK_S",
    "WAIT_PROGRESS_LIMIT_S",
    "WAIT_PROGRESS_MAX_LIMIT_S",
    "Coords",
    "WaitProgress",
    "is_blocked_by_reflex",
    "is_stuck_at_step_zero",
    "self_coords",
    "watchdog_wakeup_notice",
]

#: 站桩无进展的初始上限（秒）
WAIT_PROGRESS_LIMIT_S = 30.0
#: 同一组目标连续叫醒时的最大退避上限（秒）：30 -> 60 -> 120 -> 240
WAIT_PROGRESS_MAX_LIMIT_S = 240.0
#: 同一维度内位移达到多少格算一次角色移动进展
POSITION_PROGRESS_BLOCKS = 2.0
#: 游戏 Agent 自设计时器的送达宽限（秒）：留出游戏刻抖动余量，超时仍未响则恢复看门狗兜底
USER_TIMER_SLACK_S = 15.0

#: 维度 + 三维坐标
Coords = Tuple[str, float, float, float]

_TIMER_DUE_ID = re.compile(r"计时\s*(\d+)\s*到点")
_STEP_NUMBER = re.compile(r"第\s*(\d+)\s*步")


def self_coords(data: Mapping[str, Any]) -> Optional[Coords]:
    """从 ``observe(self)`` 的 ``data`` 取出 ``(维度, x, y, z)``；缺字段时返回 ``None``。"""
    pos = data.get("position")
    if not isinstance(pos, Mapping):
        return None
    dim = str(pos.get("dimension") or "")
    vals = [pos.get(axis) for axis in ("x", "y", "z")]
    if not dim or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return None
    return dim, float(vals[0]), float(vals[1]), float(vals[2])


def is_blocked_by_reflex(doing: str) -> bool:
    """目标此刻是否正被生存需求的临时任务抢占或处于已暂停态（此时角色位移来自自卫/换气，不算主目标推进）。"""
    text = (doing or "").strip()
    return text.startswith("生存需求的临时任务") or "已暂停" in text


def is_stuck_at_step_zero(doing: str) -> bool:
    """目标是否连第一步都没迈出就被生存需求的临时任务堵在第 0 步（此时不因长计时器压住首次有效告警）。"""
    text = (doing or "").strip()
    return text.startswith("生存需求的临时任务") and "第 0 步" in text


def watchdog_wakeup_notice(
    goal_ids: Sequence[int],
    stalled_s: float,
    *,
    doings: Optional[Mapping[int, str]] = None,
) -> str:
    """生成中性的无进展核对提示：陈述等待时长与当前状态，不把取消作为默认导向。"""
    ids_text = ", ".join(str(g) for g in goal_ids)
    detail_parts = [f"{gid}: {text.strip()}" for gid in goal_ids if doings and (text := doings.get(gid, "").strip())]
    status_clause = f"当前状态：{'；'.join(detail_parts)}。" if detail_parts else ""
    return (
        f"[系统] 挂起的目标（{ids_text}）已 {stalled_s:.0f} 秒未见新事件。{status_clause}"
        "请核对现场进展：若目标仍在正常推进（如长途赶路、连续作业或等待前置完成），"
        "可直接结束本轮继续等待（预计耗时较长也可用 timer 设定下次查看间隔）；"
        "仅在确认目标确实受阻无法推进时，再调整办法或取消重下。"
    )


class WaitProgress:
    """站桩期间的计时、多维进展识别、自设计时器礼让与指数退避。"""

    def __init__(
        self,
        clock: Callable[[], float],
        limit_s: float = WAIT_PROGRESS_LIMIT_S,
        max_limit_s: float = WAIT_PROGRESS_MAX_LIMIT_S,
    ) -> None:
        self._clock = clock
        self._limit_s = limit_s
        self._max_limit_s = max(limit_s, max_limit_s)
        self._standing_since: Optional[float] = None
        self._last_progress_at: Optional[float] = None
        self._goal_ids: List[int] = []
        self._anchor_pos: Optional[Coords] = None
        self._goal_doings: Dict[int, str] = {}
        # 连续叫醒退避：同一组目标在无新进展下连续被叫醒时翻倍，换目标或有进展归零
        self._alert_goals: Tuple[int, ...] = ()
        self._consecutive_alerts: int = 0
        # 游戏 Agent 主动设定的 maicraft:timer 到期时刻（timer_id -> 单调时钟秒）
        self._user_timers: Dict[int, float] = {}
        self._synthetic_timer_seq: int = -1

    @property
    def limit_s(self) -> float:
        """基础无进展上限（秒）。"""
        return self._limit_s

    @property
    def current_limit_s(self) -> float:
        """当前生效的无进展上限（秒，含连续叫醒指数退避）。"""
        scaled = self._limit_s * (2**self._consecutive_alerts)
        return min(self._max_limit_s, scaled)

    @property
    def consecutive_alerts(self) -> int:
        """同一组目标已连续触发无进展叫醒的次数。"""
        return self._consecutive_alerts

    @property
    def standing(self) -> bool:
        """当前是否在站桩（让出回合等目标事件）。"""
        return self._standing_since is not None

    @property
    def goal_ids(self) -> List[int]:
        """这次站桩在等的目标编号。"""
        return list(self._goal_ids)

    def stand(
        self,
        goal_ids: Sequence[int],
        *,
        initial_pos: Optional[Coords] = None,
        initial_doings: Optional[Mapping[int, str]] = None,
    ) -> None:
        """站桩开始：记录目标集合与起始位置/动作基线；若目标集合已变则清空连续退避计数。"""
        now = self._clock()
        normalized_ids = tuple(sorted(goal_ids))
        if normalized_ids != self._alert_goals:
            self._consecutive_alerts = 0
            self._alert_goals = ()
        self._standing_since = now
        self._last_progress_at = now
        self._goal_ids = list(goal_ids)
        self._anchor_pos = initial_pos
        self._goal_doings = {}
        if initial_doings:
            for gid, doing in initial_doings.items():
                clean = (doing or "").strip()
                if clean and not is_blocked_by_reflex(clean):
                    self._goal_doings[int(gid)] = clean

    def resume(self) -> None:
        """站桩结束（Agent 醒来做事了；保留同目标退避计数，供确认继续等待时衔接）。"""
        self._standing_since = None
        self._last_progress_at = None
        self._goal_ids = []
        self._anchor_pos = None
        self._goal_doings = {}

    def reset(self) -> None:
        """任务整体结束、开新任务或下播：站桩态、退避计数与自设计时器全部清零。"""
        self.resume()
        self._alert_goals = ()
        self._consecutive_alerts = 0
        self._user_timers.clear()

    def note_progress(self) -> None:
        """目标状态变化、有效位移或步数推进：重置退避计数，站桩中同步刷新进展时刻。"""
        self._consecutive_alerts = 0
        self._alert_goals = ()
        if self._standing_since is None:
            return
        self._last_progress_at = self._clock()

    def note_alert(self) -> None:
        """本次看门狗叫醒已发出：记录目标集合并推进退避档位。"""
        self._alert_goals = tuple(sorted(self._goal_ids))
        self._consecutive_alerts += 1

    # ---- 自设计时器（maicraft:timer）跟踪 ----

    def absorb_timer(self, run: GoalRun, arguments: Optional[Mapping[str, Any]] = None) -> None:
        """吸收一次 ``maicraft:timer`` 执行结果，同步当前挂着的自设计时器预计到期时刻。"""
        if run.ability != TIMER_ABILITY or run.result_status not in ("done", ""):
            return
        now = self._clock()
        result = run.result or {}
        details = result.get("details") if isinstance(result.get("details"), Mapping) else {}
        pending = details.get("pending")
        if isinstance(pending, list):
            updated: Dict[int, float] = {}
            for item in pending:
                if not isinstance(item, Mapping):
                    continue
                tid = item.get("timer_id")
                rem = item.get("remaining_seconds")
                if isinstance(tid, int) and not isinstance(tid, bool) and isinstance(rem, (int, float)):
                    updated[tid] = now + max(0.0, float(rem))
            self._user_timers = updated
            return
        goal_arg = (arguments or {}).get("goal") if isinstance(arguments, Mapping) else None
        params = goal_arg.get("parameters") if isinstance(goal_arg, Mapping) else None
        if not isinstance(params, Mapping):
            return
        op = str(params.get("operation") or "set")
        if op == "set":
            after_s = params.get("after_seconds")
            tid = details.get("timer_id")
            if isinstance(after_s, (int, float)) and not isinstance(after_s, bool) and after_s > 0:
                if not isinstance(tid, int) or isinstance(tid, bool):
                    tid = self._synthetic_timer_seq
                    self._synthetic_timer_seq -= 1
                self._user_timers[tid] = now + float(after_s)
        elif op == "cancel":
            tid = params.get("timer_id")
            if isinstance(tid, int) and not isinstance(tid, bool):
                self._user_timers.pop(tid, None)

    def timer_due(self, event: Mapping[str, Any]) -> None:
        """收到 ``timer_due``：划掉到点的自设计时器并记一次进展。"""
        tid = event.get("timer_id")
        if not isinstance(tid, int) or isinstance(tid, bool):
            matched = _TIMER_DUE_ID.search(str(event.get("message") or ""))
            tid = int(matched.group(1)) if matched is not None else None
        if tid is not None:
            self._user_timers.pop(tid, None)
        else:
            now = self._clock()
            self._user_timers = {k: due for k, due in self._user_timers.items() if due > now}
        self.note_progress()

    def clear_timers(self) -> None:
        """换存档时 Mod 已清空全部计时器，本地同步清空。"""
        self._user_timers.clear()

    def suppressed_by_timer(self, goal_doings: Optional[Mapping[int, str]] = None) -> bool:
        """是否存在仍在有效期内的自设计时器（且在跑目标未全部卡在第 0 步被生存急事堵死）。"""
        now = self._clock()
        self._user_timers = {tid: due for tid, due in self._user_timers.items() if due + USER_TIMER_SLACK_S > now}
        if not self._user_timers:
            return False
        source = goal_doings if goal_doings is not None else self._goal_doings
        relevant = [str(source.get(gid) or "").strip() for gid in self._goal_ids if str(source.get(gid) or "").strip()]
        if relevant and all(is_stuck_at_step_zero(text) for text in relevant):
            return False
        return True

    # ---- 位置与目标步数观测 ----

    def observe_state(
        self,
        position: Optional[Coords],
        goal_doings: Optional[Mapping[int, str]] = None,
    ) -> bool:
        """用最新角色坐标与在跑目标 ``doing`` 判断是否产生了新进展；有进展时刷新计时并返回 ``True``。"""
        progressed = False
        active_texts: List[str] = []
        if goal_doings:
            for gid in self._goal_ids or list(goal_doings):
                raw = str(goal_doings.get(gid) or "").strip()
                if not raw:
                    continue
                active_texts.append(raw)
                if is_blocked_by_reflex(raw):
                    continue
                prev = self._goal_doings.get(gid)
                if prev is None:
                    self._goal_doings[gid] = raw
                    # execute 当刻还未写出第 1 步文案、首次核对已走到第 2 步及以后：说明期间已完成前置步骤
                    matched_step = _STEP_NUMBER.search(raw)
                    if matched_step is not None and int(matched_step.group(1)) >= 2:
                        progressed = True
                elif raw != prev:
                    self._goal_doings[gid] = raw
                    progressed = True

        if position is not None:
            if self._anchor_pos is None:
                self._anchor_pos = position
            else:
                moved = (
                    position[0] != self._anchor_pos[0]
                    or math.dist(position[1:], self._anchor_pos[1:]) >= POSITION_PROGRESS_BLOCKS
                )
                if moved:
                    # 无论是否被生存急事占着都更新位置锚点，避免急事刚结束时把逃跑位移补算进主目标进展
                    self._anchor_pos = position
                    all_blocked = bool(active_texts) and all(is_blocked_by_reflex(t) for t in active_texts)
                    if not all_blocked:
                        progressed = True

        if progressed:
            self.note_progress()
        return progressed

    # ---- 计时读数 ----

    def standing_seconds(self) -> float:
        """这一段站桩了多久（秒）。"""
        if self._standing_since is None:
            return 0.0
        return max(0.0, self._clock() - self._standing_since)

    def stalled_seconds(self) -> float:
        """距上一次进展过了多久（秒）。"""
        if self._last_progress_at is None:
            return 0.0
        return max(0.0, self._clock() - self._last_progress_at)

    def remaining_seconds(self) -> float:
        """距离当前生效的无进展上限还剩多久（秒；站桩中才有意义）。"""
        return max(0.0, self.current_limit_s - self.stalled_seconds())

    def past_limit(self) -> bool:
        """站桩距上次进展是否已达当前退避上限（不含计时器抑制判定，供宿主决定是否先读一次现场）。"""
        return self._standing_since is not None and self.stalled_seconds() >= self.current_limit_s

    def due(self, goal_doings: Optional[Mapping[int, str]] = None) -> bool:
        """站桩超过当前退避上限、期间无进展且未被自设计时器抑制时，返回 ``True``。"""
        if not self.past_limit():
            return False
        if self.suppressed_by_timer(goal_doings):
            return False
        return True
