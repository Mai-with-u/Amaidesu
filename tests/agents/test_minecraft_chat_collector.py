"""maicraft_chat 采集器测试：长轮询 MaiCraft v1 的聊天事件流，把别人说的话转成 game.chat.received。

采集器代码归属 Minecraft Agent 包（游戏相关适配器内聚），装配走采集器框架。
覆盖：
- 元数据/注册/配置默认值（私聊默认不转）
- 读的是 events(topic=chat)；首次读取只建游标，接手前的聊天不补发
- 玩家聊天带发言人与 UUID，系统消息不带发言人，截断标记照传
- 私聊默认不转，所有者打开开关后照常转；空白消息不转，游标照样往后走
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pytest

from src.agents.minecraft.chat_collector import MaicraftChatCollector
from src.modules.collectors.base import BaseCollector
from src.modules.collectors.factory import SUPPORTED_COLLECTORS, instantiate_collector
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatPayload

from .minecraft_collector_fakes import FakeEventBus, FakeMcpClient, events_page, patch_mcp

_STEVE_ID = "8667ba71-b85a-4004-af54-457a9734eed7"


@pytest.fixture(autouse=True)
def _patch_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_mcp(monkeypatch)


def _chat(
    cursor: int, text: str, *, kind: str = "player", private: bool = False, truncated: bool = False
) -> Dict[str, Any]:
    """一条聊天事件（形状与 MaiCraft v1 的 events(topic=chat) 一致）。"""
    event: Dict[str, Any] = {"cursor": cursor, "kind": kind, "text": text}
    if kind == "player":
        event.update(sender="Steve", sender_id=_STEVE_ID)
    if private:
        event["private"] = True
    if truncated:
        event["truncated"] = True
    return event


async def _primed(
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[MaicraftChatCollector, FakeMcpClient, FakeEventBus]:
    """接上替身并完成首次读取：流里已有一条接手前的聊天，游标停在 3。"""
    bus = FakeEventBus()
    collector = MaicraftChatCollector(config=config or {}, event_bus=bus)  # type: ignore[arg-type]
    assert await collector._ensure_ready() is True
    client = FakeMcpClient.instances[-1]
    client.pages.append(events_page([_chat(3, "接手前说的话")], cursor=3))
    await collector._drain()
    return collector, client, bus


def test_collector_metadata_and_registration() -> None:
    assert issubclass(MaicraftChatCollector, BaseCollector)
    assert MaicraftChatCollector.name == "maicraft_chat"
    assert "maicraft_chat" in SUPPORTED_COLLECTORS
    assert instantiate_collector("maicraft_chat", {}, None) is not None
    cfg = MaicraftChatCollector.ConfigSchema()
    assert cfg.url.endswith("/mcp")
    assert cfg.forward_private_messages is False, "私聊默认不转给主播"


async def test_reads_the_chat_topic_and_does_not_replay_history() -> None:
    collector, client, bus = await _primed()

    assert client.read_calls[0]["arguments"] == {"topic": "chat", "wait_ms": 0}
    assert bus.events == [], "接手前的聊天不补发"
    assert collector._cursor == 3


async def test_player_and_system_messages_become_game_chat_events() -> None:
    collector, client, bus = await _primed()
    client.pages.append(
        events_page(
            [
                _chat(4, "麦麦你在干嘛"),
                _chat(5, "Alex 加入了游戏", kind="system"),
                _chat(6, "很长的公告", truncated=True),
            ],
            cursor=6,
        )
    )

    await collector._drain()

    assert [name for name, _ in bus.events] == [CoreEvents.GAME_CHAT_RECEIVED] * 3
    said, joined, long_one = (payload for _, payload in bus.events)
    assert isinstance(said, GameChatPayload)
    assert (said.kind, said.sender, said.sender_id, said.content) == ("player", "Steve", _STEVE_ID, "麦麦你在干嘛")
    assert (joined.kind, joined.sender, joined.sender_id, joined.content) == ("system", "", "", "Alex 加入了游戏")
    assert long_one.truncated is True
    assert client.read_calls[-1]["arguments"] == {
        "topic": "chat",
        "stream_id": "stream-A",
        "after_cursor": 3,
        "wait_ms": 25_000,
    }


async def test_private_messages_are_held_back_unless_the_owner_allows() -> None:
    collector, client, bus = await _primed()
    client.pages.append(events_page([_chat(4, "悄悄跟你说", private=True), _chat(5, "   ")], cursor=5))

    await collector._drain()

    assert bus.events == [], "私聊默认不转，空白消息不转"
    assert collector._cursor == 5, "不转的消息游标照样往后走"

    allowing, allowed_client, allowed_bus = await _primed({"forward_private_messages": True})
    allowed_client.pages.append(events_page([_chat(4, "悄悄跟你说", private=True)], cursor=4))

    await allowing._drain()

    assert [payload.content for _, payload in allowed_bus.events] == ["悄悄跟你说"]
