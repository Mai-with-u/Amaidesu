"""maicraft_chat 采集器测试：长轮询 MaiCraft v1 的聊天事件流，把别人说的话转成 game.chat.received。

采集器代码归属 Minecraft Agent 包（游戏相关适配器内聚），装配走采集器框架。
覆盖：
- 元数据/注册/配置默认值
- 读的是 events(topic=chat)；首次读取只建游标，接手前的聊天不补发
- 玩家聊天带发言人与 UUID，系统消息不带发言人，截断标记照传
- 私聊照常转出并标 private；空白消息不转，游标照样往后走
- 服务器把玩家聊天当系统消息发来："<账号名> 正文"认成玩家发言，角色自己的回显不转；
  还不知道角色账号名时照系统消息转出
- 没有发言人 UUID 的玩家消息：Mod 标了发言人此刻在线的不附说明，没标的和兜底认出来的附"没有发言人凭据"
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from src.agents.minecraft.chat_attribution import (
    NOTE_SHOWN_AS_SELF,
    NOTE_SPEAKER_UNCONFIRMED,
    NOTE_UNVERIFIED,
    attribute_chat,
    line_speaker,
)
from src.agents.minecraft.chat_collector import MaicraftChatCollector
from src.modules.collectors.base import BaseCollector
from src.modules.agents.companions import build_companion_collectors, owner_of
from src.modules.collectors.factory import SUPPORTED_COLLECTORS
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatPayload

from .minecraft_collector_fakes import FakeEventBus, FakeMcpClient, events_page, fake_client_factory

_STEVE_ID = "8667ba71-b85a-4004-af54-457a9734eed7"


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
    collector = MaicraftChatCollector(  # type: ignore[arg-type]
        config=config or {}, event_bus=bus, client_factory=fake_client_factory()
    )
    assert await collector._ensure_ready() is True
    client = FakeMcpClient.instances[-1]
    client.pages.append(events_page([_chat(3, "接手前说的话")], cursor=3))
    await collector._drain()
    return collector, client, bus


def test_attribution_tells_machine_lines_from_people() -> None:
    """没有发言人 UUID 的"玩家消息"名字只是显示出来的：标无凭据；Mod 判成借角色名义发的，说明不是谁说的话。"""
    maid_pos = '<Alex> [name:"winefox_momo", x:-356, y:63, z:36, dim:minecraft:overworld]'
    unsigned = attribute_chat({"kind": "player", "sender": "Alex", "text": maid_pos})
    assert (unsigned.kind, unsigned.sender, unsigned.note) == ("player", "Alex", NOTE_UNVERIFIED)
    assert unsigned.content.startswith('[name:"winefox_momo"'), "聊天格式加的 <名字> 前缀去掉"

    signed = attribute_chat({"kind": "player", "sender": "Steve", "sender_id": _STEVE_ID, "text": "<Steve> 嗨"})
    assert (signed.note, signed.content) == ("", "<Steve> 嗨"), "带 UUID 的是真发言，原话不动"
    marked = attribute_chat({"kind": "system", "text": "[name:...]", "shown_as": "Alex"})
    assert (marked.kind, marked.note) == ("system", NOTE_SHOWN_AS_SELF), "Mod 已改判的照它的标记说明"
    assert attribute_chat({"kind": "system", "text": "Alex 加入了游戏"}).note == ""


def test_note_follows_whether_the_speaker_was_checked() -> None:
    """附不附"没有发言人凭据"只看发言人核对过没有：有 UUID 或 Mod 标了此刻在线的不附，其余都附。"""
    # 有发言人 UUID：照旧当已核对
    signed = attribute_chat({"kind": "player", "sender": "Steve", "sender_id": _STEVE_ID, "text": "嗨"})
    assert signed.note == ""
    # 没签名的服务器转来的公屏聊天：Mod 对过在线玩家列表，按这个人说的话转出
    checked = attribute_chat({"kind": "player", "sender": "Steve", "sender_online": True, "text": "不是我的"})
    assert (checked.kind, checked.sender, checked.content, checked.note) == ("player", "Steve", "不是我的", "")
    # 命令方块的 /say 只借了个名字；旧版 Mod 不给这个字段：都附说明
    say = {"kind": "player", "sender": "Server", "sender_online": False, "text": "[Server] 十分钟后重启"}
    assert attribute_chat(say).note == NOTE_UNVERIFIED
    old_mod = attribute_chat({"kind": "player", "sender": "Steve", "text": "不是我的"})
    assert old_mod.note == NOTE_UNVERIFIED
    # 宿主从系统消息兜底认出来的：没对过在线玩家列表，固定附说明
    fallback = attribute_chat({"kind": "system", "text": "<Steve> 不是我的"}, self_name="Alex")
    assert (fallback.kind, fallback.sender, fallback.note) == ("player", "Steve", NOTE_UNVERIFIED)


def test_chat_lines_sent_as_system_messages_are_attributed() -> None:
    """服务器把玩家聊天当系统消息发来："<账号名> 正文"认成玩家发言；是角色自己的名字就是回显，不转。"""
    other = attribute_chat({"kind": "system", "text": "<Steve> 不是我的"}, self_name="Alex")
    assert other is not None
    assert (other.kind, other.sender, other.sender_id, other.content, other.note) == (
        "player",
        "Steve",
        "",
        "不是我的",
        NOTE_UNVERIFIED,
    )
    assert attribute_chat({"kind": "system", "text": "<alex> 你好"}, self_name="Alex") is None, "账号名不分大小写"
    # 还不知道自己叫什么：分不清是不是回显，照系统消息转出、原话不动，不当成别人搭话
    unknown_self = attribute_chat({"kind": "system", "text": "<Alex> 你好"})
    assert unknown_self is not None
    assert (unknown_self.kind, unknown_self.sender, unknown_self.content, unknown_self.note) == (
        "system",
        "",
        "<Alex> 你好",
        NOTE_SPEAKER_UNCONFIRMED,
    )
    # 不是这种整行的照普通系统消息：死亡播报、带称号的格式、Mod 已标出借名的
    for line in ({"kind": "system", "text": "Steve被杀死了"}, {"kind": "system", "text": "<[VIP]Bob> hi"}):
        kept = attribute_chat(line, self_name="Alex")
        assert kept is not None and (kept.kind, kept.content) == ("system", line["text"])
    assert line_speaker({"kind": "system", "text": "<Alex> x", "shown_as": "Alex"}) == ""
    assert line_speaker({"kind": "player", "sender": "Steve", "text": "<Steve> x"}) == ""


async def test_collector_drops_own_echo_and_reads_account_once() -> None:
    """账号名在这条连接上只读一次；自己的回显不转，别人的话按玩家发言转出；重连后重新认。"""
    collector, client, bus = await _primed()
    client.replies["observe"] = {"ok": True, "data": {"name": "Alex"}}
    client.pages.append(
        events_page(
            [
                {"cursor": 4, "kind": "system", "text": "<Alex> 你好，这块墓碑是你的吗"},
                {"cursor": 5, "kind": "system", "text": "<Steve> 不是我的"},
                {"cursor": 6, "kind": "system", "text": "Steve加入了游戏"},
            ],
            cursor=6,
        )
    )
    await collector._drain()

    assert [(p.kind, p.sender, p.content) for _, p in bus.events] == [
        ("player", "Steve", "不是我的"),
        ("system", "", "Steve加入了游戏"),
    ]
    assert [call["name"] for call in client.read_calls].count("observe") == 1

    await collector._teardown()
    assert await collector._ensure_ready() is True
    reconnected = FakeMcpClient.instances[-1]
    reconnected.replies["observe"] = {"ok": True, "data": {"name": "Steve"}}
    reconnected.pages.append(events_page([{"cursor": 7, "kind": "system", "text": "<Steve> 换号了"}], cursor=7))
    await collector._drain()
    assert len(bus.events) == 2, "重连后按新读到的账号名认回显"


async def test_unknown_account_keeps_line_as_system_and_retries() -> None:
    """读不到账号名时这一行照系统消息转出、不当成别人搭话，下一行再试着读。"""
    collector, client, bus = await _primed()
    client.replies["observe"] = {"ok": False, "error": {"code": "not_in_world", "message": "不在世界里"}}
    client.pages.append(events_page([{"cursor": 4, "kind": "system", "text": "<Alex> 在吗"}], cursor=4))
    await collector._drain()
    [(_, payload)] = bus.events
    assert (payload.kind, payload.sender, payload.note) == ("system", "", NOTE_SPEAKER_UNCONFIRMED)

    client.replies["observe"] = {"ok": True, "data": {"name": "Alex"}}
    client.pages.append(events_page([{"cursor": 5, "kind": "system", "text": "<Alex> 在吗"}], cursor=5))
    await collector._drain()
    assert len(bus.events) == 1, "读到账号名后认出是自己的回显，不再转出"
    assert [call["name"] for call in client.read_calls].count("observe") == 2


async def test_unsigned_line_is_forwarded_with_a_note() -> None:
    collector, client, bus = await _primed()
    line = {"cursor": 4, "kind": "player", "sender": "Alex", "text": '<Alex> [name:"x"]'}
    client.pages.append(events_page([line], cursor=4))
    await collector._drain()
    [(_, payload)] = bus.events
    assert payload.kind == "player" and payload.sender == "Alex"
    assert payload.content == '[name:"x"]' and "没有发言人凭据" in payload.note


async def test_checked_line_is_forwarded_without_a_note() -> None:
    collector, client, bus = await _primed()
    line = {"cursor": 4, "kind": "player", "sender": "Steve", "sender_online": True, "text": "不是我的"}
    client.pages.append(events_page([line], cursor=4))
    await collector._drain()
    [(_, payload)] = bus.events
    assert (payload.kind, payload.sender, payload.content, payload.note) == ("player", "Steve", "不是我的", "")


async def test_death_line_carries_who_died_and_its_template() -> None:
    """死亡播报的死者、凶手按出现先后带 UUID 转出，模板编号一并转出；没有 UUID 的点名不要。"""
    collector, client, bus = await _primed()
    line = {
        "cursor": 4,
        "kind": "system",
        "text": "莫莫酒狐被苦力怕炸死了",
        "mentions": [
            {"name": "莫莫酒狐", "type": "touhou_little_maid:maid", "uuid": "5f2b9c1e-0d4a-4c1b-9e7f-1a2b3c4d5e6f"},
            {"name": "苦力怕", "type": "minecraft:creeper", "uuid": "0f0e0d0c-0b0a-4908-8706-050403020100"},
            {"name": "无名"},
        ],
        "translation_key": "death.attack.explosion.player",
    }
    client.pages.append(events_page([line], cursor=4))
    await collector._drain()
    [(_, payload)] = bus.events
    assert [(m.name, m.id) for m in payload.mentions] == [
        ("莫莫酒狐", "5f2b9c1e-0d4a-4c1b-9e7f-1a2b3c4d5e6f"),
        ("苦力怕", "0f0e0d0c-0b0a-4908-8706-050403020100"),
    ]
    assert payload.message_key == "death.attack.explosion.player"


def test_collector_metadata_and_registration() -> None:
    assert issubclass(MaicraftChatCollector, BaseCollector)
    assert MaicraftChatCollector.name == "maicraft_chat"
    assert "maicraft_chat" not in SUPPORTED_COLLECTORS
    assert owner_of("maicraft_chat") == "minecraft"
    names = [collector.name for collector in build_companion_collectors("minecraft", {})]
    assert "maicraft_chat" in names
    cfg = MaicraftChatCollector.ConfigSchema()
    assert cfg.mcp.url.endswith("/mcp")


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
    # 旧版 Mod 没报在线核对、系统消息没有发言人：都按没核对过
    assert (said.sender_online, joined.sender_online) == (False, False)
    assert client.read_calls[-1]["arguments"] == {
        "topic": "chat",
        "stream_id": "stream-A",
        "after_cursor": 3,
        "wait_ms": 25_000,
    }


async def test_the_online_check_from_the_game_is_passed_on() -> None:
    """Mod 核对过发言人此刻在线的照转；控制台、命令方块借名发的是 False，存储不为它建玩家。"""
    collector, client, bus = await _primed()
    checked = _chat(4, "来切磋")
    checked["sender_online"] = True
    borrowed = {"cursor": 5, "kind": "player", "sender": "Server", "sender_online": False, "text": "[Server] 重启"}
    client.pages.append(events_page([checked, borrowed], cursor=5))

    await collector._drain()

    first, second = (payload for _, payload in bus.events)
    assert (first.sender_id, first.sender_online) == (_STEVE_ID, True)
    assert (second.sender, second.sender_id, second.sender_online) == ("Server", "", False)


async def test_chat_carries_the_save_of_its_page() -> None:
    """聊天带着这一页所在的存档：存储据此记在哪个存档见过这位玩家；旧版 Mod 的页没有 world，存档留空。"""
    collector, client, bus = await _primed()
    page = events_page([_chat(4, "来切磋")], cursor=4)
    page["world"] = {"id": "aaa", "name": "老家", "source": "world_id_file"}
    client.pages.append(page)
    client.pages.append(events_page([_chat(5, "还在吗")], cursor=5))

    await collector._drain()
    await collector._drain()

    assert [(payload.save_id, payload.save_name) for _, payload in bus.events] == [("aaa", "老家"), ("", "")]


async def test_private_messages_are_forwarded_and_marked() -> None:
    collector, client, bus = await _primed()
    client.pages.append(events_page([_chat(4, "悄悄跟你说", private=True), _chat(5, "   ")], cursor=5))

    await collector._drain()

    # 私聊照转并标出来，主播据此不在直播里念；空白消息不转，游标照样往后走。
    assert [(payload.content, payload.private) for _, payload in bus.events] == [("悄悄跟你说", True)]
    assert collector._cursor == 5
