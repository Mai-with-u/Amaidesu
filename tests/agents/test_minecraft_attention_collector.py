"""maicraft_attention 采集器测试：长轮询 MaiCraft v1 的任务事件流，把身体遭遇转成 game.body.* 事件。

采集器代码归属 Minecraft Agent 包（游戏相关适配器内聚），装配走采集器框架。

覆盖：
- 元数据/继承/配置默认值
- 分类：临时任务开始与结束、处理不了的需求进叙事通道，目标运行的处境变化不进
- 摘要：Mod 那句话去掉坐标，坐标这类遥测不进事件
- 增量语义：首读只建游标（翻过积压页），之后带流编号与游标长轮询
- 换流：只重置游标、不转发换流前后的历史
- 读取失败：游标不动（那一段事件不能因此永久丢失）
- 连接失败：采集循环不终止，稍后重试（游戏可能后启动）
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import pytest
from pydantic import ValidationError

from src.agents.minecraft.attention_collector import MaicraftAttentionCollector
from src.agents.minecraft.attention_matrix import KIND_TO_EVENT, classify, summarize, without_coordinates
from src.modules.collectors.base import BaseCollector, CollectorState
from src.modules.collectors.factory import SUPPORTED_COLLECTORS, instantiate_collector
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.body import BodyEventPayload

from .minecraft_collector_fakes import FakeEventBus as _FakeEventBus
from .minecraft_collector_fakes import FakeMcpClient as _FakeClient
from .minecraft_collector_fakes import events_page as _page
from .minecraft_collector_fakes import patch_mcp


def _event(cursor: int, kind: str, message: str = "", goal_id: int = -1) -> Dict[str, Any]:
    return {"cursor": cursor, "kind": kind, "goal_id": goal_id, "message": message}


@pytest.fixture(autouse=True)
def _patch_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_mcp(monkeypatch)


def _collector(bus: _FakeEventBus) -> MaicraftAttentionCollector:
    return MaicraftAttentionCollector(config={}, event_bus=bus)  # type: ignore[arg-type]


async def _primed(bus: _FakeEventBus, cursor: int = 5) -> tuple[MaicraftAttentionCollector, _FakeClient]:
    collector = _collector(bus)
    assert await collector._ensure_ready() is True
    client = _FakeClient.instances[-1]
    client.pages.append(_page([], cursor=cursor))
    await collector._drain()
    return collector, client


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
    assert cfg.url.endswith("/mcp")
    assert 1000 <= cfg.wait_ms <= 55_000


def test_classification_keeps_body_encounters_and_drops_goal_progress() -> None:
    """分类表：身体先处理的急事与处理不了的需求进叙事通道，目标运行的处境变化不进。"""
    assert classify("temporary_task_started") == "reflex_started"
    assert classify("temporary_task_finished") == "reflex_finished"
    assert classify("need_unhandled") == "need_unhandled"
    assert classify("character_died") == "died", "角色死亡归已有的 died 事件"
    for goal_kind in ("started", "asked", "paused", "resumed", "step_finished", "finished", "death_recovery_applied"):
        assert classify(goal_kind) is None, f"{goal_kind} 是游戏 Agent 的任务通道"
    assert classify("something_new") == "unknown", "上游新增种类不丢，但事件面保持封闭"
    assert classify("") is None


def test_summary_drops_coordinates_and_keeps_the_rest() -> None:
    """摘要只陈述事件里有的事实，带坐标的分句整句去掉，不留"停在了"这种残片。"""
    assert (
        summarize("reflex_started", "temporary_task_started", "被威胁，插入自卫：打点在 3, 64, -9")
        == "身体先停下手上的活处理急事：被威胁，插入自卫"
    )
    assert (
        without_coordinates("自卫结束但走不回打点（还差 7 格），停在了 3, 64, 9；主任务已暂停，等下一步指示")
        == "自卫结束但走不回打点（还差 7 格），主任务已暂停，等下一步指示"
    )
    assert summarize("reflex_finished", "temporary_task_finished", "退回了安全处，继续干活") == (
        "身体处理完急事：退回了安全处，继续干活"
    )
    assert summarize("need_unhandled", "need_unhandled", "") == "身体遇到处理不了的事"
    assert summarize("died", "character_died", "角色死了，等重生") == "角色倒下了：角色死了，等重生"
    assert summarize("unknown", "something_new", "") == "身体事件：something_new"


def test_body_event_names_are_the_closed_kind_set() -> None:
    """9 个具名事件与判别字段一一对应，通配订阅仍然可用。"""
    assert CoreEvents.GAME_BODY_WILDCARD == "game.body.#"
    assert set(KIND_TO_EVENT.values()) == {
        CoreEvents.GAME_BODY_ATTACKED,
        CoreEvents.GAME_BODY_ATTACK_ENDED,
        CoreEvents.GAME_BODY_DIED,
        CoreEvents.GAME_BODY_RESPAWNED,
        CoreEvents.GAME_BODY_REFLEX_STARTED,
        CoreEvents.GAME_BODY_REFLEX_FINISHED,
        CoreEvents.GAME_BODY_DIMENSION_CHANGED,
        CoreEvents.GAME_BODY_NEED_UNHANDLED,
        CoreEvents.GAME_BODY_UNKNOWN,
    }
    for kind, event_name in KIND_TO_EVENT.items():
        assert event_name.rsplit(".", 1)[-1] == kind, "判别字段必须等于事件名末段"


def test_body_payload_requires_game_and_kind() -> None:
    """游戏标识由发布方给定（框架不假定是哪款游戏），缺 game 直接报错。"""
    payload = BodyEventPayload(game="minecraft", kind="reflex_started", summary="身体先停下手上的活处理急事")
    assert payload.game == "minecraft" and payload.timestamp_ms > 0
    with pytest.raises(ValidationError):
        BodyEventPayload(kind="reflex_started", summary="身体先停下手上的活处理急事")


# ---------------------------------------------------------------------------
# 增量读取与转发
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_read_skips_the_backlog_without_forwarding() -> None:
    """首读拿到的是流里最早保留的事件：翻到最新再开始，陈年事件不灌进系统。"""
    bus = _FakeEventBus()
    collector = _collector(bus)
    assert await collector._ensure_ready() is True
    client = _FakeClient.instances[-1]
    client.pages.append(_page([_event(1, "temporary_task_started", "被威胁")], cursor=1, has_more=True))
    client.pages.append(_page([_event(2, "temporary_task_finished", "自卫结束")], cursor=2))

    await collector._drain()

    assert collector._cursor == 2 and collector._stream_id == "stream-A"
    assert bus.events == []
    assert client.read_calls[0]["arguments"] == {"wait_ms": 0}
    assert client.read_calls[1]["arguments"] == {"stream_id": "stream-A", "after_cursor": 1, "wait_ms": 0}


@pytest.mark.asyncio
async def test_incremental_read_forwards_only_body_encounters() -> None:
    """增量读取：只把身体遭遇转出去，目标运行的事件留给游戏 Agent。"""
    bus = _FakeEventBus()
    collector, client = await _primed(bus, cursor=5)
    client.pages.append(
        _page(
            [
                _event(6, "temporary_task_started", "被威胁，插入自卫：打点在 3, 64, -9"),
                _event(7, "finished", "砍了 5 块原木", goal_id=3),
                _event(8, "need_unhandled", "饿了但没吃上：身上没有食物；先回去继续干活"),
            ],
            cursor=8,
        )
    )

    await collector._drain()

    assert [name for name, _ in bus.events] == [
        CoreEvents.GAME_BODY_REFLEX_STARTED,
        CoreEvents.GAME_BODY_NEED_UNHANDLED,
    ]
    started, need = bus.events[0][1], bus.events[1][1]
    assert started.kind == "reflex_started" and started.source_event_type == "temporary_task_started"
    assert started.summary == "身体先停下手上的活处理急事：被威胁，插入自卫" and started.resolved is False
    assert need.summary.startswith("身体遇到处理不了的事：饿了但没吃上")
    # 坐标与游标这类遥测不入事件（比对时去掉随机 id，免得 uuid 里碰巧出现 "-9"）
    dumped = json.dumps([payload.model_dump(exclude={"id"}) for _, payload in bus.events], ensure_ascii=False)
    assert "-9" not in dumped and "cursor" not in dumped
    second = client.read_calls[-1]["arguments"]
    assert second == {"stream_id": "stream-A", "after_cursor": 5, "wait_ms": 25_000}
    assert collector._cursor == 8


@pytest.mark.asyncio
async def test_finished_temporary_task_is_marked_resolved() -> None:
    bus = _FakeEventBus()
    collector, client = await _primed(bus)
    client.pages.append(_page([_event(6, "temporary_task_finished", "自卫结束，回到打点；位移 2 格")], cursor=6))

    await collector._drain()

    name, payload = bus.events[0]
    assert name == CoreEvents.GAME_BODY_REFLEX_FINISHED and payload.resolved is True


@pytest.mark.asyncio
async def test_stream_change_resets_cursor_without_forwarding_history() -> None:
    """换了世界或 Mod 重启：事件流换新编号，只重新建立游标，旧流和新流的积压都不转发。"""
    bus = _FakeEventBus()
    collector, client = await _primed(bus, cursor=5)
    client.pages.append(
        _page([_event(1, "temporary_task_started", "被威胁")], cursor=1, stream_id="stream-B", status="stream_changed")
    )

    await collector._drain()

    assert bus.events == []
    assert collector._stream_id == "stream-B" and collector._cursor == 1


@pytest.mark.asyncio
async def test_read_failure_keeps_cursor_and_surfaces_to_the_loop() -> None:
    """读取失败：游标不动（那一段事件不能因此永久丢失），异常上抛给采集循环处理重连。"""
    bus = _FakeEventBus()
    collector, client = await _primed(bus, cursor=5)
    client.read_error = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await collector._drain()

    assert collector._cursor == 5
    assert bus.events == []


@pytest.mark.asyncio
async def test_mod_without_events_tool_is_not_ready(monkeypatch: pytest.MonkeyPatch, loguru_capture: Any) -> None:
    """连上的不是 MaiCraft v1（没有 events 工具）：不当成就绪，如实说明原因。"""

    class _OldModClient(_FakeClient):
        tool_names = ("perceive", "execute")

    import src.modules.mcp.client as client_module

    monkeypatch.setattr(client_module, "McpClient", _OldModClient)
    collector = _collector(_FakeEventBus())

    assert await collector._ensure_ready() is False
    assert any("events" in record["message"] for record in _unavailable_warnings(loguru_capture))


@pytest.mark.asyncio
async def test_collect_loop_survives_unavailable_game(monkeypatch: pytest.MonkeyPatch) -> None:
    """游戏没起来时：启动不失败、循环不终止，连接就绪后照常转发。"""

    class _OfflineClient(_FakeClient):
        async def connect(self) -> bool:
            return False

    import src.modules.mcp.client as client_module

    monkeypatch.setattr(client_module, "McpClient", _OfflineClient)

    bus = _FakeEventBus()
    collector = MaicraftAttentionCollector(config={"retry_interval_ms": 200, "wait_ms": 1000}, event_bus=bus)  # type: ignore[arg-type]
    await collector.start()
    assert collector.state == CollectorState.RUNNING
    await asyncio.sleep(0.05)
    assert bus.events == []

    # 游戏上线后（换成可用客户端）循环自愈
    monkeypatch.setattr(client_module, "McpClient", _FakeClient)
    await asyncio.sleep(0.35)
    assert _FakeClient.instances, "应重试建立连接"
    await collector.stop()
    assert collector.state == CollectorState.STOPPED


# ---------------------------------------------------------------------------
# 常驻重试：不刷屏、不硬撞
# ---------------------------------------------------------------------------


def _unavailable_warnings(cap: Any) -> List[Dict[str, Any]]:
    return [r for r in cap.records if r["level"] == "WARNING" and "事件流不可用" in r["message"]]


@pytest.mark.asyncio
async def test_unavailable_reported_once_per_reason(loguru_capture: Any) -> None:
    """同因不可用只 warning 一次；理由变化、或接上之后再次失败，才重新报。"""
    collector = _collector(_FakeEventBus())
    cap = loguru_capture  # fixture 已进入捕获，不要再 with（会重复计数）

    collector._report_unavailable("连接失败（RuntimeError: boom）")
    collector._report_unavailable("连接失败（RuntimeError: boom）")
    assert len(_unavailable_warnings(cap)) == 1, "Mod 没开是常态，不该按最短间隔重复 warning"

    collector._report_unavailable("连接成功但 Mod 未暴露任何工具")
    assert len(_unavailable_warnings(cap)) == 2, "失败理由变了要重新报"

    assert await collector._ensure_ready() is True
    collector._report_unavailable("连接失败（RuntimeError: boom）")
    assert len(_unavailable_warnings(cap)) == 3, "接上过之后再断属新事故"

    await collector._close_client(collector._client)


@pytest.mark.asyncio
async def test_connect_failure_backs_off_up_to_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """连不上时按 2 倍退避重试、60 秒封顶（不按最短间隔硬撞）。"""
    collector = MaicraftAttentionCollector(  # type: ignore[arg-type]
        config={"retry_interval_ms": 1000},
        event_bus=_FakeEventBus(),
    )
    slept: List[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) >= 8:
            raise asyncio.CancelledError

    async def _never_ready() -> bool:
        return False

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(collector, "_ensure_ready", _never_ready)
    with pytest.raises(asyncio.CancelledError):
        async for _ in collector.collect():
            pass

    assert slept == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
