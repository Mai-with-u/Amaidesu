"""Agent 实例化工厂（配置名 → 具体类）

中央化 main.py 原有的 streamer/game/custom 分支（Single Source of Truth），
供启动装配与 Dashboard 动态启停复用。

配置名映射（v2 命名）：
- streamer → StreamerAgent
- game    → TextAdvGameAgent（引擎 text_adv）
- custom  → 用户自定义注册（占位，不实例化）
"""

from __future__ import annotations

from typing import Any, Optional

from src.modules.agents.base import BaseAgent

# 已实现的 Agent 注册名（组件管理页"可用组件"清单来源）
SUPPORTED_AGENTS: tuple[str, ...] = ("streamer", "game")


def instantiate_agent(
    name: str,
    config: Optional[dict[str, Any]],
    *,
    llm_manager: Any,
    prompt_manager: Any,
    context_service: Optional[Any] = None,
    event_bus: Any = None,
    tool_registry: Any = None,
    memory: Any = None,
    persona_provider: Optional[Any] = None,
) -> Optional[BaseAgent]:
    """按名实例化 Agent；未知名字返回 None。

    v2.0.6 B2 修复：新增 ``persona_provider`` 关键字参数透传给 StreamerAgent。
    装配根（main._register_agents_from_config）从 config_service.get_section("persona")
    拉取 persona dict 传入；缺省 None 时 StreamerAgent 走 _DEFAULT_* 兜底。
    """
    config = config if isinstance(config, dict) else {}

    if name == "streamer":
        from src.agents.streamer.streamer_agent import StreamerAgent, StreamerAgentConfig

        try:
            cfg_obj = StreamerAgentConfig(**config) if config else StreamerAgentConfig()
        except Exception as exc:
            from src.modules.logging import get_logger

            get_logger("AgentFactory").warning(f"解析 StreamerAgent 配置失败: {exc}; 使用默认配置")
            cfg_obj = StreamerAgentConfig()
        return StreamerAgent(
            config=cfg_obj,
            llm_manager=llm_manager,
            prompt_manager=prompt_manager,
            context_service=context_service,
            event_bus=event_bus,
            tool_registry=tool_registry,
            memory=memory,
            persona_provider=persona_provider,
        )

    if name == "game":
        from src.agents.game.text_adv import TextAdvGameAgent, TextAdvGameConfig
        from src.modules.tools.content_engine import StubContentEngine

        engine_name = str(config.get("engine", "text_adv") or "text_adv")
        if engine_name != "text_adv":
            return None
        try:
            text_adv_cfg = TextAdvGameConfig(**{k: v for k, v in config.items() if k != "engine"})
        except Exception:
            text_adv_cfg = TextAdvGameConfig()
        return TextAdvGameAgent(
            config=text_adv_cfg,
            content_engine=StubContentEngine(engine_kind="text_adv"),
            llm_manager=llm_manager,
            prompt_manager=prompt_manager,
            event_bus=event_bus,
        )

    return None


__all__ = ["SUPPORTED_AGENTS", "instantiate_agent"]
