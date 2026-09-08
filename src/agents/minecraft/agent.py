"""MinecraftAgent —— Minecraft 世界的 AI 玩家（普通 ReAct Agent）

核心意象：一个用 MCP 工具玩 Minecraft 的普通 ReAct Agent。
- 主播 Agent 是它的用户：minecraft_assign 派任务、minecraft_get_state 看进度、
  game.* 事件汇报
- 命令驱动（类 Code Agent）：空闲零消耗；assign 消息唤醒任务，任务内有界
  ReAct 循环（LLM 推理 → 工具调用串行执行 → 观察喂回），自然终止/超步/停止
  后回空闲——无存在性心跳、无时间循环
- 系统提示词 + 工具面 = 全部"编程"，不发明任何特殊协议
- MCP 串行不特殊处理：LLM 自己明白调用 maicraft_task 查询/应答，系统零干预
- agent 零 maicraft 知识：工具面经 registry 动态发现（list_tools(provider="maicraft")），
  maicraft 接口变更不改 agent

继承 ``BaseAgent``（协议六面全部实现），构造注入依赖。
局部工具（todo/notebook/get_state/assign，注册名 minecraft_*）声明 → 注册进
ToolRegistry。
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import replace
from typing import Any, Deque, Dict, Iterable, List, Literal, Optional

from src.agents.minecraft.state import MinecraftAgentState
from src.agents.minecraft.tools import (
    MinecraftToolProvider,
    build_assign_spec,
    build_get_state_spec,
    build_notebook_spec,
    build_todo_spec,
)
from src.modules.agents.base import AgentState, BaseAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.llm.manager import normalize_tool_calls_for_protocol
from src.modules.logging import get_logger
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry

from .config import MinecraftConfig

__all__ = ["MinecraftAgent"]

# 局部工具名（注册名 = minecraft_<本名>；Agent 循环内直接调 provider 处理）
_LOCAL_TOOL_NAMES = {"todo", "notebook", "assign", "get_state"}

# 旧观察规整：最近 N 条工具结果保留原文，更早的替换为占位符（防上下文膨胀）
_OBSERVATION_KEEP = 10

# 自然终止后允许 LLM 附携带一段交付文本（里程碑事件载体）
_MAX_DELIVERY_TEXT = 800


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
        CoreEvents.GAME_MILESTONE,
        CoreEvents.GAME_ATTENTION_REQUIRED,
        CoreEvents.GAME_ERROR,
    )

    def __init__(
        self,
        config: MinecraftConfig,
        *,
        llm_manager: Optional[Any] = None,
        llm_profile: str = "llm",
        prompt_manager: Optional[Any] = None,
        event_bus: Optional[EventBus] = None,
        tool_registry: Optional[ToolRegistry] = None,
        live_session_id: str = "",
    ) -> None:
        """初始化 Minecraft Agent。

        Args:
            config: MinecraftConfig 实例
            llm_manager: 可选 LLMManager（ReAct 循环用；无则任务失败 fast-fail）
            llm_profile: 决策调用的 LLM profile 名（对应配置 [llm] 段键）
            prompt_manager: 可选 PromptManager（渲染系统提示词）
            event_bus: 可选 EventBus（emit game.* 事件）
            tool_registry: 可选 ToolRegistry（注册 Agent 专属工具 + 动态发现 MCP 工具）
            live_session_id: 场次 ID（写入 game.* 事件 payload）
        """
        super().__init__(event_bus=event_bus)
        self.typed_config = config
        self._llm = llm_manager
        self._llm_profile = llm_profile
        self._prompt = prompt_manager
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._live_session_id = live_session_id or "minecraft_session"

        # Agent 内部状态（内存，不持久化）
        self._mc_state: MinecraftAgentState = MinecraftAgentState()
        # 局部工具执行器：LLM 循环直接调（不依赖 registry；有 registry 时同一实例注册）
        self._tool_provider: MinecraftToolProvider = MinecraftToolProvider(
            state=self._mc_state, assign_callback=self.assign
        )

        # 命令驱动运行骨架：worker 等命令信号，任务内有界 ReAct 循环
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._wake_event: asyncio.Event = asyncio.Event()
        # 主播命令消息队列（assign 投递；任务执行中也可追加——LLM 下一次推理吸收）
        self._message_queue: Deque[str] = deque()

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
        """启动钩子：注册专属工具 + 装配 Agent 私有 MCP（启用时） + 启动命令 worker。"""
        if self._tool_registry is not None:
            self._register_tools()
            await self._bind_agent_owned_mcp()

        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        self._logger.info("MinecraftAgent 已启动（命令驱动：等待 minecraft_assign）")

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
            try:
                await client.close()
            except Exception:  # noqa: BLE001 - 关闭失败不二次上抛
                pass
            return
        if count == 0:
            # 对齐 bind_mcp_tools 的隔离风格：连接失败或 server 无工具 → 不注册
            self._logger.warning("Agent 私有 MCP（owner_agent=minecraft）连接失败或 server 未暴露工具，未注册")
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            new_count = self._tool_registry.register_provider(prov, owner_agent=self.name)
        except Exception as exc:  # noqa: BLE001 - 注册异常兜底
            self._logger.warning(f"Agent 私有 MCP（owner_agent=minecraft）注册失败: {type(exc).__name__}: {exc}")
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass
            return
        self._logger.info(f"Agent 私有 MCP（owner_agent=minecraft）装配完成：新注册 {new_count}/{count} 个工具")

    async def _on_stop(self) -> None:
        """停止钩子：取消命令 worker（任务执行随 worker 取消而中断）。"""
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
        return [build_todo_spec(), build_notebook_spec(), build_get_state_spec(), build_assign_spec()]

    def _register_tools(self) -> None:
        """注册 Agent 专属工具到 ToolRegistry（复用 __init__ 创建的执行器实例）。"""
        if self._tool_registry is None:
            return
        self._tool_registry.register_provider(self._tool_provider)
        self._logger.info(
            "MinecraftAgent 工具已注册：minecraft_todo / minecraft_notebook / minecraft_get_state / minecraft_assign"
        )

    # ==================================================================
    # 命令驱动 ReAct（命令 → 消息队列 → 任务内有界循环 → 回空闲）
    # ==================================================================

    async def assign(self, content: str) -> None:
        """接收主播命令（minecraft_assign 命令入口）。

        命令不可拒绝；原文入队 + 唤醒 worker（若执行中，LLM 下一次推理吸收）。
        系统不代写 todo——目标分解是 LLM 的行为。
        """
        self._message_queue.append(content)
        self._wake_event.set()
        self._logger.info(f"MinecraftAgent 收到 assign: {content}")

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
        """执行单个任务批：ReAct 有界循环，直到自然终止/超步/停止。

        循环每步：
        1. flush 命令消息 → 追加 user 消息
        2. 规整对话历史（旧观察 → 占位符）
        3. LLM 推理（chat_messages + 工具面）→ tool_calls（可多个）
        4. 串行执行：局部工具直接落状态；MCP 工具经 registry 透传
           （todo write 后 diff：新 done 项 → game.milestone 一次性）
        5. 工具结果作为观察喂回（OpenAI tool role + tool_call_id）
        终止：
        - LLM 无 tool_calls → 自然终止（最终文本 → milestone 交付汇报）
        - 步数超 max_steps → game.attention_required 挂起
        """
        if self._llm is None:
            await self.emit_error("无法执行任务：LLM 未注入")
            return

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
        done_set_before = self._mc_state.done_set()
        while self._running and steps < self.typed_config.max_steps:
            steps += 1

            # 步骤间挂起（平台 pause）
            await self._paused.wait()

            # --- 命令消息 flush（执行中追加的 assign 在下一次推理前吸收）---
            while self._message_queue:
                messages.append({"role": "user", "content": self._message_queue.popleft()})

            # --- 对话历史规整（旧观察 → 占位符）---
            self._compact_observations(messages)

            # --- LLM 推理 ---
            try:
                response = await self._llm.chat_messages(
                    messages=messages,
                    client_type=self._llm_profile,
                    tools=tool_defs,
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

            # 自然终止：LLM 无 tool_calls → 交付汇报
            if not tool_calls:
                delivery = (response.content or "").strip()
                if delivery:
                    await self.emit_milestone(delivery[:_MAX_DELIVERY_TEXT])
                else:
                    await self.emit_milestone("任务完成")
                self._logger.info(f"MinecraftAgent 任务自然终止（{steps} 步）")
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

                observation = await self._execute_tool(name, arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": json.dumps(observation, ensure_ascii=False, default=str),
                    }
                )

            # todo diff：本轮新 done 项 → milestone（一次）
            done_set_now = self._mc_state.done_set()
            newly_done = done_set_now - done_set_before
            if newly_done:
                for item in sorted(newly_done):
                    await self.emit_milestone(f"完成任务：{item}")
                done_set_before = done_set_now

        # 超步挂起
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

    async def _execute_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """串行执行单个工具调用：局部工具直调 provider；其余经 registry 透传。"""
        bare_name = name
        if "_" in name:
            prov, _, tail = name.partition("_")
            if prov == "minecraft" and tail in _LOCAL_TOOL_NAMES:
                bare_name = tail
        if bare_name in _LOCAL_TOOL_NAMES and self._tool_provider is not None:
            result = await self._tool_provider.invoke(
                ToolInvocation(tool_name=bare_name, arguments=arguments, source="minecraft-react")
            )
            if result.success:
                return result.structured_content if isinstance(result.structured_content, dict) else {"ok": True}
            return {"ok": False, "error": result.error_message or "工具执行失败"}

        if self._tool_registry is not None:
            result = await self._tool_registry.invoke(
                ToolInvocation(tool_name=name, arguments=arguments, source="minecraft-react")
            )
            if result.success:
                return result.structured_content if isinstance(result.structured_content, dict) else {"ok": True}
            return {"ok": False, "error": result.error_message or "工具执行失败", "tool": name}
        return {"ok": False, "error": "工具执行失败：tool_registry 未注入", "tool": name}

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
            "请遵守：优先推进 todo；把每项任务推进到 done；完成所有 todo 后输出简短总结。"
            "工具调用：一次可调多个工具（它们会依次执行）；执行串行但你可一次发出多个请求。"
        )

    # ==================================================================
    # 事件上报（三通道·事件；GamePayload(game="minecraft")）
    # ==================================================================

    async def _emit_game_event(
        self,
        event_type: Literal["milestone", "attention_required", "error"],
        message: str,
        *,
        scene: str = "",
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
        )
        if event_type == "milestone":
            # 里程碑事件同步进内存（minecraft_get_state 的 recent_milestones 数据源）
            self._mc_state.add_milestone(message)
            await self.emit_event(CoreEvents.GAME_MILESTONE, payload)
        elif event_type == "attention_required":
            await self.emit_event(CoreEvents.GAME_ATTENTION_REQUIRED, payload)
        else:
            await self.emit_event(CoreEvents.GAME_ERROR, payload)

    async def emit_milestone(self, message: str, *, scene: str = "") -> None:
        """emit game.milestone（重大进展）。"""
        await self._emit_game_event("milestone", message, scene=scene)

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
