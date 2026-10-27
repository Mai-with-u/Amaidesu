"""Sessions API：场次列表 / 开启 / 结束 / 重命名 / 删除，以及回看时间线（明细与事件历史按时间合并、发言去重、游标翻页）"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest
import tomllib

from src.modules.events.event_history import EventHistoryService, EventRecord

BASE = 1_700_000_000_000


@pytest.fixture
def event_history() -> EventHistoryService:
    return EventHistoryService(max_events=100)


@pytest.fixture
async def sessions_client(api, event_bus, session_manager, event_history, store):
    return await api(
        event_bus=event_bus,
        session_manager=session_manager,
        event_history=event_history,
        session_repo=store.sessions,
        chat_repo=store.chat,
    )


async def _open(client, title: str = "") -> int:
    return (await client.post("/api/v1/live-sessions/open", json={"title": title} if title else {})).json()[
        "live_session_id"
    ]


def _decision(pk: int, seq: int, ts: int, **data) -> EventRecord:
    return EventRecord(
        id=f"evt-{seq}",
        type="planner.decision",
        level="info",
        source=str(pk),
        summary=f"决策{seq}",
        data={"live_session_id": pk, "round_id": f"rnd_{seq}", **data},
        timestamp_ms=ts,
    )


async def test_open_list_close_flow(sessions_client) -> None:
    client = sessions_client
    # 启动不自动开新场次
    body = (await client.get("/api/v1/live-sessions")).json()
    assert (body["active_session_id"], body["items"]) == (None, [])

    pk = await _open(client, "晚间场")
    body = (await client.get("/api/v1/live-sessions")).json()
    assert body["active_session_id"] == pk
    (active,) = [item for item in body["items"] if item["is_active"]]
    assert (active["title"], active["ended_at_ms"]) == ("晚间场", None)

    # 空场次结束 → 整行丢弃（防膨胀）
    assert (await client.post(f"/api/v1/live-sessions/{pk}/close")).json()["success"] is True
    body = (await client.get("/api/v1/live-sessions")).json()
    assert body["active_session_id"] is None and all(i["live_session_id"] != pk for i in body["items"])


async def test_close_mismatched_and_delete_missing(sessions_client) -> None:
    client = sessions_client
    await _open(client)
    await _open(client)  # 开第二场时第一场已被自动结束
    active = (await client.get("/api/v1/live-sessions")).json()["active_session_id"]
    assert (await client.post(f"/api/v1/live-sessions/{active + 100000}/close")).status_code == 409

    pk = await _open(client, "待删")
    deleted = await client.delete(f"/api/v1/live-sessions/{pk}")
    assert (deleted.status_code, deleted.json()["success"]) == (200, True)
    assert (await client.delete(f"/api/v1/live-sessions/{pk}")).status_code == 404


async def test_rename_session(sessions_client) -> None:
    """重命名：进行中场次可直接改名；空串规范化为清除标题；不存在的场次 404。"""
    client = sessions_client
    pk = await _open(client, "旧标题")

    renamed = await client.patch(f"/api/v1/live-sessions/{pk}", json={"title": "  新标题  "})
    assert (renamed.status_code, renamed.json()["success"]) == (200, True)

    async def titles() -> dict:
        return {i["live_session_id"]: i["title"] for i in (await client.get("/api/v1/live-sessions")).json()["items"]}

    assert (await titles())[pk] == "新标题"
    cleared = await client.patch(f"/api/v1/live-sessions/{pk}", json={"title": "   "})
    assert (cleared.status_code, cleared.json()["detail"]) == (200, "场次标题已清除")
    assert (await titles())[pk] is None
    assert (await client.patch(f"/api/v1/live-sessions/{pk + 99999}", json={"title": "x"})).status_code == 404


async def test_list_filters_by_source_and_title(sessions_client, session_manager, store) -> None:
    """前两个显式场次都需有明细，否则自动结束时会被整行丢弃。"""
    client = sessions_client
    first = await _open(client, "筛选测试场")
    await store.chat.insert_live_chat(
        live_session_id=first, timestamp_ms=BASE, sender_role="viewer", content="让这场不被丢弃", message_type="danmaku"
    )
    second = await _open(client, "另一个场次")
    await client.post(f"/api/v1/live-sessions/{second}/close")

    manual = (await client.get("/api/v1/live-sessions", params={"source": "manual"})).json()
    assert [i["title"] for i in manual["items"]] == ["筛选测试场"]
    keyword = (await client.get("/api/v1/live-sessions", params={"q": "筛选测试"})).json()
    assert [i["title"] for i in keyword["items"]] == ["筛选测试场"]
    assert (await client.get("/api/v1/live-sessions", params={"source": "hacker"})).status_code == 400


async def test_timeline_merges_details_and_events_and_dedupes_speech(
    sessions_client, session_manager, event_history, store
) -> None:
    client = sessions_client
    pk = await _open(client, "回看测试")
    chat = store.chat
    await chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=BASE, sender_role="viewer", sender_id="u1", sender_name="观众A",
        content="主播玩什么？", message_type="danmaku", message_id="msg_seed_1",
    )  # fmt: skip
    await chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=BASE + 5_000, sender_role="assistant", sender_name="主播",
        content="今天玩《双人成行》！", message_type="speak", reply_to_message_id="msg_seed_1",
    )  # fmt: skip
    # 礼物行带 total_price：响应模型 extra=forbid 下漏声明该字段曾使整响应 500
    await chat.insert_gift(
        live_session_id=pk, timestamp_ms=BASE + 6_000, user_id="u2", user_name="土豪B",
        gift_name="小花花", quantity=3, total_price=3000,
    )  # fmt: skip
    event_history.record(_decision(pk, 1, BASE + 4_000, reply_to_message_id="msg_seed_1"))
    # 发言双源去重：与 streamer.speech 事件同文本同时刻的明细行让位（事件侧是富卡）；无事件对应的明细行保留兜底
    await chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=BASE + 10_000, sender_role="assistant", sender_name="主播",
        content="有事件对应的发言", message_type="speak",
    )  # fmt: skip
    event_history.record(
        EventRecord(
            id="evt-speech-1", type="streamer.speech", level="info", source=str(pk), summary="发言",
            data={"live_session_id": pk, "text": "有事件对应的发言", "utterance_id": "utt_1"},
            timestamp_ms=BASE + 10_000,
        )
    )  # fmt: skip
    await chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=BASE + 11_000, sender_role="assistant", sender_name="主播",
        content="只有明细行的发言", message_type="speak",
    )  # fmt: skip

    body = (await client.get(f"/api/v1/live-sessions/{pk}/timeline")).json()
    items = body["items"]
    gift = next(i for i in items if i["kind"] == "gift")
    assert (gift["gift_name"], gift["total_price"]) == ("小花花", 3000)
    decision = next(i for i in items if i["kind"] == "event" and i["event_type"] == "planner.decision")
    assert decision["data"]["round_id"] == "rnd_1"
    speech_rows = [i for i in items if i["kind"] == "speech"]
    assert {r["reply_to_message_id"] for r in speech_rows if r.get("reply_to_message_id")} == {"msg_seed_1"}
    assert "有事件对应的发言" not in [r["text"] for r in speech_rows]
    assert "只有明细行的发言" in [r["text"] for r in speech_rows]
    assert len([i for i in items if i["kind"] == "event" and i["event_type"] == "streamer.speech"]) == 1
    # 诊断未装配时不报裁剪
    assert (body["has_more"], body["events_pruned"]) == (False, False)


async def test_timeline_cursor_pagination(sessions_client, event_history) -> None:
    client = sessions_client
    pk = await _open(client, "游标翻页")
    for seq in range(1, 4):
        event_history.record(_decision(pk, seq, BASE + seq * 1_000))

    url = f"/api/v1/live-sessions/{pk}/timeline"
    page1 = (await client.get(url, params={"limit": 2})).json()
    assert [i["data"]["round_id"] for i in page1["items"]] == ["rnd_2", "rnd_3"] and page1["has_more"] is True
    page2 = (await client.get(url, params={"limit": 2, "before_timestamp_ms": page1["items"][0]["ts_ms"]})).json()
    assert [i["data"]["round_id"] for i in page2["items"]] == ["rnd_1"] and page2["has_more"] is False


# -------------------- 开场次带组件意图对齐 --------------------


class _PlanStubCollectors:
    """意图对齐用的最小采集器管理器替身（记录启停调用）。"""

    def __init__(self) -> None:
        self.running: set[str] = set()
        self.started: list[str] = []
        self.stopped: list[str] = []

    def list_running(self) -> list[str]:
        return sorted(self.running)

    def get_collector_by_name(self, name: str) -> Optional[object]:
        return object() if name in self.running else None

    def register(self, collector: Any, description: str = "") -> bool:
        # 注册即视为在运行：附属采集器经 Agent 启动连带登记后应可启动
        self.running.add(collector.name)
        return True

    async def enable_collector(self, name: str, config: Optional[dict], event_bus: Any = None) -> bool:
        self.running.add(name)
        self.started.append(name)
        return True

    async def disable_collector(self, name: str) -> bool:
        self.running.discard(name)
        self.stopped.append(name)
        return True

    async def start_collector(self, name: str) -> bool:
        return name in self.running


class _PlanStubAgents:
    """意图对齐用的最小 Agent 管理器替身（初始运行名单可指定）。"""

    def __init__(self, running: set[str]) -> None:
        self.running = set(running)
        self.enabled: list[str] = []
        self.disabled: list[str] = []

    def list_running(self) -> list[str]:
        return sorted(self.running)

    def get_agent_by_name(self, name: str) -> Optional[object]:
        return object() if name in self.running else None

    async def enable_agent(self, name: str, config: Optional[dict] = None, *, deps: Any = None, **_kw: Any) -> bool:
        self.running.add(name)
        self.enabled.append(name)
        return True

    async def disable_agent(self, name: str) -> bool:
        self.running.discard(name)
        self.disabled.append(name)
        return True

    async def start_agent(self, name: str) -> bool:
        return name in self.running


async def test_open_with_component_plan_aligns_components(
    api, event_bus, session_manager, event_history, initialized_config
) -> None:
    """带 component_plan 开场：启动期望组件、跳过附属采集器、常驻主播不受影响、名单写为意图全集。"""
    collectors = _PlanStubCollectors()
    agents = _PlanStubAgents({"streamer"})
    client = await api(
        event_bus=event_bus,
        session_manager=session_manager,
        event_history=event_history,
        config_service=initialized_config,
        collector_manager=collectors,
        agent_manager=agents,
    )

    body = (
        await client.post(
            "/api/v1/live-sessions/open",
            json={
                "title": "意图对齐场",
                "component_plan": [
                    {"group": "collectors", "name": "console_input"},
                    {"group": "collectors", "name": "maicraft_attention"},
                    {"group": "agents", "name": "minecraft"},
                ],
            },
        )
    ).json()

    assert body["live_session_id"] is not None
    report = body["plan_report"]
    assert report is not None and report["config_error"] == ""
    by_ref = {(i["group"], i["name"]): (i["action"], i["result"]) for i in report["items"]}
    assert by_ref[("collectors", "console_input")] == ("start", "started")
    assert by_ref[("collectors", "maicraft_attention")] == ("skip", "skipped")
    assert by_ref[("agents", "minecraft")] == ("start", "started")
    # 主播 Agent 常驻：不在意图集也不被停止
    assert ("agents", "streamer") not in by_ref and agents.disabled == []
    # enabled 名单落盘为意图全集（重启后按此装配）
    assert initialized_config.main_config["agents"]["enabled"] == ["minecraft"]
    collectors_doc = tomllib.loads(
        (Path(initialized_config.base_dir) / "config" / "collectors.toml").read_text(encoding="utf-8-sig")
    )
    assert collectors_doc["enabled"] == ["console_input"]


async def test_open_without_plan_keeps_components_untouched(sessions_client) -> None:
    """不带 component_plan 的开场完全不碰组件（向后兼容，plan_report 为 None）。"""
    client = sessions_client
    body = (await client.post("/api/v1/live-sessions/open", json={"title": "无清单场"})).json()
    assert body["live_session_id"] is not None and body["plan_report"] is None


@pytest.fixture
async def rundown_client(api, event_bus, session_manager, store):
    return await api(
        event_bus=event_bus,
        session_manager=session_manager,
        session_repo=store.sessions,
        rundown_transition_repo=store.rundown_transitions,
    )


async def test_rundown_execution_endpoint(rundown_client, store) -> None:
    from src.modules.storage.models.rundown import RundownTransition

    # 直接造已结束场次行（走 API 开场会是"现在"开播且空场次结账被整行丢弃）
    pk = await store.sessions.insert_live_session(started_at_ms=BASE, title="执行回看场")
    started = BASE
    ended = BASE + 600_000
    await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=ended)
    for seq, (action, seg, at, expected) in enumerate(
        [
            ("load", "s1", started, 300_000),
            ("next", "s2", started + 300_000, 300_000),
            ("finish", "", ended, None),
        ],
        start=1,
    ):
        await store.rundown_transitions.insert(
            RundownTransition(
                live_session_id=pk,
                rundown_id="rd-1",
                action=action,
                seg_id=seg,
                seg_index=seq,
                by="agent",
                at_ms=at,
                expected_ms=expected,
                updated_at_ms=at,
            )
        )

    body = (await rundown_client.get(f"/api/v1/live-sessions/{pk}/rundown-execution")).json()
    assert body["live_session_id"] == pk and body["rundown_id"] == "rd-1"
    assert [(s["seg_id"], s["dwell_ms"]) for s in body["segments"]] == [("s1", 300_000), ("s2", 300_000)]
    assert body["transitions_count"] == 3
    # 未装配事实仓储 → 503；不存在的场次 → 404
    assert (await rundown_client.get("/api/v1/live-sessions/99999/rundown-execution")).status_code == 404
