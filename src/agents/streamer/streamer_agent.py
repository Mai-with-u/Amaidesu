"""StreamerAgent - 主播 Agent（Wave 6 / §1.4 / §1.49 BaseAgent 子类）

§1.4 定案：主播 Agent = Planner（决策核心）+ reply 工具（入口）+ Replyer（表达引擎），一体。
§1.49 协议六面（最小契约）：

| # | 面 | 内容 |
|---|---|---|
| 1 | 生命周期 | start/stop/cleanup + 可重建性 |
| 2 | 工具提供 | list_tools() → 暴露 reply / should_speak_proactively / parse_command |
| 3 | 事件上报 | emit（planner.checkpoint 等；订阅 room.message.danmaku 等） |
| 4 | 状态读写 | RoomState / AgendaState 内部组件 |
| 5 | 健康 | BaseAgent 心跳协议 |
| 6 | 元数据 | name / description |

接入方式（§1.49 继承 + 构造注入）：
```python
agent = StreamerAgent(
    config=streamer_agent_config,
    llm_manager=llm,
    prompt_manager=prompt,
    context_service=context,
    event_bus=bus,
    tool_registry=registry,
    capabilities_provider=None,
    sqlite_store=store,
)
await agent.start()
# Agent now: subscribes room.message.danmaku → buffers → planner → reply tool → ...
await agent.cleanup()
```

Wave 6 重构要点：
- 移除 AmaidesuDecider wrapper：Agent 本身直接编排子组件
- 新增 toolset：reply / proactive / command 三个 @tool
- 后台双任务（BackgroundMaintainer）取代 RoomStateLoop + 部分 AmaidesuDecider 职责
- 持久化走 SQLiteStore（live_sessions + agenda_runtime）
- 删除 Intent：决策出口=reply 工具调用，零 Intent 事件
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Iterable, List, Optional

from src.modules.agents.base import BaseAgent
from src.modules.agents.manager import AgentManager
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.logging import get_logger
from src.modules.tools import ToolSpec
from src.modules.tools.registry import ToolRegistry
from src.modules.types.base.normalized_message import NormalizedMessage

from .agenda_idle import AgendaIdle
from .agenda_loader import AgendaLoader
from .agenda_state import AgendaState
from .background import BackgroundMaintainer
from .command_tool import CommandToolProvider
from .message_buffer import MessageBuffer
from .planner import Planner
from .proactive_tool import ProactiveToolProvider
from .proactive_trigger import ProactiveTrigger
from .reply_tool import ReplyToolProvider
from .replyer import ProfanityFilter, Replyer
from .room_state import RoomState
from .timing_gate import TimingGate

__all__ = ["StreamerAgent", "StreamerAgentConfig", "build_streamer_agent"]


# ---------------------------------------------------------------------------
# 配置 Schema
# ---------------------------------------------------------------------------


from pydantic import Field as _PydField


class StreamerAgentConfig(BaseConfig):
    """主播 Agent 配置 Schema（替代旧 AmaidesuDecider.ConfigSchema）。

    Wave 6 重构后字段名对齐 agents_schemas.StreamerAgentConfig（planner_llm / replyer_llm）
    + 保留旧字段（replyer_client / planner_client）作为向后兼容映射。
    """

    # --- 两阶段 LLM profile（Wave 6 新字段）---
    planner_llm: str = _PydField(default="llm_fast", description="Planner 使用的 LLM profile")
    replyer_llm: str = _PydField(default="llm", description="Replyer 使用的 LLM profile")

    # 旧字段名兼容（向后兼容旧 [agents.amaidesu] 段）
    planner_client: str = _PydField(default="llm_fast", description="（兼容字段）Planner LLM client")
    replyer_client: str = _PydField(default="llm", description="（兼容字段）Replyer LLM client")

    # --- Stage 1 弹幕聚合（idle 补偿公式保留）---
    batch_window_ms: int = _PydField(default=3000, ge=0, description="弹幕聚合时间窗口（毫秒）")
    batch_max_size: int = _PydField(default=20, ge=1, description="单批最多聚合的弹幕条数")
    tick_interval_ms: int = _PydField(default=300, ge=50, description="后台聚合检查间隔（毫秒）")
    enable_idle_compensation: bool = _PydField(default=True, description="空窗补偿开关")

    # --- Stage 1 强制触发 ---
    force_data_types: List[str] = _PydField(
        default_factory=lambda: ["super_chat", "guard", "gift"],
        description="强制响应的数据类型",
    )
    force_importance: float = _PydField(default=0.8, ge=0.0, le=1.0, description="importance 达到该值则强制响应")

    # --- 人设 ---
    bot_name: str = _PydField(default="爱德丝", description="VTuber 名称")
    history_limit: int = _PydField(default=30, ge=0, description="构建 prompt 时引用的历史消息条数")
    enable_action_selection: bool = _PydField(
        default=True,
        description="是否让 LLM 从工具能力中选择动作",
    )

    # --- 房间状态后台预处理（§1.7 后台双任务：轻循环）---
    room_state_enabled: bool = _PydField(default=True, description="是否启用房间状态后台预处理")
    room_state_cold_timeout_ms: int = _PydField(default=60_000, ge=0, description="房间冷场判定阈值（毫秒）")
    room_state_llm_summary_interval_ms: int = _PydField(default=60_000, ge=0, description="低频 LLM 摘要间隔（毫秒）")
    room_state_summary_client: str = _PydField(
        default="llm_summary",
        description="房间状态摘要专用 LLM profile（独立 client 实例）",
    )

    # --- 主动发言 ---
    proactive_enabled: bool = _PydField(default=False, description="主动发言总开关（默认关闭）")
    proactive_cold_timeout_ms: int = _PydField(default=45_000, ge=0, description="冷场判定阈值（毫秒）")
    proactive_min_interval_ms: int = _PydField(default=120_000, ge=0, description="两次主动发言最小间隔")
    proactive_schedule_interval_ms: int = _PydField(default=300_000, ge=0, description="定时话题触发间隔（0 = 关闭）")
    proactive_schedule_only_cold: bool = _PydField(default=True, description="定时触发是否仅限冷场")
    proactive_max_per_hour: int = _PydField(default=6, ge=1, description="每小时主动发言次数上限")
    proactive_topic_required: bool = _PydField(default=True, description="话题摘要缺失时是否跳过触发")

    # --- Agenda（§1.7）---
    agenda_enabled: bool = _PydField(default=False, description="Agenda 总开关（默认关闭）")
    agenda_path: str = _PydField(default="", description="Agenda TOML 文件路径")
    agenda_expand_client: str = _PydField(default="llm_agenda", description="AI 扩展用 LLM profile")
    agenda_advance_eval_enabled: bool = _PydField(default=True, description="Planner 顺带评估开关")
    agenda_scheduler_tick_ms: int = _PydField(default=1_000, ge=1, description="Agenda 调度循环 tick 间隔（毫秒）")
    agenda_auto_start: bool = _PydField(default=True, description="setup 时自动加载并启动 Agenda")
    agenda_speech_interval_ms: int = _PydField(default=3_000, ge=1000, description="Agenda 环节内两次主动发言最小间隔")

    # --- 敏感词净化（§1.46.1）---
    profanity_enabled: bool = _PydField(default=False, description="敏感词净化开关")
    profanity_words: List[str] = _PydField(default_factory=list, description="敏感词列表")
    profanity_replacement: str = _PydField(default="***", description="替换字符")
    profanity_case_sensitive: bool = _PydField(default=False, description="是否大小写敏感")
    profanity_drop_on_match: bool = _PydField(default=False, description="命中时是否整条丢弃")

    # --- 命令解析（Wave 6：CommandDecider → parse_command 工具）---
    command_prefix: str = _PydField(default="/", description="命令前缀")
    command_mappings: Dict[str, str] = _PydField(
        default_factory=lambda: {
            "chat": "chat",
            "say": "chat",
            "聊天": "chat",
            "attack": "attack",
            "攻击": "attack",
        },
        description="命令映射 {name: action}",
    )


# ---------------------------------------------------------------------------
# StreamerAgent
# ---------------------------------------------------------------------------


class StreamerAgent(BaseAgent):
    """主播 Agent：编排 Planner + Replyer + 工具 + 后台任务 + Agenda。

    实现 §1.49 协议六面：
    1. 生命周期（start/stop/cleanup）
    2. 工具提供：reply / should_speak_proactively / parse_command（3 个 @tool）
    3. 事件上报：emit（planner.checkpoint 等）；订阅 room.message.*
    4. 状态读写：内部 RoomState / AgendaState / MessageBuffer
    5. 健康：BaseAgent 心跳
    6. 元数据：name / description
    """

    # -----协议 6：元数据（必须覆写）-----
    name = "streamer_agent"
    description = "Streamer Agent - 主播决策 + 表达 + 后台维护（Wave 6 §1.4）"

    # -----协议 3：事件族声明（可选）-----
    emits_events = (
        CoreEvents.PLANNER_CHECKPOINT,
        CoreEvents.AGENDA_UPDATE,
    )

    def __init__(
        self,
        config: StreamerAgentConfig,
        *,
        llm_manager: Any,
        prompt_manager: Any,
        context_service: Optional[Any] = None,
        event_bus: Optional[EventBus] = None,
        tool_registry: Optional[ToolRegistry] = None,
        capabilities_provider: Optional[Any] = None,
        sqlite_store: Optional[Any] = None,
        persona_provider: Optional[Any] = None,
    ) -> None:
        """初始化主播 Agent。

        Args:
            config: ``StreamerAgentConfig`` 实例
            llm_manager: ``LLMManager`` 实例
            prompt_manager: ``PromptManager`` 实例
            context_service: 可选 ``ContextService``（持久化对话历史）
            event_bus: 可选 ``EventBus``（Agent 通过它订阅 room.message.* / emit planner.checkpoint）
            tool_registry: 可选 ``ToolRegistry``（Agent 把自己的工具注册进去）
            capabilities_provider: 可选工具能力提供者（用于 reply 动作白名单）
            sqlite_store: 可选 ``SQLiteStore``（live_sessions + agenda_runtime 持久化）
            persona_provider: 可选人设字典来源（鸭子类型：callable 返回 dict / dict 本身）
        """
        super().__init__(event_bus=event_bus)
        self.typed_config = config
        self._llm = llm_manager
        self._prompt = prompt_manager
        self._context = context_service
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._capabilities_provider = capabilities_provider
        self._sqlite = sqlite_store
        self._persona_provider = persona_provider
        self._logger = get_logger("StreamerAgent")

        # ===== 内部子组件 =====
        # Stage 1: 弹幕聚合缓冲 + 强制触发判定
        self._buffer = MessageBuffer(
            batch_window_ms=config.batch_window_ms,
            batch_max_size=config.batch_max_size,
            enable_idle_compensation=config.enable_idle_compensation,
        )
        self._timing_gate = TimingGate(
            force_data_types=config.force_data_types,
            force_importance=config.force_importance,
        )

        # 房间态势（纯规则滑动窗口）
        self._room_state = RoomState()

        # Stage 1: Planner（决策核心，Agent 内脏——非工具）
        self._planner = Planner(
            config={
                "planner_llm": config.planner_llm,
                "planner_client": config.planner_client,
            },
            llm_service=llm_manager,
            prompt_service=prompt_manager,
            room_state=self._room_state,
            capabilities_provider=capabilities_provider,
        )

        # 敏感词过滤器（§1.46.1）
        self._profanity_filter = ProfanityFilter(
            words=config.profanity_words if config.profanity_enabled else None,
            replacement=config.profanity_replacement,
            case_sensitive=config.profanity_case_sensitive,
            drop_on_match=config.profanity_drop_on_match,
            enabled=config.profanity_enabled,
        )

        # Stage 2: Replyer（表达引擎，Agent 内脏——非工具）
        self._replyer = Replyer(
            config={
                "replyer_llm": config.replyer_llm,
                "replyer_client": config.replyer_client,
                "enable_action_selection": config.enable_action_selection,
                "bot_name": config.bot_name,
            },
            llm_service=llm_manager,
            prompt_service=prompt_manager,
            capabilities_provider=capabilities_provider,
            profanity_filter=self._profanity_filter,
        )

        # 主动发言触发器（纯规则组件，Agent 内脏）
        proactive_config = {
            "enabled": config.proactive_enabled,
            "cold_timeout_ms": config.proactive_cold_timeout_ms,
            "min_interval_ms": config.proactive_min_interval_ms,
            "schedule_interval_ms": config.proactive_schedule_interval_ms,
            "schedule_only_cold": config.proactive_schedule_only_cold,
            "max_per_hour": config.proactive_max_per_hour,
            "topic_required": config.proactive_topic_required,
            "agenda_speech_interval_ms": config.agenda_speech_interval_ms,
        }
        self._proactive_trigger = ProactiveTrigger(proactive_config)

        # Agenda 子系统
        self._agenda_state = AgendaState()
        self._agenda_loader: Optional[AgendaLoader] = None
        self._agenda_idle: Optional[AgendaIdle] = None

        # 后台维护器（§1.7 双任务：轻循环 + 压缩 worker）
        background_config = {
            "light_tick_ms": 5_000,
            "cold_timeout_ms": config.room_state_cold_timeout_ms,
            "summary_interval_ms": config.room_state_llm_summary_interval_ms,
            "summary_client": config.room_state_summary_client,
        }
        self._background = BackgroundMaintainer(
            background_config,
            room_state=self._room_state,
            llm_service=llm_manager,
            live_session_store=sqlite_store,  # duck-typed: update_live_session_heartbeat
            context_service=context_service,
        )

        # 后台 flush 循环（Wave 6 Agent 主循环——替代 AmaidesuDecider._flush_loop）
        self._flush_task: Optional[asyncio.Task] = None
        self._flush_lock = asyncio.Lock()
        self._running = False

        # 一次性 pending flag（外部 API 触发主动发言）
        self._external_proactive_pending: bool = False
        # Agenda 切换触发 flag（AgendaIdle on_advance 回调置位）
        self._agenda_proactive_pending: bool = False

        # 统计
        self._total_messages = 0
        self._total_batches = 0
        self._total_replies = 0
        self._total_no_action = 0
        self._total_proactive = 0
        self._planner_failures = 0
        self._replyer_failures = 0

        # 工具 Provider 实例（用于 invoke）
        self._reply_provider: Optional[ReplyToolProvider] = None
        self._proactive_provider: Optional[ProactiveToolProvider] = None
        self._command_provider: Optional[CommandToolProvider] = None

        self._logger.info(
            f"StreamerAgent 已构造 "
            f"(planner_llm={config.planner_llm}, replyer_llm={config.replyer_llm}, "
            f"proactive_enabled={config.proactive_enabled}, "
            f"agenda_enabled={config.agenda_enabled})"
        )

    # ==================================================================
    # 协议 6 + 1：生命周期
    # ==================================================================

    async def _on_start(self) -> None:
        """Agent 启动钩子：订阅事件 + 启动后台任务 + 注册工具。"""
        self._running = True

        # 1. 注册自己的工具到 ToolRegistry
        if self._tool_registry is not None:
            self._register_tools()

        # 2. 订阅 room.message.*（collectors emit 的语义域事件）
        if self._event_bus is not None:
            self._subscribe_events()

        # 3. 启动后台 flush 循环
        self._flush_task = asyncio.create_task(self._flush_loop())

        # 4. 启动后台双任务（轻循环 + 压缩 worker）
        if self.typed_config.room_state_enabled:
            await self._background.start()

        # 5. 启动 Agenda 组件
        if self.typed_config.agenda_enabled and self.typed_config.agenda_auto_start:
            await self._start_agenda_components()

        self._logger.info("StreamerAgent 已启动")

    async def _on_stop(self) -> None:
        """Agent 停止钩子。"""
        self._running = False

        # 停止后台双任务
        try:
            await self._background.stop()
        except Exception as exc:
            self._logger.warning(f"停止 BackgroundMaintainer 失败: {exc}")

        # 停止 Agenda
        if self._agenda_idle is not None:
            try:
                await self._agenda_idle.stop()
            except Exception as exc:
                self._logger.warning(f"停止 AgendaIdle 失败: {exc}")

        # 停止 flush 循环
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        self._flush_task = None

        self._logger.info("StreamerAgent 已停止")

    # ==================================================================
    # 协议 2：工具提供（list_tools）
    # ==================================================================

    def list_tools(self) -> Iterable[ToolSpec]:
        """声明本 Agent 暴露的工具。

        通过 AgentManager.register_all_tools 时聚合到 ToolRegistry。
        """
        # Agent 本身只声明自己的 spec 集合；实际 invoke 由 Provider 处理
        from .reply_tool import build_reply_tool_spec
        from .proactive_tool import build_proactive_tool_spec
        from .command_tool import build_command_tool_spec

        return [
            build_reply_tool_spec(),
            build_proactive_tool_spec(),
            build_command_tool_spec(),
        ]

    def _register_tools(self) -> None:
        """注册 reply / proactive / command 三个工具 Provider 到 ToolRegistry。"""
        if self._tool_registry is None:
            return

        # reply tool
        self._reply_provider = ReplyToolProvider(
            replyer=self._replyer,
            persona=self._persona_provider or {},
            history_provider=(self._read_history_sync if self._context is not None else None),
            agenda_text_provider=self._build_agenda_text_sync,
        )
        self._tool_registry.register_provider(self._reply_provider)

        # proactive tool
        self._proactive_provider = ProactiveToolProvider(
            trigger=self._proactive_trigger,
            room_state=self._room_state,
            external_pending=lambda: self._external_proactive_pending,
            agenda_pending=lambda: self._agenda_proactive_pending,
            agenda_ready=self._is_agenda_active,
        )
        self._tool_registry.register_provider(self._proactive_provider)

        # command tool
        self._command_provider = CommandToolProvider(
            command_prefix=self.typed_config.command_prefix,
            command_mappings=self.typed_config.command_mappings,
        )
        self._tool_registry.register_provider(self._command_provider)

        self._logger.info("StreamerAgent 3 个工具已注册：reply / should_speak_proactively / parse_command")

    # ==================================================================
    # 协议 3：事件订阅
    # ==================================================================

    def _subscribe_events(self) -> None:
        """订阅 room.message.* 事件（collectors emit 的语义域事件）。"""
        if self._event_bus is None:
            return
        self._event_bus.on(
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            self._on_danmaku_received,
            model_class=RoomMessagePayload,
            priority=50,
        )
        self._logger.info("StreamerAgent 已订阅 room.message.danmaku")

    async def _on_danmaku_received(
        self,
        event_name: str,
        payload: RoomMessagePayload,
        source: str,
    ) -> None:
        """弹幕事件回调：转 NormalizedMessage + 推进 RoomState + 入缓冲。"""
        if payload.message_type != "danmaku":
            return
        try:
            from src.modules.types.message_type import require_message_type

            msg = NormalizedMessage(
                text=payload.content,
                source=source or "room.message.danmaku",
                data_type="text",
                importance=0.5,
                timestamp=payload.timestamp_ms,
                user_id=payload.user.id,
                user_nickname=payload.user.name,
            )
            require_message_type(msg.data_type)
        except Exception as exc:
            self._logger.warning(f"弹幕事件转 NormalizedMessage 失败: {exc}")
            return
        await self.handle_message(msg)

    async def handle_message(self, msg: NormalizedMessage) -> None:
        """处理一条弹幕（collectors → Agent 入口；测试也可直接调）。"""
        from src.modules.time_utils import now_ms

        self._total_messages += 1
        # 1. RoomState 热度信号
        self._room_state.update(msg, now_ms=now_ms())
        # 2. TimingGate 强制判定
        forced = self._timing_gate.is_forced(msg)
        # 3. 入缓冲
        self._buffer.add(msg, arrival_ms=now_ms(), forced=forced)

    def trigger_external_proactive(self, topic_hint: Optional[str] = None) -> None:
        """外部 API 触发主动发言（Dashboard / API 调用）。"""
        self._external_proactive_pending = True
        if topic_hint:
            self._logger.info(f"外部主动发言触发: {topic_hint}")

    # ==================================================================
    # 后台 flush 循环（Wave 6 Agent 主循环，替代 AmaidesuDecider._flush_loop）
    # ==================================================================

    async def _flush_loop(self) -> None:
        """后台循环：周期性检查缓冲并触发批次决策。"""
        interval = max(self.typed_config.tick_interval_ms / 1000.0, 0.05)
        try:
            while self._running:
                await asyncio.sleep(interval)
                try:
                    await self._maybe_flush()
                except Exception as exc:
                    self._logger.error(f"批次决策异常: {exc}", exc_info=True)
        except asyncio.CancelledError:
            raise

    async def _maybe_flush(self) -> None:
        """判断是否应该取出一批并做两阶段决策（与 AmaidesuDecider._maybe_flush 同构）。"""
        from src.modules.time_utils import now_ms

        if self._flush_lock.locked():
            return

        async with self._flush_lock:
            # 分支 2：buffer 空时 → 主动发言判定
            if self._buffer.is_empty:
                agenda_pending = self._agenda_proactive_pending
                self._agenda_proactive_pending = False
                reason = self._proactive_trigger.should_trigger(
                    self._room_state,
                    now_ms(),
                    external_pending=self._external_proactive_pending,
                    agenda_pending=agenda_pending,
                    agenda_ready=self._is_agenda_active(),
                )
                self._external_proactive_pending = False
                if reason is not None:
                    self._total_proactive += 1
                    self._logger.info(f"主动发言触发: {reason}")
                    await self._make_two_stage_decision(
                        [],
                        forced=False,
                        trigger_reason=f"proactive:{reason}",
                        proactive=True,
                    )
                return

            # 分支 1：弹幕聚合 → 两阶段决策
            now = now_ms()
            avg_interval_ms = self._estimate_avg_interval_ms()
            flush_due, flush_reason = self._buffer.should_flush(now, avg_interval_ms=avg_interval_ms)
            if not flush_due:
                return

            forced = self._buffer.force
            batch = self._buffer.drain()
            if not batch:
                return
            self._total_batches += 1

            await self._make_two_stage_decision(batch, forced=forced, trigger_reason=flush_reason)

    def _estimate_avg_interval_ms(self) -> Optional[float]:
        """估算缓冲内消息平均间隔（供 idle 补偿公式使用）。"""
        buf = self._buffer
        if buf.size < 2:
            return None
        span = buf.last_arrival_ms - buf.first_arrival_ms
        if span <= 0:
            return None
        return span / (buf.size - 1)

    async def _make_two_stage_decision(
        self,
        batch: List[NormalizedMessage],
        *,
        forced: bool,
        trigger_reason: str,
        proactive: bool = False,
    ) -> None:
        """两阶段决策：Planner → 消费 plan 评估 + 触发 reply 工具。"""
        from src.modules.time_utils import now_ms

        # 1. 读历史（duck-typed）
        history = await self._read_history("live")

        # 2. 拼装 Agenda 上下文
        agenda_text = self._build_agenda_text()

        # 3. Planner 决策
        try:
            plan = await self._planner.plan(
                batch,
                forced=forced,
                proactive=proactive,
                history=history,
                agenda_text=agenda_text,
            )
        except Exception as exc:
            self._logger.error(f"Planner 调用异常: {exc}", exc_info=True)
            plan = None

        if plan is None:
            self._planner_failures += 1
            self._total_no_action += 1
            return

        # 4. Plan 裁决
        if not plan.should_reply:
            self._total_no_action += 1
            self._consume_plan_assessment(plan)
            return

        # 5. 触发 reply 工具（StreamerAgent 直接调 reply Provider.invoke，
        #    跳过 LLM chat loop——Agent 内脏直连 LLM executor）
        try:
            result = await self._reply_provider.invoke(  # type: ignore[union-attr]
                self._make_reply_invocation(plan, batch)
            )
        except Exception as exc:
            self._logger.error(f"Reply 工具调用异常: {exc}", exc_info=True)
            self._replyer_failures += 1
            self._total_no_action += 1
            return

        if not result.success:
            self._replyer_failures += 1
            self._total_no_action += 1
            self._logger.warning(f"Reply 工具返回失败: {result.error_message}")
            return

        # 6. 成功：保存上下文 + 记录发言时刻 + 频率限制
        self._total_replies += 1
        self._room_state.record_speech(now_ms())
        if proactive:
            reason = trigger_reason.removeprefix("proactive:") if trigger_reason else "unknown"
            self._proactive_trigger.record_trigger(reason, now_ms())

        # 7. 消费 Planner 顺带评估（灌入 AgendaIdle）
        self._consume_plan_assessment(plan)

        # 8. 持久化 Agenda runtime
        if self._agenda_idle is not None and self._agenda_state.agenda is not None:
            try:
                await self._agenda_state.persist_runtime()
            except Exception:
                pass

    def _make_reply_invocation(
        self,
        plan: Any,
        batch: List[NormalizedMessage],
    ) -> Any:
        """构造 reply 工具的 ToolInvocation。"""
        from src.modules.tools import ToolInvocation

        return ToolInvocation(
            tool_name="reply",
            arguments={
                "topic_summary": plan.topic_summary,
                "reply_guidance": plan.reply_guidance,
                "target": plan.target,
                "confidence": plan.confidence,
            },
            source="streamer_agent",
        )

    def _consume_plan_assessment(self, plan: Any) -> None:
        """消费 Planner 评估字段，灌入 AgendaIdle。"""
        if self._agenda_idle is None:
            return
        if not self.typed_config.agenda_advance_eval_enabled:
            return
        try:
            self._agenda_idle.note_plan_assessment(
                may_advance=getattr(plan, "may_advance", False),
                need_more_time=getattr(plan, "need_more_time", False),
                branch_id=getattr(plan, "branch_id", None),
            )
        except Exception as exc:
            self._logger.warning(f"消费 Planner 评估异常: {exc}")

    # ==================================================================
    # Agenda 子系统管理
    # ==================================================================

    def _is_agenda_active(self) -> bool:
        """判定 Agenda 是否激活且有当前环节（供 ProactiveToolProvider outline_ready）。"""
        if self._agenda_state.agenda is None:
            return False
        if self._agenda_state.status.value != "running":
            return False
        return self._agenda_state.current_segment_id is not None

    def _build_agenda_text(self) -> Optional[str]:
        """拼装 Agenda 渲染文本（注入 Planner/Replyer $agenda 变量）。"""
        from src.modules.time_utils import format_duration_ms

        if not self._is_agenda_active():
            return None
        state = self._agenda_state
        seg_id = state.current_segment_id
        if seg_id is None:
            return None
        seg = self._find_agenda_segment(seg_id)
        if seg is None:
            return None
        agenda = state.agenda
        if agenda is None:
            return None
        seg_ids = [s.id for s in agenda.segments]
        try:
            idx = seg_ids.index(seg_id)
        except ValueError:
            return None
        total = len(agenda.segments)
        title = getattr(seg, "title", "") or ""
        task = getattr(seg, "task_description", "") or ""
        key_points = list(getattr(seg, "key_points", []) or [])
        expanded = state.get_expanded(seg_id)
        topic_guidance = (getattr(expanded, "topic_guidance", "") if expanded is not None else "") or task
        remaining_ms = state.get_current_segment_remaining_ms()
        elapsed_live = state.get_elapsed_live_ms()
        total_planned = state.get_total_planned_ms()
        progress_pct = state.get_progress_percent()

        lines = []
        lines.append(f"当前环节：{title}（第 {idx + 1}/{total} 环节）")
        if task:
            lines.append(f"任务：{task}")
        if topic_guidance and topic_guidance != task:
            lines.append(f"话题引导：{topic_guidance}")
        if key_points:
            lines.append(f"关键节点：{'、'.join(key_points)}")
        lines.append(f"环节剩余：约 {format_duration_ms(remaining_ms)}")
        if elapsed_live is not None and total_planned is not None and progress_pct is not None:
            lines.append(
                f"整场进度：已进行 {format_duration_ms(elapsed_live)} / "
                f"共 {format_duration_ms(total_planned)}（{progress_pct:.0f}%）"
            )
        return "\n".join(lines)

    def _build_agenda_text_sync(self) -> Optional[str]:
        """同步包装（reply_tool 的 agenda_text_provider 鸭子接口）。"""
        return self._build_agenda_text()

    def _find_agenda_segment(self, segment_id: str) -> Any:
        """按 id 在当前 Agenda 中查找环节对象。"""
        if self._agenda_state.agenda is None:
            return None
        for seg in self._agenda_state.agenda.segments:
            if getattr(seg, "id", None) == segment_id:
                return seg
        return None

    async def _start_agenda_components(self) -> None:
        """构造 + 启动 AgendaLoader / AgendaState / AgendaIdle。"""
        path = self.typed_config.agenda_path
        if not path:
            self._logger.info("Agenda 未配置路径，跳过加载")
            return
        try:
            self._agenda_loader = AgendaLoader(
                llm_manager=self._llm,
                prompt_manager=self._prompt,
                config={
                    "agenda_expand_client": self.typed_config.agenda_expand_client,
                },
            )
            agenda = await self._agenda_loader.load(path)
            self._agenda_state.start(agenda)

            self._agenda_idle = AgendaIdle(
                config={
                    "agenda_scheduler_tick_ms": self.typed_config.agenda_scheduler_tick_ms,
                    "agenda_advance_eval_enabled": self.typed_config.agenda_advance_eval_enabled,
                },
                state=self._agenda_state,
                loader=self._agenda_loader,
                on_advance=self._on_agenda_advance,
            )
            if self._event_bus is not None:
                self._agenda_idle.attach_event_bus(self._event_bus)
            await self._agenda_idle.start()

            self._logger.info(
                f"Agenda 已加载: path={path!r}, agenda_id={agenda.agenda_id!r}, segments={len(agenda.segments)}"
            )
        except Exception as exc:
            self._logger.error(f"Agenda 加载失败，降级为无 Agenda 模式: {exc}", exc_info=True)
            self._agenda_idle = None
            self._agenda_loader = None

    def _on_agenda_advance(self, new_segment_id: str, reason: Optional[str]) -> None:
        """AgendaIdle 推进回调（同步方法）。"""
        self._agenda_proactive_pending = True
        self._logger.info(f"Agenda 推进回调: new_segment={new_segment_id!r}, reason={reason!r}")

    # ==================================================================
    # 历史读取（duck-typed 鸭子接口）
    # ==================================================================

    def _read_history_sync(self):
        """reply_tool.history_provider 鸭子接口（返回 awaitable）。

        实际实现是返回 coroutine（不是同步 list），由 ReplyToolProvider 检测 awaitable 并 await。
        """
        if self._context is None:
            return None

        async def _do_read():
            try:
                return await self._context.get_history(  # type: ignore[union-attr]
                    "live", limit=self.typed_config.history_limit
                )
            except Exception as exc:
                self._logger.warning(f"读取会话历史失败: {exc}")
                return None

        return _do_read()

    async def _read_history(self, session_id: str) -> Optional[List[Any]]:
        """async 版本，供 _make_two_stage_decision 直接调用。"""
        if self._context is None:
            return None
        try:
            return await self._context.get_history(  # type: ignore[union-attr]
                session_id, limit=self.typed_config.history_limit
            )
        except Exception as exc:
            self._logger.warning(f"读取会话历史失败: {exc}")
            return None

    # ==================================================================
    # 统计信息
    # ==================================================================

    def get_statistics(self) -> Dict[str, Any]:
        """获取运行时统计信息（结构向后兼容旧 get_statistics）。"""
        return {
            "total_messages": self._total_messages,
            "total_batches": self._total_batches,
            "total_replies": self._total_replies,
            "total_no_action": self._total_no_action,
            "total_proactive": self._total_proactive,
            "planner_failures": self._planner_failures,
            "replyer_failures": self._replyer_failures,
        }


# ---------------------------------------------------------------------------
# 便捷工厂
# ---------------------------------------------------------------------------


def build_streamer_agent(
    *,
    config: StreamerAgentConfig,
    llm_manager: Any,
    prompt_manager: Any,
    agent_manager: AgentManager,
    context_service: Optional[Any] = None,
    event_bus: Optional[EventBus] = None,
    tool_registry: Optional[ToolRegistry] = None,
    capabilities_provider: Optional[Any] = None,
    sqlite_store: Optional[Any] = None,
    persona_provider: Optional[Any] = None,
    spec_provider: str = "builtin",
) -> StreamerAgent:
    """便捷工厂：构造 StreamerAgent + 注册到 AgentManager。

    Args:
        config: ``StreamerAgentConfig`` 实例
        其余参数同 ``StreamerAgent.__init__``
        agent_manager: ``AgentManager`` 实例（构造完后 register 到管理器）
        spec_provider: provider 来源溯源（"builtin"/"game"/"mcp"），默认 builtin

    Returns:
        构造好的 StreamerAgent（已 register 到 agent_manager）
    """
    agent = StreamerAgent(
        config=config,
        llm_manager=llm_manager,
        prompt_manager=prompt_manager,
        context_service=context_service,
        event_bus=event_bus,
        tool_registry=tool_registry,
        capabilities_provider=capabilities_provider,
        sqlite_store=sqlite_store,
        persona_provider=persona_provider,
    )
    agent_manager.register(agent, spec_provider=spec_provider)
    return agent
