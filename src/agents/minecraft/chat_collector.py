"""
MaicraftChatCollector —— 游戏内聊天采集器

上游是 MaiCraft v1 的聊天事件流（``events(topic=chat)``）：聊天栏收到的玩家聊天与服务器系统消息，
角色自己说的话 Mod 已经滤掉。本采集器**常驻**带着游标长轮询，把别人说的话转成通用的
``game.chat.received`` 事件，交给主播 Agent。连接、长轮询与游标都在 :class:`MaicraftEventsCollector`。

私聊（别人用 /msg 对角色说的话）照常转出并标 ``private``：主播据此只拿它做决定，不在直播里念出或转述。
没有发言人 UUID 的"玩家消息"名字只是聊天栏上显示的：按 ``chat_attribution`` 分出模组借角色名义发的
机器消息（改记成系统消息）与无凭据的发言，附一句说明给主播。
首次接入只对齐游标、不补发历史；Mod 换世界换了流编号后，同样只对齐游标。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from src.agents.minecraft.chat_attribution import attribute_chat
from src.agents.minecraft.events_collector import MaicraftEventsCollector
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatPayload


class MaicraftChatCollector(MaicraftEventsCollector):
    """游戏内聊天采集器（常驻长轮询 MaiCraft 的聊天事件流）。"""

    name = "maicraft_chat"
    description = "常驻读 MaiCraft 的聊天事件流，把游戏里别人说的话转成 game.chat.received 事件"
    topic = "chat"
    stream_label = "聊天流"

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        event_bus: Optional[EventBus] = None,
        client_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        super().__init__(config=config, event_bus=event_bus, client_factory=client_factory)
        # 角色自己的账号名：认出"借角色名义发的机器消息"用；Mod 没报出之前为空，只做"无凭据"标注
        self._self_name = ""

    async def _forward(self, event: Dict[str, Any]) -> bool:
        """一条收到的聊天 → 一条 ``game.chat.received``；空白的不转，私聊照转并标出来。"""
        text = event.get("text")
        if not isinstance(text, str) or not text.strip():
            return False
        attributed = attribute_chat(event, self._self_name)
        system = attributed.kind == "system"
        payload = GameChatPayload(
            game="minecraft",
            kind="system" if system else "player",
            sender=attributed.sender,
            sender_id=attributed.sender_id,
            content=attributed.content,
            truncated=event.get("truncated") is True,
            # 私聊只有玩家发得出来：系统消息即使带了标记也不算
            private=not system and event.get("private") is True,
            note=attributed.note,
        )
        await self.emit_event(CoreEvents.GAME_CHAT_RECEIVED, payload, source=self.name)
        return True


__all__ = ["MaicraftChatCollector"]
