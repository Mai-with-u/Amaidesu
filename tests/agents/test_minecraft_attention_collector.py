"""maicraft_attention 采集器测试：长轮询 MaiCraft v1 的任务事件流，把身体遭遇转成 game.body.* 事件。

采集器代码归属 Minecraft Agent 包（游戏相关适配器内聚），装配走采集器框架。

覆盖：
- 元数据/继承/配置默认值
- 分类：临时任务开始与结束、处理不了的需求进叙事通道，目标运行的处境变化不进
- 摘要：Mod 那句话改成观众听得懂的白话，坐标、血量这类遥测不进事件
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
from src.modules.agents.companions import build_companion_collectors, owner_of
from src.modules.collectors.base import BaseCollector, CollectorState
from src.modules.collectors.factory import SUPPORTED_COLLECTORS
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.body import BodyEventPayload

from .minecraft_collector_fakes import FakeEventBus as _FakeEventBus
from .minecraft_collector_fakes import FakeMcpClient as _FakeClient
from .minecraft_collector_fakes import events_page as _page
from .minecraft_collector_fakes import fake_client_factory


def _event(cursor: int, kind: str, message: str = "", goal_id: int = -1) -> Dict[str, Any]:
    return {"cursor": cursor, "kind": kind, "goal_id": goal_id, "message": message}


def _collector(bus: _FakeEventBus) -> MaicraftAttentionCollector:
    """接替身客户端的采集器（MCP 客户端经构造工厂注入）。"""
    return MaicraftAttentionCollector(config={}, event_bus=bus, client_factory=fake_client_factory())  # type: ignore[arg-type]


async def _primed(bus: _FakeEventBus, cursor: int = 5) -> tuple[MaicraftAttentionCollector, _FakeClient]:
    collector = _collector(bus)
    assert await collector._ensure_ready() is True
    client = _FakeClient.instances[-1]
    client.pages.append(_page([], cursor=cursor))
    await collector._drain()
    return collector, client


# 契约面


def test_collector_metadata_and_registration() -> None:
    """身体事件流是 Minecraft Agent 的附属采集器：不在通用采集器名单里，随 Minecraft Agent 构造。"""
    assert issubclass(MaicraftAttentionCollector, BaseCollector)
    assert MaicraftAttentionCollector.name == "maicraft_attention"
    assert "maicraft_attention" not in SUPPORTED_COLLECTORS
    assert owner_of("maicraft_attention") == "minecraft"
    names = [collector.name for collector in build_companion_collectors("minecraft", {})]
    assert "maicraft_attention" in names


def test_config_defaults() -> None:
    cfg = MaicraftAttentionCollector.ConfigSchema()
    assert cfg.mcp.url.endswith("/mcp")
    assert 1000 <= cfg.wait_ms <= 55_000


def test_classification_keeps_body_encounters_and_drops_goal_progress() -> None:
    """分类表：身体先处理的急事与处理不了的需求进叙事通道，目标运行的处境变化不进。"""
    assert classify("temporary_task_started") == "reflex_started"
    assert classify("temporary_task_finished") == "reflex_finished"
    assert classify("need_unhandled") == "need_unhandled"
    assert classify("character_died") == "died", "角色死亡归已有的 died 事件"
    for goal_kind in ("started", "asked", "paused", "resumed", "finished", "stalled", "death_recovery_applied"):
        assert classify(goal_kind) is None, f"{goal_kind} 是游戏 Agent 的任务通道"
    # 步进完成是主播可讲的进展，转 game.milestone 通道
    assert classify("step_finished") == "milestone"
    assert classify("something_new") == "unknown", "上游新增种类不丢，但事件面保持封闭"
    assert classify("") is None


def test_hurt_and_dimension_change_are_encounters_and_reminders_stay_silent() -> None:
    """一阵挨打只来一条、归 attacked；换维度归 dimension_changed；提醒是写给游戏 Agent 的，不进叙事通道。"""
    assert classify("hurt") == "attacked"
    assert classify("dimension_changed") == "dimension_changed"
    assert classify("reminder") is None
    assert classify("gear_broken") == "unknown", "装备用坏没有专门事件名，走 unknown 但有说清性质的开头"
    assert classify("discovered") == "unknown"
    assert classify("sky_changed") == "unknown", "天色变了是观众也看得见的变化，进叙事通道而不是静默"
    assert classify("timer_due") is None, "计时到点是游戏 Agent 给自己定的回头看一眼，后续进展另走步进完成与交付"


@pytest.mark.parametrize(
    ("kind", "source", "message", "expected"),
    [
        # 下面的原话都摘自真实直播的事件流：Mod 写给游戏 Agent 的说法换成观众听得懂的白话，数字一律不念
        ("attacked", "hurt", "摔了一下，掉了 1.7 点血，还剩 18.3 点", "摔了一下"),
        ("attacked", "hurt", "被苦力怕炸到一下，掉了 1 点血，还剩 7.8 点", "被苦力怕炸到一下，血掉了不少"),
        ("attacked", "hurt", "摔了 2 下，掉了 4.4 点血，还剩 17.4 点", "摔了好几下"),
        ("attacked", "hurt", "被骷髅（e12）射中 3 下，掉了 6 点血，还剩 3 点", "被骷髅射中好几下，血快没了"),
        (
            "died",
            "character_died",
            "角色死了（Dev被蜘蛛杀死了），等重生，它下的目标停在原地，目标 426 maicraft:obtain 第 0 步，"
            "看现场决定下一件做什么，重生后接着做",
            "死了：被蜘蛛杀死了，等重生，重生后接着做",
        ),
        (
            "need_unhandled",
            "need_unhandled",
            "饿了但没吃上，弄吃的过了 60 秒还没弄到（在弄#c:foods/cooked_meat 里的任一物品），先回去继续干活",
            "有件事没办成：饿了但没吃上，先回去继续干活",
        ),
        (
            "reflex_finished",
            "temporary_task_finished",
            "已撤离，甩开了追兵（手上没有任何武器，对面会还手），离打点 27 格",
            "急事处理完了：跑开了，甩开了追兵（手上没有任何武器，对面会还手）",
        ),
        (
            "reflex_finished",
            "temporary_task_finished",
            "自卫结束，回到打点，没见打倒哪只（拿 minecraft:string 挥中 1 下），位移 1 格，掉了约 1 颗心",
            "急事处理完了：打完了，没打倒哪只，掉了点血",
        ),
        (
            "reflex_finished",
            "temporary_task_finished",
            "自卫结束，回到打点，位移 1 格，掉了约 0 颗心",
            "急事处理完了：打完了，没掉血",
        ),
        # 范围限定换成"附近"但不丢：说成"根本没床"就是没凭据的断言
        (
            "reflex_finished",
            "temporary_task_finished",
            "天黑了想去睡，没睡成，睡觉没有完成，32 格内没有能睡的床，身上也没带床"
            "（扫到 1 张床，都睡不上，1 张超出这次肯走的距离），夜里不走远路去睡，也不摸黑去弄床",
            "急事处理完了：天黑了想去睡，没睡成，附近没有能睡的床，身上也没带床，夜里不走远路去睡，也不摸黑去弄床",
        ),
        (
            "reflex_finished",
            "temporary_task_finished",
            "捡回了死时撒在地上的东西（身上多了 3 格），接着干活",
            "急事处理完了：捡回了死时撒在地上的东西，接着干活",
        ),
        (
            "reflex_finished",
            "temporary_task_finished",
            "退回了安全处，继续干活",
            "急事处理完了：退回了安全处，继续干活",
        ),
        (
            "reflex_started",
            "temporary_task_started",
            "被威胁，插入自卫：打点在 3, 64, -9",
            "先放下手上的事：被怪盯上了，先自卫",
        ),
        (
            "reflex_started",
            "temporary_task_started",
            "刚重生，回死的地方捡东西（还有 285 秒就消失）",
            "先放下手上的事：刚重生，回死的地方捡东西",
        ),
        # 括号里的时间天气说得出口就照念；夹了倒计时这种数字就整段不要，不留半截
        (
            "unknown",
            "sky_changed",
            "天黑了，到了能睡的时间（傍晚，晴）",
            "天色变了：天黑了，到了能睡的时间（傍晚，晴）",
        ),
        ("unknown", "sky_changed", "开始下雨了（夜晚，距天亮约 8 分钟；雨）", "天色变了：开始下雨了"),
        (
            "unknown",
            "gear_broken",
            "装备用坏了，mainhand 的 minecraft:iron_pickaxe 耐久打空消灭了（满耐久 250，最后看到剩 1）",
            "装备用坏了：铁镐坏了",
        ),
        ("unknown", "discovered", "发现群系 minecraft:lush_caves（脚下）", "发现了新东西：繁茂洞穴（脚下）"),
        ("unknown", "discovered", "看见一片岩浆湖", "发现了新东西：看见一片岩浆湖"),
        (
            "unknown",
            "stalled",
            "目标 425（maicraft:obtain（做一张床所需的 3 块羊毛））已经 10 分钟没有任何新事件，"
            "仍在推进中，用 goal 按编号看它此刻在做什么，不会再动了就取消",
            "手上的事好一阵没进展了",
        ),
        ("dimension_changed", "dimension_changed", "从主世界到了下界，落在 12, 70, -8", "换了个维度：从主世界到了下界"),
        ("need_unhandled", "need_unhandled", "", "有件事没办成"),
        ("unknown", "something_new", "", "游戏里有了新动静"),
    ],
)
def test_summary_is_plain_speech_for_viewers(kind: str, source: str, message: str, expected: str) -> None:
    """摘要是观众听得懂的白话：开头说清是什么事，幕后说法换掉，坐标、血量、格数、秒数一律不念。"""
    assert summarize(kind, source, message) == expected


def test_without_coordinates_keeps_milestone_conclusions() -> None:
    """进展结论句只去坐标与观察编号（不走白话化），不留"停在了"这种残片。"""
    assert (
        without_coordinates("自卫结束但走不回打点（还差 7 格），停在了 3, 64, 9；主任务已暂停，等下一步指示")
        == "自卫结束但走不回打点（还差 7 格），主任务已暂停，等下一步指示"
    )
    # 物品、能力 ID 里的英文冒号不是断句符：sequence 每步做完的结论原样保留 ID，句间冒号照旧重组为逗号
    assert (
        without_coordinates("第 3 步（maicraft:build，砌东墙）：按图建好了")
        == "第 3 步（maicraft:build，砌东墙），按图建好了"
    )
    assert without_coordinates("挖到了: 3 个 minecraft:iron_ore") == "挖到了，3 个 minecraft:iron_ore"


def test_body_event_names_are_the_closed_kind_set() -> None:
    """9 个身体事件与 milestone 转发事件各就各位，通配订阅仍然可用。"""
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
        CoreEvents.GAME_MILESTONE,
    }
    for kind, event_name in KIND_TO_EVENT.items():
        assert event_name.rsplit(".", 1)[-1] == kind, "判别字段必须等于事件名末段"


def test_body_payload_requires_game_and_kind() -> None:
    """游戏标识由发布方给定（框架不假定是哪款游戏），缺 game 直接报错。"""
    payload = BodyEventPayload(game="minecraft", kind="reflex_started", summary="先放下手上的事")
    assert payload.game == "minecraft" and payload.timestamp_ms > 0
    with pytest.raises(ValidationError):
        BodyEventPayload(kind="reflex_started", summary="先放下手上的事")


# 增量读取与转发


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
    assert started.summary == "先放下手上的事：被怪盯上了，先自卫" and started.resolved is False
    assert need.summary == "有件事没办成：饿了但没吃上，身上没有食物，先回去继续干活"
    # 坐标与游标这类遥测不入事件（比对时去掉随机 id，免得 uuid 里碰巧出现 "-9"）
    dumped = json.dumps([payload.model_dump(exclude={"id"}) for _, payload in bus.events], ensure_ascii=False)
    assert "-9" not in dumped and "cursor" not in dumped
    second = client.read_calls[-1]["arguments"]
    assert second == {"stream_id": "stream-A", "after_cursor": 5, "wait_ms": 25_000}
    assert collector._cursor == 8


async def test_step_finished_forwards_to_milestone_without_coordinates() -> None:
    """步进完成 → game.milestone：结论句原样转出（去坐标/观察编号），目标完成仍不转发。"""
    bus = _FakeEventBus()
    collector, client = await _primed(bus, cursor=5)
    client.pages.append(
        _page(
            [
                _event(6, "step_finished", "采集完成：8 个橡木原木进了背包，位置 3, 64, -9", goal_id=3),
                _event(7, "finished", "目标完成", goal_id=3),
            ],
            cursor=7,
        )
    )

    await collector._drain()

    assert [name for name, _ in bus.events] == [CoreEvents.GAME_MILESTONE]
    name, payload = bus.events[0]
    assert name == CoreEvents.GAME_MILESTONE
    assert payload.game == "minecraft"
    assert payload.event_type == "milestone"
    # 坐标这类遥测不进事件，结论句本身保留（冒号是 without_coordinates 的断句符，重组为逗号）
    assert payload.message == "采集完成，8 个橡木原木进了背包"
    assert "-9" not in payload.message


async def test_finished_temporary_task_is_marked_resolved() -> None:
    bus = _FakeEventBus()
    collector, client = await _primed(bus)
    client.pages.append(_page([_event(6, "temporary_task_finished", "自卫结束，回到打点；位移 2 格")], cursor=6))

    await collector._drain()

    name, payload = bus.events[0]
    assert name == CoreEvents.GAME_BODY_REFLEX_FINISHED and payload.resolved is True


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


async def test_read_failure_keeps_cursor_and_surfaces_to_the_loop() -> None:
    """读取失败：游标不动（那一段事件不能因此永久丢失），异常上抛给采集循环处理重连。"""
    bus = _FakeEventBus()
    collector, client = await _primed(bus, cursor=5)
    client.read_error = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await collector._drain()

    assert collector._cursor == 5
    assert bus.events == []


async def test_mod_without_events_tool_is_not_ready(monkeypatch: pytest.MonkeyPatch, loguru_capture: Any) -> None:
    """连上的不是 MaiCraft v1（没有 events 工具）：不当成就绪，如实说明原因。"""

    class _OldModClient(_FakeClient):
        tool_names = ("perceive", "execute")

    fake_client_factory()
    collector = MaicraftAttentionCollector(  # type: ignore[arg-type]
        config={}, event_bus=_FakeEventBus(), client_factory=_OldModClient
    )

    assert await collector._ensure_ready() is False
    assert any("events" in record["message"] for record in _unavailable_warnings(loguru_capture))


async def test_collect_loop_survives_unavailable_game(monkeypatch: pytest.MonkeyPatch) -> None:
    """游戏没起来时：启动不失败、循环不终止，连接就绪后照常转发。"""

    class _OfflineClient(_FakeClient):
        async def connect(self) -> bool:
            return False

    # 工厂读闭包格子：游戏上线后换客户端类，循环自愈（不再 monkeypatch 模块属性）
    holder: Dict[str, Any] = {"client_cls": _OfflineClient}
    bus = _FakeEventBus()
    collector = MaicraftAttentionCollector(  # type: ignore[arg-type]
        config={"retry_interval_ms": 200, "wait_ms": 1000},
        event_bus=bus,
        client_factory=lambda name, config: holder["client_cls"](name, config),
    )
    await collector.start()
    assert collector.state == CollectorState.RUNNING
    await asyncio.sleep(0.05)
    assert bus.events == []

    # 游戏上线后（换成可用客户端）循环自愈
    holder["client_cls"] = _FakeClient
    await asyncio.sleep(0.35)
    assert _FakeClient.instances, "应重试建立连接"
    await collector.stop()
    assert collector.state == CollectorState.STOPPED


# 常驻重试：不刷屏、不硬撞


def _unavailable_warnings(cap: Any) -> List[Dict[str, Any]]:
    return [r for r in cap.records if r["level"] == "WARNING" and "事件流不可用" in r["message"]]


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
