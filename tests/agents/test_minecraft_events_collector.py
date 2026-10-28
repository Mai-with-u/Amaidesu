"""本文件测 MaicraftEventsCollector（MaiCraft v1 事件流长轮询采集器基类）的核心行为：
连接就绪、首读只建游标、增量转发、换流重新同步、读失败游标不动。"""

from __future__ import annotations

from typing import Any, Dict, List

from src.agents.minecraft.events_collector import MaicraftEventsCollector

from .minecraft_collector_fakes import FakeEventBus as _FakeEventBus
from .minecraft_collector_fakes import FakeMcpClient as _FakeClient
from .minecraft_collector_fakes import events_page as _page
from .minecraft_collector_fakes import fake_client_factory


class _RecordingCollector(MaicraftEventsCollector):
    """记录转发事件的采集器替身：基类行为不变，只把每条事件收进列表。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.forwarded: List[Dict[str, Any]] = []

    async def _forward(self, event: Dict[str, Any]) -> bool:
        self.forwarded.append(event)
        return True


async def _collector(bus: _FakeEventBus) -> tuple[_RecordingCollector, _FakeClient]:
    """接替身客户端的采集器（MCP 客户端经构造工厂注入），已确保连接就绪。"""
    collector = _RecordingCollector(config={}, event_bus=bus, client_factory=fake_client_factory())  # type: ignore[arg-type]
    assert await collector._ensure_ready() is True
    return collector, _FakeClient.instances[-1]


def test_config_defaults_and_schema_constructs_without_arguments() -> None:
    """配置可默认构造：连接参数与 Agent 的 MCP 段同源，等待时长在 MaiCraft 允许的区间内。"""
    cfg = MaicraftEventsCollector.ConfigSchema()
    assert cfg.mcp.url.endswith("/mcp")
    assert 1000 <= cfg.wait_ms <= 55_000
    assert cfg.retry_interval_ms >= 200


async def test_first_read_only_establishes_the_cursor_then_events_are_forwarded() -> None:
    """首读只建游标不转发积压事件；之后带游标长轮询，新事件逐条转发、游标推进。"""
    collector, client = await _collector(_FakeEventBus())
    client.pages.append(_page([{"cursor": 5, "kind": "hurt", "goal_id": -1, "message": "摔了一下"}], cursor=5))
    await collector._drain()
    assert collector.forwarded == [], "接手前的事件不补发"
    assert collector._cursor == 5

    client.pages.append(
        _page(
            [
                {"cursor": 6, "kind": "hurt", "goal_id": -1, "message": "又摔了一下"},
                {"cursor": 7, "kind": "sky_changed", "goal_id": -1, "message": "天黑了"},
            ],
            cursor=7,
        )
    )
    await collector._drain()
    assert [event["cursor"] for event in collector.forwarded] == [6, 7]
    assert collector._cursor == 7
    follow_up = client.read_calls[-1]["arguments"]
    assert follow_up["stream_id"] == "stream-A" and follow_up["after_cursor"] == 5, "增量读取带流编号与游标"


async def test_stream_change_resyncs_and_skips_events_before_the_new_cursor() -> None:
    """事件流换了编号（换世界、Mod 重启）：游标对到新流的最新位置，换流后的历史不转发。"""
    collector, client = await _collector(_FakeEventBus())
    client.pages.append(_page([], cursor=3))
    await collector._drain()

    client.pages.append(
        _page(
            [{"cursor": 9, "kind": "hurt", "goal_id": -1, "message": "新世界里的旧事件"}],
            cursor=9,
            stream_id="stream-B",
        )
    )
    await collector._drain()
    assert collector.forwarded == [], "换流不转发换流前的历史"
    assert collector._stream_id == "stream-B" and collector._cursor == 9

    client.pages.append(
        _page([{"cursor": 10, "kind": "hurt", "goal_id": -1, "message": "新事件"}], cursor=10, stream_id="stream-B")
    )
    await collector._drain()
    assert [event["cursor"] for event in collector.forwarded] == [10]


async def test_read_failure_keeps_the_cursor_so_events_are_not_lost() -> None:
    """一页读失败：游标不动（那一段事件不因读取失败永久丢失），由采集循环重连后重读。"""
    collector, client = await _collector(_FakeEventBus())
    client.pages.append(_page([{"cursor": 4, "kind": "hurt", "goal_id": -1, "message": "摔了一下"}], cursor=4))
    await collector._drain()

    client.read_error = RuntimeError("连接中断")
    try:
        await collector._read({"stream_id": "stream-A", "after_cursor": 4, "wait_ms": 0})
    except RuntimeError:
        pass
    assert collector._cursor == 4, "读失败游标不动"
    await collector._teardown()
    assert client.closed and collector._client is None, "拆连接后下一轮重新建立，游标保留"
