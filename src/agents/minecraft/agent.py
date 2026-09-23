"""MinecraftAgent —— Minecraft 世界的 AI 玩家（事件驱动 ReAct Agent）

核心意象：一个用 MCP 工具玩 Minecraft 的普通 ReAct Agent。
- 主播 Agent 是它的用户：framework_delegate 委派派活、minecraft_get_work_log 读工作文档、
  minecraft_report 收上报
- 命令驱动（类 Code Agent）：空闲零消耗；委派指令唤醒任务，任务内有界
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
import json
import uuid
from collections import deque
from typing import Any, Deque, Dict, Iterable, List, Literal, Optional

from src.agents.minecraft.attention_matrix import upstream_timestamp_ms
from src.agents.minecraft.builder.controller import MinecraftBuilderController
from src.agents.minecraft.design_progress import MachineDesignProgress
from src.agents.minecraft.state import MinecraftAgentState, MinecraftInstruction
from src.agents.minecraft.tools import MinecraftToolProvider
from src.modules.agents.base import AgentState, BaseAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.logging import get_logger
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry

from .config import MinecraftConfig

__all__ = ["MinecraftAgent"]

# 游戏决策使用独立用途；建筑设计的模型预算由子 Agent 自己声明。
MINECRAFT_PROFILE = "minecraft"

# 旧观察规整：最近 N 条工具结果保留原文，更早的替换为占位符（防上下文膨胀）
_OBSERVATION_KEEP = 10

# 交付/上报文本截断（事件 payload 与兜底交付共用）
_MAX_DELIVERY_TEXT = 800

# 身体事件单页上限：一次增量读取最多取几条注意流事件（超出部分下次接着读）
_ATTENTION_PAGE_LIMIT = 10

# 本批任务期间保留的身体事件条数上限（上报携带的任务上下文，多了只会淹没重点）
_MAX_BATCH_BODY_EVENTS = 5

# 模板与无提示词管理器的运行环境共用这些边界，确保角色在缺料和设计完成后采取同样的行动。
_GAMEPLAY_RULES = (
    "\n执行边界：缺料不授权探索陌生箱子。仅在玩家明确要求搜索，或告示牌、可信记忆、聊天说明、"
    "真实历史观察指向具体容器与目标材料时，才使用定向 use_container/manage_container；"
    "说明访问依据并遵守取用权限与保护范围。附近有箱子、地标名称和缺料本身都不是授权。"
    "无合规库存来源时按已允许的合成或采集路线继续，确实无法取得时报告材料缺口。"
    "设计审阅 success 只表示检查结束，必须核对 buildable/physical_layout_compiled 等真实结果。"
    "用户要求建好且设计可用、符合要求时，下一步是备料和施工，无需重复询问相同建造授权；"
    "用户只要设计时保持只设计。设计不可建时处理具体阻塞，不能把审阅完成当作工程完成。"
    "原任务会随后台唤醒恢复，沿原待办和最新阶段继续；没有其他可推进事项时让出本轮等待任务通知。"
    "机器组合由你选择具体设备、工件承载面、输送和回流关系：先读 maicraft://knowledge/machine_assembly、"
    "组件原生接口和实际配方定义，再给 design_machine/build_machine 提交显式 blueprint 与 assembly。"
    "不要用产品专用工作站模板替代组合设计，不要因为已有另一条简单配方就改变目标产物。"
    "有具体产物要求时将实际目标写入 expected_output，禁用模组写入 constraints.forbidden_mods；"
    "比较连续产量、用料和占地时区分估算与实测，缺少运行证据不能声称效率最优。"
    "传送带可声明带段或折线路径，由 Mod 展开端轴；读取返回的 power_ports 选择端点或中间带轮接入动力。"
    "计划端口存在不表示现场已供电，转角物品交接也不表示动力相连。安装附件时声明最终方块形态。"
    "设计被拒绝时按 design_diagnostics 或 validation.issues 修订原蓝图，保留目标与禁用模组；"
    "修改后的 execute 请求使用新 request_key。相同拒绝没有新证据时应改变方案或上报阻塞，勿反复原样提交或查询无关观察。"
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


def _spec_to_fn(spec: ToolSpec) -> Dict[str, Any]:
    """ToolSpec → OpenAI 风格 function def（name 用派生全名）。"""
    entry: Dict[str, Any] = {"name": spec.full_name, "description": spec.description}
    if spec.parameters_schema is not None:
        entry["parameters"] = spec.parameters_schema
    return entry


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
        """
        super().__init__(event_bus=event_bus)
        self.typed_config = config
        self._llm = llm_manager
        self._prompt = prompt_manager
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._thinking_sink = thinking_sink

        # Agent 内部状态（内存，不持久化）
        self._mc_state: MinecraftAgentState = MinecraftAgentState()
        # 局部工具执行器：LLM 循环直接调（不依赖 registry；有 registry 时同一实例注册）
        self._tool_provider: MinecraftToolProvider = MinecraftToolProvider(
            state=self._mc_state,
            report_callback=self._handle_report,
            wait_callback=self._request_wait,
        )

        # 命令驱动运行骨架：worker 等命令信号，任务内有界 ReAct 循环
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._wake_event: asyncio.Event = asyncio.Event()
        # 指令队列（委派接收 / 系统注入投递；元素 = (task_id, content)，
        # task_id 空串表示非委派来源；任务执行中也可追加——LLM 下一次推理吸收）
        self._message_queue: Deque[tuple] = deque()
        # 原始指令和执行阶段跟随逻辑任务，不能因一次后台通知重新开批就丢失。
        self._task_instructions: List[str] = []
        self._task_progress: Dict[str, Dict[str, Any]] = {}
        self._task_steps = 0
        self._task_finished = True
        self._task_suspended = False
        self._design_progress = MachineDesignProgress()
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
        self._logger.info(
            f"MinecraftAgent 已构造 (max_steps={config.max_steps}, "
            f"llm={'已注入' if llm_manager else '无'}@{MINECRAFT_PROFILE})"
        )

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
        声明，fail-closed：每个工具默认仅 minecraft 可见，读工具 perceive
        放开给主播直读；恢复刷新时对新工具集重派名单，不落"未列出=全员"
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

        工具名按原始名后缀定位（server 特有知识留在此处）；通知订阅只在
        首次绑定建立（多订阅方通道，恢复重绑定不得重复入队）。
        """
        for spec in prov.list_tools():
            if spec.name.endswith("maicraft_task"):
                prov.task_query_tool = spec.full_name
                prov.task_status_map = _MAICRAFT_TASK_STATUS_MAP
                break
        prov.attention_uri = "maicraft://attention"
        # 身体事件读取：工具名与固定入参都在这一处声明，provider 只补游标与页大小。
        # 本 Agent 另订阅同一条通知通道（举旗级），只在有进行中工作时才真去读——
        # 空闲时通知到达即返回，不产生 MCP 调用也不唤 LLM。
        for spec in prov.list_tools():
            if spec.name.endswith("perceive"):
                prov.attention_read_tool = spec.full_name
                prov.attention_read_arguments = {"view": "attention"}
                break
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
            return

    @staticmethod
    def _maicraft_visible_to(specs: Iterable[ToolSpec]) -> Dict[str, List[str]]:
        """maicraft 逐工具名单（绑定处代码分类，注解不可信）。

        fail-closed：全部默认仅 minecraft 可见（执行类 plan/execute/task 等）；
        读工具（server 原始名以 perceive 结尾）放开给主播直读——"主播随时
        直读游戏状态"的产品需求，sync 直返、不进游戏 LLM 循环。
        """
        visible: Dict[str, List[str]] = {}
        for spec in specs:
            visible[spec.full_name] = ["streamer", "minecraft"] if spec.name.endswith("perceive") else ["minecraft"]
        return visible

    async def _on_stop(self) -> None:
        """停止钩子：取消命令 worker 与 handoff 监视（任务执行随 worker 取消而中断）。"""
        self._running = False
        self._wake_event.set()
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
        "minecraft_get_work_log": ["streamer"],
    }

    def _register_tools(self) -> None:
        """注册 Agent 专属工具到 ToolRegistry（复用 __init__ 创建的执行器实例）。"""
        if self._tool_registry is None:
            return
        self.register_tool_provider(
            self._tool_provider, registry=self._tool_registry, visible_to=dict(self._LOCAL_VISIBLE_TO)
        )
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
    # 命令驱动 ReAct（命令 → 消息队列 → 任务内有界循环 → 回空闲）
    # ==================================================================

    async def send_prompt(self, content: str) -> None:
        """系统/测试注入指令（非委派来源，任务号空串）：入队 + 唤醒。

        原 minecraft_send_prompt 工具的内部职能；工具已退役，跨 Agent
        派活走 framework_delegate → receive_delegation。
        """
        self._message_queue.append(MinecraftInstruction("", content))
        self._wake_event.set()
        self._logger.info(f"MinecraftAgent 收到指令注入：{content[:60]}")

    def receive_delegation(self, *, instruction: str, task_id: str) -> None:
        """接收委派入口（framework_delegate 调用）：指令入队（带任务号）+ 唤醒。

        指令不可拒绝；队列项带任务号供任务批次把状态写回任务记录表
        （开始 → running；交付/升级 → 终态）。
        """
        self._message_queue.append(MinecraftInstruction(task_id, instruction))
        self._wake_event.set()
        self._logger.info(f"MinecraftAgent 收到委派（task_id={task_id}）：{instruction[:60]}")
        return None  # 已接收

    async def _worker(self) -> None:
        """命令工作协程：等待命令信号 → 执行目标任务 → 回到等待（空闲零消耗）。"""
        while self._running:
            await self._wake_event.wait()
            self._wake_event.clear()
            if not (self._running and self._message_queue):
                continue
            # 达到预算或上报困难后只等玩家的新指令；身体事件和任务通知不能自动续一份推理预算。
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
        """执行单个任务批：ReAct 有界循环，直到批次终止语义命中。

        循环每步：
        1. flush 命令/系统注入消息 → 追加 user 消息
        2. 规整对话历史（旧观察 → 占位符）
        3. LLM 推理（generate + 工具列表）→ tool_calls（可多个）
        4. 串行执行：统一经 ToolRegistry（观测/停用/熔断复用既有机制）
        5. 工具结果作为观察作为观察返回（OpenAI tool role + tool_call_id）
        批次终止语义（五条，全部系统可判定）：
        1. LLM 调 minecraft_report(kind=delivery) → 停止（工具内交付门禁校验）
        2. LLM 调 minecraft_report(kind=escalation) → 停止，静默等主播委派
        3. 自然终止，无 report、无未决 handoff 且待办完成 → 系统兜底交付
        4. 仅剩实际运行中的 handoff → 静默让出；待开工或待决策则提醒推进
        5. 步数超 max_steps 或提醒后仍不行动 → 挂起，系统通知不能重给预算
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
        """任务批主体：ReAct 有界循环，直到批次终止语义命中。"""
        self._task_reported = False
        self._wait_requested = False
        system_prompt = self._system_prompt()
        # 工具列表 = 注册表按可见名单计算（for_agent，每任务重新拉取）——
        # minecraft 名单内含本地件 todo/notebook/report 与 maicraft_*，共享工具
        # 按各自名单照常出现；报告缺陷（report 不在旧手工列表）随统一来源消除
        if self._tool_registry is None:
            self._logger.warning("任务执行无 tool_registry：工具列表为空，任务将失败")
            tool_defs: List[Dict[str, Any]] = []
        else:
            specs = self._tool_registry.list_tools(for_agent=self.name)
            tool_defs = [_spec_to_fn(s) for s in specs]

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        if not self._task_finished and self._task_instructions:
            # 后台任务完成后先恢复原目标、待办与当前阶段，再让模型解释这次通知。
            messages.append(
                {
                    "role": "user",
                    "content": "[继续原游戏任务]\n"
                    + json.dumps(self._current_task_context(), ensure_ascii=False, default=str),
                }
            )

        steps = 0
        mc_round = f"mc_{uuid.uuid4().hex[:12]}" if self._thinking_sink is not None else ""
        mc_seq_box = [0]
        action_reminded = False
        while self._running:
            # 步骤间挂起（平台 pause）
            await self._paused.wait()

            # --- 消息 flush（执行中追加的委派指令 / 任务通知在下一次推理前吸收）---
            while self._message_queue:
                queued = self._message_queue.popleft()
                _tid, _content = queued
                if isinstance(queued, MinecraftInstruction):
                    if self._task_finished:
                        self._task_instructions.clear()
                        self._task_progress.clear()
                        messages[:] = [{"role": "system", "content": system_prompt}]
                        self._mc_state.set_todos([])
                    self._task_instructions.append(_content)
                    self._task_finished = False
                    self._task_suspended = False
                    self._task_steps = 0
                    self._design_progress.reset()
                if _tid:
                    self._delegated_batch_ids.append(_tid)
                messages.append({"role": "user", "content": _content})
            # 本批委派任务进入进行中（agent 型单写者：执行 Agent 写）
            self._mark_delegated_running()
            if self._task_steps >= self.typed_config.max_steps:
                self._task_suspended = True
                await self.emit_attention_required(
                    f"任务超过 {self.typed_config.max_steps} 步上限，已挂起；需要新指令才能继续"
                )
                return
            steps += 1
            self._task_steps += 1

            # --- 身体事件增量读取（任务跑着的时候才知道自己正被谁打）---
            await self._drain_attention()

            # --- 对话历史规整（旧观察 → 占位符）---
            self._compact_observations(messages)

            # --- LLM 推理 ---
            on_delta = self._build_thinking_callback(mc_round, steps, mc_seq_box) if mc_round else None
            try:
                response = await self._llm.generate(
                    messages,
                    profile=MINECRAFT_PROFILE,
                    tools=tool_defs,
                    on_delta=on_delta,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单步失败转错误事件
                self._logger.warning(f"MinecraftAgent LLM 推理异常: {type(exc).__name__}: {exc}")
                await self.emit_error(f"LLM 推理异常: {type(exc).__name__}: {exc}")
                return
            if not response.success:
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

            # --- 自然终止（情形 3/4）：LLM 无 tool_calls ---
            if not tool_calls:
                pending = self._pending_task_count()
                actionable = self._actionable_task_ids()
                if actionable or not pending and self._unfinished_todos():
                    # 已有设计等开工、任务等决策或仍有未派发待办时，等通知不会推进；先给模型一次纠正机会。
                    if not action_reminded:
                        messages.append(
                            {
                                "role": "user",
                                "content": "[任务尚需行动] 仍有未完成待办或需要处理的后台任务："
                                + ", ".join(sorted(actionable))
                                + "。请调用工具推进下一阶段或处理决策；设计完成不等于已施工。确实无法推进时上报 escalation，不能只说完成或继续等待。",
                            }
                        )
                        action_reminded = True
                        continue
                    self._task_suspended = True
                    await self.emit_attention_required(
                        "模型未推进需要行动的任务；已保留原目标、待办和任务编号，等待继续指令"
                    )
                    return
                if pending > 0:
                    # 情形 4：有未决 handoff——静默让出回合，等 handoff 唤醒（零空耗）
                    self._logger.info(
                        f"任务批次自然终止（{steps} 步），{self._pending_task_count()} 个后台任务跟踪中，静默让出"
                    )
                elif not self._task_reported:
                    # 情形 3：无 report 无 handoff——系统兜底，主播必收到一次且仅一次交付
                    delivery = (response.content or "").strip()
                    await self._emit_report("delivery", delivery[:_MAX_DELIVERY_TEXT] if delivery else "任务完成")
                    self._task_finished = True
                    self._finish_delegated("succeeded", summary=delivery[:80] or "任务完成")
                    self._logger.info(f"任务批次自然终止（{steps} 步），系统兜底交付")
                else:
                    self._logger.info(f"任务批次结束（{steps} 步，已上报）")
                return

            # --- 工具执行与观察作为观察返回 ---
            action_reminded = False
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
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(observation, ensure_ascii=False, default=str),
                    }
                )
                blocked_design = self._design_progress.observe(call.name, arguments, observation)
                if blocked_design:
                    # 已知拒绝尚未产生游戏动作，停止当前批次并保存原任务，避免继续消耗推理去重放同一错误。
                    self._task_suspended = True
                    self._logger.warning(blocked_design)
                    await self.emit_attention_required(blocked_design)
                    return

            # 情形 1/2：LLM 已 report——本轮工具执行完后停止（delivery/escalation 语义）
            if self._task_reported:
                self._logger.info(f"LLM 已上报（delivery/escalation），任务批次结束（{steps} 步）")
                return

            if self._wait_requested and not self._message_queue:
                # 工具结果已完整回填；等待期间不再调用模型，真实通知保留原目标并唤醒下一批。
                return
            self._wait_requested = False

    def _request_wait(self) -> Dict[str, Any]:
        """只允许对已有后台依赖让出执行，待开工和待决策不能靠等待推进。"""
        if self._actionable_task_ids():
            return {"ok": False, "error": "仍有待开工或待决策任务，请先推进或说明具体阻塞"}
        if self._pending_task_count() == 0:
            return {"ok": False, "error": "没有已登记的后台任务；请继续执行待办或上报阻塞"}
        if self._message_queue:
            return {"ok": True, "waiting": False, "reason": "已有新消息，请处理最新事实"}
        self._wait_requested = True
        return {"ok": True, "waiting": True, "monitor": "host", "resume_on": "task_event_or_instruction"}

    def _current_task_context(self) -> Dict[str, Any]:
        """恢复原任务时附上玩家原文与工作阶段；只在批次起点恢复，不改工具观察的压缩策略。"""
        progress = dict(self._task_progress)
        ledger = getattr(self._task_tracker, "ledger", None)
        if ledger is not None:
            # accepted -> running 通常没有唤醒通知，仍须从现有账本带回已经受理的任务编号。
            for task_id in ledger.active_task_ids():
                record = ledger.get(task_id)
                if record is not None and record.initiator == self.name:
                    progress[task_id] = {**progress.get(task_id, {}), "task_id": task_id, "status": record.status}
        if self._builder is not None:
            for task_id in self._builder.pending_ids():
                progress.setdefault(task_id, {"task_id": task_id, "status": "pending"})
        return {
            "original_instructions": list(self._task_instructions),
            "todo": self._mc_state.todo_doc()["todos"],
            "notebook": self._mc_state.notebook,
            "background_tasks": list(progress.values()),
            "reasoning_steps_used": self._task_steps,
        }

    def _unfinished_todos(self) -> bool:
        """施工、备料或核验仍有待办时，交付必须继续等待这些事项完成。"""
        return any(todo.status != "done" for todo in self._mc_state.todos)

    def _compact_observations(self, messages: List[Dict[str, Any]]) -> None:
        """规整对话历史：保留**最后** N 条工具结果，更早的替换为占位符。

        只替换 tool role 消息（观察），user/assistant 保留——上下文长度受控，
        关键信息由 notebook 承载（提示词引导）。
        """
        total = sum(1 for m in messages if m.get("role") == "tool")
        excess = total - _OBSERVATION_KEEP
        if excess <= 0:
            return
        seen = 0
        for i, msg in enumerate(messages):
            if msg.get("role") != "tool":
                continue
            seen += 1
            if seen <= excess:
                messages[i] = {
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": "[观察已压缩]",
                }

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
            return result.structured_content if isinstance(result.structured_content, dict) else {"ok": True}
        if isinstance(result.structured_content, dict) and result.structured_content:
            # 错误通道保留 Mod 的坐标、规则和结果不确定性；工具失败仍由外层 ok=false 明确标记。
            observation = {**result.structured_content, "ok": False, "tool": name}
            observation.setdefault("error", result.error_message or "工具执行失败")
            return observation
        return {"ok": False, "error": result.error_message or "工具执行失败", "tool": name}

    def _build_thinking_callback(self, round_id: str, step: int, seq_box: List[int]) -> Any:
        """构造 LLM 层增量回调（duck-typed sink），只转发 reasoning 增量。

        自足实现：不在此 import streamer 包的内部件 ThinkingStreamContext——
        跨 Agent import 违反边界（Protocol 鸭子匹配）。seq_box
        是 list 包装以实现闭包内计数自增（list[0]=... 不需 nonlocal）。
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
        if not self._task_finished:
            # 任务事件记成工作阶段，下一次醒来仍知道哪个设计或施工任务走到了哪里。
            self._task_progress[payload.task_id] = {
                "task_id": payload.task_id,
                "status": payload.status,
                "summary": payload.summary,
            }
            while len(self._task_progress) > 64:
                self._task_progress.pop(next(iter(self._task_progress)))
        if self._builder is not None:
            self._builder.absorb(payload)
        if payload.executor == "minecraft_builder":
            # 设计任务号只在本地查询；施工仍需父 Agent 显式发起，不能当作已经建好。
            if self._running:
                self._inject_wakeup_message(
                    f"[系统] 建造设计 {payload.task_id}：{payload.status}，{payload.summary}。"
                    "用 minecraft_builder_task 查询结果；要求建好时再用 action=execute 发起施工。"
                    "设计完成不代表建筑完成；失败时修订要求、明确取消或上报困难。"
                )
            return
        snapshot_text = ""
        if getattr(payload, "snapshot", None):
            snapshot_text = "\n任务快照：" + json.dumps(payload.snapshot, ensure_ascii=False, default=str)
        hint = "（waiting_for_decision 用任务查询工具 answer 应答；终态请决定后续并按需上报主播）"
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
        try:
            asyncio.get_running_loop().create_task(self._drain_attention())
        except RuntimeError:
            # 没有事件循环（同步上下文）：通知只是提示，丢弃不补
            self._logger.warning("注意流通知到达时无事件循环，本次提示丢弃")

    async def _drain_attention(self) -> None:
        """按游标增量读一页注意流，按事件归属分流。

        注意流同时承载两类事件，本方法就是分流点：

        - **任务事件**（带 ``task_id``，Mod 侧标 ``priority="task"``）→ 写任务记录表
          （``_absorb_task_event``）：任务归本 Agent 管，"干完了"这件事由这里落账，
          不依赖轮询查询。
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
        except Exception as exc:  # noqa: BLE001 - 读取失败只降级，不打断任务
            self._logger.warning(f"身体事件读取失败（本轮按未读到处理）: {exc}")
            return
        if not isinstance(page, dict):
            return

        resync = bool(page.get("resync_required") or page.get("history_lost") or page.get("stream_reset"))
        stream_id = page.get("stream_id")
        if isinstance(stream_id, str) and stream_id:
            self._attention_stream_id = stream_id
        cursor = page.get("cursor")
        if isinstance(cursor, int):
            self._attention_cursor = cursor

        events = page.get("events")
        events = [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []
        if resync or not self._attention_primed:
            self._attention_primed = True
            self._logger.info(
                f"身体事件游标{'重新同步' if resync else '首次建立'}："
                f"stream={self._attention_stream_id}, cursor={self._attention_cursor}, "
                f"本页 {len(events)} 条不注入"
            )
            return

        injected = 0
        for event in events:
            if event.get("task_id"):
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
        status = _MAICRAFT_TASK_STATUS_MAP.get(event_type, event_type)
        if not task_id or status not in _TASK_EVENT_STATUSES:
            return
        tracker = self._task_tracker
        ledger = getattr(tracker, "ledger", None) if tracker is not None else None
        if ledger is None or ledger.get(task_id) is None:
            return
        written = ledger.update(
            task_id,
            status,
            # 完成与决策事件已有真实结果，不能只留下事件类型再让父 Agent 猜下一步。
            snapshot={"event_type": event_type, "task_id": task_id, "data": event.get("data") or {}},
            summary=self._task_event_summary(event_type, event),
        )
        if written is not None:
            self._logger.info(f"任务事件落账（注意流）: task_id={task_id} status={status} type={event_type}")

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
        # 委派任务终态：交付 = 成功；升级 = 失败（需发起方介入）
        self._finish_delegated("succeeded" if kind == "delivery" else "failed", summary=f"{kind}: {content[:80]}")
        return None

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
            "请遵守：优先推进 todo；把每项任务推进到 done；"
            "全部完成后用 minecraft_report(kind=delivery) 交付总结再结束；"
            "确实无法自行解决时用 minecraft_report(kind=escalation) 上报后停止。"
            "工具调用：一次可调多个工具（它们会依次执行）；执行串行但你可一次发出多个请求。"
        )

    def _with_gameplay_prompt(self, prompt: str) -> str:
        """所有环境都保留访问与阶段边界；装配建筑设计入口时再附加委派方式。"""
        prompt += _GAMEPLAY_RULES
        if self._builder is None:
            return prompt
        return prompt + (
            "\n房屋与外观结构设计交给 minecraft_builder_request：传自然语言 requirements 和已知现场 context，"
            "不要自己生成完整建筑 JSON。intent=build 要求建好，intent=design 只要设计。"
            "它立即返回任务号，不要轮询等待；完成事件会通知你。"
            "用 minecraft_builder_task 查询、修改、取消设计；设计通过后用 action=execute 按引用施工，"
            "再跟进 Mod 施工任务，核实完成后才能交付。"
        )

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
