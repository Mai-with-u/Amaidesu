"""Agent 实例化工厂（配置名 → 具体类）

配置名 → 具体类的唯一映射，供启动装配与 Dashboard 动态启停复用。

配置名映射：
- streamer   → StreamerAgent
- minecraft  → MinecraftAgent
- text_adv   → TextAdvGameAgent

Agent 间无分类层；每个 Agent 是一等公民。
"""

from __future__ import annotations

from typing import Any, Optional

from src.modules.agents.base import BaseAgent

# 已实现的 Agent 注册名
SUPPORTED_AGENTS: tuple[str, ...] = ("streamer", "minecraft", "text_adv")


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
    thinking_sink: Optional[Any] = None,
) -> Optional[BaseAgent]:
    """按名实例化 Agent；未知名字返回 None。

    ``persona_provider`` 关键字参数透传给 StreamerAgent；装配根从
    ``config_service.get_section("persona")`` 拉取 persona dict 传入，
    缺省 None 时 StreamerAgent 走 ``_DEFAULT_*`` 兜底。

    ``thinking_sink`` 关键字参数透传给 StreamerAgent 与 MinecraftAgent
    （鸭子类型：任何带 ``on_thinking_delta`` 方法的对象）；缺省 None 时
    两 Agent 的思考流旁路整体短路，决策循环行为与无旁路完全一致。
    """
    config = config if isinstance(config, dict) else {}

    if name == "streamer":
        from src.agents.streamer.config import StreamerConfig
        from src.agents.streamer.streamer_agent import StreamerAgent

        try:
            cfg_obj = StreamerConfig.from_dict(config) if config else StreamerConfig()
        except Exception as exc:
            from src.modules.logging import get_logger

            get_logger("AgentFactory").warning(f"解析 StreamerConfig 配置失败: {exc}; 使用默认配置")
            cfg_obj = StreamerConfig()
        return StreamerAgent(
            config=cfg_obj,
            llm_manager=llm_manager,
            prompt_manager=prompt_manager,
            context_service=context_service,
            event_bus=event_bus,
            tool_registry=tool_registry,
            memory=memory,
            persona_provider=persona_provider,
            thinking_sink=thinking_sink,
        )

    if name == "minecraft":
        from src.agents.minecraft import MinecraftAgent
        from src.agents.minecraft.config import MinecraftConfig

        try:
            minecraft_cfg = MinecraftConfig(**config)
        except Exception as exc:
            from src.modules.logging import get_logger

            get_logger("AgentFactory").warning(f"解析 MinecraftConfig 失败: {exc}; 使用默认配置")
            minecraft_cfg = MinecraftConfig()
        return MinecraftAgent(
            config=minecraft_cfg,
            llm_manager=llm_manager,
            llm_profile="llm",
            prompt_manager=prompt_manager,
            event_bus=event_bus,
            tool_registry=tool_registry,
            thinking_sink=thinking_sink,
        )

    if name == "text_adv":
        from src.agents.text_adv import TextAdvConfig, TextAdvGameAgent
        from src.agents.text_adv.content_engine import StubContentEngine

        try:
            text_adv_cfg = TextAdvConfig(**config) if config else TextAdvConfig()
        except Exception as exc:
            from src.modules.logging import get_logger

            get_logger("AgentFactory").warning(f"解析 TextAdvConfig 失败: {exc}; 使用默认配置")
            text_adv_cfg = TextAdvConfig()
        return TextAdvGameAgent(
            config=text_adv_cfg,
            content_engine=StubContentEngine(engine_kind="text_adv"),
            llm_manager=llm_manager,
            prompt_manager=prompt_manager,
            event_bus=event_bus,
        )

    return None


__all__ = ["SUPPORTED_AGENTS", "instantiate_agent"]
