"""流程单（Rundown）运行时状态机

本模块是 ``RundownState``——流程单运行时**唯一变更边界**。

设计要点
- **可变字段仅五个**（公开四个 + 5th 进度锚点）：``rundown`` / ``index`` /
  ``segment_started_at_ms`` / ``paused_at_ms`` / ``rundown_started_at_ms``。
  加上若干私有字段（累计暂停 ms / 推进历史 deque / 注入时钟 / 注入回调）。
- **status 派生**：``"idle"`` / ``"running"`` / ``"paused"`` / ``"done"`` 由
  上述字段即时算出，不另存枚举——切片 1 已删除 v2 五值状态机。
- **变更方法统一五步走**：validate → mutate → append transition →
  emit ``rundown.changed`` → invoke ``on_changed``。
- **拒绝结构化**：未知环节 id / 未达 ``min_duration_ms`` 不抛异常，返回
  ``RundownReject``（含可用 id 列表 / 剩余等待 ms）——调用方拿到后注入
  Agent 上下文自纠。
- **可注入时钟**：方法 ``now_ms`` 参数 > ``clock()`` > 真实 ``now_ms``。
- **不持久化**：运行进度随进程失活而丢，重启即从头读流程单。
- **依赖注入**：``emit`` / ``on_changed`` / ``clock`` 均关键字参数可空，
  全部 fail-soft（None → no-op）；不依赖 ProactiveTrigger / 工具 / 配置 / Dashboard。

不在此处
- 调度循环（v2 AgendaIdle 删除；节奏归 ProactiveTrigger 的 ``rundown_overdue``
  触发源，本切片未涉及）
- 工具调用接口（v3 ``RundownControlTool`` 在切片 3）
- 持久化（v3 取消运行进度表，重启从头读）
- EventBus 订阅（仅 `` emit ``，不订阅）
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Literal, Optional, cast

from src.agents.streamer.rundown.rundown import Rundown, RundownSegment
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms as _real_now_ms


__all__ = ["RundownActor", "RundownReject", "RundownState", "TRANSITIONS_MAXLEN"]


# ---------------------------------------------------------------------------
# 拒绝结构（变更方法返回类型）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RundownReject:
    """变更方法拒绝的结构化原因。

    Attributes:
        reason: 人类可读原因码（如 ``"unknown_segment_id"`` / ``"min_duration_not_met"`` /
            ``"already_done"`` / ``"not_running"`` / ``"not_paused"``）
        available_ids: 当前流程单的全部环节 id（未知 id 拒绝时填充）
        remaining_ms: 距 ``min_duration_ms`` 还差多少 ms（``min_duration`` 拒绝时填充）
    """

    reason: str
    available_ids: List[str] = field(default_factory=list)
    remaining_ms: int = 0


# ---------------------------------------------------------------------------
# 推进历史环形缓冲容量
# ---------------------------------------------------------------------------


#: 推进历史环形缓冲容量（最近 50 条；超出时 deque 自动丢弃最早条目）。
#: 用于 Dashboard 调试可观察性：展示"为什么跳到这里 / 上一段停留多久"。
TRANSITIONS_MAXLEN: int = 50


# ---------------------------------------------------------------------------
# 触发主体（by）：goto / next / pause / resume 的入参白名单
# ---------------------------------------------------------------------------


#: 触发主体类型。``"agent"``=Agent 工具 / ``"human"``=Dashboard 手动 / ``"system"``=装配层。
RundownActor = Literal["agent", "human", "system"]

_VALID_BY: frozenset[str] = frozenset({"agent", "human", "system"})

#: ``min_duration_ms`` 守卫只约束 Agent 抢跑；human / system（导演与装配层）免检
_MIN_GUARD_BY: frozenset[str] = frozenset({"agent"})


def _format_duration_ms(ms: int) -> str:
    """毫秒时长的口语化渲染（分钟粒度；不足 1 分钟显示秒）。"""
    if ms <= 0:
        return "0 秒"
    minutes = ms / 60_000
    if minutes >= 1:
        return f"约 {round(minutes)} 分钟"
    return f"约 {max(1, round(ms / 1000))} 秒"


# ---------------------------------------------------------------------------
# RundownState
# ---------------------------------------------------------------------------


# 注入契约：emit 为同步回调（接线层负责桥接到 EventBus 的异步 emit）；
# on_changed 用于上层（ProactiveTrigger）置位 proactive 待决信号
EmitFn = Callable[[str, Any], None]
ChangedFn = Callable[[str, str], None]
ClockFn = Callable[[], int]


class RundownState:
    """流程单运行时状态机——唯一变更边界。

    非线程安全；仅在 StreamerAgent 单一 asyncio 事件循环内使用。
    通过方法调用驱动，**不**订阅 EventBus。

    Attributes:
        status: 派生状态（``"idle"`` / ``"running"`` / ``"paused"`` / ``"done"``）
        rundown: 当前流程单（``None`` = 未启用）
        index: 当前环节下标（``-1``=未开始，``len(segments)``=已结束）
        segment_started_at_ms: 当前环节开始锚点（Unix 毫秒）
        paused_at_ms: 非空 = 暂停中（计时冻结）
        rundown_started_at_ms: 整场时间轴锚点——``load()`` 时记录
    """

    def __init__(
        self,
        *,
        emit: Optional[EmitFn] = None,
        on_changed: Optional[ChangedFn] = None,
        clock: Optional[ClockFn] = None,
    ) -> None:
        """Args:
        emit: 同步事件回调 ``(event_name: str, payload) -> None``，
            接收 ``RundownChangedPayload`` 实例；接线层负责桥接到 EventBus
            的异步 emit。``None`` 表示不发射（fail-soft）。
        on_changed: 同步状态切换通知 ``(segment_id: str, by: str) -> None``，
            用于上层（如 ProactiveTrigger）置位 ``rundown_pending`` 信号。
            ``None`` 表示不通知（fail-soft）。
        clock: 可选时钟回调（无参 → int 毫秒）。优先级：方法参数 ``now_ms``
            > ``clock()`` > 真实 ``now_ms``。``None`` 表示使用真实时钟。
        """
        self.rundown: Optional[Rundown] = None
        self.index: int = -1
        self.segment_started_at_ms: int = 0
        self.paused_at_ms: Optional[int] = None
        self.rundown_started_at_ms: Optional[int] = None

        self._accumulated_pause_ms: int = 0
        self._transitions: Deque[Dict[str, Any]] = deque(maxlen=TRANSITIONS_MAXLEN)

        self._clock: Optional[ClockFn] = clock
        self._emit: Optional[EmitFn] = emit
        self._on_changed: Optional[ChangedFn] = on_changed
        self._logger = get_logger("RundownState")

    # ------------------------------------------------------------------
    # 时钟与派生状态
    # ------------------------------------------------------------------

    def _resolve_now(self, now_ms: Optional[int]) -> int:
        """解析可选的 ``now_ms``：参数 > 注入回调 > 真实时钟。"""
        if now_ms is not None:
            return now_ms
        if self._clock is not None:
            return self._clock()
        return _real_now_ms()

    @property
    def status(self) -> str:
        """派生状态：``"idle"`` / ``"running"`` / ``"paused"`` / ``"done"``。"""
        if self.rundown is None:
            return "idle"
        if self.paused_at_ms is not None:
            return "paused"
        if self.index >= len(self.rundown.segments):
            return "done"
        return "running"

    @property
    def current_segment_id(self) -> str:
        """当前环节 id（``index < 0`` 或 ``>= total`` 时返回空字符串）。"""
        if self.rundown is None or self.index < 0 or self.index >= len(self.rundown.segments):
            return ""
        return self.rundown.segments[self.index].id

    @property
    def current_segment(self) -> Optional[RundownSegment]:
        """当前环节对象（无则返回 ``None``）。"""
        if self.rundown is None or self.index < 0 or self.index >= len(self.rundown.segments):
            return None
        return self.rundown.segments[self.index]

    def _segment_elapsed_ms(self, now: int) -> int:
        """当前环节已用时长（扣除累计暂停；暂停中以 paused_at_ms 为基准冻结）。"""
        if self.current_segment is None:
            return 0
        effective_now = self.paused_at_ms if self.paused_at_ms is not None else now
        return max(0, effective_now - self.segment_started_at_ms - self._accumulated_pause_ms)

    def _min_duration_reject(self, *, by: str, now: int) -> Optional[RundownReject]:
        """Agent 抢跑守卫：``min_duration_ms`` 只约束 ``by="agent"``（导演/装配层免检）。

        当前环节未设下界、无当前环节、或停留已达标时返回 ``None``（放行）。
        """
        if by not in _MIN_GUARD_BY:
            return None
        seg = self.current_segment
        if seg is None or seg.min_duration_ms is None:
            return None
        elapsed = self._segment_elapsed_ms(now)
        if elapsed < seg.min_duration_ms:
            return RundownReject(
                reason="min_duration_not_met",
                remaining_ms=seg.min_duration_ms - elapsed,
            )
        return None

    # ------------------------------------------------------------------
    # 变更方法：validate → mutate → record → emit → notify
    # ------------------------------------------------------------------

    def load(self, rundown: Rundown, *, now_ms: Optional[int] = None) -> None:
        """加载流程单，重置游标到首段（index=0），清空暂停与累计暂停时长。

        总是允许（无前置条件）；调用方应在状态未启用时调一次。
        """
        self.rundown = rundown
        self.index = 0
        now = self._resolve_now(now_ms)
        self.segment_started_at_ms = now
        self.rundown_started_at_ms = now
        self.paused_at_ms = None
        self._accumulated_pause_ms = 0

        first = rundown.segments[0]
        self._apply_change(
            action="load",
            segment_id=first.id,
            segment_title=first.title,
            by="system",
            now_ms=now,
        )

    def goto(
        self,
        segment_id: str,
        *,
        by: str,
        now_ms: Optional[int] = None,
    ) -> Optional[RundownReject]:
        """跳到指定环节 id（不限方向）。

        ``by`` 仅接受 ``"agent"`` / ``"human"`` / ``"system"``，其他值抛
        ``ValueError``（程序员错误，不走结构化拒绝通道）。

        Returns:
            ``None`` 表示接受；``RundownReject`` 表示拒绝（不变更、不发事件）。
        """
        if by not in _VALID_BY:
            raise ValueError(f"RundownState.goto: 非法 by={by!r}，仅接受 {_VALID_BY}")
        by_actor = cast(RundownActor, by)
        if self.rundown is None:
            return RundownReject(reason="no_rundown_loaded")
        available = [seg.id for seg in self.rundown.segments]
        if segment_id not in available:
            return RundownReject(
                reason="unknown_segment_id",
                available_ids=available,
            )

        now = self._resolve_now(now_ms)
        reject = self._min_duration_reject(by=by, now=now)
        if reject is not None:
            return reject

        target_idx = next(i for i, seg in enumerate(self.rundown.segments) if seg.id == segment_id)
        target_seg = self.rundown.segments[target_idx]
        self.index = target_idx
        self.segment_started_at_ms = now
        # 跳到目标后清空暂停计时（目标段是新一段计时）
        self.paused_at_ms = None
        self._accumulated_pause_ms = 0

        self._apply_change(
            action="goto",
            segment_id=target_seg.id,
            segment_title=target_seg.title,
            by=by_actor,
            now_ms=now,
        )
        return None

    def next(
        self,
        *,
        by: str,
        now_ms: Optional[int] = None,
    ) -> Optional[RundownReject]:
        """顺序推进到下一环节。

        - 若当前为末段（``index == total - 1``）：finish——``index = total``，
          发 ``rundown.changed``（``segment_id=""``，``index == total``）。
        - 若已 done（``index >= total``）：结构化拒绝。
        - 其他：推进到 ``index + 1``，发事件。
        """
        if by not in _VALID_BY:
            raise ValueError(f"RundownState.next: 非法 by={by!r}，仅接受 {_VALID_BY}")
        by_actor = cast(RundownActor, by)
        if self.rundown is None:
            return RundownReject(reason="no_rundown_loaded")
        total = len(self.rundown.segments)

        now = self._resolve_now(now_ms)
        reject = self._min_duration_reject(by=by, now=now)
        if reject is not None:
            return reject

        if self.index >= total:
            return RundownReject(reason="already_done")
        if self.index == total - 1:
            # finish：游标推到 total（status 派生为 done），发完成事件
            self.index = total
            self.paused_at_ms = None
            self._apply_change(
                action="finish",
                segment_id="",
                segment_title="",
                by=by_actor,
                now_ms=now,
            )
            return None

        # 普通推进
        target_idx = self.index + 1
        target_seg = self.rundown.segments[target_idx]
        self.index = target_idx
        self.segment_started_at_ms = now
        self.paused_at_ms = None
        self._accumulated_pause_ms = 0
        self._apply_change(
            action="next",
            segment_id=target_seg.id,
            segment_title=target_seg.title,
            by=by_actor,
            now_ms=now,
        )
        return None

    def pause(self, *, by: str, now_ms: Optional[int] = None) -> Optional[RundownReject]:
        """暂停计时（仅在 running 时合法；paused / done / idle 均拒绝）。"""
        if by not in _VALID_BY:
            raise ValueError(f"RundownState.pause: 非法 by={by!r}，仅接受 {_VALID_BY}")
        if self.status != "running":
            return RundownReject(reason="not_running")
        self.paused_at_ms = self._resolve_now(now_ms)
        self._apply_change(
            action="pause",
            segment_id=self.current_segment_id,
            segment_title=self.current_segment.title if self.current_segment else "",
            by=cast(RundownActor, by),
            now_ms=self.paused_at_ms,
        )
        return None

    def resume(self, *, by: str, now_ms: Optional[int] = None) -> Optional[RundownReject]:
        """恢复计时（仅在 paused 时合法；把 ``now - paused_at_ms`` 计入累计暂停）。

        注：resume() 不重置 ``segment_started_at_ms``——它把暂停期间
        的时长折算到 ``_accumulated_pause_ms`` 里扣减，保持原锚点不变。
        """
        if by not in _VALID_BY:
            raise ValueError(f"RundownState.resume: 非法 by={by!r}，仅接受 {_VALID_BY}")
        if self.status != "paused" or self.paused_at_ms is None:
            return RundownReject(reason="not_paused")
        now = self._resolve_now(now_ms)
        self._accumulated_pause_ms += max(0, now - self.paused_at_ms)
        self.paused_at_ms = None
        self._apply_change(
            action="resume",
            segment_id=self.current_segment_id,
            segment_title=self.current_segment.title if self.current_segment else "",
            by=cast(RundownActor, by),
            now_ms=now,
        )
        return None

    # ------------------------------------------------------------------
    # 统一变更收口
    # ------------------------------------------------------------------

    def _apply_change(
        self,
        *,
        action: str,
        segment_id: str,
        segment_title: str,
        by: RundownActor,
        now_ms: int,
    ) -> None:
        """记录推进 + 构造 payload + 同步发射 + 通知 on_changed。

        事件发射与 on_changed 调用都做 fail-soft：注入缺失或抛错记日志，
        不阻断状态机本身已完成的变更。
        """
        # 函数体内 import 限 5 种情形之一——此处属"循环 import 规避"：
        # 顶层导入 events.payloads.rundown 会让本 Agent 内部契约模块顶层
        # 依赖 events 层，且 import 即触发 registry 注册副作用；仅在变更
        # 实际发生时按需导入，保持"无变更不发事件"的轻量化。
        from src.modules.events.names import CoreEvents  # noqa: PLC0415
        from src.modules.events.payloads.rundown import RundownChangedPayload  # noqa: PLC0415

        total = len(self.rundown.segments) if self.rundown is not None else 0

        self._transitions.append(
            {
                "action": action,
                "segment_id": segment_id,
                "by": by,
                "at_ms": now_ms,
            }
        )

        payload = RundownChangedPayload(
            rundown_id=self.rundown.rundown_id if self.rundown is not None else "",
            segment_id=segment_id,
            segment_title=segment_title,
            index=self.index,
            total=total,
            by=by,
            at_ms=now_ms,
        )

        if self._emit is not None:
            try:
                self._emit(CoreEvents.RUNDOWN_CHANGED, payload)
            except Exception as exc:
                self._logger.warning(f"rundown.changed 发射失败（状态已变更，本条事件丢弃）: {exc}")

        if self._on_changed is not None:
            try:
                self._on_changed(segment_id, by)
            except Exception as exc:
                self._logger.warning(f"rundown on_changed 回调失败（状态已变更）: {exc}")

    # ------------------------------------------------------------------
    # 只读派生
    # ------------------------------------------------------------------

    def get_progress_percent(self, *, now_ms: Optional[int] = None) -> Optional[float]:
        """整场进度百分比（wall clock 从 ``rundown_started_at_ms`` 算；夹取到 ``[0, 100]``）。"""
        if self.rundown is None or self.rundown_started_at_ms is None:
            return None
        total = sum(seg.expected_ms for seg in self.rundown.segments)
        if total <= 0:
            return None
        elapsed = max(0, self._resolve_now(now_ms) - self.rundown_started_at_ms)
        pct = elapsed / total * 100.0
        if pct < 0.0:
            return 0.0
        if pct > 100.0:
            return 100.0
        return pct

    def get_elapsed_live_ms(self, *, now_ms: Optional[int] = None) -> Optional[int]:
        """整场已播时长（Unix 毫秒；wall clock，与整场进度同口径不扣暂停）。

        未加载流程单返回 ``None``（装配层据此判断"未开播"）。
        """
        if self.rundown is None or self.rundown_started_at_ms is None:
            return None
        return max(0, self._resolve_now(now_ms) - self.rundown_started_at_ms)

    def get_current_remaining_ms(self, *, now_ms: Optional[int] = None) -> int:
        """当前环节剩余时长（毫秒；扣除累计暂停；超时或 done 返回 0）。"""
        seg = self.current_segment
        if seg is None:
            return 0
        elapsed = self._segment_elapsed_ms(self._resolve_now(now_ms))
        return max(0, seg.expected_ms - elapsed)

    def is_current_segment_overdue(self, *, now_ms: Optional[int] = None) -> bool:
        """当前环节停留是否已超预期（超时闹钟信号；仅 running 状态有意义）。"""
        if self.status != "running":
            return False
        seg = self.current_segment
        if seg is None:
            return False
        return self._segment_elapsed_ms(self._resolve_now(now_ms)) > seg.expected_ms

    def get_snapshot(self, *, now_ms: Optional[int] = None) -> Dict[str, Any]:
        """Dashboard / 情境注入使用的快照 dict。

        字段：status / rundown_id / title / current{id,title,expected_ms,elapsed_ms,remaining_ms}
        / index / total / progress_percent / paused。
        """
        seg = self.current_segment
        current_dict: Optional[Dict[str, Any]] = None
        if seg is not None:
            now = self._resolve_now(now_ms)
            elapsed = self._segment_elapsed_ms(now)
            remaining = max(0, seg.expected_ms - elapsed)
            current_dict = {
                "id": seg.id,
                "title": seg.title,
                "expected_ms": seg.expected_ms,
                "elapsed_ms": elapsed,
                "remaining_ms": remaining,
            }

        return {
            "status": self.status,
            "rundown_id": self.rundown.rundown_id if self.rundown is not None else "",
            "title": self.rundown.title if self.rundown is not None else "",
            "current": current_dict,
            "index": self.index,
            "total": len(self.rundown.segments) if self.rundown is not None else 0,
            "progress_percent": self.get_progress_percent(now_ms=now_ms),
            "paused": self.paused_at_ms is not None,
        }

    def get_transitions(self) -> List[Dict[str, Any]]:
        """推进历史（旧→新；最多 ``TRANSITIONS_MAXLEN`` 条）。"""
        return list(self._transitions)

    # ------------------------------------------------------------------
    # 情境注入（Planner / Replyer 提示词）
    # ------------------------------------------------------------------

    def build_context_text(self, *, now_ms: Optional[int] = None) -> Optional[str]:
        """渲染注入 Planner / Replyer 的流程单情境文本。

        内容：当前环节位置与计时、目标、要点、备注、后续环节列表（Agent
        据此用 ``rundown_control`` 自主切换）。流程单未激活或已无当前环节
        （done）返回 ``None``，调用方注入占位文本。
        """
        seg = self.current_segment
        if self.rundown is None or seg is None:
            return None
        now = self._resolve_now(now_ms)
        total = len(self.rundown.segments)
        elapsed = self._segment_elapsed_ms(now)
        remaining = max(0, seg.expected_ms - elapsed)

        lines: List[str] = []
        paused_mark = "（计时暂停中）" if self.paused_at_ms is not None else ""
        lines.append(
            f"[流程单] 环节 {self.index + 1}/{total}：{seg.title}"
            f"（已进行 {_format_duration_ms(elapsed)}，剩 {_format_duration_ms(remaining)}）{paused_mark}"
        )
        if seg.task_description:
            lines.append(f"目标：{seg.task_description}")
        if seg.key_points:
            lines.append(f"要点：{'、'.join(seg.key_points)}")
        if seg.notes:
            lines.append(f"备注：{seg.notes}")
        upcoming = self.rundown.segments[self.index + 1 :]
        if upcoming:
            names = " → ".join(f"{s.title}({s.id})" for s in upcoming)
            lines.append(f"后续环节：{names}（可用 rundown_control 的 goto/next 切换）")
        return "\n".join(lines)
