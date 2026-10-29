"""rundown_coordinator - 流程单运行时协调器（RundownState 与持久化设施的粘合层）

``RundownState`` 被刻意设计为纯内存状态机（依赖全回调、无 IO，保可测性）；
它与仓储、EventBus、场次解析的粘合由本类承载（具名单元，不内联在
``StreamerAgent`` 里），让 Agent 专注决策循环主体，
流程单域的演进（装载、崩溃恢复、变更持久化、热切换）在单文件内完成。

依赖全部构造器注入（不持有宿主引用，与 ``Planner`` / ``RundownState``
同款形态）：
- 三个仓储与场次管理器可选注入，``None`` 时对应持久化能力整体降级
- ``event_bus`` 可选，``None`` 时只持久化不发事件
- ``notify_proactive``：环节变更时通知宿主置位主动发言信号（宿主消费后
  自行清零；Coordinator 不关心宿主如何判定与消费）
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional, Set, TYPE_CHECKING

from src.modules.events.event_bus import EventBus
from src.modules.logging import ModuleLogger
from src.modules.storage.models.rundown import (
    DEFAULT_RUNDOWN,
    Rundown,
    RundownInstance,
    RundownInstanceSegment,
    RundownSegment,
    RundownTransition,
)
from src.modules.utils.task_utils import spawn_background_task
from src.modules.utils.time_utils import now_ms

from .presentation import apply_rundown_control, build_rundown_view
from .rundown_state import RundownReject, RundownState
from ..tools.rundown_tool import RundownControlProvider

if TYPE_CHECKING:
    from ..config import StreamerConfig

__all__ = ["RundownCoordinator"]


class RundownCoordinator:
    """流程单运行时协调器：状态机 + 3 仓储 + 总线 + 场次解析的稳定依赖组合。

    职责（全部原属 ``StreamerAgent`` 的流程单接线族）：
    - 装载与启动：配置 ``rundown_id`` 读取、崩溃恢复（实例快照还原进度）
    - 变更旁路：``rundown.changed`` 事件发射 + transitions 落行 + 实例快照写穿；
      宿主停止时等在途的广播与落库做完，此后不再落库
    - 情境供给：环节超时 / 供能判定、Planner/Replyer 的 ``$rundown`` 文本
    - 对外控制：Dashboard 的视图读取、手动控制、热切换、定义应用

    宿主消费的三个入口：宿主动词（``begin`` / ``current_segment_title`` /
    ``get_elapsed_live_ms`` 等领域方法，状态机 ``RundownState`` 是私有实现
    细节不外露）、``tool_provider``（注册进 ToolRegistry）、``build_text`` /
    ``is_overdue`` / ``is_active``（决策执行器 provider 闭包）。
    """

    def __init__(
        self,
        *,
        config: "StreamerConfig",
        rundown_repo: Optional[Any],
        rundown_instance_repo: Optional[Any],
        rundown_transition_repo: Optional[Any],
        session_manager: Optional[Any],
        event_bus: Optional[EventBus],
        logger: ModuleLogger,
        notify_proactive: Callable[[], None],
    ) -> None:
        """构造协调器并完成状态机与控制工具的装配。

        Args:
            config: ``StreamerConfig`` 实例（读取 ``rundown_id``）
            rundown_repo: 可选 ``RundownRepo``（流程单库；``None`` 时只加载内置默认）
            rundown_instance_repo: 可选 ``RundownInstanceRepo``；``None`` 时崩溃
                恢复与实例写穿整体跳过
            rundown_transition_repo: 可选 ``RundownTransitionRepo``；``None`` 时
                变更不落行
            session_manager: 可选 ``LiveSessionManager``（场次归属解析；``None``
                时持久化无锚点、整体跳过）
            event_bus: 可选 ``EventBus``（``rundown.changed`` 发射通道）
            logger: 宿主持有的模块 logger（共享同一过滤名）
            notify_proactive: 环节变更通知回调（宿主置位主动发言信号）
        """
        self._config = config
        self._rundowns = rundown_repo
        self._instance_repo = rundown_instance_repo
        self._transition_repo = rundown_transition_repo
        self._session_manager = session_manager
        self._event_bus = event_bus
        self._logger = logger
        # 变更广播与落库的后台任务（强引用防 GC 中途回收）：每次环节变更派一个，
        # 都是单次发事件或写库，停止时等它们做完，最后一次变更才进得了库
        self._bg_tasks: Set[asyncio.Task[Any]] = set()
        # 停止后为 True：宿主停了还晚到的变更（停机途中的开播事件、Dashboard 控制）
        # 不再广播和落库，免得在关库前后抢写；start 时复位
        self._stopped = False
        self._notify_proactive = notify_proactive
        self._state = RundownState(
            emit=self._emit_rundown_changed,
            on_changed=self._on_rundown_changed,
        )
        self.tool_provider = RundownControlProvider(self._state)

    # ==================================================================
    # 宿主动词（推进权归宿主；状态机细节不外露）
    # ==================================================================

    def begin(self, *, by: str) -> Optional["RundownReject"]:
        """流程单开播起算（armed → running，双锚点 = 开播时刻）。

        已起算的场（崩溃恢复的进行中/已完成）被状态机结构化拒绝，宿主据
        返回值保持现态静默忽略。``by`` 白名单语义由 ``RundownState.begin``
        单点维护，本方法只转发。
        """
        return self._state.begin(by=by)

    def current_segment_title(self) -> str:
        """当前环节标题（嘴的现场卡用）；没有流程单为空串。"""
        segment = self._state.current_segment
        return segment.title if segment is not None else ""

    def get_elapsed_live_ms(self, *, now_ms: Optional[int] = None) -> Optional[int]:
        """整场已播时长（未加载/未起算为 ``None``；口径见 ``RundownState``）。"""
        return self._state.get_elapsed_live_ms(now_ms=now_ms)

    # ==================================================================
    # 情境供给（决策执行器 / reply_tool 的 provider 闭包）
    # ==================================================================

    def is_overdue(self) -> bool:
        """当前环节停留是否已超预期（供 ProactiveTrigger rundown_overdue 闹钟）。"""
        return self._state.is_current_segment_overdue()

    def is_active(self) -> bool:
        """判定流程单是否在供能（供 ProactiveTrigger rundown_ready 持续产出信号）。

        done 仍供能：流程单内容排完不是停止内容推进的理由——Agent 按既定节奏
        获得开口机会，凭情境（完成文案 + 弹幕）自由互动。paused 不供能（导演
        明确叫停）。供能不等于发言：is_novel 自检与台词重合度闸把关质量。
        """
        return self._state.status in ("running", "done")

    def build_text(self) -> Optional[str]:
        """拼装流程单情境文本（注入 Planner/Replyer $rundown 变量）。"""
        return self._state.build_context_text()

    def build_text_sync(self) -> Optional[str]:
        """同步包装（reply_tool 的 rundown_text_provider 鸭子接口）。"""
        return self.build_text()

    # ==================================================================
    # 装载与启动
    # ==================================================================

    async def start(self) -> None:
        """加载流程单并启动运行时（唯一装配点；fail-soft）。

        加载顺序：配置 ``rundown_id`` → 存储读取；未配置 / 不存在 / 读取失败
        → 回退内置默认流程单（初次直播·自我介绍）。
        """
        # 宿主停了又重新启动：变更恢复广播与落库
        self._stopped = False
        rundown_id = (self._config.rundown_id or "").strip()
        rundown: Optional[Rundown] = None
        if rundown_id and self._rundowns is not None:
            try:
                rundown = await self._rundowns.get_rundown(rundown_id)
            except Exception as exc:
                self._logger.warning(f"读取流程单 '{rundown_id}' 失败: {exc}")
                rundown = None
            if rundown is None:
                self._logger.warning(f"流程单 '{rundown_id}' 不存在，回退内置默认流程单")
        if rundown is None:
            rundown = DEFAULT_RUNDOWN
        if await self._try_restore(rundown):
            return
        self._state.load(rundown)
        self._logger.info(f"流程单已加载: id={rundown.rundown_id!r}, segments={len(rundown.segments)}")

    async def stop(self) -> None:
        """宿主停止时收尾：先关闭变更旁路，再等已派出的广播与落库全部做完。

        先关后等：关闭之后新到的变更不再派任务，等待集合清空就一定能结束；
        已派出的写库是短写入，等它做完，宿主停止前的最后一次环节变更（含停止
        途中嘴、发言管线、决策轮收尾时引起的变更）才落得了库，崩溃恢复读到的
        进度才是最新的。
        """
        self._stopped = True
        while self._bg_tasks:
            # 任务完成先经回调移出集合，gather 返回时已完成的任务必已出集合
            await asyncio.gather(*list(self._bg_tasks), return_exceptions=True)

    async def _try_restore(self, template: Rundown) -> bool:
        """崩溃恢复：有活跃场次且实例快照与本单模板匹配时还原进度。

        - 无实例仓储 / 无活跃场次 / 无快照 → ``False``（走常规 load）。
        - ``template_id`` 不匹配（导演开播前换了单）或快照无环节行 →
          ``False`` + info 日志，绝不拿别的单的进度套本单。
        - 快照损坏（越界游标等）→ ``RundownState.restore`` 拒绝，回退常规
          load + warning。
        - 成功 → 用快照环节行（含 Agent 追加环节，防模板漂移）构造清单并
          ``restore``，info 日志含恢复到的环节位置。
        """
        if self._instance_repo is None or self._session_manager is None:
            return False
        try:
            pk = await self._session_manager.resolve_pk()
        except Exception as exc:
            self._logger.warning(f"崩溃恢复跳过：场次主键解析失败: {exc}", exc=True)
            return False
        if pk is None:
            return False
        try:
            snapshot = await self._instance_repo.get_by_session(pk)
        except Exception as exc:
            self._logger.warning(f"崩溃恢复跳过：实例快照读取失败（回退常规加载）: {exc}", exc=True)
            return False
        if snapshot is None:
            return False
        if snapshot.template_id != template.rundown_id or not snapshot.segments:
            self._logger.info(f"实例快照与流程单不匹配（template={snapshot.template_id!r}），回退常规加载")
            return False
        rundown = template.model_copy(
            update={
                "segments": [
                    RundownSegment(
                        id=s.seg_id,
                        title=s.title,
                        task_description=s.task_description,
                        expected_ms=s.expected_ms,
                        min_duration_ms=s.min_duration_ms,
                        notes=s.notes,
                    )
                    for s in snapshot.segments
                ]
            }
        )
        reject = self._state.restore(rundown, snapshot)
        if reject is not None:
            self._logger.warning(f"实例快照损坏（{reject.reason}），回退常规加载")
            return False
        self._logger.info(
            f"流程单已从实例快照恢复: id={template.rundown_id!r}, "
            f"恢复到第 {snapshot.seg_index + 1}/{len(snapshot.segments)} 环节"
        )
        return True

    # ==================================================================
    # 变更旁路（事件发射 + 持久化）
    # ==================================================================

    def _on_rundown_changed(self, segment_id: str, by: str) -> None:
        """流程单变更通知：经回调置位宿主的 proactive 即时信号（新环节开场要马上说）。"""
        self._notify_proactive()
        self._logger.info(f"流程单变更: segment={segment_id!r}, by={by!r}")

    def _emit_rundown_changed(self, event_name: str, payload: Any) -> None:
        """桥接 RundownState 的事件回调：EventBus 异步 emit + 场次持久化（落行/写穿）。

        协调器已停止时两样都不做：宿主停了，此时数据库与总线随时可能关闭。
        """
        if self._stopped:
            self._logger.warning(
                f"流程单协调器已停止，变更不再广播与落库: action={payload.action!r}, segment={payload.segment_id!r}"
            )
            return
        if self._event_bus is not None:
            spawn_background_task(
                self._event_bus.emit(event_name, payload, source="RundownState"),
                logger=self._logger,
                tasks=self._bg_tasks,
                label="RundownCoordinator.changed_emit",
            )
        self._persist_rundown_change(payload)

    def _persist_rundown_change(self, payload: Any) -> None:
        """流程单变更的持久化旁路：transitions 落行 + 实例快照写穿（fire-and-forget）。

        两仓储任一未注入则整体跳过（测试与降级场景）；无活跃场次
        （``resolve_pk()`` 为 None）不落任何行。落行与写穿各自独立
        try/except——失败只 warning，不阻断状态机与事件链路，也不互相阻断。
        """
        if self._instance_repo is None and self._transition_repo is None:
            return

        async def _run() -> None:
            try:
                pk = await self._session_manager.resolve_pk() if self._session_manager is not None else None
            except Exception as exc:
                self._logger.warning(f"流程单持久化跳过：场次主键解析失败: {exc}", exc=True)
                return
            if pk is None:
                return

            # 变更事实行（append-only）
            if self._transition_repo is not None:
                try:
                    current = self._state.current_segment
                    task_desc: Optional[str] = None
                    if payload.action in ("append", "insert") and self._state.rundown is not None:
                        appended = next(
                            (s for s in self._state.rundown.segments if s.id == payload.segment_id),
                            None,
                        )
                        task_desc = appended.task_description if appended is not None else None
                    await self._transition_repo.insert(
                        RundownTransition(
                            live_session_id=pk,
                            rundown_id=payload.rundown_id,
                            action=payload.action,
                            seg_id=payload.segment_id,
                            seg_title=payload.segment_title,
                            seg_index=payload.index,
                            by=payload.by,
                            at_ms=payload.at_ms,
                            expected_ms=current.expected_ms if current is not None else None,
                            task_description=task_desc,
                            updated_at_ms=now_ms(),
                        )
                    )
                except Exception as exc:
                    self._logger.warning(f"rundown_transitions 落行失败（不阻断链路）: {exc}", exc=True)

            # 实例快照写穿（头 + 环节行全量替换）
            if self._instance_repo is not None:
                try:
                    progress = self._state.export_progress()
                    state_rundown = self._state.rundown
                    if progress and state_rundown is not None:
                        await self._instance_repo.upsert(
                            RundownInstance(
                                live_session_id=pk,
                                updated_at_ms=now_ms(),
                                segments=[
                                    RundownInstanceSegment(
                                        seq=i,
                                        seg_id=seg.id,
                                        title=seg.title,
                                        task_description=seg.task_description,
                                        expected_ms=seg.expected_ms,
                                        min_duration_ms=seg.min_duration_ms,
                                        notes=seg.notes,
                                    )
                                    for i, seg in enumerate(state_rundown.segments)
                                ],
                                **progress,
                            )
                        )
                except Exception as exc:
                    self._logger.warning(f"rundown_instances 写穿失败（不阻断链路）: {exc}", exc=True)

        spawn_background_task(
            _run(),
            logger=self._logger,
            tasks=self._bg_tasks,
            label="RundownCoordinator.persist",
        )

    # ==================================================================
    # 对外面（Dashboard API 消费；Agent 上保留同名薄委托）
    # ==================================================================

    def is_available(self) -> bool:
        """判定流程单是否对外可用（运行时已加载流程单）。

        - 返回 ``False`` 表示 Dashboard 编排页应降级为不可用视图。
        """
        return self._state.rundown is not None

    def get_rundown_view(self, *, now_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """聚合 Dashboard 编排页所需的流程单视图数据（视图拼装见 rundown 子包）。"""
        return build_rundown_view(self._state, now_ms=now_ms)

    async def control(
        self,
        action: str,
        *,
        segment_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        now_ms: Optional[int] = None,
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """执行 Dashboard 手动控制动作（动作翻译见 rundown 子包；insert 的 payload 为 ``{title, content?}``）。"""
        return apply_rundown_control(self._state, action, segment_id=segment_id, payload=payload, now_ms=now_ms)

    async def switch(self, rundown_id: str) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """直播中热切换流程单（Dashboard activate 的运行态写穿）。

        从流程单库读取目标单后经 :meth:`RundownState.load` 换载——游标回到
        首环节、计时重置、开场白信号按既有 pending 链路触发，不与旧单做
        进度对齐（换单是新内容开始）。读取失败/目标不存在不改动运行态，
        返回 ``(False, 原因, 当前快照)`` 供调用方渲染。
        """
        snapshot = self._state.get_snapshot()
        if self._rundowns is None:
            return False, "流程单存储未就绪", snapshot
        try:
            rundown = await self._rundowns.get_rundown(rundown_id)
        except Exception as exc:
            self._logger.error(f"热切流程单 '{rundown_id}' 读取失败: {exc}", exc=True)
            return False, f"读取流程单失败: {exc}", snapshot
        if rundown is None:
            return False, f"流程单 '{rundown_id}' 不存在", snapshot
        self._state.load(rundown)
        self._logger.info(f"已热切流程单: id={rundown.rundown_id!r}, segments={len(rundown.segments)}")
        return True, f"已切换至流程单「{rundown.title}」", self._state.get_snapshot()

    async def apply_definition(self, definition: Dict[str, Any]) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """应用编辑后的流程单定义（Dashboard 保存时的运行态写穿）。

        收 dict 由本方法内部校验转 ``Rundown``——Dashboard 不 import 本包
        模型，规避装配链循环 import。落盘由调用方负责，本方法只管运行态：

        - 运行中的流程单 id 与定义一致 → ``replace_definition``（游标按环节
          id 对齐，进度尽量保留），成功后 Agent 下一轮决策从情境中看到新内容。
        - Agent 未加载流程单 / 运行的是其他流程单 → 运行态不动，直接成功。
        """
        try:
            rundown = Rundown.model_validate(definition)
        except Exception as exc:
            return False, f"流程单定义校验失败: {exc}", None

        current = self._state.rundown
        if current is None:
            return True, "流程单已保存（运行时未加载流程单，启动后生效）", None
        if current.rundown_id != rundown.rundown_id:
            return True, "流程单已保存（当前直播运行的是其他流程单，不受影响）", self._state.get_snapshot()

        reject = self._state.replace_definition(rundown)
        if reject is not None:
            return False, f"运行态更新被拒绝: {reject.reason}", self._state.get_snapshot()
        return True, "流程单已保存并即时生效", self._state.get_snapshot()
