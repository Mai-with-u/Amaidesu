"""Review API：场次复盘聚合端点与数据包导出（聚合口径用构造数据断言）。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Dict

import httpx
import pytest

from datetime import datetime

from src.modules.dashboard.services.session_events import SessionEventReader
from src.modules.dashboard.services.session_review import (
    SessionReviewAggregator,
    longest_silence_ms,
    percentile,
)

BASE = 1_700_000_000_000


# ---------------------------------------------------------------------- #
# 纯函数口径                                                              #
# ---------------------------------------------------------------------- #


def test_percentile_interpolates() -> None:
    assert percentile([], 0.9) is None
    assert percentile([5.0], 0.5) == 5.0
    # 线性插值：p50 与 p90 落在相邻样本之间
    assert percentile([10.0, 20.0, 30.0, 40.0], 0.5) == 25.0
    assert percentile([10.0, 20.0, 30.0, 40.0], 0.9) == 37.0


def test_longest_silence_counts_boundaries() -> None:
    # 首尾边界计入：0s 开播 → 60s 首发 → 90s 末次 → 150s 收播，最长 60s
    assert longest_silence_ms([60_000, 90_000], 0, 150_000) == 60_000
    # 零发言：整场即沉默
    assert longest_silence_ms([], 1_000, 61_000) == 60_000


# ---------------------------------------------------------------------- #
# 聚合服务（真实仓储构造数据）                                            #
# ---------------------------------------------------------------------- #


async def _seed_session(store, started: int, ended: int) -> int:
    pk = await store.sessions.insert_live_session(started_at_ms=started, title="复盘场")
    await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=ended)
    return pk


async def _seed_chat(store, pk: int, ts: int, role: str, content: str, mtype: str = "danmaku") -> None:
    await store.chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=ts, sender_role=role, content=content, message_type=mtype
    )


async def test_aggregator_counts_and_silence(store) -> None:
    pk = await _seed_session(store, BASE, BASE + 600_000)
    await _seed_chat(store, pk, BASE + 1_000, "viewer", "你好", mtype="danmaku")
    await _seed_chat(store, pk, BASE + 2_000, "viewer", "来了", mtype="enter")
    await _seed_chat(store, pk, BASE + 60_000, "assistant", "欢迎", mtype="speak")
    await _seed_chat(store, pk, BASE + 90_000, "assistant", "开玩", mtype="speak")
    await store.chat.insert_gift(
        live_session_id=pk, timestamp_ms=BASE + 5_000, user_id="u1", user_name="A",
        gift_name="小花", quantity=1, total_price=100,
    )  # fmt: skip
    await store.chat.insert_super_chat(
        live_session_id=pk, timestamp_ms=BASE + 6_000, user_id="u1", user_name="A",
        message="SC", total_price=1000,
    )  # fmt: skip
    await store.events.insert_game_event(
        live_session_id=pk, game="minecraft", event_type="died", message="摔死", timestamp_ms=BASE + 7_000
    )

    row = dict(await store.sessions.get_live_session(live_session_id=pk))
    agg = SessionReviewAggregator(chat_repo=store.chat, llm_repo=store.llm, event_repo=store.events)
    body = await agg.build([row])

    assert (body.chips.danmaku_count, body.chips.enter_count) == (1, 1)
    assert (body.chips.gift_count, body.chips.gift_total_price) == (1, 100)
    assert (body.chips.super_chat_count, body.chips.super_chat_total_price) == (1, 1000)
    assert body.speech.count == 2
    # 首发 60s、末次 90s、收播 600s → 最长沉默 510s
    assert body.speech.longest_silence_ms == 510_000
    assert body.game.by_type == {"died": 1}
    assert body.window_start_ms == BASE and body.window_end_ms == BASE + 600_000


async def test_aggregator_llm_window_and_waterline(store) -> None:
    pk = await _seed_session(store, BASE, BASE + 600_000)
    other = await _seed_session(store, BASE + 3_600_000, BASE + 4_000_000)
    await store.llm.insert_llm_request(
        request_id="r1", timestamp_ms=BASE + 1_000, profile_name="planner",
        prompt_tokens=1_000, completion_tokens=100, total_tokens=1_100,
        cache_hit_tokens=900, cache_miss_tokens=100, cost=0.5, latency_ms=20_000, success=True,
    )  # fmt: skip
    await store.llm.insert_llm_request(
        request_id="r2", timestamp_ms=BASE + 2_000, profile_name="minecraft",
        prompt_tokens=2_000, completion_tokens=10, total_tokens=2_010,
        cache_hit_tokens=0, cache_miss_tokens=2_010, cost=0.0, latency_ms=5_000, success=False,
    )  # fmt: skip
    # 场外请求不进窗口
    await store.llm.insert_llm_request(
        request_id="r3", timestamp_ms=BASE + 700_000, profile_name="planner", prompt_tokens=99
    )
    # 第二场窗口内的水位点
    await store.llm.insert_llm_request(
        request_id="r4", timestamp_ms=BASE + 3_600_000 + 1_000, profile_name="planner", prompt_tokens=3_000
    )

    agg = SessionReviewAggregator(
        chat_repo=store.chat,
        llm_repo=store.llm,
        slow_thresholds={"planner": 15_000, "minecraft": 15_000},
    )
    rows = [dict(await store.sessions.get_live_session(live_session_id=sid)) for sid in (pk, other)]
    body = await agg.build(rows)

    assert body.llm.overall.calls == 3  # 场外 r3 被排除
    assert body.llm.overall.failed_calls == 1
    assert body.llm.overall.cost == pytest.approx(0.5)
    assert body.llm.overall.cache_hit_rate == pytest.approx(900 / 3010)
    assert body.llm.overall.slow_calls == 1  # r1 20s > 15s 阈值

    by_profile = {row.profile_name: row for row in body.llm.by_profile}
    assert by_profile["minecraft"].calls == 1 and by_profile["minecraft"].failed_calls == 1
    assert by_profile["planner"].slow_calls == 1

    series = {s.profile_name: s for s in body.waterline}
    planner_pts = [(p.timestamp_ms, p.prompt_tokens, p.session_id) for p in series["planner"].points]
    assert planner_pts == [
        (BASE + 1_000, 1_000, pk),
        (BASE + 3_600_000 + 1_000, 3_000, other),
    ]
    assert [p.prompt_tokens for p in series["minecraft"].points] == [2_000]


def _decision_line(pk: int, seq: int, ts: int, data: Dict[str, Any]) -> str:
    return _event_line(pk, seq, ts, "planner.decision", data)


def _event_line(pk: int, seq: int, ts: int, event_type: str, data: Dict[str, Any]) -> str:
    payload = {
        "id": f"evt-{seq}",
        "type": event_type,
        "level": "info",
        "source": "test",
        "summary": f"事件{seq}",
        "data": {"live_session_id": pk, **data},
        "timestamp_ms": ts,
    }
    return json.dumps(payload, ensure_ascii=False)


async def test_aggregator_decisions_from_events_jsonl(store, tmp_path: Path) -> None:
    pk = await _seed_session(store, BASE, BASE + 600_000)
    events_dir = tmp_path / "logs" / "events"
    day = events_dir / datetime.fromtimestamp(BASE / 1000).strftime("%Y-%m-%d")
    day.mkdir(parents=True)
    lines = [
        _decision_line(pk, 1, BASE + 40_000, {"trigger_reason": "rundown", "speech": "你好", "round_id": "rnd_1"}),
        _decision_line(pk, 2, BASE + 70_000, {"trigger_reason": "game", "speech": "打怪", "round_id": "rnd_2"}),
        _decision_line(pk, 3, BASE + 80_000, {"trigger_reason": "game", "silent_reason": "low_confidence"}),
        _decision_line(pk, 4, BASE + 90_000, {"trigger_reason": "game", "error": "planner_failed"}),
        # 时延样本：verdict（表达生成之前）与 speech 按 round_id 配对取差
        _event_line(pk, 5, BASE + 30_000, "planner.verdict", {"round_id": "rnd_1"}),
        _event_line(pk, 6, BASE + 35_000, "streamer.speech", {"round_id": "rnd_1"}),
        _event_line(pk, 7, BASE + 60_000, "planner.verdict", {"round_id": "rnd_2"}),
        _event_line(pk, 8, BASE + 83_000, "streamer.speech", {"round_id": "rnd_2"}),
        # 有 speech 无 verdict 的轮次（关联缺失）不进样本
        _event_line(pk, 9, BASE + 95_000, "streamer.speech", {"round_id": "rnd_9"}),
        # 场外决策不进窗口
        _decision_line(999, 10, BASE + 10_000, {"trigger_reason": "rundown"}),
    ]
    (day / "00.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    reader_dep = SimpleNamespace(base_dir=tmp_path / "logs")
    reader = SessionEventReader(event_history=None, events_dir=reader_dep.base_dir / "events")
    agg = SessionReviewAggregator(event_reader=reader)
    body = await agg.build([dict(await store.sessions.get_live_session(live_session_id=pk))])

    assert body.decisions.total == 4
    assert body.decisions.distribution == {"game": 3, "rundown": 1}
    assert body.decisions.silent_count == 1
    assert body.decisions.failed_count == 1
    # 配对样本 5s/23s → p50=14s、p90=21.2s、max=23s（线性插值口径）
    assert body.decisions.latency.sample_count == 2
    assert body.decisions.latency.p50_ms == 14_000
    assert body.decisions.latency.p90_ms == 21_200
    assert body.decisions.latency.max_ms == 23_000
    assert body.degraded is False


# ---------------------------------------------------------------------- #
# 端点                                                                    #
# ---------------------------------------------------------------------- #


@pytest.fixture
async def review_client(api, event_bus, store, tmp_path: Path) -> AsyncGenerator[httpx.AsyncClient, None]:
    from src.modules.session import LiveSessionManager

    manager = LiveSessionManager(store.sessions, store.chat, event_bus)
    await manager.start()
    try:
        client = await api(
            session_manager=manager,
            chat_repo=store.chat,
            llm_repo=store.llm,
            event_repo=store.events,
            storage_db_path=store.db_path if hasattr(store, "db_path") else None,
            diagnostics_writer=SimpleNamespace(base_dir=tmp_path / "logs"),
        )
        client.tmp_path = tmp_path  # type: ignore[attr-defined]
        yield client
    finally:
        if manager.active_pk is not None:
            await manager.close_session()


async def test_review_endpoint_default_latest_ended(review_client, store) -> None:
    await _seed_session(store, BASE, BASE + 60_000)
    latest = await _seed_session(store, BASE + 100_000, BASE + 200_000)

    body = (await review_client.get("/api/v1/review")).json()
    assert [s["live_session_id"] for s in body["sessions"]] == [latest]

    picked = (await review_client.get("/api/v1/review", params={"ids": str(latest)})).json()
    assert picked["window_start_ms"] == BASE + 100_000

    assert (await review_client.get("/api/v1/review", params={"ids": "99999"})).status_code == 404
    assert (await review_client.get("/api/v1/review", params={"ids": "abc"})).status_code == 400


async def test_review_merge_two_sessions(review_client, store) -> None:
    a = await _seed_session(store, BASE, BASE + 60_000)
    b = await _seed_session(store, BASE + 600_000, BASE + 660_000)
    await _seed_chat(store, a, BASE + 1_000, "viewer", "一")
    await _seed_chat(store, b, BASE + 601_000, "viewer", "二")

    body = (await review_client.get("/api/v1/review", params={"ids": f"{a},{b}"})).json()
    assert [s["live_session_id"] for s in body["sessions"]] == [a, b]
    assert body["window_start_ms"] == BASE and body["window_end_ms"] == BASE + 660_000
    assert body["chips"]["danmaku_count"] == 2
    assert body["speech"]["count"] == 0


async def test_export_zip_contains_db_manifest_logs(review_client, store, tmp_path: Path) -> None:
    pk = await _seed_session(store, BASE, BASE + 60_000)
    await _seed_chat(store, pk, BASE + 1_000, "viewer", "打包我")

    # 造一天日志素材：events 目录由 client 的 diagnostics_writer.base_dir 指向 tmp
    events_dir = tmp_path / "logs" / "events" / datetime.fromtimestamp(BASE / 1000).strftime("%Y-%m-%d")
    events_dir.mkdir(parents=True)
    (events_dir / "00.jsonl").write_text('{"type": "game.report"}\n', encoding="utf-8")

    resp = await review_client.get("/api/v1/review/export", params={"ids": str(pk)})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert "manifest.json" in names
    assert any(n.startswith("db/") and n.endswith(".db") for n in names)
    assert any(n.startswith("logs/events/") and n.endswith("00.jsonl") for n in names)

    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["sessions"][0]["live_session_id"] == pk
    assert manifest["windows_ms"] == [[BASE, BASE + 60_000]]
    assert manifest["notice"] == "含观众数据，仅限开发组"
    assert any(f["path"].startswith("logs/events/") for f in manifest["files"])


async def test_export_single_session_llm_requests_logs(review_client, store, tmp_path: Path) -> None:
    await _seed_session(store, BASE, BASE + 60_000)
    llm_dir = tmp_path / "logs" / "llm_requests" / datetime.fromtimestamp(BASE / 1000).strftime("%Y-%m-%d")
    llm_dir.mkdir(parents=True)
    (llm_dir / "00.jsonl").write_text('{"request_id": "r1"}\n', encoding="utf-8")

    resp = await review_client.get("/api/v1/review/export")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert any(n.startswith("logs/llm_requests/") for n in zf.namelist())
