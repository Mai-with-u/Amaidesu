"""maicraft_chat 采集器测试：游戏聊天流的新消息识别与转出。

覆盖：
- 元数据、框架登记与配置默认值
- 首次接入只对齐游标，不把历史聊天当成刚有人说话
- 之后只转出游标之后的新消息；AI 玩家自己的回显不转出，系统消息标 system
- Mod 换世界换了流编号：新流里的消息照常转出
- 页面残缺：抛错交给循环重连，游标不动
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from src.agents.minecraft.chat_collector import MaicraftChatCollector
from src.modules.collectors.base import BaseCollector
from src.modules.collectors.factory import SUPPORTED_COLLECTORS, instantiate_collector
from src.modules.config.collectors_schemas import CollectorsRootConfig
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatPayload


class _FakeEventBus:
    """捕获 emit 的桩。"""

    def __init__(self) -> None:
        self.events: List[tuple] = []

    async def emit(self, event_name: str, payload: Any, **kwargs: Any) -> None:
        self.events.append((event_name, payload))


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeClient:
    """MCP 客户端替身：资源读取按预置页面依次返回。"""

    instances: List["_FakeClient"] = []

    def __init__(self, name: str, config: Any) -> None:
        self.name = name
        self.config = config
        self.closed = False
        self.subscriptions: List[str] = []
        self.pages: List[Optional[Dict[str, Any]]] = []
        _FakeClient.instances.append(self)

    async def connect(self) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True

    async def subscribe_resource(self, uri: str, callback: Any) -> Any:
        self.subscriptions.append(uri)

        async def unsubscribe() -> None:
            return None

        return unsubscribe

    async def read_resource(self, uri: str) -> Any:
        page = self.pages.pop(0)
        return None if page is None else [_FakeContent(json.dumps(page, ensure_ascii=False))]


def _message(
    cursor: int,
    text: str,
    *,
    sender: str = "Steve",
    system: bool = False,
    from_self: bool = False,
) -> Dict[str, Any]:
    """一条聊天流条目（形状与 Mod 的 ChatMonitor 发布一致）。"""
    data: Dict[str, Any] = {"message": text, "system": system, "untrusted_external_text": True}
    if not system:
        data.update({"sender_name": sender, "sender_id": f"uuid-{sender}", "from_self": from_self})
    return {
        "cursor": cursor,
        "type": "game.message_received" if system else "player.chat_received",
        "timestamp": "2026-10-05T14:00:00Z",
        "message": "Received untrusted external player chat.",
        "data": data,
    }


def _page(messages: List[Dict[str, Any]], *, stream_id: str = "stream-A") -> Dict[str, Any]:
    latest = max((entry["cursor"] for entry in messages), default=0)
    return {"stream_id": stream_id, "cursor": latest, "latest_cursor": latest, "messages": messages}


@pytest.fixture(autouse=True)
def _patch_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """采集器在 _ensure_ready 内函数级 import McpClient，这里换成替身。"""
    import src.modules.mcp.client as client_module

    _FakeClient.instances = []
    monkeypatch.setattr(client_module, "McpClient", _FakeClient)


async def _ready(bus: _FakeEventBus, *pages: Optional[Dict[str, Any]]) -> MaicraftChatCollector:
    collector = MaicraftChatCollector(config={}, event_bus=bus)  # type: ignore[arg-type]
    assert await collector._ensure_ready()
    _FakeClient.instances[-1].pages.extend(pages)
    return collector


def test_collector_metadata_and_registration() -> None:
    """注册名、框架登记与默认启用：游戏里的聊天开箱就能被主播听到。"""
    assert issubclass(MaicraftChatCollector, BaseCollector)
    assert MaicraftChatCollector.name == "maicraft_chat"
    assert "maicraft_chat" in SUPPORTED_COLLECTORS
    assert instantiate_collector("maicraft_chat", {}, None) is not None
    assert "maicraft_chat" in CollectorsRootConfig().enabled
    cfg = MaicraftChatCollector.ConfigSchema()
    assert cfg.chatflow_uri == "maicraft://chatflow" and cfg.url.endswith("/mcp")


@pytest.mark.asyncio
async def test_first_read_only_aligns_cursor() -> None:
    """首次接入时聊天区已有的话是历史，不当成刚有人说话。"""
    bus = _FakeEventBus()
    collector = await _ready(bus, _page([_message(1, "早上好"), _message(2, "有人吗")]))
    assert _FakeClient.instances[-1].subscriptions == ["maicraft://chatflow"]

    await collector._drain()

    assert bus.events == []


@pytest.mark.asyncio
async def test_new_messages_forwarded_without_own_echo() -> None:
    """之后只转出新消息：别人的聊天与系统消息转出，AI 玩家自己的回显不转出。"""
    bus = _FakeEventBus()
    history = [_message(1, "早上好")]
    later = history + [
        _message(2, "麦麦你在干嘛呀"),
        _message(3, "我在做蜂蜜胶", sender="麦麦", from_self=True),
        _message(4, "Alex 请求传送到你这里", system=True),
    ]
    collector = await _ready(bus, _page(history), _page(later), _page(later))

    await collector._drain()
    await collector._drain()
    await collector._drain()  # 同一页再读一遍：没有新消息就不重复转出

    assert [name for name, _ in bus.events] == [CoreEvents.GAME_CHAT_RECEIVED] * 2
    player, system = (payload for _, payload in bus.events)
    assert isinstance(player, GameChatPayload)
    assert (player.kind, player.sender, player.sender_id, player.content) == (
        "player",
        "Steve",
        "uuid-Steve",
        "麦麦你在干嘛呀",
    )
    assert player.game == "minecraft" and player.occurred_at_ms > 0
    assert (system.kind, system.sender, system.content) == ("system", "", "Alex 请求传送到你这里")


@pytest.mark.asyncio
async def test_new_stream_messages_are_forwarded() -> None:
    """Mod 换世界换了流编号：新流里的消息都发生在上次读取之后，照常转出。"""
    bus = _FakeEventBus()
    collector = await _ready(
        bus,
        _page([_message(7, "旧世界的话")]),
        _page([_message(1, "新世界你好")], stream_id="stream-B"),
    )

    await collector._drain()
    await collector._drain()

    assert [payload.content for _, payload in bus.events] == ["新世界你好"]


@pytest.mark.asyncio
async def test_broken_page_keeps_cursor() -> None:
    """页面残缺不能被当成"没人说话"：抛错交给循环重连，游标不动，下一页照常转出。"""
    bus = _FakeEventBus()
    collector = await _ready(
        bus,
        _page([_message(1, "早上好")]),
        {"stream_id": "stream-A"},
        _page([_message(1, "早上好"), _message(2, "等你好久了")]),
    )

    await collector._drain()
    with pytest.raises(ValueError):
        await collector._drain()
    await collector._drain()

    assert [payload.content for _, payload in bus.events] == ["等你好久了"]
