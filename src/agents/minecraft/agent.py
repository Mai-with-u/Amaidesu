"""MinecraftAgent —— 用 MaiCraft v1 的五个工具玩 Minecraft 的 AI 玩家（事件驱动 ReAct Agent）

核心意象：一个用 MCP 工具玩 Minecraft 的普通 ReAct Agent。
- 主播 Agent 是它的用户：framework_delegate 委派派活、minecraft_get_work_log 读工作文档、
  minecraft_glance 看一眼游戏、minecraft_report 收上报
- 命令驱动（类 Code Agent）：空闲零消耗；委派指令唤醒任务，任务内持续推进
  ReAct 循环（LLM 推理 → 工具调用串行执行 → 观察返回）
- 系统提示词 + 工具列表 = 全部"编程"，不发明任何特殊协议
- MaiCraft v1 的工具：observe 看、lookup 查、execute 下达目标、task 管目标、events 读事件。
  需要角色动手的目标在后台推进（分钟级），宿主用 events 长轮询盯着本 Agent 下达的目标，
  做完、失败、提问、被暂停时把最新结果送回来唤醒任务；等待期间不调用模型

继承 ``BaseAgent``，构造注入依赖。局部工具（todo/notebook/report/wait/get_work_log/glance，
注册名 minecraft_*）注册进 ToolRegistry；MaiCraft 的工具经 Agent 私有 MCP 动态发现。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from typing import Any, Deque, Dict, Iterable, List, Literal, Optional, Tuple

from src.agents.minecraft.attention_material import AttentionMaterialPublisher
from src.agents.minecraft.chosen_death import is_suicide_ability
from src.agents.minecraft.context import (
    FACTS_KEY,
    INSTRUCTION_KEY,
    MinecraftHistoryCompactor,
    close_interrupted_calls,
    is_pinned_message,
    json_text,
    strip_unsendable,
)
from src.agents.minecraft.context_fold import (
    FOLD_GUIDE_FALLBACK,
    RECALL_HINT,
    RECALL_MAX_CHARS,
    STOW_MIN_TOKENS,
    FoldProposal,
    fold_lines,
    propose_folds,
)
from src.agents.minecraft.creature_book import CreatureBook, creature_from_record, creature_record
from src.agents.minecraft.delegation_book import DelegationBook
from src.agents.minecraft.goal_ledger import GoalLedger, finish_notice, goal_label, pause_notice
from src.agents.minecraft.glance import glance_brief, glance_scene, glance_self
from src.agents.minecraft.goals import GoalWatch
from src.agents.minecraft.maicraft import (
    AWAITING_ANSWER,
    EXECUTE,
    HOST_ONLY_TOOLS,
    OBSERVE,
    PAUSED,
    PROVIDER,
    GOAL,
    GoalRun,
    MaicraftReply,
    goal_id_of_ledger,
    goal_run_of,
    reply_of,
)
from src.agents.minecraft.notice_router import (
    GAME_NOT_CONNECTED,
    LIVE_NOT_ACTIVE,
    MAX_BATCH_BODY_EVENTS,
    PROMPT_SOURCE_LABELS,
    attention_notice,
    body_notice_content,
    route_body_event,
)
from src.agents.minecraft.others_food import OTHERS_FOOD_TAKEN, FoodTakenLog, food_taken_of
from src.agents.minecraft.player_body_language import PLAYER_BODY_LANGUAGE
from src.agents.minecraft.skill_support import SkillSupport
from src.agents.minecraft.state import MinecraftAgentState, WakeEvenIdle
from src.agents.minecraft.tools import MaicraftMcpProvider, MinecraftToolProvider
from src.agents.minecraft.wake_queue import WakeQueue, opens_task
from src.agents.minecraft.world_identity import WorldChange, WorldIdentity, world_ref_of
from src.modules.agents.base import AgentState, BaseAgent
from src.modules.context import (
    BackgroundCheckpoint,
    ContextFolds,
    FoldPacer,
    PrefixGuard,
    TokenCounter,
    TransientImages,
    absorb_result_images,
)
from src.modules.llm.capabilities import ProfileVisualCapabilityLookup, validate_llm_capability
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.agents import AgentRepliedPayload
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.game_chat import GameChatPayload
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.logging import get_logger
from src.modules.llm.context_meter import SECTION_SKILLS
from src.modules.llm.errors import UnsendableRequestError
from src.modules.llm.payload import response_to_assistant_message
from src.modules.skills import SkillLibrary
from src.modules.tools.conversation import to_conversation_view
from src.modules.tools.llm_defs import function_definition
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry
from src.modules.utils.time_utils import now_ms

from .config import MinecraftConfig

__all__ = ["MinecraftAgent"]

# 游戏决策使用独立用途。
MINECRAFT_PROFILE = "minecraft"
# 生物名册落库时的游戏标识（与发出的游戏事件同一个）
_GAME = "minecraft"

# 模型调用失败、身体停下时主播看一眼读到的说法（她本人的口吻）：暂时性失败再派一次活就会重试；
# 请求发不出去且换回执、重开对话都补救不了时，再派活也是同一个结果
_FAULT_RETRY = "身体出故障了，暂时动不了；再派一次活它会重新试"
_FAULT_STUCK = "身体出故障了，动不了；重新派活也没用，得等后台修好"
# 对话发不出去、按任务事实重开时给模型的说明
_RESTART_NOTICE = (
    "[系统] 上一段对话里有内容发不出去，已经整段丢掉。上面是原指令、待办与目标记录，下面是宿主刚读的现场；"
    "按这些接着做，之前看过的东西要用就重新查。"
)

# 只读查询目标的操作：一整轮都在查还在跑的目标、没做别的，就是在轮询等结果。
_GOAL_POLLING_OPERATIONS = frozenset({"get", "list"})


class MinecraftAgent(BaseAgent):
    """Minecraft 游戏 Agent（AI 玩家，普通 ReAct Agent）

    实现方式：
    - 构造注入：所有依赖经 ``__init__`` 参数传入（可 mock / 可替换）
    - list_tools：声明 Agent 专属工具（provider="minecraft"），经 registry 注册
    - 局部工具（todo/notebook）由 LLM 直接驱动；MaiCraft 工具（maicraft_*）经
      registry 动态发现并透传
    """

    # ----- 元数据 -----
    name = "minecraft"
    description = "Minecraft 游戏 Agent（AI 玩家，ReAct）"

    # ----- 事件族声明 -----
    emits_events = (
        CoreEvents.GAME_MILESTONE,
        CoreEvents.GAME_REPORT,
        CoreEvents.GAME_ATTENTION_REQUIRED,
        CoreEvents.GAME_ERROR,
    )

    def __init__(
        self,
        config: MinecraftConfig,
        *,
        llm_manager: Optional[Any] = None,
        prompt_manager: Optional[Any] = None,
        event_bus: Optional[EventBus] = None,
        tool_registry: Optional[ToolRegistry] = None,
        thinking_sink: Optional[Any] = None,
        task_tracker: Optional[Any] = None,
        skill_library: Optional[SkillLibrary] = None,
        creature_repo: Optional[Any] = None,
        clock: Optional[Callable[[], float]] = None,
        live_active: Optional[bool] = None,
        mcp_client: Optional[Any] = None,
        mcp_provider: Optional[Any] = None,
        mcp_client_factory: Optional[Callable[..., Any]] = None,
        mcp_provider_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        """初始化 Minecraft Agent。

        Args:
            config: MinecraftConfig 实例
            llm_manager: 可选 LLMManager（ReAct 循环用；无则任务失败 fast-fail）
            prompt_manager: 可选 PromptManager（渲染系统提示词）
            event_bus: 可选 EventBus（emit game.* 事件）
            tool_registry: 可选 ToolRegistry（注册 Agent 专属工具 + 动态发现 MCP 工具）
            thinking_sink: 可选思考流旁路出口（鸭子类型：任何带
                ``on_thinking_delta(round_id, phase, step, seq, text_delta)`` 方法的对象）。
            task_tracker: 通用任务基建。委派任务的账面由本 Agent 写；后台目标同时记进账本，
                主播的任务查询与长时间无进展告警复用它。
            skill_library: 技能库。注入时系统提示词附技能目录，并提供
                ``minecraft_skill`` 按名读正文；本地是直播策略，玩法技能从 MaiCraft 的
                ``resources/list`` 合并进来，正文按 ``skill://`` 地址读。
            creature_repo: 生物名册仓储（``list_creatures`` / ``save_creatures``）。注入时名册按世界
                落库、跨场次记得主播起的名字与谁死了；None 时名册只在内存里，Agent 重建即丢。
            clock: 保留给测试注入假时钟的构造参数（统一构造签名）。
            live_active: 直播闸初始态。None = 生产默认（构造期按直播中，启动钩子落收工态，
                等 live.started 恢复）；显式给值（测试用）则启动钩子不再改写。
            mcp_client / mcp_provider: 预装配好的 Agent 私有 MCP 客户端与 provider 实例。
                注入了任一实例即视为私有 MCP 已装配，启动钩子跳过真实连接（测试替身自管注册）。
            mcp_client_factory / mcp_provider_factory: 私有 MCP 客户端 / provider 的构造工厂，
                形态分别与 ``McpClient(name, config)`` 和 ``MaicraftMcpProvider(client=…,
                server_name=…, provider=…, goal_lookup=…)`` 一致；缺省 None 走本类的默认构造。
        """
        super().__init__(event_bus=event_bus)
        # 时间流逝统一经此时钟：缺省用系统单调时钟，测试注入假时钟冻结或推进时间
        self._clock = clock or time.monotonic
        self._skills = skill_library
        self.typed_config = config
        # llm_manager 另需 async generate（ReAct 推理用）；视觉查询契约在装配期校验，
        # 注入了缺方法的对象时启动即报错，而不是运行期静默按非视觉处理
        validate_llm_capability(llm_manager, ProfileVisualCapabilityLookup, parameter="llm_manager")
        self._llm = llm_manager
        self._prompt = prompt_manager
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._thinking_sink = thinking_sink
        self._logger = get_logger("MinecraftAgent")

        # Agent 内部状态（内存，不持久化）：工作文档按存档分开，换存档经 switch_world 一处切换
        self._mc_state: MinecraftAgentState = MinecraftAgentState()
        # 角色眼下在哪个存档里：GoalWatch 每读一页、每次读自己时喂入（认存档的唯一记录）
        self._world = WorldIdentity()
        # 技能世界支撑件：目录合并、正文按需读、按连接缓存的装载资料与开局代读。
        # 构造早于工具 Provider——minecraft_skill 的执行后端从这里取
        self._skill_support = SkillSupport(
            skills=skill_library,
            agent_name=self.name,
            state=self._mc_state,
            logger=self._logger,
            maicraft_ready=self._maicraft_ready,
            mcp_client=self._current_mcp_client,
            execute_tool=self._execute_tool,
            absorb_reply=self._absorb_reply,
        )
        # 生物名册：自己的生物与主播起过名的生物是谁、在不在身边、死没死（速览、起名与死讯共用这一份）
        self._creatures = CreatureBook()
        self._creature_repo = creature_repo
        # 饿急了从别人家箱子拿过的吃的：按世界记最近几笔放进任务事实，主人回头来问时按名字、区域或位置对上号
        self._food_taken = FoodTakenLog()
        # 局部工具执行器：LLM 循环直接调（不依赖 registry；有 registry 时同一实例注册）
        self._tool_provider: MinecraftToolProvider = MinecraftToolProvider(
            state=self._mc_state,
            report_callback=self._handle_report,
            wait_callback=self._request_wait,
            glance_reader=self._glance,
            creature_namer=self._name_creature,
            skill_reader=self._skill_support.read_skill if skill_library is not None else None,
            save_reader=self._world.game_save,
            context_recall=self._recall_context if config.context.active_fold else None,
        )

        # 命令驱动运行骨架：worker 等命令信号，收到命令后持续推进当前游戏任务。
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._wake_event: asyncio.Event = asyncio.Event()
        # 消息队列：队列项协议（谁能开批、谁能被替换、谁能被撤）收在 WakeQueue 一处；
        # 本 Agent 只做投递、消费与"能否开批"的判断
        self._wake_queue = WakeQueue()
        # 原始指令跟随逻辑任务，不能因一次后台通知重新开批就丢失。
        self._task_instructions: List[str] = []
        self._task_steps = 0
        self._task_finished = True
        self._task_suspended = False
        # 这一批是不是闲着时只因别的玩家冲角色做了动作被叫醒：是的话只回应互动，不开任务、不续任务、
        # 回完安静收尾（不兜底交付、不催促、不许上报），已交付或等定夺的状态原样留着
        self._interaction_only = False
        # 身体因模型调用失败而停下时的说法（主播看一眼时读到）；空串 = 没出故障。
        # 和"卡住了等主播拿主意"分开：出故障时重新派活不一定有用，主播不该以为身体在等她
        self._body_fault = ""
        # 同一逻辑任务跨后台等待沿用历史，只有新任务或集中整理才重建前缀。
        self._messages: List[Dict[str, Any]] = []
        self._context_compactor = MinecraftHistoryCompactor(llm_manager, config.context)
        # 最近一次完整进入历史的任务状态；唤醒续做只补交它之后的变化。
        self._shown_context: Dict[str, Any] = {}
        self._idle_prep: BackgroundCheckpoint[None] = BackgroundCheckpoint("minecraft 等待期间历史整理")
        # 后台折叠：历史过了折叠线，另发一次与行动步同前缀的请求，请模型把用完的区间折成摘要，
        # 原文进块库可取回；身体照常行动，写好的折叠在下一步开始前换上。开关关掉时不编号、不折叠
        self._fold_enabled = config.context.active_fold
        self._folds = ContextFolds(TokenCounter(), is_pinned=is_pinned_message, recall_hint=RECALL_HINT)
        self._fold_pacer = FoldPacer()
        self._fold_job: BackgroundCheckpoint[FoldProposal] = BackgroundCheckpoint("minecraft 后台折叠")
        # 开新任务、重开对话时加一：在这之前发起的折叠提议对应的是已丢掉的历史，算完也不再套用
        self._fold_epoch = 0
        # 工具刚返回的截图：历史里只留文字回执，真图挂在下一次推理请求的尾部，用完即弃
        self._transient_images = TransientImages()
        # 相邻两次推理请求的历史只该在尾部追加；集中整理与开新任务之外的改写记告警
        self._prefix_guard = PrefixGuard("MinecraftAgent")
        # 委派账：批内追踪（两份任务号清单）与账面写入的单一管辖；批任务号
        # 只读视图供目标账本联动结算。批生命周期由本 Agent 驱动，账只管记账
        self._delegations = DelegationBook(task_tracker=task_tracker, agent_name=self.name)

        # 后台目标账本：在跟踪的目标此刻的样子、最近结束的目标、被暂停的目标，
        # 以及目标 ↔ 任务账/委派账的记账联动（通知路由留在 Agent）。
        # 批任务号供给在此一次注入：联动结算的写方法内部自取，不会出现漏传
        self._goal_ledger = GoalLedger(
            task_tracker=task_tracker, agent_name=self.name, finished_ids=lambda: self._delegations.finished_ids
        )
        # 最近一条身体急事收尾的那句话与收到的时刻：紧接着到的暂停通知带上它，说清主任务为什么停在半路。
        self._urgent_before_pause: Optional[tuple[str, float]] = None

        # Agent 私有 MCP（_on_start 装配；构造注入实例或工厂时按注入装配）
        self._mcp_client: Optional[Any] = mcp_client
        self._mcp_provider: Optional[Any] = mcp_provider
        self._mcp_client_factory = mcp_client_factory
        self._mcp_provider_factory = mcp_provider_factory
        self._mcp_ready_once = False
        self._goals = GoalWatch(
            call=self._call_maicraft,
            connected=self._maicraft_ready,
            on_goal_changed=self._on_goal_changed,
            on_goal_gone=self._on_goal_gone,
            on_body_event=self._on_body_event,
            on_decision_asked=self._on_decision_asked,
            on_page=self._on_events_page,
            wait_ms=config.events_wait_ms,
        )

        # 本批 ReAct 是否正在跑；本批期间的身体事件（上报时随 payload 带上任务上下文）
        self._batch_active: bool = False
        self._batch_body_events: List[Dict[str, Any]] = []
        self._body_notified: set = set()
        # 主播素材发布器：身体事件原文 → game.attention_required（长文先概述，后台发布）
        self._attention_material = AttentionMaterialPublisher(
            llm_getter=lambda: self._llm,
            emit=lambda message, scene=None: self._emit_game_event("attention_required", message, scene=scene or ""),
        )
        # 挂在消息队列里、还没被模型读到的那条天色通知由 WakeQueue 簿记：新的天色来了就把它换掉，
        # 队列里只留最新一条。
        self._task_reported = False
        self._wait_requested = False

        # 平台暂停支持（AgentControl pause/resume 经 _on_pause/_on_resume 进入）
        self._paused = asyncio.Event()
        self._paused.set()
        # 直播生命周期闸：启动期按"无活跃场次"落收工态，live.started 才恢复接受委派；
        # 构造时显式注入 live_active 的（测试装配）以注入值为准，启动钩子不改写
        self._live_active = True if live_active is None else live_active
        self._live_active_injected = live_active is not None
        self._running = False

        self._logger.info(f"MinecraftAgent 已构造 (llm={'已注入' if llm_manager else '无'}@{MINECRAFT_PROFILE})")

    @property
    def _message_queue(self) -> Deque[Any]:
        """消息队列本体的只读别名（测试与通知去重直接读；写入走 ``self._wake_queue`` 的投递方法）。"""
        return self._wake_queue.items

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def _on_start(self) -> None:
        """启动钩子：注册专属工具 + 装配 Agent 私有 MCP + 启动命令 worker 与目标跟踪。"""
        if self._tool_registry is not None:
            self._register_tools()
            await self._bind_agent_owned_mcp()
        self._running = True
        if not self._live_active_injected:
            self._live_active = False
        self._worker_task = asyncio.create_task(self._worker())
        self._goals.start()
        if self._event_bus is not None:
            self._event_bus.on(CoreEvents.LIVE_STARTED, self._on_live_started, model_class=LiveStartedPayload)
            self._event_bus.on(CoreEvents.LIVE_ENDED, self._on_live_ended, model_class=LiveEndedPayload)
            # 聊天流里的死亡播报：认出是名册里哪一只，就把她的死当成游戏进展讲给主播
            self._event_bus.on(CoreEvents.GAME_CHAT_RECEIVED, self._on_game_chat, model_class=GameChatPayload)
        self._logger.info("MinecraftAgent 已启动（命令驱动：等待委派指令；当前收工态，开播后接受委派）")

    async def _on_live_started(self, event_name: str, payload: LiveStartedPayload, source: str) -> None:
        """live.started 回调：开播，恢复接受委派。"""
        del event_name, source
        self._live_active = True
        # 名册按场次决定已死的还提不提：死在上一场的，这一场是最后一次提起
        self._creatures.start_session(payload.live_session_id)
        await self._save_creatures()
        self._logger.info(f"场次已开启（id={payload.live_session_id}）：minecraft 恢复接受委派")

    async def _on_live_ended(self, event_name: str, payload: LiveEndedPayload, source: str) -> None:
        """live.ended 回调：收工即停，未处理消息一并丢弃，不留到下一场突然开跑。"""
        del event_name, source
        self._live_active = False
        self._creatures.end_session()
        dropped = len(self._message_queue)
        self._wake_queue.clear()
        settled = self._delegations.settle_on_live_end()
        self._logger.info(
            f"场次已结束（id={payload.live_session_id}）：minecraft 收工"
            f"（在途批跑完当前步即停；丢弃 {dropped} 条未处理消息；强制结算 {settled} 个悬置委派任务）"
        )

    async def _bind_agent_owned_mcp(self) -> None:
        """装配 Agent 私有 MCP server（[agents.minecraft.mcp]）——失败常驻重试。

        provider 常驻登记到 ToolRegistry（逐工具名单 fail-closed：只有 minecraft 可见，
        主播读游戏状态走 minecraft_glance）。连接失败以 0 工具降级登记，后台退避重试；
        "Mod 没开"是常态而非事故，Agent 启动不阻断。
        """
        if self._tool_registry is None or not self.typed_config.mcp.enabled:
            return
        if self._mcp_client is not None or self._mcp_provider is not None:
            # 构造注入了私有 MCP 实例：装配已完成（测试替身自管注册），不再真实连接
            return
        # 函数内 import：mcp 模块涉及 fastmcp 重型依赖，按"可选重型依赖延迟加载"只在启动期导入一次。
        from src.modules.mcp.client import McpClient

        client = (
            self._mcp_client_factory(PROVIDER, self.typed_config.mcp)
            if self._mcp_client_factory is not None
            else McpClient(name=PROVIDER, config=self.typed_config.mcp)
        )
        self._mcp_client = client
        prov: MaicraftMcpProvider = (
            self._mcp_provider_factory(
                client=client, server_name=PROVIDER, provider=PROVIDER, goal_lookup=self._call_maicraft
            )
            if self._mcp_provider_factory is not None
            else MaicraftMcpProvider(
                client=client, server_name=PROVIDER, provider=PROVIDER, goal_lookup=self._call_maicraft
            )
        )
        self._mcp_provider = prov
        self._mcp_provider = prov
        prov.on_connection_changed = self._on_mcp_connection_changed
        prov.switch_config = {"file": "agents.toml", "key": "agents.minecraft.mcp.enabled"}
        try:
            count = await prov.setup()
        except Exception as exc:  # noqa: BLE001 - 装配异常按连接失败同径降级
            self._logger.warning(f"Agent 私有 MCP 装配异常: {type(exc).__name__}: {exc}")
            count = 0
        try:
            self.register_tool_provider(prov, registry=self._tool_registry)
        except Exception as exc:  # noqa: BLE001 - 注册异常兜底
            self._logger.warning(f"Agent 私有 MCP 注册失败: {type(exc).__name__}: {exc}")
            return
        self.register_mcp_client(client)
        if count > 0:
            return
        self._logger.warning("Agent 私有 MCP 连接失败或 server 未暴露工具，已降级登记（0 工具），后台退避重试装配")
        prov.start_recovery()

    def _on_mcp_connection_changed(self, connected: bool) -> None:
        """私有 MCP 连接态跃迁：断开摘除工具；连上按首次装配或恢复分流收尾。

        断开也刷新登记：provider 清单在断开路径已置空，refresh 把降级前的
        工具从注册表整体摘除，避免 Agent 调到必然失败的陈旧条目。
        """
        if not connected:
            try:
                self._tool_registry.refresh_provider_tools(self._mcp_provider)
            except Exception as exc:  # noqa: BLE001 - 刷新失败不影响连接态本身
                self._logger.warning(f"Agent 私有 MCP 断开刷新失败: {type(exc).__name__}: {exc}")
            return
        prov = self._mcp_provider
        count = len(list(prov.list_tools())) if prov is not None else 0
        if not self._mcp_ready_once:
            self._mcp_ready_once = True
            self._logger.info(f"Agent 私有 MCP 已就绪：{count} 个工具")
            return
        self._logger.info(f"Agent 私有 MCP 装配恢复：{count} 个工具")
        try:
            report = self._tool_registry.refresh_provider_tools(prov)
        except Exception as exc:  # noqa: BLE001 - 刷新失败下轮恢复重试再补
            self._logger.warning(f"Agent 私有 MCP 恢复刷新失败: {type(exc).__name__}: {exc}")
            return
        if report.get("ok"):
            self._logger.info(f"Agent 私有 MCP 工具集已换血（新增 {len(report.get('added', []))} 个）")
            self._on_mcp_recovered()

    def _on_mcp_recovered(self) -> None:
        """连接恢复：Mod 可能重启过，能力清单、远端技能与事件流都重新读；在做的任务收到通知后评估现场。"""
        self._skill_support.reset_connection_cache()
        self._goals.forget_stream()
        self._inject_wakeup_message(
            "[系统] Minecraft 连接已恢复，maicraft 工具重新可用。若此前因连接失败受阻，请评估现场并继续原任务。"
        )
        self._task_suspended = False
        self._wake_event.set()

    def _maicraft_ready(self) -> bool:
        client = self._mcp_client
        return client is not None and bool(getattr(client, "connected", False))

    def _current_mcp_client(self) -> Optional[Any]:
        """当前 MCP 客户端（支撑件经取值回调读；连接恢复会整体换实例，不缓存）。"""
        return self._mcp_client

    def _has_maicraft_tools(self) -> bool:
        """maicraft 至少有一个工具：连接失败时按 0 个工具降级登记，没开私有 MCP 时连 provider 都没有。"""
        provider = self._mcp_provider
        return provider is not None and bool(list(provider.list_tools()))

    async def _on_stop(self) -> None:
        """停止钩子：取消命令 worker、目标跟踪与恢复循环，摘除工具并关闭 MCP。"""
        self._running = False
        self._wake_event.set()
        if self._event_bus is not None:
            self._event_bus.off(CoreEvents.GAME_CHAT_RECEIVED, self._on_game_chat)
        await self._idle_prep.cancel()
        await self._fold_job.cancel()
        await self._goals.stop()
        for task in (self._worker_task,):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - 边界兜底
                self._logger.warning(f"后台协程退出异常: {exc}")
        self._worker_task = None
        self._mcp_provider = None
        self._skill_support.reset_connection_cache()
        removed = self.unregister_tool_providers()
        await self.close_mcp_clients()
        self._mcp_client = None
        self._logger.info(f"MinecraftAgent 已停止（摘除 {removed} 个工具）")

    async def _on_pause(self) -> None:
        """暂停钩子：任务循环在步骤间挂起（不打断当前工具调用）。"""
        self._paused.clear()

    async def _on_resume(self) -> None:
        """恢复钩子：任务循环继续。"""
        self._paused.set()

    # ==================================================================
    # 观测面（只读快照，供 dashboard REST 查询；无任何写入口）
    # ==================================================================

    def memo_snapshot(self) -> Dict[str, Any]:
        """备忘录/待办/近期上报三元组快照（只读）：``GET /agents/minecraft/memo`` 的数据源。"""
        return {
            "notebook": self._mc_state.notebook,
            "todos": [{"content": t.content, "status": t.status} for t in self._mc_state.todos],
            "reports": [dict(r) for r in self._mc_state.reports],
        }

    # ==================================================================
    # 工具提供（list_tools）
    # ==================================================================

    def list_tools(self) -> Iterable[ToolSpec]:
        """声明 Agent 专属工具（provider="minecraft"）。"""
        return list(self._tool_provider.list_tools())

    def _register_tools(self) -> None:
        if self._tool_registry is None:
            return
        # 可见名单随各 spec 声明（MinecraftToolProvider.list_tools 逐 spec 盖）；
        # 没有技能库时技能工具本就不提供，名单不残留。
        self.register_tool_provider(self._tool_provider, registry=self._tool_registry)
        self._logger.info(
            "MinecraftAgent 工具已注册：minecraft_todo / notebook / report / wait / get_work_log / glance"
        )

    def _tool_definitions(self) -> List[Dict[str, Any]]:
        """本批交给模型的工具：可见名单里的全部，减去只给宿主读的事件流；按名字排序，发送顺序稳定。"""
        if self._tool_registry is None:
            self._logger.warning("任务执行无 tool_registry：工具列表为空")
            return []
        specs = [
            spec
            for spec in self._tool_registry.list_tools(for_agent=self.name)
            if spec.full_name not in HOST_ONLY_TOOLS
        ]
        # 定义形状由工具域的 LLM 转换层统一生成（llm_defs），附上下文计量归属
        return [
            function_definition(spec, self._tool_registry.category_of(spec.full_name))
            for spec in sorted(specs, key=lambda s: s.full_name)
        ]

    # ==================================================================
    # 命令驱动 ReAct（命令 → 消息队列 → 持续推进任务 → 回空闲）
    # ==================================================================

    def receive_prompt(self, *, content: str, source: str = "") -> bool:
        """接收递话（纯文本留言，不派任务、不进账本）：入队 + 唤醒；收工态拒收。"""
        if not self._live_active:
            self._logger.warning(f"收工态拒收递话（source={source or '未知'}）：{content[:60]}")
            return False
        label = PROMPT_SOURCE_LABELS.get(source, "")
        self._wake_queue.push_instruction("", f"{label}\n{content}" if label else content)
        self._wake_event.set()
        self._logger.info(f"MinecraftAgent 收到递话（source={source or '未知'}）：{content[:60]}")
        return True

    def receive_delegation(self, *, instruction: str, task_id: str) -> Optional[str]:
        """接收委派（framework_delegate）：指令入队（带任务号）+ 唤醒。

        拒收一律回一句人话理由（不返回 None）：调用方把 None 当"受理成功"，
        拒收却不给理由就会留下一条账面已受理、执行侧从没收到的僵尸任务。
        收工态拒收并如实记账；游戏没连上时拒收并说明原因。
        """
        if not self._live_active:
            self._logger.warning(f"收工态拒收委派（task_id={task_id}）：{instruction[:60]}")
            self._delegations.reject(task_id, summary="收工态拒收（下播即收工）")
            return LIVE_NOT_ACTIVE
        if not self._has_maicraft_tools():
            # 开播了但 maicraft 一个工具都没有（客户端没开、MCP 没连上）：接下来没有任何办法操作角色，
            # 接了只会拿截屏翻显示器找游戏窗口白耗几分钟。直接拒收，原因原样回给派活的一方，等连上再派。
            self._logger.warning(f"游戏没连上，拒收委派（task_id={task_id}）：{instruction[:60]}")
            return GAME_NOT_CONNECTED
        self._wake_queue.push_instruction(task_id, instruction)
        self._wake_event.set()
        self._logger.info(f"MinecraftAgent 收到委派（task_id={task_id}）：{instruction[:60]}")
        return None

    def cancel_task(self, task_id: str, source: str = "", reason: str = "") -> bool:
        """硬取消：清委派追踪清单 + 账面 cancelled + 注入停手通知（不打断当前工具调用）。

        还排在消息队列里、没被任何批次吸收的委派单同样可撤：直接出队并记账，
        不注入停手通知（模型根本没见过这条指令，无需停手）。
        ``reason`` 非空时原文附进停手通知，供下一轮读到取消原因。
        """
        if self._wake_queue.drop_task(task_id):
            self._delegations.reject(task_id, summary=f"被取消（source={source or '未知'}）")
            self._logger.info(f"MinecraftAgent 队列中的委派已撤（task_id={task_id}, source={source or '未知'}）")
            return True
        if not self._delegations.cancel(task_id, summary=f"被取消（source={source or '未知'}）"):
            return False
        notice = (
            f"[系统] 任务 {task_id} 已被取消，请停止相关工作；若还有在跑的游戏目标，"
            "用 maicraft_goal(operation=cancel) 取消。"
        )
        if reason:
            notice = f"{notice}理由：{reason}"
        self._inject_wakeup_message(notice)
        self._logger.info(f"MinecraftAgent 任务已取消（task_id={task_id}, source={source or '未知'}）")
        return True

    async def _cancel_tracked_goals(self) -> None:
        """逐个向游戏直连取消在跑目标：让 Mod 立刻停手，不等游戏 Agent 下一轮推理。

        单个目标取消失败（游戏没连上、目标已结束）只记 warning，不阻断其余目标；
        取消成功后目标结束事件经事件流回来，跟踪名单与账面由既有路径收尾。
        """
        for goal_id in list(self._goals.tracked_ids()):
            try:
                reply = await self._call_maicraft(GOAL, {"operation": "cancel", "goal_id": goal_id})
            except Exception as exc:  # noqa: BLE001 - 单个目标失败不阻断其余目标
                self._logger.warning(f"直连取消游戏目标 {goal_id} 异常：{exc}", exc=True)
                continue
            if not reply.ok:
                self._logger.warning(
                    f"直连取消游戏目标 {goal_id} 未成功：{reply.error_message or reply.error_code or '未知原因'}"
                )

    async def cancel_task_now(self, task_id: str, source: str = "", reason: str = "") -> bool:
        """急取消（运营 REST 用）：先经直连通道让游戏立刻停手，再走 cancel_task 记账与通知。"""
        await self._cancel_tracked_goals()
        return self.cancel_task(task_id, source=source, reason=reason)

    async def stop_actions(self, reason: str = "", *, cancel_delegations: bool = False) -> bool:
        """急停：直连取消所有在跑游戏目标并注入叫停理由，游戏 Agent 下一轮读到后调整。

        默认不撤委派单（角色停手但任务账不动）；``cancel_delegations`` 为真时
        连名下委派单一起撤（复用 cancel_task 的记账与通知路径）。
        """
        await self._cancel_tracked_goals()
        if cancel_delegations:
            for task_id in self._delegations.tracked_ids():
                self.cancel_task(task_id, source="operator")
        notice = "[系统] 操作员叫停了当前动作。"
        if reason:
            notice = f"[系统] 操作员叫停了当前动作。理由：{reason}"
        self._inject_wakeup_message(notice)
        self._logger.info(f"MinecraftAgent 急停（cancel_delegations={cancel_delegations}）：{reason[:60]}")
        return True

    async def _worker(self) -> None:
        """命令工作协程：等待命令信号 → 执行目标任务 → 回到等待（空闲零消耗）。"""
        while self._running:
            await self._wake_event.wait()
            self._wake_event.clear()
            if not (self._running and self._message_queue) or not self._live_active:
                continue
            # 任务交付或挂起后等主播新指令，后台通知只补充已知状态，不独立开新任务；
            # 闲着也叫醒的通知除外，判定收在 WakeQueue（can_wake）一处
            if (self._task_finished or self._task_suspended) and not self._wake_queue.can_wake():
                continue
            try:
                await self._run_task()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 任务失败不杀死 worker
                self._logger.exception(f"任务执行异常: {exc}")
                await self.emit_error(f"任务执行异常: {exc}")

    async def _run_task(self) -> None:
        """执行单个任务批，直到批次终止语义命中。

        批次终止语义（全部系统可判定）：
        1. LLM 调 minecraft_report(kind=delivery) → 停止（工具内交付门禁校验）
        2. LLM 调 minecraft_report(kind=escalation) → 停止，等主播
        3. 自然终止，无 report、无在跑的目标且待办完成 → 系统兜底交付
        4. 自然终止，仅剩在跑的后台目标 → 静默让出，等目标事件唤醒
        5. 需要行动却在提醒后继续停顿 → 挂起，等待主播指令
        """
        if self._llm is None:
            await self.emit_error("无法执行任务：LLM 未注入")
            return
        self._batch_active = True
        self._batch_body_events.clear()
        self._body_notified.clear()
        try:
            await self._run_task_batch()
        finally:
            self._batch_active = False
            self._interaction_only = False

    async def _run_task_batch(self) -> None:
        self._task_reported = False
        self._wait_requested = False
        # 先读能力清单（开局资料与本地策略的环境都用它），再登记 MaiCraft 的玩法技能目录，最后写系统提示词。
        await self._skill_support.ensure_ability_listing()
        # 玩法技能由 MaiCraft 随包发布：目录也每次连接只读一次，正文在模型要读时才取。
        self._skill_support.drop_remote_if_disconnected()
        await self._skill_support.ensure_remote_skills()
        system_message = self._system_message()
        tool_defs = self._tool_definitions()
        await self._settle_idle_compaction(tool_defs)
        if not self._messages:
            self._messages.append(dict(system_message))
        messages = self._messages
        close_interrupted_calls(messages)
        # 闲着（已交付或等主播定夺）时只因别的玩家冲角色做了动作被叫醒：这一批只回应互动，
        # 回个动作就收尾；不续原任务，所以也不把原任务的事实再摊开一遍
        self._interaction_only = (self._task_finished or self._task_suspended) and self._wake_queue.interaction_only()
        if not self._interaction_only and not self._task_finished and self._task_instructions:
            # 后台目标有了结果先恢复原目标、待办与目标状态，再让模型解释这次通知。
            messages.append(
                {
                    "role": "user",
                    "content": "[继续原游戏任务]\n" + json_text(self._continue_task_context()),
                    FACTS_KEY: True,
                }
            )

        steps = 0
        opening_due = False
        mc_round = f"mc_{uuid.uuid4().hex[:12]}" if self._thinking_sink is not None else ""
        mc_seq_box = [0]
        action_reminded = False
        polling_reminded = False
        # 请求发不出去时的补救进度：换过回执、重开过对话；请求一发成功就清零，下次再出事从头补救
        stripped = restarted = False
        while self._running:
            await self._paused.wait()
            if not self._live_active:
                self._logger.info(f"已收工，任务批停止（{steps} 步）")
                return
            if self._message_queue:
                action_reminded = False
            while self._message_queue:
                queued = self._wake_queue.pop()
                task_id, content = queued
                # 是否开任务收在 WakeQueue.opens_task 一处：闲着也叫醒的通知（计时到点、Mod 挂出的决策）
                # 轮到读时才看任务状态——已交付就和一条指令一样开新任务（计时的备注、要回答的决策就是
                # 这次要做的事）；挂起着就接着原任务做，不记成原始指令；任务进行中只是一条普通通知。
                opened = opens_task(queued, task_finished=self._task_finished)
                if opened or isinstance(queued, WakeEvenIdle):
                    # 同一批里来了指令、计时到点或决策：照常开任务或续任务，不再只是回应互动
                    self._interaction_only = False
                if opened:
                    if self._task_finished:
                        self._start_new_task(messages, system_message)
                        opening_due = True
                    self._task_instructions.append(content)
                    self._task_finished = False
                    self._task_suspended = False
                    self._task_steps = 0
                elif isinstance(queued, WakeEvenIdle):
                    self._task_suspended = False
                if task_id:
                    self._delegations.absorb(task_id)
                incoming: Dict[str, Any] = {"role": "user", "content": content}
                if opened:
                    # 开任务的原话是任务的依据：后台折叠与收起都不碰它
                    incoming[INSTRUCTION_KEY] = True
                messages.append(incoming)
            if opening_due:
                opening_due = False
                await self._skill_support.append_opening_bundle(messages)
            if not self._interaction_only:
                # 只回应互动的一批不碰委派账：等主播定夺的委派要一直显示在等定夺，不能被一次蹲起改回进行中
                self._delegations.mark_running()
            steps += 1

            # 先换上后台写好的折叠、给新消息编号，再看预算；过了折叠线就在后台请模型折叠，这一步照常行动
            self._apply_ready_folds(messages, tool_defs)
            self._number_messages(messages)
            if not await self._prepare_context(messages, tool_defs):
                return
            if self._fold_enabled:
                # 行动期间也在后台备好整理：先请模型折叠，跟不上时再起集中整理；角色都不用等
                self._schedule_idle_compaction(messages, tool_defs)
            self._task_steps += 1

            on_delta = self._build_thinking_callback(mc_round, steps, mc_seq_box) if mc_round else None
            # 上一步看过的截图只挂在这一次请求尾部，不写回历史：下一步请求时它已消失，
            # 历史前缀与这一次逐字一致，缓存照常命中；画面内容由模型这一步的说明带下去
            tail = self._transient_images.drain()
            request_messages = [*messages, tail] if tail is not None else messages
            self._prefix_guard.observe(messages)
            try:
                response = await self._llm.generate(
                    request_messages, profile=MINECRAFT_PROFILE, tools=tool_defs, on_delta=on_delta
                )
            except asyncio.CancelledError:
                raise
            except UnsendableRequestError as exc:
                # 请求因内容被拒，原样重发必然再被拒：先把回执里的二进制换成说明重试一次 ->
                # 还不行就按任务事实重开对话再试 -> 仍不行才停下，主播看一眼时读到身体出故障了
                if not stripped and strip_unsendable(messages):
                    stripped = True
                    self._prefix_guard.expect_rewrite("换掉发不出去的回执")
                    self._logger.warning(f"MinecraftAgent 请求发不出去，回执里的二进制已换成说明，重试一次: {exc}")
                    continue
                if not restarted:
                    stripped = restarted = True
                    self._restart_conversation(messages, system_message)
                    opening_due = True
                    self._logger.warning(f"MinecraftAgent 请求仍发不出去，按任务事实重开对话: {exc}")
                    continue
                self._task_suspended = True
                self._body_fault = _FAULT_STUCK
                self._logger.error(f"MinecraftAgent 请求发不出去，换掉回执、重开对话都没用，任务停下: {exc}", exc=True)
                await self.emit_error(f"请求发不出去：{exc}；换掉回执、重开对话都没用，任务停在这里，重新派活也不会好")
                return
            except Exception as exc:  # noqa: BLE001 - 单步失败转错误事件
                self._task_suspended = True
                self._body_fault = _FAULT_RETRY
                self._logger.warning(f"MinecraftAgent LLM 推理异常: {type(exc).__name__}: {exc}", exc=True)
                await self.emit_error(f"LLM 推理异常: {type(exc).__name__}: {exc}；本任务已保留，等待新指令继续")
                return
            if not response.success:
                self._task_suspended = True
                self._body_fault = _FAULT_RETRY
                await self.emit_error(f"LLM 调用失败: {response.error or '未知错误'}")
                return
            self._body_fault = ""
            stripped = restarted = False

            tool_calls = response.tool_calls or []
            # 响应转回 assistant 消息（含线协议转换与思考回传）由 LLM 模块统一承载；
            # 换到备用模型时思考由引擎丢弃，这里只管原样存
            messages.append(response_to_assistant_message(response))

            # 中间工具调用步骤的正文进观察面响应卡；自然终止轮的正文由交付卡承载，不重复发。
            if tool_calls and (response.content or "").strip() and self._event_bus is not None:
                await self.emit_event(
                    CoreEvents.AGENT_REPLIED,
                    AgentRepliedPayload(
                        agent=self.name,
                        content=(response.content or "").strip(),
                        round_id=mc_round,
                        step=steps,
                        model=response.model or "",
                        llm_request_id=response.request_id,
                    ),
                )

            if not tool_calls:
                if not await self._end_without_tool_calls(messages, response.content or "", steps, action_reminded):
                    return
                action_reminded = True
                continue

            polling_only = True
            for call in tool_calls:
                await self._paused.wait()
                arguments = call.arguments if isinstance(call.arguments, dict) else {}
                if call.name == "minecraft_wait" and len(tool_calls) != 1:
                    observation: Dict[str, Any] = {
                        "ok": False,
                        "error": "minecraft_wait 必须单独调用；先完成本轮其他动作",
                    }
                else:
                    observation = await self._execute_tool(call.name, arguments, round_id=mc_round)
                self._absorb_reply(call.name, arguments, observation)
                polling_only &= self._polls_running_goal(call.name, arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(observation, ensure_ascii=False, default=str),
                    }
                )

            if self._task_reported:
                self._logger.info(f"LLM 已上报，任务批次结束（{steps} 步）")
                return
            if self._wait_requested and not self._message_queue:
                self._schedule_idle_compaction(messages, tool_defs)
                return
            self._wait_requested = False
            if polling_only and not self._message_queue:
                # 一整轮只在查还在跑的目标：第一次提醒用 minecraft_wait 等，第二次直接替它让出。
                if polling_reminded and self._request_wait().get("waiting"):
                    self._logger.info("连续轮询在跑的目标，已并入 minecraft_wait，等目标事件唤醒")
                    self._schedule_idle_compaction(messages, tool_defs)
                    return
                messages.append(
                    {
                        "role": "user",
                        "content": "[系统] 这一轮只在查询还在跑的目标。宿主盯着事件流，目标有结果、提问或被暂停时会通知你；"
                        "有可推进或可提前准备的事就去做，没有就单独调用 minecraft_wait。",
                    }
                )
                polling_reminded = True
            else:
                polling_reminded = False

    def _restart_conversation(self, messages: List[Dict[str, Any]], system_message: Dict[str, Any]) -> None:
        """这段对话发不出去：整段丢掉，只留系统说明与任务事实重开；原指令、待办、目标记录照旧。

        重开后由调用方补一份开局资料，模型按事实与新读的现场接着做，不必等主播重新派活。
        """
        facts = self._current_task_context()
        messages[:] = [
            dict(system_message),
            {"role": "user", "content": "[原任务事实与已有证据]\n" + json_text(facts), FACTS_KEY: True},
            {"role": "user", "content": _RESTART_NOTICE},
        ]
        self._shown_context = deepcopy(facts)
        # 丢掉的那段对话里看过的截图也一并作废；在写的折叠提议与集中整理对应的历史已经没了，
        # 当场作废：集中整理写完会就地改写历史，晚一步就把旧摘要写进重开后的对话
        self._transient_images.drain()
        self._fold_epoch += 1
        self._fold_job.abandon()
        self._idle_prep.abandon()
        self._prefix_guard.expect_rewrite("对话发不出去，按任务事实重开")

    def _start_new_task(self, messages: List[Dict[str, Any]], system_message: Dict[str, Any]) -> None:
        """上一个任务已交付，新指令开新任务：历史、待办与目标记录重新开始。"""
        self._task_instructions.clear()
        messages[:] = [dict(system_message)]
        # 上个任务最后看过还没来得及用的截图属于旧现场，新任务不带
        self._transient_images.drain()
        self._prefix_guard.expect_rewrite("开新任务")
        self._context_compactor.checkpoints = 0
        # 上个任务的折叠块与请折叠的节奏都不带进新任务；后台还在写的折叠与集中整理当场作废，
        # 否则集中整理写完会把上个任务的摘要套到新任务的历史上
        self._folds.clear()
        self._fold_pacer.reset()
        self._fold_epoch += 1
        self._fold_job.abandon()
        self._idle_prep.abandon()
        self._shown_context = {}
        self._mc_state.set_todos([])
        self._goal_ledger.clear_finished()
        # 新任务只带系统说明和新指令：这个世界里拿过别人家的吃的另附一条事实，主人来问时一开工就认得出
        taken = self._food_taken.view(self._creatures.world_key)
        if taken:
            messages.append({"role": "user", "content": "[拿过别人家的吃的]\n" + json_text(taken), FACTS_KEY: True})

    async def _end_without_tool_calls(
        self, messages: List[Dict[str, Any]], content: str, steps: int, reminded: bool
    ) -> bool:
        """模型这一轮没调用工具：按终止语义收尾。返回 True 表示给过提醒、继续推进。"""
        if self._interaction_only:
            # 只回应互动的一批：回完就收尾，不兜底交付（不然别人蹲一下主播就念一遍），也不为原任务催促
            self._logger.info(f"回应互动后收尾（{steps} 步），任务状态原样留着")
            return False
        pending = self._goals.tracked_ids()
        actionable = self._goal_ledger.actionable_goal_ids()
        if actionable or (not pending and self._unfinished_todos()):
            return await self._continue_after_no_progress(
                messages, reminded, "仍有目标在等你回答或被暂停，或还有未完成待办"
            )
        if pending:
            self._logger.info(f"任务批次自然终止（{steps} 步），{len(pending)} 个后台目标在跑，静默让出")
        elif not self._task_reported:
            delivery = content.strip()
            await self._emit_report("delivery", delivery or "任务完成")
            self._task_finished = True
            self._delegations.finish("succeeded", summary=delivery or "任务完成")
            self._logger.info(f"任务批次自然终止（{steps} 步），系统兜底交付")
        return False

    async def _continue_after_no_progress(self, messages: List[Dict[str, Any]], reminded: bool, reason: str) -> bool:
        """先带着当前目标状态提示模型推进；仍不行动就挂起上报，等主播指令。"""
        if reminded:
            self._logger.warning(f"Minecraft 任务无新进展：{reason}")
            await self._suspend_with_report(f"模型未推进需要行动的任务：{reason}；已保留原目标和待办，等待继续指令")
            return False
        messages.append(
            {
                "role": "user",
                "content": "[任务尚需行动] "
                + reason
                + "。回答目标提出的问题、继续或取消被暂停的目标、推进下一项待办；确实无法推进时用 escalation 上报。\n"
                + json_text(
                    {"goals_needing_you": self._goal_ledger.notes_for(self._goal_ledger.actionable_goal_ids())}
                ),
            }
        )
        return True

    # ==================================================================
    # 工具执行与结果吸收
    # ==================================================================

    async def _execute_tool(self, name: str, arguments: Dict[str, Any], *, round_id: str = "") -> Dict[str, Any]:
        """串行执行单个工具调用：统一经 ToolRegistry（观测/停用/熔断复用既有机制）。

        MaiCraft 工具的观察就是它原样的 JSON 回复；连接失败等拿不到回复时写明原因。
        registry 工具的观察走对话安全视图：base64 二进制载荷（截图等）属
        tool.result 事件的 UI 展示通道，在对话里以占位符出现。视觉调用方
        且工具结果带图时，图经 absorb_result_images 入库换句柄、登记到易逝图，
        下一次推理请求时挂在序列最后让模型看一次；历史里始终只有文字观察。
        """
        if self._tool_registry is None:
            return {"ok": False, "error": "工具执行失败：tool_registry 未注入", "tool": name}
        result = await self._tool_registry.invoke(
            ToolInvocation(
                tool_name=name,
                arguments=arguments,
                source="minecraft-react",
                round_id=round_id,
                caller_profile=MINECRAFT_PROFILE,
            )
        )
        if name.startswith(f"{PROVIDER}_"):
            return reply_of(result).raw
        if result.success:
            data: Dict[str, Any] = (
                dict(result.structured_content)
                if isinstance(result.structured_content, dict)
                else {
                    "ok": True,
                    "content": result.content,
                }
            )
            observation = to_conversation_view(data)
            absorb_result_images(self._transient_images, self._llm, MINECRAFT_PROFILE, name, result)
            return observation
        return {"ok": False, "error": result.error_message or "工具执行失败", "tool": name}

    async def _call_maicraft(self, name: str, arguments: Dict[str, Any]) -> MaicraftReply:
        """宿主自己读 MaiCraft（目标跟踪、看一眼）：直接经私有 provider，不进模型的工具记录。"""
        provider = self._mcp_provider
        if provider is None:
            return MaicraftReply(ok=False, error_code="not_connected", error_message="游戏连接还没就绪")
        result: ToolExecutionResult = await provider.invoke(
            ToolInvocation(tool_name=name, arguments=arguments, source="minecraft-host")
        )
        return reply_of(result)

    def _absorb_reply(self, name: str, arguments: Dict[str, Any], observation: Dict[str, Any]) -> None:
        """从 execute / task 的回复里认出目标运行：没结束的开始跟踪，状态变化记下，结束的收起。"""
        if name not in (EXECUTE, GOAL) or observation.get("ok") is not True:
            return
        data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
        run = goal_run_of(data)
        if run is None:
            return
        if name == EXECUTE:
            if run.finished:
                # 当场完成的目标（记地点、只读分析）没有后台过程，结果已经在回复里；
                # 账面仍要记：先登记再写终态，直接写会因条目不存在而落空（终态变化广播不出来）。
                self._goal_ledger.remember_finished(run)
                self._goal_ledger.register_and_settle(run)
                return
            self._track_goal(run)
            return
        if arguments.get("operation") in ("cancel",) or run.finished:
            self._goals.untrack(run.goal_id)
            self._goal_ledger.forget(run.goal_id)
            self._goal_ledger.remember_finished(run)
            self._goal_ledger.update_task_ledger(run)
            return
        if not self._goals.tracking(run.goal_id) and arguments.get("operation") == "resume":
            # 重启或重进世界后恢复的目标：模型明确解除暂停，从此由宿主跟踪。
            self._track_goal(run)
            return
        if self._goals.tracking(run.goal_id):
            self._goal_ledger.note(run)
            self._goal_ledger.update_task_ledger(run)

    def _polls_running_goal(self, name: str, arguments: Dict[str, Any]) -> bool:
        """这次调用只是在查询还在跑的目标（没提问、没被暂停），不算推进。

        带 ``path`` 的 get 读的是结束了的目标里折起来的证据，不是在等进展：结束的目标本来就已移出跟踪名单，
        这里再明说一次，不靠名单碰巧对得上——宿主还没收到结束事件时，模型照着结果去读证据也不会被并入等待。
        """
        if name != GOAL or arguments.get("operation") not in _GOAL_POLLING_OPERATIONS:
            return False
        if arguments.get("path"):
            return False
        tracked = self._goals.tracked_ids()
        if not tracked or self._goal_ledger.actionable_goal_ids():
            return False
        goal_id = arguments.get("goal_id")
        return arguments.get("operation") == "list" or goal_id in tracked

    # ==================================================================
    # 后台目标（宿主盯事件流；变化回到这里，需要模型处理的才唤醒）
    # ==================================================================

    def _track_goal(self, run: GoalRun) -> None:
        self._goals.track(run)
        self._goal_ledger.track(run)
        self._logger.info(f"开始跟踪目标 {run.goal_id}（{run.ability}）：{run.state}")

    async def _on_goal_changed(
        self, run: GoalRun, after_world_change: bool = False, self_brief: str = "", stalled: bool = False
    ) -> None:
        """事件流说目标变了：记账，并在需要模型处理时带着结果唤醒任务。

        ``after_world_change``：事件流换了编号（重进世界或游戏重启）后重新核对时查到的样子。
        ``self_brief``：结束事件带来的结束那一刻角色的一行自身摘要，没有时为空串。
        ``stalled``：Mod 侧停滞判定引来（目标长时间没有新事件仍未结束）——记账本中间态
        并转发"有目标停着没动静"给主播；不另唤醒模型（Mod 节流过，模型侧由任务账本
        的停滞告警兜底）。
        """
        before = self._goal_ledger.note_of(run.goal_id)
        if run.finished:
            self._goal_ledger.settle_finished(run)
            self._inject_wakeup_message(finish_notice(run, self_brief))
            return
        self._goal_ledger.note(run)
        if stalled:
            self._goal_ledger.note_stalled(run)
            await self._emit_game_event(
                "attention_required",
                f"有目标停着没动静：目标 {run.goal_id}（{goal_label(run)}）很长时间没有新事件，还没结束。",
            )
            return
        self._goal_ledger.update_task_ledger(run)
        if run.state == AWAITING_ANSWER and before.get("question") != run.question:
            self._inject_wakeup_message(
                f"[系统] 目标 {run.goal_id}（{goal_label(run)}）在等你回答：{json_text(run.question)}。"
                f"用 maicraft_goal(operation=answer, goal_id={run.goal_id}, answer=选项编号) 回答。"
            )
        elif run.state == PAUSED and before.get("state") != PAUSED:
            # 急事用一次就清掉，不拿去解释之后别的暂停；是否新鲜由通知函数按当下时刻判定
            urgent, self._urgent_before_pause = self._urgent_before_pause, None
            self._inject_wakeup_message(pause_notice(run, after_world_change, urgent, self._clock()))

    async def _on_decision_asked(self, run: GoalRun) -> None:
        """Mod 自己挂出的决策（死亡恢复）在等回答：带着问题与选项叫醒模型，让它按问题里给的选项选。

        决策不是本 Agent 下达的目标：不进跟踪名单、不记任务账本。
        闲着、挂起、任务进行中都要叫醒：没人回答，角色就一直停在死亡屏幕。普通通知在交付或挂起后不开批，
        所以和计时到点一样排成闲着也叫醒的通知：交付过就由这条决策开一个新任务，挂起着就接着原任务做，
        进行中只是一条普通通知。
        """
        self._wake_even_idle(
            f"[系统] Mod 挂出的决策（{run.ability}，goal_id={run.goal_id}）在等你回答：{json_text(run.question)}。"
            f"按问题里给的选项，用 maicraft_goal(operation=answer, goal_id={run.goal_id}, answer=选项编号) 回答。"
        )

    async def _on_goal_gone(self, goal_id: int, reason: str) -> None:
        """在跟踪的目标查不到了（多半是换了世界）：如实告诉任务，不让它挂着等不来的结果。"""
        self._goal_ledger.mark_gone(goal_id, reason)
        self._inject_wakeup_message(f"[系统] 目标 {goal_id} 不在了：{reason}。按现场重新决定下一步。")

    async def _on_body_event(self, event: Dict[str, Any]) -> None:
        """身体事件记进本批上下文，并按路由表分派到通道。

        每类事件走哪条通道、对模型说什么话，由 ``notice_router`` 的路由表定义
        （唤醒语义与文案的取舍在那里一处可查）；本方法只执行通道动作。
        """
        kind = str(event.get("kind") or "")
        message = str(event.get("message") or kind)
        if kind != PLAYER_BODY_LANGUAGE:
            # 别的玩家的肢体动作不记进随上报带给主播的身体事件：一个人连着蹲能来十几条，会把饿了、死了这些挤掉，
            # 主播那边也已经由注意流采集器一条条知道了
            record = {"event_type": kind, "message": message, "cursor": event.get("cursor")}
            self._batch_body_events.append(record)
            if len(self._batch_body_events) > MAX_BATCH_BODY_EVENTS:
                self._batch_body_events = self._batch_body_events[-MAX_BATCH_BODY_EVENTS:]
        if kind == OTHERS_FOOD_TAKEN:
            # 闲着时也要记：主人多半是过一阵才来问，那时候模型得从任务事实里认出是自己拿的
            self._food_taken.record(self._creatures.world_key, food_taken_of(event, now_ms()))
        # 死的时候手上正跑着寻死目标：是自己选的死（回重生点），通知换成"回家"的说法、不发主播素材
        chosen_death = kind == "character_died" and any(
            is_suicide_ability(ability) for ability in self._goals.tracked_abilities()
        )
        route = route_body_event(kind, chosen_death=chosen_death)
        if route.urgent_marker:
            # 急事收尾：撤离甩开追兵后人已不在原地，Mod 写"主任务停在半路等下一步指示"，紧接着把主任务挂成暂停。
            # 记下这句交给随后的暂停通知，游戏 Agent 才知道是撤离把角色带离了原地，不会猜成游戏重连。
            self._urgent_before_pause = (message, self._clock())
        if route.channel == "carry_sky":
            self._carry_sky_notice(route.template.format(message=message))
        elif route.channel == "wake_even_idle":
            self._wake_even_idle(route.template.format(message=message))
        elif route.channel == "wake_for_interaction":
            self._wake_for_interaction(route.template.format(message=message))
        elif route.channel == "inject_if_busy":
            if self._batch_active or not self._task_finished:
                self._inject_wakeup_message(
                    body_notice_content(route, message, history=self._messages, queue=self._message_queue)
                )
            if route.publish_material:
                # 必须传 Mod 原话：白话速览的分句改写器会整段丢弃排查类分句（实测 30 句输入只剩
                # 一句"有件事没办成"），排查细节全灭；长度交给素材发布器（长文压缩、短文直通）
                self._attention_material.schedule(message)
        elif route.channel == "attention_event":
            if not self._batch_active or (kind, message) in self._body_notified:
                return
            self._body_notified.add((kind, message))
            await self._emit_game_event(
                "attention_required", attention_notice(route, message), already_resolved=route.already_resolved
            )

    def _wake_even_idle(self, content: str) -> None:
        """排一条闲着也要叫醒模型的通知（计时到点、Mod 挂出的决策），并叫醒 worker。

        排成 WakeEvenIdle 而不是普通通知：worker 认得它，交付或挂起后也开批；轮到读时再按任务状态
        决定开新任务、接着原任务做，还是当普通通知读。
        收工态不排：下一场开播时突然冒出一条上一场的提醒或决策，只会让模型去看过时的东西。
        """
        if not self._live_active:
            self._logger.info(f"收工态不叫醒：{content[:60]}")
            return
        self._wake_queue.push_wake_even_idle(content)
        self._wake_event.set()

    def _wake_for_interaction(self, content: str) -> None:
        """排一条互动通知（别的玩家冲角色做了肢体动作）并叫醒 worker。

        任务进行中它是一条普通通知；闲着时它也开批，但那一批只回应互动（见 ``_interaction_only``）。
        收工态不排：下播后有人冲她蹲起没人看，开播时再冒出来只是过时的事。
        """
        if not self._live_active:
            self._logger.info(f"收工态不叫醒：{content[:60]}")
            return
        self._wake_queue.push_interaction(content)
        self._wake_event.set()

    def _carry_sky_notice(self, content: str) -> None:
        """挂一条不唤醒模型的通知，队列里只留最新一条（天色用）。

        随本批下一步或下一次唤醒（目标结束、提问、暂停、主播新指令）带给模型；
        任务已交付时不挂：空闲的游戏 Agent 不会处理它，等到下个任务才读到只会是过时的天色，
        还没被读到的上一条也一并撤下。替换与只留最新的簿记收在 WakeQueue。
        """
        if not self._batch_active and self._task_finished:
            self._wake_queue.drop_sky_notice()
            return
        self._wake_queue.replace_sky_notice(content)

    def on_task_notification(self, payload: TaskChangedPayload) -> None:
        """账本的变化回到这里：目标的成败已经由事件流直接送达，账本只补"长时间没进展"的告警。"""
        goal_id = goal_id_of_ledger(payload.task_id)
        if goal_id is None or not getattr(payload, "alert", False) or not self._goals.tracking(goal_id):
            return
        self._inject_wakeup_message(
            f"[系统] 目标 {goal_id} 很久没有新进展：{payload.summary}。用 maicraft_goal(operation=get) 看看它在做什么，"
            "需要时取消或换办法。"
        )

    # ==================================================================
    # 认出存档
    # ==================================================================

    async def _on_events_page(self, page: Any) -> None:
        """GoalWatch 每读到一页：页里带着角色眼下在哪个存档（换存档时 Mod 会当场叫醒这次读取）。"""
        await self._apply_world_change(self._world.absorb(page.world_fields))

    async def _apply_world_change(self, change: Optional[WorldChange]) -> None:
        """事件页或 observe(self) 认出了新的存档：切换工作文档；没变化什么也不做。"""
        if change is not None:
            await self._on_world_identified(change)

    async def _on_world_identified(self, change: WorldChange) -> None:
        """认出存档：进程启动后第一次认出只记下；从一个存档换到另一个时按以下顺序处理。

        收起旧存档的待办、笔记与近期上报，换上新存档的一份（回到老存档会还原）→ 手上有任务时
        告诉它这个任务是在旧存档里接的，先看眼前再决定继续还是上报 → 发一条换存档的里程碑，
        主播的时间线上记下"从「A」换到了「B」"、存储据此知道这一场后半段玩的是哪个存档。
        """
        self._mc_state.switch_world(change.after.id)
        if not change.switched or change.before is None:
            self._logger.info(f"认出所在存档：「{change.after.label()}」")
            return
        before, after = change.before.label(), change.after.label()
        self._logger.info(f"角色换了存档：「{before}」→「{after}」，工作文档已切换")
        if not self._task_finished:
            self._inject_wakeup_message(
                f"[系统] 角色进了另一个世界「{after}」（原来在「{before}」）。在「{before}」记的待办和工作笔记已经收起，"
                f"回到「{before}」会还原；手上这个任务是在「{before}」里接的，先看看眼前再决定继续做还是用 escalation 上报。"
            )
        await self._emit_game_event("milestone", f"从存档「{before}」换到了「{after}」")

    def _inject_wakeup_message(self, content: str) -> None:
        self._wake_queue.push_notice(content)
        self._wake_event.set()

    # ==================================================================
    # 等待、任务状态与历史整理
    # ==================================================================

    def _request_wait(self) -> Dict[str, Any]:
        """只允许对在跑的目标让出；在等回答或被暂停的目标不会自己往下走。

        只回应互动的一批例外：回完动作就让出，不管原任务有没有在等回答的目标（那是闲下来之前就挂着的事）。
        """
        if self._interaction_only and not self._message_queue:
            self._wait_requested = True
            return {
                "ok": True,
                "waiting": True,
                "resume_on": "回应完互动，回到原来的状态：主播发来新指令，或闲着也要叫醒的事",
            }
        actionable = self._goal_ledger.actionable_goal_ids()
        if actionable:
            return {
                "ok": False,
                "error": "有目标在等你回答或被暂停了，等待不会推进；先处理它们",
                "goals": self._goal_ledger.notes_for(actionable),
            }
        tracked = self._goals.tracked_ids()
        if not tracked:
            # 只设了计时、没有目标在跑：计时到点不管这一轮结没结束都会叫醒，说清这点，免得模型为等计时去上报阻塞。
            return {
                "ok": False,
                "error": "没有在跑的目标；继续推进待办，或上报阻塞。只在等计时不用让出：到点会叫醒你，这一轮结束了也会叫",
            }
        if self._message_queue:
            return {"ok": True, "waiting": False, "reason": "已有新消息，请处理最新事实"}
        self._wait_requested = True
        return {
            "ok": True,
            "waiting": True,
            "goals": self._goal_ledger.notes_for(tracked),
            "resume_on": "目标结束、提问、被暂停、计时到点，或主播发来新指令",
        }

    def _unfinished_todos(self) -> bool:
        return any(todo.status != "done" for todo in self._mc_state.todos)

    def _current_task_context(self) -> Dict[str, Any]:
        """恢复与集中整理都保留：原始指令、待办、笔记、在跑的目标与最近结束的目标，
        以及主播给生物起的名字（有才带：主播说"带上阿缕"时，身体要认得出阿缕是哪一只）
        和这个世界里拿过别人家的吃的（有才带：主人来问时认得出是自己拿的）。"""
        context: Dict[str, Any] = {
            "original_instructions": list(self._task_instructions),
            "todo": self._mc_state.todo_doc()["todos"],
            "notebook": self._mc_state.notebook,
            "background_goals": [deepcopy(note) for note in self._goal_ledger.all_notes_sorted()],
            "recently_finished_goals": self._goal_ledger.recent_finished(),
            "reasoning_steps_used": self._task_steps,
        }
        named = self._creatures.named_view()
        if named:
            context["named_creatures"] = named
        # 拿过别人家的吃的（有才带）：主播转来"谁拿了我的面包"时，按主人名、区域或箱子位置认出是自己拿的
        taken = self._food_taken.view(self._creatures.world_key)
        if taken:
            context["others_food_taken"] = taken
        return context

    def _continue_task_context(self) -> Dict[str, Any]:
        """续做时相对前文最近一份完整任务状态只交付变化；首次续做交付完整状态。"""
        current = self._current_task_context()
        shown = self._shown_context
        self._shown_context = deepcopy(current)
        if not shown:
            return current
        delta = {key: value for key, value in current.items() if shown.get(key) != value}
        delta["unchanged_since_earlier_task_state"] = sorted(key for key in current if key not in delta)
        return delta

    def _schedule_idle_compaction(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> None:
        """让出等待时若历史已过提前整理线，就在后台整理；身体此刻本来就在等游戏结果。
        开了后台折叠时每步行动前也调一次：整理在后台写，角色照常行动。

        后台折叠优先：写好的先换上，过了折叠线先请模型折；折叠还在写时不并行起集中整理，
        两边同时改写历史会把彼此记下的位置对乱。
        """
        self._apply_ready_folds(messages, tools)
        self._number_messages(messages)
        self._maybe_start_fold(messages, tools)
        if self._idle_prep.busy or self._fold_job.busy:
            return
        trigger = self._context_compactor.watermarks().prepare
        if self._context_compactor.measure(messages, tools) <= trigger:
            return
        facts = self._current_task_context()
        self._idle_prep.start(self._compact_while_waiting(messages, tools, facts, trigger))

    async def _compact_while_waiting(
        self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], facts: Dict[str, Any], trigger: int
    ) -> None:
        try:
            if await self._context_compactor.compact(messages, tools, facts, trigger_tokens=trigger):
                self._shown_context = deepcopy(facts)
                # 整理就地改写了历史前段，之后的第一次请求重算前缀是预期内的
                self._prefix_guard.expect_rewrite("后台集中整理")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 提前整理失败不影响任务，醒来后按正常预算处理
            self._logger.warning(f"后台的历史整理未完成，原历史已保留：{exc}", exc=True)
        finally:
            self._task_steps += self._context_compactor.last_calls

    async def _settle_idle_compaction(self, tools: List[Dict[str, Any]]) -> None:
        """醒来时结清提前整理：已超预算就等它完成，否则放弃以便立刻处理新事实。

        开了后台折叠时两样都不做：让它在后台接着写，超预算由每步前的收起兜住，角色不用等它。
        """
        if not self._idle_prep.busy or self._fold_enabled:
            return
        if self._context_compactor.measure(self._messages, tools) > self._context_compactor.watermarks().budget:
            await self._idle_prep.wait()
            return
        await self._idle_prep.cancel()

    async def _prepare_context(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> bool:
        """历史超预算才整理；失败保留原件并挂起，避免无上下文地继续操作游戏。

        开了后台折叠时先不等摘要：把最老的大条记录收起来（原文进块库可取回），不调模型、
        瞬间降到回落线，这一步照常行动。收起后仍超预算（大头不在可收起的记录里）才走同步
        整理；后台整理已经在跑就先等它做完，不并行起第二份改写。
        """
        marks = self._context_compactor.watermarks()
        tokens = self._context_compactor.measure(messages, tools)
        if tokens <= marks.budget:
            return True
        if self._fold_enabled:
            if self._stow_oldest(messages, tools, tokens):
                return True
            if self._idle_prep.busy:
                await self._idle_prep.wait()
                if self._context_compactor.measure(messages, tools) <= marks.budget:
                    return True
        facts = self._current_task_context()
        try:
            if await self._context_compactor.compact(messages, tools, facts):
                self._shown_context = deepcopy(facts)
                self._prefix_guard.expect_rewrite("集中整理")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 整理失败保留任务，不用半份摘要继续游戏
            self._logger.warning(f"Minecraft 历史整理失败，原任务已保留：{exc}", exc=True)
            await self._suspend_with_report(f"上下文整理失败，任务已保留：{exc}")
            return False
        finally:
            self._task_steps += self._context_compactor.last_calls
        return True

    def _stow_oldest(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], tokens: int) -> bool:
        """到了预算线又没有现成的摘要：收起最老的大条记录降到回落线，收起后不超预算返回 True。

        收起只把正文换成一行占位，消息位置、编号和调用配对都不变；占位写着块号，模型要用
        原文时自己读回。后台的折叠与集中整理照常进行，之后会把这些占位连同上下文压成摘要。
        """
        marks = self._context_compactor.watermarks()
        saved = self._folds.stow(
            messages,
            keep_recent=self.typed_config.context.fold_keep_recent,
            current_tokens=tokens,
            target_tokens=marks.target,
            min_tokens=STOW_MIN_TOKENS,
        )
        if saved <= 0:
            return False
        # 收起就地改写了历史中段，下一次请求重算前缀是预期内的
        self._prefix_guard.expect_rewrite("收起旧记录")
        after = self._context_compactor.measure(messages, tools)
        self._logger.info(
            f"历史 {tokens} token 到了预算线 {marks.budget}，不等摘要，先收起最老的记录：-> {after} token（原文可取回）"
        )
        return after <= marks.budget

    def _number_messages(self, messages: List[Dict[str, Any]]) -> None:
        """给还没发出过的新消息写上编号，模型折叠时用编号指定区间（关掉后台折叠时不编号）。"""
        if self._fold_enabled:
            self._folds.number(messages)

    def _maybe_start_fold(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> None:
        """历史过了折叠线、且距上次请模型又涨了一截时，在后台发一次整理请求；身体照常行动，不等它。

        集中整理正在后台跑时不发：两边同时改写历史会把彼此记下的位置对乱。
        """
        if not self._fold_enabled or self._llm is None or self._fold_job.busy or self._idle_prep.busy:
            return
        config = self.typed_config.context
        lines = fold_lines(self._context_compactor.watermarks().budget, config.fold_ratio)
        tokens = self._context_compactor.measure(messages, tools)
        if not self._fold_pacer.due(tokens, line=lines.line, growth=lines.growth):
            return
        # 发起即记基线：模型认为没东西可折时，要等历史再涨一截才再请
        self._fold_pacer.settle(tokens)
        ranges = self._folds.ranges(messages, keep_recent=config.fold_keep_recent, min_tokens=lines.min_range)
        if not ranges:
            return
        work = propose_folds(
            self._llm,
            list(messages),
            tools,
            ranges,
            tokens,
            max_chars=config.fold_summary_max_chars,
            profile=MINECRAFT_PROFILE,
        )
        self._fold_job.start(work, epoch=self._fold_epoch)
        self._logger.info(f"历史 {tokens} token 过了折叠线 {lines.line}，后台请模型折叠（{len(ranges)} 段可折区间）")

    def _apply_ready_folds(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> None:
        """后台折叠写好了就换上；集中整理正在后台改写历史时先不换，等它结束。

        提议基于发起时的那份历史算出：期间历史只在尾部追加，按编号重新定位即可；
        对应的任务已结束、对话已重开的提议直接丢弃。
        """
        if not self._fold_enabled or self._idle_prep.busy:
            return
        epoch = self._fold_job.meta.get("epoch")
        proposal = self._fold_job.take()
        if proposal is None:
            return
        if epoch != self._fold_epoch:
            self._logger.debug("后台折叠提议属于已结束的任务或已重开的对话，丢弃")
            return
        if proposal.error:
            self._logger.warning(f"后台整理请求失败，历史不变：{proposal.error}")
            return
        if proposal.ignored:
            self._logger.warning(f"后台整理请求里模型调了别的工具，已丢弃不执行：{'、'.join(proposal.ignored)}")
        if not proposal.requests:
            self._logger.info("后台整理请求：模型认为暂时没有可折的区间")
            return
        config = self.typed_config.context
        lines = fold_lines(self._context_compactor.watermarks().budget, config.fold_ratio)
        before = self._context_compactor.measure(messages, tools)
        outcome = self._folds.apply(
            messages,
            proposal.requests,
            keep_recent=config.fold_keep_recent,
            min_tokens=lines.min_range,
            max_summary_chars=config.fold_summary_max_chars,
        )
        for error in outcome.errors:
            self._logger.warning(f"后台折叠有一段没套上：{error}")
        if not outcome.blocks:
            return
        # 折叠就地改写了历史中段，下一次请求重算前缀是预期内的
        self._prefix_guard.expect_rewrite("后台折叠")
        after = self._context_compactor.measure(messages, tools)
        self._fold_pacer.settle(after)
        names = "、".join(block.block_id for block in outcome.blocks)
        self._logger.info(f"后台折叠落地：{names}，历史 {before} -> {after} token")

    def _recall_context(self, block: str, query: str) -> Dict[str, Any]:
        """取回被折叠或收起的原文（minecraft_context_recall 的执行后端）。"""
        return self._folds.recall(block=block, query=query, max_chars=RECALL_MAX_CHARS)

    def _build_thinking_callback(self, round_id: str, step: int, seq_box: List[int]) -> Any:
        """构造 LLM 层增量回调（duck-typed sink），只转发 reasoning 增量。"""
        sink = self._thinking_sink
        if sink is None:
            return None

        def _on_delta(kind: str, text_delta: str) -> None:
            if kind != "reasoning" or not text_delta:
                return
            seq_box[0] += 1
            sink.on_thinking_delta(
                round_id=round_id, phase="minecraft", step=step, seq=seq_box[0], text_delta=text_delta
            )

        return _on_delta

    # ==================================================================
    # 主播看一眼
    # ==================================================================

    async def _glance(self) -> Dict[str, Any]:
        """主播看一眼：读一次自己与周围，只留直播叙事用得上的事实，再附上身体手头的工作。

        结果最前面放一句话现场（``brief``）：主播的现场卡只放这一句白话，其余字段留给想细看时查。
        """
        view: Dict[str, Any] = {}
        if not self._maicraft_ready():
            view["unavailable"] = "游戏连接还没就绪，暂时看不到游戏里的情况"
        else:
            body = await self._call_maicraft(OBSERVE, {"what": "self"})
            view.update(glance_self(body))
            # 先更新名册再写身边：起过名的按名字称呼，墓碑对到死者
            scene = await self._read_creatures(body)
            view.update(glance_scene(scene, self._creatures.nicknames_by_scene_id()))
            creatures = self._creatures.status_view()
            if creatures:
                view["creatures"] = creatures
        view["work"] = self._glance_work()
        return {"tool": "glance", "brief": glance_brief(view), **view}

    async def _name_creature(self, who: str, nickname: str) -> Dict[str, Any]:
        """主播给游戏里的生物起名：先重读一次身边（起名对象以眼下为准），再在名册里登记。

        顺序：主播说"就叫你阿缕吧" → Planner 调起名工具 → 读一次身边 → 按速览里的称呼找到那只 → 登记；
        找不到、分不清是哪只、名字被占了都不登记，原因原样回给主播。
        """
        if self._maicraft_ready():
            await self._read_creatures(await self._call_maicraft(OBSERVE, {"what": "self"}))
        result = self._creatures.name(who, nickname)
        if result.named is None:
            return {"success": False, "error": result.reason}
        await self._save_creatures()
        creature = result.named
        if not creature.nickname:
            return {"success": True, "named": creature.label(), "note": "不再用主播起的名字称呼它"}
        self._logger.info(f"生物名册登记名字：{creature.label()}（身份 {creature.key}）")
        return {"success": True, "named": creature.label(), "note": "以后现场速览里按这个名字称呼它"}

    async def _on_game_chat(self, event_name: str, payload: GameChatPayload, source: str) -> None:
        """``game.chat.received`` 回调：系统消息若是名册里某只的死讯，把她的死当成游戏进展发给主播。

        顺序：自己的女仆被苦力怕炸死 → 游戏只给主人发一条"莫莫酒狐被苦力怕炸死了" → 名册认出是阿缕 →
        发 game.milestone"阿缕（你的女仆，莫莫酒狐皮肤）被苦力怕炸死了"：嘴当场接住，场次回顾也看得到。
        只记账、发事件，不开任务、不调模型，游戏 Agent 闲着时照样不花钱。

        认死者：消息带模板编号时只看死亡播报（``death.`` 开头），死者是消息开头写出名字的那个点名实体，
        按她的 UUID 对名册；旧版 Mod 不给模板编号与点名时，交给名册按文字认。
        """
        del event_name, source
        if payload.game != "minecraft" or payload.kind != "system":
            return
        if payload.message_key and not payload.message_key.startswith("death."):
            return
        victim = next(
            (m for m in payload.mentions if m.name and payload.content.strip().startswith(m.name)),
            None,
        )
        try:
            creature = self._creatures.note_death(
                payload.content,
                now_ms(),
                victim_uuid=victim.id if victim is not None else "",
                victim_name=victim.name if victim is not None else "",
            )
            if creature is None:
                return
            self._logger.info(f"生物名册认出死讯：{creature.label()}（原话：{payload.content}）")
            await self._save_creatures()
            await self._emit_game_event("milestone", f"{creature.label()}{creature.death_note}")
        except Exception as exc:  # noqa: BLE001 - 认不出死讯不影响聊天流本身
            self._logger.warning(f"按名册认死讯失败（原话：{payload.content}）", exc=exc)

    async def _read_creatures(self, body: MaicraftReply) -> MaicraftReply:
        """读一次周围并更新名册，返回这次的场景。

        顺序：按 observe(self) 认出所在的世界（认存档；换了世界先换上那个世界的名册）→ 读 observe(scene) →
        名册记下这次看到的 → 变化写回库。认不出世界时不更新名册：分不清这些生物属于哪个世界。
        """
        if body.ok:
            # 读自己的回复同样带着 world：顺手更新一次眼下的存档；是不是旧版 Mod 只看事件页，
            # 这次回复恰好缺 world 键时不把已认出的存档退回不分存档
            await self._apply_world_change(self._world.absorb_observation(body.data))
        await self._bind_creature_world(body)
        scene = await self._call_maicraft(OBSERVE, {"what": "scene"})
        if scene.ok and self._creatures.world_key is not None:
            self._creatures.observe(scene.data, now_ms())
            await self._save_creatures()
        return scene

    async def _bind_creature_world(self, body: MaicraftReply) -> None:
        """认出角色在哪个世界：刚启动或换了世界，就先把旧世界的名册写回，再读回这个世界的。

        世界编号取 observe(self) 的 ``world.id``（与认存档同一口径）。第一次按这个编号读名册前，
        如果 Mod 说这个世界的数据在旧版本里存在另一个编号名下（``world_key`` 与 ``world.id`` 不同），
        先把那个编号名下的名册搬过来（新编号名下已有名册就不搬）。``world`` 为 null（还没认出）
        时不换：分不清这些生物属于哪个世界。旧版 Mod 不报 ``world``：照旧按 ``world_key`` 分，
        也不报时按空串、各世界共用一份。读不到自己时不换。读库失败从空名册开始（只会少认出
        几只，不会改坏库里的记录）。
        """
        if not body.ok:
            return
        legacy_key = str(body.data.get("world_key") or "")
        if "world" in body.data:
            ref = world_ref_of(body.data.get("world"))
            if ref is None:
                return
            world_key = ref.id
        else:
            world_key = legacy_key
        if world_key == self._creatures.world_key:
            return
        await self._save_creatures()
        if world_key != legacy_key:
            await self._adopt_old_creatures(legacy_key, world_key)
        records: List[Any] = []
        if self._creature_repo is not None:
            try:
                records = await self._creature_repo.list_creatures(game=_GAME, world_key=world_key)
            except Exception as exc:  # noqa: BLE001 - 读不到名册不影响看一眼本身
                self._logger.warning(f"读生物名册失败（世界 {world_key or '未报编号'}），从空名册开始", exc=exc)
        self._creatures.load(world_key, [creature_from_record(record) for record in records])
        self._logger.info(f"生物名册换到世界 {world_key or '未报编号'}：读回 {len(records)} 只")

    async def _adopt_old_creatures(self, legacy_key: str, world_key: str) -> None:
        """旧版本按另一个编号存的这个世界的名册，搬到世界编号名下；搬不动只记日志，从现有记录读。"""
        if self._creature_repo is None or not legacy_key:
            return
        try:
            moved = await self._creature_repo.move_world(game=_GAME, from_key=legacy_key, to_key=world_key)
        except Exception as exc:  # noqa: BLE001 - 搬不动不影响看一眼，旧行留在原处下次再搬
            self._logger.warning(f"生物名册从旧编号搬到世界编号失败（{legacy_key} → {world_key}）", exc=exc)
            return
        if moved:
            self._logger.info(f"生物名册从旧编号搬到世界编号：{moved} 只（{legacy_key} → {world_key}）")

    async def _save_creatures(self) -> None:
        """把名册上次写回之后的变化写进库；没装存储或还没认出世界时只留在内存里。"""
        world_key = self._creatures.world_key
        if self._creature_repo is None or world_key is None:
            return
        changed, removed = self._creatures.take_changes()
        if not changed and not removed:
            return
        try:
            await self._creature_repo.save_creatures(
                game=_GAME,
                world_key=world_key,
                creatures=[creature_record(creature) for creature in changed],
                removed_keys=removed,
            )
        except Exception as exc:  # noqa: BLE001 - 写不进库时名册仍在内存里可用
            self._logger.warning(f"生物名册写回失败（{len(changed)} 只有变化，{len(removed)} 只移出）", exc=exc)

    def _glance_work(self) -> Dict[str, Any]:
        """身体手头的工作，写成主播自己的口吻：读这份结果的就是主播本人。"""
        if self._body_fault:
            state = self._body_fault
        elif self._task_suspended:
            state = "卡住了，正等我拿主意"
        elif not self._task_finished:
            state = "正在做手上的事"
        else:
            state = "空闲"
        work: Dict[str, Any] = {"state": state, "todo": self._mc_state.todo_doc()["todos"]}
        goals = [
            {key: note[key] for key in ("ability", "purpose", "state", "doing") if key in note}
            for note in self._goal_ledger.all_notes_sorted()
        ]
        if goals:
            work["goals"] = goals
        return work

    # ==================================================================
    # 上报
    # ==================================================================

    async def _suspend_with_report(self, reason: str) -> None:
        """角色已停止自动行动：主动上报待定夺，并保留原委派。"""
        self._task_suspended = True
        self._delegations.hold(reason)
        await self._emit_report("escalation", reason)

    async def _handle_report(self, kind: str, content: str, scene: str) -> Optional[str]:
        """minecraft_report：交付门禁通过后发射 game.report。返回拒绝原因；None = 受理。"""
        if self._interaction_only:
            return (
                "这一轮只是回应别的玩家冲你做的动作，不是一件要交付的活：不交付也不上报，主播那边已经知道了；"
                "回完动作就单独调用 minecraft_wait 结束这一轮，要说话用 chat"
            )
        running = self._goals.tracked_ids()
        if kind == "delivery" and running:
            return (
                f"还有 {len(running)} 个目标在跑（{', '.join(str(goal_id) for goal_id in running)}），不能交付——"
                "等它们结束、取消它们，或改用 escalation 说明情况"
            )
        if kind == "delivery" and self._unfinished_todos():
            return "仍有未完成待办，不能交付；继续推进，确实受阻时使用 escalation"
        await self._emit_report(kind, content, scene=scene)
        self._task_reported = True
        self._task_finished = kind == "delivery"
        self._task_suspended = kind == "escalation"
        if kind == "delivery":
            self._delegations.finish("succeeded", summary=f"{kind}: {content}")
        else:
            self._delegations.hold(f"{kind}: {content}")
        return None

    # ==================================================================
    # 系统提示词
    # ==================================================================

    def _system_message(self) -> Dict[str, Any]:
        """任务历史开头的系统消息；附技能目录时带计量标注，上下文面板据此把它记入技能段。"""
        catalog = self._skill_support.catalog_section()
        message: Dict[str, Any] = {"role": "system", "content": self._system_prompt() + self._fold_guide() + catalog}
        if catalog:
            message["context_parts"] = [{"section": SECTION_SKILLS, "name": "技能目录", "text": catalog}]
        return message

    def _fold_guide(self) -> str:
        """开了后台折叠时系统提示词附上的说明：消息编号、折叠块与取回工具；模板不可用时用内建短说明。"""
        if not self._fold_enabled:
            return ""
        if self._prompt is not None:
            try:
                return "\n\n" + self._prompt.render("amaidesu_minecraft_context_fold")
            except Exception as exc:  # noqa: BLE001 - 渲染失败降级内建短说明
                self._logger.warning(f"折叠说明提示词渲染失败，降级内建: {type(exc).__name__}: {exc}")
        return "\n\n" + FOLD_GUIDE_FALLBACK

    def _system_prompt(self) -> str:
        """系统提示词：渲染 prompt_manager 模板（无则用内建兜底）。"""
        if self._prompt is not None:
            try:
                return self._prompt.render("amaidesu_minecraft_agent")
            except Exception as exc:  # noqa: BLE001 - 渲染失败降级内建
                self._logger.warning(f"MinecraftAgent 提示词渲染失败，降级内建: {type(exc).__name__}: {exc}")
        # 模板不可用时仍要守住的底线：工具怎么用、什么才算交付、卡住怎么办、不走捷径。
        return (
            "你是 Minecraft 世界中的 AI 玩家，用工具玩 Minecraft。"
            "maicraft_observe 看自己与周围，maicraft_lookup 查能力，maicraft_execute 下达目标，"
            "maicraft_goal 查看、回答、暂停、继续或取消目标。身体同一时间只做一件事；需要动手的目标在后台推进，"
            "宿主会在它结束、提问或被暂停时通知你；没有可做的事就单独调用 minecraft_wait。"
            "复杂任务用 minecraft_todo 维护阶段。本次目标要的结果真的达成才交付，用 minecraft_report(kind=delivery)；"
            "拿别的顶替或只做一半不算，附带原话与本次目标冲突时听本次目标。"
            "卡住时读清失败原因、对症改一处、再换实质不同的办法；至少试过两种办法再用 "
            "minecraft_report(kind=escalation) 上报，写清试过什么，然后停止。"
            "像生存玩家一样玩：不用 /tp、/give 这类管理员命令，也不用创造模式专属的物品和方块走捷径。"
        )

    # ==================================================================
    # 事件上报（GamePayload(game="minecraft")）
    # ==================================================================

    async def _emit_game_event(
        self,
        event_type: Literal["milestone", "report", "attention_required", "error"],
        message: str,
        *,
        scene: str = "",
        report_kind: Optional[Literal["delivery", "escalation"]] = None,
        already_resolved: bool = False,
    ) -> None:
        """emit game.* 事件；叙事面事件带上本批任务期间的身体事件，主播据此讲"做着做着遇到了什么"。"""
        if self._event_bus is None:
            return
        body_events: List[Dict[str, Any]] = []
        if event_type in ("report", "attention_required"):
            body_events = [dict(item) for item in self._batch_body_events]
        # 事件发生时所在的存档由本 Agent 自己填（旧版 Mod 与不明时为空串）
        save_id, save_name = self._world.save_fields()
        payload = GamePayload(
            game="minecraft",
            event_type=event_type,
            message=message,
            scene=scene,
            save_id=save_id,
            save_name=save_name,
            report_kind=report_kind,
            already_resolved=already_resolved,
            body_events=body_events,
        )
        if event_type == "report":
            self._mc_state.add_report(report_kind or "", message, scene)
            await self.emit_event(CoreEvents.GAME_REPORT, payload)
        elif event_type == "attention_required":
            await self.emit_event(CoreEvents.GAME_ATTENTION_REQUIRED, payload)
        elif event_type == "milestone":
            await self.emit_event(CoreEvents.GAME_MILESTONE, payload)
        else:
            await self.emit_event(CoreEvents.GAME_ERROR, payload)

    async def _emit_report(self, kind: Literal["delivery", "escalation"], message: str, *, scene: str = "") -> None:
        await self._emit_game_event("report", message, scene=scene, report_kind=kind)

    async def emit_attention_required(self, message: str, *, scene: str = "") -> None:
        await self._emit_game_event("attention_required", message, scene=scene)

    async def emit_error(self, message: str, *, scene: str = "") -> None:
        await self._emit_game_event("error", message, scene=scene)

    # ==================================================================
    # 状态查询（测试/外部）
    # ==================================================================

    def get_state_snapshot(self) -> Dict[str, Any]:
        """导出状态快照（minecraft_get_work_log 同构；测试可断言）。"""
        return self._mc_state.to_dict()

    @property
    def tracked_goal_ids(self) -> List[int]:
        """在跟踪的后台目标编号（只读观测面：仪表盘与测试读，不提供写入口）。"""
        return self._goals.tracked_ids()

    @property
    def queued_messages(self) -> List[Tuple[str, Any]]:
        """还没被模型读到的消息队列快照（只读观测面）。"""
        return list(self._message_queue)

    @property
    def batch_active(self) -> bool:
        """本批 ReAct 是否正在跑（只读观测面）。"""
        return self._batch_active

    def observation(self) -> Dict[str, Any]:
        """任务推进状态快照（只读观测面）：批状态、任务状态、身体故障说法与本批身体事件。"""
        return {
            "batch_active": self._batch_active,
            "task_finished": self._task_finished,
            "task_suspended": self._task_suspended,
            "task_instructions": list(self._task_instructions),
            "body_fault": self._body_fault,
            "batch_body_events": [dict(record) for record in self._batch_body_events],
        }

    @property
    def paused(self) -> bool:
        return self._state == AgentState.PAUSED
