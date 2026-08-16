"""OutlineScheduler - 直播大纲后台调度循环（时间驱动 + AI 顺带评估消费）。

职责边界（重要）
----------------
本模块是 Task 9 产出，**只**回答"按既定节奏推进大纲"——读取 :class:`OutlineState`
的当前环节和时间状态、按时间到期自动推进、按 AI 评估做延长/跳转/推进，
**不**实现状态机本身（Task 6 ``outline_state.py``）、
**不**实现 LLM 扩展（Task 5 ``outline_loader.py``）、
**不**持久化、**不**订阅 EventBus、**不**直接 emit 任何事件。

设计要点（沿用 ``RoomStateLoop`` 范式）
------------------------------------
- **生命周期**：
  ``start()`` 创建后台 asyncio.Task → ``_loop()`` 周期调用 ``_tick()`` →
  ``stop()`` 取消任务；与 :class:`RoomStateLoop` 同构（``asyncio.create_task``
  + ``_running`` 标志 + ``cancel`` + 异常隔离）。
- **可注入时钟**：``_tick(now_ms=...)`` 接受确定性时间戳，测试无需 ``time.sleep``。
  ``note_plan_assessment(...)`` 同样接受可选 ``now_ms`` 注入。
- **不直接 emit 事件**：通过 ``on_advance`` 回调通知 Decider，由 Decider 走
  正常决策链触发一次 proactive 发言。回调签名 ``Callable[[str, Optional[str]], None]``
  （参数：新环节 id + reason 字符串）。
- **尊重 pause 冻结语义**：底层 ``OutlineState.get_current_segment_elapsed_ms``
  在暂停期间天然冻结时间（已扣除暂停时长），本模块**不**做额外的暂停判断。
- **尊重 ``manually_overridden``**：手动操作后调度器跳过下一次自动推进；下一次
  ``advance_to`` 自动推进成功后由调度器调用 ``state.clear_manually_overridden()``
  重置（实际状态机 ``advance_to`` 不设置该标志，跳过即可达成"手动后下一次不自动"）。
- **AI 顺带评估消费**：Task 10 Planner 顺带产出 ``may_advance`` / ``need_more_time`` /
  ``branch_id``，由 Decider 调 :meth:`note_plan_assessment` 灌入。调度器负责：
  - ``need_more_time=True`` 且未达硬超时 → 延长当前段一次（重新计时）；
  - ``may_advance=True`` 且已达 ``min_duration_ms``（若配置）→ 推进到下一段；
  - ``branch_id`` 命中 → 跳转到分支目标段；
  - 分支目标不存在 / ``branch_id`` 未在当前环节 → fallback 到
    ``outline.fallback_segment_id``（无则顺序下一段）。
- **异步扩展不阻塞 tick**：当 ``state.needs_expansion(current_id)`` 为真时，
  调度器通过 ``asyncio.create_task`` 启动 ``OutlineLoader.expand_segment`` 后台任务，
  完成后调 ``state.cache_expanded()`` 缓存。多次触发由 ``_pending_expansions`` 集合
  去重，重复 tick 不会重复调度。

使用方契约
----------
- :class:`AmaidesuDecider`：
  - ``setup()`` 创建 ``OutlineLoader`` + ``OutlineState`` + ``OutlineScheduler``，
    注入 ``on_advance`` 回调 → ``scheduler.start()``；
  - ``_make_two_stage_decision()`` 后调 ``scheduler.note_plan_assessment(...)`` 灌入 Planner 评估；
  - ``cleanup()`` 调 ``scheduler.stop()``（仿 RoomStateLoop.stop()）。
- Dashboard：只读 ``OutlineState.get_snapshot()``（不动 Scheduler）。

不在此处
--------
- 不实现状态机（Task 6）
- 不调用 LLM（Task 5）
- 不持久化
- 不订阅 EventBus / 不 emit 事件
- 不实现 Dashboard HTTP API（Task 10/11）
"""

from __future__ import annotations

import asyncio
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Optional,
    Set,
)

from src.modules.logging import get_logger
from src.modules.time_utils import now_ms as _real_now_ms


if TYPE_CHECKING:
    # ``OutlineState`` 由 T6 ``outline_state.py`` 定义；
    # ``OutlineLoader`` 由 T5 定义；
    # 本模块在运行时通过构造器注入这些对象的实例，**不**主动 import 它们。
    # 该 TYPE_CHECKING 块仅用于类型注解（``from __future__ import annotations``
    # 已开启，运行时注解都是字符串），避免 ``deciders.__init__`` →
    # ``decision_schemas`` → ``amaidesu_decider`` 死锁循环导入。
    from .outline_loader import OutlineLoader
    from .outline_state import OutlineState


__all__ = ["OutlineScheduler"]


# ---------------------------------------------------------------------------
# 默认值与原因常量
# ---------------------------------------------------------------------------

_DEFAULT_TICK_INTERVAL_MS = 1_000
# 回调 reason 字符串（与 ``RoomStateLoop`` 的日志 reason 风格保持一致）：
# 全部以 ``outline:`` 前缀开头，方便日志聚合/过滤。Task 9 QA 验收脚本
# ``.omo/evidence/task-9-*.txt`` 断言 ``reason`` 含 ``outline``。
_REASON_TIME = "outline:time"  # 时间到期自动推进
_REASON_ASSESSMENT = "outline:assessment"  # AI 评估 may_advance 推进
_REASON_BRANCH = "outline:branch"  # 分支跳转
_REASON_EXTEND = "outline:extend"  # AI 评估 need_more_time 延长


def _cfg(config: Any, key: str, default: Any) -> Any:
    """从配置对象 / dict 读取字段,缺失回退到默认值(仿 RoomStateLoop._cfg)。"""
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


# ---------------------------------------------------------------------------
# OutlineScheduler
# ---------------------------------------------------------------------------


class OutlineScheduler:
    """直播大纲后台调度循环。

    生命周期：
        ``start()`` 创建 ``asyncio.create_task(self._loop())`` → ``_loop()``
        每 ``outline_scheduler_tick_ms`` 调用 ``_tick()`` → ``stop()`` 取消任务。
        与 :class:`RoomStateLoop` 同构。

    Attributes:
        tick_interval_ms: 后台 tick 间隔(毫秒,默认 1000ms)
        on_advance: 推进回调 ``Callable[[new_segment_id, reason], None]``;
                    调度器完成"时间到期/AI 评估/分支跳转"任一推进后触发
    """

    def __init__(
        self,
        config: Any,
        state: "OutlineState",
        loader: Optional["OutlineLoader"] = None,
        *,
        on_advance: Optional[Callable[[str, Optional[str]], None]] = None,
        clock: Optional[Callable[[], int]] = None,
    ) -> None:
        """初始化 OutlineScheduler。

        Args:
            config: 配置字典或对象;读取字段 ``outline_scheduler_tick_ms``
                    (默认 1000ms)。容忍 dict / 已解析对象 / None。
            state: 已构造好的 :class:`OutlineState` 实例(由 Decider 创建并 ``start()`` 大纲)。
            loader: 可选 :class:`OutlineLoader`(用于 ``needs_expansion`` 时触发异步扩展);
                    ``None`` 时跳过扩展触发(调度循环其余逻辑不受影响)。
            on_advance: 推进回调 ``Callable[[new_segment_id: str, reason: Optional[str]], None]``;
                    Decider 用此触发一次 proactive 发言。``None`` 时静默 no-op。
            clock: 可选时钟回调(无参 → int 毫秒);用于测试中注入确定性时间。
                    优先级:方法参数 ``now_ms`` > ``clock()`` > 真实时钟。
                    默认 ``None`` 退到 :func:`src.modules.time_utils.now_ms`。
        """
        self._state = state
        self._loader = loader
        self._on_advance = on_advance
        self._clock = clock

        self._tick_interval_ms: int = _cfg(config, "outline_scheduler_tick_ms", _DEFAULT_TICK_INTERVAL_MS)

        self.logger = get_logger("OutlineScheduler")

        self._task: Optional[asyncio.Task] = None
        self._running = False

        # 已触发但未完成的扩展任务(segment_id → asyncio.Task),用于去重
        self._pending_expansions: Set[str] = set()
        # 已延长过的环节 id(segment_id → True);同一环节 ``need_more_time`` 只生效一次
        self._extended: Dict[str, bool] = {}

    # ------------------------------------------------------------------
    # 时钟注入
    # ------------------------------------------------------------------

    def _resolve_now(self, now_ms: Optional[int]) -> int:
        """解析可选的 ``now_ms``:参数 > 注入回调 > 真实时钟。"""
        if now_ms is not None:
            return now_ms
        if self._clock is not None:
            return self._clock()
        return _real_now_ms()

    # ------------------------------------------------------------------
    # 生命周期:start / stop(照抄 RoomStateLoop)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动后台调度循环(创建 asyncio.Task)。

        已运行/已禁用场景下幂等 no-op。
        """
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        self.logger.info(
            f"OutlineScheduler 已启动 (tick={self._tick_interval_ms}ms, "
            f"loader={'注入' if self._loader else '未注入'}, "
            f"on_advance={'注入' if self._on_advance else '未注入'})"
        )

    async def stop(self) -> None:
        """停止后台调度循环(cancel 任务 + 等待清理)。

        幂等:重复调用安全。
        """
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._pending_expansions.clear()
        self.logger.info("OutlineScheduler 已停止")

    async def _loop(self) -> None:
        """后台循环主体(照抄 RoomStateLoop._loop)。"""
        interval = max(self._tick_interval_ms / 1000.0, 0.1)
        try:
            while self._running:
                await asyncio.sleep(interval)
                try:
                    await self._tick()
                except Exception as e:
                    self.logger.error(f"OutlineScheduler tick 异常: {e}", exc_info=True)
        except asyncio.CancelledError:
            raise

    # ------------------------------------------------------------------
    # tick 主逻辑(注入时钟)
    # ------------------------------------------------------------------

    async def _tick(self, *, now_ms: Optional[int] = None) -> None:
        """单次 tick 逻辑(对应任务规格的 4 步)。

        1. 状态非 ``RUNNING`` → 跳过。
        2. 当前环节已到 ``duration_ms``(扣除暂停时长)→ 时间到期自动推进。
        3. 已达 ``min_duration_ms``(若配置)→ 标记"可接受推进"(等待 Planner 评估)。
        4. 当前环节 ``needs_expansion=True`` → 触发异步扩展(不阻塞 tick)。

        Args:
            now_ms: 注入时间戳(Unix 毫秒);``None`` 时使用注入回调/真实时钟。
        """
        ts = self._resolve_now(now_ms)

        # 1. 状态非 RUNNING → 跳过(包括 INACTIVE / LOADING / COMPLETED / UNLOADED)
        if self._state.status != self._state_status_running():
            return

        current_id = self._state.current_segment_id
        if current_id is None:
            # COMPLETED 末段 / 异常态 → 无 segment 可推进
            return

        # 2. 时间到期自动推进(手动覆盖时跳过)
        if not self._state.manually_overridden:
            elapsed = self._state.get_current_segment_elapsed_ms(now_ms=ts)
            seg = self._find_current_segment(current_id)
            if seg is not None and elapsed >= seg.duration_ms:
                next_id = self._next_segment_id(current_id)
                if next_id is not None:
                    self._state.advance_to(next_id, reason=_REASON_TIME, now_ms=ts)
                    # 清掉旧 segment 的延长标记(进入新环节后允许再次延长)
                    self._extended.pop(current_id, None)
                    self._fire_on_advance(next_id, _REASON_TIME, ts)
                    # 推进后,新环节从 elapsed=0 开始,本次 tick 不再触发扩展/标记
                    return
                # 已是末段:不主动设 COMPLETED(由 segment 自然结束或外部处理),
                # 但 _tick 不再做任何时间推进
                return

        # 3. 标记"可接受推进"(已达 min_duration)
        # 当前实现不存储该标记——note_plan_assessment 自带 elapsed >= min_duration 检查。
        # 这里保留空操作,保持与任务规格的步骤对应,便于未来扩展(例如推送 hint 到 Planner)。

        # 4. needs_expansion → 异步触发扩展
        if self._loader is not None and self._state.needs_expansion(current_id):
            seg = self._find_current_segment(current_id)
            if seg is not None:
                self._trigger_expansion(current_id, seg)

    # ------------------------------------------------------------------
    # AI 顺带评估消费:note_plan_assessment
    # ------------------------------------------------------------------

    def note_plan_assessment(
        self,
        *,
        may_advance: bool = False,
        need_more_time: bool = False,
        branch_id: Optional[str] = None,
        now_ms: Optional[int] = None,
    ) -> None:
        """消费 Planner 顺带产出的 AI 评估(Task 10 接线入口)。

        优先级(互斥):
            1. ``branch_id`` 非空 → 解析分支目标;命中跳转 / 未命中 fallback;
            2. ``need_more_time=True`` 且未达硬超时 → 延长当前段一次;
            3. ``may_advance=True`` 且已达 ``min_duration_ms``(若配置)→ 推进到下一段。

        行为:
            - 分支跳转 → ``state.jump_to(target)`` + ``on_advance(target, "outline:branch")``;
            - 延长 → 仅重置 ``segment_started_at_ms = now``(一次),不调回调;
            - 推进 → ``state.advance_to(next)`` + ``on_advance(next, "outline:assessment")``。
            - 任何情况下都不会因为"AI 评估字段全 False"而做主动动作(尊重沉默)。

        Args:
            may_advance: Planner 评估当前环节可提前结束(必须已过 min_duration)。
            need_more_time: Planner 评估当前环节需要更多时间(已到 duration 时延长一次)。
            branch_id: Planner 选择的分支 id;命中跳转 / 未命中 fallback。
            now_ms: 注入时间戳(Unix 毫秒);``None`` 时使用注入回调/真实时钟。
        """
        ts = self._resolve_now(now_ms)

        if self._state.status != self._state_status_running():
            return
        current_id = self._state.current_segment_id
        if current_id is None:
            return

        # 1. 分支跳转(优先级最高)
        if branch_id is not None and branch_id != "":
            target = self._resolve_branch_target(current_id, branch_id)
            if target is not None:
                self._do_jump(target, current_id=current_id, reason=_REASON_BRANCH, now_ms=ts)
                return
            # 分支目标不存在 / branch_id 未在当前环节 → fallback(已由 _resolve_branch_target 处理)
            target = self._fallback_target(current_id)
            if target is not None:
                self._do_jump(target, current_id=current_id, reason=_REASON_BRANCH, now_ms=ts)
                return
            # fallback 也无(不应发生,防御性兜底)
            self.logger.warning(f"branch_id={branch_id!r} 未命中且 fallback 不可用,保持当前环节 {current_id!r}")
            return

        # 2. need_more_time 延长(已到 duration 才生效,且每段只延长一次)
        if need_more_time:
            if self._extended.get(current_id, False):
                # 本段已延长过一次,忽略(防止 AI 反复延长死循环)
                return
            elapsed = self._state.get_current_segment_elapsed_ms(now_ms=ts)
            seg = self._find_current_segment(current_id)
            if seg is None:
                return
            if elapsed < seg.duration_ms:
                # 尚未到 duration,无需延长(AI 提前喊"再延长"也尊重沉默,不重置计时)
                return
            # 重新计时:segment_started_at_ms = now,等价于再给一段 duration_ms 时间
            self._state.segment_started_at_ms = ts
            self._state.paused_elapsed_ms = 0  # 重置暂停累计,避免延长期间意外冻结
            self._extended[current_id] = True
            self.logger.info(
                f"AI 评估 need_more_time,延长环节 {current_id!r} (elapsed={elapsed}ms, duration={seg.duration_ms}ms)"
            )
            return

        # 3. may_advance 推进(必须已过 min_duration)
        if may_advance:
            seg = self._find_current_segment(current_id)
            if seg is None:
                return
            min_dur = getattr(seg, "min_duration_ms", None)
            elapsed = self._state.get_current_segment_elapsed_ms(now_ms=ts)
            if min_dur is not None and elapsed < min_dur:
                # 未达 min_duration,拒绝推进(防御 AI 过早推进)
                return
            next_id = self._next_segment_id(current_id)
            if next_id is None:
                # 末段:不主动跳(尊重自然完成)
                return
            self._state.advance_to(next_id, reason=_REASON_ASSESSMENT, now_ms=ts)
            self._extended.pop(current_id, None)
            self._fire_on_advance(next_id, _REASON_ASSESSMENT, ts)
            return

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _do_jump(
        self,
        target_id: str,
        *,
        current_id: str,
        reason: str,
        now_ms: int,
    ) -> None:
        """执行跳转(``jump_to`` + 清理延长标记 + 触发回调)。

        ``jump_to`` 内部会校验 target 存在于 ``outline.segments``,失败抛 ``ValueError``;
        这里用 try/except 隔离(理论上 _resolve_branch_target 已校验过,但兜底防御)。
        """
        try:
            self._state.jump_to(target_id, reason=reason, now_ms=now_ms)
        except ValueError as e:
            self.logger.warning(f"jump_to({target_id!r}) 失败: {e}")
            return
        # 清掉旧 segment 的延长标记 + 旧段的扩展缓存已在 jump_to 内部处理
        self._extended.pop(current_id, None)
        self._extended.pop(target_id, None)
        self._fire_on_advance(target_id, reason, now_ms)

    def _resolve_branch_target(self, current_id: str, branch_id: str) -> Optional[str]:
        """解析 ``branch_id`` 对应的 target_segment_id。

        查找逻辑:
            - 遍历当前环节的 ``branches``,找到匹配的取 ``target_segment_id``;
            - 校验 target 存在于 ``outline.segments``,否则视为"未命中"。

        Returns:
            target_segment_id(有效时)或 ``None``(未命中 / 无效)。
        """
        seg = self._find_current_segment(current_id)
        if seg is None:
            return None
        target: Optional[str] = None
        for branch in getattr(seg, "branches", []) or []:
            if getattr(branch, "branch_id", None) == branch_id:
                target = getattr(branch, "target_segment_id", None)
                break
        if target is None:
            return None
        # 校验 target 在大纲中
        seg_ids = self._segment_ids()
        if target in seg_ids:
            return target
        return None

    def _fallback_target(self, current_id: str) -> Optional[str]:
        """分支未命中时的 fallback 目标。

        优先级:
            1. ``outline.fallback_segment_id``(若存在且在 segments 中);
            2. 当前环节的顺序下一段(``_next_segment_id``)。

        Returns:
            fallback 目标 segment_id;均无则 ``None``。
        """
        outline = getattr(self._state, "outline", None)
        if outline is not None:
            fb = getattr(outline, "fallback_segment_id", None)
            if fb and fb in self._segment_ids():
                return fb
        return self._next_segment_id(current_id)

    def _segment_ids(self) -> Set[str]:
        """返回当前大纲所有 segment_id 集合(用于校验 branch target)。"""
        outline = getattr(self._state, "outline", None)
        if outline is None:
            return set()
        segs = getattr(outline, "segments", None) or []
        return {seg.id for seg in segs}

    def _find_current_segment(self, segment_id: str) -> Optional[Any]:
        """按 id 查找当前大纲环节(duck-typed)。"""
        outline = getattr(self._state, "outline", None)
        if outline is None:
            return None
        for seg in getattr(outline, "segments", None) or []:
            if getattr(seg, "id", None) == segment_id:
                return seg
        return None

    def _next_segment_id(self, current_id: str) -> Optional[str]:
        """返回 ``current_id`` 在大纲中的下一环节 id;末尾或找不到返回 ``None``。"""
        outline = getattr(self._state, "outline", None)
        if outline is None:
            return None
        segs = getattr(outline, "segments", None) or []
        ids = [getattr(seg, "id", None) for seg in segs]
        try:
            idx = ids.index(current_id)
        except ValueError:
            return None
        if idx + 1 >= len(ids):
            return None
        return ids[idx + 1]

    def _fire_on_advance(self, new_segment_id: str, reason: Optional[str], now_ms: int) -> None:
        """触发 ``on_advance`` 回调;未注入时静默。"""
        if self._on_advance is None:
            return
        try:
            self._on_advance(new_segment_id, reason)
        except Exception as e:
            # 回调异常不中断 tick(避免 Decider bug 影响大纲推进)
            self.logger.error(f"on_advance 回调异常: {e}", exc_info=True)

    def _trigger_expansion(self, segment_id: str, segment: Any) -> None:
        """异步触发扩展(不阻塞 tick)。

        ``_pending_expansions`` 去重:同一 segment_id 已触发则不再重复调度。
        任务对象**不**强引用保存(避免 asyncio.Task 与 done callback 形成循环),
        依赖事件循环关闭时的级联取消清理。
        """
        if segment_id in self._pending_expansions:
            return
        loader = self._loader
        if loader is None:
            return

        self._pending_expansions.add(segment_id)

        async def _do_expand() -> None:
            try:
                expanded = await loader.expand_segment(segment)
            except Exception as e:
                self.logger.error(f"扩展 segment_id={segment_id!r} 异常: {e}", exc_info=True)
                return
            try:
                self._state.cache_expanded(expanded)
            except Exception as e:
                self.logger.error(f"缓存扩展内容 segment_id={segment_id!r} 异常: {e}", exc_info=True)

        def _on_done(task: asyncio.Task) -> None:
            self._pending_expansions.discard(segment_id)
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                self.logger.error(
                    f"扩展任务 segment_id={segment_id!r} 未捕获异常: {exc}",
                    exc_info=True,
                )

        task = asyncio.create_task(_do_expand())
        task.add_done_callback(_on_done)

    # ------------------------------------------------------------------
    # 类型辅助(避免硬 import OutlineStatus)
    # ------------------------------------------------------------------

    def _state_status_running(self) -> Any:
        """返回 ``OutlineStatus.RUNNING``(鸭子类型,不直接 import)。"""
        status_class = type(self._state.status)
        for member in status_class:
            if getattr(member, "value", None) == "running":
                return member
        return self._state.status
