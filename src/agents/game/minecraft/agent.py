"""MinecraftAgent —— Minecraft 世界的 AI 玩家（决策层）

设计：
- 继承 ``BaseAgent``（协议六面全部实现）
- 构造注入依赖（event_bus/tool_registry/maicraft_adapter/...）
- 局部工具（mc_todo/mc_memo/mc_get_state）声明 → 注册进 ToolRegistry（provider="game"）
- 决策循环：read todo → perceive（maicraft）→ LLM 决策（占位）→ 执行 → emit 事件
- 三通道协作：set_goal（主播→玩家命令）/ game.* 事件（玩家→主播汇报）/
  mc_get_state（状态通道查询工具）
- 不建模游戏数据；maicraft 返回原样给 LLM；slim 适配隔离 maicraft 接口

注意：LLM 决策（think）本轮为占位（决策循环骨架 + 工具契约完整），
Minecraft 特殊提示词后续数据驱动优化——不写详细提示词。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Iterable, List, Literal, Optional

from src.agents.game.minecraft.maicraft_adapter import MaicraftAdapter
from src.agents.game.minecraft.state import MinecraftAgentState
from src.agents.game.minecraft.tools import (
    MinecraftToolProvider,
    build_get_state_spec,
    build_memo_spec,
    build_set_goal_spec,
    build_todo_spec,
)
from src.modules.agents.base import BaseAgent
from src.modules.agents.manager import AgentManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.logging import get_logger
from src.modules.tools.registry import ToolRegistry
from src.modules.tools import ToolSpec

from .config import MinecraftConfig


__all__ = [
    "MinecraftAgent",
    "build_minecraft_agent",
    "DECIDE_FN_DEF",
]


# 决策 function 定义（标准 function calling；Agent 内部协议，不进 ToolRegistry）
DECIDE_FN_DEF: Dict[str, Any] = {
    "name": "minecraft_decide",
    "description": (
        "Minecraft 玩家决策：根据当前目标、待办列表与世界感知，决定下一步动作。"
        "输出语义动作（mine/build/craft/move_to/none）、目标描述、"
        "可转述消息（面向直播叙事）与是否写备忘录。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "next_action": {
                "type": "string",
                "enum": ["mine", "build", "craft", "move_to", "none"],
                "description": "要执行的语义动作",
            },
            "action_goal": {
                "type": "string",
                "description": "动作目标描述（如 '挖 3 个钻石'）；next_action=none 时留空",
            },
            "visible_message": {
                "type": "string",
                "description": "值得向主播/直播叙事转述的一句话；无则留空",
            },
            "should_write_memo": {
                "type": "boolean",
                "description": "是否把关键发现写入备忘录",
            },
        },
        "required": ["next_action", "action_goal", "visible_message", "should_write_memo"],
    },
}

# 降级决策（无 LLM / 调用失败时返回：不动作、不转述）
_NOOP_DECISION: Dict[str, Any] = {
    "next_action": "none",
    "action_goal": "",
    "visible_message": "",
    "should_write_memo": False,
}

# 无 prompt_manager 时的内建兜底提示词（minimal；正式模板见 prompts/）
_DEFAULT_DECIDE_PROMPT = (
    "你是 Minecraft 世界中的 AI 玩家。根据当前目标、待办与世界感知，"
    "决定下一步动作（mine/build/craft/move_to/none），输出可转述消息与是否需要写备忘录。"
    "待办为空且有目标时，将目标拆解为待办并开始执行。"
)


class MinecraftAgent(BaseAgent):
    """Minecraft 游戏 Agent（AI 玩家）

    实现方式：
    - 零框架改动：本文件不修改任何 ``modules/agents/`` / ``modules/tools/`` 文件
    - 构造注入：所有依赖经 ``__init__`` 参数传入（可 mock / 可替换）
    - list_tools：声明 Agent 专属工具（provider="game"），经 registry 注册
    - 局部工具（mc_todo/mc_memo）驱动决策循环；mc_get_state 供外部查询
    """

    # ----- 元数据 -----
    name = "minecraft"
    description = "Minecraft 游戏 Agent（AI 玩家）"

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
        prompt_manager: Optional[Any] = None,
        event_bus: Optional[EventBus] = None,
        tool_registry: Optional[ToolRegistry] = None,
        maicraft: Optional[MaicraftAdapter] = None,
        live_session_id: str = "",
    ) -> None:
        """初始化 Minecraft Agent。

        Args:
            config: MinecraftConfig 实例
            llm_manager: 可选 LLMManager（决策循环用；无则降级 noop）
            prompt_manager: 可选 PromptManager（渲染决策提示词）
            event_bus: 可选 EventBus（emit game.* 事件）
            tool_registry: 可选 ToolRegistry（注册 Agent 专属工具 + 转发 maicraft 调用）
            maicraft: 可选 MaicraftAdapter（无则降级——循环只跑 todo/memo，不感知世界）
            live_session_id: 场次 ID（写入 game.* 事件 payload）
        """
        super().__init__(event_bus=event_bus)
        self.typed_config = config
        self._llm = llm_manager
        self._prompt = prompt_manager
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._maicraft: MaicraftAdapter = maicraft or MaicraftAdapter(tool_registry, server_id=config.server_id)
        self._live_session_id = live_session_id or "minecraft_session"

        # Agent 内部状态（内存，不持久化）
        self._mc_state: MinecraftAgentState = MinecraftAgentState()
        self._tool_provider: Optional[MinecraftToolProvider] = None

        # 决策循环任务句柄
        self._loop_task: Optional[asyncio.Task[None]] = None
        self._running = False

        self._logger = get_logger("MinecraftAgent")
        self._logger.info(
            f"MinecraftAgent 已构造 (tick={config.tick_seconds}s, maicraft={'已注入' if maicraft else '降级'}, llm={'已注入' if llm_manager else '无'})"
        )

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def _on_start(self) -> None:
        """启动钩子：注册专属工具 + 启动决策循环。"""
        if self._tool_registry is not None:
            self._register_tools()

        self._running = True
        self._loop_task = asyncio.create_task(self._decision_loop())
        self._logger.info("MinecraftAgent 已启动（决策循环就绪）")

    async def _on_stop(self) -> None:
        """停止钩子：优雅退出决策循环。"""
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - 边界兜底
                self._logger.warning(f"决策循环退出异常: {exc}")
            self._loop_task = None
        self._logger.info("MinecraftAgent 已停止")

    # ==================================================================
    # 工具提供（list_tools）
    # ==================================================================

    def list_tools(self) -> Iterable[ToolSpec]:
        """声明 Agent 专属工具（provider="game"）。"""
        return [build_todo_spec(), build_memo_spec(), build_get_state_spec(), build_set_goal_spec()]

    def _register_tools(self) -> None:
        """注册 Agent 专属工具到 ToolRegistry（Provider 类）。"""
        if self._tool_registry is None:
            return
        self._tool_provider = MinecraftToolProvider(state=self._mc_state)
        self._tool_registry.register_provider(self._tool_provider)
        self._logger.info("MinecraftAgent 工具已注册：mc_todo / mc_memo / mc_get_state")

    # ==================================================================
    # 决策循环（感知-决策-推进骨架）
    # ==================================================================

    async def _decision_loop(self) -> None:
        """决策循环：每 tick_seconds 跑一轮。

        骨架（LLM 决策占位）：
          read todo → perceive（maicraft，可用时）→ 【LLM 决策·待施工】→
          execute（maicraft，可用时）→ emit 事件
        无 LLM/无 maicraft 时：只跑 todo/memo 文档维护开销（降级不报错）。
        """
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单轮失败不中断循环
                self._logger.error(f"决策循环单轮异常: {exc}", exc_info=True)
                await self._emit_game_event("error", f"决策循环异常: {exc}")
            await asyncio.sleep(self.typed_config.tick_seconds)

    async def _tick(self) -> None:
        """单轮决策：read todo → perceive → LLM 决策 → 执行/更新/事件。"""
        # 1. 读待办（驱动循环的数据源）
        todo_doc = self._mc_state.todo_doc()

        # 2. 感知（maicraft 可用时；失败仅记日志，不阻断）
        perception = await self._maicraft.perceive("situation")
        perception_block: Dict[str, Any] = {"ok": bool(perception.get("ok"))}

        # 3. LLM 决策（function calling；无 LLM/失败 → noop 降级）
        decision = await self._llm_decide(todo_doc, perception_block)

        # 4. 执行决策
        await self._apply_decision(decision)

        self._logger.debug(
            f"MinecraftAgent tick: todo={len(todo_doc['todos'])} "
            f"perception_ok={perception_block['ok']} action={decision.get('next_action')!r}"
        )

    # ------------------------------------------------------------------
    # LLM 决策（内部协议 minescript_decide）
    # ------------------------------------------------------------------

    async def _llm_decide(self, todo_doc: Dict[str, Any], perception_block: Dict[str, Any]) -> Dict[str, Any]:
        """调 LLM 决策；返回 {next_action, action_goal, visible_message, should_write_memo}。
        无 LLM / 调用失败 / 无 tool_calls → 降级 noop。"""
        if self._llm is None:
            return _NOOP_DECISION

        try:
            if self._prompt is not None:
                prompt = self._prompt.render(
                    "amaidesu_minecraft_decide",
                    current_goal=self._mc_state.current_goal or "",  # 留空：模板变量可缺省
                    todo=todo_doc,
                    perception="感知不可用" if not perception_block.get("ok") else "世界状态可感知",
                )
            else:
                prompt = _DEFAULT_DECIDE_PROMPT  # 无 prompt_manager 时用内建兜底

            response = await self._llm.call_tools(
                prompt=prompt,
                tools=[DECIDE_FN_DEF],
                system_message="你是 Minecraft 世界中的 AI 玩家，根据待办与世界感知决定下一步动作。",
            )
            if not response.success:
                self._logger.warning(f"MinecraftAgent LLM 决策失败: {response.error}")
                return _NOOP_DECISION
            arguments_str = self._extract_decide_arguments(response.tool_calls)
            if not arguments_str:
                return _NOOP_DECISION
            parsed = json.loads(arguments_str)
            if not isinstance(parsed, dict):
                return _NOOP_DECISION
            return {
                "next_action": str(parsed.get("next_action", "none")),
                "action_goal": str(parsed.get("action_goal", "") or ""),
                "visible_message": str(parsed.get("visible_message", "") or ""),
                "should_write_memo": bool(parsed.get("should_write_memo", False)),
            }
        except Exception as exc:  # noqa: BLE001 - 决策失败不中断循环
            self._logger.warning(f"MinecraftAgent LLM 决策异常，降级 noop: {type(exc).__name__}: {exc}")
            return _NOOP_DECISION

    @staticmethod
    def _extract_decide_arguments(tool_calls: Optional[List[Dict[str, Any]]]) -> Optional[str]:
        """从 tool_calls 找 minescript_decide 的 arguments 字符串；找不到 → None。"""
        if not tool_calls:
            return None
        for call in tool_calls:
            name = call.get("name")
            if name == "minecraft_decide":
                arguments = call.get("arguments")
                if isinstance(arguments, str):
                    return arguments
                if isinstance(arguments, dict):
                    return json.dumps(arguments, ensure_ascii=False)
        return None

    async def _apply_decision(self, decision: Dict[str, Any]) -> None:
        """执行决策：动作 → maicraft.execute；消息 → milestone；备忘录 → 写。"""
        action = decision.get("next_action")
        if action not in (None, "none", ""):
            goal = decision.get("action_goal")
            if goal:
                result = await self._maicraft.execute({"action": action, "goal": goal})
                if not result.get("ok"):
                    await self.emit_error(f"执行决策失败: {goal}")
                    return

        visible = decision.get("visible_message") or ""
        if visible:
            await self.emit_milestone(visible)

        if decision.get("should_write_memo"):
            self._mc_state.set_memo(visible or self._mc_state.memo)

    # ==================================================================
    # set_goal 命令入口（三通道·命令）
    # ==================================================================

    async def set_goal(self, goal: str) -> None:
        """接收主播命令（set_goal）——目标级意图，写入 current_goal。

        命令不可拒绝（保命级例外由执行层自主处理）；目标写入状态，
        由决策循环/下次 LLM 决策分解为 todo。
        """
        self._mc_state.set_goal(goal)
        self._logger.info(f"MinecraftAgent 收到 set_goal: {goal}")

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
            # 里程碑事件同步进内存（mc_get_state 的 recent_milestones 数据源）
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
        """导出状态快照（mc_get_state 同构；测试可断言）。"""
        return self._mc_state.to_dict()


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def build_minecraft_agent(
    *,
    config: MinecraftConfig,
    agent_manager: AgentManager,
    event_bus: Optional[EventBus] = None,
    tool_registry: Optional[ToolRegistry] = None,
    maicraft: Optional[MaicraftAdapter] = None,
    live_session_id: str = "",
    spec_provider: str = "game",
) -> MinecraftAgent:
    """便捷工厂：构造 MinecraftAgent + 注册到 AgentManager。

    Args:
        config: MinecraftConfig 实例
        agent_manager: AgentManager 实例（构造完后 register）
        其余参数同 :class:`MinecraftAgent`
        spec_provider: provider 来源溯源（默认 "game"）

    Returns:
        构造好的 MinecraftAgent（已 register 到 agent_manager）
    """
    agent = MinecraftAgent(
        config=config,
        event_bus=event_bus,
        tool_registry=tool_registry,
        maicraft=maicraft,
        live_session_id=live_session_id,
    )
    agent_manager.register(agent, spec_provider=spec_provider)
    return agent
