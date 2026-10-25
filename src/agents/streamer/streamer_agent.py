"""StreamerAgent - 主播 Agent（BaseAgent 子类）

主播 Agent = Planner（决策核心）+ reply 工具（入口）+ Replyer（表达引擎），一体。
协议六项（最小契约）：
- 生命周期：start/stop/cleanup + 可重建性
- 工具提供：list_tools() → 暴露 reply（streamer_reply）；rundown_control 由 rundown 注册项声明
- 事件上报：emit（rundown.changed 等；订阅 room.message.danmaku 等）
- 状态读写：RoomState / RundownCoordinator（内含 RundownState）内部组件
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
    viewer_profile_repo=store.viewer_profiles,
    recap_repo=store.recaps,
)
await agent.start()
# Agent now: subscribes room.message.danmaku → buffers → planner → reply tool → ...
await agent.cleanup()
```
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any, Coroutine, Dict, Iterable, List, Optional, TYPE_CHECKING

from src.modules.agents.base import BaseAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.body import BodyEventPayload
from src.modules.events.payloads.game_chat import GameChatPayload
from src.modules.logging import get_logger
from src.modules.utils.task_utils import spawn_background_task
from src.modules.tools import ToolSpec
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.utils.time_utils import now_ms
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.events.payloads.room_state import RoomStateWatchedPayload
from src.modules.vision.image_store import get_image_store

from .rundown.rundown_coordinator import RundownCoordinator
from .tools.rundown_tool import build_rundown_tool_provider
from .background import SUMMARY_PROFILE, BackgroundMaintainer
from .decision_executor import DecisionRoundExecutor
from .memory import (
    RegularsRosterWriter,
    SessionRecapWriter,
    StreamerNotebook,
    build_memory_tool_provider,
    build_notebook_tool_provider,
    emit_notebook_changed,
)
from .message_buffer import MessageBuffer
from .mouth import SCENE_CHANGING_BODY_KINDS, URGENT_BODY_KINDS, URGENT_GAME_TYPES, Mouth
from .planner import Planner
from .proactive_trigger import ProactiveTrigger
from .replyer import WordFilter, Replyer
from .room_state import RoomState
from .scene_card import brief_of
from .speech_dispatcher import SpeechDispatcher
from .game_ledger import GameLedger
from .stats import StreamerStats
from .timeline import GameEventBatch, StreamerTimeline, TimelineTurn
from .timing_gate import TimingGate
from .tools.reply_tool import ReplyToolProvider
from .command.router import CommandRouter
from .config import StreamerConfig

if TYPE_CHECKING:
    from src.modules.subtitle import SubtitleService
    from src.modules.tts import TTSProvider

__all__ = ["StreamerAgent", "StreamerConfig"]

# LLM profile 用途名（与 [llm_profiles.<name>] 三层结构对齐；model.toml 必填 6 成员）
_PROFILE_PLANNER = "planner"
_PROFILE_REPLYER = "replyer"

# 私聊暂存上限（行）。只进本窗决策参考段，不进时间线
_PRIVATE_CHAT_LINES_MAX = 10

# 运营递话提醒队列上限（条）。满时拒收递话（receive_prompt 返回 False，
# REST 侧映射 409）——必达语义不允许静默挤掉旧条目。
_REMINDER_QUEUE_MAX = 5


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
    # 发生时刻：落库行与内存对话轮按它合并成同一条时间线
    timestamp_ms: int = 0


def _text_field(item: Any, name: str) -> str:
    """鸭子字段取文本：只接受 str，其余（None/Mock 等）按空串处理。"""
    value = getattr(item, name, "")
    return value if isinstance(value, str) else ""


class StreamerAgent(BaseAgent):
    """主播 Agent：编排 Planner + Replyer + 工具 + 后台任务 + 流程单。

    实现协议六项：
    - 生命周期（start/stop/cleanup）
    - 工具提供：reply（streamer_reply，经 ToolRegistry 注册 + 名单 ["streamer"]）；
      rundown_control 由 rundown 注册项声明（provider="rundown"）。
      should_speak_proactively / parse_command 是代码直连的内部件，不是工具。
    - 事件上报：emit（rundown.changed / planner.decision 等）；订阅 room.message.*
    - 状态读写：内部 RoomState / RundownCoordinator / MessageBuffer
    - 健康：BaseAgent 心跳
    - 元数据：name / description
    """

    # -----元数据（必须覆写）-----
    name = "streamer"
    description = "Streamer Agent - 主播决策 + 表达 + 后台维护"

    # -----事件族声明（可选）-----
    emits_events = (CoreEvents.RUNDOWN_CHANGED, CoreEvents.STREAMER_RECAP_WRITTEN)

    def __init__(
        self,
        config: StreamerConfig,
        *,
        llm_manager: Any,
        prompt_manager: Any,
        event_bus: Optional[EventBus] = None,
        tool_registry: Optional[ToolRegistry] = None,
        rundown_repo: Optional[Any] = None,
        rundown_instance_repo: Optional[Any] = None,
        rundown_transition_repo: Optional[Any] = None,
        chat_repo: Optional[Any] = None,
        sessions_repo: Optional[Any] = None,
        topic_repo: Optional[Any] = None,
        viewer_repo: Optional[Any] = None,
        viewer_profile_repo: Optional[Any] = None,
        recap_repo: Optional[Any] = None,
        event_repo: Optional[Any] = None,
        context_assembler_config: Optional[Any] = None,
        speech_config: Optional[Dict[str, Any]] = None,
        tts_engine: Optional["TTSProvider"] = None,
        subtitle_service: Optional["SubtitleService"] = None,
        session_manager: Optional[Any] = None,
        thinking_sink: Optional[Any] = None,
        task_ledger: Optional[Any] = None,
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
            rundown_instance_repo: 可选 ``RundownInstanceRepo``（实例快照仓储）。
                ``None`` 时崩溃恢复与实例写穿整体跳过（保持可测试性）
            rundown_transition_repo: 可选 ``RundownTransitionRepo``（变更事实仓储）。
                ``None`` 时变更不落行（保持可测试性）
            chat_repo: 可选 ``ChatRepo``（live_chat 会话历史读取，reply tool 历史源）
            sessions_repo: 可选 ``SessionRepo``（live_sessions 实时状态，转交后台维护器）
            topic_repo: 可选 ``TopicRepo``（摘要/话题快照落地，转交后台维护器）
            viewer_repo: 可选 ``ViewerRepo``（观众统计仓储）——Planner 画像注入
                时经它实时取观众昵称、观众画像按互动量筛候选、工具按昵称反查；
                None 时注入行回退 platform/user_id、画像不更新
            viewer_profile_repo: 可选 ``ViewerProfileRepo``（观众画像）。``None`` 时
                画像相关功能整体降级——Planner 走无画像注入路径、收播不更新印象、
                ``memory_recall_viewer`` 工具不注册
            recap_repo: 可选 ``SessionRecapRepo``（场次回顾）。``None`` 时收播不写
                回顾、开播不注入上场回顾、``memory_recall_session`` 工具不注册
            event_repo: 可选 ``EventRepo``（游戏事件）——场次回顾的游戏进展原料；
                None 时回顾少这一类原料
            context_assembler_config: 可选上下文组装器配置（[agents.streamer.context] 子段；
                控制 Planner 组装路径开关；None 时 Planner 走内置默认）
            speech_config: 可选发言管线配置（来自核心 ``[tts]`` 段）。
                形态::

                    {
                        "enabled": bool,  # 是否启用 TTS 下游管线
                        "max_queue": int,  # 队列容量（默认 3）
                        "render_timeout_ms": int,  # 单 utterance 超时（默认 60000）
                        "wait_playback": bool,  # 发言等上一句播完（默认 True）
                        "wait_playback_timeout_ms": int,  # 等播排空上限（默认 60000，0 不限时）
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
            task_ledger: 可选任务记录表（``TaskLedger``，异步任务基建）。
                ``None`` 时委派渲染不带机器账状态（planner 只显示 LLM 交付语）。
        """
        super().__init__(event_bus=event_bus)
        self.typed_config = config
        self._llm = llm_manager
        self._prompt = prompt_manager
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        # 任务记录表引用：委派渲染的机器状态从这里查询（None 时 planner 降级只看交付语）
        self._task_ledger = task_ledger
        # 仓储注入（组合根按需分发；None 时对应能力降级。流程单三仓储由
        # RundownCoordinator 直接收取，不在 Agent 身上落字段）
        self._chat = chat_repo
        self._session_manager = session_manager
        # 思考流旁路出口（可选；观察面专用，不进 EventBus 不落库）
        self._thinking_sink = thinking_sink
        # 观众统计仓储（Planner 注入画像时实时取昵称用；None 时回退 platform/user_id）
        self._viewer_repo = viewer_repo
        # 主播记忆的两张表（可选；None 时对应记忆功能整体降级）
        self._profile_repo = viewer_profile_repo
        self._recap_repo = recap_repo
        self._event_repo = event_repo
        self._logger = get_logger("StreamerAgent")
        # fire-and-forget 后台任务强引用（防 GC 中途回收 + 异常可见化）
        self._bg_tasks: set = set()

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
        # context 组装器路径开关——直接读包内权威
        _context_enabled = config.context.enabled
        self._planner = Planner(
            config={
                "profile": _PROFILE_PLANNER,
                "planner_max_steps": config.planner_max_steps,
                "planner_recent_intents_max": config.planner_recent_intents_max,
                "planner_recent_relays_max": config.planner_recent_relays_max,
                "reference_tools": list(config.reference_tools),
                # 冷场提示复用主动发言的冷场阈值：同一个"多久算冷"只配一处
                "cold_silence_ms": config.proactive.cold_timeout_ms,
            },
            llm_service=llm_manager,
            prompt_service=prompt_manager,
            room_state=self._room_state,
            tool_registry=tool_registry,
            profile_repo=viewer_profile_repo,
            viewer_repo=viewer_repo,
            context_enabled=bool(_context_enabled),
            behavior_style=_behavior_style,
            # 房管也要知道她是谁：挑话题、在回复指引里定她的态度都以人设为准；措辞风格仍只给表达侧
            bot_name=config.persona.bot_name,
            personality=config.persona.personality,
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

        # 本场备忘录（进程内会话状态，不入库；收播取快照折叠进回顾后清空）
        self._notebook = StreamerNotebook(max_chars=config.memory.notebook_max_chars)
        # 收播时刻的备忘录全文快照：清空备忘录前取走，供回顾生成原料读取
        self._session_notebook_snapshot: str = ""

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
                # 表达出口的台词长度预算：超出先重说成一句，仍超按完整句子截短
                "speech_max_chars": config.speech_max_chars,
            },
            llm_service=llm_manager,
            prompt_service=prompt_manager,
            tool_registry=tool_registry,
            word_filter=self._word_filter,
            notebook_text_provider=self._notebook.read,
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
            "rundown_ready_interval_ms": proactive.rundown_ready_interval_ms,
        }
        self._proactive_trigger = ProactiveTrigger(proactive_config)

        # 流程单（Rundown）子系统：备忘录 + 闹钟（推进权归 Agent）。
        # 状态机与仓储/总线/场次解析的粘合收拢在协调器内（依赖全构造注入，
        # 环节变更经回调置位宿主的 proactive pending）；开播时长锚点沿用
        # bind 注入（同 bind_reply_provider）
        self._rundown_coord = RundownCoordinator(
            config=config,
            rundown_repo=rundown_repo,
            rundown_instance_repo=rundown_instance_repo,
            rundown_transition_repo=rundown_transition_repo,
            session_manager=session_manager,
            event_bus=event_bus,
            logger=self._logger,
            bg_tasks=self._bg_tasks,
            notify_proactive=self._on_rundown_changed_proactive,
        )
        self._planner.bind_elapsed_live_provider(self._rundown_coord.state.get_elapsed_live_ms)
        self._planner.bind_notebook_provider(self._notebook.read)
        if task_ledger is not None:
            self._planner.bind_delegation_status_provider(self._delegation_machine_status)

        # 后台维护器（双任务：轻循环 + 压缩 worker）——读包内权威配置
        bg = config.background
        background_config = {
            "enabled": bg.enabled,
            "light_tick_ms": bg.light_tick_ms,
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
            event_bus=event_bus,
            topic_repo=topic_repo,  # 摘要落地：timeline_summary + topics 快照
            prompt_manager=self._prompt,  # 摘要/画像系统提示词渲染（复用 Agent 持有的 PromptManager）
        )

        # 主播记忆：场次回顾（收播写 / 启动补写 / 开播注入）与观众画像（收播更新）。
        # 仓储或开关缺一则该项降级为 None；两者都用 summary 档位模型，不抢决策优先级
        memory_cfg = config.memory
        self._recap_inject_count: int = int(memory_cfg.recap_inject_count)
        self._recap_writer: Optional[SessionRecapWriter] = None
        if memory_cfg.recap_enabled and recap_repo is not None and sessions_repo is not None and chat_repo is not None:
            self._recap_writer = SessionRecapWriter(
                sessions_repo=sessions_repo,
                chat_repo=chat_repo,
                recap_repo=recap_repo,
                topic_repo=topic_repo,
                event_repo=event_repo,
                llm_service=llm_manager,
                prompt_manager=prompt_manager,
                notebook_text_provider=lambda: self._session_notebook_snapshot,
                max_length=memory_cfg.recap_max_length,
                event_bus=event_bus,
            )
        self._roster_writer: Optional[RegularsRosterWriter] = None
        if (
            memory_cfg.roster_enabled
            and viewer_profile_repo is not None
            and chat_repo is not None
            and viewer_repo is not None
        ):
            self._roster_writer = RegularsRosterWriter(
                chat_repo=chat_repo,
                viewer_repo=viewer_repo,
                profile_repo=viewer_profile_repo,
                llm_service=llm_manager,
                prompt_manager=prompt_manager,
                min_interactions=memory_cfg.roster_min_interactions,
                batch_max=memory_cfg.roster_batch_max,
                profile_max_length=memory_cfg.profile_max_length,
            )
        # 上场回顾段文本：开播（live.started）时从库里读出并固定，整场不变
        self._recap_section: str = ""

        # 后台 flush 循环（Agent 主循环）
        self._flush_task: Optional[asyncio.Task] = None
        self._flush_lock = asyncio.Lock()
        self._running = False

        # 场次进行位：live.started 置位 / live.ended 复位。主动发言的业务边界
        # = 场次——没开播只回弹幕不主动开题，开场白（rundown opening）等开播
        self._live_active: bool = False
        # 一次性 pending flag（外部 API 触发主动发言）
        self._external_proactive_pending: bool = False
        # 流程单环节切换触发 flag（协调器变更回调置位；经 _on_rundown_changed_proactive）
        self._rundown_proactive_pending: bool = False
        # 游戏待定夺触发 flag（game.report 事件置位；下次空缓冲 tick 消费）
        self._game_decision_pending: bool = False

        # 统计（决策循环各分支增量；对外经 get_statistics 导出）
        self._stats = StreamerStats()

        # 游戏事件账本（game.* / game.body.* / game.chat.* 三通道统一）：事件到达
        # 先入账做同摘要折叠，下个决策窗边界（读历史时）整批出账追加进时间线的
        # game 条目——事件在对话历史里只出现一次、不再逐窗重放，更早的细节查对话
        # 历史与前情提要，需要数值现场用游戏状态查询工具。相邻窗口的历史前缀逐字一致
        self._game_ledger = GameLedger()
        # 私聊的那几行：只进本窗决策参考段，不进时间线——表达侧与历史都看不到，
        # 私下的话不能被当众说出来
        self._pending_private_chat: deque[str] = deque(maxlen=_PRIVATE_CHAT_LINES_MAX)
        # 最近一次决策窗边界入账的批次（决策执行器每窗在历史读取后经 provider 取用；
        # 状态跃迁事实块与本轮新鲜语料从这批行取，不再从重放全文提取）
        self._window_game_batch = GameEventBatch()

        # 未落库的对话轮：没开场次时的全部对话、调试面板注入的观众消息都不会写进
        # live_chat，由这里按时间并入历史——主播问"要不要拉一下"、观众回"拉"时，
        # 下一轮决策仍看得到自己刚问过什么。场次开始/结束时清空，与 live_chat 同以场次为界。
        self._unpersisted_turns: List[_LiveChatTurn] = []
        # 本场对话进 LLM 前的只追加投影：Planner 与 Replyer 共用同一份历史，
        # 超预算时后台按章压成前情提要，两次换章之间历史前缀逐字不变
        self._timeline = StreamerTimeline(
            budget=config.context,
            llm_service=llm_manager,
            prompt_service=self._prompt,
            profiles=(_PROFILE_PLANNER, _PROFILE_REPLYER),
            summary_profile=SUMMARY_PROFILE,
        )
        # 进时间线的游戏记录：游戏事件（game.* / game.body.* / game.chat.*）与我在
        # 游戏里开始做的事。事件在决策窗边界整批入账，这里留下整场的先后，换章时
        # 一起写进前情提要。与对话轮同以场次为界
        self._timeline_notes: List[TimelineTurn] = []
        self._planner.bind_action_recorder(self._record_delegation)
        # 最新一帧直播画面：查询式取自图像暂存的 latest 槽位（live_frame 采集器
        # 每次采样覆盖写入），每个决策窗第一次请求必挂——走查询不走事件流，
        # 保证决策时拿到的是刚截的画面
        if config.live_frame.enabled:
            self._planner.bind_frame_provider(self._latest_live_frame, stale_warn_ms=config.live_frame.stale_warn_ms)

        # 运营递话提醒队列（receive_prompt 入队；下个决策窗以【运营提醒】段
        # 注入参考块，读取即取空——送达一次制）。满时拒收，必达不允许静默挤旧。
        self._reminder_queue: deque[str] = deque(maxlen=_REMINDER_QUEUE_MAX)

        # 观众命令接线（最小接线：玩法待扩展）。enabled + 任一映射表非空才激活；
        # 两张映射表（委派指令 / 工具调用）合起来即白名单，限频窗口/次数 config 化。
        # 命令路由是代码直连的内部件，不进 ToolRegistry。
        cmd_cfg = config.command
        self._command_router: Optional[CommandRouter] = None
        if cmd_cfg.enabled and (cmd_cfg.mappings or cmd_cfg.tool_mappings):
            self._command_router = CommandRouter(cmd_cfg, tool_registry, logger=self._logger)

        # 工具 Provider 实例（用于 invoke）
        self._reply_provider: Optional[ReplyToolProvider] = None

        # ===== 发言管线（speech → TTS / 字幕）=====
        # 队列生命周期由 dispatcher 自持（start/stop）；未启用时决策循环
        # 只读 result.success，不消费 result.content。情绪渲染由虚拟形象适配器
        # 订阅 streamer.speech 反射，不在本管线扇出。
        self._speech = SpeechDispatcher(
            event_bus=event_bus,
            subtitle_service=subtitle_service,
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
            rundown_text_provider=self._rundown_coord.build_text,
            game_events_provider=self._current_game_batch,
            reminders_provider=self._drain_reminders,
            session_recap_provider=self._session_recap_text,
            logger=self._logger,
        )

        # 嘴（Agent 内部件，代码直连）：游戏里出事时不经 Planner 当场出一句短反应。
        # 开关关闭时不构造，所有路径与原来一致
        self._mouth: Optional[Mouth] = None
        if config.mouth.enabled:
            self._mouth = Mouth(
                config.mouth,
                llm_service=llm_manager,
                prompt_service=prompt_manager,
                speech=self._speech,
                room_state=self._room_state,
                persona={
                    "bot_name": config.persona.bot_name,
                    "personality": config.persona.personality,
                    "style_constraints": config.persona.style_constraints,
                },
                speech_max_chars=config.speech_max_chars,
                gate_window=config.speech_gate_window,
                gate_ratio=config.speech_gate_ratio,
                word_filter=self._word_filter,
                goals_provider=self._planner.current_goals,
                notebook_provider=self._notebook.read,
                segment_provider=self._current_segment_title,
                scene_reader=self._read_scene_brief,
                is_live=lambda: self._live_active,
                profile=_PROFILE_REPLYER,
            )
            # 她说过的每句话（Planner 的与嘴自己的）经发声分发器直连记进嘴的最近记录，嘴才不会重复自己
            self._speech.bind_spoken_listener(self._mouth.note_speech)

        self._logger.info(
            f"StreamerAgent 已构造 "
            f"(profile_planner={_PROFILE_PLANNER}, profile_replyer={_PROFILE_REPLYER}, "
            f"proactive_enabled={config.proactive.enabled}, "
            f"rundown_id={config.rundown_id!r}, "
            f"tts_enabled={self._speech.tts_enabled}, "
            f"tts_engine={'<已注入>' if tts_engine is not None else '<未注入>'}, "
            f"mouth_enabled={self._mouth is not None})"
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

        # 收播整理善后：上次进程退出时没来得及写的回顾，启动后在后台补写一场、
        # 顺带补跑该场的观众画像更新，补完再把"上场回顾"段读出来（开播前就能用）
        if self._recap_writer is not None:
            spawn_background_task(
                self._bootstrap_recaps(),
                logger=self._logger,
                tasks=self._bg_tasks,
                label="StreamerAgent.recap_bootstrap",
            )

        # 启动流程单（fail-soft：加载失败降级为无流程单）
        await self._rundown_coord.start()

        # 启动发言管线（speech → TTS / emotion → VTS；队列生命周期归 dispatcher）
        await self._speech.start()
        if self._mouth is not None:
            await self._mouth.start()

        self._logger.info("StreamerAgent 已启动")

    async def _on_stop(self) -> None:
        """Agent 停止钩子。"""
        self._running = False

        # 嘴先停：它会往发声管线里塞台词
        if self._mouth is not None:
            await self._mouth.stop()

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

        # 时间线在跑的后台整理随 Agent 停止作废
        await self._timeline.reset()

        # 资源清理契约：摘除本 Agent 注册的 provider（reply / rundown / memory）
        removed = self.unregister_tool_providers()

        self._logger.info(f"StreamerAgent 已停止（摘除 {removed} 个工具）")

    def _delegation_machine_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """委派任务在任务账本里的机器状态（planner 委派渲染的保底事实源，只读查询）。

        返回 ``None`` = 终态已移除或查不到（渲染侧以 LLM 交付语或"没有回音"为准）。
        """
        if self._task_ledger is None:
            return None
        record = self._task_ledger.get(task_id)
        if record is None:
            return None
        return {"status": record.status, "updated_at_ms": record.updated_at_ms, "snapshot": dict(record.snapshot)}

    # ==================================================================
    # 观测面（只读快照，供 dashboard REST 查询；无任何写入口）
    # ==================================================================

    def memo_snapshot(self) -> Dict[str, Any]:
        """备忘录快照（只读）：``GET /agents/streamer/memo`` 的数据源。"""
        return {"notebook": self._notebook.read()}

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
        """构造 reply Provider；reply / rundown_control / memory_* 注册进 ToolRegistry。

        名单口径（注册处声明）：
        - ``streamer_reply``：LLM 可调的真工具，注册 + 名单 ``["streamer"]``
          （自己的工具填自己）；Planner 经 registry 统一调用，本窗交接材料
          随 ``ToolInvocation.attachment`` 传入（不经实例槽位）。
        - ``rundown_control``：注册（provider="rundown"，名单 ``["streamer"]``）
          进 ToolRegistry，与其他工具同一路径被 Planner 调用；可见性随
          Provider 注册/摘除进出名单（Agent 启动即注册，无需二次开关）。
        - ``memory_recall_session`` / ``memory_recall_viewer``：主播记忆工具
          （provider="memory"，名单 ``["streamer"]``），两张记忆表都注入时才注册。
        - ``streamer_notebook``：本场备忘录（provider="streamer"，名单 ``["streamer"]``），
          会话内状态不依赖仓储，随注册无条件注册。
        """

        # reply tool（无条件构造——注册与 Planner 侧引用共用同一实例）
        self._reply_provider = ReplyToolProvider(
            replyer=self._replyer,
            # 没有聊天仓储时历史仍由内存对话轮承载，表达侧照样知道上一句说了什么
            history_provider=self._read_history_sync,
            rundown_text_provider=self._rundown_coord.build_text_sync,
            event_bus=self._event_bus,
            gate_window=self.typed_config.speech_gate_window,
            gate_ratio=self.typed_config.speech_gate_ratio,
        )
        # Planner 的 reply 调用经 registry；绑定 Provider 仅供引用/诊断（材料走 attachment）
        self._planner.bind_reply_provider(self._reply_provider)

        # proactive tool
        # 注册：reply 与 rundown_control（可见名单 ["streamer"]）；经基类入口
        # 登记归属，stop 路径逐一摘除（disable/重建不留残留工具）
        if self._tool_registry is not None:
            self.register_tool_provider(
                self._reply_provider, registry=self._tool_registry, visible_to={"streamer_reply": ["streamer"]}
            )
            self.register_tool_provider(
                build_rundown_tool_provider(self._rundown_coord.tool_provider),
                registry=self._tool_registry,
                visible_to={"rundown_control": ["streamer"]},
            )
            registered = "streamer_reply / rundown_control"
            # 备忘录工具：随主播 Agent 无条件注册（会话内状态，不依赖任何仓储）
            self.register_tool_provider(
                build_notebook_tool_provider(self._notebook, event_bus=self._event_bus),
                registry=self._tool_registry,
                visible_to={"streamer_notebook": ["streamer"]},
            )
            registered += " / streamer_notebook"
            if self._recap_repo is not None and self._profile_repo is not None:
                self.register_tool_provider(
                    build_memory_tool_provider(
                        recap_repo=self._recap_repo,
                        profile_repo=self._profile_repo,
                        viewer_repo=self._viewer_repo,
                        current_session_provider=self._current_session_pk,
                    ),
                    registry=self._tool_registry,
                    visible_to={"memory_recall_session": ["streamer"], "memory_recall_viewer": ["streamer"]},
                )
                registered += " / memory_recall_session / memory_recall_viewer"
            self._logger.info(f"StreamerAgent 工具已注册：{registered}（名单 [streamer]）")

    # ==================================================================
    # 事件订阅
    # ==================================================================

    def _subscribe_events(self) -> None:
        """订阅 room.message.* + room.state.* + game.* + live.* 事件（语义域事件）。"""
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
        # 进房（room.message.enter）：只记 RoomState 的进房名单（活跃度行的
        # 欢迎原料），不是对话消息——不进决策缓冲、不触发决策窗
        self._event_bus.on(
            CoreEvents.ROOM_MESSAGE_ENTER,
            self._on_room_enter,
            model_class=RoomMessagePayload,
        )
        # 房间状态流（room.state.*）：观看数等统计推送只更新 RoomState 快照
        # （后台记账数据源），不是对话消息——不进决策缓冲、不影响热度
        self._event_bus.on(
            CoreEvents.ROOM_STATE_WATCHED_COUNT,
            self._on_room_state_watched,
            model_class=RoomStateWatchedPayload,
        )
        # 直播画面不在此订阅：决策窗挂帧查询式读图像暂存的 latest 槽位（见 _latest_live_frame）
        # 游戏事件（三通道）：游戏 Agent（如 MinecraftAgent）emit game.*
        # → 主播侧入待入账队列，决策窗边界批量追加进对话时间线（按 payload.game
        # 过滤可扩展到多游戏）
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
        # 游戏异常也进事件行（如"无法执行目标：LLM 未注入"）——否则主播不知命令失败
        self._event_bus.on(
            CoreEvents.GAME_ERROR,
            self._on_game_event,
            model_class=GamePayload,
        )
        # 游戏主动上报（交付总结/升级决策）——追加进历史并兼作"是否回提示词"的决策数据源
        self._event_bus.on(
            CoreEvents.GAME_REPORT,
            self._on_game_event,
            model_class=GamePayload,
        )
        # 身体侧近况（AI 玩家遭遇：被袭击/死亡/重生/紧急反应）——采集器分类后的事件，
        # 与上面的 game.* 事件分两条线（行标签区分）；通配订阅覆盖 8 类，按 payload.kind 便于扩展
        self._event_bus.on(
            CoreEvents.GAME_BODY_WILDCARD,
            self._on_body_event,
            model_class=BodyEventPayload,
        )
        # 游戏里的聊天（采集器滤掉了 AI 自己的回显）：玩家搭话促发一轮决策，系统消息只作参考
        self._event_bus.on(
            CoreEvents.GAME_CHAT_WILDCARD,
            self._on_game_chat,
            model_class=GameChatPayload,
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
            "StreamerAgent 已订阅 room.message.danmaku|gift|super_chat|guard|enter / room.state.watched_count"
            " / game.* / game.body.* / game.chat.* / live.started|ended"
        )

    async def _on_live_started(
        self,
        event_name: str,
        payload: LiveStartedPayload,
        source: str,
    ) -> None:
        """live.started 回调：开播，放行主动发言，读出上场回顾固定整场。"""
        del event_name, source
        self._live_active = True
        if self._mouth is not None:
            self._mouth.reset()
        # 流程单开播起算：armed（已装载待开播）→ running，双锚点 = 开播时刻。
        # 已起算的场（崩溃恢复的进行中/已完成）被结构化拒绝，保持现态静默忽略；
        # begin 经 on_changed 置位 proactive pending——开场白信号在开播时刻才置
        reject = self._rundown_coord.state.begin(by="system")
        if reject is not None:
            self._logger.debug(f"开播起算流程单跳过（{reject.reason}）：流程单已在进行中")
        # 开播后对话改由 live_chat 记录；开播前的调试闲聊不混进这一场的历史
        self._unpersisted_turns.clear()
        self._game_ledger.reset()
        self._pending_private_chat.clear()
        self._timeline_notes.clear()
        await self._timeline.reset()
        # 上场回顾：开播这一刻从库里读最近几场（排除本场），整场固定不再变
        await self._refresh_recap_section(exclude_session_id=payload.live_session_id)
        self._logger.info(f"场次已开启（id={payload.live_session_id}）：主动发言放行")

    async def _on_live_ended(
        self,
        event_name: str,
        payload: LiveEndedPayload,
        source: str,
    ) -> None:
        """live.ended 回调：下播，主动发言收闸，道别收束本场，后台写本场回顾并更新观众画像。"""
        del event_name, source
        self._live_active = False
        if self._mouth is not None:
            self._mouth.reset()
        self._logger.info("场次已结束：主动发言收闸")
        # 收播道别窗：手动结束场次会绕过流程单收尾环节（只有自然走完才道别），
        # 没有这一窗 Agent 会一路静默到停机。触发判据是"场次结束但 Agent 仍在
        # 运行"——应用停机路径（AgentManager 先停，live.ended 后到）此时
        # _running 已为 False，进程退出时的道别无意义，不触发。forced 豁免
        # 重复闸：道别话题常与收尾环节的发言字面重合，被误抑制就永远说不出再见。
        # await 形态：本回调由事件总线的后台任务驱动，await 不阻塞发布方，
        # 且保证道别决策窗赶在事件面仍在、发言管线未停的窗口内完整走完
        # （说完再见再做收播整理与记忆清理）。
        if self._running:
            await self._execute_round([], forced=True, trigger_reason="proactive:signoff", proactive=True)
        # 下播即结束这一场的对话记忆，下播后的调试对话重新从空白开始记
        # （道别发言不属已结账的场次，留在内存对话里供下播后的调试闲聊接续）
        self._unpersisted_turns.clear()
        self._game_ledger.reset()
        self._pending_private_chat.clear()
        self._timeline_notes.clear()
        await self._timeline.reset()
        # 备忘录生命周期收口：先取全文快照（回顾生成的原料之一，经只读回调递入）
        # 再清空——备忘录是场次作用域，下一场从空白开始
        self._session_notebook_snapshot = self._notebook.read()
        if self._session_notebook_snapshot:
            self._logger.info(f"本场备忘录已归档待回顾折叠（{len(self._session_notebook_snapshot)} 字），备忘录清空")
            # 清空也是备忘录状态变化：发观测事件（快照非空才发——空备忘录清空无状态变化）
            await emit_notebook_changed(self._event_bus, "")
        self._notebook.clear()
        # 收播整理：先写这一场的回顾，再给本场活跃的常客更新印象（两次 LLM 调用，
        # 放后台不阻塞事件回调）。空场次（无任何明细行，行已删）没东西可整理
        if not payload.empty_discarded and (self._recap_writer is not None or self._roster_writer is not None):
            spawn_background_task(
                self._settle_session(payload.live_session_id),
                logger=self._logger,
                tasks=self._bg_tasks,
                label="StreamerAgent.settle_session",
            )

    # ==================================================================
    # 主播记忆：场次回顾 / 观众画像
    # ==================================================================

    def _session_recap_text(self) -> str:
        """上场回顾段（DecisionRoundExecutor 每窗读；开播时固定，整场不变）。"""
        return self._recap_section

    def _current_session_pk(self) -> Optional[int]:
        """进行中场次主键（无场次管理器或未开播为 None）；回忆往前第 n 场时要排除本场。"""
        if self._session_manager is None:
            return None
        pk = getattr(self._session_manager, "active_pk", None)
        return int(pk) if isinstance(pk, int) else None

    async def _refresh_recap_section(self, *, exclude_session_id: Optional[int] = None) -> None:
        """从库里读最近几场回顾渲染成注入段；未启用或没有上一场时为空串（段整段省略）。"""
        if self._recap_writer is None or self._recap_inject_count <= 0:
            self._recap_section = ""
            return
        current = exclude_session_id if exclude_session_id is not None else self._current_session_pk()
        self._recap_section = await self._recap_writer.render_recent(
            count=self._recap_inject_count, exclude_session_id=current
        )
        if self._recap_section:
            self._logger.info(f"上场回顾已装入参考段（{len(self._recap_section)} 字）")

    async def _bootstrap_recaps(self) -> None:
        """启动善后：补写上次退出时漏掉的回顾（最多一场）并补跑该场画像更新，再装入上场回顾段。

        画像只取上次整理之后的新话，即使上次其实已经更新过也不会重复调用模型。
        """
        assert self._recap_writer is not None  # noqa: S101 调用方已 guard
        written = await self._recap_writer.backfill_missing(limit=1)
        if written:
            self._logger.info(f"启动补写了场次回顾：{written}")
        if self._roster_writer is not None:
            for live_session_id in written:
                try:
                    await self._roster_writer.update_for_session(live_session_id)
                except Exception as exc:
                    self._logger.warning(f"启动补跑观众画像失败（场次 {live_session_id}）: {exc}")
        await self._refresh_recap_section()

    async def _settle_session(self, live_session_id: int) -> None:
        """收播整理：回顾 → 观众画像。逐项降级，一项失败不影响另一项。"""
        if self._recap_writer is not None:
            try:
                await self._recap_writer.write_recap(live_session_id)
            except Exception as exc:
                self._logger.warning(f"收播写回顾失败（场次 {live_session_id}）: {exc}")
        if self._roster_writer is not None:
            try:
                await self._roster_writer.update_for_session(live_session_id)
            except Exception as exc:
                self._logger.warning(f"收播更新观众画像失败（场次 {live_session_id}）: {exc}")

    async def _on_game_event(
        self,
        event_name: str,
        payload: GamePayload,
        source: str,
    ) -> None:
        """game.* 事件回调：事件行入待入账队列，下个决策窗边界批量追加进时间线。

        行标签带事件类型（``游戏·<game>·<类型>``），决策层据此区分"剧情推进"
        （milestone）与"需要定夺"（report）；message 本体经术语脱敏（任务号/观测
        引用等内部标识不进主播上下文）。``report`` 类型（交付总结/升级决策）同时
        置位待定夺触发信号——游戏停在选项处等主播定夺，下次空缓冲 tick 应促发
        一轮主动决策。
        """
        try:
            message = self._game_ledger.record_event(
                payload.game, payload.event_type, payload.message, payload.timestamp_ms or now_ms()
            )
            if self._mouth is not None and payload.event_type in URGENT_GAME_TYPES and message:
                self._mouth.note_game(message)
                self._mouth.react(f"game:{payload.event_type}", message, urgent=True)
            if payload.event_type == "report":
                self._game_decision_pending = True
                # 游戏里做完或卡住待定夺：【我手头在游戏里做的事】里那件事随之标成已上报
                self._planner.note_game_report(payload.game, payload.report_kind or "")
        except Exception as exc:  # noqa: BLE001 - 收集失败不阻断
            self._logger.warning(f"收集游戏事件失败: {exc}", exc=True)

    def _drain_game_events(self) -> GameEventBatch:
        """决策窗边界：账本整批出账，本批时间线条目落进时间线，批次行交决策链。

        折叠过多条的行附计数说明（"另 N 次相同"）后再入时间线；时间线条目只追加
        不改写，相邻窗口的历史前缀逐字一致。私聊行不进时间线，单独随批次交本窗
        决策参考段。
        """
        batch = self._game_ledger.drain(private_chat=self._pending_private_chat)
        self._pending_private_chat.clear()
        self._timeline_notes.extend(batch.timeline_turns)
        self._window_game_batch = batch
        return batch

    def _current_game_batch(self) -> GameEventBatch:
        """本窗新入账的游戏事件批次（决策执行器在历史读取后取用；无新事件为空批次）。"""
        return self._window_game_batch

    def _record_delegation(self, agent: str, instruction: str) -> None:
        """Planner 受理委派后回调：把"我在哪个游戏里开始做什么"按此刻进时间线。

        委派行直写时间线，不进决策窗批次（批次只收三类游戏事件通道的条目）。
        """
        self._timeline_notes.append(self._game_ledger.record_delegation(agent, instruction))

    async def _on_body_event(
        self,
        event_name: str,
        payload: BodyEventPayload,
        source: str,
    ) -> None:
        """``game.body.*`` 回调：AI 玩家身体侧事件入待入账队列（标签 ``遭遇·<game>·<种类>``）。

        与游戏进展（``game.*``）同走追加式历史，行标签区分两条线；同一件事两条线
        都报（任务中遭遇攻击）时，由提示词规定只讲一次、以带任务上下文的那条为准。
        死亡是要紧事：置位待定夺信号（与 game.report 同款），下次空缓冲 tick 立即
        促发一轮决策，主播当场知道、当场解说。
        """
        try:
            summary = self._game_ledger.record_encounter(
                payload.game,
                payload.kind,
                payload.summary,
                bool(getattr(payload, "resolved", False)),
                payload.timestamp_ms or now_ms(),
            )
            if self._mouth is not None:
                # 嘴开着：遭遇由嘴当场反应（几秒内出声），死亡也一样——不再为它专门唤醒 Planner，
                # 免得嘴说一句、Planner 过十几秒又把同一件事讲一遍
                self._mouth.note_game(summary)
                self._mouth.react(
                    f"body:{payload.kind}",
                    summary,
                    urgent=payload.kind in URGENT_BODY_KINDS,
                    refresh_scene=payload.kind in SCENE_CHANGING_BODY_KINDS,
                )
            elif payload.kind == "died":
                # 死亡是要紧事：与 game.report 同款置位待定夺信号，下次空缓冲 tick 立即
                # 促发一轮决策（绕最小间隔与每小时上限）。死亡连发时连窗是有意行为——
                # 那是叙事价值最高的时刻，主播应当场知道、当场解说。
                self._game_decision_pending = True
        except Exception as exc:  # noqa: BLE001 - 收集失败不阻断
            self._logger.warning(f"收集身体侧事件失败: {exc}", exc=True)

    async def _on_game_chat(
        self,
        event_name: str,
        payload: GameChatPayload,
        source: str,
    ) -> None:
        """``game.chat.*`` 回调：游戏里别人说的话入待入账队列；玩家搭话促发一轮决策。

        玩家聊天是有人在跟游戏里的我说话，像弹幕一样值得及时接住，所以置位待决策信号
        （下次空缓冲 tick 触发；弹幕在排队时随那一轮一起看到）。系统消息（公告、死亡
        提示、传送请求等）是信息，只作参考，不单独唤醒主播。私聊行不进时间线：
        只交本窗决策参考段，私下的话不能被当众说出来。
        """
        del event_name, source
        try:
            speaker = "系统消息" if payload.kind == "system" else f"玩家 {payload.sender or '（名字未知）'}"
            if payload.private:
                # 私聊标在发言人后面：决策看到这是只发给我的话，不在直播里念出或转述
                speaker += "（私聊，只发给我）"
            note = "（原话过长已截断）" if payload.truncated else ""
            if payload.suppressed:
                note += f"（之前还有 {payload.suppressed} 条重复或刷屏的没转过来）"
            body = f"{speaker}：{payload.content}{note}"
            if payload.private:
                self._pending_private_chat.append(f"[{payload.game}] {body}")
            else:
                self._game_ledger.record_chat(payload.game, body, payload.timestamp_ms or now_ms())
                if self._mouth is not None:
                    self._mouth.note_game(body)
            if payload.kind == "player":
                self._game_decision_pending = True
        except Exception as exc:  # noqa: BLE001 - 收集失败不阻断
            self._logger.warning(f"收集游戏聊天失败: {exc}", exc=True)

    def _current_segment_title(self) -> str:
        """当前流程单环节的标题（嘴的现场卡用）；没有流程单为空。"""
        segment = self._rundown_coord.state.current_segment
        return segment.title if segment is not None else ""

    async def _read_scene_brief(self) -> str:
        """读一句话现场：逐个调配置的参考工具，取第一个结果里的 ``brief``；都没有返回空串。"""
        if self._tool_registry is None:
            return ""
        for name in self.typed_config.reference_tools:
            try:
                result = await self._tool_registry.invoke(
                    ToolInvocation(tool_name=name, arguments={}, source="mouth-scene", caller_profile=_PROFILE_REPLYER)
                )
            except Exception as exc:  # noqa: BLE001 - 读不到现场不挡反应
                self._logger.warning(f"嘴读现场工具 '{name}' 失败: {exc}", exc=True)
                continue
            brief = brief_of(result.structured_content) if result.success else ""
            if brief:
                return brief
        return ""

    def _latest_live_frame(self) -> Optional[Any]:
        """最新一帧直播画面（图像暂存 latest 槽位快照；从未采样过为 None）。"""
        return get_image_store().latest()

    async def _on_room_state_watched(
        self,
        event_name: str,
        payload: RoomStateWatchedPayload,
        source: str,
    ) -> None:
        """``room.state.watched_count`` 回调：更新 RoomState 累计观看快照。

        仅记账数据源（background 轻循环经 get_snapshot 写 live_sessions），
        与决策链无关。
        """
        del event_name, source
        self._room_state.set_audience_total(payload.audience_total, now_ms=payload.timestamp_ms)

    async def _on_room_enter(
        self,
        event_name: str,
        payload: RoomMessagePayload,
        source: str,
    ) -> None:
        """``room.message.enter`` 回调：记 RoomState 进房名单（限频去重在 RoomState 内）。

        主播原先完全看不到观众进房（观众数协议无源恒 0，做不了人数门槛），
        名单经【房间活跃度】行进决策上下文，供欢迎话术顺口点名。
        """
        del event_name, source
        self._room_state.record_enter(payload.user.id, payload.user.name, now_ms=payload.timestamp_ms)

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
        if self._mouth is not None:
            sender = msg.user.name if msg.user is not None else ""
            self._mouth.note_viewer(sender or "观众", msg.content)

    def trigger_external_proactive(self, topic_hint: Optional[str] = None) -> None:
        """外部 API 触发主动发言（Dashboard / API 调用）。"""
        self._external_proactive_pending = True
        if topic_hint:
            self._logger.info(f"外部主动发言触发: {topic_hint}")

    def _on_rundown_changed_proactive(self) -> None:
        """流程单环节变更通知（协调器回调）：置位 proactive 即时信号，下个 tick 消费。"""
        self._rundown_proactive_pending = True

    def receive_prompt(self, *, content: str, source: str = "") -> bool:
        """接收递话（运营/跨 Agent 留言）：入提醒队列，下个决策窗必达。

        不冒充观众弹幕、不进对话历史——队列内容在决策窗入口取空，以
        【运营提醒】段注入 Planner 参考块（消费即送达，一次制）。队列
        满时拒收（返回 False）：必达语义不允许静默挤掉旧条目。
        """
        text = content.strip()
        if not text:
            self._logger.warning(f"递话内容为空，拒收（source={source or '未知'}）")
            return False
        if len(self._reminder_queue) >= _REMINDER_QUEUE_MAX:
            self._logger.warning(f"提醒队列已满（{_REMINDER_QUEUE_MAX}），拒收递话（source={source or '未知'}）")
            return False
        self._reminder_queue.append(text)
        self._logger.info(
            f"收到递话提醒（source={source or '未知'}，队列 "
            f"{len(self._reminder_queue)}/{_REMINDER_QUEUE_MAX}）：{text[:60]}"
        )
        return True

    def _drain_reminders(self) -> str:
        """取空提醒队列（决策执行器在决策窗入口调用；送达一次制）。"""
        if not self._reminder_queue:
            return ""
        lines = [f"- {item}" for item in self._reminder_queue]
        self._reminder_queue.clear()
        return "\n".join(lines)

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
                # 调试注入不经 room.message 事件，观众这句不会落库；记入内存后下一轮仍能接上话
                result = await self._execute_round(
                    messages,
                    forced=forced,
                    trigger_reason=trigger_reason,
                    proactive=proactive,
                    batch_persisted=False,
                )
        except Exception as exc:
            self._logger.exception(f"调试决策执行异常: {exc}")
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
                    self._logger.exception(f"批次决策异常: {exc}")
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
            # buffer 空时 → 主动发言判定。递话必达优先于开播闸：下播后运营递话
            # 仍须送达（TTS 管道不依赖场次），队列空且未开播才早退——早退不消费
            # pending 信号，环节变更/游戏待定夺信号保留到开播后首个 tick 生效。
            # 未开播的递话窗里环节/游戏 pending 不参与判定（不能在未开播时说
            # 开场白/推进游戏），也因此不会被误消费。
            if self._buffer.is_empty:
                if not self._reminder_queue and not self._live_active:
                    return
                live_gated = not self._live_active
                rundown_pending = False if live_gated else self._rundown_proactive_pending
                game_pending = False if live_gated else self._game_decision_pending
                if not live_gated:
                    self._game_decision_pending = False
                reason = self._proactive_trigger.should_trigger(
                    self._room_state,
                    now_ms(),
                    external_pending=False if live_gated else self._external_proactive_pending,
                    rundown_pending=rundown_pending,
                    rundown_overdue=False if live_gated else self._rundown_coord.is_overdue(),
                    rundown_ready=False if live_gated else self._rundown_coord.is_active(),
                    game_pending=game_pending,
                    # 提醒队列非空即催醒；防接龙阻塞时队列保留，下一 tick 重试
                    # （送达在决策执行器取空队列处发生，任何决策窗都会消费）
                    reminder_pending=bool(self._reminder_queue),
                )
                # 环节切换信号在确认消费后才清零：should_trigger 里总开关关闭 /
                # reminder 抢先返回时不会走到 rundown_pending 分支，信号保留，
                # 重新满足条件后开场白还能补说（pending 分支优先级高于
                # overdue，返回 "rundown" 且 rundown_pending=True 时
                # 必然源自本信号）。game_pending 同形态问题按任务范围界定暂不处理。
                if rundown_pending and reason == "rundown":
                    self._rundown_proactive_pending = False
                if not live_gated:
                    self._external_proactive_pending = False
                if reason is not None:
                    self._stats.total_proactive += 1
                    self._logger.info(f"主动发言触发: {reason}")
                    await self._execute_round(
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

            await self._execute_round(batch, forced=forced, trigger_reason=flush_reason)

    async def _execute_round(
        self,
        batch: List[RoomMessagePayload],
        *,
        forced: bool,
        trigger_reason: str,
        proactive: bool = False,
        batch_persisted: bool = True,
    ) -> Dict[str, Any]:
        """执行一轮决策，并把本轮观众消息与主播发言记入尚未落库的对话轮。

        ``batch_persisted`` 表示本批消息是否经 room.message 事件进了落库链路；
        调试注入传 False——即使开着场次，这些消息也不在 live_chat 里。
        """
        result = await self._rounds.execute(batch, forced=forced, trigger_reason=trigger_reason, proactive=proactive)
        await self._remember_round(batch, result, batch_persisted=batch_persisted)
        return result

    async def _remember_round(
        self,
        batch: List[RoomMessagePayload],
        result: Dict[str, Any],
        *,
        batch_persisted: bool,
    ) -> None:
        """决策轮结束后补记不会从 live_chat 读回的对话：观众先说、主播后答。"""
        chat_persisted = await self._chat_persisted()
        if not (batch_persisted and chat_persisted):
            for msg in batch:
                user = getattr(msg, "user", None)
                nickname = getattr(user, "name", None) or getattr(user, "id", None)
                timestamp = getattr(msg, "timestamp_ms", None)
                self._unpersisted_turns.append(
                    _LiveChatTurn(
                        role="viewer",
                        content=_text_field(msg, "content"),
                        sender_name=nickname if isinstance(nickname, str) else "",
                        message_type=_text_field(msg, "message_type") or "danmaku",
                        message_id=_text_field(msg, "message_id"),
                        timestamp_ms=timestamp if isinstance(timestamp, int) and timestamp > 0 else now_ms(),
                    )
                )
        speech = result.get("speech")
        # 开着场次时主播发言由 StorageLedger 写进 live_chat，这里只补没有落库的那一份
        if speech and not chat_persisted:
            self._unpersisted_turns.append(
                _LiveChatTurn(
                    role="assistant",
                    content=str(speech),
                    sender_name="主播",
                    message_type="speak",
                    timestamp_ms=now_ms(),
                )
            )

    async def _chat_persisted(self) -> bool:
        """当前对话是否会写进 live_chat 并能被历史读回（显式场次进行中且有聊天仓储）。"""
        if self._chat is None or self._session_manager is None:
            return False
        try:
            return await self._session_manager.resolve_pk() is not None
        except Exception as exc:
            self._logger.warning(f"解析当前场次失败（本轮对话按未落库记入内存）: {exc}", exc=exc)
            return False

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
    # 公开门面：主动发言动态开关（Dashboard 直播控制台）
    # ==================================================================

    def is_proactive_enabled(self) -> bool:
        """主动发言总开关当前状态（运行时实际值，供动态开关展示）。"""
        return self._proactive_trigger.is_enabled()

    def set_proactive_enabled(self, enabled: bool) -> None:
        """切换主动发言总开关（运行时立即生效；落盘由调用方负责）。"""
        self._proactive_trigger.set_enabled(enabled)

    # ==================================================================
    # 公开门面：供 Dashboard API 读取与控制流程单（实现收拢在协调器内）
    # ==================================================================

    def is_rundown_available(self) -> bool:
        """判定流程单是否对外可用（运行时已加载流程单）。"""
        return self._rundown_coord.is_available()

    def get_rundown_view(self, *, now_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """聚合 Dashboard 编排页所需的流程单视图数据（视图拼装见 rundown 子包）。"""
        return self._rundown_coord.get_rundown_view(now_ms=now_ms)

    async def rundown_control(
        self,
        action: str,
        *,
        segment_id: Optional[str] = None,
        now_ms: Optional[int] = None,
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """执行 Dashboard 手动控制动作（动作翻译见 rundown 子包）。"""
        return await self._rundown_coord.control(action, segment_id=segment_id, now_ms=now_ms)

    async def switch_rundown(self, rundown_id: str) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """直播中热切换流程单（Dashboard activate 的运行态写穿）。"""
        return await self._rundown_coord.switch(rundown_id)

    async def apply_rundown_definition(
        self,
        definition: Dict[str, Any],
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """应用编辑后的流程单定义（Dashboard 保存时的运行态写穿）。"""
        return await self._rundown_coord.apply_definition(definition)

    # ==================================================================
    # 历史读取（duck-typed 鸭子接口）
    # ==================================================================

    def _read_history_sync(self) -> "Coroutine[Any, Any, Any]":
        """reply_tool.history_provider 鸭子接口（返回 awaitable）。

        实际实现是返回 coroutine（不是同步 list），由 ReplyToolProvider 检测 awaitable 并 await。
        """
        return self._read_history()

    async def _read_history(self) -> Optional[List[Any]]:
        """读取当前对话并经时间线投影：前情提要各章 + 工作层原文，按主播看到的先后排列。

        显式场次进行中时，live_chat 是已落库对话的事实源；没开场次时的对话与
        调试注入的观众消息不会落库，由内存对话轮按发生时刻并入同一条时间线。
        读库失败返回 None（宁可本轮无历史，也不拿残缺历史冒充完整对话）。

        决策窗边界在此收口：待入账的游戏事件队列整批追加进时间线（本方法每轮
        决策先被调用一次，队列只在这里消费），本批行同时记为"本轮新入账批次"
        供决策执行器与状态事实块取用。
        """
        self._drain_game_events()
        stored: List[_LiveChatTurn] = []
        live_pk: Optional[int] = None
        if self._chat is not None and self._session_manager is not None:
            try:
                live_pk = await self._session_manager.resolve_pk()
                rows = (
                    await self._chat.list_recent_live_chat(live_session_id=live_pk, limit=None)
                    if live_pk is not None
                    else []
                )
            except Exception as exc:
                self._logger.warning(f"读取会话历史失败: {exc}")
                return None
            stored = [
                _LiveChatTurn(
                    role=row["sender_role"],
                    content=row["content"],
                    sender_name=row["sender_name"] or "",
                    message_type=row["message_type"],
                    message_id=row["message_id"] or "",
                    timestamp_ms=int(row["timestamp_ms"] or 0),
                )
                for row in rows
            ]
        if self._unpersisted_turns or self._timeline_notes:
            # 对话、未落库的对话轮与游戏记录按发生时刻合并；稳定排序保持同一时刻各自的先后。
            # 已进时间线的条目不再挪位置，排序只决定同一窗里新条目的先后
            stored = sorted(
                [*stored, *self._unpersisted_turns, *self._timeline_notes], key=lambda turn: turn.timestamp_ms
            )
        return await self._timeline.project(stored, scope=live_pk)

    # ==================================================================
    # 统计信息
    # ==================================================================

    def get_statistics(self) -> Dict[str, Any]:
        """获取运行时统计信息（结构向后兼容旧 get_statistics）。"""
        return self._stats.as_dict()
