"""直播近况上报器：开播 / 换环节 / 收播快照、心跳、整场记录推送与重试、收尾与 HTTP 推送。

场次回顾推送随旧记忆退役（回顾由记忆账本取代），整场记录改为收播时直接读库推送。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import main as app_main
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.planner import PlannerDecisionPayload
from src.modules.events.payloads.rundown import RundownChangedPayload
from src.modules.maibot_sync import HttpStatusSender, LiveStatusReporter, StatusRejected


class FakeSender:
    """记录推送内容；``failures`` 次之前的推送抛 ``error``（缺省模拟 MaiBot 暂时不可达）。"""

    def __init__(self, failures: int = 0, error: Exception | None = None) -> None:
        self.bodies: List[Dict[str, Any]] = []
        self.attempts = 0
        self.failures = failures
        self.error = error or ConnectionError("MaiBot 未启动")
        self.closed = False

    async def send(self, body: Dict[str, Any]) -> None:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise self.error
        self.bodies.append(body)

    async def close(self) -> None:
        self.closed = True


class FakeTranscriptSource:
    """整场记录来源：按场次给一份固定报文；reject 为真时始终返回 None。"""

    def __init__(self, body: Optional[Dict[str, Any]] = None, *, reject: bool = False) -> None:
        self.body = body if body is not None else {"type": "transcript", "live_session_id": 0, "lines": []}
        self.reject = reject
        self.asked: List[int] = []

    async def load(self, live_session_id: int) -> Optional[Dict[str, Any]]:
        self.asked.append(int(live_session_id))
        if self.reject:
            return None
        return {**self.body, "live_session_id": int(live_session_id)}


async def _wait_until(predicate: Callable[[], bool], timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "等待超时"
        await asyncio.sleep(0.005)


def _reporter(sender: FakeSender, **kwargs: Any) -> tuple[EventBus, LiveStatusReporter]:
    bus = EventBus()
    kwargs.setdefault("signoff_settle_s", 0.0)
    reporter = LiveStatusReporter(event_bus=bus, sender=sender, clock_ms=lambda: 42, **kwargs)
    reporter.setup()
    return bus, reporter


def _rundown(index: int, total: int = 3, title: str = "搭屋顶", segment_id: str = "s") -> RundownChangedPayload:
    return RundownChangedPayload(rundown_id="r1", segment_id=segment_id, segment_title=title, index=index, total=total)


async def _end_live(bus: EventBus, live_session_id: int) -> None:
    """收播（只发 live.ended）：整场记录此时还压着，等道别轮收场信号。"""
    await bus.emit(
        CoreEvents.LIVE_ENDED, LiveEndedPayload(live_session_id=live_session_id, ended_at_ms=9_000), source="test"
    )
    await bus.wait_until_drained()


async def _signoff_round_done(bus: EventBus, live_session_id: int) -> None:
    """补发收播道别那一轮收场的 planner.decision：整场记录收到它才读库推送。"""
    await bus.emit(
        CoreEvents.PLANNER_DECISION,
        PlannerDecisionPayload(
            round_id="rnd_signoff", live_session_id=live_session_id, trigger_reason="proactive:signoff"
        ),
        source="test",
    )


async def test_live_lifecycle_pushes_full_snapshots() -> None:
    sender = FakeSender()
    bus, reporter = _reporter(sender, heartbeat_interval_ms=60_000)
    try:
        # 开播前编辑流程单不是近况，不推
        await bus.emit(CoreEvents.RUNDOWN_CHANGED, _rundown(0), source="test")
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=7, title="周五 MC 建房", platform="bilibili", started_at_ms=1_000),
            source="test",
        )
        await _wait_until(lambda: len(sender.bodies) == 1)
        started = sender.bodies[0]
        assert started["type"] == "snapshot" and started["live"] is True and started["segment"] is None
        assert (started["live_session_id"], started["title"], started["started_at_ms"]) == (7, "周五 MC 建房", 1_000)
        assert started["sent_at_ms"] == 42

        await bus.emit(CoreEvents.RUNDOWN_CHANGED, _rundown(2), source="test")
        await _wait_until(lambda: len(sender.bodies) == 2)
        assert sender.bodies[1]["segment"] == {"title": "搭屋顶", "index": 2, "total": 3, "finished": False}

        await bus.emit(CoreEvents.RUNDOWN_CHANGED, _rundown(3, title="", segment_id=""), source="test")
        await _wait_until(lambda: len(sender.bodies) == 3)
        assert sender.bodies[2]["segment"]["finished"] is True

        await bus.emit(CoreEvents.LIVE_ENDED, LiveEndedPayload(live_session_id=7, ended_at_ms=9_000), source="test")
        await _wait_until(lambda: len(sender.bodies) == 4)
        ended = sender.bodies[3]
        assert ended["live"] is False and ended["ended_at_ms"] == 9_000 and ended["discarded"] is False
        assert ended["title"] == "周五 MC 建房"
        assert reporter._heartbeat_task is None  # 收播停心跳
    finally:
        await reporter.stop()


async def test_heartbeat_resends_snapshot_while_live() -> None:
    sender = FakeSender()
    bus, reporter = _reporter(sender, heartbeat_interval_ms=10)
    try:
        await bus.emit(CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=1, title="t"), source="test")
        await _wait_until(lambda: len(sender.bodies) >= 3)
        assert all(body["live"] is True for body in sender.bodies)
    finally:
        await reporter.stop()


async def test_rehearsal_sessions_are_not_synced() -> None:
    sender = FakeSender()
    bus, reporter = _reporter(sender)
    try:
        await bus.emit(CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=3, kind="rehearsal"), source="test")
        await bus.emit(CoreEvents.RUNDOWN_CHANGED, _rundown(1), source="test")
        await bus.emit(CoreEvents.LIVE_ENDED, LiveEndedPayload(live_session_id=3, kind="rehearsal"), source="test")
        await bus.wait_until_drained()
        assert sender.attempts == 0
    finally:
        await reporter.stop()


async def test_transcript_is_pushed_after_the_ending_snapshot_and_signoff_round() -> None:
    """整场记录等收播道别那一轮收场后再读库推送（此刻道别台词已落库）；读不到就不推。"""
    sender = FakeSender()
    source = FakeTranscriptSource()
    bus, reporter = _reporter(sender, transcript_source=source)
    try:
        await bus.emit(CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=11), source="test")
        await _end_live(bus, 11)
        await _wait_until(lambda: len(sender.bodies) == 2)
        # 道别轮还没收场：此刻读库会缺收尾台词，所以先不读
        assert [body["type"] for body in sender.bodies] == ["snapshot", "snapshot"]
        assert source.asked == []

        await _signoff_round_done(bus, 11)
        await _wait_until(lambda: any(body.get("type") == "transcript" for body in sender.bodies))
        assert [body["type"] for body in sender.bodies] == ["snapshot", "snapshot", "transcript"]
        assert source.asked == [11]
    finally:
        await reporter.stop()

    missing = FakeTranscriptSource(reject=True)
    sender2 = FakeSender()
    bus2, reporter2 = _reporter(sender2, transcript_source=missing)
    try:
        await bus2.emit(CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=12), source="test")
        await _end_live(bus2, 12)
        await _signoff_round_done(bus2, 12)
        await _wait_until(lambda: not reporter2._push_tasks)
        assert [body["type"] for body in sender2.bodies] == ["snapshot", "snapshot"]
        assert missing.asked == [12], "来源返回 None 时也读了一次，只是不推"
    finally:
        await reporter2.stop()


async def test_rejected_transcript_is_not_retried() -> None:
    """插件明确拒收（如没开整场记录回放，回 403）：重试也没用，只推一次。"""
    # 前两次推送是开播与收播近况，第三次才是整场记录（被拒）
    sender = FakeSender(failures=3, error=StatusRejected("HTTP 403: live_memory.enabled=false"))
    source = FakeTranscriptSource()
    bus, reporter = _reporter(sender, retry_delays_ms=(1, 1, 1), transcript_source=source)
    try:
        await bus.emit(CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=13), source="test")
        await _end_live(bus, 13)
        await _signoff_round_done(bus, 13)
        await _wait_until(lambda: not reporter._push_tasks)
        # 被拒后不再退避重试：恰好三次尝试（两次近况 + 一次整场记录），一条都没送到
        assert sender.attempts == 3 and sender.bodies == []
        assert reporter._pending_transcript is None
    finally:
        await reporter.stop()


@pytest.mark.parametrize(
    "ended",
    [
        pytest.param(LiveEndedPayload(live_session_id=9, empty_discarded=True), id="empty-discarded"),
        pytest.param(LiveEndedPayload(live_session_id=9, deleted=True), id="deleted-by-operator"),
    ],
)
async def test_discarded_or_deleted_session_is_flagged(ended: LiveEndedPayload) -> None:
    """空场次被丢弃、运营者删掉进行中的场次，两种都告诉插件这一场作废。"""
    sender = FakeSender()
    bus, reporter = _reporter(sender)
    try:
        await bus.emit(CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=9), source="test")
        await bus.emit(CoreEvents.LIVE_ENDED, ended, source="test")
        await _wait_until(lambda: len(sender.bodies) == 2)
        assert sender.bodies[1]["discarded"] is True and sender.bodies[1]["live"] is False
    finally:
        await reporter.stop()


async def test_stop_unsubscribes_cancels_and_closes() -> None:
    sender = FakeSender()
    bus, reporter = _reporter(sender, heartbeat_interval_ms=10, retry_delays_ms=(10_000,))
    await bus.emit(CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=1), source="test")
    await _wait_until(lambda: len(sender.bodies) >= 1)
    failing = FakeSender(failures=100)
    reporter._sender = failing
    source = FakeTranscriptSource()
    reporter._transcript_source = source
    await bus.emit(CoreEvents.LIVE_ENDED, LiveEndedPayload(live_session_id=1, ended_at_ms=2_000), source="test")
    await _wait_until(lambda: bool(reporter._push_tasks))

    await reporter.stop()

    assert failing.closed and reporter._heartbeat_task is None and not reporter._push_tasks
    attempts = failing.attempts
    await bus.emit(CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=2), source="test")
    await bus.wait_until_drained()
    assert failing.attempts == attempts  # 已退订：不再推送


async def test_http_sender_posts_json_with_bearer_token_and_raises_on_rejection() -> None:
    received: List[Dict[str, Any]] = []

    async def handle(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != "Bearer secret":
            return web.Response(status=401, text="unauthorized")
        received.append(await request.json())
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/live-status", handle)
    server = TestServer(app)
    await server.start_server()
    try:
        url = str(server.make_url("/live-status"))
        sender = HttpStatusSender(url=url, token="secret", timeout_ms=2_000)
        await sender.send({"type": "snapshot", "live": True})
        assert received == [{"type": "snapshot", "live": True}]

        wrong = HttpStatusSender(url=url, token="nope", timeout_ms=2_000)
        with pytest.raises(StatusRejected, match="HTTP 401"):
            await wrong.send({"type": "snapshot"})
        await sender.close()
        await wrong.close()
    finally:
        await server.close()


def test_composition_root_builds_reporter_only_when_fully_configured() -> None:
    bus = EventBus()
    assert app_main._build_maibot_sync_reporter(bus, {}) is None
    assert (
        app_main._build_maibot_sync_reporter(bus, {"maibot_sync": {"enabled": True, "url": "http://x/live-status"}})
        is None
    )
    reporter = app_main._build_maibot_sync_reporter(
        bus, {"maibot_sync": {"enabled": True, "url": "http://127.0.0.1:8767/live-status", "token": "t"}}
    )
    assert isinstance(reporter, LiveStatusReporter)
    assert bus.get_listeners_count(CoreEvents.LIVE_STARTED) == 1
    # 没有数据库时只推近况，不推整场记录
    assert reporter._transcript_source is None
