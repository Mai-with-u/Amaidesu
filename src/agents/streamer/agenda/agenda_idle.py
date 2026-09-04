"""AgendaIdle - 节目单后台调度循环（空转探测器/Agenda 调度）

职责边界（重要）
----------------
本模块**只**回答"按既定节奏推进节目单"——读取 :class:`AgendaState`
的当前环节和时间状态、按时间到期自动推进、按 AI 评估做延长/跳转/推进，
**不**实现状态机本身（``agenda_state.py``）、
**不**实现 LLM 扩展（``agenda_loader.py``）、
**不**订阅 EventBus、**不**直接 emit 任何事件。

设计变更（vs 原 outline_scheduler.py）：
- 类名 ``OutlineScheduler`` → ``AgendaIdle``
- 文件名 ``outline_scheduler.py`` → ``agenda_idle.py``
- 强调"空转探测器"角色（判据 ① 决策循环 idle + ② 无 pending 异步工具 +
  ③ 事件队列空 + ④ 有未完成 AgendaItem；全过 → emit planner.checkpoint 提醒）
- 保留后台调度 + AI 顺带评估消费逻辑（与原 outline_scheduler 同构）

设计要点（沿用 RoomStateLoop 范式）
----------------------------------------------
- **生命周期**：
  ``start()`` 创建后台 asyncio.Task → ``_loop()`` 周期调用 ``_tick()`` →
  ``stop()`` 取消任务；与 RoomStateLoop 同构（``asyncio.create_task``
  + ``_running`` 标志 + ``cancel`` + 异常隔离）。
- **可注入时钟**：``_tick(now_ms=...)`` 接受确定性时间戳，测试无需 ``time.sleep``。
  ``note_plan_assessment(...)`` 同样接受可选 ``now_ms`` 注入。
- **不直接 emit 事件**：通过 ``on_advance`` 回调通知 Agent，由 Agent 走
  正常决策链触发一次 proactive 发言。回调签名 ``Callable[[str, Optional[str]], None]``
  （参数：新环节 id + reason 字符串）。
- **尊重 pause 冻结语义**：底层 ``AgendaState.get_current_segment_elapsed_ms``
  在暂停期间天然冻结时间（已扣除暂停时长），本模块**不**做额外的暂停判断。
- **尊重 ``manually_overridden``**：手动操作后调度器跳过下一次自动推进。
- **AI 顺带评估消费**：Agent 调 :meth:`note_plan_assessment` 灌入 Planner 评估。
  调度器负责：
  - ``need_more_time=True`` 且未达硬超时 → 延长当前段一次（重新计时）；
  - ``may_advance=True`` 且已达 ``min_duration_ms``（若配置）→ 推进到下一段；
  - ``branch_id`` 命中 → 跳转到分支目标段；
  - 分支目标不存在 / ``branch_id`` 未在当前环节 → fallback 到
    ``agenda.fallback_segment_id``（无则顺序下一段）。
- **异步扩展不阻塞 tick**：当 ``state.needs_expansion(current_id)`` 为真时，
  调度器通过 ``asyncio.create_task`` 启动 ``AgendaLoader.expand_segment`` 后台任务，
  完成后调 ``state.cache_expanded()`` 缓存。多次触发由 ``_pending_expansions`` 集合
  去重，重复 tick 不会重复调度。

使用方契约
----------
- :class:`StreamerAgent`：
  - ``setup()`` 创建 ``AgendaLoader`` + ``AgendaState`` + ``AgendaIdle``，
    注入 ``on_advance`` 回调 → ``idle.start()``；
  - 每轮决策后调 ``idle.note_plan_assessment(...)`` 灌入 Planner 评估；
  - ``cleanup()`` 调 ``idle.stop()``（仿 RoomStateLoop.stop()）。
- Dashboard：只读 ``AgendaState.get_snapshot()``（不动 Scheduler）。
"""

from __future__ import annotations

import asyncio
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Optional,
    Set,
)

from src.modules.logging import get_logger
from src.modules.time_utils import now_ms as _real_now_ms

from .agenda_state import AgendaStatus


if TYPE_CHECKING:
    from .agenda_loader import AgendaLoader
    from .agenda_state import AgendaState


__all__ = ["AgendaIdle"]


_DEFAULT_TICK_INTERVAL_MS = 1_000

_REASON_TIME = "agenda:time"
_REASON_ASSESSMENT = "agenda:assessment"
_REASON_BRANCH = "agenda:branch"
_REASON_EXTEND = "agenda:extend"


def _cfg(config: Any, key: str, default: Any) -> Any:
    """从配置对象读取字段值（dict 用 .get，其他对象用 getattr 兜底）。"""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


class AgendaIdle:
    """节目单后台调度循环（空转探测器/Agenda 调度）。"""

    def __init__(
        self,
        config: Any,
        state: "AgendaState",
        loader: Optional["AgendaLoader"] = None,
        on_advance: Optional[Callable[[str, Optional[str]], None]] = None,
    ) -> None:
        self._state = state
        self._loader = loader
        self._on_advance = on_advance
        self._event_bus: Optional[Any] = None
        self._logger = get_logger("AgendaIdle")

        self._tick_interval_ms: int = _cfg(config, "agenda_scheduler_tick_ms", _DEFAULT_TICK_INTERVAL_MS)
        self._advance_eval_enabled: bool = _cfg(config, "agenda_advance_eval_enabled", True)

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._pending_expansions: Set[str] = set()

    def attach_event_bus(self, event_bus: Any) -> None:
        """注入 EventBus 用于 emit planner.checkpoint。"""
        self._event_bus = event_bus

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        self._logger.info(
            f"AgendaIdle 已启动 (tick={self._tick_interval_ms}ms, advance_eval={self._advance_eval_enabled})"
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._logger.info("AgendaIdle 已停止")

    async def _loop(self) -> None:
        interval = max(self._tick_interval_ms / 1000.0, 0.1)
        try:
            while self._running:
                await asyncio.sleep(interval)
                try:
                    await self._tick()
                except Exception as exc:
                    self._logger.error(f"AgendaIdle tick 异常: {exc}", exc_info=True)
        except asyncio.CancelledError:
            raise

    async def _tick(self, *, now_ms: Optional[int] = None) -> None:
        ts = now_ms if now_ms is not None else _real_now_ms()

        if self._state.status.value not in ("running", "completed"):
            return
        current_id = self._state.current_segment_id
        if current_id is None:
            return

        await self._maybe_expand(current_id)

        if self._is_idle():
            self._emit_checkpoint(ts)
            return

        elapsed = self._state.get_current_segment_elapsed_ms(now_ms=ts)
        seg = self._state._find_segment(current_id)
        if seg is None:
            return
        duration_ms = getattr(seg, "duration_ms", 0) or 0
        min_duration_ms = getattr(seg, "min_duration_ms", None)

        if self._state.manually_overridden:
            self._state.clear_manually_overridden()
            return

        if elapsed < duration_ms:
            return
        if min_duration_ms is not None and elapsed < min_duration_ms:
            return

        await self._advance(current_id, _REASON_TIME)

    async def _maybe_expand(self, segment_id: str) -> None:
        if self._loader is None:
            return
        if not self._state.needs_expansion(segment_id):
            return
        if segment_id in self._pending_expansions:
            return
        self._pending_expansions.add(segment_id)
        seg_obj = self._state._find_segment(segment_id)
        if seg_obj is None:
            self._pending_expansions.discard(segment_id)
            return
        asyncio.create_task(self._do_expand(segment_id, seg_obj))

    async def _do_expand(self, segment_id: str, seg_obj: Any) -> None:
        try:
            expanded = await self._loader.expand_segment(seg_obj)  # type: ignore[union-attr]
            self._state.cache_expanded(expanded)
            self._logger.debug(f"Agenda 环节 '{segment_id}' 扩展完成")
        except Exception as exc:
            self._logger.warning(f"Agenda 环节 '{segment_id}' 扩展失败: {exc}")
        finally:
            self._pending_expansions.discard(segment_id)

    async def _advance(self, current_id: str, reason: str) -> None:
        nxt_id = self._state._next_segment_id(current_id)
        if nxt_id is None:
            self._state.completed_segment_ids.append(current_id)
            self._state.current_segment_id = None
            self._state.status = AgendaStatus.COMPLETED
            self._state._append_transition(
                event="auto_advance_to_end",
                segment_id=None,
                reason=reason,
                now_ms=_real_now_ms(),
            )
            self._logger.info(f"Agenda 推进到末尾，已完成（reason={reason}）")
            return
        self._state.advance_to(nxt_id)
        self._logger.info(f"Agenda 自动推进：{current_id} → {nxt_id}（reason={reason}）")
        if self._on_advance is not None:
            try:
                self._on_advance(nxt_id, reason)
            except Exception as exc:
                self._logger.error(f"on_advance 回调异常: {exc}", exc_info=True)

    def _is_idle(self) -> bool:
        """判定当前是否处于"空转"状态。

        简化版（仅条件 ④）—— Planner 自身维护 idle 状态，
        调度器只检查"有未完成 AgendaItem"。
        """
        return self._state.status.value == "running" and self._state.current_segment_id is not None

    def _emit_checkpoint(self, now_ms: int) -> None:
        """空转条件全过 → emit planner.checkpoint 提醒。"""
        bus = self._event_bus
        if bus is None:
            return
        try:
            from src.modules.events.names import CoreEvents
            from src.modules.events.payloads.planner import (
                CheckpointAgendaPosition,
                CheckpointPayload,
            )

            active_id = self._state.current_segment_id or ""
            nxt_id = self._state._next_segment_id(self._state.current_segment_id)
            payload = CheckpointPayload(
                timestamp_ms=now_ms,
                agenda_item=CheckpointAgendaPosition(
                    active=active_id,
                    next=nxt_id or "",
                    expected_ms=self._state.get_current_segment_remaining_ms(now_ms=now_ms),
                ),
                timeline_summary="",
                duration_ms=self._state.get_elapsed_live_ms(now_ms=now_ms) or 0,
            )
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None and loop.is_running():
                loop.create_task(bus.emit(CoreEvents.PLANNER_CHECKPOINT, payload, source="AgendaIdle"))
        except (ImportError, AttributeError) as exc:
            self._logger.debug(f"planner.checkpoint payload 不可用，跳过 emit: {exc}")

    def note_plan_assessment(
        self,
        *,
        may_advance: bool,
        need_more_time: bool,
        branch_id: Optional[str] = None,
    ) -> None:
        """消费 Planner 顺带产出的 AI 评估字段（StreamerAgent 每轮决策后调用）。"""
        if not self._advance_eval_enabled:
            return
        current_id = self._state.current_segment_id
        if current_id is None:
            return

        if not (may_advance or need_more_time or branch_id):
            return

        seg = self._state._find_segment(current_id)
        if seg is None:
            return

        if need_more_time:
            duration_ms = getattr(seg, "duration_ms", 0) or 0
            extra = duration_ms // 2
            try:
                object.__setattr__(
                    seg,
                    "_extended_extra_ms",
                    getattr(seg, "_extended_extra_ms", 0) + extra,
                )
                self._logger.info(f"Agenda 延长 {extra}ms：{current_id} ({duration_ms}ms → {duration_ms + extra}ms)")
            except Exception:
                pass
            self._state._append_transition(
                event="assessment_extend",
                segment_id=current_id,
                reason=_REASON_EXTEND,
                now_ms=_real_now_ms(),
            )
            return

        if may_advance:
            elapsed = self._state.get_current_segment_elapsed_ms()
            min_duration = getattr(seg, "min_duration_ms", None)
            if min_duration is not None and elapsed < min_duration:
                self._logger.debug(f"Agenda may_advance 但未达 min_duration：elapsed={elapsed}, min={min_duration}")
                return
            nxt_id = self._state._next_segment_id(current_id)
            if nxt_id is not None:
                self._state.advance_to(nxt_id)
                self._logger.info(f"Agenda may_advance 推进：{current_id} → {nxt_id}")
                if self._on_advance is not None:
                    try:
                        self._on_advance(nxt_id, _REASON_ASSESSMENT)
                    except Exception as exc:
                        self._logger.error(f"on_advance 回调异常: {exc}", exc_info=True)
            return

        if branch_id is not None:
            target = self._resolve_branch_target(seg, branch_id)
            if target is not None:
                self._state.jump_to(target)
                self._logger.info(f"Agenda 分支跳转：{current_id} → {target}（branch_id={branch_id}）")
                if self._on_advance is not None:
                    try:
                        self._on_advance(target, _REASON_BRANCH)
                    except Exception as exc:
                        self._logger.error(f"on_advance 回调异常: {exc}", exc_info=True)

    def _resolve_branch_target(self, seg: Any, branch_id: str) -> Optional[str]:
        for branch in getattr(seg, "branches", []) or []:
            if getattr(branch, "branch_id", None) == branch_id:
                target = getattr(branch, "target_segment_id", None)
                if target and self._state._find_segment(target) is not None:
                    return target
        fallback = getattr(self._state.agenda, "fallback_segment_id", None)
        if fallback and self._state._find_segment(fallback) is not None:
            return fallback
        return self._state._next_segment_id(getattr(seg, "id", None))
