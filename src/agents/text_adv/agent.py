"""TextAdvGameAgent —— 文字冒险游戏 Agent

设计：
- 继承 ``BaseAgent``（协议六项全部实现）
- 构造注入依赖（llm/prompt/event_bus/tool_registry/content_engine/...）
- 自带 Agent 专属工具（``text_adv_choose_option`` / ``text_adv_get_story``），provider="text_adv"
- 复用公用感知工具（注册名 ``vision_look_at_screen``，provider="vision"）—— 通过 ToolRegistry 调
- 内容引擎（ContentEngine）为包内私有接口：构造注入，Agent 与工具直连调用，不经工具注册表
- 内部状态 ``TextAdvGameAgentState``（内容状态内部自由）
- 感知-决策-推进闭环：``on_state_change`` → vision_look_at_screen → decide → text_adv_choose_option
- 不继承任何"组合式引擎"（无组合式引擎定案）

协议六项（最小契约）：
- 生命周期：start/stop/cleanup（默认实现）
- 工具提供：list_tools() → text_adv_choose_option + text_adv_get_story（provider="text_adv"）
- 事件上报：emit game.milestone / game.attention_required / game.error
- 状态读写：内部 TextAdvGameAgentState（内容状态）
- 健康：BaseAgent 心跳（默认实现）
- 元数据：name / description
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from src.modules.agents.base import BaseAgent
from src.modules.agents.manager import AgentManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.logging import get_logger
from src.modules.tools import ToolSpec
from src.agents.text_adv.content_engine import (
    ContentEngine,
    StubContentEngine,
)
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry

from .config import TextAdvConfig
from .state import TextAdvGameAgentState, TextAdvOption
from .tools import (
    TextAdvToolProvider,
    build_choose_option_spec,
    build_get_story_spec,
)


__all__ = [
    "TextAdvGameAgent",
    "TextAdvConfig",
    "build_text_adv_agent",
]


# ---------------------------------------------------------------------------
# TextAdvGameAgent
# ---------------------------------------------------------------------------


class TextAdvGameAgent(BaseAgent):
    """文字冒险游戏 Agent

    实现方式：
    - 零框架改动：本文件**不修改**任何 ``modules/agents/`` / ``modules/tools/`` 文件
    - 构造注入：所有依赖经 ``__init__`` 参数传入（可 mock / 可替换）
    - list_tools：仅声明 Agent 专属工具（provider="text_adv"），公用感知工具不声明
    - 感知复用：通过 ``ToolRegistry.invoke("vision_look_at_screen")`` 调公用工具
    - 推进专属："text_adv_choose_option" 翻译为 content_engine 输入
    """

    # ----- 元数据 -----
    name = "text_adv"
    description = "文字冒险游戏 Agent"

    # ----- 事件族声明 -----
    emits_events = (
        CoreEvents.GAME_MILESTONE,
        CoreEvents.GAME_ATTENTION_REQUIRED,
        CoreEvents.GAME_ERROR,
    )

    def __init__(
        self,
        config: TextAdvConfig,
        *,
        content_engine: Optional[ContentEngine] = None,
        llm_manager: Optional[Any] = None,
        prompt_manager: Optional[Any] = None,
        event_bus: Optional[EventBus] = None,
        tool_registry: Optional[ToolRegistry] = None,
        live_session_id: str = "",
    ) -> None:
        """初始化文字冒险 Agent。

        Args:
            config: TextAdvConfig 实例
            content_engine: 内容引擎（默认 StubContentEngine）
            llm_manager: 可选 LLMManager（简化版未用，保留接口以备未来扩展）
            prompt_manager: 可选 PromptManager（同上）
            event_bus: 可选 EventBus（emit game.* 事件）
            tool_registry: 可选 ToolRegistry（注册 Agent 专属工具）
            live_session_id: 场次 ID（写入 game.* 事件 payload）
        """
        super().__init__(event_bus=event_bus)
        self.typed_config = config
        self._llm = llm_manager
        self._prompt = prompt_manager
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._content_engine: ContentEngine = content_engine or StubContentEngine()
        self._live_session_id = live_session_id or "wave7_session"

        # 内容状态（Agent 内部自由）；避免与 BaseAgent.state 属性同名故用 _game_state
        self._game_state: TextAdvGameAgentState = TextAdvGameAgentState()

        # ToolProvider 实例（在 _on_start 中注册进 ToolRegistry）
        self._game_provider: Optional[TextAdvToolProvider] = None

        # 统计
        self._step_count = 0
        self._perception_count = 0
        self._advance_count = 0

        self._logger = get_logger("TextAdvGameAgent")

        self._logger.info(
            f"TextAdvGameAgent 已构造 (engine_kind={config.engine_kind}, decision={config.decision_strategy})"
        )

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def _on_start(self) -> None:
        """启动钩子：注册 Agent 专属工具 + 启动 content_engine。"""
        # 注册 Agent 专属工具（text_adv_choose_option / text_adv_get_story）
        if self._tool_registry is not None:
            self._register_tools()

        # 启动 content_engine（如果未启动）
        await self._safe_engine_start()

        self._logger.info("TextAdvGameAgent 已启动")

    async def _on_stop(self) -> None:
        """停止钩子：优雅停止 content_engine。"""
        try:
            await self._content_engine.stop()
        except Exception as exc:  # noqa: BLE001 - 边界兜底
            self._logger.warning(f"停止 ContentEngine 失败: {exc}")

    # ==================================================================
    # 工具提供（list_tools）
    # ==================================================================

    def list_tools(self) -> Iterable[ToolSpec]:
        """声明 Agent 专属工具（provider="text_adv"）。"""
        return [build_choose_option_spec(), build_get_story_spec()]

    def _register_tools(self) -> None:
        """注册 Agent 专属工具到 ToolRegistry。"""
        if self._tool_registry is None:
            return
        self._game_provider = TextAdvToolProvider(
            state=self._game_state,
            engine=self._content_engine,
        )
        self._tool_registry.register_provider(self._game_provider)
        self._logger.info("TextAdvGameAgent 工具已注册：text_adv_choose_option / text_adv_get_story")

    async def _safe_engine_start(self) -> None:
        """启动 content_engine（捕获异常，不阻断 Agent 启动）。"""
        try:
            await self._content_engine.start()
        except Exception as exc:  # noqa: BLE001 - 边界兜底
            self._logger.warning(f"ContentEngine.start() 失败: {exc}")

    # ==================================================================
    # 事件上报（game.* 语义域）
    # ==================================================================

    async def emit_milestone(self, message: str, *, scene: str = "") -> None:
        """emit ``game.milestone`` 事件（剧情推进/章节完成）。"""
        if not self.typed_config.enable_event_emission:
            return
        payload = GamePayload(
            live_session_id=self._live_session_id,
            game="text_adv",
            event_type="milestone",
            message=message,
            scene=scene or self._game_state.scene_id,
        )
        await self.emit_event(CoreEvents.GAME_MILESTONE, payload)

    async def emit_attention_required(self, message: str, *, scene: str = "") -> None:
        """emit ``game.attention_required`` 事件（等待选择 / 安全阀偏差）。"""
        if not self.typed_config.enable_event_emission:
            return
        payload = GamePayload(
            live_session_id=self._live_session_id,
            game="text_adv",
            event_type="attention_required",
            message=message,
            scene=scene or self._game_state.scene_id,
        )
        await self.emit_event(CoreEvents.GAME_ATTENTION_REQUIRED, payload)

    async def emit_error(self, message: str, *, scene: str = "") -> None:
        """emit ``game.error`` 事件。"""
        if not self.typed_config.enable_event_emission:
            return
        payload = GamePayload(
            live_session_id=self._live_session_id,
            game="text_adv",
            event_type="error",
            message=message,
            scene=scene or self._game_state.scene_id,
        )
        await self.emit_event(CoreEvents.GAME_ERROR, payload)

    # ==================================================================
    # 感知-推进-循环（闭环）
    # ==================================================================

    async def feed_state_change(
        self, new_screen_text: str, options: Optional[List[TextAdvOption]] = None
    ) -> Dict[str, Any]:
        """外部 API：注入"游戏画面变化"→ 触发一次感知-推进闭环。

        感知-推进闭环的核心入口：
            输入：屏幕文本 + 选项列表（mock 数据或真实采集）
            过程：调公用 look_at_screen 工具感知 → 解析更新内部状态 →
            决策（简化：首选项）→ 调 text_adv_choose_option 触发推进 →
            emit game.milestone 报告推进
            返回：本次闭环的统计 + 状态快照

        测试场景通过此方法验证"感知调用 + 推进触发 + 循环闭合"。
        """
        if self._tool_registry is None:
            raise RuntimeError("TextAdvGameAgent 未注入 tool_registry，无法跑闭环")

        self._step_count += 1
        result: Dict[str, Any] = {
            "step": self._step_count,
            "perception_called": False,
            "advance_called": False,
            "options_updated": False,
            "decision": None,
            "engine_input_accepted": False,
        }

        # ---- 感知（调公用 vision_look_at_screen）----
        screen_text = new_screen_text
        try:
            perception = await self._tool_registry.invoke(
                _make_invocation("vision_look_at_screen", arguments={}, source=self.name)
            )
            self._perception_count += 1
            result["perception_called"] = True
            # 若 look_at_screen 成功且返回了文本，优先用其内容（模拟真实流程）
            if perception.success and perception.structured_content:
                perceived_text = str(perception.structured_content.get("text") or "")
                if perceived_text:
                    screen_text = perceived_text
            elif not perception.success:
                await self.emit_error(f"感知失败: {perception.error_message}")
                return result
        except Exception as exc:  # noqa: BLE001
            await self.emit_error(f"感知异常: {type(exc).__name__}: {exc}")
            return result

        # ---- 解析（更新内部状态）----
        changed = self._game_state.apply_screen_text(screen_text)
        if options is not None:
            self._game_state.set_options(options)
            result["options_updated"] = True
        if not changed and not options:
            # 无变化 → 不推进（避免空转）
            return result

        # ---- 决策（默认首选项）----
        chosen = self._game_state.pick_default_option()
        if chosen is None:
            await self.emit_attention_required("无可选选项，等待新场景")
            return result
        result["decision"] = chosen.option_id

        # ---- 推进（调 text_adv_choose_option）----
        try:
            advance_result = await self._tool_registry.invoke(
                _make_invocation(
                    "text_adv_choose_option",
                    arguments={"option_id": chosen.option_id},
                    source=self.name,
                )
            )
            self._advance_count += 1
            result["advance_called"] = True
            if advance_result.success:
                result["engine_input_accepted"] = True
                # ---- 里程碑上报 ----
                await self.emit_milestone(
                    f"已选择选项 {chosen.option_id}（{chosen.label}）",
                    scene=self._game_state.scene_id,
                )
            else:
                await self.emit_error(f"推进失败: {advance_result.error_message}")
        except Exception as exc:  # noqa: BLE001
            await self.emit_error(f"推进异常: {type(exc).__name__}: {exc}")

        return result

    # ==================================================================
    # 统计
    # ==================================================================

    def get_statistics(self) -> Dict[str, Any]:
        """获取运行时统计（测试可断言）。"""
        return {
            "step_count": self._step_count,
            "perception_count": self._perception_count,
            "advance_count": self._advance_count,
            "state": self._game_state.to_dict(),
        }


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _make_invocation(tool_name: str, *, arguments: Dict[str, Any], source: str):
    """构造 ToolInvocation（避免顶层 import 长路径污染）。"""
    return ToolInvocation(
        tool_name=tool_name,
        arguments=arguments,
        source=source,
    )


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def build_text_adv_agent(
    *,
    config: TextAdvConfig,
    agent_manager: AgentManager,
    content_engine: Optional[ContentEngine] = None,
    llm_manager: Optional[Any] = None,
    prompt_manager: Optional[Any] = None,
    event_bus: Optional[EventBus] = None,
    tool_registry: Optional[ToolRegistry] = None,
    live_session_id: str = "",
    spec_provider: str = "text_adv",
) -> TextAdvGameAgent:
    """便捷工厂：构造 TextAdvGameAgent + 注册到 AgentManager。

    Args:
        config: TextAdvGameConfig 实例
        agent_manager: AgentManager 实例（构造完后 register）
        其余参数同 :class:`TextAdvGameAgent`
        spec_provider: provider 来源溯源（默认 "text_adv"）

    Returns:
        构造好的 TextAdvGameAgent（已 register 到 agent_manager）
    """
    agent = TextAdvGameAgent(
        config=config,
        content_engine=content_engine,
        llm_manager=llm_manager,
        prompt_manager=prompt_manager,
        event_bus=event_bus,
        tool_registry=tool_registry,
        live_session_id=live_session_id,
    )
    agent_manager.register(agent, spec_provider=spec_provider)
    return agent
