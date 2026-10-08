"""MinecraftAgent —— Minecraft 世界的 AI 玩家（事件驱动 ReAct Agent）

核心意象：一个用 MCP 工具玩 Minecraft 的普通 ReAct Agent。
- 主播 Agent 是它的用户：framework_delegate 委派派活、minecraft_get_work_log 读工作文档、
  minecraft_glance 看一眼游戏、minecraft_report 收上报
- 命令驱动（类 Code Agent）：空闲零消耗；委派指令唤醒任务，任务内持续推进
  ReAct 循环（LLM 推理 → 工具调用串行执行 → 观察作为观察返回），批次终止语义见
  ``_run_task``——无存在性心跳、无时间循环
- 系统提示词 + 工具列表 = 全部"编程"，不发明任何特殊协议
- 异步受理：Mod 施工与包内建筑设计返回受理回执（task_id），真实施工
  由游戏 tick 后台驱动（分钟级）——系统登记 handoff 跟踪，经资源订阅通知 +
  周期兜底核实任务快照，状态真迁移才注入消息唤醒 LLM（通知是提示可丢，
  task get 是事实源）；等待期 LLM 自由行动或让出回合，零空耗
- 通用游戏工具经 registry 动态发现；建筑设计的新协议由包内适配器对接，
  施工进度继续走通用任务跟踪。

继承 ``BaseAgent``（协议六项全部实现），构造注入依赖。
局部工具（todo/notebook/get_work_log/report，注册名 minecraft_*）声明 →
注册进 ToolRegistry。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from typing import Any, Deque, Dict, Iterable, List, Literal, Optional

from src.agents.minecraft.attention_matrix import upstream_timestamp_ms
from src.agents.minecraft.builder.controller import MinecraftBuilderController
from src.agents.minecraft.context import MinecraftHistoryCompactor, close_interrupted_calls, context_chars
from src.agents.minecraft.design_progress import MachineDesignProgress
from src.agents.minecraft.environment import read_installed_mods
from src.agents.minecraft.glance import SURROUNDINGS_SECTIONS, glance_situation, glance_surroundings
from src.agents.minecraft.observations import MinecraftObservations, json_text, repeated_read
from src.agents.minecraft.observation_context import project_context
from src.agents.minecraft.plan_facts import MinecraftPlanFacts
from src.agents.minecraft.polling_gate import FailureBackoffGate, NoProgressGate, default_clock_ms
from src.agents.minecraft.readback import is_reference, read_receipt
from src.agents.minecraft.state import MinecraftAgentState, MinecraftInstruction
from src.agents.minecraft.task_facts import decision_facts, machine_facts, task_decision
from src.agents.minecraft.tool_content import failed_observation, successful_observation
from src.agents.minecraft.tool_names import find_mod_tool
from src.agents.minecraft.tools import MinecraftToolProvider
from src.modules.agents.base import AgentState, BaseAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.agents import AgentRepliedPayload
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.llm.context_meter import SECTION_SKILLS
from src.modules.logging import get_logger
from src.modules.skills import SkillEnvironment, SkillLibrary, render_catalog
from src.modules.task_utils import spawn_background_task
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry

from .config import MinecraftConfig

__all__ = ["MinecraftAgent"]

# 游戏决策使用独立用途；建筑设计的模型预算由子 Agent 自己声明。
MINECRAFT_PROFILE = "minecraft"

# 让出等待后台任务时，历史超过预算的这一比例就提前整理；整理目标仍按完整预算的六成计算。
_IDLE_COMPACTION_RATIO = 0.7

# 开局资料里的周边段：位置、告示牌、设施盘点、附近生物与局部决策摘要，覆盖开局最常读的现场事实。
_OPENING_SURROUNDINGS_SECTIONS = (
    "position",
    "nearby_signs",
    "nearby_facilities",
    "nearby_entities",
    "local_decision_summary",
)

# 递话来源标签：运营在控制面直接说的话与主播决策循环递来的补充分开标注。
_PROMPT_SOURCE_LABELS = {"operator": "[运营原话]", "planner-react": "[主播补充]"}

# 身体事件单页上限：一次增量读取最多取几条注意流事件（超出部分下次接着读）
_ATTENTION_PAGE_LIMIT = 10

# 本批任务期间保留的身体事件条数上限（上报携带的任务上下文，多了只会淹没重点）
_MAX_BATCH_BODY_EVENTS = 5

# 游戏内动作连续失败到第 3 次时把卡点讲给主播，之后每再失败 5 次补报一次（只叙事，不打断任务）
_FAILURE_STREAK_NOTICE = 3
_FAILURE_STREAK_REPEAT = 5

# 失败唤醒退避：连续失败第 1/2 次推迟唤醒（30s/60s），第 3 次立即升级为 LLM 决策，
# 升级后仍按 120s 封顶节流——实证里失败事件一到就触发完整推理（间隔 1-2 秒）是最大忙等源
_FAILURE_WAKE_DELAYS_MS = (30_000, 60_000, 120_000)
_FAILURE_WAKE_ESCALATE_AFTER = 3

# 无进展决策合并闸：窗口内的再次"只重复读取"直接并入 minecraft_wait，不再发起推理
_NO_PROGRESS_WINDOW_MS = 60_000

# 角色按目标理解口语并推进施工；常驻提示只保留决策与访问边界，部件细节按需从 Mod 资料读取。
_GAMEPLAY_RULES = (
    # 做什么以主播发来的本次目标为准：原话离开直播间上下文容易读偏，只拿来核对物品名写法与禁用条件
    "\n目标理解：依据本次目标、场景和工艺决定要完成的结果，保留产物、地点、数量与模组限制；"
    "附带的来源原话只用来核对物品名写法与禁用条件，与本次目标冲突时听本次目标。"
    "口语、错别字或自定义机器名称不要求与注册名完全一致；要建造的机器也可以是生产目标物品的一组装置。"
    "找到符合目标的产物与工艺后，说明采用的解释并继续设计，无需先证明全库不存在同名整机。"
    "只有证据支持会造成实质不同结果的多个解释、且上下文无法取舍时才澄清；真正缺少目标时也应询问。"
    "把转述中的‘不明确先查证’作为解决实际疑问的提醒，不新增等待用户确认的步骤。"
    # 模板和内建提示共用取材原则，避免换提示入口后又排除手上的无线库存、先去远处搜索。
    "\n取材按最近原则：先用随身库存和可立即使用的 AE 无线现货，再找最近的世界资源。"
    "成品无现货时也先考虑无线库存中的可加工原料。普通取材交给 Mod 排序，默认省略 allowed_sources；"
    "只有用户明确限定来源才收窄，不能自行排除无线终端。任意木板等可替代材料使用物品标签，"
    "不按木种逐个试搜，也不先去找某种生物群系。"
    "\n机器建造：读取目标工艺 → perceive(view=construction_site) 勘测 → 生成显式 blueprint 与 assembly → "
    "plan(goal.ability=maicraft:build_machine) 后 execute(plan_id)。用户只要设计时不执行施工。"
    "命中相关工艺 URI 就读取正文；后续查询只补当前设计的具体缺口，不为确认名称遍历其他机器或全部 Ponder。"
    "蓝图格式按需读取 maicraft://knowledge/machine_assembly，部件由你组合选择，不用产品专用模板替代设计。"
    "目标写入 expected_output，禁用模组写入 constraints.forbidden_mods。供料、安装与施工检查交给 Mod；"
    "库存、供电和产出实测不是设计前置条件，design_machine 仅为可选审阅。"
    "plan_facts 或 _pending_execution 确认 ready 的计划直接提交；具体诊断或相关现场变化才触发补查与修订。"
    # 机器变更由完整施工链承担，避免模型把同一改造拆成手动敲块、拿一块支撑或逐次瞄准用桶。
    "\n修改已有机器：复用已有机器引用和仍有效的现场信息，提交 modify_machine 的 apply_blueprint 变更。"
    "让 Mod 执行声明范围内的拆换、源流体收放和缺料补给，再读实际回执与 diff 决定是否继续修改。"
    "人物层逐块交互、逐次用桶不替代机器修改流程；已有信息足够时直接提交，不重复勘测同一区域。"
    # 教程库存按工艺角色决定是否建造；资源需求绑定接收端，供料方式由现场条件选择。
    "\n资源与动力：按功能理解 Ponder 边界：创造马达表示应力 IN，仅作演示供料的保险库、箱子或流体罐"
    "表示外部材料 IN；机器所需内部缓存和产物收集仍须明确设计。结合旁白与原生接口判定库存角色，"
    "蓝图保留真实接收口并声明 external_inputs，再选择现场供给方式；声明本身不证明已经接通。"
    "允许某模组不等于授权创造资源。优先接入附近已授权的传动网，"
    # 接线是机器修改的操作名；明确所属能力，避免把操作名当独立能力反复查目录。
    "需要寻找时用 perceive(view=kinetic_sources,query=短名称或ID)，施工后用 "
    "modify_machine(operation=connect_external_input) 接线；"
    "局部勘测未发现接口不证明附近没有动力，发现候选也不授权连接地下、隔墙或私人网络。"
    "缺料优先使用AE网络，不授权翻陌生箱子；仅在玩家明确要求搜索，或告示牌、可信记忆、聊天说明、真实历史观察"
    "指向具体容器与目标材料时才定向取用，并遵守权限与保护范围。无合规库存来源时走已允许的合成或采集。"
    "no_space/inventory_capacity 先解决容量；已取得物品但 outcome_uncertain 时核验收尾，避免重复领取。"
    # 一批相同原生加工只提交语义产量，双手整理与逐次持用由 Mod 执行并结算。
    "\n重复加工：需要多次执行同一种有限原生物品加工时，读取 maicraft:use_item 的当前契约，"
    "优先一次提交支持的 count、ingredient_item_id 和 expected_output_item_id，由 Mod 准备双手、"
    "补充随身耗材并逐次核对产物。按契约区分新增产量与最终库存，读取 completed_output_count、"
    "remaining_output_count 和不确定性回执后再处理剩余工作，避免整批重发或为每件产物反复调度模型。"
)

# MaiCraft 状态名 → 任务词表状态映射（绑定处适配声明的一部分；词表）
# 两组键各服务一条来路，互不冲突：
# - 状态名（pending/success/…）：轮询查询拿到的任务快照字段
# - 事件类型名（started/completed/…）：注意流任务事件（priority="task"）
#   两套名字都翻译到同一份任务词表，避免在 Agent 里并存两张词表
_MAICRAFT_TASK_STATUS_MAP = {
    "pending": "accepted",
    "running": "running",
    "waiting_for_decision": "waiting_for_decision",
    "success": "succeeded",
    "failed": "failed",
    "timeout": "timeout",
    "cancelled": "cancelled",
    # 注意流任务事件名（Mod 结算/推进时发布的事件类型）
    # 只列能明确折进词表状态的：started/completed/failed/cancelled 一目了然，
    # decision 对应"停在决策点等应答"。
    # 故意不映射 paused/resumed/plan_changed：paused 在 Mod 侧既有"等人回答"
    # 也有"系统暂停（掉线等）"两种来源，事件类型本身分不出来，硬折成
    # waiting_for_decision 会把系统暂停谎报成待应答；这些事件留给任务快照去核实。
    "started": "running",
    "completed": "succeeded",
    "decision": "waiting_for_decision",
}

# 任务词表里可折进记录表的全部状态（写入前校验，挡住认不出来的事件类型）
_TASK_EVENT_STATUSES = frozenset(
    {"accepted", "running", "waiting_for_decision", "succeeded", "failed", "cancelled", "timeout"}
)


class MinecraftAgent(BaseAgent):
    """Minecraft 游戏 Agent（AI 玩家，普通 ReAct Agent）

    实现方式：
    - 构造注入：所有依赖经 ``__init__`` 参数传入（可 mock / 可替换）
    - list_tools：声明 Agent 专属工具（provider="minecraft"），经 registry 注册
    - 局部工具（todo/notebook）由 LLM 直接驱动；MCP 工具（maicraft_*）经
      registry 动态发现并透传——agent 不感知 maicraft 接口
    """

    # ----- 元数据 -----
    name = "minecraft"
    description = "Minecraft 游戏 Agent（AI 玩家，ReAct）"

    # ----- 事件族声明 -----
    emits_events = (
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
        clock: Optional[Callable[[], int]] = None,
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
                ``None`` 时思考流整体短路，决策循环行为与无旁路完全一致。
            task_tracker: 通用任务基建（TaskTracker；跟踪循环 + 记录表）。
                execute 受理回执经它登记跟踪，状态变化经 task.changed 唤醒
                本 Agent（on_task_notification 注入消息）。
            skill_library: 技能库（玩法经验文档）。注入时系统提示词附技能目录，
                并提供 ``minecraft_skill`` 按名读取正文；``None`` 时两者都不出现。
            clock: 毫秒时钟（返回 int）。仅用于忙等治理闸的时间差比较；
                缺省单调时钟，测试注入假时钟做确定性推进。
        """
        super().__init__(event_bus=event_bus)
        self.typed_config = config
        self._llm = llm_manager
        self._prompt = prompt_manager
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._thinking_sink = thinking_sink
        self._skills = skill_library
        # Mod 报告的已装模组编号（None=未知）：技能目录据此移除确认没装的模组玩法。
        # 清单在游戏客户端启动时冻结，每次连接只需读一次；连接恢复后可能换了实例，重新读取。
        self._installed_mods: Optional[frozenset[str]] = None
        self._installed_mods_probed = False

        # Agent 内部状态（内存，不持久化）
        self._mc_state: MinecraftAgentState = MinecraftAgentState()
        self._observations = MinecraftObservations()
        # 局部工具执行器：LLM 循环直接调（不依赖 registry；有 registry 时同一实例注册）
        self._tool_provider: MinecraftToolProvider = MinecraftToolProvider(
            state=self._mc_state,
            report_callback=self._handle_report,
            wait_callback=self._request_wait,
            observation_reader=self._read_observation,
            glance_reader=self._glance,
            skill_reader=self._read_skill if skill_library is not None else None,
        )
        # Mod 的感知工具全名（装配成功时由适配器绑定填入）：主播看一眼经它读原生观察
        self._perceive_tool: Optional[str] = None
        # 全部能力的签名简表（每次 MCP 连接只读一次）；新任务开局随资料包附上，模型不必逐个读契约
        self._ability_signatures: Optional[Dict[str, Any]] = None

        # 命令驱动运行骨架：worker 等命令信号，收到命令后持续推进当前游戏任务。
        self._worker_task: Optional[asyncio.Task[None]] = None
        # fire-and-forget 后台任务强引用（防 GC 中途回收 + 异常可见化）
        self._bg_tasks: set = set()
        self._wake_event: asyncio.Event = asyncio.Event()
        # 指令队列（委派接收 / 系统注入投递；元素 = (task_id, content)，
        # task_id 空串表示非委派来源；任务执行中也可追加——LLM 下一次推理吸收）
        self._message_queue: Deque[tuple] = deque()
        # 原始指令和执行阶段跟随逻辑任务，不能因一次后台通知重新开批就丢失。
        self._task_instructions: List[str] = []
        self._task_progress: Dict[str, Dict[str, Any]] = {}
        self._recent_results: Deque[Dict[str, Any]] = deque(maxlen=6)
        self._plan_facts = MinecraftPlanFacts()
        self._task_notice_fingerprints: Dict[str, str] = {}
        # 忙等治理闸：失败唤醒退避（实证：failed 事件一到就触发完整推理是最大忙等源）
        # 与无进展决策合并（实证：连续调用间隔 1-2 秒、completion 只有"等待结果"几十 token）
        self._clock: Callable[[], int] = clock if clock is not None else default_clock_ms
        self._failure_gate = FailureBackoffGate(
            delays_ms=_FAILURE_WAKE_DELAYS_MS, escalate_after=_FAILURE_WAKE_ESCALATE_AFTER
        )
        self._no_progress_gate = NoProgressGate(window_ms=_NO_PROGRESS_WINDOW_MS)
        # 退避期的失败唤醒定时任务（task_id → 定时器）；到点注入攒下的失败事实
        self._failure_wake_timers: Dict[str, asyncio.Task[None]] = {}
        # 累计推理次数供恢复任务时观察进展，持续施工不会因次数达到固定值而中断。
        self._task_steps = 0
        # 游戏内动作连续失败计数（成功即清零、新指令清零）：卡在同一步时把卡点讲给主播，
        # 免得身体闷头重试几十次、主播却一句话说不出来；只做叙事通报，不改变任务走向
        self._failure_streak = 0
        # Mod 已暂停的本人后台任务 → 暂停原因。通用任务词表没有"暂停"，账面仍是 running；
        # 这里单独记下，身体停着时唤醒自己处理，而不是在 minecraft_wait 里干等
        self._paused_tasks: Dict[str, str] = {}
        self._task_finished = True
        self._task_suspended = False
        self._design_progress = MachineDesignProgress()
        # 同一逻辑任务跨后台等待沿用历史，只有新任务或集中整理才重建前缀。
        self._messages: List[Dict[str, Any]] = []
        self._context_compactor = MinecraftHistoryCompactor(llm_manager, config.context)
        # 最近一次完整进入历史的任务状态（集中整理的固定事实或首次续做快照）；唤醒续做只补交它之后的变化。
        self._shown_context: Dict[str, Any] = {}
        # 让出等待后台任务时提前进行的历史整理；醒来时若尚未完成，按预算决定等待或放弃。
        self._idle_compaction: Optional[asyncio.Task[None]] = None
        # 本批已吸收的委派任务号（进入 running）与待写终态的委派任务号
        self._delegated_batch_ids: List[str] = []
        self._delegated_finished_ids: List[str] = []

        # 通用任务基建（组合根注入；缺省 None = 无跟踪能力，受理回执只作为观察返回 LLM）
        self._task_tracker: Optional[Any] = task_tracker
        # Agent 私有 MCP（_on_start 装配成功时持有）：资源订阅接线用
        self._mcp_client: Optional[Any] = None
        # 私有 MCP provider（_on_start 常驻登记，连接失败时以 0 工具降级登记）
        # 与其降级恢复循环：连不上不再丢弃，后台退避重试直至装配成功
        self._mcp_provider: Optional[Any] = None
        self._mcp_recover_task: Optional[asyncio.Task[None]] = None
        # 适配器是否已绑定：通知订阅只建一次，恢复重绑定不得重复订阅
        self._mcp_adapters_bound: bool = False
        # 注意流读取适配器（装配成功时持有）+ 本 Agent 自己的增量游标：
        # 游标属于"谁消费事件谁持有"，跨任务批次保留，避免每次重读最新一页。
        self._attention_provider: Optional[Any] = None
        self._attention_stream_id: Optional[str] = None
        self._attention_cursor: int = 0
        # 通知与任务步都可能同时读取，串行确认游标，防止较慢的旧快照把新位置覆盖回去。
        self._attention_read_lock = asyncio.Lock()
        self._attention_primed: bool = False
        # 本批 ReAct 是否正在跑（决定通知到达时要不要读身体事件）
        self._batch_active: bool = False
        # 本批任务期间观察到的身体事件（上报时随 payload 带上任务上下文）
        self._batch_body_events: List[Dict[str, Any]] = []
        # 本批已经就"开始/结束"告知过主播的身体事件类型（每个类型各一次，防刷屏）
        self._body_notified: set = set()

        # 本批次 LLM 是否已 report（delivery/escalation 终止语义判定）
        self._task_reported = False
        self._wait_requested = False

        # 平台暂停支持（AgentControl pause/resume 经 _on_pause/_on_resume 进入）
        self._paused = asyncio.Event()
        self._paused.set()

        self._running = False

        # 建造器只持有本游戏的设计任务；关闭 Minecraft 时不独立装配或借用其他连接。
        self._builder: Optional[MinecraftBuilderController] = None
        if config.builder.enabled and tool_registry is not None:
            self._builder = MinecraftBuilderController(
                config.builder,
                llm=llm_manager,
                registry=tool_registry,
                tracker=task_tracker,
                client=lambda: self._mcp_client,
                parent_task=lambda: (self._delegated_batch_ids + self._delegated_finished_ids or [""])[-1],
            )

        self._logger = get_logger("MinecraftAgent")
        self._logger.info(f"MinecraftAgent 已构造 (llm={'已注入' if llm_manager else '无'}@{MINECRAFT_PROFILE})")

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def _on_start(self) -> None:
        """启动钩子：注册专属工具 + 装配 Agent 私有 MCP（启用时）+ 启动命令 worker 与 handoff 监视。"""
        if self._tool_registry is not None:
            self._register_tools()
            await self._bind_agent_owned_mcp()

        self._running = True
        if self._builder is not None:
            self._builder.open()
        self._worker_task = asyncio.create_task(self._worker())
        self._logger.info("MinecraftAgent 已启动（命令驱动：等待委派指令）")

    async def _bind_agent_owned_mcp(self) -> None:
        """装配 Agent 私有 MCP server（[agents.minecraft.mcp]）——失败常驻重试。

        启用条件：registry 非空且 ``typed_config.mcp.enabled`` 为 True。
        装配：provider **常驻登记**到 ToolRegistry（可见名单以策略 callable
        声明，fail-closed：每个工具仅 minecraft 可见，主播读游戏状态走
        minecraft_glance；恢复刷新时对新工具集重派名单，不落"未列出=全员"
        默认）。连接失败/装配异常不再丢弃——provider 以 0 工具降级登记
        （工具页可见、可手动重连），后台退避重试直至装配成功（对齐
        MaicraftAttentionCollector 的失败语义："Mod 没开"是常态而非事故）。
        Agent 启动不阻断——Agent 是命令驱动，MCP 不可用只降级。
        关闭：本 Agent 在 ``_on_stop`` 取消恢复循环、摘除 provider 并关闭
        登记的 MCP 客户端（经基类登记入口）；全局 ``close_mcp_providers``
        仍保留供组合根全量停机兜底。
        """
        if self._tool_registry is None:
            return
        mcp_cfg = self.typed_config.mcp
        if not mcp_cfg.enabled:
            return
        # 函数内 import：mcp 模块涉及 fastmcp 重型依赖；按"可选重型依赖延迟加载"
        # 白名单情形，_on_start 是异步路径，导入仅在启动期发生一次。
        from src.modules.mcp.client import McpClient
        from src.modules.mcp.provider import McpToolProvider

        server_name = "maicraft"
        client = McpClient(name=server_name, config=mcp_cfg)
        self._mcp_client = client
        prov = McpToolProvider(
            client=client,
            server_name=server_name,
            provider=server_name,  # spec.provider = "maicraft"，与历史契约一致
        )
        self._mcp_provider = prov
        # 绑定处声明在 setup 之前就位：初次装配 / 恢复重试 / 手动重连三条
        # 路径的适配器绑定统一经 on_tools_refreshed 回调，单一入口不分叉
        prov.on_tools_refreshed = self._on_mcp_tools_ready
        prov.switch_config = {"file": "agents.toml", "key": "agents.minecraft.mcp.enabled"}
        try:
            count = await prov.setup()
        except Exception as exc:  # noqa: BLE001 - 装配异常按连接失败同径降级
            self._logger.warning(f"Agent 私有 MCP（名单 fail-closed）装配异常: {type(exc).__name__}: {exc}")
            count = 0
        try:
            self.register_tool_provider(prov, registry=self._tool_registry, visible_to=self._maicraft_visible_to)
        except Exception as exc:  # noqa: BLE001 - 注册异常兜底
            self._logger.warning(f"Agent 私有 MCP（名单 fail-closed）注册失败: {type(exc).__name__}: {exc}")
            return
        # 客户端纳入基类登记（stop/重建路径统一关闭）
        self.register_mcp_client(client)
        if count > 0:
            # setup 成功路径：_on_mcp_tools_ready 回调已完成适配器绑定与装配日志
            return
        self._logger.warning(
            "Agent 私有 MCP（名单 fail-closed）连接失败或 server 未暴露工具，已降级登记（0 工具），后台退避重试装配"
        )
        self._start_mcp_recover_loop(prov)

    def _on_mcp_tools_ready(self, count: int) -> None:
        """工具清单（重）同步成功回调（provider 三条路径统一入口）。

        绑定适配器并按首次/恢复分别记日志；首次经 setup 内部调用（此时
        provider 尚在注册流程中，只做绑定不碰 registry），恢复路径由重试
        循环 / 手动重连的 registry 刷新随后完成工具补注册。
        """
        prov = self._mcp_provider
        if prov is None:
            return
        first = not self._mcp_adapters_bound
        self._bind_mcp_adapters(prov)
        if first:
            self._logger.info(f"Agent 私有 MCP（名单 fail-closed）适配器绑定完成：{count} 个工具")
        else:
            self._logger.info(f"Agent 私有 MCP 装配恢复：{count} 个工具（适配器已重绑定）")

    def _bind_mcp_adapters(self, prov: Any) -> None:
        """绑定处适配声明（幂等）：任务查询 + 状态映射 + attention 读取 + 通知订阅。

        工具名按当前声明与已知旧别名定位（server 特有知识留在此处）；通知订阅只在
        首次绑定建立（多订阅方通道，恢复重绑定不得重复入队）。
        """
        spec = find_mod_tool(prov.list_tools(), "task")
        prov.task_query_tool = spec.full_name if spec is not None else None
        prov.task_status_map = _MAICRAFT_TASK_STATUS_MAP
        prov.attention_uri = "maicraft://attention"
        # 身体事件读取：工具名与固定入参都在这一处声明，provider 只补游标与页大小。
        # 本 Agent 另订阅同一条通知通道（举旗级），只在有进行中工作时才真去读——
        # 空闲时通知到达即返回，不产生 MCP 调用也不唤 LLM。
        spec = find_mod_tool(prov.list_tools(), "perceive")
        prov.attention_read_tool = spec.full_name if spec is not None else None
        self._perceive_tool = spec.full_name if spec is not None else None
        prov.attention_read_arguments = {"view": "attention"}
        self._attention_provider = prov if getattr(prov, "attention_read_tool", None) else None
        if self._mcp_adapters_bound:
            return
        if self._attention_provider is not None:
            # 可选钩子（与 TaskTracker 同一处约定）：无通知能力的提供者只是失去"被推醒"，
            # 任务步内的增量读取照常工作——不能因为缺钩子就整个装配失败。
            subscribe = getattr(prov, "subscribe_task_notifications", None)
            if callable(subscribe):
                subscribe(self._on_attention_notification)
            else:
                self._logger.warning("MCP provider 无通知适配器：身体事件只能靠任务步内增量读取发现")
        self._mcp_adapters_bound = True

    def _start_mcp_recover_loop(self, prov: Any) -> None:
        """启动私有 MCP 降级恢复循环（已存在则跳过）。"""
        if self._mcp_recover_task is not None and not self._mcp_recover_task.done():
            return
        self._mcp_recover_task = asyncio.create_task(self._mcp_recover_loop(prov))

    async def _mcp_recover_loop(self, prov: Any) -> None:
        """私有 MCP 降级恢复循环：退避重试装配（5s 起步 60s 封顶，连上即止）。

        "Mod 没开"是常态而非事故——同因告警去重已在 McpClient，本循环安静
        重试；setup 成功即经回调完成适配器绑定，再刷新 registry 工具集
        （0 工具降级登记 → 换血补注册）后退出。Agent 停止即退出。
        """
        delay = 5.0
        while self._running:
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)
            if not self._running:
                return
            try:
                count = await prov.setup()
            except Exception as exc:  # noqa: BLE001 - 单轮失败安静重试
                self._logger.debug(f"Agent 私有 MCP 恢复重试失败: {type(exc).__name__}: {exc}")
                continue
            if count == 0:
                continue
            # 工具清单到手：registry 补注册（适配器绑定已在 setup 回调内完成）
            try:
                report = self._tool_registry.refresh_provider_tools(prov)
            except Exception as exc:  # noqa: BLE001 - 刷新失败下轮再试
                self._logger.warning(f"Agent 私有 MCP 恢复刷新失败: {type(exc).__name__}: {exc}")
                continue
            if not report.get("ok"):
                continue
            self._logger.info(
                f"Agent 私有 MCP（名单 fail-closed）装配恢复：{count} 个工具（新增 {len(report.get('added', []))}）"
            )
            self._on_mcp_recovered()
            return

    def _on_mcp_recovered(self) -> None:
        """MCP 连接恢复后的任务侧动作：注入通知 + 解锁挂起 + 唤醒。

        断连期间任务层不知情——批次在跑则模型沉浸于失败观察，已挂起
        （``_task_suspended``）则门卫等 MinecraftInstruction 永不唤醒。
        恢复通知进队列后批次在跑被下一步 flush 吸收；挂起中解锁后被
        唤醒重跑，账面由批次重启的 ``_mark_delegated_running`` 对追踪
        清单旧委派重写 running（不代写账，恢复循环只给信号）。
        """
        # 重连后 Mod 可能已换版本，能力签名下次开局重新读取。
        self._ability_signatures = None
        self._inject_wakeup_message(
            "[系统] Minecraft 连接已恢复，maicraft 工具重新可用。若此前因连接失败受阻，请评估现场并继续原任务。"
        )
        if self._task_suspended:
            self._task_suspended = False
        # 断连期间游戏客户端可能重启换了整合包，下一批重新读取已装模组
        self._installed_mods_probed = False
        self._installed_mods = None
        self._wake_event.set()

    @staticmethod
    def _maicraft_visible_to(specs: Iterable[ToolSpec]) -> Dict[str, List[str]]:
        """maicraft 逐工具名单（绑定处代码分类，注解不可信）：全部仅 minecraft 可见。

        主播"随时直读游戏状态"走 minecraft_glance 精简视图：原始观察里的几何
        与证据是给游戏 Agent 规划用的，交给主播会让一次回应读进几万字、
        还会诱导主播自己去推方块坐标、替身体做工程判断。
        """
        return {spec.full_name: ["minecraft"] for spec in specs}

    async def _on_stop(self) -> None:
        """停止钩子：取消命令 worker 与 handoff 监视（任务执行随 worker 取消而中断）。"""
        self._running = False
        self._wake_event.set()
        # 停机时放弃等待期间的提前整理；整理只在成功时替换历史，取消不会留下半份摘要。
        if self._idle_compaction is not None:
            self._idle_compaction.cancel()
            with suppress(asyncio.CancelledError):
                await self._idle_compaction
            self._idle_compaction = None
        # 退避中的失败唤醒随停机取消，重启后由下一次真实事件重新判断
        for timer in self._failure_wake_timers.values():
            timer.cancel()
        self._failure_wake_timers.clear()
        self._failure_gate.reset()
        self._no_progress_gate.reset()
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - 边界兜底
                self._logger.warning(f"命令 worker 退出异常: {exc}")
            self._worker_task = None
        # 先收束设计子任务，再释放它借用的 MCP，避免停机后仍读取资料或产生新设计。
        if self._builder is not None:
            await self._builder.close()
        # 私有 MCP 恢复循环随停机取消；provider 引用与适配器状态一并复位
        if self._mcp_recover_task is not None:
            self._mcp_recover_task.cancel()
            try:
                await self._mcp_recover_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - 边界兜底
                self._logger.warning(f"私有 MCP 恢复循环退出异常: {exc}")
            self._mcp_recover_task = None
        self._mcp_provider = None
        self._attention_provider = None
        self._mcp_adapters_bound = False
        # 资源清理契约：摘除本 Agent 注册的 provider + 关闭登记的 MCP 客户端
        removed = self.unregister_tool_providers()
        await self.close_mcp_clients()
        self._mcp_client = None
        self._logger.info(f"MinecraftAgent 已停止（摘除 {removed} 个工具）")

    async def _on_pause(self) -> None:
        """暂停钩子：任务循环在步骤间挂起（不打断当前工具调用）。"""
        self._paused.clear()
        if self._builder is not None:
            self._builder.set_paused(True)

    async def _on_resume(self) -> None:
        """恢复钩子：任务循环继续。"""
        self._paused.set()
        if self._builder is not None:
            self._builder.set_paused(False)

    # ==================================================================
    # 工具提供（list_tools）
    # ==================================================================

    def list_tools(self) -> Iterable[ToolSpec]:
        """声明 Agent 专属工具（provider="minecraft"）。"""
        specs = list(self._tool_provider.list_tools())
        if self._builder is not None:
            specs.extend(self._builder.provider.list_tools())
        return specs

    # 局部工具可见名单（注册处声明）：本地件只有 minecraft 自己可见；
    # get_work_log 是主播的叙事素材读服务。派活走框架委派原语（framework_delegate）。
    _LOCAL_VISIBLE_TO = {
        "minecraft_todo": ["minecraft"],
        "minecraft_notebook": ["minecraft"],
        "minecraft_report": ["minecraft"],
        "minecraft_wait": ["minecraft"],
        "minecraft_observation": ["minecraft"],
        "minecraft_skill": ["minecraft"],
        "minecraft_get_work_log": ["streamer"],
        "minecraft_glance": ["streamer"],
    }

    def _register_tools(self) -> None:
        """注册 Agent 专属工具到 ToolRegistry（复用 __init__ 创建的执行器实例）。"""
        if self._tool_registry is None:
            return
        visible_to = dict(self._LOCAL_VISIBLE_TO)
        if self._skills is None:
            # 未注入技能库时不声明读取工具，名单也不能留下没有对应工具的条目
            visible_to.pop("minecraft_skill")
        self.register_tool_provider(self._tool_provider, registry=self._tool_registry, visible_to=visible_to)
        if self._builder is not None:
            provider = self._builder.provider
            self.register_tool_provider(
                provider,
                registry=self._tool_registry,
                visible_to={spec.full_name: [self.name] for spec in provider.list_tools()},
            )
        self._logger.info(
            "MinecraftAgent 工具已注册：minecraft_todo / minecraft_notebook / minecraft_get_work_log / minecraft_report"
        )

    # ==================================================================
    # 命令驱动 ReAct（命令 → 消息队列 → 持续推进任务 → 回空闲）
    # ==================================================================

    def receive_prompt(self, *, content: str, source: str = "") -> bool:
        """接收递话（纯文本留言，不派任务、不进账本）：入队 + 唤醒。

        任务号空串（非委派来源），经 worker 门卫的 MinecraftInstruction
        形状检查——任务执行中下一步推理前被 flush 吸收；任务挂起中被唤醒
        重新判断。source 仅用于日志。
        """
        # 标明递话来自谁：运营原话是直接要求；主播递来的补充可能夹带对现场的转述，
        # 与回执或运营原话冲突时以回执和原话为准（规则见系统提示词）。
        label = _PROMPT_SOURCE_LABELS.get(source, "")
        self._message_queue.append(MinecraftInstruction("", f"{label}\n{content}" if label else content))
        self._wake_event.set()
        self._logger.info(f"MinecraftAgent 收到递话（source={source or '未知'}）：{content[:60]}")
        return True

    def receive_delegation(self, *, instruction: str, task_id: str) -> None:
        """接收委派入口（framework_delegate 调用）：指令入队（带任务号）+ 唤醒。

        指令不可拒绝；队列项带任务号供任务批次把状态写回任务记录表
        （开始 → running；交付/升级 → 终态）。
        """
        self._message_queue.append(MinecraftInstruction(task_id, instruction))
        self._wake_event.set()
        self._logger.info(f"MinecraftAgent 收到委派（task_id={task_id}）：{instruction[:60]}")
        return None  # 已接收

    def cancel_task(self, task_id: str, source: str = "") -> bool:
        """硬取消：清委派追踪清单 + 账面 cancelled + 注入停手通知。

        软取消哲学（与 pause 同）：不打断当前工具调用，靠系统消息让 LLM
        下一步自行停手走既有终止语义；终态粘滞保证其后的收尾动作写不进
        账。任务不在追踪清单（未知号/已终态移除）→ False。
        """
        if task_id not in self._delegated_batch_ids and task_id not in self._delegated_finished_ids:
            return False
        if task_id in self._delegated_batch_ids:
            self._delegated_batch_ids.remove(task_id)
        if task_id in self._delegated_finished_ids:
            self._delegated_finished_ids.remove(task_id)
        if self._task_tracker is not None:
            self._task_tracker.ledger.update(task_id, "cancelled", summary=f"被取消（source={source or '未知'}）")
        self._inject_wakeup_message(f"[系统] 任务 {task_id} 已被取消，请停止相关工作。")
        self._logger.info(f"MinecraftAgent 任务已取消（task_id={task_id}, source={source or '未知'}）")
        return True

    async def _worker(self) -> None:
        """命令工作协程：等待命令信号 → 执行目标任务 → 回到等待（空闲零消耗）。"""
        while self._running:
            await self._wake_event.wait()
            self._wake_event.clear()
            if not (self._running and self._message_queue):
                continue
            # 任务交付、上报困难或执行中断后等待玩家新指令，后台通知只补充已知状态。
            if (self._task_finished or self._task_suspended) and not any(
                isinstance(message, MinecraftInstruction) for message in self._message_queue
            ):
                continue
            try:
                await self._run_task()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 任务失败不杀死 worker
                self._logger.exception(f"任务执行异常: {exc}")
                await self.emit_error(f"任务执行异常: {exc}")

    async def _run_task(self) -> None:
        """执行单个任务批：围绕当前指令持续推进，直到批次终止语义命中。

        循环每步：
        1. flush 命令/系统注入消息 → 追加 user 消息
        2. 按预算集中整理历史，普通轮次保留既有消息
        3. LLM 推理（generate + 工具列表）→ tool_calls（可多个）
        4. 串行执行：统一经 ToolRegistry（观测/停用/熔断复用既有机制）
        5. 工具结果作为观察作为观察返回（OpenAI tool role + tool_call_id）
        批次终止语义（五条，全部系统可判定）：
        1. LLM 调 minecraft_report(kind=delivery) → 停止（工具内交付门禁校验）
        2. LLM 调 minecraft_report(kind=escalation) → 停止，静默等主播委派
        3. 自然终止，无 report、无未决 handoff 且待办完成 → 系统兜底交付
        4. 仅剩实际运行中的 handoff → 静默让出；待开工或待决策则提醒推进
        5. 提醒后仍不行动 → 挂起，等待玩家指令处理实际阻塞
        """
        if self._llm is None:
            await self.emit_error("无法执行任务：LLM 未注入")
            return
        self._batch_active = True
        # 本批的身体事件上下文归本批：上一批的攻击不该被算进这一批的交付总结
        self._batch_body_events.clear()
        self._body_notified.clear()
        try:
            await self._run_task_batch()
        finally:
            self._batch_active = False

    async def _run_task_batch(self) -> None:
        """任务批主体：持续调用工具推进当前任务，直到交付、等待或真实阻塞。"""
        self._task_reported = False
        self._wait_requested = False
        await self._probe_installed_mods()
        system_message = self._system_message()
        # 工具列表 = 注册表按可见名单计算（for_agent，每任务重新拉取）——
        # minecraft 名单内含本地件 todo/notebook/report 与 maicraft_*，共享工具
        # 按各自名单照常出现；报告缺陷（report 不在旧手工列表）随统一来源消除
        if self._tool_registry is None:
            self._logger.warning("任务执行无 tool_registry：工具列表为空，任务将失败")
            tool_defs: List[Dict[str, Any]] = []
        else:
            specs = self._tool_registry.list_tools(for_agent=self.name)
            # 注册顺序的偶然变化不能改变同一组工具的发送顺序。
            tool_defs = [
                self._tool_registry.function_definition(s) for s in sorted(specs, key=lambda spec: spec.full_name)
            ]

        # 等待期间的提前整理必须在本批改动历史之前结清，避免两边同时改写同一份工作历史。
        await self._settle_idle_compaction(tool_defs)
        if not self._messages:
            self._messages.append(dict(system_message))
        messages = self._messages
        close_interrupted_calls(messages)
        if not self._task_finished and self._task_instructions:
            # 后台任务完成后先恢复原目标、待办与当前阶段，再让模型解释这次通知。
            # 唤醒续做：前文已完整展示的任务状态不再整份重抄，只补交变化的字段与后台任务。
            messages.append(
                {
                    "role": "user",
                    "content": "[继续原游戏任务]\n"
                    + json.dumps(self._continue_task_context(), ensure_ascii=False, default=str),
                    "_minecraft_context_facts": True,
                }
            )

        steps = 0
        # 本批若开始了新任务，就在第一次推理前附上宿主代读的开局资料。
        opening_due = False
        mc_round = f"mc_{uuid.uuid4().hex[:12]}" if self._thinking_sink is not None else ""
        mc_seq_box = [0]
        action_reminded = False
        while self._running:
            # 步骤间挂起（平台 pause）
            await self._paused.wait()

            # 新指令和真实任务通知提供了新事实，恢复后允许模型重新判断，不继承上一轮的停滞提醒。
            if self._message_queue:
                action_reminded = False
            # --- 消息 flush（执行中追加的委派指令 / 任务通知在下一次推理前吸收）---
            while self._message_queue:
                queued = self._message_queue.popleft()
                _tid, _content = queued
                if isinstance(queued, MinecraftInstruction):
                    if self._task_finished:
                        self._task_instructions.clear()
                        self._task_progress.clear()
                        self._paused_tasks.clear()
                        self._recent_results.clear()
                        self._plan_facts.clear()
                        self._task_notice_fingerprints.clear()
                        messages[:] = [dict(system_message)]
                        self._context_compactor.checkpoints = 0
                        # 新任务从空历史开始，下一次续做必须重新完整展示任务状态。
                        self._shown_context = {}
                        self._mc_state.set_todos([])
                        # 原文引用只属于本次逻辑任务；后台唤醒和同任务补充要求继续使用已有证据。
                        self._observations = MinecraftObservations()
                        opening_due = True
                    self._task_instructions.append(_content)
                    self._task_finished = False
                    self._task_suspended = False
                    self._task_steps = 0
                    # 主播给了新指令就换了方向，之前的失败连击不再代表"卡在同一步"
                    self._failure_streak = 0
                    # 忙等治理账目同作废：旧方向攒的失败退避与无进展窗口不属于新方向
                    self._failure_gate.reset()
                    self._no_progress_gate.reset()
                    for stale_task_id in list(self._failure_wake_timers):
                        self._cancel_failure_wake(stale_task_id)
                    self._design_progress.reset()
                if _tid:
                    self._delegated_batch_ids.append(_tid)
                message: Dict[str, Any] = {"role": "user", "content": _content}
                # 只有宿主入队的通知可参与证据投影；用户即使写了相同前缀，其指令也必须保持原文。
                if not isinstance(queued, MinecraftInstruction):
                    message["_minecraft_task_notice"] = True
                messages.append(message)
            if opening_due:
                # 新任务开局：模型以往开头几轮都在读现状、地标、笔记和能力契约，由宿主一次代读，省掉这几轮请求。
                opening_due = False
                await self._append_opening_bundle(messages)
            # 本批委派任务进入进行中（agent 型单写者：执行 Agent 写）
            self._mark_delegated_running()
            steps += 1

            # --- 身体事件增量读取（任务跑着的时候才知道自己正被谁打）---
            await self._drain_attention()

            # 历史过长时先整理上下文再继续游戏行动，普通轮次只追加消息。
            if not await self._prepare_context(messages, tool_defs):
                return
            self._task_steps += 1

            # --- LLM 推理 ---
            on_delta = self._build_thinking_callback(mc_round, steps, mc_seq_box) if mc_round else None
            try:
                response = await self._llm.generate(
                    # 原文留在工作历史；本轮仅引用仍在实际请求中的相同证据，整理掉的内容会自动完整重现。
                    project_context(messages),
                    profile=MINECRAFT_PROFILE,
                    tools=tool_defs,
                    on_delta=on_delta,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单步失败转错误事件
                # 未完成的生成没有派发工具；保留已验证计划并暂停，身体通知不能在中断后悄悄重启同一轮推理。
                self._task_suspended = True
                self._logger.warning(f"MinecraftAgent LLM 推理异常: {type(exc).__name__}: {exc}", exc=True)
                await self.emit_error(
                    f"LLM 推理异常: {type(exc).__name__}: {exc}；本任务与已有回执已保留，等待新指令继续"
                )
                return
            if not response.success:
                self._task_suspended = True
                await self.emit_error(f"LLM 调用失败: {response.error or '未知错误'}")
                return

            # 组装 assistant 消息（完整 tool_calls 形态，供后续关联作为观察返回）
            tool_calls = response.tool_calls or []
            assistant_content = response.content
            if assistant_content is not None and not isinstance(assistant_content, str):
                # OpenAI 协议要求 content 为 string/null，部分 client 返回结构化内容
                assistant_content = json.dumps(assistant_content, ensure_ascii=False, default=str)
            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": assistant_content}
            if tool_calls:
                # 扁平 ToolCall → OpenAI 协议嵌套形态（喂回时 arguments 须为 JSON 字符串）
                assistant_msg["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False, default=str),
                        },
                    }
                    for call in tool_calls
                ]
            messages.append(assistant_msg)

            # --- 每步响应事实（观察面响应卡数据源）---
            # 只发中间工具调用步骤：自然终止轮的正文由 game.report 交付卡承载，
            # 重复发会出现两张同文卡；正文为空的纯工具步骤不发（动作已由工具卡呈现）。
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

            # --- 自然终止（情形 3/4）：LLM 无 tool_calls ---
            if not tool_calls:
                pending = self._pending_task_count()
                actionable = self._actionable_task_ids()
                if actionable or not pending and self._unfinished_todos():
                    # 已有设计等开工、任务等决策或仍有未派发待办时，等通知不会推进；先给模型一次纠正机会。
                    if not await self._continue_after_no_progress(
                        messages, action_reminded, "仍有未完成待办或需要处理的后台任务"
                    ):
                        return
                    action_reminded = True
                    continue
                if pending > 0:
                    # 情形 4：有未决 handoff——静默让出回合，等 handoff 唤醒（零空耗）
                    self._logger.info(
                        f"任务批次自然终止（{steps} 步），{self._pending_task_count()} 个后台任务跟踪中，静默让出"
                    )
                elif not self._task_reported:
                    # 情形 3：无 report 无 handoff——系统兜底，主播必收到一次且仅一次交付
                    delivery = (response.content or "").strip()
                    await self._emit_report("delivery", delivery or "任务完成")
                    self._task_finished = True
                    self._finish_delegated("succeeded", summary=delivery or "任务完成")
                    self._logger.info(f"任务批次自然终止（{steps} 步），系统兜底交付")
                else:
                    self._logger.info(f"任务批次结束（{steps} 步，已上报）")
                return

            # --- 工具执行与观察作为观察返回 ---
            only_repeated_reads = True
            for call in tool_calls:
                await self._paused.wait()
                # 扁平 ToolCall：name/arguments(id 关联观察回填)；arguments 已是解析后的 dict
                arguments = call.arguments if isinstance(call.arguments, dict) else {}

                # 等待必须独占本轮，防止同批后续动作与“已让出”回执相互矛盾。
                if call.name == "minecraft_wait" and len(tool_calls) != 1:
                    observation = {"ok": False, "error": "minecraft_wait 必须单独调用；先完成本轮其他动作"}
                else:
                    observation = await self._execute_tool(call.name, arguments, round_id=mc_round)
                self._track_receipt(call.name, observation)
                # 业务跟踪先读完整原件，模型再读呈现版本；阅读工具本身不再次套上原文引用。
                shown = (
                    observation
                    if call.name == "minecraft_observation"
                    else self._observations.present(call.name, arguments, observation)
                )
                if call.name == "minecraft_observation" and "same_request_and_result" not in shown:
                    # 错误路径也属于一次读取结果；反复读取同一个不存在的字段不能绕过无进展判断。
                    shown = self._observations.mark_read({**shown, "read_request": deepcopy(arguments)})
                self._remember_result(call.name, arguments, observation, shown)
                only_repeated_reads &= repeated_read(call.name, arguments, shown)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(shown, ensure_ascii=False, default=str),
                    }
                )
                blocked_design = self._design_progress.observe(call.name, arguments, observation)
                if blocked_design:
                    # 已知拒绝尚未产生游戏动作，停止当前批次并保存原任务，避免继续消耗推理去重放同一错误。
                    self._logger.warning(blocked_design)
                    await self._suspend_with_report(blocked_design)
                    return

            # 情形 1/2：LLM 已 report——本轮工具执行完后停止（delivery/escalation 语义）
            if self._task_reported:
                self._logger.info(f"LLM 已上报（delivery/escalation），任务批次结束（{steps} 步）")
                return

            if self._wait_requested and not self._message_queue:
                # 工具结果已完整回填；等待期间不再调用模型，真实通知保留原目标并唤醒下一批。
                # 身体在等游戏结果时，历史若已接近预算就借这段空档提前整理，醒来后直接行动。
                self._schedule_idle_compaction(messages, tool_defs)
                return
            self._wait_requested = False
            # 工具被调用不等于游戏目标得到推进；整轮只重读旧证据时沿用同一次提醒，而不是重新计为行动。
            if only_repeated_reads and not self._message_queue:
                # 框架闸：窗口内的重复无进展决策直接并入等待，不再发起推理
                # （提示词约束不住模型轮询时由这里兜底；有可等的后台任务才合并，否则照旧提醒/挂起）
                now_ms = self._clock()
                if self._no_progress_gate.in_window(now_ms) and self._request_wait()["ok"]:
                    self._logger.info("重复无进展决策已并入 minecraft_wait，让出等真实事件唤醒")
                    self._schedule_idle_compaction(messages, tool_defs)
                    return
                if not await self._continue_after_no_progress(
                    messages, action_reminded, "本轮重复读取已有资料或未变化的任务回执，没有取得新证据"
                ):
                    return
                self._no_progress_gate.mark(now_ms)
                action_reminded = True
            elif not only_repeated_reads:
                self._no_progress_gate.reset()
                action_reminded = False

    async def _continue_after_no_progress(self, messages: List[Dict[str, Any]], reminded: bool, reason: str) -> bool:
        """先带着当前决策提示模型推进；仍空转时保留任务并让出，避免反复读回执和生成摘要。"""
        if reminded:
            self._logger.warning(f"Minecraft 任务无新进展：{reason}")
            await self._suspend_with_report(
                f"模型未推进需要行动的任务：{reason}；已保留原目标、待办和任务编号，等待继续指令"
            )
            return False
        pending = [
            task
            for task in self._current_task_context()["background_tasks"]
            if task.get("status") == "waiting_for_decision"
        ]
        messages.append(
            {
                "role": "user",
                "content": "[任务尚需行动] "
                + reason
                + "。请使用已有事实推进下一阶段或回答当前决策；只有新的具体缺口才补查。"
                "确实无法推进时上报 escalation，不能以重复读取代替处理。\n"
                + json_text({"pending_tasks": pending, "ready_plans": self._plan_facts.pending()}),
            }
        )
        return True

    def _request_wait(self) -> Dict[str, Any]:
        """只允许对已有后台依赖让出执行，待开工和待决策不能靠等待推进。"""
        if self._actionable_task_ids():
            return {"ok": False, "error": "仍有待开工或待决策任务，请先推进或说明具体阻塞"}
        # 自卫离位等暂停不会自己恢复：等下去身体只会一直站着，必须先继续、取消或改方案
        stuck = {task_id: reason for task_id, reason in self._paused_tasks.items() if reason != "control_unavailable"}
        if stuck:
            return {
                "ok": False,
                "error": "后台任务已被 Mod 暂停且不会自行继续，等待不会推进；"
                "先看现场，再用 maicraft_task 继续或取消，或上报阻塞",
                "paused_tasks": stuck,
            }
        if self._pending_task_count() == 0:
            return {"ok": False, "error": "没有已登记的后台任务；请继续执行待办或上报阻塞"}
        if self._message_queue:
            return {"ok": True, "waiting": False, "reason": "已有新消息，请处理最新事实"}
        self._wait_requested = True
        result: Dict[str, Any] = {
            "ok": True,
            "waiting": True,
            "monitor": "host",
            "resume_on": "task_event_or_instruction",
        }
        if self._paused_tasks:
            # 控制权暂不可用的暂停会在交还控制后自动继续；如实说明身体此刻并没有在动
            result["paused_tasks"] = dict(self._paused_tasks)
            result["note"] = "这些任务处于暂停，身体此刻没有在执行；控制权恢复后 Mod 会自动继续"
        return result

    def _read_observation(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """每次从当前任务取原文，避免新任务仍绑定上一任务的观察集合。"""
        return self._observations.read(arguments)

    async def _append_opening_bundle(self, messages: List[Dict[str, Any]]) -> None:
        """新任务开局由宿主代读固定资料：当前状态、周边（告示牌、设施、生物）、地标、笔记与全部能力签名。

        以一组宿主代发的工具调用和回执放进历史，和模型自己读取时同样带观察引用、参与证据去重；
        读取失败的那一项照实留下错误回执。这些都是本地 MCP 读取，不消耗推理请求。
        """
        reads: List[tuple[str, Dict[str, Any]]] = []
        if self._perceive_tool is not None:
            reads.append((self._perceive_tool, {"view": "situation"}))
            reads.append(
                (
                    self._perceive_tool,
                    {"view": "surroundings", "sections": list(_OPENING_SURROUNDINGS_SECTIONS)},
                )
            )
            reads.append((self._perceive_tool, {"view": "landmarks", "limit": 20}))
        if self._mc_state.notebook:
            reads.append(("minecraft_notebook", {"action": "read"}))
        signature_args = {"view": "abilities", "detail": "signatures"}
        if self._perceive_tool is not None:
            reads.append((self._perceive_tool, signature_args))
        if not reads:
            return
        batch = uuid.uuid4().hex[:8]
        calls: List[Dict[str, Any]] = []
        results: List[Dict[str, Any]] = []
        for index, (name, arguments) in enumerate(reads):
            call_id = f"opening-{batch}-{index}"
            if arguments is signature_args and self._ability_signatures is not None:
                observation = self._ability_signatures
            else:
                observation = await self._execute_tool(name, arguments)
                if arguments is signature_args and observation.get("ok", True) is not False:
                    self._ability_signatures = observation
            shown = self._observations.present(name, arguments, observation)
            self._remember_result(name, arguments, observation, shown)
            calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
                }
            )
            results.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(shown, ensure_ascii=False, default=str),
                }
            )
        messages.append(
            {
                "role": "assistant",
                "content": "[开局资料] 宿主已代为读取当前状态、周边、地标、笔记和全部能力签名；仍有效时直接使用，不重复读取。",
                "tool_calls": calls,
            }
        )
        messages.extend(results)

    async def _glance(self) -> Dict[str, Any]:
        """主播看一眼：读一次现状与周边，只留直播叙事用得上的事实，再附上身体手头的工作。

        主播被观众问到"你现在在哪/背包里有啥/面前是什么"时调用；只读，不进本
        Agent 的推理循环、不写观察记录。连接没就绪或读取失败时照实写出原因。
        """
        result: Dict[str, Any] = {"tool": "glance"}
        if self._perceive_tool is None:
            result["unavailable"] = "游戏连接还没就绪，暂时看不到游戏里的情况"
        else:
            # 先看自己（位置/血量/背包），再看周边（牌子/生物/设施），两次读取互不依赖
            situation = await self._execute_tool(self._perceive_tool, {"view": "situation"})
            surroundings = await self._execute_tool(
                self._perceive_tool, {"view": "surroundings", "sections": list(SURROUNDINGS_SECTIONS)}
            )
            result.update(glance_situation(situation))
            result.update(glance_surroundings(surroundings))
        result["work"] = self._glance_work()
        return result

    def _glance_work(self) -> Dict[str, Any]:
        """身体手头的工作：在做/等主播指令/空闲、待办、未结束的游戏内动作、连续失败次数。"""
        # 状态写成主播自己的口吻：读这份结果的就是主播本人，"主播交代的事"会让它以为另有人在干活
        if self._task_suspended:
            state = "卡住了，正等我拿主意"
        elif not self._task_finished:
            state = "正在做手上的事"
        else:
            state = "空闲"
        work: Dict[str, Any] = {"state": state, "todo": self._mc_state.todo_doc()["todos"]}
        # 只列还没结束的动作；已结束的成败由连续失败次数和上报讲清楚
        active = [
            {"status": item.get("status"), "summary": item.get("summary")}
            for item in self._task_progress.values()
            if item.get("status") not in {"succeeded", "failed", "cancelled", "timeout"}
        ]
        if active:
            work["active_actions"] = active
        if self._failure_streak:
            work["failure_streak"] = self._failure_streak
        return work

    def _remember_result(
        self, tool: str, arguments: Dict[str, Any], original: Dict[str, Any], shown: Dict[str, Any]
    ) -> None:
        """把子任务的请求与结果引用串起来，局部审阅目标不能覆盖玩家的最终要求。"""
        ref = shown.get("_observation", {}).get("ref")
        self._absorb_resume_receipt(arguments, original)
        self._plan_facts.observe(tool, arguments, original, ref)
        pending_plans = self._plan_facts.pending()
        if pending_plans:
            # 连续查询和阅读旧原文时保留当前待执行编号，避免模型把历史疑问误当成还没通过规划。
            shown["_pending_execution"] = pending_plans
        if original.get("accepted") is True and original.get("task_id"):
            task_id = str(original["task_id"])
            record = self._task_progress.setdefault(task_id, {"task_id": task_id, "status": "accepted"})
            goal = arguments.get("goal")
            record.setdefault("request_ref", ref)
            if isinstance(goal, dict):
                record.setdefault(
                    "requested_goal",
                    {key: deepcopy(goal[key]) for key in ("ability", "outcome", "target") if key in goal},
                )
            while len(self._task_progress) > 64:
                self._task_progress.pop(next(iter(self._task_progress)))
        if tool in {"maicraft_task", "maicraft_execute", "maicraft_perceive"}:
            self._remember_task_snapshot(original, ref)
        if not tool.startswith("minecraft_") and not shown.get("_observation", {}).get("same_request_and_result"):
            # 整理时仍保留近期失败与结果未知的区别，详细过程从同一引用恢复。
            mechanical = machine_facts(original)
            self._recent_results.append(
                {
                    "tool": tool,
                    "ref": ref,
                    **({"machine_facts": mechanical} if mechanical else {}),
                    **{
                        key: shown[key]
                        for key in (
                            "ok",
                            "success",
                            "accepted",
                            "error",
                            "complete",
                            "buildable",
                            "outcome_known",
                            "plan_id",
                            "ready_to_execute",
                            "snapshot_id",
                        )
                        if key in shown
                    },
                }
            )

    def _current_task_context(self) -> Dict[str, Any]:
        """恢复与集中整理都保留玩家原文、当前工作文档、任务阶段和可补读的证据。"""
        progress = deepcopy(self._task_progress)
        ledger = getattr(self._task_tracker, "ledger", None)
        if ledger is not None:
            # accepted -> running 通常没有唤醒通知，仍须从现有账本带回已经受理的任务编号。
            for task_id in ledger.active_task_ids():
                record = ledger.get(task_id)
                if record is not None and record.initiator == self.name:
                    progress[task_id] = {**progress.get(task_id, {}), "task_id": task_id, "status": record.status}
                    if record.status != "waiting_for_decision":
                        progress[task_id].pop("decision", None)
        if self._builder is not None:
            for task_id in self._builder.pending_ids():
                progress.setdefault(task_id, {"task_id": task_id, "status": "pending"})
        return {
            "original_instructions": list(self._task_instructions),
            "todo": self._mc_state.todo_doc()["todos"],
            "notebook": self._mc_state.notebook,
            "background_tasks": list(progress.values()),
            "reasoning_steps_used": self._task_steps,
            "observations": self._observations.index(),
            "observation_count": self._observations.count,
            "recent_results": list(self._recent_results),
            "plan_facts": self._plan_facts.snapshot(),
        }

    def _unfinished_todos(self) -> bool:
        """施工、备料或核验仍有待办时，交付必须继续等待这些事项完成。"""
        return any(todo.status != "done" for todo in self._mc_state.todos)

    def _remember_task_snapshot(self, snapshot: Dict[str, Any], ref: str | None = None) -> None:
        """查询拿到的新决策立即进入任务事实；完整编号和缺口不依赖下一次历史摘要复述。"""
        task = snapshot.get("task", snapshot)
        if not isinstance(task, dict) or snapshot.get("error"):
            return
        task_id = task.get("task_id")
        raw_status = task.get("state", task.get("status"))
        status = _MAICRAFT_TASK_STATUS_MAP.get(raw_status, raw_status) if isinstance(raw_status, str) else ""
        if not isinstance(task_id, str) or status not in _TASK_EVENT_STATUSES:
            return
        progress = self._task_progress.setdefault(task_id, {"task_id": task_id})
        progress["status"] = status
        if ref:
            progress["result_ref"] = ref
        if status == "waiting_for_decision":
            facts = decision_facts(snapshot, progress.get("summary", ""))
            if facts:
                if ref:
                    facts["result_ref"] = ref
                progress["decision"] = facts
        else:
            # 恢复受理或任务结束后删除旧应答编号，下一次决策只能使用新回执提供的编号。
            progress.pop("decision", None)
        ledger = getattr(self._task_tracker, "ledger", None)
        record = ledger.get(task_id) if ledger is not None else None
        if record is not None and record.initiator == self.name:
            ledger.update(task_id, status, snapshot=snapshot)

    def _continue_task_context(self) -> Dict[str, Any]:
        """续做时相对前文最近一份完整任务状态只交付变化；首次续做或刚换任务时交付完整状态。

        前文完整状态来自集中整理的固定事实或上一份续做快照，二者都留在本次请求里，
        因此未变化的原始指令、笔记与待办不必每次唤醒再抄一遍。观察索引只在完整状态中给出，
        之后新增的观察回执本身就带着引用编号。
        """
        current = self._current_task_context()
        shown = self._shown_context
        self._shown_context = deepcopy(current)
        if not shown:
            return current
        delta: Dict[str, Any] = {}
        for key, value in current.items():
            if key == "observations":
                continue
            if key == "background_tasks":
                # 后台任务按编号比较，只补交状态、决策或结果引用变化过的那几项。
                before = {task.get("task_id"): task for task in shown.get(key, []) if isinstance(task, dict)}
                changed = [
                    task for task in value if not isinstance(task, dict) or before.get(task.get("task_id")) != task
                ]
                if changed:
                    delta[key] = changed
            elif shown.get(key) != value:
                delta[key] = value
        delta["unchanged_since_earlier_task_state"] = sorted(
            key for key in current if key not in delta and key != "observations"
        )
        return delta

    def _schedule_idle_compaction(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> None:
        """让出等待时若历史已过提前整理线，就在后台整理；身体此刻本来就在等游戏结果。"""
        if self._idle_compaction is not None and not self._idle_compaction.done():
            return
        trigger = int(self.typed_config.context.max_context_chars * _IDLE_COMPACTION_RATIO)
        if context_chars(project_context(messages), tools) <= trigger:
            return
        facts = self._current_task_context()
        self._idle_compaction = asyncio.create_task(self._compact_while_waiting(messages, tools, facts, trigger))

    async def _compact_while_waiting(
        self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], facts: Dict[str, Any], trigger: int
    ) -> None:
        """后台整理只在成功时一次性替换历史；失败或被取消都保留原件，醒来后按正常预算再判断。"""
        try:
            if await self._context_compactor.compact(
                messages, tools, facts, context_projector=project_context, trigger_chars=trigger
            ):
                self._shown_context = deepcopy(facts)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 提前整理失败不影响任务，醒来后按正常预算处理
            self._logger.warning(f"等待期间的历史整理未完成，原历史已保留：{exc}", exc=True)
        finally:
            self._task_steps += self._context_compactor.last_calls

    async def _settle_idle_compaction(self, tools: List[Dict[str, Any]]) -> None:
        """醒来时结清提前整理：已超预算就等它完成（本来也要整理），否则放弃以便立刻处理新事实。"""
        task, self._idle_compaction = self._idle_compaction, None
        if task is None:
            return
        if not task.done():
            if context_chars(project_context(self._messages), tools) > self.typed_config.context.max_context_chars:
                await task
                return
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _prepare_context(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> bool:
        """历史超预算才生成检查点，失败保留原件并挂起，避免无上下文地继续操作游戏。"""
        if context_chars(project_context(messages), tools) <= self.typed_config.context.max_context_chars:
            return True
        facts = self._current_task_context()
        try:
            if await self._context_compactor.compact(
                messages,
                tools,
                facts,
                context_projector=project_context,
            ):
                # 整理后的固定事实就是之后续做比较变化的基准。
                self._shown_context = deepcopy(facts)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 整理失败保留任务，不用半份摘要继续游戏
            self._logger.warning(f"Minecraft 历史整理失败，原任务已保留：{exc}", exc=True)
            await self._suspend_with_report(f"上下文整理失败，任务已保留：{exc}")
            return False
        finally:
            # 首次摘要和修订都计入进展统计，整理成功后继续处理原游戏目标。
            self._task_steps += self._context_compactor.last_calls
        return True

    async def _execute_tool(self, name: str, arguments: Dict[str, Any], *, round_id: str = "") -> Dict[str, Any]:
        """串行执行单个工具调用：统一经 ToolRegistry（观测/停用/熔断复用既有机制）。

        ``round_id`` 任务内 ReAct 轮 ID（思考流旁路同轮）；经 ToolInvocation
        透传到 tool.result 事件，供 WebUI 工具卡关联思考轮。无任务上下文
        的调用（如 handoff watcher）保持空串。
        """
        if self._tool_registry is None:
            return {"ok": False, "error": "工具执行失败：tool_registry 未注入", "tool": name}
        result = await self._tool_registry.invoke(
            ToolInvocation(tool_name=name, arguments=arguments, source="minecraft-react", round_id=round_id)
        )
        if result.success:
            # 知识正文可能只在文本通道中，先完整拼装再交给原文保存与呈现层。
            return successful_observation(result, arguments)
        return failed_observation(result, name)

    def _build_thinking_callback(self, round_id: str, step: int, seq_box: List[int]) -> Any:
        """构造 LLM 层增量回调（duck-typed sink），只转发 reasoning 增量。

        自足实现：不在此 import streamer 包的内部件 ThinkingStreamContext——
        跨 Agent import 违反边界（Protocol 鸭子匹配）。seq_box
        是 list 包装以实现闭包内计数自增（list[0]=... 不需 nonlocal）。
        响应正文不经此通道（每步正文由 agent.replied 事件承载）。
        """
        sink = self._thinking_sink
        if sink is None:
            return None

        def _on_delta(kind: str, text_delta: str) -> None:
            if kind != "reasoning" or not text_delta:
                return
            seq_box[0] += 1
            sink.on_thinking_delta(
                round_id=round_id,
                phase="minecraft",
                step=step,
                seq=seq_box[0],
                text_delta=text_delta,
            )

        return _on_delta

    # ==================================================================
    # 后台任务跟踪（通用基建适配声明；原 handoff 手写跟踪已迁移到通用任务基建）
    # ==================================================================

    def _track_receipt(self, tool_full_name: str, observation: Dict[str, Any]) -> None:
        """回执型工具的受理回执（``accepted=true + task_id``）→ 登记通用任务跟踪。

        受理回执照常作为观察返回 LLM（回合继续）；核实/事件/唤醒交给跟踪循环
        （通知=提示、查询=事实源），状态真变化经 ``task.changed`` 回来
        （见 ``on_task_notification``）。无 tracker（未注入）时只记日志。
        """
        # 建造入口已经区分本地 agent 任务与 Mod 施工任务，不能再按 wrapper provider 登记。
        if self._builder is not None and self._builder.owns_tool(tool_full_name):
            return
        if not isinstance(observation, dict) or observation.get("accepted") is not True:
            return
        raw_task_id = observation.get("task_id")
        if not raw_task_id:
            return
        if self._task_tracker is None:
            self._logger.debug(f"受理回执（task_id={raw_task_id}）无任务基建，跳过跟踪")
            return
        provider = ""
        spec = self._tool_registry.get(tool_full_name) if self._tool_registry is not None else None
        if spec is not None:
            provider = spec.provider
        self._task_tracker.track(
            task_id=str(raw_task_id),
            provider=provider,
            tool=tool_full_name,
            initiator=self.name,
        )
        self._logger.info(f"受理回执已登记跟踪（task_id={raw_task_id}, tool={tool_full_name}）")

    def _pending_task_count(self) -> int:
        """自己发起的进行中后台任务数（交付门禁与批次让出判定用）。"""
        pending = self._builder.pending_ids() if self._builder is not None else set()
        if self._task_tracker is not None:
            ledger = self._task_tracker.ledger
            pending.update(
                task_id
                for task_id in ledger.active_task_ids()
                if (rec := ledger.get(task_id)) is not None and rec.initiator == self.name
            )
        return len(pending)

    def _actionable_task_ids(self) -> set[str]:
        """等待施工启动或玩家决策的任务须继续处理；仅 accepted/running 才能静默等通知。"""
        pending = self._builder.actionable_ids() if self._builder is not None else set()
        ledger = getattr(self._task_tracker, "ledger", None)
        if ledger is not None:
            pending.update(
                task_id
                for task_id in ledger.active_task_ids()
                if (record := ledger.get(task_id)) is not None
                and record.initiator == self.name
                and record.status == "waiting_for_decision"
            )
        return pending

    def on_task_notification(self, payload: TaskChangedPayload) -> None:
        """task.changed 到达（发起方是自己）：注入快照消息 + 唤醒 worker。

        等价原 handoff 行为：状态真变化（含决策点/暂停/终态）与停滞告警
        （payload.alert）都送进消息队列，由下一次推理吸收。
        """
        if self._builder is not None:
            self._builder.absorb(payload)
        if payload.status not in {"accepted", "running"}:
            # 终态或待答问题接替了暂停：不再按"身体停着"处理
            self._paused_tasks.pop(payload.task_id, None)
        # 同一决策从查询和注意流抵达时只处理一次；新的 decision_id 即使仍是待决策状态也必须交给模型。
        decision_id = self._decision_id(payload.snapshot) if payload.status == "waiting_for_decision" else ""
        fingerprint = (
            [payload.status, decision_id] if decision_id else [payload.status, payload.summary, payload.snapshot]
        )
        signature = hashlib.sha256(json_text(fingerprint).encode()).hexdigest()
        if not payload.alert and self._task_notice_fingerprints.get(payload.task_id) == signature:
            return
        self._task_notice_fingerprints[payload.task_id] = signature
        while len(self._task_notice_fingerprints) > 64:
            self._task_notice_fingerprints.pop(next(iter(self._task_notice_fingerprints)))
        if not self._task_finished:
            # 任务事件记成工作阶段，下一次醒来仍知道哪个设计或施工任务走到了哪里。
            self._task_progress[payload.task_id] = {
                **self._task_progress.get(payload.task_id, {}),
                "task_id": payload.task_id,
                "status": payload.status,
                "summary": payload.summary,
            }
            if payload.status == "waiting_for_decision":
                facts = decision_facts(payload.snapshot or {}, payload.summary)
                if facts:
                    self._task_progress[payload.task_id]["decision"] = facts
            else:
                self._task_progress[payload.task_id].pop("decision", None)
            while len(self._task_progress) > 64:
                self._task_progress.pop(next(iter(self._task_progress)))
            # 设计子任务另有完成通知；这里只数身体真正去做的游戏内动作
            if payload.executor != "minecraft_builder":
                self._note_task_outcome(payload)
        # 任务回到非失败状态即清失败账并撤掉退避定时器（恢复/成功不欠一次"再等等"）。
        if payload.status not in {"failed", "timeout"} or getattr(payload, "alert", False):
            self._failure_gate.recover(payload.task_id)
            self._cancel_failure_wake(payload.task_id)
        # 受理转运行和普通进度由宿主记账，只有决策点、终态或停滞告警才需要模型判断。
        if payload.status in {"accepted", "running"} and not payload.alert:
            return
        snapshot_text = ""
        if payload.snapshot:
            shown = self._observations.present("task_notification", {"task_id": payload.task_id}, payload.snapshot)
            snapshot_text = "\n任务快照：" + json_text(shown)
            # 终态经注意流到达时，也把缺链数量和实际受电设备放进已有的六条近期结果，避免靠摘要模型复述。
            mechanical = machine_facts(payload.snapshot)
            if mechanical:
                self._recent_results.append(
                    {"tool": "task_notification", "ref": shown["_observation"]["ref"], "machine_facts": mechanical}
                )
            if payload.task_id in self._task_progress:
                self._task_progress[payload.task_id]["result_ref"] = shown["_observation"]["ref"]
                if payload.status == "waiting_for_decision" and "decision" in self._task_progress[payload.task_id]:
                    # 决策自己的引用随原始快照保存，后续普通查询不能让诊断路径指向另一份回执。
                    self._task_progress[payload.task_id]["decision"]["result_ref"] = shown["_observation"]["ref"]
        if payload.executor == "minecraft_builder":
            # 设计任务号只在本地查询；施工仍需父 Agent 显式发起，不能当作已经建好。
            if self._running:
                self._inject_wakeup_message(
                    f"[系统] 建造设计 {payload.task_id}：{payload.status}，{payload.summary}。"
                    + snapshot_text
                    + "按原目标处理已交付产物；要求建好且设计有效时用 minecraft_builder_task(action=execute) 发起施工。"
                    "只有缺少具体信息时才查询结果；设计完成不代表建筑完成，失败时处理已知阻塞。"
                )
            return
        hint = "（已有核实快照请直接使用；waiting_for_decision 用任务查询工具 answer 应答；终态沿原目标推进下一待办，缺少具体证据才补查）"
        if payload.status in {"failed", "timeout"} and not getattr(payload, "alert", False):
            # 失败唤醒退避（框架强制，实证里失败事件一到就触发完整推理是最大忙等源）
            self._gate_failure_wakeup(payload, snapshot_text, hint)
            return
        if getattr(payload, "alert", False):
            content = f"[系统] 后台任务 {payload.task_id} 停滞告警：{payload.summary}{snapshot_text}。请核查该任务。"
        else:
            content = f"[系统] 后台任务 {payload.task_id} 状态变化：{payload.status}{snapshot_text}{hint}"
        self._message_queue.append(("", content))
        self._wake_event.set()
        self._logger.info(f"任务通知注入唤醒（task_id={payload.task_id}, status={payload.status}）")

    def _inject_wakeup_message(self, content: str) -> None:
        """系统消息入队 + 唤醒 worker（非委派来源，任务号空串）。"""
        self._message_queue.append(("", content))
        self._wake_event.set()

    def _gate_failure_wakeup(self, payload: TaskChangedPayload, snapshot_text: str, hint: str) -> None:
        """失败/超时对决策唤醒的退避：阈值前推迟注入攒事实，达到阈值立即升级交模型。

        退避期的失败事实（原因、连续次数、快照）先攒在闸上，到期一并注入——
        唤醒上下文自带"为什么被醒来 + 失败事实"，模型不必重新感知一遍。
        升级后的继续失败仍按封顶间隔节流，防止"重试→再失败"缩回秒级循环。
        """
        count_before = self._failure_gate.failure_count(payload.task_id)
        delay_ms, count = self._failure_gate.on_failure(
            payload.task_id, (payload.summary or "").strip() or payload.status
        )
        facts = "；".join(self._failure_gate.facts(payload.task_id))
        if delay_ms > 0:
            content = (
                f"[系统] 后台任务 {payload.task_id} 状态变化：{payload.status}{snapshot_text}{hint}"
                f"（这是第 {count} 次失败，宿主已退避 {delay_ms // 1000} 秒后才唤醒你；"
                f"醒来前请勿发起任何调用空转等待。近期失败：{facts}）"
            )
            self._failure_gate.store_wakeup(payload.task_id, content)
            self._schedule_failure_wake(payload.task_id, delay_ms)
            self._logger.info(f"任务失败唤醒已退避（task_id={payload.task_id}, 第{count}次, {delay_ms}ms 后再交模型）")
            return
        if count_before == 0:
            # 闸上无账却走到立即升级：保留原有即时注入语义，不附加失败叙事
            content = f"[系统] 后台任务 {payload.task_id} 状态变化：{payload.status}{snapshot_text}{hint}"
        else:
            content = (
                f"[系统] 后台任务 {payload.task_id} 状态变化：{payload.status}{snapshot_text}{hint}"
                f"（已连续失败 {count} 次：{facts}。请基于这些失败事实换实质不同的方案，"
                "或用 minecraft_report(kind=escalation) 上报，不要原样重试后干等）"
            )
        self._inject_wakeup_message(content)
        self._logger.info(f"任务连续失败升级唤醒（task_id={payload.task_id}, 连续{count}次）")

    def _schedule_failure_wake(self, task_id: str, delay_ms: int) -> None:
        """退避到期注入攒下的失败事实；同一任务的旧定时器先撤再排。"""
        self._cancel_failure_wake_timer(task_id)
        self._failure_wake_timers[task_id] = asyncio.create_task(self._deferred_failure_wake(task_id, delay_ms / 1000))

    async def _deferred_failure_wake(self, task_id: str, delay_seconds: float) -> None:
        """退避到期后注入暂存的失败唤醒；期间任务恢复则暂存已被取走，到点即空操作。"""
        try:
            await asyncio.sleep(delay_seconds)
        except asyncio.CancelledError:
            return
        self._failure_wake_timers.pop(task_id, None)
        self._flush_deferred_failure(task_id)

    def _flush_deferred_failure(self, task_id: str) -> None:
        """注入退避期攒下的失败唤醒（测试可直接调用，配合注入时钟确定性推进）。"""
        content = self._failure_gate.take_wakeup(task_id)
        if content is None:
            return
        self._inject_wakeup_message(content)

    def _cancel_failure_wake_timer(self, task_id: str) -> None:
        """撤掉退避定时器（不动闸上暂存的内容）。"""
        timer = self._failure_wake_timers.pop(task_id, None)
        if timer is not None and not timer.done():
            timer.cancel()

    def _cancel_failure_wake(self, task_id: str) -> None:
        """任务不再处于失败等待：撤定时器并丢弃暂存的失败唤醒。"""
        self._cancel_failure_wake_timer(task_id)
        self._failure_gate.take_wakeup(task_id)

    def _note_task_outcome(self, payload: TaskChangedPayload) -> None:
        """游戏内动作成败计数：连续失败到第 3 次时告诉主播卡在哪，之后每再失败 5 次补报一次。

        成功一次就清零。通报走 attention_required 叙事事件，只让主播有话可讲
        （"取铁板这步一直失败，还在换办法"），不打断任务、不替身体决定放弃或换法。
        """
        if payload.status == "succeeded":
            self._failure_streak = 0
            return
        if payload.status not in {"failed", "timeout"}:
            return
        self._failure_streak += 1
        streak = self._failure_streak
        if streak < _FAILURE_STREAK_NOTICE or (streak - _FAILURE_STREAK_NOTICE) % _FAILURE_STREAK_REPEAT:
            return
        reason = (payload.summary or "").strip() or payload.status
        # 叙事是主播自己的经历，用第一人称写：连续失败 -> 照实说卡在哪 -> 说明还在换办法，不让主播替我放弃
        message = (
            f"我卡在同一步了：最近连续 {streak} 个游戏内动作没有成功（最近一次：{reason}），"
            "我还在换办法继续，没有放弃。"
        )
        spawn_background_task(
            self._emit_game_event("attention_required", message),
            logger=self._logger,
            tasks=self._bg_tasks,
            label="MinecraftAgent.failure_streak_notice",
        )
        self._logger.info(f"游戏内动作连续失败 {streak} 次，已通报主播")

    def _absorb_pause_event(self, task_id: str, event_type: str, event: Dict[str, Any]) -> None:
        """旧版注意流的暂停/恢复事件（完整事件体）：只处理本人在册的后台任务。"""
        ledger = getattr(self._task_tracker, "ledger", None)
        record = ledger.get(task_id) if ledger is not None else None
        if record is None or record.initiator != self.name:
            return
        if event_type == "resumed":
            self._paused_tasks.pop(task_id, None)
            return
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        self._note_task_paused(
            task_id, str(data.get("pause_reason") or data.get("reason") or ""), str(event.get("message") or "")
        )

    def _note_task_paused(self, task_id: str, reason: str, detail: str) -> None:
        """Mod 暂停了本人的后台任务：唤醒自己处理，并让主播知道身体停下了。

        同一次暂停只通报一次。控制权暂不可用的暂停会在交还控制后由 Mod 自动继续，
        其他原因（如自卫把身体带离工位）不继续就一直停着——两种情况都如实说明，
        怎么处理由模型按现场决定。
        """
        reason = reason or "unknown"
        if self._paused_tasks.get(task_id) == reason:
            return
        self._paused_tasks[task_id] = reason
        if task_id in self._task_progress:
            self._task_progress[task_id].update(status="paused", summary=detail or reason)
        if reason == "control_unavailable":
            label = "第一人称控制暂时不可用（游戏窗口不在前台或玩家接管了操作）"
            advice = (
                "控制权回来后 Mod 会自动继续。暂停期间身体没有在动：可以先做不需要身体动作的准备"
                "（读下一步要用的资料、想好方案），不要把等待当成任务在推进。"
            )
        else:
            label = "自卫时被带离了工位" if reason == "self_defense_displaced" else f"原因：{reason}"
            advice = (
                "不处理它就会一直停着：先看一眼现场，再用 maicraft_task(action=resume) 从当前位置继续，"
                "或取消、改方案，确实推不动再上报主播。"
            )
        detail_text = f"（Mod 说明：{detail}）" if detail else ""
        self._inject_wakeup_message(f"[系统] 后台任务 {task_id} 已被 Mod 暂停：{label}{detail_text}。{advice}")
        spawn_background_task(
            # 告诉主播的是"我手上的事停了"：主播和游戏里的我是同一个人，不说成另一个身体
            self._emit_game_event("attention_required", f"我手上的游戏内动作暂停了：{label}。"),
            logger=self._logger,
            tasks=self._bg_tasks,
            label="MinecraftAgent.task_paused_notice",
        )
        self._logger.info(f"后台任务暂停，已唤醒处理（task_id={task_id}, reason={reason}）")

    # ==================================================================
    # 身体事件（注意流增量读取）
    # ==================================================================

    def _on_attention_notification(self, task_id: str = "") -> None:
        """注意流资源通知（举旗级、可丢）：有进行中工作时才去读身体事件。

        空闲时直接返回——身体事件的日常值守归主播侧采集器，"游戏 Agent 只在干活时
        需要知道身体受威胁"，这样"空闲零消耗"仍然成立（通知到达不产生 MCP 调用与 LLM 推理）。
        """
        if not (self._batch_active or self._pending_task_count() > 0):
            return
        spawn_background_task(
            self._drain_attention(),
            logger=self._logger,
            tasks=self._bg_tasks,
            label="MinecraftAgent.drain_attention",
        )

    async def _drain_attention(self) -> None:
        """共用一个游标读取者，分页补读期间到达的通知按顺序核实。"""
        async with self._attention_read_lock:
            await self._read_attention_page()

    async def _read_attention_page(self) -> None:
        """按游标增量读一页注意流，按事件归属分流。

        注意流同时承载两类事件，本方法就是分流点：

        - **任务事件**（带 ``task_id``）→ 完整回执可直接落账；简短通知先查询当前任务，
          确认终态或待答问题后才更新本人的工作事实。
        - **身体事件**（``priority="important"``）→ 注入待吸收消息，供本 Agent 判断
          要不要因此调整手里的活。

        规格三条：
        - **增量**：带 stream_id 与 after_cursor，只取新事件，不重读最新一页；
        - **首读只对游标**：第一次读到的是一页历史，注入会把陈年事件灌进上下文，
          所以首读只记录游标、不注入；
        - **重新同步不注入**：换世界/流重置/历史已丢（resync_required）时同样只重置
          游标，旧世界的身体事件不属于当前这条命。
        读取失败按"这一轮没读到"处理并记日志，绝不当作"身体没事"。
        """
        provider = self._attention_provider
        if provider is None:
            return
        try:
            page = await provider.read_attention(
                stream_id=self._attention_stream_id,
                after_cursor=self._attention_cursor,
                limit=_ATTENTION_PAGE_LIMIT,
            )
            if not isinstance(page, dict):
                return
            events = page.get("events")
            if is_reference(events):
                if self._mcp_client is None:
                    raise ValueError("注意流事件需要补读，但 MCP 连接不可用")
                events = await read_receipt(self._mcp_client, events)
            if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
                raise ValueError("注意流缺少完整事件数组")
        except Exception as exc:  # noqa: BLE001 - 读取失败只降级，不打断任务
            self._logger.warning(f"身体事件读取失败（本轮按未读到处理）: {exc}", exc=exc)
            return

        resync = bool(page.get("resync_required") or page.get("history_lost") or page.get("stream_reset"))
        stream_id = page.get("stream_id")
        if isinstance(stream_id, str) and stream_id:
            self._attention_stream_id = stream_id
        cursor = page.get("cursor")
        if isinstance(cursor, int):
            self._attention_cursor = cursor

        if resync or not self._attention_primed:
            self._attention_primed = True
            self._logger.info(
                f"身体事件游标{'重新同步' if resync else '首次建立'}："
                f"stream={self._attention_stream_id}, cursor={self._attention_cursor}, "
                f"本页 {len(events)} 条不注入"
            )
            return

        injected = 0
        verified_tasks: set[str] = set()
        for event in events:
            if event.get("task_id"):
                if page.get("schema_version", 1) >= 3:
                    # 简短通知不含完整决策；先核实当前任务，不能凭门铃移除交付门禁或覆盖已有失败事实。
                    task_id = str(event["task_id"])
                    if task_id not in verified_tasks:
                        await self._verify_short_task_event(event)
                        verified_tasks.add(task_id)
                    continue
                # 任务类事件（priority="task"）归任务跟踪：写记录表，终态即"活干完了"。
                # 不进消息队列——记录表写入本身经 task.changed 唤醒本 Agent，重复注入是两遍。
                self._absorb_task_event(event)
                continue
            if event.get("priority") != "important":
                continue  # background 只是世界时间/天气
            self._inject_wakeup_message(self._attention_event_message(event))
            await self._record_body_event(event)
            injected += 1
        if injected:
            self._logger.info(f"身体事件注入 {injected} 条（cursor={self._attention_cursor}）")

    async def _verify_short_task_event(self, event: Dict[str, Any]) -> None:
        """只核实本人在册任务；查询失败交给既有跟踪器继续等待，不将缺少回执判成已完成。"""
        task_id = str(event.get("task_id") or "")
        tracker = self._task_tracker
        ledger = getattr(tracker, "ledger", None)
        record = ledger.get(task_id) if ledger is not None else None
        if record is None or record.initiator != self.name:
            return
        try:
            result = await self._attention_provider.query_task(task_id)
            snapshot = result.get("snapshot") if isinstance(result, dict) else None
            if not isinstance(snapshot, dict) or snapshot.get("task_id") != task_id:
                raise ValueError("任务查询未返回同一任务的可读快照")
            self._remember_task_snapshot(snapshot)
            status = result.get("status")
            if status == "paused":
                # 通用词表认不出暂停，跟踪器会忽略它；这里按核实快照把"身体停下了"交给自己处理
                self._note_task_paused(
                    task_id, str(snapshot.get("pause_reason") or ""), str(event.get("message") or "")
                )
            elif status in {"running", "accepted"}:
                # 控制权交还后 Mod 自动恢复：撤掉暂停记录，继续按原通知等待结果
                self._paused_tasks.pop(task_id, None)
            if status == "waiting_for_decision":
                # 待答期间可能改成新的死亡恢复问题；同状态轮询不会广播，因此按新决策编号补一次通知。
                self.on_task_notification(
                    TaskChangedPayload(
                        task_id=task_id,
                        status="waiting_for_decision",
                        initiator=self.name,
                        executor=record.executor,
                        snapshot=snapshot,
                        summary=str(result.get("summary") or ""),
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.warning(f"简短任务通知尚未取得核实结果（task_id={task_id}）", exc=exc)
            tracker.notify(task_id)

    async def _record_body_event(self, event: dict) -> None:
        """记下本批任务期间的身体事件，并决定是否即时告知主播。

        上报要能说"你在进行 xx 任务的时候遭遇了僵尸的攻击，正在处理"，
        所以身体事实要在本批内留存；同时按"片段开始/结束"各通报一次——
        片段由 mod 侧聚合（一次遭遇最多两条），因此不会刷屏。
        """
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        event_type = str(event.get("type") or "")
        phase = str(data.get("phase") or "")
        record = {
            "event_type": event_type,
            "phase": phase,
            "cursor": int(event.get("cursor") or 0),
            "occurred_at_ms": upstream_timestamp_ms(event.get("timestamp")),
            "message": str(event.get("message") or ""),
            "facts": data,
        }
        self._batch_body_events.append(record)
        if len(self._batch_body_events) > _MAX_BATCH_BODY_EVENTS:
            self._batch_body_events = self._batch_body_events[-_MAX_BATCH_BODY_EVENTS:]

        if not self._batch_active:
            return  # 空闲时身体事件由采集器那条通道交给主播，游戏 Agent 不插话
        key = (event_type, phase)
        if phase not in ("started", "finished") or key in self._body_notified:
            return
        self._body_notified.add(key)
        notice = f"执行任务期间遭遇身体事件：{event_type}" if phase == "started" else f"身体事件已结束：{event_type}"
        await self._emit_game_event(
            "attention_required",
            notice,
            scene="",
            occurred_at_ms=record["occurred_at_ms"],
            already_resolved=phase == "finished",
        )

    @staticmethod
    def _attention_event_message(event: dict) -> str:
        """把一条注意流事件压成一句可读事实：只陈述事件里真有的字段。"""
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        cause = data.get("cause") if isinstance(data.get("cause"), dict) else {}
        repeat = data.get("repeat") if isinstance(data.get("repeat"), dict) else {}
        defense = data.get("defense") if isinstance(data.get("defense"), dict) else {}
        parts = [f"[身体事件] {event.get('message') or event.get('type') or '未知事件'}"]
        attacker = cause.get("causing_entity_type_id")
        if attacker:
            distance = cause.get("causing_entity_distance")
            parts.append(f"来源 {attacker}" + (f"（距离 {distance}）" if distance is not None else ""))
        if repeat:
            parts.append(f"本片段命中 {repeat.get('hits')} 次、累计掉血 {repeat.get('damage_total')}")
        if data.get("current_health") is not None:
            parts.append(f"当前血量 {data['current_health']}")
        if defense:
            parts.append("自卫链本次可接管" if defense.get("would_engage") else "自卫链本次不接管")
        if data.get("evidence") == "health_drop_without_packet":
            parts.append("（只是观察到掉血，没有伤害包，来源未知）")
        parts.append("这是观察不是命令；需要行动时自行决定，并按需上报主播")
        return "；".join(parts)

    def _absorb_task_event(self, event: Dict[str, Any]) -> None:
        """把一条任务类注意流事件落进任务记录表（本 Agent 自己的后台任务）。

        「任务干完了」这个事实的权威来源是 Mod 的注意流，不是轮询查询：
        事件本身带着 ``task_id`` 与结算内容（Mod 在结算时把任务结果压进 ``data``），
        所以这里不需要再发一次 MCP 查询。

        只在**记录表里已有该任务号**时写入——记录表是本 Agent 自己登记的后台任务
        台账，对不在册的任务号建条目会造出没人认领的幽灵任务。写入后由记录表自己
        广播 ``task.changed`` 唤醒本 Agent：终态从记录表移除（交付门禁随之为 0），
        决策点/暂停则注入消息让本 Agent 处理。

        状态词表：Mod 的事件类型名与任务词表多数重合，不重合的（如 ``completed``）
        由 ``_MAICRAFT_TASK_STATUS_MAP`` 翻译——与轮询查询共用同一张表，不另立词表。
        """
        task_id = str(event.get("task_id") or "")
        event_type = str(event.get("type") or "")
        if task_id and event_type in {"paused", "resumed"}:
            # 暂停/恢复不进通用词表（分不清等人回答还是系统暂停），但身体是否停着必须让自己知道
            self._absorb_pause_event(task_id, event_type, event)
            return
        status = _MAICRAFT_TASK_STATUS_MAP.get(event_type, event_type)
        if not task_id or status not in _TASK_EVENT_STATUSES:
            return
        tracker = self._task_tracker
        ledger = getattr(tracker, "ledger", None) if tracker is not None else None
        record = ledger.get(task_id) if ledger is not None else None
        if record is None:
            return
        prior_status = record.status
        written = ledger.update(
            task_id,
            status,
            # 完成与决策事件已有真实结果，不能只留下事件类型再让父 Agent 猜下一步。
            snapshot={"event_type": event_type, "task_id": task_id, "data": event.get("data") or {}},
            summary=self._task_event_summary(event_type, event),
        )
        if written is not None:
            self._logger.info(f"任务事件落账（注意流）: task_id={task_id} status={status} type={event_type}")
        elif status == prior_status == "waiting_for_decision" and record.initiator == self.name:
            # 通用台账对同状态更新静默；Minecraft 仍须交付这次新的原生决策，不能漏掉恢复步骤里的再次失败。
            self.on_task_notification(
                TaskChangedPayload(
                    task_id=task_id,
                    status=status,
                    initiator=self.name,
                    executor=record.executor,
                    snapshot={"event_type": event_type, "task_id": task_id, "data": event.get("data") or {}},
                    summary=self._task_event_summary(event_type, event),
                )
            )

    @staticmethod
    def _decision_id(snapshot: Dict[str, Any]) -> str:
        """只从原生决策位置读取编号，不把蓝图等任意嵌套内容当成待应答事实。"""
        return str(task_decision(snapshot).get("decision_id") or "")

    def _absorb_resume_receipt(self, arguments: Dict[str, Any], receipt: Dict[str, Any]) -> None:
        """恢复答复已被 Mod 受理时立即解除旧待决策事实，不等待轮询才允许零推理等待。"""
        task_id = receipt.get("task_id")
        if arguments.get("action") not in {"answer", "resume"} or task_id != arguments.get("task_id"):
            return
        if receipt.get("state") != "running" or receipt.get("error") or receipt.get("success") is False:
            return
        ledger = getattr(self._task_tracker, "ledger", None)
        record = ledger.get(task_id) if ledger is not None else None
        if record is None or record.initiator != self.name:
            return
        ledger.update(task_id, "running", snapshot=receipt, summary="原任务恢复请求已受理")
        progress = self._task_progress.setdefault(task_id, {"task_id": task_id})
        progress.update(status="running", summary="原任务恢复请求已受理")
        progress.pop("decision", None)

    @staticmethod
    def _task_event_summary(event_type: str, event: Dict[str, Any]) -> str:
        """任务事件压成一句可读事实：只陈述事件里真有的字段。"""
        message = str(event.get("message") or "").strip()
        return message or f"注意流任务事件：{event_type or '未知'}"

    def _mark_delegated_running(self) -> None:
        """把本批吸收的委派任务标记为进行中（写回任务记录表）。

        同时并入终态追踪清单——本批结束时按批次终止语义写终态
        （delivery → succeeded / escalation → failed / 其余保持进行中）。
        """
        if self._task_tracker is None:
            self._delegated_batch_ids.clear()
            return
        # 收到继续指令后，旧委派与新补充指令共同恢复运行，直到最终交付才移除原委派。
        for tid in self._delegated_finished_ids:
            self._task_tracker.ledger.update(tid, "running", summary="执行 Agent 已恢复原任务")
        for tid in self._delegated_batch_ids:
            self._task_tracker.ledger.update(tid, "running", summary="执行 Agent 已开始处理")
            self._delegated_finished_ids.append(tid)
        self._delegated_batch_ids.clear()

    def _finish_delegated(self, status: str, *, summary: str) -> None:
        """批次终态时把已受理委派任务写终态（delivery=succeeded 等）。"""
        if self._task_tracker is None:
            return
        for tid in self._delegated_finished_ids:
            self._task_tracker.ledger.update(tid, status, summary=summary)
        self._delegated_finished_ids.clear()

    async def _suspend_with_report(self, reason: str) -> None:
        """角色已停止自动行动时主动上报待定夺，并保留原委派，不能让主播仍把它当成正在施工。"""
        self._task_suspended = True
        if self._task_tracker is not None:
            for task_id in self._delegated_finished_ids:
                self._task_tracker.ledger.update(
                    task_id,
                    "waiting_for_decision",
                    summary=reason,
                    snapshot={"waiting_for_instruction": True, "reason": reason},
                )
        await self._emit_report("escalation", reason)

    # ==================================================================
    # 上报通道（玩家→主播：delivery/escalation）
    # ==================================================================

    async def _handle_report(self, kind: str, content: str, scene: str) -> Optional[str]:
        """minecraft_report 回调：发射 game.report + 记入 recent_reports。

        Returns:
            拒绝原因（交付门禁：有未决 handoff 时拒绝 delivery——后台任务
            没收尾不能交付，错误观察作为观察返回 LLM 自纠）；None = 受理。
        """
        pending = self._pending_task_count()
        if kind == "delivery" and pending:
            return (
                f"仍有 {pending} 个后台任务未决（跟踪中），"
                "不能交付——先用任务查询工具处理它们，或改用 escalation 说明情况"
            )
        if kind == "delivery" and self._unfinished_todos():
            return "仍有未完成待办，不能交付；继续推进备料、施工或验证，确实受阻时使用 escalation"
        await self._emit_report(kind, content, scene=scene)
        self._task_reported = True
        self._task_finished = kind == "delivery"
        self._task_suspended = kind == "escalation"
        # 委派账面：交付写 succeeded 终态；升级写 waiting_for_decision——
        # "升级=受阻上报待定夺"，记 failed 是语义误用且终态粘滞会让续跑
        # 失明（恢复后无处可写）。与 _suspend_with_report 同形（快照同形、
        # **保留追踪清单**），批次重启时旧委派经 _mark_delegated_running
        # 重写 running。
        if kind == "delivery":
            self._finish_delegated("succeeded", summary=f"{kind}: {content}")
        elif self._task_tracker is not None:
            for tid in self._delegated_finished_ids:
                self._task_tracker.ledger.update(
                    tid,
                    "waiting_for_decision",
                    summary=f"{kind}: {content}",
                    snapshot={"waiting_for_instruction": True, "reason": f"{kind}: {content}"},
                )
        return None

    def _system_message(self) -> Dict[str, Any]:
        """任务历史开头的系统消息；技能目录段附计量标注，上下文面板据此把它记入技能段。

        标注由 LLM 层收下用于分段统计，不发给模型；目录为空时不附标注。
        """
        content = self._system_prompt()
        message: Dict[str, Any] = {"role": "system", "content": content}
        catalog = self._skill_catalog_section()
        if catalog and catalog in content:
            message["context_parts"] = [{"section": SECTION_SKILLS, "name": "技能目录", "text": catalog}]
        return message

    def _system_prompt(self) -> str:
        """系统提示词：渲染 prompt_manager 模板（无则用内建兜底）。"""
        if self._prompt is not None:
            try:
                return self._with_gameplay_prompt(self._prompt.render("amaidesu_minecraft_agent"))
            except Exception as exc:  # noqa: BLE001 - 渲染失败降级内建
                self._logger.warning(f"MinecraftAgent 提示词渲染失败，降级内建: {type(exc).__name__}: {exc}")
        return self._with_gameplay_prompt(
            "你是 Minecraft 世界中的 AI 玩家。用工具玩 Minecraft："
            "minecraft_todo 管理目标与进度、minecraft_notebook 记录关键信息，"
            "其余工具（maicraft_*）是你在游戏内的操作能力。"
            # 模板不可用时也直接推进游戏目标；只有复杂任务才需要维护待办。
            "复杂任务用 todo 维护阶段，简单指令直接执行；"
            "全部完成后用 minecraft_report(kind=delivery) 交付总结再结束；"
            "确实无法自行解决时用 minecraft_report(kind=escalation) 上报后停止。"
            # 模板不可用时同样要求：身体执行任务时提前准备下一步，身体闲着时尽快让它动起来
            "execute 受理后复用宿主任务通知；身体执行任务时先准备下一步要用的契约与方案，"
            "既无可推进也无可准备的事项时才调用 minecraft_wait；任务被暂停时按原因处理，不干等。"
            "没有后台任务在执行时优先发出能让身体动起来的调用。"
            "历史整理后需要核对原始回执或请求时，按 ref/path 只读需要的字段。"
            "同一路径连续失败就换路径，都试过仍不行再 escalation；上报只写几句要点。"
            # 模板不可用时也要像生存玩家一样玩：不靠管理员命令或创造物品走捷径
            "像生存玩家一样玩：不用 /tp、/give 等管理员命令，也不用创造模式专属的物品和方块；"
            "卡住了换正常办法，还不行就把卡点上报主播。"
            # 不摆烂：本次目标要的结果达成才算交付；卡住时按读清失败、对症改、换路、查资料的顺序想办法
            "本次目标要的结果真的达成才交付，拿别的顶替或做一半不算；子问题解决后直接回原目标，不请示。"
            "卡住时先读清失败原因、对症改一处、再换实质不同的路、针对卡点查资料；"
            "说做不到要有证据，至少试过两种办法再上报，并写清试过什么。"
            "工具调用：一次可调多个工具（它们会依次执行）；执行串行但你可一次发出多个请求。"
        )

    def _with_gameplay_prompt(self, prompt: str) -> str:
        """所有环境都保留访问与阶段边界；装配建筑设计入口时再附加委派方式，有技能库时附技能目录。"""
        prompt += _GAMEPLAY_RULES
        if self._builder is not None:
            prompt += (
                "\n房屋与外观结构设计交给 minecraft_builder_request：传自然语言 requirements 和已知现场 context，"
                "不要自己生成完整建筑 JSON。intent=build 要求建好，intent=design 只要设计。"
                "它立即返回任务号，不要轮询等待；完成事件会通知你。"
                "用 minecraft_builder_task 查询、修改、取消设计；设计通过后用 action=execute 按引用施工，"
                "再跟进 Mod 施工任务，核实完成后才能交付。"
            )
        return prompt + self._skill_catalog_section()

    # ==================================================================
    # 技能（玩法经验文档：目录常驻系统提示词，正文按需读取）
    # ==================================================================

    def _skill_environment(self) -> SkillEnvironment:
        """技能前提的已知环境事实：读到已装模组清单后才认定"装了/没装"，否则按未知。"""
        if self._installed_mods is None:
            return {}
        return {"mods": self._installed_mods}

    def _skill_catalog_section(self) -> str:
        """系统提示词里的技能目录段；没有技能库或没有可用技能时整段省略。"""
        if self._skills is None:
            return ""
        catalog = render_catalog(self._skills.catalog(self.name, self._skill_environment()))
        if not catalog:
            return ""
        return (
            "\n\n## 技能\n"
            "技能是把一类目标做成的打法与经验：步骤、决策点、常见坑和该查的资料。"
            "开始一类不熟悉或容易出错的工作前，用 minecraft_skill 按名读取相关技能；"
            "技能是参考，不授予额外权限，现场证据与能力契约优先。"
            "标注“前提待确认”的技能只在现场确实具备该前提时使用。\n" + catalog
        )

    def _read_skill(self, name: str) -> Dict[str, Any]:
        """minecraft_skill：按本 Agent 受众名与当前已知环境读取技能正文。"""
        if self._skills is None:
            return {"success": False, "error": "技能库未装配"}
        return self._skills.read(name, self.name, self._skill_environment())

    async def _probe_installed_mods(self) -> None:
        """每次连接读一次 Mod 的已装模组清单；读不到按未知处理，不阻断任务。"""
        if self._skills is None or self._installed_mods_probed:
            return
        client = self._mcp_client
        if client is None or not getattr(client, "connected", False):
            return
        # 先置位再读：旧版 Mod 没有该资源时，同一连接内不必每批重复尝试
        self._installed_mods_probed = True
        try:
            self._installed_mods = await read_installed_mods(client)
        except Exception as exc:  # noqa: BLE001 - 环境读取失败只影响技能筛选，不能中断任务
            self._logger.warning(f"读取已装模组清单失败，技能前提按未知处理：{exc}", exc=True)
            self._installed_mods = None
            return
        if self._installed_mods is not None:
            self._logger.info(f"已读取已装模组清单：{len(self._installed_mods)} 个模组")

    # ==================================================================
    # 事件上报（三通道·事件；GamePayload(game="minecraft")）
    # ==================================================================

    @staticmethod
    def _batch_body_resolved(events: List[Dict[str, Any]]) -> bool:
        """本批身体事件是否都已收尾。

        片段在 mod 侧成对发出（started → finished），所以按事件类型配对计数：
        每个 started 都有对应 finished 才算收尾；还有未收尾的片段就返回 False，
        宁可说"可能还在进行"，也不把未结束说成已结束。
        """
        started: Dict[str, int] = {}
        finished: Dict[str, int] = {}
        for item in events:
            kind = str(item.get("event_type") or "")
            phase = str(item.get("phase") or "")
            if phase == "started":
                started[kind] = started.get(kind, 0) + 1
            elif phase == "finished":
                finished[kind] = finished.get(kind, 0) + 1
        return all(finished.get(kind, 0) >= count for kind, count in started.items())

    async def _emit_game_event(
        self,
        event_type: Literal["report", "attention_required", "error"],
        message: str,
        *,
        scene: str = "",
        report_kind: Optional[Literal["delivery", "escalation"]] = None,
        occurred_at_ms: int = 0,
        already_resolved: bool = False,
    ) -> None:
        """emit game.* 事件（统一 payload 构造）。

        叙事面事件（report / attention_required）自动带上**本批任务期间**观察到的
        身体事件上下文：主播据此说"你在进行 xx 任务的时候遭遇了僵尸的攻击"，
        而不必自己去猜时间与结局。已结束的状况用 ``already_resolved`` 标出，
        让措辞能自然滞后（不说"正在被攻击"而说"刚才"）。
        """
        if self._event_bus is None:
            return
        body_events: List[Dict[str, Any]] = []
        if event_type in ("report", "attention_required"):
            body_events = [dict(item) for item in self._batch_body_events]
            if body_events and not occurred_at_ms:
                stamps = [int(item.get("occurred_at_ms") or 0) for item in body_events]
                known = [stamp for stamp in stamps if stamp > 0]
                occurred_at_ms = min(known) if known else 0
            if body_events and not already_resolved:
                already_resolved = self._batch_body_resolved(body_events)
        payload = GamePayload(
            game="minecraft",
            event_type=event_type,
            message=message,
            scene=scene,
            report_kind=report_kind,
            occurred_at_ms=occurred_at_ms,
            already_resolved=already_resolved,
            body_events=body_events,
        )
        if event_type == "report":
            # 上报事件同步进内存（minecraft_get_state 的 recent_reports 数据源）
            self._mc_state.add_report(report_kind or "", message, scene)
            await self.emit_event(CoreEvents.GAME_REPORT, payload)
        elif event_type == "attention_required":
            await self.emit_event(CoreEvents.GAME_ATTENTION_REQUIRED, payload)
        else:
            await self.emit_event(CoreEvents.GAME_ERROR, payload)

    async def _emit_report(
        self,
        kind: Literal["delivery", "escalation"],
        message: str,
        *,
        scene: str = "",
    ) -> None:
        """emit game.report（玩家→主播上报：交付总结 / 升级决策）。"""
        await self._emit_game_event("report", message, scene=scene, report_kind=kind)

    async def emit_attention_required(self, message: str, *, scene: str = "") -> None:
        """emit game.attention_required（安全阀偏差报告）。"""
        await self._emit_game_event("attention_required", message, scene=scene)

    async def emit_error(self, message: str, *, scene: str = "") -> None:
        """emit game.error（异常）。"""
        await self._emit_game_event("error", message, scene=scene)

    # ==================================================================
    # 状态查询（测试/外部）
    # ==================================================================

    def get_state_snapshot(self) -> Dict[str, Any]:
        """导出状态快照（minecraft_get_state 同构；测试可断言）。"""
        return self._mc_state.to_dict()

    @property
    def paused(self) -> bool:
        """agent_state 辅助（测试断言）。"""
        return self._state == AgentState.PAUSED
