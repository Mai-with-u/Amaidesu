"""Minecraft Agent 的附属采集器：身体事件流与聊天流

两条流都只对 Minecraft Agent 有意义，随它一同开关（框架侧入口见 ``src.modules.agents.companions``）。
配置从 ``[agents.minecraft]`` 换算：连接用 ``mcp`` 那一份（地址、传输方式、请求头、超时与 Agent 完全一致，
不会出现两处写成不一样），等待与重试间隔用 ``events_wait_ms`` / ``events_retry_interval_ms``。
Agent 的 MCP 关着（``mcp.enabled=false``）时 Agent 本身就不接游戏，两条流也不装。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.agents.minecraft.attention_collector import MaicraftAttentionCollector
from src.agents.minecraft.chat_collector import MaicraftChatCollector
from src.agents.minecraft.config import MinecraftConfig
from src.modules.collectors.base import BaseCollector
from src.modules.events.event_bus import EventBus


def companion_config(agent_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Agent 配置 → 附属采集器的配置；Agent 的 MCP 关着为 None（不装）。"""
    config = MinecraftConfig.from_dict(agent_config or {})
    if not config.mcp.enabled:
        return None
    return {
        "mcp": config.mcp.model_dump(),
        "wait_ms": config.events_wait_ms,
        "retry_interval_ms": config.events_retry_interval_ms,
    }


def build_companion_collectors(
    agent_config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    context: Optional[Dict[str, Any]] = None,
) -> List[BaseCollector]:
    """构造身体事件流与聊天流两个采集器（未启动）；Agent 的 MCP 关着时为空表。

    ``context`` 是组合根透传的跨 Agent 参考事实：聊天流采集器判"玩家聊天提到她"需要主播的人设名
    （玩家在游戏公屏叫的名字），那份配置在 streamer 段，这里只按名取用、不感知 streamer 包内部。
    """
    config = companion_config(agent_config)
    if config is None:
        return []
    context = context or {}
    bot_name = str(context.get("streamer_bot_name") or "")
    return [
        MaicraftAttentionCollector(config=dict(config), event_bus=event_bus),
        MaicraftChatCollector(
            config=dict(config),
            event_bus=event_bus,
            self_mention_names=(bot_name,) if bot_name else (),
        ),
    ]


__all__ = ["build_companion_collectors", "companion_config"]
