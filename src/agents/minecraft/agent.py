"""MinecraftAgent —— Minecraft 世界的 AI 玩家（事件驱动 ReAct Agent）

核心意象：一个用 MCP 工具玩 Minecraft 的普通 ReAct Agent。
- 主播 Agent 是它的用户：minecraft_send_prompt 发提示词、minecraft_get_state 看进度、
  minecraft_report 收上报
- 命令驱动（类 Code Agent）：空闲零消耗；send_prompt 唤醒任务，任务内有界
  ReAct 循环（LLM 推理 → 工具调用串行执行 → 观察喂回），批次终止语义见
  ``_run_task``——无存在性心跳、无时间循环
- 系统提示词 + 工具面 = 全部"编程"，不发明任何特殊协议
- execute 受理异步唯一特判：maicraft_execute 返回受理回执（task_id），真实执行
  由游戏 tick 后台驱动（分钟级）——系统登记 handoff 跟踪，经资源订阅通知 +
  周期兜底核实任务快照，状态真迁移才注入消息唤醒 LLM（通知是提示可丢，
  task get 是事实源）；等待期 LLM 自由行动或让出回合，零空耗
- agent 零 maicraft 接口知识：工具面经 registry 动态发现（list_tools(provider="maicraft)")，
  任务查询工具按原始名后缀匹配发现（注册名前缀形态不定）

继承 ``BaseAgent``（协议六面全部实现），构造注入依赖。
局部工具（todo/notebook/get_state/send_prompt/report，注册名 minecraft_*）声明 →
注册进 ToolRegistry。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Deque, Dict, Iterable, List, Literal, Optional

from src.agents.minecraft.state import MinecraftAgentState
from src.agents.minecraft.tools import (
    MinecraftToolProvider,
    build_get_state_spec,
    build_notebook_spec,
    build_report_spec,
    build_send_prompt_spec,
    build_todo_spec,
)
from src.modules.agents.base import AgentState, BaseAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.llm.manager import normalize_tool_calls_for_protocol
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry

from .config import MinecraftConfig

__all__ = ["MinecraftAgent"]

# 局部工具名（注册名 = minecraft_<本名>；Agent 循环内直接调 provider 处理）
_LOCAL_TOOL_NAMES = {"todo", "notebook", "send_prompt", "get_state", "report"}

# 旧观察规整：最近 N 条工具结果保留原文，更早的替换为占位符（防上下文膨胀）
_OBSERVATION_KEEP = 10

# 交付/上报文本截断（事件 payload 与兜底交付共用）
_MAX_DELIVERY_TEXT = 800

# 任务快照中"继续后台跑"的状态（MaiCraft publicState 小写形态）：仅这些不算迁移
_PENDING_TASK_STATES = frozenset({"pending", "running"})

# 任务终态：终态上报注入后 handoff 移除（跟踪结束）
_TERMINAL_TASK_STATES = frozenset({"success", "failed", "timeout", "cancelled"})

# MaiCraft attention 资源 URI（任务生命周期事件流；每任务必有 started/completed）
_ATTENTION_URI = "maicraft://attention"


@dataclass(slots=True)
class _Handoff:
    """跟踪中的后台任务（execute 受理回执登记；终态注入后移除）。

    Attributes:
        task_id: MaiCraft 侧任务 ID
        last_state: 最近核实到的状态（"" = 尚未核实过）
        deadline_ms: wait_timeout 截止（epoch 毫秒）；到点注入告警后顺延一个周期
    """

    task_id: str
    last_state: str = ""
    deadline_ms: int = 0


def _spec_to_fn(spec: ToolSpec) -> Dict[str, Any]:
    """ToolSpec → OpenAI 风格 function def。"""
    entry: Dict[str, Any] = {"name": spec.name, "description": spec.description}
    if spec.parameters_schema is not None:
        entry["parameters"] = spec.parameters_schema
    return entry


def _rename_registered(spec: ToolSpec) -> ToolSpec:
    """生成注册名视角的 spec（name = minecraft_<工具名>），LLM 工具面用。

    局部工具声明名是裸名（todo/notebook），注册进 Registry 后对外才是
    minecraft_todo；LLM 看到的必须是注册名（与主播侧调用契约一致）。
    """
    return replace(spec, name=f"{spec.provider}_{spec.name}")


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
        llm_profile: str = "minecraft",
        prompt_manager: Optional[Any] = None,
        event_bus: Optional[EventBus] = None,
        tool_registry: Optional[ToolRegistry] = None,
        live_session_id: str = "",
        thinking_sink: Optional[Any] = None,
    ) -> None:
        """初始化 Minecraft Agent。

        Args:
            config: MinecraftConfig 实例
            llm_manager: 可选 LLMManager（ReAct 循环用；无则任务失败 fast-fail）
            llm_profile: 决策调用的 LLM profile 名（对应配置 [llm_profiles.<name>] 段键，默认 'minecraft'）
            prompt_manager: 可选 PromptManager（渲染系统提示词）
            event_bus: 可选 EventBus（emit game.* 事件）
            tool_registry: 可选 ToolRegistry（注册 Agent 专属工具 + 动态发现 MCP 工具）
            live_session_id: 场次 ID（写入 game.* 事件 payload）
            thinking_sink: 可选思考流旁路出口（鸭子类型：任何带
                ``on_thinking_delta(round_id, phase, step, seq, text_delta)`` 方法的对象）。
                ``None`` 时思考流整体短路，决策循环行为与无旁路完全一致。
        """
        super().__init__(event_bus=event_bus)
        self.typed_config = config
        self._llm = llm_manager
        self._llm_profile = llm_profile
        self._prompt = prompt_manager
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._live_session_id = live_session_id or "minecraft_session"
        self._thinking_sink = thinking_sink

        # Agent 内部状态（内存，不持久化）
        self._mc_state: MinecraftAgentState = MinecraftAgentState()
        # 局部工具执行器：LLM 循环直接调（不依赖 registry；有 registry 时同一实例注册）
        self._tool_provider: MinecraftToolProvider = MinecraftToolProvider(
            state=self._mc_state,
            send_prompt_callback=self.send_prompt,
            report_callback=self._handle_report,
        )

        # 命令驱动运行骨架：worker 等命令信号，任务内有界 ReAct 循环
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._wake_event: asyncio.Event = asyncio.Event()
        # 主播命令消息队列（send_prompt 投递；任务执行中也可追加——LLM 下一次推理吸收）
        self._message_queue: Deque[str] = deque()

        # handoff 跟踪（execute 受理后台任务）：注册表 + 唤醒信号 + 监视协程
        self._handoffs: Dict[str, _Handoff] = {}
        self._handoff_signal: asyncio.Event = asyncio.Event()
        self._watcher_task: Optional[asyncio.Task[None]] = None
        self._next_poll_ms: int = 0  # 下次周期兜底核实时刻（0 = 登记即首查）
        # Agent 私有 MCP（_on_start 装配成功时持有）：资源订阅接线用
        self._mcp_client: Optional[Any] = None
        self._unsubscribe_attention: Optional[Any] = None  # attention 资源退订句柄

        # 本批次 LLM 是否已 report（delivery/escalation 终止语义判定）
        self._task_reported = False

        # 平台暂停支持（AgentControl pause/resume 经 _on_pause/_on_resume 进入）
        self._paused = asyncio.Event()
        self._paused.set()

        self._running = False

        self._logger = get_logger("MinecraftAgent")
        self._logger.info(
            f"MinecraftAgent 已构造 (max_steps={config.max_steps}, "
            f"llm={'已注入' if llm_manager else '无'}@{llm_profile})"
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
        self._worker_task = asyncio.create_task(self._worker())
        self._watcher_task = asyncio.create_task(self._handoff_watcher())
        self._logger.info("MinecraftAgent 已启动（命令驱动：等待 minecraft_send_prompt）")

    async def _bind_agent_owned_mcp(self) -> None:
        """装配 Agent 私有 MCP server（[agents.minecraft.mcp]）。

        启用条件：registry 非空且 ``typed_config.mcp.enabled`` 为 True。
        装配：以 ``owner_agent="minecraft"`` 注册到 ToolRegistry——默认不进入
        LLM 通用工具面，仅 MinecraftAgent 通过 ``list_tools(provider="maicraft")``
        域内查询可见。
        失败语义：整个装配 try/except 包裹，连接失败/装配异常仅 warning 不阻断
        Agent 启动——Agent 是命令驱动，MCP 不可用只降级（无 maicraft 工具可调）。
        关闭：依赖全局 ``close_mcp_providers``（registry._providers 遍历）——
        本 Agent 不在 _on_stop 单独关闭，保持与"通用 MCP 通道"一致的清理路径。
        装配成功时把 client 引用留给 handoff 订阅接线（``_mcp_client``）。
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
            prefix=None,  # 走默认 <server_name>_ 前缀
            provider=server_name,  # spec.provider = "maicraft"，与历史契约一致
        )
        try:
            count = await prov.setup()
        except Exception as exc:  # noqa: BLE001 - 装配异常仅降级
            self._logger.warning(f"Agent 私有 MCP（owner_agent=minecraft）装配异常: {type(exc).__name__}: {exc}")
            self._mcp_client = None
            try:
                await client.close()
            except Exception:  # noqa: BLE001 - 关闭失败不二次上抛
                pass
            return
        if count == 0:
            # 对齐 bind_mcp_tools 的隔离风格：连接失败或 server 无工具 → 不注册
            self._logger.warning("Agent 私有 MCP（owner_agent=minecraft）连接失败或 server 未暴露工具，未注册")
            self._mcp_client = None
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            new_count = self._tool_registry.register_provider(prov, owner_agent=self.name)
        except Exception as exc:  # noqa: BLE001 - 注册异常兜底
            self._logger.warning(f"Agent 私有 MCP（owner_agent=minecraft）注册失败: {type(exc).__name__}: {exc}")
            self._mcp_client = None
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass
            return
        self._logger.info(f"Agent 私有 MCP（owner_agent=minecraft）装配完成：新注册 {new_count}/{count} 个工具")

    async def _on_stop(self) -> None:
        """停止钩子：取消命令 worker 与 handoff 监视（任务执行随 worker 取消而中断）。"""
        self._running = False
        self._wake_event.set()
        self._handoff_signal.set()
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - 边界兜底
                self._logger.warning(f"命令 worker 退出异常: {exc}")
            self._worker_task = None
        if self._watcher_task is not None:
            self._watcher_task.cancel()
            try:
                await self._watcher_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - 边界兜底
                self._logger.warning(f"handoff 监视退出异常: {exc}")
            self._watcher_task = None
        self._handoffs.clear()
        self._unsubscribe_attention = None  # 连接随全局清理关闭，订阅自然消亡
        self._logger.info("MinecraftAgent 已停止")

    async def _on_pause(self) -> None:
        """暂停钩子：任务循环在步骤间挂起（不打断当前工具调用）。"""
        self._paused.clear()

    async def _on_resume(self) -> None:
        """恢复钩子：任务循环继续。"""
        self._paused.set()

    # ==================================================================
    # 工具提供（list_tools）
    # ==================================================================

    def list_tools(self) -> Iterable[ToolSpec]:
        """声明 Agent 专属工具（provider="minecraft"）。"""
        return [
            build_todo_spec(),
            build_notebook_spec(),
            build_get_state_spec(),
            build_send_prompt_spec(),
            build_report_spec(),
        ]

    def _register_tools(self) -> None:
        """注册 Agent 专属工具到 ToolRegistry（复用 __init__ 创建的执行器实例）。"""
        if self._tool_registry is None:
            return
        self._tool_registry.register_provider(self._tool_provider)
        self._logger.info(
            "MinecraftAgent 工具已注册："
            "minecraft_todo / minecraft_notebook / minecraft_get_state / minecraft_send_prompt / minecraft_report"
        )

    # ==================================================================
    # 命令驱动 ReAct（命令 → 消息队列 → 任务内有界循环 → 回空闲）
    # ==================================================================

    async def send_prompt(self, content: str) -> None:
        """接收主播提示词（minecraft_send_prompt 命令入口）。

        提示词不可拒绝；原文入队 + 唤醒 worker（若执行中，LLM 下一次推理吸收）。
        系统不代写 todo——目标分解是 LLM 的行为。
        """
        self._message_queue.append(content)
        self._wake_event.set()
        self._logger.info(f"MinecraftAgent 收到 send_prompt: {content}")

    async def _worker(self) -> None:
        """命令工作协程：等待命令信号 → 执行目标任务 → 回到等待（空闲零消耗）。"""
        while self._running:
            await self._wake_event.wait()
            self._wake_event.clear()
            if not (self._running and self._message_queue):
                continue
            try:
                await self._run_task()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 任务失败不杀死 worker
                self._logger.error(f"任务执行异常: {exc}", exc_info=True)
                await self.emit_error(f"任务执行异常: {exc}")

    async def _run_task(self) -> None:
        """执行单个任务批：ReAct 有界循环，直到批次终止语义命中。

        循环每步：
        1. flush 命令/系统注入消息 → 追加 user 消息
        2. 规整对话历史（旧观察 → 占位符）
        3. LLM 推理（chat_messages + 工具面）→ tool_calls（可多个）
        4. 串行执行：局部工具直接落状态；MCP 工具经 registry 透传
        5. 工具结果作为观察喂回（OpenAI tool role + tool_call_id）
        批次终止语义（五条，全部系统可判定）：
        1. LLM 调 minecraft_report(kind=delivery) → 停止（工具内交付门禁校验）
        2. LLM 调 minecraft_report(kind=escalation) → 停止，静默等主播 send_prompt
        3. 自然终止，无 report、无未决 handoff → 系统兜底把终止文本包装为一次 delivery
        4. 自然终止，有未决 handoff → 静默让出，等 handoff 唤醒
        5. 步数超 max_steps → game.attention_required 挂起（不变）
        """
        if self._llm is None:
            await self.emit_error("无法执行任务：LLM 未注入")
            return

        self._task_reported = False
        system_prompt = self._system_prompt()
        local_specs = [build_todo_spec(), build_notebook_spec()]
        # 动态工具面 = 局部文档工具 + registry 发现的 MCP 工具（每任务重新拉取）
        mcp_specs: List[ToolSpec] = []
        if self._tool_registry is not None:
            mcp_specs = self._tool_registry.list_tools(provider="maicraft")
        # LLM 看到的局部工具名 = 注册名（minecraft_<工具名>，与 registry 对外契约一致）；
        # MCP 工具名已在注册时带 maicraft_ 前缀
        tool_defs: List[Dict[str, Any]] = [_spec_to_fn(_rename_registered(s)) for s in local_specs] + [
            _spec_to_fn(s) for s in mcp_specs
        ]

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

        steps = 0
        mc_round = f"mc_{uuid.uuid4().hex[:12]}" if self._thinking_sink is not None else ""
        mc_seq_box = [0]
        while self._running and steps < self.typed_config.max_steps:
            steps += 1

            # 步骤间挂起（平台 pause）
            await self._paused.wait()

            # --- 消息 flush（执行中追加的 send_prompt / handoff 注入在下一次推理前吸收）---
            while self._message_queue:
                messages.append({"role": "user", "content": self._message_queue.popleft()})

            # --- 对话历史规整（旧观察 → 占位符）---
            self._compact_observations(messages)

            # --- LLM 推理 ---
            on_delta = self._build_thinking_callback(mc_round, steps, mc_seq_box) if mc_round else None
            try:
                response = await self._llm.chat_messages(
                    messages=messages,
                    client_type=self._llm_profile,
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

            # 组装 assistant 消息（完整 tool_calls 形态，供后续关联喂回）
            tool_calls = response.tool_calls or []
            assistant_content = response.content
            if assistant_content is not None and not isinstance(assistant_content, str):
                # OpenAI 协议要求 content 为 string/null，部分 client 返回结构化内容
                assistant_content = json.dumps(assistant_content, ensure_ascii=False, default=str)
            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": assistant_content}
            if tool_calls:
                assistant_msg["tool_calls"] = normalize_tool_calls_for_protocol(tool_calls)
            messages.append(assistant_msg)

            # --- 自然终止（情形 3/4）：LLM 无 tool_calls ---
            if not tool_calls:
                if self._handoffs:
                    # 情形 4：有未决 handoff——静默让出回合，等 handoff 唤醒（零空耗）
                    self._logger.info(
                        f"任务批次自然终止（{steps} 步），{len(self._handoffs)} 个后台任务跟踪中，静默让出"
                    )
                elif not self._task_reported:
                    # 情形 3：无 report 无 handoff——系统兜底，主播必收到一次且仅一次交付
                    delivery = (response.content or "").strip()
                    await self._emit_report("delivery", delivery[:_MAX_DELIVERY_TEXT] if delivery else "任务完成")
                    self._logger.info(f"任务批次自然终止（{steps} 步），系统兜底交付")
                else:
                    self._logger.info(f"任务批次结束（{steps} 步，已上报）")
                return

            # --- 工具执行与观察喂回 ---
            for call in tool_calls:
                await self._paused.wait()
                func = call.get("function") or {}
                name = func.get("name", "")
                arguments = func.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}

                observation = await self._execute_tool(name, arguments, round_id=mc_round)
                await self._register_handoff_from_receipt(observation)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": json.dumps(observation, ensure_ascii=False, default=str),
                    }
                )

            # 情形 1/2：LLM 已 report——本轮工具执行完后停止（delivery/escalation 语义）
            if self._task_reported:
                self._logger.info(f"LLM 已上报（delivery/escalation），任务批次结束（{steps} 步）")
                return

        # 情形 5：超步挂起
        if self._running:
            await self.emit_attention_required(f"任务超过 {self.typed_config.max_steps} 步上限，已挂起")

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
        """串行执行单个工具调用：局部工具直调 provider；其余经 registry 透传。

        ``round_id`` 任务内 ReAct 轮 ID（思考流旁路同轮）；经 ToolInvocation
        透传到 tool.result 事件，供 WebUI 工具卡关联思考轮。无任务上下文
        的调用（如 handoff watcher）保持空串。
        """
        bare_name = name
        if "_" in name:
            prov, _, tail = name.partition("_")
            if prov == "minecraft" and tail in _LOCAL_TOOL_NAMES:
                bare_name = tail
        if bare_name in _LOCAL_TOOL_NAMES and self._tool_provider is not None:
            result = await self._tool_provider.invoke(
                ToolInvocation(tool_name=bare_name, arguments=arguments, source="minecraft-react", round_id=round_id)
            )
            if result.success:
                return result.structured_content if isinstance(result.structured_content, dict) else {"ok": True}
            return {"ok": False, "error": result.error_message or "工具执行失败"}

        if self._tool_registry is not None:
            result = await self._tool_registry.invoke(
                ToolInvocation(tool_name=name, arguments=arguments, source="minecraft-react", round_id=round_id)
            )
            if result.success:
                return result.structured_content if isinstance(result.structured_content, dict) else {"ok": True}
            return {"ok": False, "error": result.error_message or "工具执行失败", "tool": name}
        return {"ok": False, "error": "工具执行失败：tool_registry 未注入", "tool": name}

    def _build_thinking_callback(self, round_id: str, step: int, seq_box: List[int]) -> Any:
        """构造 LLM 层增量回调（duck-typed sink），只转发 reasoning 增量。

        自足实现：不在此 import streamer 包的内脏 ThinkingStreamContext——
        跨 Agent import 违反边界（ADR-008：Protocol 鸭子匹配）。seq_box
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
    # handoff 跟踪（execute 受理 → 订阅/兜底核实 → 真迁移注入唤醒）
    # ==================================================================

    @staticmethod
    def _resolve_task_query_tool(mcp_specs: List[ToolSpec]) -> str:
        """发现任务查询工具（maicraft_task 的注册名）。

        MCP 注册名带 server 前缀且形态不定（双前缀/自定义前缀皆可能），
        按 MaiCraft 原始名后缀匹配发现；找不到返回空串——handoff 核实
        降级关闭（只等订阅通知，无从核实事实）。
        """
        for spec in mcp_specs:
            if spec.name.endswith("maicraft_task"):
                return spec.name
        return ""

    async def _register_handoff_from_receipt(self, observation: Dict[str, Any]) -> None:
        """execute 受理回执（``accepted=true`` + ``task_id``）→ 登记 handoff。

        受理回执照常喂回 LLM（回合继续），跟踪交给订阅/周期兜底；
        重复 task_id 幂等（MaiCraft 对同一任务重复 execute 不产生新跟踪）。
        """
        if not isinstance(observation, dict) or observation.get("accepted") is not True:
            return
        raw_task_id = observation.get("task_id")
        if not raw_task_id:
            return
        task_id = str(raw_task_id)
        if task_id in self._handoffs:
            return
        self._handoffs[task_id] = _Handoff(
            task_id=task_id,
            deadline_ms=now_ms() + self.typed_config.wait_timeout_ms,
        )
        self._logger.info(f"execute 受理回执（task_id={task_id}），登记 handoff 跟踪")
        await self._ensure_attention_subscribed()
        self._next_poll_ms = 0  # 登记即触发一次首查（watcher 醒来核实）
        self._handoff_signal.set()

    async def _ensure_attention_subscribed(self) -> None:
        """有跟踪任务时确保 attention 资源已订阅（尽力而为，失败降级周期兜底）。"""
        if self._unsubscribe_attention is not None or self._mcp_client is None:
            return
        try:
            self._unsubscribe_attention = await self._mcp_client.subscribe_resource(
                _ATTENTION_URI, self._on_attention_notified
            )
        except Exception as exc:  # noqa: BLE001 - 订阅失败仅降级
            self._logger.warning(f"attention 资源订阅失败（降级周期兜底）: {type(exc).__name__}: {exc}")

    def _on_attention_notified(self, uri: str) -> None:
        """订阅通知回调（举旗级，跑在 fastmcp 消息循环内）。

        通知是提示（advisory：单槽合并、可丢）——只举旗，核实留给监视协程。
        """
        self._handoff_signal.set()

    async def _handoff_watcher(self) -> None:
        """handoff 监视协程：订阅通知/周期兜底到点 → task get 核实 → 真迁移注入唤醒。

        无跟踪任务时挂起等旗（空闲零消耗）；等待时限取"下次兜底轮询"与
        "最近 wait_timeout 截止"的较小者——通知丢失/断连时周期兜底覆盖，
        长期无进展时 wait_timeout 告警（不杀任务，deadline 顺延再等）。
        """
        while self._running:
            await self._paused.wait()
            if not self._handoffs:
                self._handoff_signal.clear()
                await self._handoff_signal.wait()
                continue
            now = now_ms()
            deadline = min(h.deadline_ms for h in self._handoffs.values())
            wait_s = max(0.0, (min(self._next_poll_ms, deadline) - now) / 1000)
            try:
                await asyncio.wait_for(self._handoff_signal.wait(), timeout=wait_s)
            except asyncio.TimeoutError:
                pass
            self._handoff_signal.clear()
            if not self._running:
                return
            try:
                await self._verify_handoffs()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单轮核实失败不杀监视协程
                self._logger.error(f"handoff 核实异常: {exc}", exc_info=True)
            # 推进周期兜底节拍：下一次无通知时的核实时刻
            self._next_poll_ms = now_ms() + self.typed_config.poll_interval_ms

    async def _verify_handoffs(self) -> None:
        """核实全部跟踪中任务：状态真迁移注入唤醒；wait_timeout 到点注入告警。"""
        registry = self._tool_registry
        task_query_tool = ""
        if registry is not None:
            task_query_tool = self._resolve_task_query_tool(registry.list_tools(provider="maicraft"))
        now = now_ms()
        for task_id, handoff in list(self._handoffs.items()):
            snapshot: Dict[str, Any] = {}
            verified = False
            if registry is not None and task_query_tool:
                result = await registry.invoke(
                    ToolInvocation(
                        tool_name=task_query_tool,
                        arguments={"action": "get", "task_id": task_id},
                        source="minecraft-handoff",
                    )
                )
                if result.success and isinstance(result.structured_content, dict):
                    snapshot = result.structured_content
                    verified = True
                    state = str(snapshot.get("state", ""))
                    if state and state != handoff.last_state:
                        handoff.last_state = state
                        if state not in _PENDING_TASK_STATES:
                            # 真迁移（决策点/暂停/终态）→ 注入快照 + 唤醒 worker；
                            # 终态移除跟踪，决策点/暂停保留（LLM 应答后任务继续后台跑）
                            if state in _TERMINAL_TASK_STATES:
                                self._handoffs.pop(task_id, None)
                            await self._inject_handoff_wakeup(task_id, state, snapshot)
                            continue
            if now >= handoff.deadline_ms:
                # wait_timeout：长期无进展告警（不杀任务），deadline 顺延一个周期
                handoff.deadline_ms = now + self.typed_config.wait_timeout_ms
                detail = (
                    f"最近快照：{json.dumps(snapshot, ensure_ascii=False, default=str)}"
                    if verified
                    else "任务状态核实不可用（任务查询工具缺失或调用失败）"
                )
                await self._inject_wakeup_message(
                    f"[系统] 后台任务 {task_id} 超过 {self.typed_config.wait_timeout_ms}ms 无进展"
                    f"（wait_timeout），{detail}。请核查该任务（查询/取消/推进其他工作）。"
                )
                self._logger.warning(f"handoff wait_timeout（task_id={task_id}），已注入告警")
        await self._maybe_unsubscribe_attention()

    async def _inject_handoff_wakeup(self, task_id: str, state: str, snapshot: Dict[str, Any]) -> None:
        """状态真迁移注入：快照进消息队列 + 唤醒 worker（回合未结束当回合内吸收）。"""
        await self._inject_wakeup_message(
            f"[系统] 后台任务 {task_id} 状态迁移：state={state}\n"
            f"任务快照：{json.dumps(snapshot, ensure_ascii=False, default=str)}\n"
            f"（waiting_for_decision 用任务查询工具 answer 应答；终态请决定后续并按需上报主播）"
        )
        self._logger.info(f"handoff 状态迁移注入唤醒（task_id={task_id}, state={state}）")

    async def _inject_wakeup_message(self, content: str) -> None:
        """系统消息入队 + 唤醒 worker：复用 send_prompt 的同一投递通道。"""
        self._message_queue.append(content)
        self._wake_event.set()

    async def _maybe_unsubscribe_attention(self) -> None:
        """无跟踪任务时退订 attention 资源（每任务订阅/退订的收尾半步）。"""
        if self._handoffs or self._unsubscribe_attention is None:
            return
        unsubscribe = self._unsubscribe_attention
        self._unsubscribe_attention = None
        try:
            await unsubscribe()
        except Exception as exc:  # noqa: BLE001 - 退订失败仅日志
            self._logger.warning(f"attention 资源退订失败: {type(exc).__name__}: {exc}")

    # ==================================================================
    # 上报通道（玩家→主播：delivery/escalation）
    # ==================================================================

    async def _handle_report(self, kind: str, content: str, scene: str) -> Optional[str]:
        """minecraft_report 回调：发射 game.report + 记入 recent_reports。

        Returns:
            拒绝原因（交付门禁：有未决 handoff 时拒绝 delivery——后台任务
            没收尾不能交付，错误观察喂回 LLM 自纠）；None = 受理。
        """
        if kind == "delivery" and self._handoffs:
            return (
                f"仍有 {len(self._handoffs)} 个后台任务未决（handoff 跟踪中），"
                "不能交付——先用任务查询工具处理它们，或改用 escalation 说明情况"
            )
        await self._emit_report(kind, content, scene=scene)
        self._task_reported = True
        return None

    def _system_prompt(self) -> str:
        """系统提示词：渲染 prompt_manager 模板（无则用内建兜底）。"""
        if self._prompt is not None:
            try:
                return self._prompt.render("amaidesu_minecraft_agent")
            except Exception as exc:  # noqa: BLE001 - 渲染失败降级内建
                self._logger.warning(f"MinecraftAgent 提示词渲染失败，降级内建: {type(exc).__name__}: {exc}")
        return (
            "你是 Minecraft 世界中的 AI 玩家。用工具玩 Minecraft："
            "minecraft_todo 管理目标与进度、minecraft_notebook 记录关键信息，"
            "其余工具（maicraft_*）是你在游戏内的操作能力。"
            "请遵守：优先推进 todo；把每项任务推进到 done；"
            "全部完成后用 minecraft_report(kind=delivery) 交付总结再结束；"
            "确实无法自行解决时用 minecraft_report(kind=escalation) 上报后停止。"
            "工具调用：一次可调多个工具（它们会依次执行）；执行串行但你可一次发出多个请求。"
        )

    # ==================================================================
    # 事件上报（三通道·事件；GamePayload(game="minecraft")）
    # ==================================================================

    async def _emit_game_event(
        self,
        event_type: Literal["report", "attention_required", "error"],
        message: str,
        *,
        scene: str = "",
        report_kind: Optional[Literal["delivery", "escalation"]] = None,
    ) -> None:
        """emit game.* 事件（统一 payload 构造）。"""
        if self._event_bus is None:
            return
        payload = GamePayload(
            live_session_id=self._live_session_id,
            game="minecraft",
            event_type=event_type,
            message=message,
            scene=scene,
            report_kind=report_kind,
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
