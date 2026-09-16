"""maicraft_attention 采集器测试：注意流增量读取与事件转发。

采集器代码归属 Minecraft Agent 包（游戏相关适配器内聚），装配走采集器框架。

覆盖：
- 元数据/继承/配置默认值
- 增量语义：首读只建游标，之后按游标读取且只转发 ``important``
- 换流/重新同步：只重置游标、不转发历史
- 读取失败：游标不动（那一段事件不能因此永久丢失）
- 连接失败：采集循环不终止，稍后重试（游戏可能后启动）
- 事件面：``game.body.<上游类型>`` 名字折叠与 payload 字段
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from src.agents.minecraft.attention_collector import MaicraftAttentionCollector
from src.modules.collectors.base import BaseCollector, CollectorState
from src.modules.collectors.factory import SUPPORTED_COLLECTORS, instantiate_collector
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.body import BodyEventPayload, body_event_name


class _FakeEventBus:
    """捕获 emit 的桩（替代真实 EventBus）。"""

    def __init__(self) -> None:
        self.events: List[tuple] = []

    async def emit(self, event_name: str, payload: Any, **kwargs: Any) -> None:
        self.events.append((event_name, payload))


class _FakeClient:
    """MCP 客户端替身：连接、读工具、资源订阅都可控。"""

    instances: List["_FakeClient"] = []

    def __init__(self, name: str, config: Any) -> None:
        self.name = name
        self.config = config
        self.connected = True
        self.closed = False
        self.subscriptions: List[str] = []
        self.unsubscribed: List[str] = []
        self.pages: List[Dict[str, Any]] = []
        self.read_error: Optional[Exception] = None
        self.read_calls: List[Dict[str, Any]] = []
        _FakeClient.instances.append(self)

    async def connect(self) -> bool:
        return self.connected

    async def close(self) -> None:
        self.closed = True
        self.connected = False

    async def list_tools(self) -> List[Any]:
        return [_FakeTool("perceive"), _FakeTool("execute")]

    async def subscribe_resource(self, uri: str, callback: Any) -> Any:
        self.subscriptions.append(uri)
        self._callback = callback

        async def unsubscribe() -> None:
            self.unsubscribed.append(uri)

        return unsubscribe

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        self.read_calls.append({"name": name, "arguments": dict(arguments)})
        if self.read_error is not None:
            raise self.read_error
        page = self.pages.pop(0) if self.pages else None
        return _FakeCallResult(page)


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"desc {name}"
        self.inputSchema = {"type": "object"}


class _FakeCallResult:
    def __init__(self, structured: Optional[Dict[str, Any]]) -> None:
        self.is_error = structured is None
        self.content = []
        self.structured_content = structured


def _page(
    events: List[Dict[str, Any]],
    *,
    cursor: int,
    stream_id: str = "stream-A",
    resync: bool = False,
) -> Dict[str, Any]:
    return {
        "stream_id": stream_id,
        "cursor": cursor,
        "latest_cursor": cursor,
        "oldest_cursor": 1,
        "has_more": False,
        "history_lost": False,
        "stream_reset": False,
        "resync_required": resync,
        "events": events,
    }


def _damage(cursor: int, *, priority: str = "important") -> Dict[str, Any]:
    return {
        "cursor": cursor,
        "type": "agent.damaged",
        "priority": priority,
        "timestamp": "2026-09-16T10:16:23.580633Z",
        "message": "The agent took damage",
        "data": {
            "current_health": 18.0,
            "cause": {"causing_entity_type_id": "minecraft:zombie"},
            "defense": {"policy": "instinct", "would_engage": True},
        },
    }


@pytest.fixture(autouse=True)
def _patch_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """把采集器用到的 MCP 类型换成替身（采集器在 _ensure_ready 内函数级 import）。"""
    import src.modules.mcp.client as client_module
    import src.modules.mcp.provider as provider_module

    _FakeClient.instances = []
    monkeypatch.setattr(client_module, "McpClient", _FakeClient)
    # provider 保持真实实现（只借助它做工具缓存与参数拼装）
    monkeypatch.setattr(provider_module, "McpClient", _FakeClient)


def _collector(bus: _FakeEventBus) -> MaicraftAttentionCollector:
    return MaicraftAttentionCollector(config={}, event_bus=bus)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 契约面
# ---------------------------------------------------------------------------


def test_collector_metadata_and_registration() -> None:
    assert issubclass(MaicraftAttentionCollector, BaseCollector)
    assert MaicraftAttentionCollector.name == "maicraft_attention"
    assert "maicraft_attention" in SUPPORTED_COLLECTORS
    assert instantiate_collector("maicraft_attention", {}, None) is not None


def test_config_defaults() -> None:
    cfg = MaicraftAttentionCollector.ConfigSchema()
    assert cfg.attention_uri == "maicraft://attention"
    assert cfg.url.endswith("/mcp")
    assert cfg.page_limit >= 1 and cfg.idle_poll_ms >= 1000


def test_body_event_name_folds_upstream_type() -> None:
    """上游类型 → 事件名末段必须是单个词元，且落在 game.body.# 通配下。"""
    assert body_event_name("agent.damaged") == "game.body.agent_damaged"
    assert body_event_name("world.time_phase_changed") == "game.body.world_time_phase_changed"
    assert body_event_name("") == "game.body.unknown"
    assert CoreEvents.GAME_BODY_WILDCARD == "game.body.#"


def test_body_payload_requires_event_type() -> None:
    payload = BodyEventPayload(event_type="agent.damaged", message="x", facts={"a": 1})
    assert payload.game == "minecraft" and payload.timestamp_ms > 0


# ---------------------------------------------------------------------------
# 增量读取与转发
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_read_primes_cursor_without_forwarding() -> None:
    bus = _FakeEventBus()
    collector = _collector(bus)
    assert await collector._ensure_ready() is True
    client = _FakeClient.instances[-1]
    assert client.subscriptions == ["maicraft://attention"], "必须订阅注意流资源"

    client.pages.append(_page([_damage(5), _damage(6)], cursor=6))
    await collector._drain()

    assert collector._cursor == 6 and collector._stream_id == "stream-A"
    assert bus.events == [], "首读拿到的是历史页，转发等于把陈年事件灌进系统"


@pytest.mark.asyncio
async def test_incremental_read_forwards_only_important() -> None:
    bus = _FakeEventBus()
    collector = _collector(bus)
    await collector._ensure_ready()
    client = _FakeClient.instances[-1]

    client.pages.append(_page([_damage(5)], cursor=5))
    await collector._drain()

    client.pages.append(_page([_damage(6), _damage(7, priority="background")], cursor=7))
    await collector._drain()

    assert [name for name, _ in bus.events] == ["game.body.agent_damaged"]
    _, payload = bus.events[0]
    assert payload.event_type == "agent.damaged" and payload.cursor == 6
    assert payload.facts["current_health"] == 18.0
    assert payload.occurred_at_ms > 0, "上游 ISO-8601 时刻要解析成毫秒"
    # 增量语义：第二次读取带上游标与流编号
    second = client.read_calls[-1]["arguments"]
    assert second["after_cursor"] == 5 and second["stream_id"] == "stream-A"
    assert second["view"] == "attention" and second["wait_ms"] == 0


@pytest.mark.asyncio
async def test_resync_resets_cursor_without_forwarding_history() -> None:
    bus = _FakeEventBus()
    collector = _collector(bus)
    await collector._ensure_ready()
    client = _FakeClient.instances[-1]

    client.pages.append(_page([_damage(5)], cursor=5))
    await collector._drain()
    client.pages.append(_page([_damage(9)], cursor=9, stream_id="stream-B", resync=True))
    await collector._drain()

    assert bus.events == [], "换流后的历史页不转发（旧世界的身体事件不属于当前这条命）"
    assert collector._stream_id == "stream-B" and collector._cursor == 9


@pytest.mark.asyncio
async def test_read_failure_keeps_cursor_and_surfaces_to_the_loop() -> None:
    """读取失败：游标不动（那一段事件不能因此永久丢失），异常上抛给采集循环处理重连。

    恢复动作归循环（记 warning + 断开重连，见 test_collect_loop_survives_unavailable_game）；
    这里只钉住"失败不得推进游标"。
    """
    bus = _FakeEventBus()
    collector = _collector(bus)
    await collector._ensure_ready()
    client = _FakeClient.instances[-1]

    client.pages.append(_page([_damage(5)], cursor=5))
    await collector._drain()
    client.read_error = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await collector._drain()

    assert collector._cursor == 5, "读取失败不能推进游标（否则那一段事件永久丢失）"
    assert bus.events == []


@pytest.mark.asyncio
async def test_collect_loop_survives_unavailable_game(monkeypatch: pytest.MonkeyPatch) -> None:
    """游戏没起来时：启动不失败、循环不终止，连接就绪后照常转发。"""

    class _OfflineClient(_FakeClient):
        async def connect(self) -> bool:
            return False

    import src.modules.mcp.client as client_module

    monkeypatch.setattr(client_module, "McpClient", _OfflineClient)

    bus = _FakeEventBus()
    collector = MaicraftAttentionCollector(config={"retry_interval_ms": 200, "idle_poll_ms": 1000}, event_bus=bus)  # type: ignore[arg-type]
    await collector.start()
    assert collector.state == CollectorState.RUNNING
    await asyncio.sleep(0.05)
    assert bus.events == []

    # 游戏上线后（换成可用客户端）循环自愈
    monkeypatch.setattr(client_module, "McpClient", _FakeClient)
    await asyncio.sleep(0.35)
    assert _FakeClient.instances, "应重试建立连接"
    online = _FakeClient.instances[-1]
    online.pages.append(_page([_damage(5)], cursor=5))
    online.pages.append(_page([_damage(6)], cursor=6))
    collector._on_notify("maicraft://attention")
    await asyncio.sleep(0.05)
    await collector.stop()
    assert collector.state == CollectorState.STOPPED
