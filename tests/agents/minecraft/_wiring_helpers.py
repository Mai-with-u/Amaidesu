"""MinecraftAgent 记忆与游戏公屏门控接线测试共用轻量辅助件。"""

from __future__ import annotations

import asyncio
from typing import Iterable

from src.modules.agents import BaseAgent
from src.modules.events.event_bus import EventBus
from src.modules.tools.models import ToolSpec


class StreamerPlaceholder(BaseAgent):
    """用于 AgentManager 占位的最小主播桩。"""

    name = "streamer"
    description = "占位主播"

    def __init__(self) -> None:
        super().__init__(heartbeat_interval_ms=0)

    def list_tools(self) -> Iterable[ToolSpec]:
        return []


async def no_real_sleep(_delay_ms: int) -> None:
    """注入给游戏公屏合批定时器的零真实 sleep 等待器，由假时钟推进 + flush_pending_chat 精确驱动。"""
    return None


async def settle(bus: EventBus, turns: int = 6) -> None:
    """等待事件总线排空并让出若干事件循环轮次。"""
    await bus.wait_until_drained()
    for _ in range(turns):
        await asyncio.sleep(0)
