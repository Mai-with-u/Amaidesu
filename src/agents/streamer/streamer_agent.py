"""StreamerAgent - 主播 Agent（BaseAgent 子类）

主播 Agent = Planner（决策核心）+ reply 工具（入口）+ Replyer（表达引擎），一体。
协议六项（最小契约）：
- 生命周期：start/stop/cleanup + 可重建性
- 工具提供：list_tools() → 暴露 reply（streamer_reply）；rundown_control 由 rundown 注册项声明
- 事件上报：emit（rundown.changed 等；订阅 room.message.danmaku 等）
- 状态读写：RoomState / RundownState 内部组件
- 健康：BaseAgent 心跳协议
- 元数据：name / description

接入方式（继承 + 构造注入）：
```python
agent = StreamerAgent(
    config=streamer_agent_config,
    llm_manager=llm,
    prompt_manager=prompt,
    event_bus=bus,
    tool_registry=registry,
    rundown_repo=store.rundowns,
    chat_repo=store.chat,
    sessions_repo=store.sessions,
    topic_repo=store.topics,
)
await agent.start()
# Agent now: subscribes room.message.danmaku → buffers → planner → reply tool → ...
await agent.cleanup()
```
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

from src.modules.agents.base import BaseAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.body import BodyEventPayload
from src.modules.logging import get_logger
from src.modules.tools import ToolSpec
from src.modules.tools.registry import ToolRegistry
from src.modules.time_utils import now_ms
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser

from .rundown.presentation import apply_rundown_control, build_rundown_view
from .rundown.rundown import DEFAULT_RUNDOWN, Rundown
from .rundown.rundown_state import RundownState
from .tools.rundown_tool import RundownControlProvider, build_rundown_tool_provider
from .background import BackgroundMaintainer
from .decision_executor import DecisionRoundExecutor
from .message_buffer import MessageBuffer
from .planner import Planner
from .proactive_trigger import ProactiveTrigger
from .replyer import WordFilter, Replyer
from .room_state import RoomState
from .speech_dispatcher import SpeechDispatcher
from .stats import StreamerStats
from .timing_gate import TimingGate
from .tools.reply_tool import ReplyToolProvider
from .command.router import CommandRouter
from .config import StreamerConfig

if TYPE_CHECKING:
    from src.modules.subtitle import SubtitleService
    from src.modules.tts import TTSProvider

__all__ = ["StreamerAgent", "StreamerConfig"]

# 游戏叙事摘要保留条数（近期叙事够用；进 Planner 上下文）
_MAX_GAME_NARRATIVE = 10
# 身体近况缓冲更小：它更新快、且只服务于"刚发生了什么"的叙述，不需要长记忆
_MAX_BODY_NARRATIVE = 5

# LLM profile 用途名（与 [llm_profiles.<name>] 三层结构对齐；model.toml 必填 6 成员）
_PROFILE_PLANNER = "planner"
_PROFILE_REPLYER = "replyer"


# ---------------------------------------------------------------------------
# StreamerAgent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LiveChatTurn:
    """live_chat 行的对话历史视图（鸭子类型：仅需 ``role`` / ``content`` 属性）。

    ``role`` 直接承载 live_chat 的 ``sender_role``（viewer / assistant），
    下游按字符串取值；昵称/类型/消息 ID 供 canonical 映射序列化
    （content 格式 = ``昵称: 内容 [id:…]``，批与历史同形）。
    """

    role: str
    content: str
    sender_name: str = ""
    message_type: str = "danmaku"
    message_id: str = ""


class StreamerAgent(BaseAgent):
    """主播 Agent：编排 Planner + Replyer + 工具 + 后台任务 + 流程单。

    实现协议六项：
    - 生命周期（start/stop/cleanup）
    - 工具提供：reply（streamer_reply，经 ToolRegistry 注册 + 名单 ["streamer"]）；
      rundown_control 由 rundown 注册项声明（provider="rundown"）。
      should_speak_proactively / parse_command 是代码直连的内部件，不是工具。
    - 事件上报：emit（rundown.changed / planner.decision 等）；订阅 room.message.*
    - 状态读写：内部 RoomState / RundownState / MessageBuffer
    - 健康：BaseAgent 心跳
    - 元数据：name / description
    """

    # -----元数据（必须覆写）-----
    name = "streamer"
    description = "Streamer Agent - 主播决策 + 表达 + 后台维护"

    # -----事件族声明（可选）-----
    emits_events = ("rundown.changed",)

    def __init__(
        self,
        config: StreamerConfig,
        *,
        llm_manager: Any,
        prompt_manager: Any,
        event_bus: Optional[EventBus] = None,
        tool_registry: Optional[ToolRegistry] = None,
        rundown_repo: Optional[Any] = None,
        chat_repo: Optional[Any] = None,
        sessions_repo: Optional[Any] = None,
        topic_repo: Optional[Any] = None,
        memory: Any = None,
        context_assembler_config: Optional[Any] = None,
        speech_config: Optional[Dict[str, Any]] = None,
        tts_engine: Optional["TTSProvider"] = None,
        subtitle_service: Optional["SubtitleService"] = None,
        session_manager: Optional[Any] = None,
        thinking_sink: Optional[Any] = None,
    ) -> None:
        """初始化主播 Agent。

        Args:
            config: ``StreamerConfig`` 实例
            llm_manager: ``LLMManager`` 实例
            prompt_manager: ``PromptManager`` 实例
            event_bus: 可选 ``EventBus``（Agent 通过它订阅 room.message.* / emit rundown.changed 等）
            tool_registry: 可选 ``ToolRegistry``（Agent 把自己的工具注册进去；
                VTS 表情工具仍走该 registry，TTS 不再走；
                Planner/Replyer 也从它读取 game 工具清单做动作选择）
            rundown_repo: 可选 ``RundownRepo``（流程单库；配置 rundown_id 时启动读取）
            chat_repo: 可选 ``ChatRepo``（live_chat 会话历史读取，reply tool 历史源）
            sessions_repo: 可选 ``SessionRepo``（live_sessions 实时状态，转交后台维护器）
            topic_repo: 可选 ``TopicRepo``（摘要/话题快照落地，转交后台维护器）
            context_assembler_config: 可选上下文组装器配置（[agents.streamer.context] 子段；
                控制 Planner 组装路径开关与长记忆召回条数；None 时 Planner 走内置默认）
            memory: 可选记忆后端（实现 ``MemoryProvider`` 协议，含
                ``recall(query, top_k)`` / ``ingest(text, source, tags)``）。
                传 ``None`` 时记忆相关功能整体降级——Planner 走无记忆路径，
                BackgroundMaintainer 跳过 ingest 写入。这是契约保证的"功能
                可关闭"而非"崩溃友好"。
            speech_config: 可选发言管线配置（来自核心 ``[tts]`` 段）。
                形态::

                    {
                        "enabled": bool,  # 是否启用 TTS 下游管线
                        "max_queue": int,  # 队列容量（默认 3）
                        "render_timeout_ms": int,  # 单 utterance 超时（默认 10000）
                    }

                ``None`` 或 ``enabled=False`` 时发言管线整体关闭（决策循环
                行为与改造前完全一致）。
            tts_engine: 可选 TTS 引擎 Provider 实例（须满足
                ``src.modules.tts.TTSProvider`` 结构契约，由
                ``src.modules.tts.build_tts_infrastructure`` 在装配期
                构造并通过 ``isinstance`` 校验后注入）。``None`` 或
                ``speech_config.enabled=False`` 时发言管线整体关闭。
                引擎实例直接持有，由编排队列调用其
                ``handle_speech(text, utterance_id)``，不再经 ``ToolRegistry``。
            subtitle_service: 可选字幕服务实例（须满足
                ``src.modules.subtitle.SubtitleService`` 结构契约），
                装配期由字幕基建工厂构造后注入；``None`` 时发言管线
                跳过字幕显示，与 TTS 关闭正交——字幕关闭不影响业务事件
                与 TTS 入队，仅关闭视觉字幕渲染通道。
            session_manager: 可选 ``LiveSessionManager``（场次唯一事实源）。
                后台心跳按其解析的当前场次主键写 live_sessions 实时状态；
                ``None`` 时心跳降级跳过（场次归属由管理器负责，Agent 不自建）。
            thinking_sink: 可选思考流旁路出口（``ThinkingStreamSink`` 结构契约，
                dashboard 侧 hub 实现）。``None`` 或配置关闭时思考流
                整体短路——决策循环行为与无旁路完全一致。
        """
        super().__init__(event_bus=event_bus)
        self.typed_config = config
        self._llm = llm_manager
        self._prompt = prompt_manager
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        # 仓储注入（组合根按需分发；None 时对应能力降级）
        self._rundowns = rundown_repo
        self._chat = chat_repo
        self._session_manager = session_manager
        # 思考流旁路出口（可选；观察面专用，不进 EventBus 不落库）
        self._thinking_sink = thinking_sink
        # 记忆后端（可选；None 时记忆相关功能整体降级）
        self._memory = memory
        self._logger = get_logger("StreamerAgent")

        # ===== 内部子组件 =====
        # 弹幕聚合缓冲 + 强制触发判定
        self._buffer = MessageBuffer(
            batch_window_ms=config.batch.batch_window_ms,
            batch_max_size=config.batch.batch_max_size,
            enable_idle_compensation=config.batch.enable_idle_compensation,
        )
        self._timing_gate = TimingGate(
            force_message_types=config.force.force_message_types,
        )

        # 房间态势（纯规则滑动窗口）
        self._room_state = RoomState()

        # Planner（决策核心，Agent 内部件——非工具）
        # 行为准则（behavior_style）只读包内配置权威 config.persona（决策侧通道）
        _behavior_style = config.persona.behavior_style or ""
        # context 组装器路径开关与召回条数——直接读包内权威
        _context_enabled = config.context.enabled
        _recall_top_k = config.context.memory_recall_long_term
        self._planner = Planner(
            config={
                "profile": _PROFILE_PLANNER,
                "planner_max_steps": config.planner_max_steps,
            },
            llm_service=llm_manager,
            prompt_service=prompt_manager,
            room_state=self._room_state,
            tool_registry=tool_registry,
            memory=memory,
            recall_top_k=int(_recall_top_k or 3),
            context_enabled=bool(_context_enabled),
            behavior_style=_behavior_style,
        )

        # 敏感词过滤器（输出净化；None 表示不启用）
        wf = config.word_filter
        self._word_filter = WordFilter(
            words=wf.words if wf.enabled else None,
            replacement=wf.replacement,
            case_sensitive=wf.case_sensitive,
            drop_on_match=wf.drop_on_match,
            enabled=wf.enabled,
        )

        # Replyer（表达引擎，Agent 内部件——非工具）
        # 人设四件套从包内配置权威 config.persona 单点构建、构造期注入
        # （behavior_style 不进表达侧——只走 Planner 决策通道）
        self._replyer = Replyer(
            config={
                "profile": _PROFILE_REPLYER,
                "bot_name": config.persona.bot_name,
                "personality": config.persona.personality,
                "style_constraints": config.persona.style_constraints,
                "audience_salutation": config.persona.audience_salutation,
            },
            llm_service=llm_manager,
            prompt_service=prompt_manager,
            tool_registry=tool_registry,
            word_filter=self._word_filter,
        )

        # 主动发言触发器（纯规则组件，Agent 内部件）
        proactive = config.proactive
        proactive_config = {
            "enabled": proactive.enabled,
            "cold_timeout_ms": proactive.cold_timeout_ms,
            "min_interval_ms": proactive.min_interval_ms,
            "schedule_interval_ms": proactive.schedule_interval_ms,
            "schedule_only_cold": proactive.schedule_only_cold,
            "max_per_hour": proactive.max_per_hour,
            "topic_required": proactive.topic_required,
            "rundown_speech_interval_ms": proactive.rundown_speech_interval_ms,
        }
        self._proactive_trigger = ProactiveTrigger(proactive_config)

        # 流程单（Rundown）子系统：备忘录 + 闹钟（推进权归 Agent）
        # Planner 构造早于 RundownState，开播时长锚点沿用 bind 注入（同 bind_reply_provider）
        self._rundown_state = RundownState(
            emit=self._emit_rundown_changed,
            on_changed=self._on_rundown_changed,
        )
        self._planner.bind_elapsed_live_provider(self._rundown_state.get_elapsed_live_ms)
        # 控制执行器：经 ToolRegistry 注册后供 Planner ReAct 循环调用（单实例单状态机）
        self._rundown_tool_provider = RundownControlProvider(self._rundown_state)

        # 后台维护器（双任务：轻循环 + 压缩 worker）——读包内权威配置
        bg = config.background
        background_config = {
            "enabled": bg.enabled,
            "light_tick_ms": bg.light_tick_ms,
            "cold_timeout_ms": bg.cold_timeout_ms,
            "summary_interval_ms": bg.summary_interval_ms,
            "compressor_concurrency": bg.compressor.concurrency,
            "compressor_queue_max": bg.compressor.queue_max,
        }
        self._background = BackgroundMaintainer(
            background_config,
            room_state=self._room_state,
            llm_service=llm_manager,
            sessions_repo=sessions_repo,  # live_sessions 实时状态落库（按 session_manager 解析的当前场次）
            chat_repo=chat_repo,  # 话题摘要读 live_chat 最近观众行
            session_manager=session_manager,  # 场次归属解析（None 时心跳降级跳过）
            memory=memory,  # 记忆写入面：摘要 → ingest；None 时降级
            event_bus=event_bus,  # 高价值事件（礼物/SC）→ ingest
            topic_repo=topic_repo,  # 摘要落地：timeline_summary + topics 快照
            prompt_manager=self._prompt,  # 摘要系统提示词渲染（复用 Agent 持有的 PromptManager）
        )

        # 后台 flush 循环（Agent 主循环）
        self._flush_task: Optional[asyncio.Task] = None
        self._flush_lock = asyncio.Lock()
        self._running = False

        # 场次进行位：live.started 置位 / live.ended 复位。主动发言的业务边界
        # = 场次——没开播只回弹幕不主动开题，开场白（rundown opening）等开播
        self._live_active: bool = False
        # 一次性 pending flag（外部 API 触发主动发言）
        self._external_proactive_pending: bool = False
        # 流程单环节切换触发 flag（RundownState 变更回调置位；装配接线）
        self._rundown_proactive_pending: bool = False
        # 游戏待定夺触发 flag（game.report 事件置位；下次空缓冲 tick 消费）
        self._game_decision_pending: bool = False

        # 统计（决策循环各分支增量；对外经 get_statistics 导出）
        self._stats = StreamerStats()

        # 游戏叙事摘要（订阅 game.* 收集，最多保留 N 条；进 Planner 上下文）
        self._game_narrative_blocks: List[str] = []
        self._body_narrative_blocks: List[str] = []

        # 观众命令接线（最小接线：玩法待扩展）。enabled + mappings 非空才激活；
        # mappings 即白名单，限频窗口/次数 config 化。命令路由是代码直连的
        # 内部件，不进 ToolRegistry。
        cmd_cfg = config.command
        self._command_router: Optional[CommandRouter] = None
        if cmd_cfg.enabled and cmd_cfg.mappings:
            self._command_router = CommandRouter(cmd_cfg, tool_registry, logger=self._logger)

        # 工具 Provider 实例（用于 invoke）
        self._reply_provider: Optional[ReplyToolProvider] = None

        # ===== 发言管线（speech → TTS / emotion → VTS）=====
        # 队列生命周期由 dispatcher 自持（start/stop）；未启用时决策循环
        # 只读 result.success，不消费 result.content。
        self._speech = SpeechDispatcher(
            event_bus=event_bus,
            subtitle_service=subtitle_service,
            tool_registry=tool_registry,
            tts_engine=tts_engine,
            speech_config=speech_config,
            logger=self._logger,
        )
        # 决策执行器（两阶段决策的执行半；调度半留 Agent）
        self._rounds = DecisionRoundExecutor(
            planner=self._planner,
            speech=self._speech,
            event_bus=event_bus,
            room_state=self._room_state,
            proactive_trigger=self._proactive_trigger,
            stats=self._stats,
            thinking_sink=thinking_sink,
            thinking_enabled=bool(getattr(config.thinking_stream, "enabled", True)),
            history_provider=self._read_history,
            rundown_text_provider=self._build_rundown_text,
            game_narrative_provider=self._game_narrative_text,
            body_narrative_provider=self._body_narrative_text,
            logger=self._logger,
        )

        self._logger.info(
            f"StreamerAgent 已构造 "
            f"(profile_planner={_PROFILE_PLANNER}, profile_replyer={_PROFILE_REPLYER}, "
            f"proactive_enabled={config.proactive.enabled}, "
            f"rundown_id={config.rundown_id!r}, "
            f"tts_enabled={self._speech.tts_enabled}, "
            f"tts_engine={'<已注入>' if tts_engine is not None else '<未注入>'})"
        )

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def _on_start(self) -> None:
        """Agent 启动钩子：订阅事件 + 启动后台任务 + 注册工具。"""
        self._running = True

        # 构造并（若有 registry）注册自己的工具 Provider
        self._register_tools()

        # 订阅 room.message.*（collectors emit 的语义域事件）
        if self._event_bus is not None:
            self._subscribe_events()

        # 启动后台 flush 循环
        self._flush_task = asyncio.create_task(self._flush_loop())

        # 启动后台双任务（轻循环 + 压缩 worker）
        if self.typed_config.background.enabled:
            await self._background.start()

        # 启动流程单（fail-soft：加载失败降级为无流程单）
        await self._start_rundown()

        # 启动发言管线（speech → TTS / emotion → VTS；队列生命周期归 dispatcher）
        await self._speech.start()

        self._logger.info("StreamerAgent 已启动")

    async def _on_stop(self) -> None:
        """Agent 停止钩子。"""
        self._running = False

        # 停止发言管线（TTS 队列先停，保证不遗留 invoke 在飞）
        await self._speech.stop()

        # 停止后台双任务
        try:
            await self._background.stop()
        except Exception as exc:
            self._logger.warning(f"停止 BackgroundMaintainer 失败: {exc}")

        # 停止 flush 循环
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        self._flush_task = None

        # 资源清理契约：摘除本 Agent 注册的 provider（reply / rundown）
        removed = self.unregister_tool_providers()

        self._logger.info(f"StreamerAgent 已停止（摘除 {removed} 个工具）")

    # ==================================================================
    # 工具提供（list_tools）
    # ==================================================================

    def list_tools(self) -> Iterable[ToolSpec]:
        """声明本 Agent 暴露的工具（审计对账口径，AgentManager.audit_tools）。

        只有真工具进声明：``streamer_reply``（注册 + 名单 ["streamer"]）。
        should_speak_proactively / parse_command 是代码直连的内部件，不是工具、
        不声明不注册（注册处生产侧声明判据）；rundown_control 由 rundown 注册项声明（provider
        ="rundown"，非本 Agent 名下）。

        前置启动窗口兜底：Provider 槽位在 ``__init__`` 里被置 None，直到
        ``_on_start → _register_tools`` 才实例化；此时回退到工厂函数构造
        spec（同一套定义，无事实源漂移）。
        """
        if self._reply_provider is not None:
            return list(self._reply_provider.list_tools())

        # 预启动窗口：Provider 尚未构造（Agent 已 register 但未 start）
        from .tools.reply_tool import build_reply_tool_spec

        return [build_reply_tool_spec()]

    def _register_tools(self) -> None:
        """构造 reply Provider；reply 与 rundown_control 注册进 ToolRegistry。

        名单口径（注册处声明）：
        - ``streamer_reply``：LLM 可调的真工具，注册 + 名单 ``["streamer"]``
          （自己的工具填自己）；Planner 经 registry 统一调用，thinking 回调
          槽位仍挂在本 Provider 实例上。
        - ``rundown_control``：注册（provider="rundown"，名单 ``["streamer"]``）
          进 ToolRegistry，与其他工具同一路径被 Planner 调用；可见性随
          Provider 注册/摘除进出名单（Agent 启动即注册，无需二次开关）。
        """

        # reply tool（无条件构造——thinking 槽位与注册共用同一实例）
        self._reply_provider = ReplyToolProvider(
            replyer=self._replyer,
            history_provider=(self._read_history_sync if self._chat is not None else None),
            rundown_text_provider=self._build_rundown_text_sync,
            event_bus=self._event_bus,
        )
        # Planner 的 reply 调用经 registry；绑定 Provider 仅为 thinking 回调槽位
        self._planner.bind_reply_provider(self._reply_provider)

        # proactive tool
        # 注册：reply 与 rundown_control（可见名单 ["streamer"]）；经基类入口
        # 登记归属，stop 路径逐一摘除（disable/重建不留残留工具）
        if self._tool_registry is not None:
            self.register_tool_provider(
                self._reply_provider, registry=self._tool_registry, visible_to={"streamer_reply": ["streamer"]}
            )
            self.register_tool_provider(
                build_rundown_tool_provider(self._rundown_tool_provider),
                registry=self._tool_registry,
                visible_to={"rundown_control": ["streamer"]},
            )
            self._logger.info("StreamerAgent 工具已注册：streamer_reply / rundown_control（名单 [streamer]）")

    # ==================================================================
    # 事件订阅
    # ==================================================================

    def _subscribe_events(self) -> None:
        """订阅 room.message.* + game.* + perception.* 事件（语义域事件）。"""
        if self._event_bus is None:
            return
        # room.message 全族（danmaku/gift/super_chat/guard）共用同一回调：
        # 按载荷自身 message_type 走 handle_message 统一入口，付费类型由
        # TimingGate 强制判定（类型驱动，非 importance 数值）
        for event_name in (
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            CoreEvents.ROOM_MESSAGE_GIFT,
            CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
            CoreEvents.ROOM_MESSAGE_GUARD,
        ):
            self._event_bus.on(
                event_name,
                self._on_room_message_received,
                model_class=RoomMessagePayload,
            )
        # 游戏叙事（三通道·事件）：游戏 Agent（如 MinecraftAgent）emit game.*
        # → 主播侧收集最近叙事，进 Planner 上下文（按 payload.game 过滤可扩展到多游戏）
        self._event_bus.on(
            CoreEvents.GAME_MILESTONE,
            self._on_game_event,
            model_class=GamePayload,
        )
        self._event_bus.on(
            CoreEvents.GAME_ATTENTION_REQUIRED,
            self._on_game_event,
            model_class=GamePayload,
        )
        # 游戏异常也进叙事（如"无法执行目标：LLM 未注入"）——否则主播不知命令失败
        self._event_bus.on(
            CoreEvents.GAME_ERROR,
            self._on_game_event,
            model_class=GamePayload,
        )
        # 游戏主动上报（交付总结/升级决策）——主播叙事与"是否回提示词"的决策数据源
        self._event_bus.on(
            CoreEvents.GAME_REPORT,
            self._on_game_event,
            model_class=GamePayload,
        )
        # 身体侧近况（AI 玩家遭遇：被袭击/死亡/重生/紧急反应）——采集器分类后的事件，
        # 与上面的 game.* 叙事分两条线；通配订阅覆盖 8 类，按 payload.kind 便于扩展
        self._event_bus.on(
            CoreEvents.GAME_BODY_WILDCARD,
            self._on_body_event,
            model_class=BodyEventPayload,
        )
        # 场次边界事件：开播放行主动发言，下播收闸（开场白属于场次，不属于进程）
        self._event_bus.on(
            CoreEvents.LIVE_STARTED,
            self._on_live_started,
            model_class=LiveStartedPayload,
        )
        self._event_bus.on(
            CoreEvents.LIVE_ENDED,
            self._on_live_ended,
            model_class=LiveEndedPayload,
        )
        self._logger.info(
            "StreamerAgent 已订阅 room.message.danmaku|gift|super_chat|guard / game.* / live.started|ended"
        )

    async def _on_live_started(
        self,
        event_name: str,
        payload: LiveStartedPayload,
        source: str,
    ) -> None:
        """live.started 回调：开播，放行主动发言。"""
        del event_name, source
        self._live_active = True
        self._logger.info(f"场次已开启（id={payload.live_session_id}）：主动发言放行")

    async def _on_live_ended(
        self,
        event_name: str,
        payload: LiveEndedPayload,
        source: str,
    ) -> None:
        """live.ended 回调：下播，主动发言收闸。"""
        del event_name, source
        self._live_active = False
        self._logger.info("场次已结束：主动发言收闸")

    async def _on_game_event(
        self,
        event_name: str,
        payload: GamePayload,
        source: str,
    ) -> None:
        """game.* 事件回调：收集最近游戏叙事（保留 N 条，进 Planner 上下文）。

        叙事行带事件类型标记（``[game·event_type]``），Planner 据此区分
        "剧情推进"（milestone）与"需要定夺"（report）；message 本体不变。
        ``report`` 类型（交付总结/升级决策）同时置位待定夺触发信号——游戏
        停在选项处等主播定夺，下次空缓冲 tick 应促发一轮主动决策。
        """
        try:
            line = f"[{payload.game}·{payload.event_type}] {payload.message}"
            self._game_narrative_blocks.append(line)
            if len(self._game_narrative_blocks) > _MAX_GAME_NARRATIVE:
                self._game_narrative_blocks = self._game_narrative_blocks[-_MAX_GAME_NARRATIVE:]
            if payload.event_type == "report":
                self._game_decision_pending = True
        except Exception as exc:  # noqa: BLE001 - 收集失败不阻断
            self._logger.warning(f"收集游戏叙事失败: {exc}")

    def _game_narrative_text(self) -> str:
        """导出最近游戏叙事摘要文本（Planner 上下文用）。"""
        return "\n".join(self._game_narrative_blocks)

    async def _on_body_event(
        self,
        event_name: str,
        payload: BodyEventPayload,
        source: str,
    ) -> None:
        """``game.body.*`` 回调：收集 AI 玩家身体侧近况（独立小缓冲，进 Planner 上下文）。

        与游戏叙事分两条线：``game.*`` 是低频进展/上报（含任务上下文），
        ``game.body.*`` 是身体侧的遭遇（被袭击/死亡/重生/紧急反应）。
        两者形状不同、更新频率不同，混在一条缓冲里会让高频的遭遇把进展挤掉，
        所以各留各的；同一件事若两条线都报（任务中遭遇攻击），由提示词规定
        只讲一次、以带任务上下文的那条为准。
        """
        try:
            marker = "（已结束）" if getattr(payload, "resolved", False) else ""
            line = f"[{payload.game}·{payload.kind}] {payload.summary}{marker}"
            self._body_narrative_blocks.append(line)
            if len(self._body_narrative_blocks) > _MAX_BODY_NARRATIVE:
                self._body_narrative_blocks = self._body_narrative_blocks[-_MAX_BODY_NARRATIVE:]
        except Exception as exc:  # noqa: BLE001 - 收集失败不阻断
            self._logger.warning(f"收集身体近况失败: {exc}")

    def _body_narrative_text(self) -> str:
        """导出最近身体侧近况文本（Planner 上下文用）。"""
        return "\n".join(self._body_narrative_blocks)

    async def _on_room_message_received(
        self,
        event_name: str,
        payload: RoomMessagePayload,
        source: str,
    ) -> None:
        """room.message.* 回调：推进 RoomState + 入缓冲（载荷即事件载荷，零映射）。

        danmaku / gift / super_chat / guard 全部进入决策链；强制响应与否由
        handle_message 内的 TimingGate 按 message_type 判定，本回调不做过滤。
        """
        del event_name, source
        await self.handle_message(payload)

    async def handle_message(self, msg: RoomMessagePayload) -> None:
        """处理一条弹幕（collectors → Agent 入口；测试也可直接调）。"""
        # 命令分支（仅弹幕类型）：命中白名单 → 委派后短路，不进决策链；
        # 非命令按原路径继续（行为不变）
        if (
            msg.message_type == "danmaku"
            and self._command_router is not None
            and await self._command_router.try_dispatch(msg)
        ):
            return
        self._stats.total_messages += 1
        # RoomState 热度信号
        self._room_state.update(msg, now_ms=now_ms())
        # TimingGate 强制判定
        forced = self._timing_gate.is_forced(msg)
        # 入缓冲
        self._buffer.add(msg, arrival_ms=now_ms(), forced=forced)

    def trigger_external_proactive(self, topic_hint: Optional[str] = None) -> None:
        """外部 API 触发主动发言（Dashboard / API 调用）。"""
        self._external_proactive_pending = True
        if topic_hint:
            self._logger.info(f"外部主动发言触发: {topic_hint}")

    async def debug_test_decision(
        self,
        *,
        batch: Optional[List[Dict[str, str]]] = None,
        forced: bool = False,
        proactive: bool = False,
    ) -> Dict[str, Any]:
        """调试门面：手动驱动一次完整两阶段决策并回传中间产物（Dashboard 专用）。

        与真实链路的差异（有意为之，均为测试诉求）：
        - **绕过 MessageBuffer 聚合窗口**：弹幕批次直接进入决策，不等 3s 聚合；
        - **proactive=True 时绕过 ProactiveTrigger 四道限流**（proactive_enabled /
          防接龙间隔 / 每小时上限 / 话题要求）——测试"主播主动开口"不受配置卡死；
          测限流本身请走 ``trigger_external_proactive``（真实链路）；
        - ``forced=True`` 时 Planner 的低置信度降级豁免（与 SC/礼物强制响应同语义）。

        与真实链路的一致性（核心承诺）：Planner / Replyer / 发言管线 /
        RoomState / 限流记账全部走同一份代码——本方法不复制任何决策编排逻辑，
        仅持有 ``_flush_lock`` 防止与后台 flush 循环并发决策。

        Args:
            batch: 模拟弹幕列表，元素 ``{"nickname": str, "text": str}``；
                ``proactive=True`` 时必须为空/None（主动发言无弹幕批次）。
            forced: 是否强制响应（透传 Planner 的 ``$forced``）。
            proactive: 是否主动发言决策（透传 Planner 的 ``$proactive``）。

        Returns:
            ``DecisionRoundExecutor.execute`` 的决策结果视图 + ``success`` /
            ``elapsed_ms`` 字段；入参校验失败时返回 ``success=False`` + ``error``。
        """
        if proactive and batch:
            return {
                "success": False,
                "error": "proactive 模式不接受弹幕批次（主动发言由房间状态驱动，batch 置空）",
            }
        if not proactive and not (batch and any(str(item.get("text", "")).strip() for item in batch)):
            return {"success": False, "error": "弹幕批次为空：请至少提供一条 text 非空的弹幕"}

        messages: List[RoomMessagePayload] = []
        for item in batch or []:
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            nickname = str(item.get("nickname", "")).strip() or "测试观众"
            messages.append(
                RoomMessagePayload(
                    message_type="danmaku",
                    user=RoomMessageUser(id=f"debug_{nickname}", name=nickname),
                    content=text,
                    timestamp_ms=now_ms(),
                )
            )

        started_ms = now_ms()
        trigger_reason = "proactive:dashboard_debug" if proactive else "dashboard:debug_test"
        try:
            async with self._flush_lock:
                result = await self._rounds.execute(
                    messages,
                    forced=forced,
                    trigger_reason=trigger_reason,
                    proactive=proactive,
                )
        except Exception as exc:
            self._logger.error(f"调试决策执行异常: {exc}", exc_info=True)
            return {"success": False, "error": f"debug_test_decision 执行异常: {exc}"}

        result["success"] = True
        result["elapsed_ms"] = now_ms() - started_ms
        return result

    # ==================================================================
    # 后台 flush 循环（Agent 主循环）
    # ==================================================================

    async def _flush_loop(self) -> None:
        """后台循环：周期性检查缓冲并触发批次决策。"""
        interval = max(self.typed_config.batch.tick_interval_ms / 1000.0, 0.05)
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
        """判断是否应该取出一批并做两阶段决策。"""
        # 下方 ``locked()`` + ``async with`` 是 check-then-act：单事件循环下
        # ``locked()`` 与 ``async with`` 之间存在 TOCTOU 窗口，仅作尽力快路径。
        # 真正的互斥闸门是紧随其后的 ``async with self._flush_lock``。
        # 当前唯一调用方是 ``_flush_loop``（单点、串行 tick），故此快路径无害；
        # 若新增并发调用方需重新评估。
        if self._flush_lock.locked():
            return

        async with self._flush_lock:
            # buffer 空时 → 主动发言判定（场次边界闸：未开播直接返回且不消费
            # pending 信号，环节变更/游戏待定夺信号保留到开播后首个 tick 生效）
            if self._buffer.is_empty:
                if not self._live_active:
                    return
                rundown_pending = self._rundown_proactive_pending
                self._rundown_proactive_pending = False
                game_pending = self._game_decision_pending
                self._game_decision_pending = False
                reason = self._proactive_trigger.should_trigger(
                    self._room_state,
                    now_ms(),
                    external_pending=self._external_proactive_pending,
                    rundown_pending=rundown_pending,
                    rundown_ready=self._is_rundown_active(),
                    rundown_overdue=self._is_rundown_overdue(),
                    game_pending=game_pending,
                )
                self._external_proactive_pending = False
                if reason is not None:
                    self._stats.total_proactive += 1
                    self._logger.info(f"主动发言触发: {reason}")
                    await self._rounds.execute(
                        [],
                        forced=False,
                        trigger_reason=f"proactive:{reason}",
                        proactive=True,
                    )
                return

            # 弹幕聚合 → 两阶段决策
            now = now_ms()
            avg_interval_ms = self._estimate_avg_interval_ms()
            flush_due, flush_reason = self._buffer.should_flush(now, avg_interval_ms=avg_interval_ms)
            if not flush_due:
                return

            forced = self._buffer.force
            batch = self._buffer.drain()
            if not batch:
                return
            self._stats.total_batches += 1

            await self._rounds.execute(batch, forced=forced, trigger_reason=flush_reason)

    def _estimate_avg_interval_ms(self) -> Optional[float]:
        """估算缓冲内消息平均间隔（供 idle 补偿公式使用）。"""
        buf = self._buffer
        if buf.size < 2:
            return None
        span = buf.last_arrival_ms - buf.first_arrival_ms
        if span <= 0:
            return None
        return span / (buf.size - 1)

    # ==================================================================
    # 流程单（Rundown）运行时
    # ==================================================================

    def _is_rundown_active(self) -> bool:
        """判定流程单是否激活且有当前环节（供 ProactiveTrigger rundown_ready）。"""
        return self._rundown_state.status == "running"

    def _is_rundown_overdue(self) -> bool:
        """当前环节停留是否已超预期（供 ProactiveTrigger rundown_overdue 闹钟）。"""
        return self._rundown_state.is_current_segment_overdue()

    def _build_rundown_text(self) -> Optional[str]:
        """拼装流程单情境文本（注入 Planner/Replyer $rundown 变量）。"""
        return self._rundown_state.build_context_text()

    def _build_rundown_text_sync(self) -> Optional[str]:
        """同步包装（reply_tool 的 rundown_text_provider 鸭子接口）。"""
        return self._build_rundown_text()

    async def _start_rundown(self) -> None:
        """加载流程单并启动运行时（唯一装配点；fail-soft）。

        加载顺序：配置 ``rundown_id`` → 存储读取；未配置 / 不存在 / 读取失败
        → 回退内置默认流程单（初次直播·自我介绍）。
        """
        rundown_id = (self.typed_config.rundown_id or "").strip()
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
        self._rundown_state.load(rundown)
        self._logger.info(f"流程单已加载: id={rundown.rundown_id!r}, segments={len(rundown.segments)}")

    def _on_rundown_changed(self, segment_id: str, by: str) -> None:
        """流程单变更通知：置位 proactive 即时信号（新环节开场要马上说）。"""
        self._rundown_proactive_pending = True
        self._logger.info(f"流程单变更: segment={segment_id!r}, by={by!r}")

    def _emit_rundown_changed(self, event_name: str, payload: Any) -> None:
        """桥接 RundownState 的事件回调到 EventBus（异步 emit 经任务调度）。"""
        if self._event_bus is None:
            return
        try:
            asyncio.create_task(self._event_bus.emit(event_name, payload, source="RundownState"))
        except RuntimeError as exc:
            self._logger.warning(f"rundown.changed 任务创建失败（已忽略）: {exc}")

    # ==================================================================
    # 公开门面：主动发言动态开关（Dashboard 直播控制台）
    # ==================================================================

    def is_proactive_enabled(self) -> bool:
        """主动发言总开关当前状态（运行时实际值，供动态开关展示）。"""
        return self._proactive_trigger.is_enabled()

    def set_proactive_enabled(self, enabled: bool) -> None:
        """切换主动发言总开关（运行时立即生效；落盘由调用方负责）。"""
        self._proactive_trigger.set_enabled(enabled)

    # ==================================================================
    # 公开门面：供 Dashboard API 读取与控制流程单
    # ==================================================================

    def is_rundown_available(self) -> bool:
        """判定流程单是否对外可用（运行时已加载流程单）。

        - 返回 ``False`` 表示 Dashboard 编排页应降级为不可用视图。
        """
        return self._rundown_state.rundown is not None

    def get_rundown_view(self, *, now_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """聚合 Dashboard 编排页所需的流程单视图数据（视图拼装见 rundown 子包）。"""
        return build_rundown_view(self._rundown_state, now_ms=now_ms)

    async def rundown_control(
        self,
        action: str,
        *,
        segment_id: Optional[str] = None,
        now_ms: Optional[int] = None,
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """执行 Dashboard 手动控制动作（动作翻译见 rundown 子包）。"""
        return apply_rundown_control(self._rundown_state, action, segment_id=segment_id, now_ms=now_ms)

    async def apply_rundown_definition(
        self,
        definition: Dict[str, Any],
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
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

        current = self._rundown_state.rundown
        if current is None:
            return True, "流程单已保存（运行时未加载流程单，启动后生效）", None
        if current.rundown_id != rundown.rundown_id:
            return True, "流程单已保存（当前直播运行的是其他流程单，不受影响）", self._rundown_state.get_snapshot()

        reject = self._rundown_state.replace_definition(rundown)
        if reject is not None:
            return False, f"运行态更新被拒绝: {reject.reason}", self._rundown_state.get_snapshot()
        return True, "流程单已保存并即时生效", self._rundown_state.get_snapshot()

    # ==================================================================
    # 历史读取（duck-typed 鸭子接口）
    # ==================================================================

    def _read_history_sync(self):
        """reply_tool.history_provider 鸭子接口（返回 awaitable）。

        实际实现是返回 coroutine（不是同步 list），由 ReplyToolProvider 检测 awaitable 并 await。
        """
        return self._read_history()

    async def _read_history(self) -> Optional[List[Any]]:
        """读当前场次最近对话历史（live_chat 单一事实源）。

        场次主键经 ``LiveSessionManager.resolve_pk()`` 解析——与写路径
        （StorageLedger 落库）同源；无显式场次（首场/未开播）或存储缺失时
        返回空列表，不抛错（首场首决定窗的空读是常态而非异常）。
        """
        if self._chat is None or self._session_manager is None:
            return None
        try:
            live_pk = await self._session_manager.resolve_pk()
            if live_pk is None:
                return []
            rows = await self._chat.list_recent_live_chat(
                live_session_id=live_pk,
                limit=self.typed_config.history_limit,
            )
        except Exception as exc:
            self._logger.warning(f"读取会话历史失败: {exc}")
            return None
        return [
            _LiveChatTurn(
                role=row["sender_role"],
                content=row["content"],
                sender_name=row["sender_name"] or "",
                message_type=row["message_type"],
                message_id=row["message_id"] or "",
            )
            for row in rows
        ]

    # ==================================================================
    # 统计信息
    # ==================================================================

    def get_statistics(self) -> Dict[str, Any]:
        """获取运行时统计信息（结构向后兼容旧 get_statistics）。"""
        return self._stats.as_dict()
