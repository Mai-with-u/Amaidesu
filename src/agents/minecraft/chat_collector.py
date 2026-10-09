"""
MaicraftChatCollector —— 游戏内聊天采集器

上游是 MaiCraft v1 的聊天事件流（``events(topic=chat)``）：聊天栏收到的玩家聊天与服务器系统消息，
角色自己说的话 Mod 已经滤掉。本采集器**常驻**带着游标长轮询，把别人说的话转成通用的
``game.chat.received`` 事件，交给主播 Agent。连接、长轮询与游标都在 :class:`MaicraftEventsCollector`。

私聊（别人用 /msg 对角色说的话）默认不转：主播可能当众念出来；所有者打开配置开关后照常转出。
首次接入只对齐游标、不补发历史；Mod 换世界换了流编号后，同样只对齐游标。
"""

from __future__ import annotations

from typing import Any, Dict

from pydantic import Field

from src.agents.minecraft.events_collector import MaicraftEventsCollector
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatPayload


class MaicraftChatCollector(MaicraftEventsCollector):
    """游戏内聊天采集器（常驻长轮询 MaiCraft 的聊天事件流）。"""

    name = "maicraft_chat"
    description = "常驻读 MaiCraft 的聊天事件流，把游戏里别人说的话转成 game.chat.received 事件"
    topic = "chat"
    stream_label = "聊天流"

    class ConfigSchema(MaicraftEventsCollector.ConfigSchema):
        """采集器配置（包内权威 Schema）：连接与长轮询同身体事件采集器，另加私聊开关。"""

        forward_private_messages: bool = Field(
            default=False,
            description="别人私聊角色（/msg）的话也转给主播；主播可能当众念出来，默认不转",
        )

    async def _forward(self, event: Dict[str, Any]) -> bool:
        """一条收到的聊天 → 一条 ``game.chat.received``；空白的不转，私聊按开关决定。"""
        text = event.get("text")
        if not isinstance(text, str) or not text.strip():
            return False
        if event.get("private") is True and not self.typed_config.forward_private_messages:
            # 私聊默认不进主播：只记一行日志，所有者想让主播听到时再打开开关。
            self.logger.debug(f"私聊不转给主播（{event.get('sender') or '未知发言人'}）")
            return False
        system = event.get("kind") == "system"
        payload = GameChatPayload(
            game="minecraft",
            kind="system" if system else "player",
            sender="" if system else str(event.get("sender") or ""),
            sender_id="" if system else str(event.get("sender_id") or ""),
            content=text,
            truncated=event.get("truncated") is True,
        )
        await self.emit_event(CoreEvents.GAME_CHAT_RECEIVED, payload, source=self.name)
        return True


__all__ = ["MaicraftChatCollector"]
