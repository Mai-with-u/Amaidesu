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
)
await agent.start()
# Agent now: subscribes room.message.danmaku → buffers → planner → reply tool → ...
await agent.cleanup()
```
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections import deque
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Coroutine, Dict, Iterable, List, Literal, Optional, Sequence, Tuple

from src.modules.agents.base import AgentState, BaseAgent
from src.modules.agents.delegation_board import read_boards
from src.modules.agents.factory import AGENT_BINDINGS
from src.modules.background import get_registry as get_task_registry
from src.modules.background import setup_registry as setup_task_registry
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.core import CoreStartupPayload
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.body import BodyEventPayload
from src.modules.events.payloads.game_chat import GameChatPayload, GameSelfChattedPayload
from src.modules.llm.embedding import EmbeddingSetup
from src.modules.logging import get_logger
from src.modules.utils.task_utils import spawn_background_task
from src.modules.storage.repos._base import BaseRepo
from src.modules.storage.repos.memory_corrections import MemoryCorrectionRepo
from src.modules.tools import ToolSpec
from src.modules.tools.models import ToolInvocation
from src.modules.tools.tasks import (
    IN_PROGRESS_TASK_STATES,
    REPLY_NOTE_AT_KEY,
    REPLY_NOTE_KEY,
)
from src.modules.tools.registry import ToolRegistry
from src.modules.types.game_save import GameSave
from src.modules.types.game_situation import GameSituation
from src.modules.types.session_kind import SESSION_KIND_LIVE, SESSION_KINDS, SessionKind
from src.modules.utils.time_utils import now_ms
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.events.payloads.room_state import RoomStateWatchedPayload
from src.modules.events.payloads.base import BasePayload
from src.modules.events.payloads.streamer import StreamerPromptDeliveredPayload, StreamerPromptReceivedPayload
from src.modules.vision.image_store import get_image_store

from .rundown.rundown_coordinator import RundownCoordinator
from .tools.rundown_tool import build_rundown_tool_provider
from .background import SUMMARY_PROFILE, BackgroundMaintainer
from .decision_executor import DecisionRoundExecutor
from .session_scope import ClosingSession, SessionScope
from .memory.brief import build_brief
from .memory.cues import MemoryCueProvider
from .memory.index import MemoryIndexer
from .memory.last_wrapup import OPENING_WRAPUP_MAX_WINDOWS, build_last_wrapup
from .memory.recall import MemoryRecallService, build_memory_recall_provider
from .memory.search import MemorySearch
from .memory.settle import SessionSettler
from .message_buffer import MessageBuffer
from .delegation_workbench import PLANNER_CALL_SOURCE
from .delegation_stall_watch import DelegationStallWatch
from .mouth import SCENE_CHANGING_BODY_KINDS, URGENT_BODY_KINDS, URGENT_GAME_TYPES, Mouth
from .mouth_reconcile import ReconcileVerdict, SpokenLog, extract_player_join_name, reconcile_with_mouth
from .planner import Planner, PlannerConfig, PlannerEnv
from .paid_viewers import PaidViewerLookup
from .proactive_trigger import ProactiveTrigger
from .proactive_signals import ProactiveSignals
from .replyer import Replyer
from .room_state import RoomState
from .scene_card import brief_of
from .speech_dispatcher import SpeechDispatcher
from .speech_guard import WordFilter
from .game_ledger import GameLedger
from .game_now import AgentNow, GameNow, GameSideLog, goal_results_from_ledger
from .planner_render import Intent
from .stats import StreamerStats
from .talk.deck import TopicDeck
from .talk.profile import PersonaProfile, load_persona_profile
from .talk.speech_mode import SpeechMode, resolve_speech_mode
from .talk.workbench import TalkWorkbench
from .timeline import StreamerTimeline
from .trigger_cause import TriggerCause, TriggerKind, TriggerSource, cause_for_batch_flush
from .tools.memory_correct import MemoryCorrectionToolProvider, build_memory_correct_tool_spec
from .tools.reply_tool import ReplyToolProvider
from .tools.social_fact import SocialFactToolProvider, build_social_fact_tool_spec
from .tools.talk_cards_tool import build_talk_cards_tool_provider
from .command.router import CommandRouter
from .config import StreamerConfig
from .event_log_reader import EventLogFileReader

if TYPE_CHECKING:
    from src.modules.events.payloads.tasks import TaskChangedPayload
    from src.modules.subtitle import SubtitleService
    from src.modules.tts import TTSProvider

__all__ = ["StreamerAgent", "StreamerConfig"]

# 游戏 Agent 名：Agent 登记表里除主播自己以外的都是游戏（下播整理列"她能做的事"用；
# 是否"正在玩"另由事件与委派任务判断，见 _playing_games）
_GAME_AGENT_NAMES: tuple[str, ...] = tuple(name for name in AGENT_BINDINGS if name != "streamer")

# LLM profile 用途名（与 [llm_profiles.<name>] 三层结构对齐；model.toml 必填 6 成员）
_PROFILE_PLANNER = "planner"
_PROFILE_REPLYER = "replyer"

# 运营递话提醒队列上限（条）。满时拒收递话（receive_prompt 返回 False，
# REST 侧映射 409）——必达语义不允许静默挤掉旧条目。
_REMINDER_QUEUE_MAX = 5

# 续播重放递话的回扫上界（毫秒）：在窗口内找"上一次 core.startup"确定上一进程
# 窗口下界；找不到时退回扫描窗起点。正常重启间隔远小于该值，只是防扫描拖成灾
_PROMPT_REPLAY_SCAN_MS = 24 * 60 * 60 * 1000

# 续播只重放上一进程结束前这么久之内收到、又还没送进决策窗的递话（毫秒）：
# 更早的运营指令多半已经过时，重放出来让她照着做比丢了更糟
_PROMPT_REPLAY_RECENT_MS = 10 * 60 * 1000

# 下播整理前、Agent 停止前等播放队列播完队尾的上限（毫秒）。她最后几句（道别、"下次继续……"）
# 常排在队尾，先等它们播出去；TTS 卡住时等满这么久就照常往下走，不挡下播整理与停机
_SPEECH_TAIL_WAIT_MS = 30_000

# 下播收尾等本场整理跑完的上限（毫秒）：整理很慢时也放手执行清理，不让合规删除被模型拖住
_STREAM_END_SETTLE_WAIT_MS = 300_000

# 下播收尾等最后几句台词写库落完的上限（毫秒）
_STREAM_END_WRITE_WAIT_MS = 15_000

# "正在玩这个游戏"的判定窗（毫秒）：这么久没有这个游戏的事件就不算在玩
_GAME_ACTIVE_WINDOW_MS = 5 * 60 * 1000

# 收播道别那一轮正在结束的那一场（其余时候为 None）。只在道别那一轮自己的调用链里看得见：
# _execute_round 开头设上、跑完（含出错）复位。表达侧读历史（reply 工具 → _read_history）与
# 回想工具取场次类型都在这条调用链上（同一任务，或这一轮派生的任务——派生任务创建时复制上下文），
# 读得到这一场；下播瞬间并发的常规轮次、调试轮次、道别期间新开场次的回调各在自己的任务里，
# 读到的是 None，照常按进行中场次走。不挂在 Agent 实例上，正是为了不漏给这些并发轮次
_CLOSING_SESSION: ContextVar[Optional[ClosingSession]] = ContextVar("streamer_closing_session", default=None)

# 本轮开窗时所在场次的开播计数（开窗时没在直播、或这一轮是道别轮时为 None）。说完要出声前
# 拿它比对：开窗时的那一场已经下播（不在直播了，或已换成下一场），这句就作废——下播后只有
# 道别那一轮能开口。与 _CLOSING_SESSION 同理放在上下文变量里，并发的轮次各看各的
_ROUND_LIVE_EPOCH: ContextVar[Optional[int]] = ContextVar("streamer_round_live_epoch", default=None)


def _chat_speaker(payload: GameChatPayload) -> str:
    """这句游戏聊天是谁说的：核对过在线的玩家写 UUID，认不出是谁的玩家聊天写 "?"，系统消息为空。"""
    if payload.kind != "player":
        return ""
    return payload.sender_id if payload.sender_id and payload.sender_online else "?"


def _game_chat_line(payload: GameChatPayload) -> str:
    """游戏里别人说的一句话写成给决策看的一行：发言人 + 标注 + 原话 + 截断/刷屏说明。

    实时入账与续播重放共用这一份写法，私聊、归属说明（如"没有发言人凭据"）等标注两边逐字一致。
    """
    speaker = "系统消息" if payload.kind == "system" else f"玩家 {payload.sender or '（名字未知）'}"
    if payload.private:
        # 私聊标在发言人后面：决策看到这是只发给我的话，不在直播里念出或转述
        speaker += "（私聊，只发给我）"
    if payload.note:
        # 采集器的归属说明同样跟在发言人后面：模组借我名义发的机器消息不会被当成又一个玩家
        speaker += f"（{payload.note}）"
    note = "（原话过长已截断）" if payload.truncated else ""
    if payload.suppressed:
        note += f"（之前还有 {payload.suppressed} 条重复或刷屏的没转过来）"
    return f"{speaker}：{payload.content}{note}"


# ---------------------------------------------------------------------------
# StreamerAgent
# ---------------------------------------------------------------------------


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
    emits_events = (CoreEvents.RUNDOWN_CHANGED,)

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
        commitments_repo: Optional[Any] = None,
        archive_repo: Optional[Any] = None,
        memory_index_repo: Optional[Any] = None,
        memory_cue_log_repo: Optional[Any] = None,
        game_player_repo: Optional[Any] = None,
        social_fact_repo: Optional[Any] = None,
        talk_card_repo: Optional[Any] = None,
        embedding_setup: Optional[EmbeddingSetup] = None,
        event_repo: Optional[Any] = None,
        privacy_service: Optional[Any] = None,
        context_assembler_config: Optional[Any] = None,
        speech_config: Optional[Dict[str, Any]] = None,
        tts_engine: Optional["TTSProvider"] = None,
        subtitle_service: Optional["SubtitleService"] = None,
        session_manager: Optional[Any] = None,
        thinking_sink: Optional[Any] = None,
        task_ledger: Optional[Any] = None,
        stall_auto_cancel_alerts: int = 3,
        stream_ledger: Optional[Any] = None,
        planner: Optional[Planner] = None,
        round_executor: Optional[DecisionRoundExecutor] = None,
        reply_provider: Optional[ReplyToolProvider] = None,
        session_scope: Optional[SessionScope] = None,
        mouth: Optional[Mouth] = None,
        memory_cues: Optional[MemoryCueProvider] = None,
        events_dir: Optional[Path] = None,
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
            memory_index_repo: 可选 ``MemoryIndexRepo``（记忆检索的派生索引）——注入时后台
                索引任务把档案行、承诺、台词、游戏事件补进全文与向量索引；None 时不补算
            embedding_setup: 可选嵌入服务装配结果；None 或没配嵌入模型时索引只补全文
            game_player_repo: 可选 ``GamePlayerRepo``（游戏里的人）——玩家在游戏里开口或冲她做动作时，
                推一张"这位是谁、和你有过什么"的玩家卡；下播整理按它核对对玩家的承诺。缺失时没有玩家卡，
                对玩家的承诺也认不出。
            social_fact_repo: 可选 ``SocialFactRepo``（游戏社交事实）——注入时注册 ``streamer_social_fact``
                工具、参考段常驻【游戏社交事实】、回想工具多查这一类；缺失时三处都没有。
            memory_cue_log_repo: 可选 ``MemoryCueLogRepo``（线索卡记录）——被动想起每推一张卡记一行，
                复盘面板读；None 时只记日志
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
            stream_ledger: 可选 ``StorageLedger``（记账器）。注入时它把下播清理交给本
                Agent 托管：等道别轮落库、队尾台词播完、本场整理跑完，再由本 Agent
                调它执行到期清理与清空遮名。``None`` 时记账器按自己的时机清理，
                行为与注入前一致。
            planner: 可选 ``Planner`` 替身（决策核心）。``None`` 时按配置内建；
                注入时决策轮执行器与 Agent 共用同一实例（测试替 LLM/规划器
                写测试的注入面，不必穿透内部组装链）。
            round_executor: 可选 ``DecisionRoundExecutor`` 替身（决策执行半）。
                ``None`` 时按内建依赖装配；注入时整只替换（``execute`` 即
                测试可见的全部契约）。
            reply_provider: 可选 ``ReplyToolProvider`` 替身（reply 工具提供者）。
                ``None`` 时启动期照常构造；注入时启动注册复用同一实例不重建。
            session_scope: 可选 ``SessionScope`` 替身（场次作用域）。``None`` 时
                按内建依赖装配；注入时账本/时间线等以替身自带为准。
            mouth: 可选 ``Mouth`` 替身（嘴）。``None`` 且配置关闭时不构造；
                注入时忽略配置直接使用替身。
            memory_cues: 可选 ``MemoryCueProvider`` 替身（线索推送）。``None`` 时有承诺与对话仓储
                就按内建依赖装配；注入时直接使用替身。
        """
        super().__init__(event_bus=event_bus)
        self.typed_config = config
        self._llm = llm_manager
        # LLMManager 先于事件总线由组合根构造，挂载点在 Agent 侧：总线到手后
        # 递给它，"全部模型失败"才能旁路发布 core.error 告警（幂等，不覆盖已有总线）
        if llm_manager is not None and event_bus is not None:
            llm_manager.attach_event_bus(event_bus)
        self._prompt = prompt_manager
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        # 任务记录表引用：委派渲染的机器状态从这里查询（None 时 planner 降级只看交付语）
        self._task_ledger = task_ledger
        # 记账器（可选）：注入时接管下播清理时序（见 _finish_stream_end）
        self._stream_ledger = stream_ledger
        # 本场游戏活动：游戏名 → 最后一次事件时刻，以及本场出现过事件的游戏。
        # 游戏承诺卡只认"正在玩"（见 _playing_games），下播整理取"本场玩过"（见 _session_game_names）
        self._game_activity_ms: Dict[str, int] = {}
        self._session_games: set[str] = set()
        # 分存档的游戏眼下所在的存档（每个决策窗开窗前从工具面拉一次），与每个游戏上一次
        # 认出的存档：简报、线索卡、回想按前者分存档；换存档按后者认（中间回过主菜单也认得出）
        self._current_saves: Dict[str, GameSave] = {}
        self._known_saves: Dict[str, GameSave] = {}
        # 仓储注入（组合根按需分发；None 时对应能力降级。流程单三仓储由
        # RundownCoordinator 直接收取，不在 Agent 身上落字段；聊天仓储进
        # SessionScope 作只读事实源）
        self._session_manager = session_manager
        # 思考流旁路出口（可选；观察面专用，不进 EventBus 不落库）
        self._thinking_sink = thinking_sink
        # 观众统计仓储（Planner 注入画像时实时取昵称用；None 时回退 platform/user_id）
        self._viewer_repo = viewer_repo
        # 场次表：下播整理按场次主键取台词与状态
        self._sessions_repo = sessions_repo
        # 开播【上次收尾】注入闸门：新开场置位，头两窗或本场第一件委派受理后自动收闸
        self._opening_wrapup_active: bool = False
        self._opening_wrapup_windows: int = 0
        # 聊天仓储：开播日志统计"上一场播出了几句台词"用（None 时日志省略句数）
        self._chat_repo = chat_repo
        # 付费史标注器：查本批观众在付费三表有无记录（观众仓储 + 聊天仓储都在才装）
        self._paid_viewer_lookup = (
            PaidViewerLookup(viewer_repo=viewer_repo, chat_repo=chat_repo)
            if viewer_repo is not None and chat_repo is not None
            else None
        )
        # 账本两仓储（承诺 + 场次档案）：下播整理写入，常驻简报/线索卡/回想工具读取
        self._commitments_repo = commitments_repo
        self._archive_repo = archive_repo
        self._game_player_repo = game_player_repo
        # 场内记忆更正仓储：从已有 SQLite 账本仓储同库派生，供场内更正工具、运营常驻提醒与简报渲染共用
        _correction_cm = (
            archive_repo.connection_manager
            if isinstance(archive_repo, BaseRepo)
            else commitments_repo.connection_manager
            if isinstance(commitments_repo, BaseRepo)
            else sessions_repo.connection_manager
            if isinstance(sessions_repo, BaseRepo)
            else None
        )
        self._memory_correction_repo: Optional[MemoryCorrectionRepo] = (
            MemoryCorrectionRepo(_correction_cm)
            if _correction_cm is not None and (archive_repo is not None or commitments_repo is not None)
            else None
        )
        # 待落库的本场运营常驻提醒（receive_prompt 同步入口入队，下一窗重渲简报前异步落进 memory_corrections）
        self._pending_operator_notes: deque[str] = deque()
        # 游戏社交事实：场内工具登记、参考段每批现读、回想工具查（None 时三处都关）
        self._social_fact_repo = social_fact_repo
        self._event_repo = event_repo
        # 隐私服务：线索推送要按配置的短语表判定"忘了我"与撤回（缺失时不出回执行）
        self._privacy_service = privacy_service
        self._logger = get_logger("StreamerAgent")
        # 主播记忆整理的后台任务（启动补写整理、收播整理），强引用防 GC 中途回收。
        # 每个都含好几次 LLM 调用、可能跑几十秒，停止时直接取消不等：没做完的
        # 整理由下次启动的补写补完（流程单落库这类短写入归协调器，停止时等它做完）
        self._memory_tasks: set[asyncio.Task[Any]] = set()

        # ===== 内部子组件 =====
        # 弹幕聚合缓冲 + 强制触发判定
        self._buffer = MessageBuffer(
            batch_window_ms=config.batch.batch_window_ms,
            batch_max_size=config.batch.batch_max_size,
            enable_idle_compensation=config.batch.enable_idle_compensation,
            batch_max_wait_ms=config.batch.batch_max_wait_ms,
        )
        # 强制触发判定就是一次集合归属判定（消息类型 ∈ 强制名单），直接持有
        # 名单内联判定，不再为它单设类
        self._force_message_types = frozenset(config.force.force_message_types)
        # 房间态势（纯规则滑动窗口）与本场口头流水（区分嘴与决策出口，出声前对账与现场卡共用）
        self._room_state = RoomState()
        self._spoken_log = SpokenLog()

        # 流程单（Rundown）子系统：备忘录 + 闹钟（推进权归 Agent）。
        # 状态机与仓储/总线/场次解析的粘合收拢在协调器内（依赖全构造注入，
        # 环节变更经回调置位宿主的 proactive pending）
        self._rundown_coord = RundownCoordinator(
            config=config,
            rundown_repo=rundown_repo,
            rundown_instance_repo=rundown_instance_repo,
            rundown_transition_repo=rundown_transition_repo,
            session_manager=session_manager,
            event_bus=event_bus,
            logger=self._logger,
            notify_proactive=self._on_rundown_changed_proactive,
        )

        # 杂谈与自我表达三件内部件（均不进工具表，仅 talk_draw_cards 在启动期注册工具）：
        # 1) 结构化人物设定档案：本地 Markdown/TOML 解析，未配置或缺失时为 None
        self._persona_profile: Optional[PersonaProfile] = load_persona_profile(
            config.persona.profile_file,
            active_outfit=config.persona.active_outfit,
        )
        # 2) 话头工作台：跟踪当前环节提纲要点进度与被打断尾句，并给流程单防早退护栏供数
        self._talk_workbench = TalkWorkbench()
        self._rundown_coord.set_remaining_beats_provider(self._talk_workbench.remaining_beats)
        # 3) 自备话题匣：有话题卡仓储时按场次挑未用卡片预摆或供主动抽卡
        self._topic_deck: Optional[TopicDeck] = (
            TopicDeck(talk_card_repo, session_id_fn=self._current_session_pk, clock=now_ms)
            if talk_card_repo is not None
            else None
        )

        # 主动发言触发器（纯规则组件，Agent 内部件；先于 Planner 构造供冷场死锁判定查询）
        self._proactive_trigger = ProactiveTrigger(config.proactive)

        # Planner（决策核心，Agent 内部件——非工具）；测试可注入替身
        if planner is not None:
            self._planner = planner
        else:
            # Planner 运行环境：迟绑定依赖在构造期一次性注入（reply Provider 依赖
            # start 期才构造，经工厂化解生命周期错位）；live_frame 关闭时画面依赖缺席
            planner_env = PlannerEnv(
                reply_provider_factory=lambda: self._reply_provider,
                elapsed_live_provider=self._rundown_coord.get_elapsed_live_ms,
                brief_provider=lambda: self._memory_brief_text,
                brief_ids_provider=lambda: self._memory_brief_ids,
                brief_archive_ids_provider=lambda: self._memory_brief_archive_ids,
                used_archive_ids_provider=lambda: self._memory_used_archive_ids,
                commitment_lookup=self._lookup_commitment,
                archive_lookup=self._lookup_archive_line,
                memory_cues_provider=self._collect_memory_cues,
                open_games_provider=self._playing_games,
                action_recorder=self._record_delegation,
                delegation_status_provider=self._delegation_machine_status if task_ledger is not None else None,
                delegation_boards_provider=(lambda: read_boards(self._task_ledger))
                if task_ledger is not None
                else None,
                game_now_provider=self._game_now,
                # 付费史标注：查本批观众在付费三表有无记录，给决策侧的消息行加「（付费观众）」
                paid_identities_provider=self._paid_viewer_lookup.paid_identities
                if self._paid_viewer_lookup is not None
                else None,
                # 游戏社交事实：每批从库现读，参考段常驻【游戏社交事实】
                social_facts_provider=self._load_social_facts if social_fact_repo is not None else None,
                # 开播【上次收尾】：头两窗或第一件委派受理前从确定性账面拼装上一场收工实况
                opening_wrapup_provider=self._consume_opening_wrapup if sessions_repo is not None else None,
                # 嘴与决策双向对账：表达侧可见实时口头流水，出声前核对本轮嘴说的话并在矛盾时补最新现场
                recent_speeches_provider=self._spoken_log.recent_texts,
                mouth_reconciler=self._reconcile_with_mouth,
                fresh_context_since=self._fresh_context_since_open,
                # 说话档位、话头工作台、自备话题匣与主动沉默状态
                speech_mode_provider=self._current_speech_mode,
                talk_workbench_provider=self._render_talk_workbench_for_window,
                talk_cards_provider=self._refresh_talk_cards_for_window if self._topic_deck is not None else None,
                proactive_silent_provider=lambda: self._proactive_trigger.has_silent_cooldown,
            )
            if config.live_frame.enabled:
                planner_env = dataclasses.replace(
                    planner_env,
                    frame_provider=self._latest_live_frame,
                    frame_stale_warn_ms=config.live_frame.stale_warn_ms,
                )
            # 行为准则（behavior_style）只读包内配置权威 config.persona（决策侧通道）
            _behavior_style = config.persona.behavior_style or ""
            # context 组装器路径开关——直接读包内权威
            _context_enabled = config.context.enabled
            self._planner = Planner(
                config=PlannerConfig(
                    planner_max_steps=config.planner_max_steps,
                    planner_recent_intents_max=config.planner_recent_intents_max,
                    planner_recent_relays_max=config.planner_recent_relays_max,
                    reference_tools=list(config.reference_tools),
                    # 冷场提示复用主动发言的冷场阈值：同一个"多久算冷"只配一处
                    cold_silence_ms=config.proactive.cold_timeout_ms,
                ),
                llm_service=llm_manager,
                prompt_service=prompt_manager,
                room_state=self._room_state,
                tool_registry=tool_registry,
                viewer_repo=viewer_repo,
                context_enabled=bool(_context_enabled),
                behavior_style=_behavior_style,
                # 房管也要知道她是谁：挑话题、在回复指引里定她的态度都以人设为准；措辞风格仍只给表达侧
                bot_name=config.persona.bot_name,
                personality=config.persona.personality,
                # 边界栏三条通路同源：房管替她拿主意时也不答应线下见面、改称呼这类事
                boundaries=config.persona.boundaries,
                inside_jokes=config.persona.inside_jokes,
                persona_profile=self._persona_profile.render_for_planner() if self._persona_profile is not None else "",
                env=planner_env,
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
        # 人设三件套从包内配置权威 config.persona 单点构建、构造期注入
        # （behavior_style 不进表达侧——只走 Planner 决策通道）
        self._replyer = Replyer(
            config={
                "bot_name": config.persona.bot_name,
                "personality": config.persona.personality,
                "style_constraints": config.persona.style_constraints,
                # 人设块的口吻示例与边界栏：系统提示词以人设为主，后半场说话仍像她
                "voice_examples": config.persona.voice_examples,
                "boundaries": config.persona.boundaries,
                "persona_profile": (
                    self._persona_profile.render_for_replyer() if self._persona_profile is not None else ""
                ),
                # 表达出口的台词长度预算：边玩边说与杂谈两档，超出先重说仍超按完整句截短
                "speech_max_chars": config.speech_max_chars,
                "talk_speech_max_chars": config.talk_speech_max_chars,
                "has_game": self._has_enabled_game,
                # 表达侧只带最近几条原文（0 = 回退到整份历史）
                "history_items": config.replyer_history_items,
            },
            llm_service=llm_manager,
            prompt_service=prompt_manager,
            word_filter=self._word_filter,
        )

        # 后台维护器（双任务：轻循环 + 压缩 worker）——读包内权威配置
        self._background = BackgroundMaintainer(
            config.background,
            room_state=self._room_state,
            llm_service=llm_manager,
            sessions_repo=sessions_repo,  # live_sessions 实时状态落库（按 session_manager 解析的当前场次）
            chat_repo=chat_repo,  # 话题摘要读 live_chat 最近观众行
            session_manager=session_manager,  # 场次归属解析（None 时心跳降级跳过）
            event_bus=event_bus,
            topic_repo=topic_repo,  # 摘要落地：timeline_summary + topics 快照
            prompt_manager=self._prompt,  # 摘要/画像系统提示词渲染（复用 Agent 持有的 PromptManager）
        )

        # 后台任务登记处：进程级单例（下播整理与记忆索引补算共用一个面板数据源）。
        # 没装配事件总线就发不出变更事件，登记口保持 None——整理与补算照常跑，
        # 只是底栏任务面板看不见；已装配过则复用既有实例（查询端点读同一个单例）
        if event_bus is not None:
            task_registry = get_task_registry() or setup_task_registry(event_bus)
        else:
            # 总线都没装：注册不发事件也没有面板意义，登记口为 None
            task_registry = get_task_registry()

        # 下播整理：每场正式直播结束后把承诺与场次档案写进账本（账本唯一写入入口）。
        # 四个仓储或模型任一缺失就整体关闭，账本保持为空
        self._settler: Optional[SessionSettler] = None
        if (
            commitments_repo is not None
            and archive_repo is not None
            and sessions_repo is not None
            and chat_repo is not None
        ):
            self._settler = SessionSettler(
                sessions_repo=sessions_repo,
                chat_repo=chat_repo,
                commitments_repo=commitments_repo,
                event_repo=event_repo,
                # 滚动档案快照：整理时对上一份快照逐行挑留、去掉或替换，只引用原行
                archive_repo=archive_repo,
                llm_service=llm_manager,
                prompt_manager=prompt_manager,
                # 「她能做的事」是能力清单：启用的游戏 Agent 名，不含形象等非游戏工具面
                game_names_provider=self._enabled_game_names,
                # 旧场次现拼基底时数"时长够的场次"，与简报的"上一场"同一个下限
                min_session_ms=self.typed_config.memory_brief_min_session_ms,
                # 游戏里的人：对玩家的承诺按台词记下的回应对象、公屏点名与紧接着回话核对
                game_player_repo=game_player_repo,
                # 唠叨指标里线索卡那几项：推了几张、用上几张、只由她自己的台词触发的几张
                cue_log_repo=memory_cue_log_repo,
                # 第四次调用：更新游戏社交事实（没接仓储时不跑）
                social_fact_repo=social_fact_repo,
                # 后台任务面板：整理全程（取料/第 N 笔模型调用/写库）在这里可见
                task_registry=task_registry,
            )

        # 记忆检索索引：后台补算全文（必开）与向量（配置了嵌入服务才开）；整理写完时叫醒补一轮。
        # 混合检索与它共用同一个仓储和嵌入客户端，回想工具（以后还有被动想起）都走它
        # 线索卡记录：被动想起推出去的每张卡、用上的时刻（交给线索推送写）
        self._memory_cue_log_repo = memory_cue_log_repo
        self._memory_indexer: Optional[MemoryIndexer] = None
        self._memory_search: Optional[MemorySearch] = None
        if memory_index_repo is not None:
            setup = embedding_setup or EmbeddingSetup(client=None, note="语义检索没开：没有装配嵌入服务")
            self._memory_indexer = MemoryIndexer(
                repo=memory_index_repo,
                embedding=setup.client,
                embedding_note=setup.note,
                # 缺行总数超过门槛的补算（启动补算/换嵌入模型）登记进后台任务面板
                task_registry=task_registry,
            )
            self._memory_search = MemorySearch(repo=memory_index_repo, embedding=setup.client)

        # 常驻简报：按"上一场的档案 + 不针对人的未了结承诺"渲染，每窗按表快照重渲
        self._memory_brief_text: str = ""
        self._memory_brief_ids: tuple[int, ...] = ()
        # 常驻简报里标着编号的档案行（点名档案行的合法名单之一）
        self._memory_brief_archive_ids: tuple[int, ...] = ()
        # 本场已点名提过的档案行（随简报每窗重算）：Planner 拦住没人问起时的复提
        self._memory_used_archive_ids: tuple[int, ...] = ()
        # 线索推送：每批按弹幕与开着的游戏算"这一批想起来的事"；状态只活在本场
        self._memory_cues: Optional[MemoryCueProvider] = memory_cues
        if memory_cues is None and commitments_repo is not None and chat_repo is not None:
            self._memory_cues = MemoryCueProvider(
                commitments_repo=commitments_repo,
                chat_repo=chat_repo,
                viewer_repo=viewer_repo,
                privacy_service=privacy_service,
                current_session_provider=self._current_session_pk,
                previous_session_provider=self._previous_session_id,
                archive_repo=archive_repo,
                cue_log_repo=memory_cue_log_repo,
                game_player_repo=game_player_repo,
                inside_jokes=config.persona.inside_jokes,
                passive_enabled=self.typed_config.memory_passive_recall_enabled,
                search=self._memory_search,
                vector_enabled=self.typed_config.memory_passive_recall_vector_enabled,
                min_session_ms=self.typed_config.memory_brief_min_session_ms,
            )
        # 回想工具：她说过的话、档案行、不针对具体人的承诺、本场观众弹幕
        self._memory_recall: Optional[MemoryRecallService] = None
        if commitments_repo is not None and chat_repo is not None and archive_repo is not None:
            self._memory_recall = MemoryRecallService(
                chat_repo=chat_repo,
                commitments_repo=commitments_repo,
                archive_repo=archive_repo,
                privacy_service=privacy_service,
                viewer_repo=viewer_repo,
                current_session_provider=self._current_session_pk,
                event_repo=event_repo,
                saves_provider=lambda: self._current_saves,
                search=self._memory_search,
                social_fact_repo=social_fact_repo,
            )
        # 后台 flush 循环（Agent 主循环）
        self._flush_task: Optional[asyncio.Task] = None
        self._flush_lock = asyncio.Lock()
        self._running = False
        # 递话/外部触发到达即唤醒位：receive_prompt 与 trigger_external_proactive
        # 置位，flush 循环的等待立即醒来评估递话——递话不必陪弹幕批次等完整个 tick
        self._flush_kick = asyncio.Event()

        # 场次进行位：live.started 置位 / live.ended 复位。主动发言的业务边界
        # = 场次——没开播只回弹幕不主动开题，开场白（rundown opening）等开播
        self._live_active: bool = False
        # 开播计数：每收到一次 live.started 加一。下播回调要等道别那一轮跑完（等模型好几秒）
        # 才清场，这期间若已开了新场次，新场次的开播回调已经清过一次场、对话与备忘录都归
        # 新场次了；下播回调比对道别前后的计数，变了就不再清，免得把新场次刚记下的东西抹掉
        self._session_epoch: int = 0
        # 正在收尾的下播回调数（进回调加一、道别跑完减一）。开播回调据此判断内存里那份
        # 备忘录是开播前写下的（带进新场次），还是上一场还没来得及清掉的（不带）
        self._closings_in_flight: int = 0
        # 一次性主动触发信号（外部点播/流程单环节/游戏待定夺）：状态与
        # 消费纪律一处声明（game/rundown 确认消费、external 读即消费）
        self._proactive_signals = ProactiveSignals()

        # 委派停滞告警的决断兜底：同一委派告警计数，第 N 次仍无决断判自动撤单
        # （N 来自 [tools.tasks].stall_auto_cancel_alerts，经装配透传）
        self._stall_watch = DelegationStallWatch(auto_cancel_alerts=stall_auto_cancel_alerts)

        # 统计（决策循环各分支增量；对外经 get_statistics 导出）
        self._stats = StreamerStats()

        # 本场对话进 LLM 前的只追加投影：超预算时后台按章压成前情提要，两次换章之间
        # 历史前缀逐字不变。Replyer 只截最近几条原文，不再整份消费时间线，所以预算只按
        # Planner 的模型窗口换算；回退开关（replyer_history_items=0）让 Replyer 重新整份
        # 消费，预算随之改回取两个模型中较小的窗口，免得整份历史撑爆表达侧
        timeline_profiles = (
            (_PROFILE_PLANNER,) if config.replyer_history_items > 0 else (_PROFILE_PLANNER, _PROFILE_REPLYER)
        )
        self._timeline = StreamerTimeline(
            budget=config.context,
            llm_service=llm_manager,
            prompt_service=self._prompt,
            profiles=timeline_profiles,
            summary_profile=SUMMARY_PROFILE,
        )
        # 场次作用域：这一场记得什么（游戏账本、私聊行、未落库轮、时间线 notes、
        # 本窗批次、上场回顾快照）与决策窗开窗的单一承载。入账动词经 scope.ledger
        # 直打账本接口；出账/清场/读历史/补记走 scope 自己的动词（见 session_scope.py）。
        # 测试可注入替身（替身自带账本/时间线时以替身为准）
        self._scope = session_scope or SessionScope(
            ledger=GameLedger(),
            timeline=self._timeline,
            chat=chat_repo,
            session_manager=session_manager,
            logger=self._logger,
        )
        # 运营递话提醒队列（receive_prompt 入队；下个决策窗以【运营提醒】段
        # 注入参考块，读取即取空——送达一次制）。满时拒收，必达不允许静默挤旧。
        self._reminder_queue: deque[str] = deque(maxlen=_REMINDER_QUEUE_MAX)
        # 进程启动时刻（core.startup 到达即更新）：续播重放以它为界划上一进程窗口。
        # 构造时刻兜底——core.startup 在组件装配完成后才发，构造期先按本进程起步记
        self._startup_ms: int = now_ms()
        # 本进程那条 core.startup 的事件 id：落盘记录与订阅者拿到的是同一个 id，
        # 找"上一次启动"时据此排除本进程自己的记录（订阅者并发执行，记录器可能先落盘）
        self._startup_event_id: str = ""
        # 诊断事件流读取器（续播重放递话/玩家聊天的数据源；默认指向项目根 logs/events）
        self._event_log_reader = EventLogFileReader(events_dir if events_dir is not None else Path("logs") / "events")
        # 续播重放的玩家游戏发言（一次性缓冲：续播时装入重启前她可能还没回的那几句，
        # 下个决策轮注入【重启前可能还没回的话】段后取空，不重复）
        self._resume_chat_replay: list[str] = []
        # 游戏那边的流水：游戏 Agent 每步说的最近一句判断、她自己在游戏公屏说的话（【游戏那边此刻】用，场次边界清空）
        self._game_side = GameSideLog()

        # 观众命令接线（最小接线：玩法待扩展）。enabled + 任一映射表非空才激活；
        # 两张映射表（委派指令 / 工具调用）合起来即白名单，限频窗口/次数 config 化。
        # 命令路由是代码直连的内部件，不进 ToolRegistry。
        cmd_cfg = config.command
        self._command_router: Optional[CommandRouter] = None
        if cmd_cfg.enabled and (cmd_cfg.mappings or cmd_cfg.tool_mappings):
            self._command_router = CommandRouter(cmd_cfg, tool_registry, logger=self._logger)

        # 工具 Provider 实例（用于 invoke）；测试可注入替身，启动注册期复用不重建
        self._reply_provider: Optional[ReplyToolProvider] = reply_provider

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
        # 杂谈档连播段落被插话打断时，未播出的尾句交给话头工作台记下，下一轮空闲窗由她决定是否补完
        self._speech.bind_interrupted_tail_listener(self._talk_workbench.note_interrupted_tail)
        # 决策执行器（两阶段决策的执行半；调度半留 Agent）。测试可注入替身，
        # 契约 = ``execute(batch, *, forced, trigger_cause, proactive, closing_session)``；
        # 注入方经 ``set_reminders_provider`` 接上递话取空动词（内建构造路径
        # 走构造参数收取，两者写同一处状态）
        if round_executor is not None:
            round_executor.set_reminders_provider(self._drain_reminders)
            round_executor.set_resume_replay_provider(self._drain_resume_replay)
        self._rounds = round_executor or DecisionRoundExecutor(
            planner=self._planner,
            speech=self._speech,
            event_bus=event_bus,
            room_state=self._room_state,
            proactive_trigger=self._proactive_trigger,
            stats=self._stats,
            thinking_sink=thinking_sink,
            thinking_enabled=bool(getattr(config.thinking_stream, "enabled", True)),
            window_opener=self._scope.open_window,
            rundown_text_provider=self._rundown_coord.build_text,
            reminders_provider=self._drain_reminders,
            resume_replay_provider=self._drain_resume_replay,
            speech_allowed=self._round_session_still_live,
            mouth_reconciler=self._reconcile_with_mouth,
            on_mouth_superseded=self._on_mouth_superseded,
            on_round_spoken=self._on_round_spoken,
            logger=self._logger,
        )

        # 嘴（Agent 内部件，代码直连）：游戏里出事时不经 Planner 当场出一句短反应。
        # 注入替身优先；开关关闭时不构造，关闭与替身缺席路径行为一致（不说话）
        self._mouth: Optional[Mouth] = None
        if mouth is not None:
            self._mouth = mouth
        elif config.mouth.enabled:
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
                    # 游戏里出急事时脱口而出的一句同样守边界栏
                    "boundaries": config.persona.boundaries,
                },
                speech_max_chars=config.speech_max_chars,
                word_filter=self._word_filter,
                goals_provider=self._planner.workbench.current_goals,
                segment_provider=self._current_segment_title,
                scene_reader=self._read_scene_brief,
                is_live=lambda: self._live_active,
                profile=_PROFILE_REPLYER,
                recent_speeches_provider=self._spoken_log.recent_texts,
                on_mouth_spoken=self._on_mouth_spoken,
            )
        # 她说过的每句话（Planner 的与嘴自己的）经发声分发器直连记进本场口头流水、核销已欢迎名单并同步给嘴
        self._speech.add_spoken_listener(self._on_speech_emitted)

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

        # 收播整理善后：上次退出时没整理完的场次（没写回顾，或回顾写完后整理被打断），
        # 启动后在后台补完一场，补完再把长期记忆与"上场回顾"段读出来（开播前就能用）
        if self._settler is not None:
            spawn_background_task(
                self._bootstrap_recaps(),
                logger=self._logger,
                tasks=self._memory_tasks,
                label="StreamerAgent.recap_bootstrap",
            )

        # 记忆检索索引：第一轮就是启动补算，在后台跑，不挡开播
        if self._memory_indexer is not None:
            await self._memory_indexer.start()

        # 启动流程单（fail-soft：加载失败降级为无流程单）
        await self._rundown_coord.start()

        # 启动发言管线（speech → TTS / emotion → VTS；队列生命周期归 dispatcher）
        await self._speech.start()
        if self._mouth is not None:
            await self._mouth.start()

        # 记账器把下播清理交给本 Agent 托管：那一刻不能清遮名、也不能删观众——
        # 道别句与下播整理都排在 live.ended 之后（收尾见 _finish_stream_end）
        if self._stream_ledger is not None:
            try:
                self._stream_ledger.set_cleanup_on_live_ended(False)
            except Exception as exc:  # noqa: BLE001 - 接管失败只影响清理时序，不挡启动
                self._logger.warning(f"记账器下播清理托管失败（记账器仍按自己的时机清理）: {exc}", exc=exc)

        self._logger.info("StreamerAgent 已启动")

    async def _on_stop(self) -> None:
        """Agent 停止钩子。"""
        self._running = False

        # 嘴先停：它会往发声管线里塞台词
        if self._mouth is not None:
            await self._mouth.stop()

        # 停发声管线之前先等队尾播完（最多 30 秒）：关程序时 Agent 先停、live.ended 后到，
        # 她最后几句往往还排在播放队列里，直接停队列会把它们当没送达丢掉。嘴已停、
        # 不再塞新台词，这里只等已派发的那几句；TTS 卡住等满上限就照常停，剩下的标没送达
        await self._wait_speech_tail(before="停止发声管线")

        # 停止发言管线（TTS 队列先停，保证不遗留 invoke 在飞）
        await self._speech.stop()

        # 停止后台双任务
        try:
            await self._background.stop()
        except Exception as exc:
            self._logger.warning(f"停止 BackgroundMaintainer 失败: {exc}")

        # 停止记忆索引任务（没补完的行下次启动再补）
        if self._memory_indexer is not None:
            await self._memory_indexer.stop()

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

        # 收拢本 Agent 派出的后台任务，保证停止返回后不再有它发起的写库或 LLM 调用
        # （停机随后就关 LLM、关库）。排在嘴、发言管线、决策循环都停下之后：停止途中
        # 它们收尾时引起的流程单变更也已派出落库任务，一并等到。
        # 流程单落库与广播是短写入：等它做完，最后一次环节变更才进得了库
        await self._rundown_coord.stop()
        # 记忆整理含多次 LLM 调用：直接取消，不拖住停机
        await self._cancel_memory_tasks()

        # 把下播清理还给记账器：Agent 停了就没人再跑 _finish_stream_end，
        # 记账器在 live.ended 时按原时机清理；进程内的下一次启动清理也是兜底
        if self._stream_ledger is not None:
            try:
                self._stream_ledger.set_cleanup_on_live_ended(True)
            except Exception as exc:  # noqa: BLE001 - 交还失败不挡停机
                self._logger.warning(f"记账器下播清理交还失败（下次启动的清理兜底）: {exc}", exc=exc)

        # 资源清理契约：摘除本 Agent 注册的 provider（reply / rundown / memory）
        removed = self.unregister_tool_providers()

        self._logger.info(f"StreamerAgent 已停止（摘除 {removed} 个工具）")

    async def _cancel_memory_tasks(self) -> None:
        """取消在跑的记忆整理任务并等它们退出。

        停机退出时若有尚未跑完的下播整理任务，显式记录 warning 提示哪些场次未完成：
        已完成的模型调用步骤均已落入 settle_checkpoints，下次启动时从中断那一步接着跑。
        """
        inflight_sessions = self._settler.inflight_session_ids() if self._settler is not None else []
        cancelled = 0
        while self._memory_tasks:
            tasks = list(self._memory_tasks)
            for task in tasks:
                if task.cancel():
                    cancelled += 1
            # 任务完成先经回调移出集合，gather 返回时已退出的任务必已出集合
            await asyncio.gather(*tasks, return_exceptions=True)
        if inflight_sessions:
            joined = "、".join(f"#{sid}" for sid in inflight_sessions)
            self._logger.warning(
                f"进程退出时场次 {joined} 的下播记忆整理尚未跑完；已完成步骤断点已落库，下次启动将从断点续跑"
            )
        elif cancelled:
            self._logger.info(f"停止时取消了 {cancelled} 个记忆整理任务（没做完的整理下次启动从断点补完）")

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
        # 社交事实工具与场内记忆更正工具只在对应仓储可用时注册，声明口径同注册口径
        extra: List[ToolSpec] = []
        if self._social_fact_repo is not None:
            extra.append(build_social_fact_tool_spec())
        if self._memory_correction_repo is not None:
            extra.append(build_memory_correct_tool_spec())
        if self._reply_provider is not None:
            return [*self._reply_provider.list_tools(), *extra]

        # 预启动窗口：Provider 尚未构造（Agent 已 register 但未 start）
        from .tools.reply_tool import build_reply_tool_spec

        return [build_reply_tool_spec(), *extra]

    def _register_tools(self) -> None:
        """构造 reply Provider；reply / rundown_control / memory_* 注册进 ToolRegistry。

        名单口径（注册处声明）：
        - ``streamer_reply``：LLM 可调的真工具，注册 + 名单 ``["streamer"]``
          （自己的工具填自己）；Planner 经 registry 统一调用，本窗交接材料
          随 ``ToolInvocation.attachment`` 传入（不经实例槽位）。
        - ``rundown_control``：注册（provider="rundown"，名单 ``["streamer"]``）
          进 ToolRegistry，与其他工具同一路径被 Planner 调用；可见性随
          Provider 注册/摘除进出名单（Agent 启动即注册，无需二次开关）。
        - ``memory_recall``：回想工具（provider="memory"，名单 ``["streamer"]``），
          台词/承诺/档案三个仓储都注入时才注册；没注册时工具面里没有它，
          提示词里关于回想工具的条款照常渲染。
        - ``streamer_social_fact``：登记游戏社交事实（provider="streamer"，名单 ``["streamer"]``），
          社交事实仓储注入时才注册；没注册时 Planner 的工具列表里没有它。
        """

        # reply tool（构造注入的替身复用不重建；缺省时构造——注册与 Planner 侧引用共用同一实例）
        if self._reply_provider is None:
            self._reply_provider = ReplyToolProvider(
                replyer=self._replyer,
                # 没有聊天仓储时历史仍由内存对话轮承载，表达侧照样知道上一句说了什么
                history_provider=self._read_history_sync,
                rundown_text_provider=self._rundown_coord.build_text_sync,
                event_bus=self._event_bus,
            )
        # Planner 的 reply 调用经 registry；Provider 经 PlannerEnv 工厂迟解析，
        # 仅供引用/诊断（材料走 attachment）

        # proactive tool
        # 注册：reply 与 rundown_control（可见名单 ["streamer"]）；经基类入口
        # 登记归属，stop 路径逐一摘除（disable/重建不留残留工具）
        if self._tool_registry is not None:
            self.register_tool_provider(self._reply_provider, registry=self._tool_registry)
            self.register_tool_provider(
                build_rundown_tool_provider(self._rundown_coord.tool_provider),
                registry=self._tool_registry,
            )
            registered = "streamer_reply / rundown_control"
            # 回想工具：她说过的话、档案行、不针对具体人的承诺、本场观众弹幕（唯一的记忆工具）
            if self._memory_recall is not None:
                self.register_tool_provider(
                    build_memory_recall_provider(self._memory_recall),
                    registry=self._tool_registry,
                )
                registered += " / memory_recall"
            if self._social_fact_repo is not None:
                self.register_tool_provider(
                    SocialFactToolProvider(
                        repo=self._social_fact_repo,
                        session_provider=self._current_session_pk,
                        game_scope_provider=self._social_game_scope,
                        on_written=self._wake_memory_indexer,
                    ),
                    registry=self._tool_registry,
                )
                registered += " / streamer_social_fact"
            if self._memory_correction_repo is not None:
                self.register_tool_provider(
                    MemoryCorrectionToolProvider(
                        correction_repo=self._memory_correction_repo,
                        session_provider=self._current_session_pk,
                        brief_archive_ids_provider=lambda: self._memory_brief_archive_ids,
                        commitments_repo=self._commitments_repo,
                        event_repo=self._event_repo,
                        game_player_repo=self._game_player_repo,
                        chat_repo=self._chat_repo,
                        overview_provider=lambda: getattr(self._planner, "last_overview_text", ""),
                        on_written=self._refresh_memory_brief,
                    ),
                    registry=self._tool_registry,
                )
                registered += " / streamer_memory_correct"
            if self._topic_deck is not None:
                self.register_tool_provider(
                    build_talk_cards_tool_provider(self._topic_deck),
                    registry=self._tool_registry,
                )
                registered += " / talk_draw_cards"
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
        # 强制触发判定（类型集合归属，非 importance 数值）
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
        # 她自己在游戏公屏打出去的话（游戏那一面自己开口的也在内）：进时间线，她知道自己在游戏里说过什么
        self._event_bus.on(
            CoreEvents.GAME_SELF_CHATTED,
            self._on_self_chatted,
            model_class=GameSelfChattedPayload,
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
        # 进程启动完成（core.startup 在组件装配完成后发，本 Agent 已在订阅位）：
        # 记下本进程启动时刻，续播重放以它为界划上一进程窗口
        self._event_bus.on(
            CoreEvents.CORE_STARTUP,
            self._on_core_startup,
            model_class=CoreStartupPayload,
        )
        self._logger.info(
            "StreamerAgent 已订阅 room.message.danmaku|gift|super_chat|guard|enter / room.state.watched_count"
            " / game.* / game.body.* / game.chat.* / game.self.chatted / live.started|ended"
            " / core.startup"
        )

    async def _on_core_startup(
        self,
        event_name: str,
        payload: CoreStartupPayload,
        source: str,
    ) -> None:
        """``core.startup`` 回调：更新本进程启动时刻（续播重放的进程窗口边界）。

        启动事件的载荷不带时刻字段，按回调执行时的当前时刻记。
        """
        del event_name, source
        self._startup_ms = now_ms()
        self._startup_event_id = payload.id

    async def _on_live_started(
        self,
        event_name: str,
        payload: LiveStartedPayload,
        source: str,
    ) -> None:
        """live.started 回调：开播，放行主动发言，重置本场状态并拼第一份常驻简报。

        断线续播场（``resumed=True``）是新场开播的减法：不清场（当场的对话
        历史在库里，按场次主键接着读）、先还原流程单实例快照让中断的进度
        接着跑（还原失败退化为从头起算）、再排一轮接回发言告诉观众信号回来了。
        """
        del event_name, source
        resumed = payload.resumed
        # 开播计数先加：上一场的下播回调若还在等道别那一轮，跑完后据此知道已经换场、不再清场
        self._session_epoch += 1
        self._live_active = True
        # 新开场开启【上次收尾】头两窗闸门，放在第一个 await 之前，避免开窗循环抢在初始化前读到旧状态；
        # 断线续播是同一场接回，不作为新开场重复灌上一场收尾
        self._opening_wrapup_active = not resumed
        self._opening_wrapup_windows = 0
        # 上一场的下播回调还在收尾（道别那一轮在等模型）时，内存里这份备忘录是上一场的
        # 工作笔记（每次写入都已落进上一场的场次行），不是开播前写下的：当场清掉、不带进
        # 新场次。放在第一个 await 之前，新场次的第一轮决策开窗时读到的已经是空备忘录
        if self._mouth is not None:
            self._mouth.reset()
        # 断线续播先把流程单实例快照还原进状态机（启动时装载的是 armed 空单，
        # 那时场次还没接回、崩溃恢复必然落空）：还原成功后计时锚点非空，下面
        # begin 被结构化拒绝，中断的流程单接着跑；还原失败（无快照/不匹配）
        # 保持 armed，begin 照常起算，退化为从头跑
        if resumed:
            await self._rundown_coord.resume_restore()
        # 流程单开播起算：armed（已装载待开播）→ running，双锚点 = 开播时刻。
        # 已起算的场（崩溃恢复的进行中/已完成）被结构化拒绝，保持现态静默忽略；
        # begin 经 on_changed 置位 proactive pending——开场白信号在开播时刻才置
        reject = self._rundown_coord.begin(by="system")
        if reject is not None:
            self._logger.debug(f"开播起算流程单跳过（{reject.reason}）：流程单已在进行中")
        # 开播后对话改由 live_chat 记录；开播前的调试闲聊不混进这一场的历史。
        # 续播场不清场：当场对话历史在库里按场次主键接着读，内存本来就空；手头的
        # 事、补充要求、已聊话题同样只属于"上一场"，续播场没有上一场，一并保留
        if not resumed:
            await self._scope.begin_session()
            # 手头的事、补充要求、已聊话题只属于上一场：开播清空，新的一场不从"两小时前在游戏里
            # 开始做某事"接着说（上一场下播时游戏侧已结算委派）
            self._planner.workbench.reset()
            # 游戏里找她的玩家条目、游戏那边的流水同样只属于上一场：留几分钟的那份不带进新场次
            self._planner.reset_player_notes()
            self._game_side.reset()
            self._room_state.reset_session()
            self._spoken_log.reset()
            self._pending_operator_notes.clear()
            self._talk_workbench.sync_segment(None, [])
            if self._task_ledger is not None:
                self._task_ledger.clear_provider_outcomes()
        # 按场次类型先拼第一份简报（正式场接正式场、彩排场接彩排场）；此后每窗按表快照重渲。
        # 上一场若还没整理完，补整理排到后台跑，不阻塞开播；跑完后的下一个决策窗自然重渲出
        # 新简报，这期间简报退化为更早一场的档案
        self._sync_current_saves()
        await self._refresh_memory_brief(kind=payload.kind, announce=True)
        self._settle_pending_before_brief()
        # 线索推送的计数与回执追踪只在本场内有效，开播先清零
        if self._memory_cues is not None:
            self._memory_cues.reset()
        # 游戏活动也随场次重置：游戏承诺卡只看这一场"正在玩"什么，上一场的事件不带过来
        self._game_activity_ms.clear()
        self._session_games.clear()
        if resumed:
            self._logger.info(f"场次已续播（id={payload.live_session_id}）：中断场接回，主动发言放行")
            # 先从诊断事件流划出上一进程的时间段，递话与玩家聊天的重放共用这一段
            previous_window = self._previous_process_window()
            # 续播重放递话：上一进程里还没送进决策窗的运营留言随重启丢了，从诊断事件流
            # 补进提醒队列——接回那一轮（或下一轮）以【运营提醒】送达，必达跨重启
            self._replay_prompts_from_event_log(previous_window)
            # 续播重放玩家聊天：上一进程最后一轮决策之后玩家在游戏里说的话她还没接，
            # 随重启丢了；回读后暂存一次性缓冲，下个决策轮注入参考段（消费后不重复）
            self._load_resume_chat_replay(previous_window)
            # 续播接回已聊话题：本场前面几个进程里说出口的那几轮按先后重新记进打算账，
            # 早先讲过的段子（"更早聊过"）不随重启忘掉
            self._restore_spoken_topics(payload.live_session_id, payload.started_at_ms)
            # 续播认领委派：这一场重启前我派的、没交付完的那件记回手头的事并标成被打断，接回那一轮就看得到
            await self._restore_interrupted_delegations(payload.live_session_id)
        else:
            self._logger.info(f"场次已开启（id={payload.live_session_id}）：主动发言放行")
        if resumed:
            # 断线续播的接回发言窗：说一句"信号回来了，接着继续"（TriggerKind.RESUME
            # 情境文案管内容），不打断流程单、不重新开场。await 形态同道别窗：本回调
            # 由事件总线的后台任务驱动，保证这一轮赶在事件面存活的窗口内完整走完
            try:
                await self._execute_round(
                    [],
                    forced=True,
                    trigger_cause=TriggerCause(kind=TriggerKind.RESUME, source=TriggerSource.LIVE_START),
                    proactive=True,
                )
            except Exception as exc:  # noqa: BLE001 - 接回发言失败不挡续播链路
                self._logger.exception(f"场次 {payload.live_session_id} 的续播接回那一轮出错: {exc}")

    async def _restore_interrupted_delegations(self, live_session_id: int) -> None:
        """续播认领：这一场每个游戏里我最后派的那件，若是重启前受理、没交付完，就记回手头的事并标成被打断。

        重启后内存里的手头的事是空的，游戏那边也不会接着做原来的活：不记回来，她以为手上没事，
        观众问起答不上；记回来，【我手头在游戏里做的事】写着"重启前在做这件事，被打断了，做到哪不确定"。
        只读委派账：库里那件记成被打断由存储落库器在启动时写，这里不写库。两边不分先后：
        库里已记成被打断的、还挂着受理的（落库器还没收尾）都算。只认我自己派的（运营在 WebUI
        直派的调试指令不是她手头的事），也只看这一场（别的场次的事不带进来）。
        认领失败只省掉这一步，不挡续播。
        """
        if self._event_repo is None or not live_session_id:
            return
        try:
            records = await self._event_repo.list_session_delegations(live_session_id=live_session_id)
        except Exception as exc:  # noqa: BLE001 - 认领失败只少一条手头的事，不挡续播
            self._logger.warning(f"续播读委派账失败（手头的事不标被打断）: {exc}", exc=exc)
            return
        # 按受理先后排好的：同一个游戏后派的覆盖先派的，留下每个游戏我最后派的那件
        latest = {record.game: record for record in records if record.initiator == PLANNER_CALL_SOURCE}
        for record in latest.values():
            if record.started_ms >= self._startup_ms or record.status not in ("accepted", "interrupted"):
                continue
            self._planner.workbench.restore_interrupted(
                record.game, instruction=record.instruction, task_id=record.task_id, at_ms=record.started_ms
            )
            self._logger.info(f"续播认领：{record.game} 重启前的委派 {record.task_id} 记回手头的事，标成被打断")

    def _previous_process_window(self) -> Optional[Tuple[int, int]]:
        """上一进程在诊断事件流里的时间段：（它的 core.startup 时刻, 它最后一条记录的时刻）。

        起点是本进程启动前最近的一条 core.startup（本进程自己那条按事件 id 排除）；
        回扫窗内找不到时退回回扫窗起点。终点取这段里最后一条落盘记录——崩溃不发
        core.shutdown，这是能拿到的最接近"上一进程结束"的时刻；本进程装配期的
        记录也落在 core.startup 之前，混进来时终点偏向本进程启动，重放窗口只会更窄。
        诊断流缺席（目录缺失/没有记录）返回 None，重放降级为不做。
        """
        scan_start_ms = self._startup_ms - _PROMPT_REPLAY_SCAN_MS
        startups = [
            row
            for row in self._event_log_reader.read_records(
                event_name=CoreEvents.CORE_STARTUP,
                start_ms=scan_start_ms,
                end_ms=self._startup_ms,
            )
            if not self._startup_event_id or row.get("id") != self._startup_event_id
        ]
        prev_startup_ms = int(startups[-1]["timestamp_ms"]) if startups else scan_start_ms
        prev_end_ms = self._event_log_reader.latest_record_ms(start_ms=prev_startup_ms, end_ms=self._startup_ms)
        if prev_end_ms is None:
            return None
        return prev_startup_ms, prev_end_ms

    def _replay_prompts_from_event_log(self, previous_window: Optional[Tuple[int, int]]) -> None:
        """续播重放递话：上一进程结束前收到、却没来得及送进决策窗的递话补回提醒队列。

        对账口径是"收到 − 已送达"：按时间先后走一遍 streamer.prompt_received 与
        streamer.prompt_delivered，送达一条就划掉最早一条同文的收到记录，剩下的才是
        重启时还压在队列里的。只看上一进程结束前 ``_PROMPT_REPLAY_RECENT_MS`` 之内收到的；
        超过队列余量时留最新的几条，按收到先后入队；与当前队列或彼此同文的只补一次。
        重放是尽力而为的补偿：诊断流缺席降级为不重放，不抛错阻断续播。
        """
        if previous_window is None:
            return
        prev_startup_ms, prev_end_ms = previous_window
        since_ms = max(prev_startup_ms, prev_end_ms - _PROMPT_REPLAY_RECENT_MS)
        rows = [
            row
            for event_name in (CoreEvents.STREAMER_PROMPT_RECEIVED, CoreEvents.STREAMER_PROMPT_DELIVERED)
            for row in self._event_log_reader.read_records(
                event_name=event_name, start_ms=since_ms, end_ms=self._startup_ms
            )
        ]
        # 收到与送达合在一起按时刻排：送达只能划掉它之前收到的那几条
        rows.sort(key=lambda row: int(row.get("timestamp_ms", 0)))
        pending: list[str] = []
        for row in rows:
            data = row.get("data")
            if not isinstance(data, dict):
                continue
            if row.get("event_name") == CoreEvents.STREAMER_PROMPT_RECEIVED:
                content = str(data.get("content", "")).strip()
                if content:
                    pending.append(content)
                continue
            for delivered in data.get("contents") or []:
                delivered_text = str(delivered).strip()
                if delivered_text in pending:
                    pending.remove(delivered_text)
        # 从最新往回挑，直到队列余量用完；同文只补一次，已在当前队列里的不补
        room = _REMINDER_QUEUE_MAX - len(self._reminder_queue)
        seen = set(self._reminder_queue)
        chosen: list[str] = []
        for content in reversed(pending):
            if content in seen:
                continue
            seen.add(content)
            if len(chosen) >= room:
                self._logger.warning("续播重放递话时提醒队列余量不足，更早的几条递话不再重放")
                break
            chosen.append(content)
        if not chosen:
            return
        # 挑的时候是从新到旧，入队按收到先后
        self._reminder_queue.extend(reversed(chosen))
        self._logger.info(f"续播重放：已把上一进程没送出去的 {len(chosen)} 条递话补进提醒队列")
        self._flush_kick.set()

    def _restore_spoken_topics(self, live_session_id: int, started_at_ms: int) -> None:
        """续播接回已聊话题：本场在前面几个进程里说出口的那几轮，按先后重新记进打算账。

        已聊话题（最近几轮与"更早聊过"）只在内存里，程序重启后整份没了，续播场她会把这一场
        早先讲过的段子又当新话题讲。本场每轮决策都写进了诊断事件流（planner.decision，带话题短名、
        话题摘要、指引和说出口的台词），按场次编号挑出说出口的那几轮依次重放：最近几轮放满后更早的
        自然挪进"更早聊过"，和没重启时同一套规则。开播时刻不早于本进程启动时（载荷没带原场次的
        开播时刻）退回回扫窗起点。诊断流缺席或读不了时降级为不接回，不阻断续播。
        """
        start_ms = started_at_ms if 0 < started_at_ms < self._startup_ms else self._startup_ms - _PROMPT_REPLAY_SCAN_MS
        try:
            rows = self._event_log_reader.read_records(
                event_name=CoreEvents.PLANNER_DECISION, start_ms=start_ms, end_ms=self._startup_ms
            )
        except Exception as exc:  # noqa: BLE001 - 接回是尽力而为，读不了诊断流就不接回
            self._logger.warning(f"续播读不了诊断事件流，已聊话题不接回: {exc}", exc=True)
            return
        restored = 0
        for row in rows:
            data = row.get("data")
            if not isinstance(data, dict) or data.get("live_session_id") != live_session_id:
                continue
            # 只接回说出口的那几轮：没开口、表达失败的不算聊过
            speech = str(data.get("speech") or "").strip()
            if not speech:
                continue
            self._planner.workbench.note_intent(
                Intent(
                    at_ms=int(row.get("timestamp_ms") or 0),
                    topic_summary=str(data.get("topic_summary") or ""),
                    reply_guidance=str(data.get("reply_guidance") or ""),
                    topic_tag=str(data.get("topic_tag") or ""),
                    speech=speech,
                )
            )
            restored += 1
        if restored:
            self._logger.info(f"续播接回已聊话题：本场前面说出口的 {restored} 轮重新记进打算账")

    def _load_resume_chat_replay(self, previous_window: Optional[Tuple[int, int]]) -> None:
        """续播重放玩家聊天：只补上一进程里她还没接过的那段尾巴，存一次性缓冲。

        上一进程活着的时候游戏聊天照常进决策窗、她基本都回过了；真正没接的，是上一进程
        最后一轮台词确实播出了的决策开窗之后才到的那几句（界线取开窗时刻而不是她开口的
        时刻：她回上一句时刚到的话还没进那一窗）。窗口 = 从那一刻到本进程启动，且不早于
        上一进程结束前 ``resume_replay_window_ms``；这段里找不到这样的一轮时兜底为整段
        ``resume_replay_window_ms``，那几句里可能有她已经回过的，提示词标题照实写明。
        取自诊断事件流的 game.chat.received（kind=player），一行的写法与实时入账同一份
        （含归属说明）。私聊不重放——当众念出私聊是隐私事故，比实时路径从严。
        诊断流缺席降级为不重放，不抛错阻断续播。
        """
        window_ms = self.typed_config.resume_replay_window_ms
        if window_ms <= 0 or previous_window is None:
            return
        prev_startup_ms, prev_end_ms = previous_window
        since_ms = max(prev_startup_ms, prev_end_ms - window_ms)
        # 先划出上一进程最后几分钟，再在这段里找最后一轮台词播出过的决策开窗时刻：
        # 找到了就只补它之后的尾巴；找不到就整段补，并记一笔兜底原因
        aired_open_ms = self._last_aired_round_open_ms(since_ms)
        if aired_open_ms is not None:
            since_ms = max(since_ms, aired_open_ms)
        else:
            self._logger.info(
                "续播重放：上一进程结束前这段里找不到台词播出过的决策轮（没开 TTS、没落盘或这段没开口），"
                f"按结束前 {window_ms // 1000} 秒整段补玩家聊天，其中可能有她已经回过的"
            )
        records = self._event_log_reader.read_records(
            event_name=CoreEvents.GAME_CHAT_RECEIVED,
            start_ms=since_ms,
            end_ms=self._startup_ms,
        )
        lines: list[str] = []
        for row in records:
            data = row.get("data")
            if not isinstance(data, dict) or data.get("kind") != "player" or data.get("private"):
                continue
            try:
                payload = GameChatPayload.model_validate(data)
            except ValueError as exc:
                self._logger.debug(f"续播重放跳过一条无法解析的游戏聊天记录: {exc}", exc=True)
                continue
            if not payload.content.strip():
                continue
            ts = int(row.get("timestamp_ms", 0))
            time_text = datetime.fromtimestamp(ts / 1000).strftime("%H:%M") if ts > 0 else "--:--"
            lines.append(f"{time_text} {_game_chat_line(payload)}")
        if lines:
            self._resume_chat_replay = lines
            self._logger.info(f"续播重放：已暂存重启前她可能还没回的 {len(lines)} 条玩家发言，下个决策轮注入")

    def _last_aired_round_open_ms(self, start_ms: int) -> Optional[int]:
        """从 ``start_ms`` 到本进程启动，最后一轮台词确实播出了的决策是什么时候开窗的；没有返回 None。

        一轮决策开头发 streamer.stage(planning)，开窗那一刻账本里攒着的游戏聊天都被这一窗
        取走；收尾的 planner.decision 带这一轮台词的 utterance_id；TTS 开始出声时发同一个
        utterance_id 的 tts.utterance.started。三者对上才算她看过这批话并且开口回了。
        静默收场、模型出错、台词还没播进程就崩了的轮次都不算。播放可能早于决策记录落盘，
        所以三类记录先全部收齐再对账，不依赖先后。
        """
        opened: dict[str, int] = {}
        utterance_round: dict[str, str] = {}
        aired: set[str] = set()
        for row in self._event_log_reader.read_records(
            event_name=(CoreEvents.STREAMER_STAGE, CoreEvents.PLANNER_DECISION, CoreEvents.TTS_UTTERANCE_STARTED),
            start_ms=start_ms,
            end_ms=self._startup_ms,
        ):
            data = row.get("data")
            if not isinstance(data, dict):
                continue
            event_name = row.get("event_name")
            round_id = str(data.get("round_id") or "")
            utterance_id = str(data.get("utterance_id") or "")
            # 开窗记时刻 → 决策记录把台词挂到这一轮 → 播放记录说明这句真的出声了
            if event_name == CoreEvents.STREAMER_STAGE and data.get("stage") == "planning" and round_id:
                opened[round_id] = int(row.get("timestamp_ms", 0))
            elif event_name == CoreEvents.PLANNER_DECISION and round_id and utterance_id:
                utterance_round[utterance_id] = round_id
            elif event_name == CoreEvents.TTS_UTTERANCE_STARTED and utterance_id:
                aired.add(utterance_id)
        aired_opens = [
            opened[round_id]
            for utterance_id, round_id in utterance_round.items()
            if utterance_id in aired and round_id in opened
        ]
        return max(aired_opens) if aired_opens else None

    def _drain_resume_replay(self) -> str:
        """取空续播重放缓冲（决策执行器在决策窗入口调用；注入一次制）。"""
        if not self._resume_chat_replay:
            return ""
        text = "\n".join(self._resume_chat_replay)
        self._resume_chat_replay.clear()
        return text

    async def _on_live_ended(
        self,
        event_name: str,
        payload: LiveEndedPayload,
        source: str,
    ) -> None:
        """live.ended 回调：下播，主动发言收闸，道别收束本场，后台跑这次的下播整理。"""
        del event_name, source
        self._live_active = False
        self._opening_wrapup_active = False
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
        # 被新开的场次顶掉（如彩排直接转正式开播）不是真下播：紧接着就是下一场
        # 的开播，道别要好几秒才生成完，会落进新场次、在正式直播开头说"今天就到
        # 这儿"，所以不道别，直接收拾本场
        # 场次行已删或马上要删的两种情形：空场次（一条消息都没有，结账时整行丢弃），以及
        # Dashboard 删除进行中场次（场次管理器先结束这一场、发完 live.ended 紧接着删行）。
        # 这两种都不交场次上下文、不排收播整理，下面两处共用这一个判断
        session_row_gone = payload.empty_discarded or payload.deleted
        # 记下道别前的开播计数，并登记"有一场正在收尾"：道别那一轮要等模型好几秒，期间新开的
        # 场次由它自己的开播回调接管对话与备忘录，道别跑完后据此决定这里还清不清场
        epoch = self._session_epoch
        self._closings_in_flight += 1
        try:
            if self._running and not payload.superseded:
                # 场次管理器先把这一场写成已结束、清掉进行中场次，才发 live.ended，道别这一轮里
                # "当前场次"已解析不到：把正在结束的这一场显式交下去，她按这一场读最后的对话、
                # 道别句记进这一场、彩排场的道别仍按彩排算。场次行已删或将删时不交：道别要等
                # 模型好几秒才说出口，那时行早没了，交了会给道别句盖上已删场次的编号、在库里留下
                # 孤儿行；这时道别照旧运行（她照样说再见），只是这句不落库
                closing = None if session_row_gone else self._closing_session_of(payload)
                try:
                    await self._execute_round(
                        [],
                        forced=True,
                        trigger_cause=TriggerCause(kind=TriggerKind.SIGNOFF, source=TriggerSource.LIVE_END),
                        proactive=True,
                        closing_session=closing,
                    )
                except Exception as exc:  # noqa: BLE001 - 道别失败不能挡住清场与收播整理
                    # 道别没说出来只少一句再见；要是让异常穿出去，下面的清场与收播整理全被跳过，
                    # 这一场的对话留在内存里，下一场开播会把它当成开播前写下的带进去
                    self._logger.exception(
                        f"场次 {payload.live_session_id} 的道别那一轮出错，照常清场与收播整理: {exc}"
                    )
        finally:
            self._closings_in_flight -= 1
        if self._session_epoch != epoch:
            # 道别那几秒里已开了新场次：新场次的开播回调已清过一次场（对话轮、游戏账本、时间线
            # 归零），也已把这一场的备忘录清掉；此刻内存里的东西都是新场次刚记下的，再清就会
            # 抹掉新场次的开场内容
            self._logger.info(
                f"场次 {payload.live_session_id} 道别期间已开新场次，内存里的对话与备忘录归新场次，不再清场"
            )
        else:
            # 下播即结束这一场的对话记忆，下播后的调试对话重新从空白开始记
            # （道别句已带着这一场的编号落库，随场次留档，不再留在内存对话里）
            await self._scope.end_session()
            # 道别说完再清玩家条目与游戏那边的流水：下播后的调试对话不接着这一场的玩家和游戏说
            self._planner.reset_player_notes()
            self._game_side.reset()
            self._room_state.reset_session()
            self._spoken_log.reset()
            self._pending_operator_notes.clear()
            self._talk_workbench.sync_segment(None, [])
            if self._task_ledger is not None:
                self._task_ledger.clear_provider_outcomes()
        # 收播收尾：排进后台，依次是"等队尾台词播完 → 本场整理（有上限）→ 记账器清理"。
        # 场次行已删或将删时不整理（空场次没东西可整理，用户删掉的场次本意是清掉它），
        # 但后面的清理照跑：待删观众与保留期清理不依赖场次行。
        if self.state in (AgentState.STOPPING, AgentState.STOPPED):
            # 应用停机路径：Agent 先停、live.ended 后到，LLM 与数据库紧接着就关，
            # 整理跑不完还白调一次模型，干脆不起；这场该做的删除由下次启动的清理
            # 与 24 小时宽限删除兜底
            self._logger.info(f"Agent 已停止，场次 {payload.live_session_id} 的收播整理与下播清理留给下次启动补上")
            return
        spawn_background_task(
            self._finish_stream_end(
                payload.live_session_id,
                settle=not session_row_gone and self._settler is not None,
                epoch=epoch,
            ),
            logger=self._logger,
            tasks=self._memory_tasks,
            label="StreamerAgent.finish_stream_end",
        )

    @staticmethod
    def _closing_session_of(payload: LiveEndedPayload) -> ClosingSession:
        """从 live.ended 载荷取出正在结束的那一场：编号、类型，开播时刻由结束时刻减时长推得。"""
        started_at_ms = payload.ended_at_ms - payload.duration_ms if payload.duration_ms is not None else None
        return ClosingSession(
            live_session_id=payload.live_session_id,
            kind=payload.kind,
            started_at_ms=started_at_ms,
        )

    async def _wait_speech_tail(self, *, before: str) -> None:
        """等播放队列把已派发的台词播完再往下走，最多等 ``_SPEECH_TAIL_WAIT_MS``。

        下播整理前与 Agent 停止前共用：她最后几句（道别、"下次继续……"）常排在队尾，先让
        它们真的播给观众，整理时这几句已经说出口，停队列也不会把它们当没送达丢掉。TTS 关着
        时没有播放队列，立即返回。等满上限仍没播完（TTS 卡住）记 warning、照常往下走：
        下播整理不被挡住，停机时剩下的句子随队列停止标没送达。
        """
        try:
            idle = await self._speech.wait_idle(timeout_ms=_SPEECH_TAIL_WAIT_MS)
        except Exception as exc:  # noqa: BLE001 - 等不等队尾都不能挡住整理与停机
            self._logger.warning(f"{before}前等播放队列播完出错，不再等、照常继续: {exc}", exc=exc)
            return
        if not idle:
            self._logger.warning(
                f"{before}前等播放队列播完：等了 {_SPEECH_TAIL_WAIT_MS // 1000} 秒仍没播完（TTS 可能卡住），照常继续"
            )

    async def _finish_stream_end(self, live_session_id: int, *, settle: bool, epoch: int) -> None:
        """下播收尾：等队尾播完 → 本场整理（有上限）→ 记账器清理并清空遮名。

        清理必须排在最后：道别句要在遮名集合还在时写库，下播整理要读得到本场台词、
        且不能读到已删的观众。``settle=False`` 表示这一场没有可整理的（场次行已删或没配
        整理），仍要等队尾再清理。整理超过上限就放手照样清理：清理晚做会拖住合规删除，
        早于整理完成最多让这一场的整理读到已删观众（已知边界，宽限删除仍有 24 小时兜底）。
        """
        try:
            await self._wait_speech_tail(before=f"下播收尾场次 {live_session_id} ")
        except Exception as exc:  # noqa: BLE001 - 等不等队尾都要收尾
            self._logger.warning(f"下播收尾前等队尾出错（照常收尾）: {exc}", exc=exc)
        if settle:
            await self._settle_this_session(live_session_id)
        if self._stream_ledger is None:
            return
        try:
            await self._stream_ledger.wait_writes_idle(timeout_ms=_STREAM_END_WRITE_WAIT_MS)
            # 道别期间已开新场次时不清遮名：新场次刚记下的遮名不能被旧场次的收尾抹掉
            await self._stream_ledger.finish_stream_end(clear_masks=self._session_epoch == epoch)
        except Exception as exc:  # noqa: BLE001 - 清理失败记日志，下次启动的清理兜底
            self._logger.error(
                f"场次 {live_session_id} 的下播清理失败（下次启动的清理与 24 小时兜底补上）: {exc}",
                exc=exc,
            )

    async def _settle_this_session(self, live_session_id: int) -> None:
        """整理这一场（最多等 ``_STREAM_END_SETTLE_WAIT_MS``）；失败、超时都只记日志，不挡清理。"""
        if self._settler is None:
            return
        try:
            async with asyncio.timeout(_STREAM_END_SETTLE_WAIT_MS / 1000):
                settled = await self._settler.settle_session(live_session_id)
        except asyncio.TimeoutError:
            self._logger.warning(
                f"场次 {live_session_id} 的下播整理超过 {_STREAM_END_SETTLE_WAIT_MS // 1000} 秒未结束，先执行下播清理"
            )
            return
        except Exception as exc:  # noqa: BLE001 - 整理异常不挡清理
            self._logger.warning(f"场次 {live_session_id} 的下播整理出错（照常清理）: {exc}", exc=exc)
            return
        if not settled:
            self._logger.info(f"场次 {live_session_id} 的下播整理未完成（失败或跳过），照常执行下播清理")
            return
        # 本场新写的档案行与承诺马上补进索引，下一场开播就能查到
        self._request_index_run()

    def _request_index_run(self) -> None:
        """整理写完后叫醒记忆索引任务补一轮（没装配索引时什么也不做）。"""
        if self._memory_indexer is not None:
            self._memory_indexer.request_run()

    # ==================================================================
    # 主播记忆：常驻简报 / 线索推送 / 回想工具 / 下播整理
    # ==================================================================

    @property
    def memory_brief_text(self) -> str:
        """本场常驻简报的正文（空串 = 还没有可记的事）。

        每个决策窗按台账表快照重渲：表没变时逐字节相同（系统提示词前缀缓存仍命中），
        直播中运营者纠错、上一场整理刚写完，下一个窗口就反映出来。
        """
        return self._memory_brief_text

    @property
    def memory_brief_commitment_ids(self) -> Tuple[int, ...]:
        """常驻简报里出现的承诺编号（Planner 点名的合法名单之一，观测与测试用）。"""
        return self._memory_brief_ids

    @property
    def memory_brief_archive_ids(self) -> Tuple[int, ...]:
        """常驻简报里标着编号的档案行（Planner 点名的合法名单之一，观测与测试用）。"""
        return self._memory_brief_archive_ids

    @property
    def memory_used_archive_ids(self) -> Tuple[int, ...]:
        """本场已点名提过的档案行（Planner 据此拦住没人问起时的复提；观测与测试用）。"""
        return self._memory_used_archive_ids

    def _current_session_pk(self) -> Optional[int]:
        """进行中场次主键；收播道别那一轮取正在结束的那一场（无场次管理器或未开播为 None）。

        道别那一轮里场次管理器已经把进行中场次清空，而回想工具的"本场"、线索推送的
        场次、常驻简报的"上一场"都还要按这一场算；这一场只在道别轮自己的调用链里
        看得见（见 ``_CLOSING_SESSION``），并发轮次照常取进行中场次。
        """
        closing = _CLOSING_SESSION.get()
        if closing is not None:
            return closing.live_session_id
        if self._session_manager is None:
            return None
        pk = getattr(self._session_manager, "active_pk", None)
        return int(pk) if isinstance(pk, int) else None

    def _enabled_game_names(self) -> List[str]:
        """启用了的游戏 Agent 名（能力清单：这一场她玩得了哪些游戏）。

        取"注册表里有使用说明的工具面"与"Agent 登记表里的游戏 Agent"的交集：
        形象这类带说明但不是 Agent 的工具面不在交集里。与 ``_playing_games`` 的分工——
        这一份是能力（没玩过的游戏也能承诺下次玩），那一份是当下是否正在玩。
        """
        if self._tool_registry is None:
            return []
        registered = {provider for provider, _guide in self._tool_registry.guides(for_agent="streamer")}
        return sorted(registered.intersection(_GAME_AGENT_NAMES))

    @property
    def talk_workbench(self) -> TalkWorkbench:
        """话头工作台（只读观测入口）。"""
        return self._talk_workbench

    @property
    def topic_deck(self) -> Optional[TopicDeck]:
        """自备话题匣（未注入话题卡仓储时为 None）。"""
        return self._topic_deck

    @property
    def current_speech_mode(self) -> SpeechMode:
        """当前说话档位（"play" 边玩边说 / "talk" 杂谈唠嗑）。"""
        return self._current_speech_mode()

    def _has_enabled_game(self) -> bool:
        """本场是否挂有游戏（启用了游戏工具面，或本场已出现过游戏动静/在途委派）。"""
        return bool(self._enabled_game_names() or self._session_games or self._playing_games())

    def _is_game_waiting(self) -> bool:
        """在途游戏委派是否全部处于停滞或等待定夺（游戏暂时没法推进时切杂谈档撑场）。"""
        if self._task_ledger is None:
            return False
        has_active = False
        for task_id in self._task_ledger.active_task_ids():
            record = self._task_ledger.get(task_id)
            if record is None or record.status not in IN_PROGRESS_TASK_STATES:
                continue
            has_active = True
            snap = record.snapshot if isinstance(record.snapshot, dict) else {}
            if not snap.get("stalled_goals") and record.status != "waiting":
                return False
        return has_active

    def _current_speech_mode(self) -> SpeechMode:
        """按当前流程单环节模式、游戏启用与失联等待判定说话档位。"""
        return resolve_speech_mode(
            segment_mode=self._rundown_coord.current_segment_mode(),
            game_active=self._has_enabled_game(),
            game_waiting=self._is_game_waiting(),
        )

    def _sync_talk_workbench(self) -> None:
        """同步话头工作台到当前流程单环节（同环节同提纲幂等，换环节自动重置）。"""
        segment = self._rundown_coord.current_segment()
        if segment is None:
            self._talk_workbench.sync_segment(None, [])
        else:
            self._talk_workbench.sync_segment(segment.id, list(segment.beats))

    def _is_card_friendly_segment(self) -> bool:
        """当前环节或当前提纲要点是否属于提问箱/话题卡/自由闲聊环节。"""
        segment = self._rundown_coord.current_segment()
        if segment is None:
            return False
        haystack = f"{segment.id} {segment.title} {segment.task_description}"
        remaining = self._talk_workbench.remaining_beats(segment.id, list(segment.beats))
        if remaining:
            haystack += " " + remaining[0]
        return any(hint in haystack for hint in ("提问箱", "棉花糖", "话题卡", "话题匣", "qna", "快问快答", "自由闲聊"))

    def _render_talk_workbench_for_window(self, has_viewer_batch: bool) -> str:
        """为本决策窗渲染【当前话头与本节提纲】；空闲窗读完即清尾句提醒，避免重复唠叨。"""
        self._sync_talk_workbench()
        if self._current_speech_mode() != "talk":
            return ""
        rendered = self._talk_workbench.render()
        if rendered and not has_viewer_batch:
            self._talk_workbench.consume_interrupted_tail()
        return rendered

    async def _refresh_talk_cards_for_window(self, has_viewer_batch: bool) -> str:
        """为本决策窗刷新并渲染【可抽的话题卡 / 提问箱】（真人弹幕在场时不预摆）。"""
        if self._topic_deck is None:
            return ""
        self._sync_talk_workbench()
        should_preset = (
            self._current_speech_mode() == "talk"
            and not has_viewer_batch
            and not self._talk_workbench.has_active_card()
            and (not self._talk_workbench.has_open_beat() or self._is_card_friendly_segment())
        )
        await self._topic_deck.refresh(should_preset=should_preset)
        return self._topic_deck.render()

    async def _on_round_spoken(self, outcome: Any, had_viewer_batch: bool) -> None:
        """一轮决策成功发出台词后更新话题匣与话头工作台进度。"""
        self._sync_talk_workbench()
        used_card = False
        if outcome.use_card_id is not None and self._topic_deck is not None:
            card = await self._topic_deck.use_card(outcome.use_card_id)
            if card is not None:
                self._talk_workbench.start_card(card.id, card.title)
                used_card = True
        if self._current_speech_mode() != "talk":
            return
        # 观众弹幕或玩家开口的插话轮若未主动选卡、也未声明收束要点，不扣减当前提纲要点轮数
        answering_digression = bool(had_viewer_batch or outcome.target or outcome.reply_player[0])
        if used_card or outcome.beat_done or not answering_digression:
            self._talk_workbench.record_round(beat_done=outcome.beat_done)
        if outcome.beat_done:
            self._talk_workbench.consume_interrupted_tail()

    def _note_game_activity(self, game: str) -> None:
        """记一笔这个游戏刚有动静：game.* / game.body.* / game.chat.* 事件或委派受理。"""
        name = (game or "").strip()
        if not name:
            return
        self._game_activity_ms[name] = now_ms()
        self._session_games.add(name)

    def _playing_games(self) -> List[str]:
        """正在玩的游戏：有进行中的委派任务，或判定窗内有这个游戏的事件。

        判据按优先级：任务记录表里有 executor 是它、状态未终了的任务 → 算正在玩；
        否则才看游戏事件（game.* / body / chat）的最近时刻是否落在 ``_GAME_ACTIVE_WINDOW_MS`` 内。
        两条都不满足 → 不算在玩。只看事件与任务，不看工具面是否可见，所以形象等
        非游戏工具面不会混进游戏承诺卡。
        """
        playing: set[str] = set()
        if self._task_ledger is not None:
            for task_id in self._task_ledger.active_task_ids():
                record = self._task_ledger.get(task_id)
                if record is None or record.status not in IN_PROGRESS_TASK_STATES:
                    continue
                executor = str(record.executor or "").strip()
                if executor:
                    playing.add(executor)
        now = now_ms()
        for game, last_ms in self._game_activity_ms.items():
            if now - last_ms <= _GAME_ACTIVE_WINDOW_MS:
                playing.add(game)
        return sorted(playing)

    def _social_game_scope(self) -> Tuple[str, str, str]:
        """弹幕作证的社交事实挂在哪：眼下在玩的第一个游戏与它的存档（没在玩游戏时游戏为空串）。"""
        games = self._playing_games()
        if not games:
            return "", "", ""
        save = self._current_saves.get(games[0])
        return games[0], save.id if save is not None else "", save.name if save is not None else ""

    async def _load_social_facts(self) -> List[Any]:
        """参考段用的现行社交事实：眼下在玩的每个游戏，按它当前存档取（存档不明时取整个游戏）。"""
        if self._social_fact_repo is None:
            return []
        facts: List[Any] = []
        for game in self._playing_games():
            save = self._current_saves.get(game)
            save_id = save.id if save is not None and save.known else None
            facts.extend(await self._social_fact_repo.list_active_facts(game=game, save_id=save_id))
        return facts

    async def _wake_memory_indexer(self) -> None:
        """社交事实写成后叫醒索引任务补一轮（回想马上查得到）；没装索引时什么都不做。"""
        if self._memory_indexer is not None:
            self._memory_indexer.request_run()

    def _sync_current_saves(self) -> None:
        """从游戏工具面拉一次各游戏眼下所在的存档（只读，不触发游戏里的任何动作）。

        某个游戏从一个认出的存档换到了另一个（中间回过主菜单也算）：手头在这个游戏里的事是在
        旧世界里交代的，清掉这个游戏的委派与补充要求。换存档这件事本身由游戏自己发的里程碑进
        时间线（MaiCraft 的游戏 Agent 认出换存档时发），这里不再写一遍。拉不到就沿用上一次的。
        """
        if self._tool_registry is None:
            return
        try:
            saves = self._tool_registry.current_saves(for_agent="streamer")
        except Exception as exc:  # noqa: BLE001 - 拉不到存档就沿用上一次的，不挡决策
            self._logger.warning(f"读各游戏眼下的存档失败（沿用上一次的）: {exc}", exc=exc)
            return
        for game, save in saves.items():
            if not save.known:
                continue
            before = self._known_saves.get(game)
            if before is not None and before.id != save.id:
                self._planner.workbench.forget_game(game)
                self._game_side.forget_game(game)
                if self._task_ledger is not None:
                    self._task_ledger.clear_provider_outcomes(initiator=game)
                self._logger.info(f"{game} 从存档「{before.label}」换到「{save.label}」：清掉手头这个游戏的事")
            self._known_saves[game] = save
        self._current_saves = dict(saves)

    def _session_game_names(self) -> List[str]:
        """本场出现过事件的游戏（整场口径；下播整理取"本场玩过的游戏"用）。"""
        return sorted(self._session_games)

    def _current_session_kind(self) -> SessionKind:
        """进行中场次的类型；未开播按正式场算（开播前的闲聊读的是正式直播的上一场）。

        收播道别那一轮里进行中场次已清空，按正在结束的那一场取：彩排场道别时回想
        "上一场"仍只翻彩排场，不会被当成正式场。这一场只在道别那一轮自己的调用链里看得见
        （见 ``_CLOSING_SESSION``）：道别期间并发的常规轮次、刚开的新场次照常取进行中场次的类型。
        """
        closing = _CLOSING_SESSION.get()
        if closing is not None:
            return closing.kind
        kind = getattr(self._session_manager, "active_kind", None) if self._session_manager is not None else None
        return kind if kind in SESSION_KINDS else SESSION_KIND_LIVE

    async def _previous_session_id(self) -> Optional[int]:
        """上一场 = 最近一个已整理、有档案行、时长够的同类型场次（与常驻简报同口径）。

        承诺卡的时间词按它给：登记在上一场的写"上一场"，其余写"之前"。没有档案仓储
        （账本没启用）或读不到时返回 None，承诺卡一律写"之前"。
        """
        if self._archive_repo is None:
            return None
        try:
            return await self._archive_repo.get_last_archived_session_id(
                kind=self._current_session_kind(),
                min_duration_ms=self.typed_config.memory_brief_min_session_ms,
            )
        except Exception as exc:  # noqa: BLE001 - 取不到就按没有上一场处理，不挡线索推送
            self._logger.warning(f"取上一场编号失败（承诺卡时间词写「之前」）: {exc}", exc=exc)
            return None

    async def _bootstrap_recaps(self) -> None:
        """启动善后：补完上次退出时没整理完的场次，再拼好常驻简报。"""
        await self._settle_ledger()
        self._sync_current_saves()
        await self._refresh_memory_brief(announce=True)

    def _settle_pending_before_brief(self) -> None:
        """开播时把补整理排到后台，不阻塞开播回调。

        下播整理在后台跑：道别那一轮、等队尾台词（最多 30 秒）、三次模型调用，常超过
        一分钟；开播回调不能同步等它，否则上一场没整理完时开播要卡到模型返回。这里先
        按当前表快照拼简报，整理在后台跑完后的下一个决策窗重渲时自然生效，这期间简报
        里没有刚结束那一场的档案。同一场若下播后台整理还在跑，SessionSettler 的按场次
        在途表会让两边共享同一个任务，不会重复调模型。
        """
        if self._settler is None:
            return
        spawn_background_task(
            self._settle_ledger(),
            logger=self._logger,
            tasks=self._memory_tasks,
            label="StreamerAgent.settle_catchup",
        )

    async def refresh_memory_brief(self, *, kind: Optional[SessionKind] = None, announce: bool = False) -> None:
        """按台账快照重渲常驻简报（公开接缝，供决策窗与测试驱动重渲）。"""
        await self._refresh_memory_brief(kind=kind, announce=announce)

    async def _refresh_memory_brief(self, *, kind: Optional[SessionKind] = None, announce: bool = False) -> None:
        """按台账快照重渲常驻简报（每决策窗一次）；仓储缺失时为空。

        渲染只依赖表快照：表没变时输出逐字节相同，Planner 系统提示词的前缀缓存仍命中；
        表变了（直播中纠错、上一场整理刚写完）下一窗就反映出来。``announce`` 只在开播与
        启动善后打开：那条 info 日志带"上一场编号与播出台词数"，供运营者核对注入内容。
        """
        if self._commitments_repo is None or self._archive_repo is None:
            self._memory_brief_text = ""
            self._memory_brief_ids = ()
            self._memory_brief_archive_ids = ()
            self._memory_used_archive_ids = ()
            return
        await self._flush_pending_operator_notes()
        resolved_kind = kind if kind is not None else self._current_session_kind()
        try:
            brief = await build_brief(
                archive_repo=self._archive_repo,
                commitments_repo=self._commitments_repo,
                kind=resolved_kind,
                min_session_ms=self.typed_config.memory_brief_min_session_ms,
                # 本场点名用过的承诺不再列（用上即停）；没在直播时没有"本场"，整份照列
                chat_repo=self._chat_repo,
                live_session_id=self._current_session_pk(),
                # 分存档的游戏：开头写眼下在哪个存档，上一场别的存档里的事折成一行
                event_repo=self._event_repo,
                current_saves=self._current_saves,
                correction_repo=self._memory_correction_repo,
            )
        except Exception as exc:  # noqa: BLE001 - 简报拼不出来只省这一段，不挡开播
            self._logger.warning(f"拼常驻简报失败（本场省略简报段）: {exc}", exc=exc)
            self._memory_brief_text = ""
            self._memory_brief_ids = ()
            self._memory_brief_archive_ids = ()
            self._memory_used_archive_ids = ()
            return
        self._memory_brief_text = brief.text
        self._memory_brief_ids = brief.commitment_ids
        self._memory_brief_archive_ids = brief.archive_ids
        self._memory_used_archive_ids = brief.used_archive_ids
        if announce:
            await self._log_brief_summary(brief)

    async def _flush_pending_operator_notes(self) -> None:
        """把运营在本场递进来的提醒落进 memory_corrections，使它在本场常驻简报与下播整理中一直可见。"""
        if not self._pending_operator_notes:
            return
        session_id = self._current_session_pk()
        if self._memory_correction_repo is None or session_id is None:
            self._pending_operator_notes.clear()
            return
        while self._pending_operator_notes:
            note = self._pending_operator_notes.popleft()
            try:
                await self._memory_correction_repo.record_operator_note(
                    live_session_id=int(session_id),
                    content=note,
                )
            except Exception as exc:  # noqa: BLE001 - 单条提醒落库异常不阻塞本窗简报渲染
                self._logger.warning(f"本场运营常驻提醒落库失败: {exc}", exc=exc)

    async def _log_brief_summary(self, brief: Any) -> None:
        """开播时记一条简报摘要：上一场编号、档案行数、播出台词数与承诺条数。"""
        if brief.previous_session_id is None:
            self._logger.info(
                f"常驻简报已装入：没有上一场，承诺 {len(brief.commitment_ids)} 条（{len(brief.text)} 字）"
            )
            return
        speech_count = await self._count_delivered_speech(int(brief.previous_session_id))
        self._logger.info(
            f"常驻简报已装入：上一场 #{brief.previous_session_id}，档案 {brief.archive_line_count} 行，"
            f"播出台词 {speech_count} 句，承诺 {len(brief.commitment_ids)} 条（{len(brief.text)} 字）"
        )

    async def _count_delivered_speech(self, live_session_id: int) -> int:
        """某场真正播出的台词条数（掉落标记为空的行）；取不到按 0 记并告警。"""
        if self._chat_repo is None:
            return 0
        try:
            lines = await self._chat_repo.list_session_speech_for_settle(live_session_id=int(live_session_id))
        except Exception as exc:  # noqa: BLE001 - 日志统计失败不影响开播
            self._logger.warning(f"统计上一场 #{live_session_id} 播出台词数失败（按 0 记）: {exc}", exc=exc)
            return 0
        return sum(1 for line in lines if line.dropped_at_ms is None)

    async def _lookup_commitment(self, commitment_id: int) -> Any:
        """按编号取一条承诺（Planner 点名后渲染说话档用）；取不到返回 None。"""
        if self._commitments_repo is None:
            return None
        return await self._commitments_repo.get_commitment(commitment_id=int(commitment_id))

    async def _lookup_archive_line(self, line_id: int) -> Any:
        """按编号取一行档案（Planner 点名后渲染说话档用）；取不到返回 None。"""
        if self._archive_repo is None:
            return None
        lines = await self._archive_repo.list_lines_by_ids(line_ids=[int(line_id)])
        return lines[0] if lines else None

    async def _collect_memory_cues(
        self, batch: List[Any], open_games: Sequence[str], game_players: Sequence[Tuple[str, str]] = ()
    ) -> Any:
        """本批线索：承诺卡、玩家卡、被动想起的档案卡与老梗卡、老观众在场一行、回执行；没启用线索推送时返回 None。

        常驻简报里列着的条目交过去：同一件事简报里有了，卡片不再推一遍。``game_players`` 是本窗在游戏里
        开口或冲她做动作的玩家，(游戏, UUID)：有往来的推一张"这位是谁、和你有过什么"。
        """
        if self._memory_cues is None:
            return None
        return await self._memory_cues.collect(
            batch=list(batch),
            open_games=list(open_games or ()),
            current_saves=self._current_saves,
            situations=self._current_situations(),
            brief_archive_ids=self._memory_brief_archive_ids,
            brief_commitment_ids=self._memory_brief_ids,
            game_tasks=self._game_tasks(),
            game_players=list(game_players),
            recent_enters=self._room_state.recent_unwelcomed_enters(),
        )

    def _game_tasks(self) -> Dict[str, str]:
        """各游戏手上还在做的委派（她交代的原话）：游戏情境查询用，换了委派就是情境变了。"""
        self._planner.workbench.sync_from_ledger()
        return {
            agent: delegation.instruction
            for agent, delegation in self._planner.workbench.delegations.items()
            if not delegation.report_kind and delegation.instruction
        }

    def _current_situations(self) -> Dict[str, GameSituation]:
        """各游戏角色眼下离哪些记住的地点多远（只读游戏 Agent 的缓存）；读不到就当这一批没有。"""
        if self._tool_registry is None:
            return {}
        try:
            return self._tool_registry.current_situations(for_agent="streamer")
        except Exception as exc:  # noqa: BLE001 - 读不到情境，这一批地点一路不判
            self._logger.warning(f"读各游戏眼下的情境失败（这一批不判地点到达）: {exc}", exc=exc)
            return {}

    async def _settle_ledger(self) -> None:
        """整理待整理的场次，把承诺与场次档案写进账本；没启用时直接返回。

        调度器自己挑场次、自己记账（失败原因进整理报告），这里只兜住意外异常：
        下播回调与启动都不该因为账本整理崩掉。
        """
        if self._settler is None:
            return
        try:
            await self._settler.settle_pending()
        except Exception as exc:  # noqa: BLE001 - 账本整理失败不能挡住收播与启动
            self._logger.warning(f"下播整理账本出错: {exc}", exc=exc)
            return
        # 补整理写下的档案行与承诺同样马上补进索引
        self._request_index_run()

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
            self._note_game_activity(payload.game)
            message = self._game_ledger.record_event(
                payload.game, payload.event_type, payload.message, payload.timestamp_ms or now_ms()
            )
            if self._mouth is not None and payload.event_type in URGENT_GAME_TYPES and message:
                self._mouth.note_game(message)
                self._mouth.react(f"game:{payload.event_type}", message, urgent=True)
            if payload.event_type == "report":
                self._proactive_signals.set_game()
                # 游戏里做完或卡住待定夺：【我手头在游戏里做的事】里那件事随之标成已上报
                self._planner.workbench.note_game_report(
                    payload.game, payload.report_kind or "", payload.delegation_ids
                )
        except Exception as exc:  # noqa: BLE001 - 收集失败不阻断
            self._logger.warning(f"收集游戏事件失败: {exc}", exc=True)

    @property
    def _game_ledger(self) -> GameLedger:
        """游戏账本入账面（game.* 回调写入口；持有者见 ``SessionScope.ledger``）。"""
        return self._scope.ledger

    async def _consume_opening_wrapup(self) -> Optional[Any]:
        """开播头两窗（且本场尚未受理任何委派）返回上一正式场的【上次收尾】；超出窗口或已派单后收闸。"""
        if not self._opening_wrapup_active or self._sessions_repo is None:
            return None
        if self._opening_wrapup_windows >= OPENING_WRAPUP_MAX_WINDOWS:
            self._opening_wrapup_active = False
            return None
        session_pk = self._current_session_pk()
        if self._event_repo is not None and session_pk is not None:
            try:
                current_delegations = await self._event_repo.list_session_delegations(live_session_id=int(session_pk))
            except Exception as exc:  # noqa: BLE001 - 查不到本场委派时照常按窗口计数决定
                self._logger.warning(f"核对本场委派账失败（按窗口计数继续注入【上次收尾】）: {exc}", exc=exc)
                current_delegations = []
            if current_delegations:
                self._opening_wrapup_active = False
                return None
        self._opening_wrapup_windows += 1
        if self._opening_wrapup_windows >= OPENING_WRAPUP_MAX_WINDOWS:
            self._opening_wrapup_active = False
        return await build_last_wrapup(
            sessions_repo=self._sessions_repo,
            chat_repo=self._chat_repo,
            event_repo=self._event_repo,
            commitments_repo=self._commitments_repo,
            kind=self._current_session_kind(),
            min_session_ms=self.typed_config.memory_brief_min_session_ms,
            live_session_id=session_pk,
            current_saves=self._current_saves,
        )

    def on_task_notification(self, payload: TaskChangedPayload) -> None:
        """任务变化通知（``task.changed``，仅发起方是自己时被调）：停滞告警送进决策窗并履行决断义务。

        数据面修复（场 65 实证）：基类默认只记日志，停滞告警进不了决策上下文，
        告警发了主播也做不出决断（全场 11 次告警零反应）。这里把 alert 类告警
        （``payload.alert=True``）记进游戏账本当决策窗必读材料（委派原话、已停
        多久、第几次告警，形态同遭遇 / 递话类注入）并置位待定夺信号唤醒决策；
        同一委派按决断兜底计数，排队 / 停着等定夺的第 N 次告警仍无决断时自动
        撤单并补一条场记事实（"这件事我喊停了"），主播下一窗顺带交代。非告警
        的变化只喂给决断兜底对账（终态出册、真变化重置计数）。
        """
        if not getattr(payload, "alert", False):
            self._stall_watch.note_change(payload)
            return
        outcome = self._stall_watch.note_alert(payload)
        if outcome is None:
            return
        try:
            self._game_ledger.record_event(
                outcome.executor, "task_alert", outcome.material, payload.timestamp_ms or now_ms()
            )
            if outcome.auto_cancel:
                self._game_ledger.record_event(
                    outcome.executor,
                    "auto_cancel",
                    f"派去 {outcome.executor} 的委派「{outcome.own_words}」一直没进展也没人撤，我把它喊停了，"
                    "待会儿跟观众交代一下这件事",
                    now_ms(),
                )
        except Exception as exc:  # noqa: BLE001 - 入账失败不阻断撤单兜底
            self._logger.warning(f"停滞告警材料入账失败（task_id={payload.task_id}）: {exc}", exc=exc)
        self._proactive_signals.set_game()
        if outcome.auto_cancel:
            spawn_background_task(
                self._cancel_stalled_delegation(str(payload.task_id)),
                logger=self._logger,
                tasks=self._memory_tasks,
                label="StreamerAgent.stall_auto_cancel",
            )

    async def _cancel_stalled_delegation(self, task_id: str) -> None:
        """决断兜底的自动撤单：走 framework_task_settle(outcome='drop')（待办直撤，在做的收手后自动送达下一件）。"""
        if self._tool_registry is None:
            self._logger.warning(f"停滞决断兜底跳过撤单（task_id={task_id}）：工具注册表未注入")
            return
        try:
            result = await self._tool_registry.invoke(
                ToolInvocation(
                    tool_name="framework_task_settle",
                    arguments={"item": task_id, "outcome": "drop"},
                    source="streamer_stall_watch",
                )
            )
        except Exception as exc:  # noqa: BLE001 - 撤单失败只记日志，账面等下一轮告警重判
            self._logger.warning(f"停滞决断兜底自动撤单失败（task_id={task_id}）: {exc}", exc=exc)
            return
        if result.success:
            self._logger.info(f"停滞决断兜底已自动撤单: task_id={task_id}")
        else:
            self._logger.warning(f"停滞决断兜底自动撤单被拒（task_id={task_id}）: {result.error_message}")

    def _record_delegation(self, agent: str, instruction: str) -> None:
        """Planner 受理委派后回调：把"我在哪个游戏里开始做什么"按此刻进时间线。

        委派行直写时间线，不进决策窗批次（批次只收三类游戏事件通道的条目）。
        受理即算"开始玩这个游戏"：游戏承诺卡据此出现，不必等那个游戏报出第一个事件；
        同时第一件委派已落地，开播【上次收尾】提前收闸不再注入。
        """
        self._opening_wrapup_active = False
        self._note_game_activity(agent)
        self._scope.note_delegation(agent, instruction)

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
            self._note_game_activity(payload.game)
            summary = self._game_ledger.record_encounter(
                payload.game,
                payload.kind,
                payload.summary,
                bool(getattr(payload, "resolved", False)),
                payload.timestamp_ms or now_ms(),
                # 只有冲她做肢体动作的玩家算找过她，本窗据此推他的玩家卡；跟随状况上挂的是她跟着的人，
                # 他没来找她，不推卡、不算她在回应谁
                player=payload.player_uuid if payload.kind == "player_body_language" else "",
                # 冲她做动作的人的名字：表达侧据此说清是谁扔来的、谁拍的她，不说成凭空到手
                who=payload.player_name if payload.kind == "player_body_language" else "",
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
                self._proactive_signals.set_game()
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
            self._note_game_activity(payload.game)
            body = _game_chat_line(payload)
            if payload.private:
                self._scope.record_private_chat(f"[{payload.game}] {body}")
            else:
                # 记下这句是谁说的：本窗只有一位核对过的玩家开口时，她这一窗的话落库时记成在回应他
                # 带上玩家名：表达侧据此分清这句是哪位玩家说的，不安到刚点过名的女仆、宠物头上
                self._game_ledger.record_chat(
                    payload.game,
                    body,
                    payload.timestamp_ms or now_ms(),
                    speaker=_chat_speaker(payload),
                    who=payload.sender if payload.kind == "player" else "",
                    message_id=payload.message_id,
                )
                if self._mouth is not None:
                    self._mouth.note_game(body)
                if payload.kind != "player":
                    # 公屏系统消息里的玩家加入提示（如"系统消息：玩家甲加入了游戏"）：登记待欢迎名，防嘴与决策重复欢迎
                    joined_player = extract_player_join_name(payload.content) or extract_player_join_name(body)
                    if joined_player:
                        self._room_state.record_player_join(joined_player)
            if payload.kind == "player":
                self._proactive_signals.set_game()
                # 游戏里说过话的人记成已知玩家名：游戏情境查询发给嵌入服务前把它换掉
                if self._memory_cues is not None and payload.sender:
                    self._memory_cues.note_name(payload.sender)
        except Exception as exc:  # noqa: BLE001 - 收集失败不阻断
            self._logger.warning(f"收集游戏聊天失败: {exc}", exc=True)

    async def _on_self_chatted(self, event_name: str, payload: GameSelfChattedPayload, source: str) -> None:
        """``game.self.chatted`` 回调：她在游戏公屏说出去的一句入待入账队列，下个决策窗进时间线。

        游戏那一面会自己找玩家搭话（第 58 场问人要蒲公英），不记下来的话玩家回一句"找到了"，
        她在直播里接不上。这是她自己的话：不促发决策、不交表达侧当别人刚说的话。
        """
        del event_name, source
        try:
            self._note_game_activity(payload.game)
            at_ms = payload.timestamp_ms or now_ms()
            self._game_ledger.record_self_chat(payload.game, payload.message, at_ms)
            # 同一句也记进游戏那边的流水：【游戏那边此刻】列她近五分钟在公屏说过什么
            self._game_side.note_self_chat(payload.game, payload.message, at_ms)
        except Exception as exc:  # noqa: BLE001 - 收集失败不阻断
            self._logger.warning(f"收集她自己的公屏话失败: {exc}", exc=True)

    def _game_now(self, agents: Sequence[str]) -> GameNow:
        """游戏那边此刻的读数（Planner 每窗拼【游戏那边此刻】用）：各 Agent 此刻那一步与最近的判断、近期目标成败，加她的公屏话。"""
        current = now_ms()
        readings: Dict[str, AgentNow] = {}
        for agent in agents:
            step, step_at_ms = self._latest_game_step(agent)
            judgement, judged_at_ms = self._latest_game_judgement(agent)
            readings[agent] = AgentNow(
                step=step, step_at_ms=step_at_ms, judgement=judgement, judgement_at_ms=judged_at_ms
            )
        return GameNow(
            agents=readings,
            self_chat=self._game_side.recent_self_chat(current),
            goal_results=goal_results_from_ledger(self._task_ledger, current_ms=current, exclude_initiator=self.name),
        )

    def _latest_game_step(self, agent: str) -> Tuple[str, int]:
        """这个游戏 Agent 此刻在做的那一步：任务账本里它自己发起、还在跑的最近一步的目的与开始时刻。

        只认它自己发起、自己驱动的那几步（provider 型）；它接下的委派不是"一步"。目的取执行侧快照里约定的
        ``purpose``（这一步要干什么），没写目的的那一步不报。没接任务账本或没有在跑的一步时为 ("", 0)。
        """
        if self._task_ledger is None:
            return "", 0
        latest = None
        for task_id in self._task_ledger.active_task_ids():
            record = self._task_ledger.get(task_id)
            if record is None or record.source != "provider" or record.initiator != agent:
                continue
            if record.status not in IN_PROGRESS_TASK_STATES:
                continue
            # 同一毫秒登记的几步取后登记的那一步（账本按登记先后列）
            if latest is None or record.created_at_ms >= latest.created_at_ms:
                latest = record
        if latest is None:
            return "", 0
        return str((latest.snapshot or {}).get("purpose") or "").strip(), latest.created_at_ms

    def _latest_game_judgement(self, agent: str) -> Tuple[str, int]:
        """这个游戏 Agent 最近说的一句判断与时刻：任务账本里它名下在跑的委派快照的旁注，取最新的一句。

        这是游戏那一面自己的推理（"门在左边，先拆一格墙出去"），可能看错，所以只进【游戏那边此刻】并标明
        不一定对；不进对话时间线、不促发决策。每窗决策前读一次，不订阅它的回复事件——Agent 只听输入与
        基础设施事件。没接任务账本或没有时为 ("", 0)。
        """
        if self._task_ledger is None:
            return "", 0
        latest: Tuple[str, int] = ("", 0)
        for task_id in self._task_ledger.active_task_ids():
            record = self._task_ledger.get(task_id)
            if record is None or record.source != "agent" or record.executor != agent:
                continue
            snapshot = record.snapshot or {}
            text = str(snapshot.get(REPLY_NOTE_KEY) or "").strip()
            at_ms = int(snapshot.get(REPLY_NOTE_AT_KEY) or 0)
            if text and at_ms >= latest[1]:
                latest = (text, at_ms)
        return latest

    def _current_segment_title(self) -> str:
        """当前流程单环节的标题（嘴的现场卡用）；没有流程单为空。"""
        return self._rundown_coord.current_segment_title()

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

    def _on_speech_emitted(self, text: str, at_ms: int, source: str) -> None:
        """发声分发器每播出一句台词（决策环节或嘴）后同步记入本场口头流水、核销已欢迎名单并喂给嘴。"""
        resolved_source: Literal["mouth", "planner"] = "mouth" if source == "mouth" else "planner"
        self._spoken_log.record(text, at_ms, source=resolved_source)
        self._room_state.note_spoken(text)
        if self._mouth is not None:
            self._mouth.note_speech(text, at_ms)

    def _on_mouth_spoken(self, text: str, at_ms: int, trigger_text: str) -> None:
        """嘴发声完成后补记触发由头，让决策环节出声前核对时知道嘴刚才接的是哪件事。"""
        self._spoken_log.record(text, at_ms, source="mouth", trigger=trigger_text)

    async def _reconcile_with_mouth(self, candidate: str, since_ms: int) -> ReconcileVerdict:
        """决策台词出声前核对：剥掉本场重复欢迎，并对照 ``since_ms`` 之后嘴说出口的话及新到游戏事件裁决。"""
        return await reconcile_with_mouth(
            candidate,
            mouth_entries=self._spoken_log.mouth_since(since_ms),
            new_game_lines=self._game_ledger.pending_lines(),
            welcomed_names=self._room_state.welcomed_names,
            llm_service=self._llm,
            profile=_PROFILE_REPLYER,
        )

    async def _fresh_context_since_open(self, since_ms: int) -> Dict[str, Any]:
        """决策台词因与嘴或新现场矛盾退回重想时，附上本窗开窗后嘴说的话、待入账新事件与最新一句话现场。"""
        ctx: Dict[str, Any] = {
            "mouth_just_said": [entry.text for entry in self._spoken_log.mouth_since(since_ms)],
            "latest_game_events": self._game_ledger.pending_lines()[-8:],
        }
        scene = await self._read_scene_brief()
        if scene:
            ctx["latest_scene"] = scene
        return ctx

    def _on_mouth_superseded(self) -> None:
        """出声前因现场已变撤回过时台词后：若账本已有新游戏事件待出账，置位主动信号唤醒下一轮评估。"""
        if self._game_ledger.pending_lines():
            self._proactive_signals.set_game()
            self._flush_kick.set()

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

        观众进房名单经【房间活跃度】行进决策上下文，供欢迎话术顺口点名。
        （观众数协议无源恒 0，人数门槛做不了；进房名单是唯一的观众到场景来源。）
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
        handle_message 内的强制名单按 message_type 判定，本回调不做过滤。
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
        # 强制触发判定：消息类型在强制名单内即强制
        forced = msg.message_type in self._force_message_types
        # 入缓冲
        self._buffer.add(msg, arrival_ms=now_ms(), forced=forced)
        # 弹幕一到就在后台算它的查询向量：攒批窗口几秒，组装线索时多半已算好，决策不等它
        if self._memory_cues is not None:
            await self._memory_cues.prefetch(msg)
        if self._mouth is not None:
            sender = msg.user.name if msg.user is not None else ""
            self._mouth.note_viewer(sender or "观众", msg.content)

    def trigger_external_proactive(self, topic_hint: Optional[str] = None) -> None:
        """外部 API 触发主动发言（Dashboard / API 调用）。"""
        self._proactive_signals.set_external()
        self._flush_kick.set()
        if topic_hint:
            self._logger.info(f"外部主动发言触发: {topic_hint}")

    def _on_rundown_changed_proactive(self) -> None:
        """流程单环节变更通知（协调器回调）：置位 proactive 即时信号，下个 tick 消费。"""
        self._proactive_signals.set_rundown()

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
        # 运营从控制台发来的提醒除了进当窗【运营提醒】，还落成本场常驻更正提醒（本场未完不消失，下播整理按事实参考处理）
        if source in ("operator", "dashboard") and self._current_session_pk() is not None:
            self._pending_operator_notes.append(text)
        # 到达即唤醒 flush 循环：递话不必陪弹幕批次等完当前 tick
        self._flush_kick.set()
        self._emit_prompt_received(text=text, source=source)
        self._logger.info(
            f"收到递话提醒（source={source or '未知'}，队列 "
            f"{len(self._reminder_queue)}/{_REMINDER_QUEUE_MAX}）：{text[:60]}"
        )
        return True

    def _emit_prompt_received(self, *, text: str, source: str) -> None:
        """发递话到达事件（含全文原文，不截断；事件名注释见 events/names.py）。"""
        self._emit_from_sync(
            CoreEvents.STREAMER_PROMPT_RECEIVED, StreamerPromptReceivedPayload(content=text, source=source)
        )

    def _emit_from_sync(self, event_name: str, payload: BasePayload) -> None:
        """从同步入口发事件：派发任务挂到运行中的事件循环（递话受理与取空都是同步调用）。"""
        if self._event_bus is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._logger.warning(f"递话事件无运行中的事件循环可派发，本次未发 {event_name}", exc=True)
            return
        loop.create_task(self._event_bus.emit(event_name, payload, source="StreamerAgent"))

    def _drain_reminders(self) -> str:
        """取空提醒队列（决策执行器在决策窗入口调用；送达一次制）。"""
        if not self._reminder_queue:
            return ""
        items = list(self._reminder_queue)
        lines = [f"- {item}" for item in items]
        self._reminder_queue.clear()
        # 送达留痕：决策窗一取走就记一条送达事件，续播重放据此只补重启时还没送出去的
        self._emit_from_sync(CoreEvents.STREAMER_PROMPT_DELIVERED, StreamerPromptDeliveredPayload(contents=items))
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
        trigger_cause = TriggerCause(kind=TriggerKind.DASHBOARD_DEBUG, source=TriggerSource.DASHBOARD)
        try:
            async with self._flush_lock:
                # 调试注入不经 room.message 事件，观众这句不会落库；记入内存后下一轮仍能接上话
                result = await self._execute_round(
                    messages,
                    forced=forced,
                    trigger_cause=trigger_cause,
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
        """后台循环：周期性检查缓冲并触发批次决策；递话/外部触发到达即提前唤醒。"""
        interval = max(self.typed_config.batch.tick_interval_ms / 1000.0, 0.05)
        try:
            while self._running:
                # 唤醒位等待：有 kick 时立即醒来，否则照旧按 tick 间隔轮询
                # （批次聚合窗口不受影响——唤醒只提前评估，不改变批次放行条件）
                try:
                    await asyncio.wait_for(self._flush_kick.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
                self._flush_kick.clear()
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
            # 递话评估移出判空闸：提醒队列非空时每个 tick 先走主动判定序——
            # 弹幕批次滞留期间递话到达也照常成轮，批次原地不动继续等自己的
            # 放行条件。只有这一轮真的执行了才结束本 tick：递话被防接龙挡住时
            # 接着看弹幕批次，到期就照常开窗，递话随这一窗送达（决策执行器在
            # 任何决策窗入口都取空提醒队列）。否则她每说一句都刷新防接龙计时，
            # 递话一直排着，弹幕回复和礼物感谢跟着停到防接龙放行为止
            reminder_evaluated = False
            if self._reminder_queue:
                reminder_evaluated = True
                if await self._evaluate_proactive_round():
                    return

            if not self._buffer.is_empty:
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

                await self._execute_round(batch, forced=forced, trigger_cause=cause_for_batch_flush(flush_reason))
                return

            # buffer 空：queue 空且未开播才早退——早退不消费 pending 信号，
            # 环节变更/游戏待定夺信号保留到开播后首个 tick 生效。未开播的
            # 递话窗里环节/游戏 pending 不参与判定（不能在未开播时说开场白/
            # 推进游戏），也因此不会被误消费。
            if not self._live_active:
                return
            # 本 tick 带着递话已经判过一次、没有触发：同样的信号再判一遍结果不变，不重复判
            if reminder_evaluated:
                return
            await self._evaluate_proactive_round()

    async def _evaluate_proactive_round(self) -> bool:
        """一个 tick 的主动发言判定序：读信号 → 判定 → 命中即执行一轮主动决策。

        读信号 → 下播折参 → 判定 → 确认消费的完整判定序收在
        ProactiveSignals.evaluate_tick 单点；Agent 只按结果执行。返回这一轮
        有没有真的执行（调用方据此决定本 tick 还要不要看弹幕批次）。
        """
        # 读信号 → 下播折参 → 判定 → 确认消费的完整判定序收在
        # ProactiveSignals.evaluate_tick 单点；Agent 只按结果执行。
        reason = self._proactive_signals.evaluate_tick(
            live=self._live_active,
            trigger=self._proactive_trigger,
            room_state=self._room_state,
            now_ms=now_ms(),
            rundown_overdue=self._rundown_coord.is_overdue(),
            rundown_ready=self._rundown_coord.is_active(),
            # 提醒队列非空即催醒；防接龙阻塞时队列保留，下一 tick 重试
            # （送达在决策执行器取空队列处发生，任何决策窗都会消费）
            reminder_pending=bool(self._reminder_queue),
            talk_mode=self._current_speech_mode() == "talk",
            viewer_waiting_since_ms=None if self._buffer.is_empty else self._buffer.first_arrival_ms,
        )
        if reason is None:
            return False
        self._stats.total_proactive += 1
        self._logger.info(f"主动发言触发: {reason.value}")
        await self._execute_round(
            [],
            forced=False,
            trigger_cause=TriggerCause(kind=reason),
            proactive=True,
        )
        return True

    async def _execute_round(
        self,
        batch: List[RoomMessagePayload],
        *,
        forced: bool,
        trigger_cause: TriggerCause,
        proactive: bool = False,
        batch_persisted: bool = True,
        closing_session: Optional[ClosingSession] = None,
    ) -> Dict[str, Any]:
        """执行一轮决策，并把本轮观众消息与主播发言记入尚未落库的对话轮。

        ``batch_persisted`` 表示本批消息是否经 room.message 事件进了落库链路；
        调试注入传 False——即使开着场次，这些消息也不在 live_chat 里。

        ``closing_session`` 只在收播道别那一轮给出（正在结束的那一场）。决策执行器开窗按
        这一场读历史、道别句带这一场的编号；reply 工具经 :meth:`_read_history` 给表达侧取
        历史、回想工具取场次类型，也都按这一场——这两处不经参数传递，靠 ``_CLOSING_SESSION``
        这个上下文变量读到。

        每一轮开头都把上下文变量设成本轮的值（常规轮次设 None），跑完（含出错）用 token
        复位：并发的轮次各在自己的任务里，互相看不见对方的值；两场相继手动结束、两个道别轮
        重叠时也各管各的，前一轮复位不会清掉后一轮的场次。道别那一轮不持决策锁，与下播瞬间
        的常规轮次照旧可以并发（她不用等手头那一轮答完才道别）；下播前开窗的常规轮说完时
        那一场已经下播，台词作废不出声（见 :meth:`_round_session_still_live`），道别句是本场最后一句。
        """
        token = _CLOSING_SESSION.set(closing_session)
        # 开窗时在直播的常规轮记下这一场的开播计数，说完出声前再比对一次（见 _ROUND_LIVE_EPOCH）；
        # 道别轮不记：它就是下播后那唯一一句
        is_signoff = trigger_cause.kind is TriggerKind.SIGNOFF
        live_token = _ROUND_LIVE_EPOCH.set(self._session_epoch if self._live_active and not is_signoff else None)
        try:
            # 开窗前先看各游戏眼下在哪个存档（换了存档清掉那个游戏手头的事），再按台账快照重渲
            # 常驻简报（直播中纠错下一窗生效；表没变、存档没换时逐字节相同，缓存不失效）
            self._sync_current_saves()
            await self._refresh_memory_brief()
            result = await self._rounds.execute(
                batch,
                forced=forced,
                trigger_cause=trigger_cause,
                proactive=proactive,
                closing_session=closing_session,
            )
            scope_pk = closing_session.live_session_id if closing_session is not None else None
            await self._scope.remember_round(
                batch, speech=result.get("speech"), batch_persisted=batch_persisted, scope_pk=scope_pk
            )
        finally:
            _ROUND_LIVE_EPOCH.reset(live_token)
            _CLOSING_SESSION.reset(token)
        return result

    def _round_session_still_live(self) -> bool:
        """本轮说完时还能不能出声：开窗时没在直播或这是道别轮的一律能；开窗时在直播的，
        要那一场还在播（没下播、也没换成下一场）。"""
        epoch = _ROUND_LIVE_EPOCH.get()
        return epoch is None or (self._live_active and self._session_epoch == epoch)

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

    def is_live_active(self) -> bool:
        """场次进行位当前状态（live.started 置位 / live.ended 复位；只读观测面）。"""
        return self._live_active

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
        payload: Optional[Dict[str, Any]] = None,
        now_ms: Optional[int] = None,
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """执行 Dashboard 手动控制动作（动作翻译见 rundown 子包；insert 的 payload 为 ``{title, content?}``）。"""
        return await self._rundown_coord.control(
            action,
            segment_id=segment_id,
            payload=payload,
            now_ms=now_ms,
        )

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
        """读取当前对话并经时间线投影（兼容转发门面；本体见 ``SessionScope.read_history``）。

        reply_tool 的 history_provider 锚在本方法上，既有测试也经它读历史——
        保留薄转发，读数语义（合并投影/读库失败返回 None）在 SessionScope 单点维护。
        收播道别那一轮按正在结束的那一场读：表达侧写道别句时看得到本场最后的对话，
        时间线也不会因为"当前场次"解析成空而被当作换场清空。这一场从 ``_CLOSING_SESSION``
        取，只有道别那一轮自己的调用链读得到；并发的常规轮次照常读进行中场次。
        """
        closing = _CLOSING_SESSION.get()
        return await self._scope.read_history(scope_pk=closing.live_session_id if closing is not None else None)

    # ==================================================================
    # 统计信息
    # ==================================================================

    def get_statistics(self) -> Dict[str, Any]:
        """获取运行时统计信息（结构向后兼容旧 get_statistics）。"""
        return self._stats.as_dict()
