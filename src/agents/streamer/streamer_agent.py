"""StreamerAgent - 主播 Agent（BaseAgent 子类）

主播 Agent = Planner（决策核心）+ reply 工具（入口）+ Replyer（表达引擎），一体。
协议六项（最小契约）：
- 生命周期：start/stop/cleanup + 可重建性
- 工具提供：list_tools() → 暴露 reply / should_speak_proactively / parse_command
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
    context_service=context,
    event_bus=bus,
    tool_registry=registry,
    sqlite_store=store,
)
await agent.start()
# Agent now: subscribes room.message.danmaku → buffers → planner → reply tool → ...
await agent.cleanup()
```
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

from src.modules.agents.base import BaseAgent
from src.modules.agents.manager import AgentManager
from src.modules.context.models import MessageRole
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.planner import (
    PlannerBatchItem,
    PlannerDecisionPayload,
    StreamerStagePayload,
)
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.logging import get_logger
from src.modules.tools import ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry
from src.modules.time_utils import now_ms
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.message_type import require_message_type

from .rundown.rundown import DEFAULT_RUNDOWN, Rundown
from .rundown.rundown_state import RundownState
from .tools.rundown_tool import RundownControlProvider, build_rundown_tool_provider
from .background import BackgroundMaintainer
from .message_buffer import MessageBuffer
from .planner import Planner
from .proactive_trigger import ProactiveTrigger
from .replyer import WordFilter, Replyer
from .room_state import RoomState
from .thinking_stream import ThinkingStreamContext
from .timing_gate import TimingGate
from .tools.reply_tool import ReplyToolProvider
from .utterance_queue import (
    DEFAULT_MAX_QUEUE,
    DEFAULT_RENDER_TIMEOUT_MS,
    UtteranceQueue,
)
from .config import StreamerConfig

if TYPE_CHECKING:
    from src.modules.subtitle import SubtitleService
    from src.modules.tts import TTSProvider

__all__ = ["StreamerAgent", "StreamerConfig", "build_streamer_agent"]

# 游戏叙事摘要保留条数（近期叙事够用；进 Planner 上下文）
_MAX_GAME_NARRATIVE = 10

# LLM profile 用途名（与 [llm_profiles.<name>] 三层结构对齐；model.toml 必填 6 成员）
_PROFILE_PLANNER = "planner"
_PROFILE_REPLYER = "replyer"
_PROFILE_SUMMARY = "summary"


# ---------------------------------------------------------------------------
# StreamerAgent
# ---------------------------------------------------------------------------


class StreamerAgent(BaseAgent):
    """主播 Agent：编排 Planner + Replyer + 工具 + 后台任务 + 流程单。

    实现协议六项：
    - 生命周期（start/stop/cleanup）
    - 工具提供：reply / should_speak_proactively / parse_command（3 个工具）
      + rundown_control（Planner 局部协议工具）
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
        context_service: Optional[Any] = None,
        event_bus: Optional[EventBus] = None,
        tool_registry: Optional[ToolRegistry] = None,
        sqlite_store: Optional[Any] = None,
        persona_provider: Optional[Any] = None,
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
            context_service: 可选 ``ContextService``（持久化对话历史）
            event_bus: 可选 ``EventBus``（Agent 通过它订阅 room.message.* / emit rundown.changed 等）
            tool_registry: 可选 ``ToolRegistry``（Agent 把自己的工具注册进去；
                VTS 表情工具仍走该 registry，TTS 不再走；
                Planner/Replyer 也从它读取 game 工具清单做动作选择）
            sqlite_store: 可选 ``SQLiteStore``（live_sessions 状态 + rundowns 流程单库）
            persona_provider: 可选人设字典来源（鸭子类型：callable 返回 dict / dict 本身）
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
        self._context = context_service
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._sqlite = sqlite_store
        self._session_manager = session_manager
        self._persona_provider = persona_provider
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
            force_data_types=config.force.force_data_types,
            force_importance=config.force.force_importance,
        )

        # 房间态势（纯规则滑动窗口）
        self._room_state = RoomState()

        # Planner（决策核心，Agent 内部件——非工具）
        # 行为准则（behavior_style）优先级：包内 persona 权威 > persona_provider（dict）传入。
        # 包内 config.persona 是配置权威；persona_provider 仍保留以兼容外部注入
        # （如测试），存在时其 behavior_style 覆盖 config.persona.behavior_style。
        _behavior_style = config.persona.behavior_style or ""
        if self._persona_provider is not None:
            try:
                _persona_dict = self._persona_provider() if callable(self._persona_provider) else self._persona_provider
                if isinstance(_persona_dict, dict):
                    _provider_bs = str(_persona_dict.get("behavior_style") or "")
                    if _provider_bs:
                        _behavior_style = _provider_bs
            except Exception as exc:
                self._logger.warning(f"Planner 读取 persona_provider.behavior_style 失败: {exc}")
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
        # audience_salutation 默认"大家"——旧 user_name 默认值
        self._replyer = Replyer(
            config={
                "profile": _PROFILE_REPLYER,
                "enable_action_selection": config.enable_action_selection,
                "bot_name": config.persona.bot_name,
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
        # 控制执行器单实例：Planner 直连与 ToolRegistry 注册共用同一状态机
        self._rundown_tool_provider = RundownControlProvider(self._rundown_state)
        self._planner.bind_rundown_provider(self._rundown_tool_provider)

        # 后台维护器（双任务：轻循环 + 压缩 worker）——读包内权威配置
        bg = config.background
        background_config = {
            "enabled": bg.enabled,
            "light_tick_ms": bg.light_tick_ms,
            "cold_timeout_ms": bg.cold_timeout_ms,
            "summary_interval_ms": bg.summary_interval_ms,
            "summary_client": _PROFILE_SUMMARY,
            "window_event_threshold": bg.window_event_threshold,
            "compressor_concurrency": bg.compressor.concurrency,
            "compressor_queue_max": bg.compressor.queue_max,
        }
        self._background = BackgroundMaintainer(
            background_config,
            room_state=self._room_state,
            llm_service=llm_manager,
            live_session_store=sqlite_store,  # live_sessions 实时状态落库（按 session_manager 解析的当前场次）
            session_manager=session_manager,  # 场次归属解析（None 时心跳降级跳过）
            context_service=context_service,
            memory=memory,  # 记忆写入面：摘要 → ingest；None 时降级
            event_bus=event_bus,  # 高价值事件（礼物/SC）→ ingest
            sqlite_store=sqlite_store,  # 摘要落地：timeline_summary + topics 快照
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

        # 统计
        self._total_messages = 0
        self._total_batches = 0
        self._total_replies = 0
        self._total_no_action = 0
        self._total_proactive = 0
        self._planner_failures = 0
        self._replyer_failures = 0

        # 游戏叙事摘要（订阅 game.* 收集，最多保留 N 条；进 Planner 上下文）
        self._game_narrative_blocks: List[str] = []

        # 工具 Provider 实例（用于 invoke）
        self._reply_provider: Optional[ReplyToolProvider] = None

        # ===== 发言管线（speech → TTS / emotion → VTS）=====
        # 仅当显式启用且 tts_engine 注入时才构造队列；否则决策循环行为
        # 与改造前一致（只读 result.success，不消费 result.content）。
        speech_cfg = speech_config or {}
        self._tts_enabled: bool = bool(speech_cfg.get("enabled", False))
        self._speech_max_queue: int = int(speech_cfg.get("max_queue", DEFAULT_MAX_QUEUE))
        self._speech_render_timeout_ms: int = int(speech_cfg.get("render_timeout_ms", DEFAULT_RENDER_TIMEOUT_MS))
        self._tts_engine = tts_engine
        self._subtitle_service = subtitle_service
        self._utterance_queue: Optional[UtteranceQueue] = None
        # utterance_id 自增计数器（进程内单调；启动时复位为 0，首次自增到 1）
        self._utterance_seq: int = 0
        # 决策轮次自增计数器（round_id 生成用；与 utterance_seq 同风格）
        self._round_seq: int = 0

        self._logger.info(
            f"StreamerAgent 已构造 "
            f"(profile_planner={_PROFILE_PLANNER}, profile_replyer={_PROFILE_REPLYER}, "
            f"proactive_enabled={config.proactive.enabled}, "
            f"rundown_id={config.rundown_id!r}, "
            f"tts_enabled={self._tts_enabled}, "
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

        # 场次初始同步：订阅前场次可能已开启（模拟器回放 auto_start 先于 Agent
        # 订阅），以 session_manager 当前状态为准，不依赖事件是否错过
        if self._session_manager is not None and self._session_manager.active_pk is not None:
            self._live_active = True
            self._logger.info(f"启动时场次已在进行（id={self._session_manager.active_pk}）：主动发言放行")

        # 启动后台 flush 循环
        self._flush_task = asyncio.create_task(self._flush_loop())

        # 启动后台双任务（轻循环 + 压缩 worker）
        if self.typed_config.background.enabled:
            await self._background.start()

        # 启动流程单（fail-soft：加载失败降级为无流程单）
        await self._start_rundown()

        # 启动发言管线（speech → TTS / emotion → VTS）
        # 仅在 TTS 显式启用且 tts_engine 注入时构造；否则保持禁用（决策循环
        # 行为与改造前一致：只读 result.success，不消费 result.content）。
        if self._tts_enabled:
            if self._tts_engine is None:
                self._logger.warning("TTS 已启用但未注入 tts_engine，发言管线降级为关闭")
                self._tts_enabled = False
            else:
                try:
                    engine = self._tts_engine

                    async def _speak(text: str, utterance_id: Optional[str] = None) -> None:
                        """编排队列 → TTS 引擎的 speak 适配器。"""
                        await engine.handle_speech(text, utterance_id=utterance_id)

                    self._utterance_queue = UtteranceQueue(
                        speak=_speak,
                        logger_name="StreamerAgent.UtteranceQueue",
                        max_queue=self._speech_max_queue,
                        render_timeout_ms=self._speech_render_timeout_ms,
                    )
                    await self._utterance_queue.start()
                except Exception as exc:
                    self._logger.warning(f"启动发言管线失败，已降级为关闭: {exc}")
                    self._utterance_queue = None
                    self._tts_enabled = False

        self._logger.info("StreamerAgent 已启动")

    async def _on_stop(self) -> None:
        """Agent 停止钩子。"""
        self._running = False

        # 停止发言管线（TTS 队列先停，保证不遗留 invoke 在飞）
        if self._utterance_queue is not None:
            try:
                await self._utterance_queue.stop()
            except Exception as exc:
                self._logger.warning(f"停止发言管线失败: {exc}")
            self._utterance_queue = None

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

        self._logger.info("StreamerAgent 已停止")

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
          进 ToolRegistry 获得观测/管理条目；决策面仍由 Planner 按流程单激活
          状态条件追加（动态工具的已知例外）。
        """

        # reply tool（无条件构造——thinking 槽位与注册共用同一实例）
        self._reply_provider = ReplyToolProvider(
            replyer=self._replyer,
            persona=self._persona_provider or {},
            history_provider=(self._read_history_sync if self._context is not None else None),
            rundown_text_provider=self._build_rundown_text_sync,
            event_bus=self._event_bus,
        )
        # Planner 的 reply 调用经 registry；绑定 Provider 仅为 thinking 回调槽位
        self._planner.bind_reply_provider(self._reply_provider)

        # proactive tool
        # 注册：reply 与 rundown_control（可见名单 ["streamer"]）
        if self._tool_registry is not None:
            self._tool_registry.register_provider(self._reply_provider, visible_to={"streamer_reply": ["streamer"]})
            self._tool_registry.register_provider(
                build_rundown_tool_provider(self._rundown_tool_provider),
                visible_to={"rundown_control": ["streamer"]},
            )
            self._logger.info("StreamerAgent 工具已注册：streamer_reply / rundown_control（名单 [streamer]）")

    # ==================================================================
    # 事件订阅
    # ==================================================================

    def _subscribe_events(self) -> None:
        """订阅 room.message.* + game.* 事件（collectors emit 的语义域事件）。"""
        if self._event_bus is None:
            return
        self._event_bus.on(
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            self._on_danmaku_received,
            model_class=RoomMessagePayload,
            priority=50,
        )
        # 游戏叙事（三通道·事件）：游戏 Agent（如 MinecraftAgent）emit game.*
        # → 主播侧收集最近叙事，进 Planner 上下文（按 payload.game 过滤可扩展到多游戏）
        self._event_bus.on(
            CoreEvents.GAME_MILESTONE,
            self._on_game_event,
            model_class=GamePayload,
            priority=40,
        )
        self._event_bus.on(
            CoreEvents.GAME_ATTENTION_REQUIRED,
            self._on_game_event,
            model_class=GamePayload,
            priority=40,
        )
        # 游戏异常也进叙事（如"无法执行目标：LLM 未注入"）——否则主播不知命令失败
        self._event_bus.on(
            CoreEvents.GAME_ERROR,
            self._on_game_event,
            model_class=GamePayload,
            priority=40,
        )
        # 游戏主动上报（交付总结/升级决策）——主播叙事与"是否回提示词"的决策数据源
        self._event_bus.on(
            CoreEvents.GAME_REPORT,
            self._on_game_event,
            model_class=GamePayload,
            priority=40,
        )
        # 场次边界事件：开播放行主动发言，下播收闸（开场白属于场次，不属于进程）
        self._event_bus.on(
            CoreEvents.LIVE_STARTED,
            self._on_live_started,
            model_class=LiveStartedPayload,
            priority=60,
        )
        self._event_bus.on(
            CoreEvents.LIVE_ENDED,
            self._on_live_ended,
            model_class=LiveEndedPayload,
            priority=60,
        )
        self._logger.info("StreamerAgent 已订阅 room.message.danmaku / game.* / live.started|ended")

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
        """game.* 事件回调：收集最近游戏叙事（保留 N 条，进 Planner 上下文）。"""
        try:
            line = f"[{payload.game}] {payload.message}"
            self._game_narrative_blocks.append(line)
            if len(self._game_narrative_blocks) > _MAX_GAME_NARRATIVE:
                self._game_narrative_blocks = self._game_narrative_blocks[-_MAX_GAME_NARRATIVE:]
        except Exception as exc:  # noqa: BLE001 - 收集失败不阻断
            self._logger.warning(f"收集游戏叙事失败: {exc}")

    def _game_narrative_text(self) -> str:
        """导出最近游戏叙事摘要文本（Planner 上下文用）。"""
        return "\n".join(self._game_narrative_blocks)

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
            # message_id 透传 payload 值（采集器/模拟器生成，落库与回复关联同键）；
            # 缺省时 NormalizedMessage 自动生成 UUID，保证批次内 [id:] 编号可用
            msg_kwargs: Dict[str, Any] = dict(
                text=payload.content,
                source=source or "room.message.danmaku",
                data_type="text",
                importance=0.5,
                timestamp=payload.timestamp_ms,
                user_id=payload.user.id,
                user_nickname=payload.user.name,
            )
            if payload.message_id:
                msg_kwargs["message_id"] = payload.message_id
            msg = NormalizedMessage(**msg_kwargs)
            require_message_type(msg.data_type)
        except Exception as exc:
            self._logger.warning(f"弹幕事件转 NormalizedMessage 失败: {exc}")
            return
        await self.handle_message(msg)

    async def handle_message(self, msg: NormalizedMessage) -> None:
        """处理一条弹幕（collectors → Agent 入口；测试也可直接调）。"""
        self._total_messages += 1
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
            ``_make_two_stage_decision`` 的决策结果视图 + ``success`` /
            ``elapsed_ms`` 字段；入参校验失败时返回 ``success=False`` + ``error``。
        """
        if proactive and batch:
            return {
                "success": False,
                "error": "proactive 模式不接受弹幕批次（主动发言由房间状态驱动，batch 置空）",
            }
        if not proactive and not (batch and any(str(item.get("text", "")).strip() for item in batch)):
            return {"success": False, "error": "弹幕批次为空：请至少提供一条 text 非空的弹幕"}

        messages: List[NormalizedMessage] = []
        for item in batch or []:
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            nickname = str(item.get("nickname", "")).strip() or "测试观众"
            messages.append(
                NormalizedMessage(
                    text=text,
                    source="dashboard.debug",
                    data_type="text",
                    importance=0.5,
                    timestamp=now_ms(),
                    user_id=f"debug_{nickname}",
                    user_nickname=nickname,
                )
            )

        started_ms = now_ms()
        trigger_reason = "proactive:dashboard_debug" if proactive else "dashboard:debug_test"
        try:
            async with self._flush_lock:
                result = await self._make_two_stage_decision(
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
        if self._flush_lock.locked():
            return

        async with self._flush_lock:
            # buffer 空时 → 主动发言判定（场次边界闸：未开播直接返回且不消费
            # pending 信号，环节变更信号保留到开播后首个 tick 生效）
            if self._buffer.is_empty:
                if not self._live_active:
                    return
                rundown_pending = self._rundown_proactive_pending
                self._rundown_proactive_pending = False
                reason = self._proactive_trigger.should_trigger(
                    self._room_state,
                    now_ms(),
                    external_pending=self._external_proactive_pending,
                    rundown_pending=rundown_pending,
                    rundown_ready=self._is_rundown_active(),
                    rundown_overdue=self._is_rundown_overdue(),
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
    ) -> Dict[str, Any]:
        """两阶段决策外壳：Planner → 消费 plan 评估 + 触发 reply 工具。

        决策可观测收口（每轮恰好一条 planner.decision，成功/失败/降级全覆盖）：
        生成本轮 ``round_id``，边界处发射 ``streamer.stage``（planning → idle），
        决策收口处发射 ``planner.decision``——观察者据此回答"主播为什么这么做/
        为什么没反应"，无需翻日志。两阶段执行逻辑在 ``_decide_round``。

        Returns:
            决策结果视图（正常 flush 循环忽略返回值；调试门面
            ``debug_test_decision`` 消费它向 Dashboard 回传完整中间产物）::

                {
                    "round_id": str,
                    "trigger_reason": str,
                    "proactive": bool,
                    "forced": bool,
                    "plan": {
                        "should_reply",
                        "target",
                        "reply_to",
                        "topic_summary",
                        "reply_guidance",
                        "confidence",
                        "silent_reason",
                    }
                    | None,
                    "speech": str | None,
                    "emotion": str | None,
                    "utterance_id": str | None,
                    "reply_to_message_id": str | None,  # 回复关联键
                    "silent_reason": str | None,  # low_confidence=低置信度压制
                    "error": str | None,  # planner/reply 失败原因，成功为 None
                    "planner_raw": str,  # Planner LLM 原始输出（截断）
                    "llm_request_id": str | None,  # LLM 请求历史指针
                    "planner_duration_ms": int,
                    "reply_duration_ms": int,
                    "total_duration_ms": int,
                }
        """
        round_id = self._next_round_id()
        started_ms = now_ms()
        await self._emit_streamer_stage(
            stage="planning",
            agent_state="running",
            round_id=round_id,
            detail=trigger_reason,
        )
        result = await self._decide_round(
            batch,
            round_id=round_id,
            started_ms=started_ms,
            forced=forced,
            trigger_reason=trigger_reason,
            proactive=proactive,
        )
        await self._emit_planner_decision(result, batch)
        closing = "决策轮结束"
        if result.get("speech"):
            closing += "：发言已出"
        elif result.get("error"):
            closing += f"：{result['error']}"
        elif result.get("silent_reason"):
            closing += f"：静默（{result['silent_reason']}）"
        await self._emit_streamer_stage(
            stage="idle",
            agent_state="wait",
            round_id=round_id,
            detail=closing,
        )
        return result

    async def _decide_round(
        self,
        batch: List[NormalizedMessage],
        *,
        round_id: str,
        started_ms: int,
        forced: bool,
        trigger_reason: str,
        proactive: bool,
    ) -> Dict[str, Any]:
        """两阶段决策执行体（被 ``_make_two_stage_decision`` 外壳驱动）。

        决策过程副产品（原始输出/请求 ID/分段耗时）随 ``result`` 带回，
        由外壳统一发射决策事件；本方法不直接发事件。
        """
        result: Dict[str, Any] = {
            "round_id": round_id,
            "trigger_reason": trigger_reason,
            "proactive": proactive,
            "forced": forced,
            "plan": None,
            "speech": None,
            "emotion": None,
            "utterance_id": None,
            "reply_to_message_id": None,
            "silent_reason": None,
            "error": None,
            "planner_raw": "",
            "llm_request_id": None,
            "planner_duration_ms": 0,
            "reply_duration_ms": 0,
            "total_duration_ms": now_ms() - started_ms,
        }

        # 读历史（duck-typed）
        history = await self._read_history("live")

        # 拼装流程单上下文
        rundown_text = self._build_rundown_text()

        # 游戏叙事（三通道·事件：MinecraftAgent 等 emit 的 game.* 摘要）
        game_narrative = self._game_narrative_text()

        # Planner ReAct 决策（循环内完成查信息与 reply 调用；失败细节经
        # Planner.last_failure 带出，供决策事件区分降级原因）
        planner_started_ms = now_ms()
        thinking = None
        if self._thinking_sink is not None and getattr(self.typed_config.thinking_stream, "enabled", True):
            thinking = ThinkingStreamContext(self._thinking_sink, round_id)
        try:
            outcome = await self._planner.plan(
                batch,
                forced=forced,
                proactive=proactive,
                history=history,
                rundown_text=rundown_text,
                game_narrative=game_narrative,
                thinking=thinking,
                round_id=round_id,
            )
        except Exception as exc:
            self._logger.error(f"Planner 调用异常: {exc}", exc_info=True)
            outcome = None
            result["error"] = f"planner_failed: {exc}"
        result["planner_duration_ms"] = now_ms() - planner_started_ms
        result["planner_raw"] = (getattr(self._planner, "last_raw_content", "") or "")[:2000]
        result["llm_request_id"] = getattr(self._planner, "last_request_id", None)

        if outcome is None:
            self._planner_failures += 1
            self._total_no_action += 1
            detail = getattr(self._planner, "last_failure", None)
            result["error"] = result["error"] or (
                f"planner_failed: {detail}" if detail else "planner_failed: 决策循环异常"
            )
            result["total_duration_ms"] = now_ms() - started_ms
            return result

        result["plan"] = {
            "should_reply": outcome.get("replied", False),
            "target": outcome.get("target"),
            "reply_to": outcome.get("reply_to"),
            "topic_summary": outcome.get("topic_summary", ""),
            "reply_guidance": outcome.get("reply_guidance", ""),
            "confidence": outcome.get("confidence"),
            "silent_reason": outcome.get("silent_reason"),
        }
        result["reply_to_message_id"] = outcome.get("reply_to")
        result["silent_reason"] = outcome.get("silent_reason")

        # 未说话（自然终止/超步/LLM 失败）——静默收场
        if not outcome.get("replied"):
            self._total_no_action += 1
            if outcome.get("error"):
                self._planner_failures += 1
                result["error"] = f"planner_failed: {outcome['error']}"
            result["total_duration_ms"] = now_ms() - started_ms
            return result

        # reply 已在 Planner ReAct 循环内经 reply 工具完成（Planner 阶段耗时含
        # 表达生成）；此处仅把产出送发言管线（speech → TTS / emotion → VTS）。
        speech_info = self._dispatch_speech_and_emotion(
            outcome.get("reply_payload"),
            self._resolve_reply_target_user(outcome, batch),
            reply_to_message_id=outcome.get("reply_to"),
            round_id=round_id,
        )
        if speech_info is not None:
            result["speech"], result["emotion"], result["utterance_id"] = speech_info

        # 成功：保存上下文 + 记录发言时刻 + 频率限制
        self._total_replies += 1
        self._room_state.record_speech(now_ms())
        if proactive:
            # 决策循环内约定 proactive 原因带 "proactive:" 标记（debug/记账共用），此处取其后正文
            reason = trigger_reason[len("proactive:") :] if trigger_reason else "unknown"
            self._proactive_trigger.record_trigger(reason, now_ms())

        result["total_duration_ms"] = now_ms() - started_ms
        return result

    def _resolve_reply_target_user(
        self,
        outcome: Dict[str, Any],
        batch: List[NormalizedMessage],
    ) -> Optional[str]:
        """从 batch 反查本次回复的观众 user_id。

        优先消费 ``outcome["reply_to"]``（reply 意图指向的弹幕 message_id——按
        message_id 等值命中即可）；未提供时回退 ``outcome["target"]``（弹幕
        ``message_id`` 或文本片段）匹配：message_id 等值 → text 包含/相等。
        全未命中时保守兜底为 batch 最后一条消息的 user_id（"回复最后那条"
        通常是意图所指）；batch 为空或 target 为空时返回 None。该方法只做
        反查，不写状态、不发事件；异常吞掉记 warning 不上抛（决策循环必须继续）。
        """
        reply_to = outcome.get("reply_to")
        target = reply_to or outcome.get("target")
        if not isinstance(target, str) or not target:
            return None
        if not batch:
            return None

        try:
            # reply_to 是精确 message_id，等值命中即返回
            if reply_to:
                for msg in batch:
                    if getattr(msg, "message_id", None) == reply_to:
                        return getattr(msg, "user_id", None)
                return None
            for msg in batch:
                if getattr(msg, "message_id", None) == target:
                    return getattr(msg, "user_id", None)
                msg_text = getattr(msg, "text", None)
                if isinstance(msg_text, str) and msg_text and (target in msg_text or msg_text == target):
                    return getattr(msg, "user_id", None)
            return getattr(batch[-1], "user_id", None)
        except Exception as exc:
            self._logger.warning(f"反查 reply target user 异常: {exc}")
            return None

    # ==================================================================
    # 发言管线（speech → TTS / emotion → VTS）
    # ==================================================================

    # emotion → VTS 表情参数映射（与 VTSProvider._emotion_map 形态一致）。
    # 独立保留一份是为了让 StreamerAgent 在不持有 VTSProvider 实例时
    # 也能把 emotion 翻译为可调用参数；与 VTSProvider 的真实映射解耦
    # 也便于单测直接断言。
    _EMOTION_TO_VTS_PARAMS: Dict[str, Dict[str, float]] = {
        "happy": {"MouthSmile": 1.0},
        "surprised": {"EyeOpenLeft": 1.0, "EyeOpenRight": 1.0, "MouthOpen": 0.5},
        "sad": {"MouthSmile": -0.3, "EyeOpenLeft": 0.7, "EyeOpenRight": 0.7},
        "angry": {"EyeOpenLeft": 0.6, "EyeOpenRight": 0.6, "MouthSmile": -0.5},
        "shy": {"MouthSmile": 0.3, "EyeOpenLeft": 0.8, "EyeOpenRight": 0.8},
        "love": {"MouthSmile": 0.8, "EyeOpenLeft": 0.9, "EyeOpenRight": 0.9},
        "excited": {"MouthSmile": 1.0, "EyeOpenLeft": 1.0, "EyeOpenRight": 1.0},
        "confused": {"EyeOpenLeft": 0.7, "EyeOpenRight": 0.7, "MouthOpen": 0.2},
        "scared": {"EyeOpenLeft": 0.5, "EyeOpenRight": 0.5, "MouthOpen": 0.3},
        "neutral": {},
    }

    def _next_utterance_id(self) -> str:
        """生成下一个 utterance_id（格式 ``utt_{epoch_ms}_{seq}``）。

        自增计数器在 ``__init__`` 中初始化为 0，首次调用返回 seq=1。
        seq 是进程内单调递增，保证同场内 utterance_id 唯一。
        """
        self._utterance_seq += 1
        return f"utt_{now_ms()}_{self._utterance_seq}"

    def _next_round_id(self) -> str:
        """生成下一个决策轮次 ID（格式 ``rnd_{epoch_ms}_{seq}``）。

        与 utterance_id 同风格的进程内单调关联键：本轮弹幕批次、决策记录、
        发言、工具结果经它成组（观察器按轮渲染）。
        """
        self._round_seq += 1
        return f"rnd_{now_ms()}_{self._round_seq}"

    async def _emit_streamer_stage(
        self,
        *,
        stage: str,
        agent_state: str,
        round_id: Optional[str] = None,
        detail: str = "",
    ) -> None:
        """发布 ``streamer.stage`` 阶段状态事件（决策循环内直接 await，保证先后顺序）。"""
        if self._event_bus is None:
            return
        payload = StreamerStagePayload(
            stage=stage,
            agent_state=agent_state,
            round_id=round_id,
            detail=detail,
        )
        try:
            await self._event_bus.emit(
                CoreEvents.STREAMER_STAGE,
                payload,
                source="streamer_agent.stage",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 观测事件不阻断决策循环
            self._logger.warning(f"streamer.stage 发布失败（已忽略）: stage={stage}, err={exc}")

    async def _emit_planner_decision(self, result: Dict[str, Any], batch: List[NormalizedMessage]) -> None:
        """发布 ``planner.decision`` 决策轮记录事件（决策循环内直接 await，保证先于 idle 状态）。

        从 ``_decide_round`` 的结果视图构造决策事件：触发原因、批次摘要、
        决策结论、回复关联、失败原因、原始输出指针与分段耗时全部入事件，
        观察者与互动分析不再依赖日志。
        """
        if self._event_bus is None:
            return
        # 关联/指针字段统一收口为 str（鸭子类型 mock 响应可能带任意对象属性）
        request_id = result.get("llm_request_id")
        reply_to = result.get("reply_to_message_id")
        silent = result.get("silent_reason")
        payload = PlannerDecisionPayload(
            round_id=str(result.get("round_id") or ""),
            trigger_reason=str(result.get("trigger_reason") or ""),
            proactive=bool(result.get("proactive")),
            forced=bool(result.get("forced")),
            batch=[
                PlannerBatchItem(
                    message_id=str(getattr(msg, "message_id", "") or ""),
                    user_id=str(getattr(msg, "user_id", "") or ""),
                    user_name=str(getattr(msg, "user_nickname", "") or ""),
                    text=(str(getattr(msg, "text", "") or ""))[:120],
                )
                for msg in batch or []
            ],
            should_reply=bool((result.get("plan") or {}).get("should_reply", False)),
            target=(
                str((result.get("plan") or {}).get("target")) if (result.get("plan") or {}).get("target") else None
            ),
            topic_summary=str((result.get("plan") or {}).get("topic_summary", "") or ""),
            reply_guidance=str((result.get("plan") or {}).get("reply_guidance", "") or ""),
            confidence=float((result.get("plan") or {}).get("confidence", 0.0) or 0.0),
            reply_to_message_id=str(reply_to) if reply_to else None,
            silent_reason=str(silent) if silent else None,
            speech=(str(result["speech"]) if result.get("speech") else None),
            emotion=(str(result["emotion"]) if result.get("emotion") else None),
            utterance_id=(str(result["utterance_id"]) if result.get("utterance_id") else None),
            error=(str(result["error"]) if result.get("error") else None),
            planner_raw=str(result.get("planner_raw", "") or ""),
            llm_request_id=str(request_id) if request_id else None,
            planner_duration_ms=int(result.get("planner_duration_ms", 0) or 0),
            reply_duration_ms=int(result.get("reply_duration_ms", 0) or 0),
            total_duration_ms=int(result.get("total_duration_ms", 0) or 0),
        )
        try:
            await self._event_bus.emit(
                CoreEvents.PLANNER_DECISION,
                payload,
                source="streamer_agent.decision",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 观测事件不阻断决策循环
            self._logger.warning(f"planner.decision 发布失败（已忽略）: round_id={result.get('round_id')}, err={exc}")

    def _dispatch_speech_and_emotion(
        self,
        reply_payload: Any,
        reply_target_user_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        round_id: str = "",
    ) -> Optional[tuple]:
        """消费 reply 结构化结果并触发下游管线（决策循环安全：永不抛异常）。

        Args:
            reply_payload: ``ToolExecutionResult.structured_content``（dict），
                形态 ``{speech, emotion, actions}``；emotion 为
                ``{"name": str, "intensity": float}``。
            reply_target_user_id: 本次回复的观众 user_id（可选；透传到
                ``streamer.speech`` 业务事件，None 表示主动发言/无特定对象）。
            reply_to_message_id: 本次回复所指向弹幕的 message_id（可选；
                来自 Planner 决策输出，透传到发言事件并落库为互动关联）。
            round_id: 决策轮次 ID（可选；透传到 ``streamer.speech`` 事件供
                观察器把发言卡与该轮思考过程成组）。

        行为契约：
        - 非 dict 输入 → WARN 日志 + 直接返回（决策循环不受影响）
        - TTS 未启用 → 仍发布 ``streamer.speech`` 业务事件 + 写入 ContextService 历史
          （TTS 启用与否与主播发言业务事实正交；下游消费者仅依赖业务事件）
        - speech 非空 → 生成 utterance_id + 发布业务事件 + 写入历史；TTS 启用时
          复用同一 utterance_id 入 TTS 队列（与 ``tts.utterance.*`` 共用关联键）
        - emotion 非空 → ``asyncio.create_task`` 调 VTS 表情工具（fire-and-forget）
        - actions 非空 → 逐条 ``asyncio.create_task`` 调 ToolRegistry（fire-and-forget）
        """
        if not isinstance(reply_payload, dict):
            self._logger.warning(f"reply structured_content 非 dict，跳过发言管线: {type(reply_payload).__name__}")
            return None

        speech = reply_payload.get("speech", "")
        emotion = reply_payload.get("emotion", "")
        actions = reply_payload.get("actions", [])

        cleaned_speech = speech.strip() if isinstance(speech, str) else ""
        # emotion 新契约为 {name, intensity}；兼容旧字符串形态（防御）
        cleaned_emotion_intensity = 0.5
        if isinstance(emotion, dict):
            cleaned_emotion: Optional[str] = str(emotion.get("name", "") or "").strip() or None
            try:
                cleaned_emotion_intensity = min(1.0, max(0.0, float(emotion.get("intensity", 0.5))))
            except (TypeError, ValueError):
                cleaned_emotion_intensity = 0.5
        elif isinstance(emotion, str):
            cleaned_emotion = emotion.strip() or None
        else:
            cleaned_emotion = None

        # 动作工具调用（fire-and-forget；决策循环安全：失败仅记日志）
        self._schedule_actions(actions)

        # emotion → VTS 表情调用跟随 TTS 启用门：TTS 关闭（无语音/无声卡场景）
        # 时 avatar 表情随同关闭，避免与改造前的"管线整体退化"语义漂移。
        # 与 speech 派发独立（speech 空但 emotion 非空时仍可触发）。
        if cleaned_emotion and self._tts_enabled and self._utterance_queue is not None:
            self._schedule_vts_emotion(cleaned_emotion, intensity=cleaned_emotion_intensity)

        # speech 非空：先发布业务事件 + 写历史（与 TTS 启用与否正交），
        # TTS 启用时复用同一 utterance_id 入 TTS 队列。
        if cleaned_speech:
            utterance_id = self._next_utterance_id()
            self._emit_streamer_speech(
                utterance_id,
                cleaned_speech,
                cleaned_emotion,
                reply_target_user_id,
                reply_to_message_id=reply_to_message_id,
                round_id=round_id,
            )
            self._record_streamer_speech_history(cleaned_speech, cleaned_emotion)
            self._schedule_subtitle_show(cleaned_speech, utterance_id)
            if self._tts_enabled and self._utterance_queue is not None:
                try:
                    asyncio.create_task(self._utterance_queue.enqueue(utterance_id, cleaned_speech))
                except Exception as exc:
                    self._logger.warning("utterance 入队失败（已忽略）: utterance_id={}, err={}", utterance_id, exc)
            return cleaned_speech, cleaned_emotion, utterance_id

        return None

    def _emit_streamer_speech(
        self,
        utterance_id: str,
        text: str,
        emotion: Optional[str],
        target_user_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        round_id: str = "",
    ) -> None:
        """发布 ``streamer.speech`` 业务事件（fire-and-forget；下游不得触发新决策）。"""
        event_bus = self._event_bus
        if event_bus is None:
            return
        payload = StreamerSpeechPayload(
            utterance_id=utterance_id,
            round_id=round_id or None,
            text=text,
            emotion=emotion,
            target_user_id=target_user_id,
            reply_to_message_id=reply_to_message_id,
        )

        async def _do_emit() -> None:
            try:
                await event_bus.emit(
                    CoreEvents.STREAMER_SPEECH,
                    payload,
                    source="streamer_agent.speech",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 异步背景任务边界
                self._logger.warning(f"streamer.speech 发布失败（已忽略）: utterance_id={utterance_id}, err={exc}")

        try:
            asyncio.create_task(_do_emit())
        except RuntimeError as exc:
            self._logger.warning(f"streamer.speech 任务创建失败（已忽略）: utterance_id={utterance_id}, err={exc}")

    def _record_streamer_speech_history(self, text: str, emotion: Optional[str]) -> None:
        """把主播发言写入 ContextService 历史（fire-and-forget；缺 context_service 跳过）。

        会话 id 与现有 ``_read_history_sync`` / ``_read_history`` 一致，使用
        ``"live"`` 作为 live 场次默认 session_id；下游 Planner/Replyer 共享同一历史。
        """
        ctx = self._context
        if ctx is None:
            return

        async def _do_record() -> None:
            try:
                await ctx.add_message(  # type: ignore[union-attr]
                    session_id="live",
                    role=MessageRole.ASSISTANT,
                    content=text,
                    emotion=emotion,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 异步背景任务边界
                self._logger.warning(f"主播发言写入 ContextService 历史失败（已忽略）: err={exc}")

        try:
            asyncio.create_task(_do_record())
        except RuntimeError as exc:
            self._logger.warning(f"主播发言历史任务创建失败（已忽略）: err={exc}")

    def _schedule_subtitle_show(self, text: str, utterance_id: str) -> None:
        """异步触发字幕推送（fire-and-forget；缺字幕服务跳过，失败不抛异常）。

        调用 ``SubtitleService.show(text, utterance_id)``：服务内部并行
        广播到所有已注册 Backend，单 Backend 故障隔离由服务负责，本方法
        只关心"任务跑出去"。与 ``_emit_streamer_speech`` 同模式——
        决策循环同步路径绝不 await 异步操作。
        """
        service = self._subtitle_service
        if service is None:
            return

        async def _do_show() -> None:
            try:
                await service.show(text, utterance_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 异步背景任务边界
                self._logger.warning(f"字幕 show 异常（已忽略）: utterance_id={utterance_id}, err={exc}")

        try:
            asyncio.create_task(_do_show())
        except RuntimeError as exc:
            self._logger.warning(f"字幕 show 任务创建失败（已忽略）: utterance_id={utterance_id}, err={exc}")

    def _schedule_vts_emotion(self, emotion: str, intensity: float = 0.5) -> None:
        """异步触发 VTS 表情调用（fire-and-forget，失败不影响决策循环）。

        VTS 工具契约（vts_set_expression）::

            arguments = {
                "parameters": {param_name: value, ...},  # 表情参数映射
                "weight": float,                         # 混合权重（情绪强度驱动）
            }

        若 emotion 不在已知映射表中，DEBUG 日志提示"无映射"并跳过；
        已知映射但参数为空（如 ``neutral``）也照样发起调用，让 VTS
        工具自身的静默处理逻辑统一接管（不做空表达式的特判短路）。

        Args:
            emotion: 情绪枚举名（映射表键）。
            intensity: Replyer 输出的情绪强度 [0.0, 1.0]，直接映射为表情混合权重。
        """
        vts_params = self._EMOTION_TO_VTS_PARAMS.get(emotion)
        if vts_params is None:
            self._logger.debug(f"emotion '{emotion}' 未在已知映射表中，跳过 VTS 调用")
            return

        # 在闭包外捕获 registry 引用：避免 LSP 跨闭包推断失败，
        # 同时确保 _invoke_vts 在工具尚未注入时不会抛 AttributeError。
        registry = self._tool_registry
        if registry is None:
            self._logger.debug(f"emotion '{emotion}' 触发条件不满足（tool_registry 缺失），跳过")
            return

        invocation = ToolInvocation(
            tool_name="vts_set_expression",
            arguments={
                "parameters": dict(vts_params),
                "weight": float(min(1.0, max(0.0, intensity))),
            },
            source="streamer_agent.emotion",
        )

        async def _invoke_vts() -> None:
            try:
                await registry.invoke(invocation)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 异步背景任务边界
                self._logger.warning(f"VTS 表情调用异常（已忽略）: emotion={emotion}, err={exc}")

        try:
            asyncio.create_task(_invoke_vts())
        except RuntimeError as exc:
            self._logger.warning(f"VTS 表情任务创建失败（已忽略）: emotion={emotion}, err={exc}")

    def _schedule_actions(self, actions: Any) -> None:
        """异步触发动作工具调用（fire-and-forget，失败不影响决策循环）。

        ``actions`` 契约：``[{name: str, parameters: dict}, ...]``（来自
        Replyer 的 tool_calls 非 reply 部分；LLM 通过标准 function calling
        选择的动作工具）。

        与 ``_schedule_vts_emotion`` 同模式：
        - 每条动作独立 ``asyncio.create_task``（互不阻塞）
        - registry 缺失时静默跳过
        - 工具失败只记 WARN（注册表 invoke 本身不抛异常，双保险）
        """
        if not isinstance(actions, list) or not actions:
            return

        registry = self._tool_registry
        if registry is None:
            self._logger.debug(f"actions 触发条件不满足（tool_registry 缺失），跳过 {len(actions)} 条")
            return

        for action in actions:
            if not isinstance(action, dict):
                self._logger.debug(f"action 条目非 dict，跳过: {action!r}")
                continue
            name = str(action.get("name", "") or "").strip()
            if not name:
                self._logger.debug("action 条目缺 name，跳过")
                continue
            arguments = action.get("parameters") or action.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            invocation = ToolInvocation(
                tool_name=name,
                arguments=arguments,
                source="streamer_agent.action",
            )

            async def _invoke_action(inv: ToolInvocation = invocation) -> None:
                try:
                    await registry.invoke(inv)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 异步背景任务边界
                    self._logger.warning(f"动作工具调用异常（已忽略）: tool={inv.tool_name}, err={exc}")

            try:
                asyncio.create_task(_invoke_action())
            except RuntimeError as exc:
                self._logger.warning(f"动作任务创建失败（已忽略）: tool={name}, err={exc}")

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
        if rundown_id and self._sqlite is not None:
            try:
                rundown = await self._sqlite.get_rundown(rundown_id)
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
        """聚合 Dashboard 编排页所需的流程单视图数据。

        Returns:
            ``None`` 当流程单未加载；否则返回::

                {
                    "snapshot": dict,  # RundownState.get_snapshot() 原样
                    "transitions": list[dict],  # 最近 50 条变更历史
                    "segments": list[dict],  # 环节清单（id/title/目标/要点/时长/备注）
                }
        """
        state = self._rundown_state
        rundown = state.rundown
        if rundown is None:
            return None

        segments_view: List[Dict[str, Any]] = [
            {
                "id": seg.id,
                "title": seg.title,
                "task_description": seg.task_description,
                "key_points": list(seg.key_points),
                "expected_ms": seg.expected_ms,
                "min_duration_ms": seg.min_duration_ms,
                "notes": seg.notes,
            }
            for seg in rundown.segments
        ]

        return {
            "snapshot": state.get_snapshot(now_ms=now_ms),
            "transitions": state.get_transitions(),
            "segments": segments_view,
        }

    async def rundown_control(
        self,
        action: str,
        *,
        segment_id: Optional[str] = None,
        now_ms: Optional[int] = None,
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """执行 Dashboard 手动控制动作（pause/resume/next/goto，by="human"）。

        仅做转发与结构化拒绝翻译；调用方可凭 ``success`` / ``message`` /
        ``snapshot`` 三元组渲染 UI。

        Args:
            action: 控制动作名（pause/resume/next/goto）
            segment_id: goto 必填；其它动作忽略
            now_ms: 可选时间戳（默认走 RundownState 注入时钟或真实时钟）

        Returns:
            ``(success, message, snapshot)``：失败时 ``snapshot`` 为 ``None``。
        """
        state = self._rundown_state

        def _snap() -> Optional[Dict[str, Any]]:
            try:
                return state.get_snapshot(now_ms=now_ms)
            except Exception as exc:  # pragma: no cover - 防御
                self._logger.warning(f"rundown_control 取快照失败: {exc}")
                return None

        try:
            if action == "pause":
                reject = state.pause(by="human", now_ms=now_ms)
            elif action == "resume":
                reject = state.resume(by="human", now_ms=now_ms)
            elif action == "next":
                reject = state.next(by="human", now_ms=now_ms)
            elif action == "goto":
                if not segment_id:
                    return False, "goto 必须提供 segment_id", None
                reject = state.goto(segment_id, by="human", now_ms=now_ms)
            else:
                return False, f"未知 action: {action!r}", None

            if reject is not None:
                messages = {
                    "min_duration_not_met": (
                        f"当前环节最少停留未到（还需约 {max(1, round(reject.remaining_ms / 1000))} 秒）"
                    ),
                    "unknown_segment_id": f"环节不存在（可用: {', '.join(reject.available_ids)}）",
                    "already_done": "流程单已完成",
                    "not_running": "流程单未在运行",
                    "not_paused": "流程单未在暂停",
                    "no_rundown_loaded": "未加载流程单",
                }
                return False, messages.get(reject.reason, reject.reason), _snap()
            return True, "已执行", _snap()
        except ValueError as exc:
            # 契约级错误（非法 by 等程序员错误）
            return False, str(exc), _snap()
        except Exception as exc:
            # 状态机内部错误统一兜底，避免 dashboard 500
            self._logger.warning(f"rundown_control {action!r} 异常: {exc}", exc_info=True)
            return False, f"控制失败: {exc}", _snap()

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
    config: StreamerConfig,
    llm_manager: Any,
    prompt_manager: Any,
    agent_manager: AgentManager,
    context_service: Optional[Any] = None,
    event_bus: Optional[EventBus] = None,
    tool_registry: Optional[ToolRegistry] = None,
    sqlite_store: Optional[Any] = None,
    persona_provider: Optional[Any] = None,
    spec_provider: str = "builtin",
    speech_config: Optional[Dict[str, Any]] = None,
) -> StreamerAgent:
    """便捷工厂：构造 StreamerAgent + 注册到 AgentManager。

    Args:
        config: ``StreamerConfig`` 实例
        其余参数同 ``StreamerAgent.__init__``
        agent_manager: ``AgentManager`` 实例（构造完后 register 到管理器）
        spec_provider: provider 来源溯源（"builtin"/Agent 名/"mcp"），默认 builtin
        speech_config: 发言管线配置（来自核心 ``[tts]`` 段；可选，组合根接线
            由独立任务负责）。

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
        sqlite_store=sqlite_store,
        persona_provider=persona_provider,
        speech_config=speech_config,
    )
    agent_manager.register(agent, spec_provider=spec_provider)
    return agent
