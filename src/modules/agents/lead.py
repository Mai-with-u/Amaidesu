"""动态主从协调器（LeadCoordinator）——主播与自主游戏 Agent 的主控权交接中枢。

核心职责：
- 维护当前主控态（主播主控 / 游戏自主主控）、人工覆盖策略（auto / streamer / 指定游戏）
  与切换时间戳，全部切换在异步互斥锁内串行执行并保证幂等。
- 动态发现具备自主领航能力的候选 Agent（按 ``BaseAgent.supports_autonomous_lead`` 与
  ``can_take_lead()`` 运行态判定，框架层零游戏名硬编码）。
- 在主控权真实发生变化时发布 ``agent.lead.changed`` 事件并通知已注册监听器。
- 提供跨 Agent 零耦合桥接：
  - 游戏主控 → 主播解说：``bind_commentary_handler`` + ``submit_commentary``
  - 主播发声 → 游戏回声：``publish_spoken_echo``（同步直转，零唤醒）
  - 主播/观众 → 游戏非强杀建议：``send_suggestion``
  - 交权记忆摘要按需构建：``bind_handover_builder``
  - 权威承诺账本只读拉取：``set_commitment_ledger_provider`` + ``fetch_commitment_ledger``
"""

from __future__ import annotations

import asyncio
import inspect
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Union,
)

from pydantic import BaseModel, Field

from src.modules.agents.base import AgentState, BaseAgent
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.agents import (
    AgentLeadChangedPayload,
    AgentLeadCommentaryPayload,
    AgentLeadSuggestionPayload,
    LeadHandoverBrief,
)
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.agents.manager import AgentManager

logger = get_logger("LeadCoordinator")

# 主播 Agent 的框架级默认主控名（无游戏主控时的唯一回落身份）
STREAMER_AGENT_NAME = "streamer"

# 游戏主控期流程单超时提醒默认节流间隔（毫秒）：每 5 分钟至多推送 1 次，避免刷屏干扰推理
DEFAULT_RUNDOWN_NUDGE_INTERVAL_MS = 300_000

LeadMode = Literal["streamer", "game"]
LeadOverride = Union[Literal["auto", "streamer"], str]


class LeadHandoverContext(BaseModel):
    """交权给自主游戏 Agent 时随带的一份结构化现场记忆摘要。"""

    segment_id: str = ""
    segment_title: str = ""
    segment_goal: str = ""
    expected_ms: int = 0
    save_name: str = ""
    memory_brief: str = ""
    operator_notes: List[str] = Field(default_factory=list)
    recent_speech: List[str] = Field(default_factory=list)
    reason: str = ""


class CommentaryRequest(BaseModel):
    """游戏主控 Agent 提交给主播的现场解说素材（由主播按自身人设润色发声）。"""

    game: str
    summary: str
    mood: str = "neutral"
    urgency: Literal["normal", "high"] = "normal"
    about_player: str = ""
    correction: bool = False
    timestamp_ms: int = 0


class CommentaryAck(BaseModel):
    """解说请求受理回执。"""

    accepted: bool
    spoken: bool = False
    reason: str = ""


class LeadStateSnapshot(BaseModel):
    """动态主从运行态只读快照（供观测面与 REST API 序列化）。"""

    mode: LeadMode
    lead_agent: str
    override: str
    enabled: bool
    candidates: List[str] = Field(default_factory=list)
    ready_candidates: List[str] = Field(default_factory=list)
    since_ms: int
    reason: str = ""


CommentaryHandler = Callable[[CommentaryRequest], Awaitable[CommentaryAck]]
HandoverBuilder = Callable[[str], Union[Awaitable[LeadHandoverContext], LeadHandoverContext]]
LeadChangedListener = Callable[[AgentLeadChangedPayload], Union[Awaitable[None], None]]
AgentReadyListener = Callable[[str, str], Union[Awaitable[None], None]]


class PassiveCueProvider(Protocol):
    """跨场被动记忆线索提供回调协议（由记忆持有方注册，供主控游戏按需只读拉取）。"""

    def __call__(
        self,
        *,
        game: str,
        texts: Sequence[str] = (),
        players: Sequence[Tuple[str, str]] = (),
        situation: Optional[Any] = None,
        arrived_places: Sequence[str] = (),
        exclude_texts: Sequence[str] = (),
        limit: int = 2,
    ) -> Union[Awaitable[Sequence[str]], Sequence[str]]: ...


class CommitmentLedgerProvider(Protocol):
    """跨场权威承诺账本只读提供回调协议（由记忆持有方注册，供主控游戏按需拉取当前存档与在场玩家的承诺账本）。"""

    def __call__(
        self,
        *,
        game: str,
        save_id: str = "",
        player_uuids: Sequence[str] = (),
        limit: int = 8,
    ) -> Union[Awaitable[Mapping[str, Any]], Mapping[str, Any]]: ...


class LeadCoordinator:
    """动态主从协调器（组合根单例）。

    不变量：
    1. 任一时刻至多一个主控者：要么 ``mode == "streamer"`` 且 ``active_lead_agent is None``，
       要么 ``mode == "game"`` 且 ``active_lead_agent`` 为某已注册自主游戏 Agent 的注册名。
    2. 全部主控切换动作经 ``_lock`` 串行化，同状态重复请求幂等不重发事件。
    3. 所有跨 Agent 协作均走回调注册与基类协议，协调器本身不反向依赖任何具体 Agent 包。
    """

    def __init__(
        self,
        agent_manager: "AgentManager",
        event_bus: Optional[Any] = None,
        clock: Optional[Callable[[], int]] = None,
        *,
        enabled: bool = True,
    ) -> None:
        self._agent_manager = agent_manager
        self._event_bus = event_bus
        self._clock: Callable[[], int] = clock or now_ms
        self._enabled: bool = bool(enabled)

        # 初始恒为主播主控，自动策略等待流程单或运行态触发切换
        self._mode: LeadMode = "streamer"
        self._active_lead_agent: Optional[str] = None
        self._override: str = "auto"
        self._since_ms: int = self._clock()
        self._reason: str = "initial"

        # 串行保护主从切换，防止流程单切换、游戏断连与人工覆盖并发交错
        self._lock = asyncio.Lock()

        # 跨 Agent 零耦合回调槽（由主播等参与方在启动期绑定）
        self._commentary_handler: Optional[CommentaryHandler] = None
        self._handover_builder: Optional[HandoverBuilder] = None
        self._passive_cue_provider: Optional[PassiveCueProvider] = None
        self._commitment_ledger_provider: Optional[CommitmentLedgerProvider] = None
        self._lead_changed_listeners: List[LeadChangedListener] = []
        self._ready_listeners: List[AgentReadyListener] = []
        # 流程单超时提醒节流状态（上次推送时刻与对应环节 id；切段或主控变更时自动清零）
        self._last_rundown_nudge_ms: Optional[int] = None
        self._last_rundown_nudge_segment_id: str = ""

    # -------------------- 只读状态与候选发现 --------------------

    @property
    def mode(self) -> LeadMode:
        """当前主控模式：``"streamer"`` 或 ``"game"``。"""
        return self._mode

    @property
    def active_lead_agent(self) -> Optional[str]:
        """当前主控游戏 Agent 注册名；主播主控态返回 None。"""
        return self._active_lead_agent

    @property
    def lead_agent(self) -> str:
        """当前主控者注册名（``"streamer"`` 或具体游戏 Agent 注册名）。"""
        return self._active_lead_agent or STREAMER_AGENT_NAME

    @property
    def is_game_lead(self) -> bool:
        """当前是否处于游戏自主主控态。"""
        return self._mode == "game" and self._active_lead_agent is not None

    def is_agent_leading(self, name: str) -> bool:
        """指定注册名的游戏 Agent 此刻是否正处于自主主控态。"""
        return self.is_game_lead and self._active_lead_agent == name

    @property
    def override(self) -> str:
        """当前人工覆盖策略（``"auto"`` / ``"streamer"`` / 具体游戏注册名）。"""
        return self._override

    @property
    def enabled(self) -> bool:
        """动态主从开关（False 时始终保持主播主控，拒绝向游戏交权）。"""
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """同步动态主从运行期开关（供主播启动期按 ``lead_mode`` 配置对齐）。"""
        self._enabled = bool(enabled)
        logger.info(f"动态主从开关已更新: enabled={self._enabled}")

    def lead_capable_agents(self) -> List[str]:
        """动态发现已注册且声明支持自主领航的 Agent 注册名列表。"""
        result: List[str] = []
        for name in self._agent_manager.list_agents():
            if name == STREAMER_AGENT_NAME:
                continue
            agent = self._agent_manager.get_agent_by_name(name)
            if agent is not None and bool(getattr(agent, "supports_autonomous_lead", False)):
                result.append(name)
        return result

    def ready_lead_agents(self) -> List[str]:
        """动态筛选当前处于 RUNNING 且 ``can_take_lead()`` 为真的自主领航候选列表。"""
        ready: List[str] = []
        for name in self.lead_capable_agents():
            agent = self._agent_manager.get_agent_by_name(name)
            if agent is None or agent.state != AgentState.RUNNING:
                continue
            try:
                if bool(agent.can_take_lead()):
                    ready.append(name)
            except Exception as exc:  # noqa: BLE001 - 单个候选探测异常视为未就绪
                logger.warning(f"探测 Agent '{name}' 自主领航就绪态异常（视为未就绪）: {exc}", exc=True)
        return ready

    def snapshot(self) -> LeadStateSnapshot:
        """返回当前动态主从状态的只读快照。"""
        return LeadStateSnapshot(
            mode=self._mode,
            lead_agent=self.lead_agent,
            override=self._override,
            enabled=self._enabled,
            candidates=self.lead_capable_agents(),
            ready_candidates=self.ready_lead_agents(),
            since_ms=self._since_ms,
            reason=self._reason,
        )

    # -------------------- 回调绑定（跨 Agent 零耦合桥接） --------------------

    def bind_commentary_handler(self, handler: Optional[CommentaryHandler]) -> None:
        """绑定主播侧解说处理器（传 None 解绑）。"""
        self._commentary_handler = handler

    def bind_handover_builder(self, builder: Optional[HandoverBuilder]) -> None:
        """绑定交权记忆摘要构建器（手动切到游戏主控且未显式传入构建器时调用；传 None 解绑）。"""
        self._handover_builder = builder

    def set_passive_cue_provider(self, callback: Optional[PassiveCueProvider]) -> None:
        """绑定跨场被动记忆线索提供回调（供自主游戏 Agent 在主控态按需只读拉取；传 None 解绑）。"""
        self._passive_cue_provider = callback

    def set_commitment_ledger_provider(self, provider: Optional[CommitmentLedgerProvider]) -> None:
        """绑定跨场权威承诺账本只读提供回调（供自主游戏 Agent 在主控态按需拉取；传 None 解绑）。"""
        self._commitment_ledger_provider = provider

    def bind_lead_changed_listener(self, listener: LeadChangedListener) -> None:
        """注册主控切换监听器（重复注册自动去重）。"""
        if listener not in self._lead_changed_listeners:
            self._lead_changed_listeners.append(listener)

    def unbind_lead_changed_listener(self, listener: LeadChangedListener) -> None:
        """注销主控切换监听器（未注册时幂等忽略）。"""
        if listener in self._lead_changed_listeners:
            self._lead_changed_listeners.remove(listener)

    def bind_ready_listener(self, listener: AgentReadyListener) -> None:
        """注册自主游戏候选就绪通知监听器（重复注册自动去重）。"""
        if listener not in self._ready_listeners:
            self._ready_listeners.append(listener)

    def unbind_ready_listener(self, listener: AgentReadyListener) -> None:
        """注销自主游戏候选就绪通知监听器（未注册时幂等忽略）。"""
        if listener in self._ready_listeners:
            self._ready_listeners.remove(listener)

    async def notify_agent_ready(self, agent_name: str, *, reason: str = "agent_ready") -> None:
        """候选自主游戏就绪时通知监听方按现行规则幂等重评主控切换。

        触发条件：游戏侧完成开播置位或连接恢复且具备自主领航条件时调用；
        执行顺序：校验参数 -> 锁外依次回调已注册监听器并隔离单个异常，不直接抢权，
        由主播侧监听方依据开播态、流程单环节、动态主从开关与人工覆盖策略统一决定是否交权。
        """
        normalized = (agent_name or "").strip()
        if not normalized:
            return
        effective_reason = (reason or "").strip() or "agent_ready"
        for listener in list(self._ready_listeners):
            try:
                ret = listener(normalized, effective_reason)
                if inspect.isawaitable(ret):
                    await ret
            except Exception as exc:  # noqa: BLE001 - 单个就绪监听器异常隔离
                logger.warning(
                    f"候选游戏 '{normalized}' 就绪通知监听器执行异常（reason={effective_reason}，已隔离）: {exc}",
                    exc=True,
                )

    # -------------------- 主控权切换（锁内串行 + 幂等） --------------------

    async def request_game_lead(
        self,
        context: LeadHandoverContext,
        *,
        target: Optional[str] = None,
        reason: str = "",
    ) -> Optional[str]:
        """请求把主控权交给自主游戏 Agent；成功返回接管者注册名，未交权返回 None。

        执行顺序：校验开关与人工覆盖 -> 解析唯一或指定就绪候选 -> 若已有其他游戏主控则先收权
        -> 调目标 ``enter_lead`` -> 更新状态 -> 锁外广播事件并通知监听器。
        """
        emitted: List[AgentLeadChangedPayload] = []
        async with self._lock:
            if not self._enabled:
                logger.info("动态主从已禁用，拒绝向游戏交权")
                return None
            if self._override == STREAMER_AGENT_NAME:
                logger.info("当前人工覆盖强制为主播主控（override='streamer'），拒绝向游戏交权")
                return None

            requested = (target or "").strip()
            ready = self.ready_lead_agents()

            # 解析目标候选：显式指定优先，其次人工锁定目标，再次唯一就绪候选
            if requested:
                if self._override not in ("auto", requested):
                    logger.info(f"请求向 '{requested}' 交权被人工覆盖策略拦下（当前 override='{self._override}'）")
                    return None
                if self.is_agent_leading(requested):
                    return requested
                if requested not in ready:
                    logger.info(f"目标游戏 '{requested}' 未就绪或不支持自主领航（ready={ready}），暂不交权")
                    return None
                chosen = requested
            else:
                if self._override not in ("auto", STREAMER_AGENT_NAME):
                    if self.is_agent_leading(self._override):
                        return self._override
                    if self._override not in ready:
                        logger.info(f"人工覆盖目标 '{self._override}' 当前未就绪，暂不交权")
                        return None
                    chosen = self._override
                elif self.is_game_lead and self._active_lead_agent in ready:
                    return self._active_lead_agent
                elif len(ready) == 1:
                    chosen = ready[0]
                elif not ready:
                    logger.debug("当前无就绪的自主领航游戏候选，维持主播主控")
                    return None
                else:
                    logger.warning(f"存在多个就绪自主领航候选 {ready} 且未指定 target，拒绝盲选")
                    return None

            effective_reason = (reason or context.reason or "").strip() or "request_game_lead"
            ok = await self._enter_game_lead_unlocked(chosen, context, reason=effective_reason, emitted=emitted)
            result = chosen if ok else None

        await self._dispatch_emitted(emitted)
        return result

    async def reclaim_streamer_lead(self, *, reason: str = "") -> str:
        """从当前游戏主控收回主控权回主播，并返回游戏侧收尾摘要（已在主播态幂等返回空串）。"""
        emitted: List[AgentLeadChangedPayload] = []
        async with self._lock:
            effective_reason = (reason or "").strip() or "reclaim_streamer_lead"
            summary = await self._reclaim_streamer_unlocked(reason=effective_reason, emitted=emitted)

        await self._dispatch_emitted(emitted)
        return summary

    async def notify_agent_unavailable(
        self,
        agent_name: str,
        *,
        reason: str = "game_unavailable",
    ) -> str:
        """游戏断连/故障时自动交回主播主控（非当前主控者时幂等返回空串）。

        注意：断连只做运行态安全收权，不篡改人工 override 标记（由人工决定是否改回 auto/streamer）。
        """
        emitted: List[AgentLeadChangedPayload] = []
        async with self._lock:
            if not self.is_agent_leading(agent_name):
                return ""
            effective_reason = (reason or "").strip() or "game_unavailable"
            logger.warning(f"主控游戏 '{agent_name}' 不可用（reason={effective_reason}），自动交回主播主控")
            summary = await self._reclaim_streamer_unlocked(reason=effective_reason, emitted=emitted)

        await self._dispatch_emitted(emitted)
        return summary

    async def set_override(
        self,
        target: str,
        *,
        reason: str = "manual_override",
        handover_builder: Optional[HandoverBuilder] = None,
    ) -> LeadStateSnapshot:
        """设置人工覆盖策略并立即落位目标主控态。

        - ``"auto"``：解除人工覆盖，恢复跟随流程单/健康态自动判定（不强制打断当前主控态）。
        - ``"streamer"``：锁定主播主控，若当前在游戏主控则立即收权。
        - 具体候选游戏名：校验支持自主领航且就绪后构建交接记忆摘要，立即交权并锁定覆盖策略。

        Raises:
            ValueError: ``target`` 为空、不是 ``"auto"`` / ``"streamer"`` 且不在自主领航候选名单中。
            RuntimeError: 动态主从已禁用、目标游戏当前未就绪或目标拒绝接管主控。
        """
        normalized = (target or "").strip()
        effective_reason = (reason or "").strip() or "manual_override"
        emitted: List[AgentLeadChangedPayload] = []

        async with self._lock:
            candidates = self.lead_capable_agents()
            valid_targets = ("auto", STREAMER_AGENT_NAME, *candidates)
            if not normalized or normalized not in valid_targets:
                raise ValueError(f"无效的主控切换目标: '{target}'（可选: {', '.join(valid_targets)}）")

            if normalized == "auto":
                self._override = "auto"
                self._reason = effective_reason
                snap = self.snapshot()
            elif normalized == STREAMER_AGENT_NAME:
                self._override = STREAMER_AGENT_NAME
                if self.is_game_lead:
                    await self._reclaim_streamer_unlocked(reason=effective_reason, emitted=emitted)
                else:
                    self._reason = effective_reason
                snap = self.snapshot()
            else:
                if not self._enabled:
                    raise RuntimeError("动态主从已禁用，无法手动切换至游戏主控")
                ready = self.ready_lead_agents()
                if normalized not in ready:
                    raise RuntimeError(
                        f"目标游戏 '{normalized}' 当前未就绪（未运行或 can_take_lead 为 False），无法接管主控"
                    )

                prev_override = self._override
                self._override = normalized
                if not self.is_agent_leading(normalized):
                    context = await self._build_handover_context(
                        normalized,
                        reason=effective_reason,
                        builder=handover_builder or self._handover_builder,
                    )
                    ok = await self._enter_game_lead_unlocked(
                        normalized, context, reason=effective_reason, emitted=emitted
                    )
                    if not ok:
                        # 交权失败时回滚人工覆盖，避免 override 悬空指向未接管的目标
                        self._override = prev_override
                        raise RuntimeError(f"目标游戏 '{normalized}' 拒绝接管主控（enter_lead 返回 False）")
                else:
                    self._reason = effective_reason
                snap = self.snapshot()

        await self._dispatch_emitted(emitted)
        return snap

    # -------------------- 内部无锁切换与事件派发 --------------------

    async def _enter_game_lead_unlocked(
        self,
        target_name: str,
        context: LeadHandoverContext,
        *,
        reason: str,
        emitted: List[AgentLeadChangedPayload],
    ) -> bool:
        """在持锁状态下切入指定游戏主控；若先前有另一游戏在主控则先收权。"""
        agent = self._agent_manager.get_agent_by_name(target_name)
        if agent is None:
            logger.warning(f"尝试交权给 '{target_name}' 失败：Agent 不在名册")
            return False

        # 若此前有另一款游戏在主控，先让旧主控有序收尾再交给新目标
        previous_lead = self.lead_agent
        if self._active_lead_agent is not None and self._active_lead_agent != target_name:
            old_name = self._active_lead_agent
            old_summary = await self._safe_yield_lead(old_name, reason=f"switch_to:{target_name}")
            # 先将内存状态退回主播，若后续新目标接管失败可直接落盘回退事件保证一致
            self._mode = "streamer"
            self._active_lead_agent = None
            previous_lead = old_name
            rollback_summary = old_summary
        else:
            rollback_summary = ""

        handover_ctx = context if context.reason else context.model_copy(update={"reason": reason})
        try:
            accepted = bool(await agent.enter_lead(handover_ctx))
        except Exception as exc:  # noqa: BLE001 - 隔离目标 Agent 的接管异常
            logger.exception(f"Agent '{target_name}' enter_lead 执行异常: {exc}")
            accepted = False

        if not accepted:
            logger.info(f"Agent '{target_name}' 未接受主控权（reason={reason}）")
            if previous_lead != STREAMER_AGENT_NAME:
                ts = self._clock()
                self._since_ms = ts
                self._reason = f"fallback_after_refuse:{target_name}"
                emitted.append(
                    AgentLeadChangedPayload(
                        lead_agent=STREAMER_AGENT_NAME,
                        previous_lead=previous_lead,
                        mode="streamer",
                        reason=self._reason,
                        manual_override=self._override,
                        handover_summary=rollback_summary,
                        handover_brief=LeadHandoverBrief(
                            segment_id=handover_ctx.segment_id,
                            segment_title=handover_ctx.segment_title,
                            segment_goal=handover_ctx.segment_goal,
                            expected_ms=handover_ctx.expected_ms,
                            save_name=handover_ctx.save_name,
                            memory_brief=handover_ctx.memory_brief,
                            operator_notes=handover_ctx.operator_notes,
                            recent_speech=handover_ctx.recent_speech,
                        ),
                        timestamp_ms=ts,
                    )
                )
            return False

        ts = self._clock()
        self._mode = "game"
        self._active_lead_agent = target_name
        self._since_ms = ts
        self._reason = reason
        # 进入新游戏主控时重置流程单超时提醒节流，避免受上一任主控节流窗口影响
        self._last_rundown_nudge_ms = None
        self._last_rundown_nudge_segment_id = ""
        handover_summary = handover_ctx.segment_goal or handover_ctx.segment_title or ""
        emitted.append(
            AgentLeadChangedPayload(
                lead_agent=target_name,
                previous_lead=previous_lead,
                mode="game",
                reason=reason,
                manual_override=self._override,
                handover_summary=handover_summary,
                handover_brief=LeadHandoverBrief(
                    segment_id=handover_ctx.segment_id,
                    segment_title=handover_ctx.segment_title,
                    segment_goal=handover_ctx.segment_goal,
                    expected_ms=handover_ctx.expected_ms,
                    save_name=handover_ctx.save_name,
                    memory_brief=handover_ctx.memory_brief,
                    operator_notes=handover_ctx.operator_notes,
                    recent_speech=handover_ctx.recent_speech,
                ),
                timestamp_ms=ts,
            )
        )
        logger.info(f"主控权已交接: {previous_lead} -> {target_name}（mode=game, reason={reason}）")
        return True

    async def _reclaim_streamer_unlocked(
        self,
        *,
        reason: str,
        emitted: List[AgentLeadChangedPayload],
    ) -> str:
        """在持锁状态下收回主控权至主播；已在主播主控态幂等返回空串。"""
        if not self.is_game_lead or self._active_lead_agent is None:
            return ""
        previous_lead = self._active_lead_agent
        summary = await self._safe_yield_lead(previous_lead, reason=reason)
        ts = self._clock()
        self._mode = "streamer"
        self._active_lead_agent = None
        self._since_ms = ts
        self._reason = reason
        # 收回主播主控时重置流程单超时提醒节流状态
        self._last_rundown_nudge_ms = None
        self._last_rundown_nudge_segment_id = ""
        emitted.append(
            AgentLeadChangedPayload(
                lead_agent=STREAMER_AGENT_NAME,
                previous_lead=previous_lead,
                mode="streamer",
                reason=reason,
                manual_override=self._override,
                handover_summary=summary,
                timestamp_ms=ts,
            )
        )
        logger.info(f"主控权已收回主播: {previous_lead} -> {STREAMER_AGENT_NAME}（reason={reason}）")
        return summary

    async def _safe_yield_lead(self, agent_name: str, *, reason: str) -> str:
        """安全调用目标 Agent 的 ``yield_lead``，异常只记日志不阻断收权。"""
        agent = self._agent_manager.get_agent_by_name(agent_name)
        if agent is None:
            return ""
        try:
            summary = await agent.yield_lead(reason=reason)
            return str(summary or "")
        except Exception as exc:  # noqa: BLE001 - 收权路径异常隔离，确保必回主播态
            logger.exception(f"Agent '{agent_name}' yield_lead 执行异常（忽略摘要继续收权）: {exc}")
            return ""

    async def _build_handover_context(
        self,
        target_name: str,
        *,
        reason: str,
        builder: Optional[HandoverBuilder],
    ) -> LeadHandoverContext:
        """调用交接记忆摘要构建器；无构建器或构建异常时回落默认记忆摘要。"""
        if builder is None:
            return LeadHandoverContext(reason=reason)
        try:
            built = builder(target_name)
            if inspect.isawaitable(built):
                built = await built
            if isinstance(built, LeadHandoverContext):
                return built if built.reason else built.model_copy(update={"reason": reason})
            logger.warning(f"handover_builder 返回非 LeadHandoverContext（{type(built).__name__}），使用默认记忆摘要")
        except Exception as exc:  # noqa: BLE001 - 记忆摘要构建失败不阻断人工切换
            logger.warning(f"构建交接记忆摘要失败（target={target_name}，使用默认记忆摘要）: {exc}", exc=True)
        return LeadHandoverContext(reason=reason)

    async def _dispatch_emitted(self, payloads: List[AgentLeadChangedPayload]) -> None:
        """锁外按序广播 ``agent.lead.changed`` 并通知所有已注册监听器。"""
        for payload in payloads:
            if self._event_bus is not None:
                try:
                    await self._event_bus.emit(CoreEvents.AGENT_LEAD_CHANGED, payload, source="LeadCoordinator")
                except Exception as exc:  # noqa: BLE001 - 事件广播失败不影响内存主控态与监听器
                    logger.warning(f"广播 {CoreEvents.AGENT_LEAD_CHANGED} 失败: {exc}", exc=True)
            for listener in list(self._lead_changed_listeners):
                try:
                    ret = listener(payload)
                    if inspect.isawaitable(ret):
                        await ret
                except Exception as exc:  # noqa: BLE001 - 单个监听器异常隔离
                    logger.warning(f"主控切换监听器执行异常（已隔离）: {exc}", exc=True)

    # -------------------- 跨 Agent 业务桥接 --------------------

    async def _emit_commentary_event(
        self,
        game: str,
        request: CommentaryRequest,
        ack: CommentaryAck,
    ) -> None:
        """发布 ``agent.lead.commentary`` 观测事件（提交即发，成功与每种失败都算事实）。

        event_bus 未注入时跳过；广播失败只记日志，不影响受理回执。
        """
        if self._event_bus is None:
            return
        payload = AgentLeadCommentaryPayload(
            game=game,
            summary=request.summary,
            mood=request.mood,
            urgency=request.urgency,
            about_player=request.about_player,
            correction=request.correction,
            accepted=ack.accepted,
            spoken=ack.spoken,
            reason=ack.reason,
        )
        try:
            await self._event_bus.emit(CoreEvents.AGENT_LEAD_COMMENTARY, payload, source="LeadCoordinator")
        except Exception as exc:  # noqa: BLE001 - 观测事件失败不影响受理主链
            logger.warning(f"广播 {CoreEvents.AGENT_LEAD_COMMENTARY} 失败: {exc}", exc=True)

    async def _emit_suggestion_event(
        self,
        *,
        content: str,
        source: str,
        target: str,
        lead_agent: str,
        accepted: bool,
        reason: str,
    ) -> None:
        """发布 ``agent.lead.suggestion`` 观测事件（content 非空的每条退出路径都发，含拒收）。

        event_bus 未注入时跳过；广播失败只记日志，不影响转达主链。
        """
        if self._event_bus is None:
            return
        payload = AgentLeadSuggestionPayload(
            content=content,
            source=source,
            target=target,
            lead_agent=lead_agent,
            accepted=accepted,
            reason=reason,
        )
        try:
            await self._event_bus.emit(CoreEvents.AGENT_LEAD_SUGGESTION, payload, source="LeadCoordinator")
        except Exception as exc:  # noqa: BLE001 - 观测事件失败不影响转达主链
            logger.warning(f"广播 {CoreEvents.AGENT_LEAD_SUGGESTION} 失败: {exc}", exc=True)

    async def submit_commentary(self, caller: str, request: CommentaryRequest) -> CommentaryAck:
        """受理主控游戏 Agent 的解说素材并转发给主播侧处理器。

        门禁：只有此刻处于主控态的 ``caller`` 才允许提交；非主控调用或未绑定处理器时
        返回清晰拒绝回执，不抛异常。每条退出路径都发布 ``agent.lead.commentary``
        观测事件（成功与每种失败都算事实）。
        """
        caller_name = (caller or "").strip()
        if not caller_name or not self.is_agent_leading(caller_name):
            logger.debug(f"拒绝非主控方 '{caller_name}' 提交解说（当前 lead={self.lead_agent}）")
            ack = CommentaryAck(accepted=False, spoken=False, reason="not_in_lead")
            await self._emit_commentary_event(caller_name, request, ack)
            return ack

        handler = self._commentary_handler
        if handler is None:
            logger.warning(f"游戏 '{caller_name}' 提交解说但未绑定主播解说处理器")
            ack = CommentaryAck(accepted=False, spoken=False, reason="no_commentary_handler")
            await self._emit_commentary_event(caller_name, request, ack)
            return ack

        req = request if request.timestamp_ms > 0 else request.model_copy(update={"timestamp_ms": self._clock()})
        try:
            ack = await handler(req)
            if not isinstance(ack, CommentaryAck):
                ack = CommentaryAck(accepted=bool(ack), spoken=False)
        except Exception as exc:  # noqa: BLE001 - 解说处理器异常隔离
            logger.exception(f"解说处理器执行异常（game={caller_name}）: {exc}")
            ack = CommentaryAck(accepted=False, spoken=False, reason=f"handler_error: {type(exc).__name__}")
        await self._emit_commentary_event(caller_name, req, ack)
        return ack

    def publish_spoken_echo(self, text: str, *, source: str = "", timestamp_ms: int = 0) -> None:
        """把主播刚说出口的话同步转发给当前主控游戏 Agent（零唤醒、异常隔离）。"""
        if not self.is_game_lead or self._active_lead_agent is None:
            return
        spoken_text = (text or "").strip()
        if not spoken_text:
            return
        agent: Optional[BaseAgent] = self._agent_manager.get_agent_by_name(self._active_lead_agent)
        if agent is None:
            return
        ts = timestamp_ms if timestamp_ms > 0 else self._clock()
        try:
            agent.receive_spoken_echo(spoken_text, source=source, timestamp_ms=ts)
        except Exception as exc:  # noqa: BLE001 - 回声是纯旁路，绝不反噬发声主链
            logger.warning(
                f"向主控游戏 '{self._active_lead_agent}' 转发发言回声失败（已忽略）: {exc}",
                exc=True,
            )

    def publish_rundown_update(
        self,
        *,
        segment_id: str = "",
        segment_title: str = "",
        segment_goal: str = "",
        expected_ms: int = 0,
    ) -> None:
        """把最新流程单环节同步转发给当前主控游戏 Agent（异常隔离、零硬编码游戏名）。"""
        if not self.is_game_lead or self._active_lead_agent is None:
            return
        agent: Optional[BaseAgent] = self._agent_manager.get_agent_by_name(self._active_lead_agent)
        if agent is None:
            return
        try:
            agent.receive_rundown_update(
                segment_id=(segment_id or "").strip(),
                segment_title=(segment_title or "").strip(),
                segment_goal=(segment_goal or "").strip(),
                expected_ms=max(0, int(expected_ms)),
            )
            # 流程单切段或续期刷新后重置超时提醒节流状态，下一段超时时立即允许提醒
            self._last_rundown_nudge_ms = None
            self._last_rundown_nudge_segment_id = ""
        except Exception as exc:  # noqa: BLE001 - 环节同步旁路异常隔离，不阻断流程单与主控主链
            logger.warning(
                f"向主控游戏 '{self._active_lead_agent}' 同步流程单环节失败（已忽略）: {exc}",
                exc=True,
            )

    def publish_rundown_nudge(
        self,
        *,
        segment_id: str = "",
        segment_title: str = "",
        elapsed_ms: int = 0,
        expected_ms: int = 0,
        next_segment_id: str = "",
        next_segment_title: str = "",
        min_interval_ms: int = DEFAULT_RUNDOWN_NUDGE_INTERVAL_MS,
    ) -> bool:
        """向当前主控游戏 Agent 节流推送流程单环节到期/超时提醒（异常隔离、零硬编码游戏名）。

        触发条件：游戏自主主控期当前非杂谈环节进入超时状态时调用；
        动作与边界：仅在处于游戏主控态且主控 Agent 在名册中时生效；同一环节在
        ``min_interval_ms``（默认 5 分钟）内至多推送 1 次防刷屏，切换至新环节 id 时
        不受旧环节冷却约束。投递成功返回 True，未投递或处于节流窗口返回 False。
        """
        if not self.is_game_lead or self._active_lead_agent is None:
            return False
        seg_id = (segment_id or "").strip()
        interval = max(0, int(min_interval_ms))
        now = self._clock()
        if (
            interval > 0
            and self._last_rundown_nudge_ms is not None
            and seg_id == self._last_rundown_nudge_segment_id
            and (now - self._last_rundown_nudge_ms) < interval
        ):
            return False
        agent: Optional[BaseAgent] = self._agent_manager.get_agent_by_name(self._active_lead_agent)
        if agent is None:
            return False
        try:
            agent.receive_rundown_nudge(
                segment_id=seg_id,
                segment_title=(segment_title or "").strip(),
                elapsed_ms=max(0, int(elapsed_ms or 0)),
                expected_ms=max(0, int(expected_ms or 0)),
                next_segment_id=(next_segment_id or "").strip(),
                next_segment_title=(next_segment_title or "").strip(),
            )
            self._last_rundown_nudge_ms = now
            self._last_rundown_nudge_segment_id = seg_id
            return True
        except Exception as exc:  # noqa: BLE001 - 流程单超时提醒旁路异常隔离，不阻断主循环
            logger.warning(
                f"向主控游戏 '{self._active_lead_agent}' 发送流程单超时提醒失败（已忽略）: {exc}",
                exc=True,
            )
            return False

    def publish_silence_nudge(self, *, silent_ms: int) -> bool:
        """向当前主控游戏 Agent 发送直播间静默暖场提醒（异常隔离、零硬编码游戏名）。

        仅在当前处于游戏主控态且主控 Agent 在名册中时投递；投递成功返回 True，否则返回 False。
        """
        if not self.is_game_lead or self._active_lead_agent is None:
            return False
        agent: Optional[BaseAgent] = self._agent_manager.get_agent_by_name(self._active_lead_agent)
        if agent is None:
            return False
        try:
            agent.receive_silence_nudge(silent_ms=max(0, int(silent_ms)))
            return True
        except Exception as exc:  # noqa: BLE001 - 静默提醒旁路异常隔离，不阻断主循环
            logger.warning(
                f"向主控游戏 '{self._active_lead_agent}' 发送静默暖场提醒失败（已忽略）: {exc}",
                exc=True,
            )
            return False

    async def send_suggestion(
        self,
        content: str,
        *,
        target: Optional[str] = None,
        source: str = STREAMER_AGENT_NAME,
        source_dialogue: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """向当前主控游戏 Agent 转达观众建议或提醒（非强杀、不建委派账本）。

        content 非空时每条退出路径都发布 ``agent.lead.suggestion`` 观测事件（含拒收）；
        content 为空直接返回、不发事件。
        """
        text = (content or "").strip()
        if not text:
            return {"accepted": False, "target": (target or "").strip(), "reason": "empty_content"}

        if not self.is_game_lead or self._active_lead_agent is None:
            result = {
                "accepted": False,
                "target": (target or "").strip(),
                "reason": "not_in_game_lead",
            }
            await self._emit_suggestion_event(
                content=text,
                source=source,
                target=result["target"],
                lead_agent="",
                accepted=False,
                reason=result["reason"],
            )
            return result

        lead_name = self._active_lead_agent
        requested = (target or "").strip()
        if requested and requested != lead_name:
            result = {
                "accepted": False,
                "target": requested,
                "lead_agent": lead_name,
                "reason": "target_not_leading",
            }
            await self._emit_suggestion_event(
                content=text,
                source=source,
                target=requested,
                lead_agent=lead_name,
                accepted=False,
                reason=result["reason"],
            )
            return result

        agent = self._agent_manager.get_agent_by_name(lead_name)
        if agent is None:
            result = {"accepted": False, "target": lead_name, "reason": "agent_not_found"}
            await self._emit_suggestion_event(
                content=text,
                source=source,
                target=lead_name,
                lead_agent=lead_name,
                accepted=False,
                reason=result["reason"],
            )
            return result

        try:
            accepted = bool(await agent.receive_suggestion(text, source=source, source_dialogue=source_dialogue))
            reason = "" if accepted else "refused"
        except Exception as exc:  # noqa: BLE001 - 建议投递异常转结构化失败
            logger.exception(f"向主控游戏 '{lead_name}' 投递建议异常: {exc}")
            accepted = False
            reason = f"error: {type(exc).__name__}"

        await self._emit_suggestion_event(
            content=text,
            source=source,
            target=lead_name,
            lead_agent=lead_name,
            accepted=accepted,
            reason=reason,
        )
        return {
            "accepted": accepted,
            "target": lead_name,
            "lead_agent": lead_name,
            "reason": reason,
        }

    async def query_active_short_term_memory(
        self,
        query: str,
        *,
        scope: str = "all",
        limit: int = 6,
    ) -> List[Mapping[str, Any]]:
        """向当前主控或已注册具备自主领航能力的 Agent 聚合本场短期记忆条目（异常隔离、零游戏名硬编码）。

        查询顺序：若此刻存在主控游戏 Agent 则优先查它，随后补充其余在册且未停机的自主领航候选，
        单个 Agent 抛异常仅记告警并继续，不阻断上层记忆回忆。
        """
        clean_query = (query or "").strip()
        if not clean_query or limit <= 0:
            return []
        ordered_names: List[str] = []
        if self.is_game_lead and self._active_lead_agent:
            ordered_names.append(self._active_lead_agent)
        for name in self.lead_capable_agents():
            if name not in ordered_names:
                ordered_names.append(name)

        aggregated: List[Mapping[str, Any]] = []
        for name in ordered_names:
            agent = self._agent_manager.get_agent_by_name(name)
            if agent is None or agent.state == AgentState.STOPPED:
                continue
            try:
                entries = await agent.query_short_term_memory(clean_query, scope=scope, limit=limit)
            except Exception as exc:  # noqa: BLE001 - 单个 Agent 短期记忆查询异常隔离
                logger.warning(f"查询 Agent '{name}' 本场短期记忆失败（已忽略）: {exc}", exc=True)
                continue
            for entry in entries or ():
                if isinstance(entry, Mapping):
                    aggregated.append(dict(entry))
                    if len(aggregated) >= limit:
                        return aggregated
        return aggregated

    async def fetch_passive_cues(
        self,
        *,
        game: str,
        texts: Sequence[str] = (),
        players: Sequence[Tuple[str, str]] = (),
        situation: Optional[Any] = None,
        arrived_places: Sequence[str] = (),
        exclude_texts: Sequence[str] = (),
        limit: int = 2,
    ) -> List[str]:
        """供主控游戏 Agent 在收到游戏公屏合批、产生行动意图或到达地标时拉取跨场被动记忆线索（异常隔离、零游戏名硬编码）。"""
        provider = self._passive_cue_provider
        normalized_game = (game or "").strip()
        if provider is None or not normalized_game or limit <= 0:
            return []
        clean_places = tuple(p for raw in (arrived_places or ()) if (p := str(raw or "").strip()))
        clean_excludes = tuple(t for raw in (exclude_texts or ()) if (t := str(raw or "").strip()))
        kwargs: Dict[str, Any] = {
            "game": normalized_game,
            "texts": tuple(texts or ()),
            "players": tuple(players or ()),
            "situation": situation,
            "limit": int(limit),
        }
        # 检测回调签名，仅向声明接收对应可选参数或 **kwargs 的回调透传，保持对旧签名回调向后兼容
        try:
            params = inspect.signature(provider).parameters
            has_var_kw = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())
            accepts_arrived = "arrived_places" in params or has_var_kw
            accepts_excludes = "exclude_texts" in params or has_var_kw
        except (TypeError, ValueError):
            accepts_arrived = True
            accepts_excludes = True
        if accepts_arrived:
            kwargs["arrived_places"] = clean_places
        if accepts_excludes:
            kwargs["exclude_texts"] = clean_excludes
        try:
            raw = provider(**kwargs)
            if inspect.isawaitable(raw):
                raw = await raw
        except Exception as exc:  # noqa: BLE001 - 被动线索属旁路增强，异常隔离不阻断游戏决策
            logger.warning(f"拉取跨场被动记忆线索失败（game={normalized_game}，已忽略）: {exc}", exc=True)
            return []
        result: List[str] = []
        seen: set[str] = set()
        for item in raw or ():
            line = str(item or "").strip()
            if not line or line in seen:
                continue
            seen.add(line)
            result.append(line)
            if len(result) >= limit:
                break
        return result

    async def fetch_commitment_ledger(
        self,
        *,
        game: str,
        save_id: str = "",
        player_uuids: Sequence[str] = (),
        limit: int = 8,
    ) -> Dict[str, Any]:
        """供主控游戏 Agent 按当前存档与在场玩家只读拉取权威承诺账本（异常降级为空字典，零硬编码游戏名）。

        触发条件：游戏主控侧组装尾部动态任务状态前按需调用；
        动作与边界：透传 ``game`` / ``save_id`` / 去重后的 ``player_uuids`` / ``limit`` 给已注册回调，
        对返回结果按紧凑结构归一化（``open`` 与 ``closed_this_session``）；两者皆空或回调异常时
        一律返回 ``{}``，避免向尾部状态输出空结构浪费上下文。
        """
        provider = self._commitment_ledger_provider
        normalized_game = (game or "").strip()
        effective_limit = int(limit)
        if provider is None or not normalized_game or effective_limit <= 0:
            return {}
        clean_save_id = (save_id or "").strip()
        clean_uuids = tuple(dict.fromkeys(uid for raw in (player_uuids or ()) if (uid := str(raw or "").strip())))
        try:
            raw = provider(
                game=normalized_game,
                save_id=clean_save_id,
                player_uuids=clean_uuids,
                limit=effective_limit,
            )
            if inspect.isawaitable(raw):
                raw = await raw
        except Exception as exc:  # noqa: BLE001 - 承诺账本查询属只读增强，异常隔离不阻断游戏决策
            logger.warning(f"拉取跨场权威承诺账本失败（game={normalized_game}，已忽略）: {exc}", exc=True)
            return {}
        if not isinstance(raw, Mapping):
            return {}

        open_items: List[Dict[str, str]] = []
        for item in raw.get("open") or ():
            if not isinstance(item, Mapping):
                continue
            cid = str(item.get("id") or "").strip()
            what = " ".join(str(item.get("what") or "").split())
            target = " ".join(str(item.get("target") or "").split()) or "公共"
            progress = " ".join(str(item.get("progress") or "").split())
            if not cid or not what:
                continue
            entry: Dict[str, str] = {"id": cid, "target": target, "what": what}
            if progress:
                entry["progress"] = progress
            open_items.append(entry)
            if len(open_items) >= effective_limit:
                break

        closed_items: List[Dict[str, str]] = []
        for item in raw.get("closed_this_session") or ():
            if not isinstance(item, Mapping):
                continue
            cid = str(item.get("id") or "").strip()
            what = " ".join(str(item.get("what") or "").split())
            summary = " ".join(str(item.get("summary") or "").split())
            if not cid or (not what and not summary):
                continue
            closed_items.append({"id": cid, "what": what, "summary": summary})

        if not open_items and not closed_items:
            return {}
        return {"open": open_items, "closed_this_session": closed_items}


__all__ = [
    "STREAMER_AGENT_NAME",
    "DEFAULT_RUNDOWN_NUDGE_INTERVAL_MS",
    "LeadMode",
    "LeadOverride",
    "LeadHandoverContext",
    "CommentaryRequest",
    "CommentaryAck",
    "LeadStateSnapshot",
    "CommentaryHandler",
    "HandoverBuilder",
    "LeadChangedListener",
    "AgentReadyListener",
    "PassiveCueProvider",
    "CommitmentLedgerProvider",
    "LeadCoordinator",
]
