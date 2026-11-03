"""直播近况上报器：开播 / 换环节 / 收播快照、心跳、回顾重试（插件拒收不重试）、回放场次跳过、收尾与 HTTP 推送。"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import main as app_main
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.recap import StreamerRecapWrittenPayload
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


async def _wait_until(predicate: Callable[[], bool], timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "等待超时"
        await asyncio.sleep(0.005)


def _reporter(sender: FakeSender, **kwargs: Any) -> tuple[EventBus, LiveStatusReporter]:
    bus = EventBus()
    reporter = LiveStatusReporter(event_bus=bus, sender=sender, clock_ms=lambda: 42, **kwargs)
    reporter.setup()
    return bus, reporter


def _rundown(index: int, total: int = 3, title: str = "搭屋顶", segment_id: str = "s") -> RundownChangedPayload:
    return RundownChangedPayload(rundown_id="r1", segment_id=segment_id, segment_title=title, index=index, total=total)


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
        await bus.emit(
            CoreEvents.STREAMER_RECAP_WRITTEN,
            StreamerRecapWrittenPayload(
                live_session_id=3, kind="rehearsal", started_at_ms=1, ended_at_ms=2, recap_text="彩排"
            ),
            source="test",
        )
        await bus.wait_until_drained()
        assert sender.attempts == 0
    finally:
        await reporter.stop()


async def test_rehearsal_recap_backfilled_after_restart_is_not_synced() -> None:
    """进程重启后补写的彩排回顾：新上报器没见过这场的开播事件，仍按事件里的类型跳过。"""
    sender = FakeSender()
    bus, reporter = _reporter(sender)
    try:
        await bus.emit(
            CoreEvents.STREAMER_RECAP_WRITTEN,
            StreamerRecapWrittenPayload(
                live_session_id=4, kind="rehearsal", started_at_ms=1, ended_at_ms=2, recap_text="补写的彩排"
            ),
            source="test",
        )
        await bus.wait_until_drained()
        assert sender.attempts == 0
    finally:
        await reporter.stop()


async def test_recap_retries_until_delivered_and_gives_up_after_budget() -> None:
    sender = FakeSender(failures=2)
    bus, reporter = _reporter(sender, retry_delays_ms=(1, 1, 1))
    recap = StreamerRecapWrittenPayload(
        live_session_id=5, started_at_ms=1_000, ended_at_ms=2_000, recap_text="挖了钻石"
    )
    try:
        await bus.emit(CoreEvents.STREAMER_RECAP_WRITTEN, recap, source="test")
        await _wait_until(lambda: len(sender.bodies) == 1)
        assert sender.attempts == 3
        body = sender.bodies[0]
        assert body["type"] == "recap" and body["text"] == "挖了钻石" and body["live_session_id"] == 5

        hopeless = FakeSender(failures=100)
        bus2, reporter2 = _reporter(hopeless, retry_delays_ms=(1, 1))
        await bus2.emit(CoreEvents.STREAMER_RECAP_WRITTEN, recap, source="test")
        await _wait_until(lambda: hopeless.attempts == 3 and not reporter2._push_tasks)
        await reporter2.stop()
    finally:
        await reporter.stop()


def _recap_payload(live_session_id: int = 5) -> StreamerRecapWrittenPayload:
    return StreamerRecapWrittenPayload(
        live_session_id=live_session_id, started_at_ms=1_000, ended_at_ms=2_000, recap_text="答应了小明明天再播"
    )


async def test_send_recap_on_subscribes_and_pushes_recap() -> None:
    """回顾推送开着（默认）：订阅回顾事件，回顾写好后推给插件，正文原样带上。"""
    sender = FakeSender()
    bus, reporter = _reporter(sender, send_recap=True)
    try:
        assert bus.get_listeners_count(CoreEvents.STREAMER_RECAP_WRITTEN) == 1
        await bus.emit(CoreEvents.STREAMER_RECAP_WRITTEN, _recap_payload(), source="test")
        await _wait_until(lambda: len(sender.bodies) == 1)
        assert sender.bodies[0]["type"] == "recap" and sender.bodies[0]["text"] == "答应了小明明天再播"
    finally:
        await reporter.stop()
    # 收尾时一并退订
    assert bus.get_listeners_count(CoreEvents.STREAMER_RECAP_WRITTEN) == 0


async def test_send_recap_off_neither_subscribes_nor_pushes() -> None:
    """回顾推送关掉：不订阅回顾事件，回顾正文（含观众昵称与付费者）不会推出去，开播近况照常推。"""
    sender = FakeSender()
    bus, reporter = _reporter(sender, send_recap=False)
    try:
        assert bus.get_listeners_count(CoreEvents.STREAMER_RECAP_WRITTEN) == 0
        await bus.emit(CoreEvents.STREAMER_RECAP_WRITTEN, _recap_payload(), source="test")
        await bus.wait_until_drained()
        assert sender.attempts == 0

        # 关回顾不影响近况：开播快照照常推送
        await bus.emit(CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=5, title="t"), source="test")
        await _wait_until(lambda: len(sender.bodies) == 1)
        assert sender.bodies[0]["type"] == "snapshot"
    finally:
        await reporter.stop()
    assert bus.get_listeners_count(CoreEvents.STREAMER_RECAP_WRITTEN) == 0


async def test_send_recap_off_still_pushes_transcript_when_enabled() -> None:
    """只关回顾、整场记录另开：两个开关各管各的——整场记录照推，回顾不推。"""
    sender = FakeSender()
    transcript = {"type": "transcript", "live_session_id": 5, "lines": [{"ts_ms": 1, "role": "viewer", "text": "hi"}]}
    source = FakeTranscriptSource({5: transcript})
    bus, reporter = _reporter(sender, send_recap=False, transcript_source=source)
    try:
        # 整场记录挂在回顾事件上，所以仍要订阅，但回顾正文不发
        assert bus.get_listeners_count(CoreEvents.STREAMER_RECAP_WRITTEN) == 1
        await bus.emit(CoreEvents.STREAMER_RECAP_WRITTEN, _recap_payload(), source="test")
        await _wait_until(lambda: len(sender.bodies) == 1)
        assert [body["type"] for body in sender.bodies] == ["transcript"]
        assert source.loaded == [5]
    finally:
        await reporter.stop()
    assert bus.get_listeners_count(CoreEvents.STREAMER_RECAP_WRITTEN) == 0


async def test_rejected_push_is_not_retried() -> None:
    """插件明确拒收（如没开整场记录回放，回 403）：重试也没用，只推一次。"""
    sender = FakeSender(failures=100, error=StatusRejected("HTTP 403: live_memory.enabled=false"))
    bus, reporter = _reporter(sender, retry_delays_ms=(1, 1, 1))
    try:
        await bus.emit(
            CoreEvents.STREAMER_RECAP_WRITTEN,
            StreamerRecapWrittenPayload(live_session_id=5, started_at_ms=1, ended_at_ms=2, recap_text="x"),
            source="test",
        )
        await _wait_until(lambda: sender.attempts == 1 and not reporter._push_tasks)
        assert sender.attempts == 1
    finally:
        await reporter.stop()


class FakeTranscriptSource:
    """按场次给出整场记录；``broken`` 时模拟读库失败。"""

    def __init__(self, bodies: Dict[int, Dict[str, Any]], broken: bool = False) -> None:
        self.bodies = bodies
        self.broken = broken
        self.loaded: List[int] = []

    async def load(self, live_session_id: int) -> Dict[str, Any] | None:
        self.loaded.append(live_session_id)
        if self.broken:
            raise RuntimeError("数据库被锁")
        return self.bodies.get(live_session_id)


async def test_transcript_follows_recap_with_retry_and_skips_missing_or_broken() -> None:
    sender = FakeSender(failures=1)
    transcript = {"type": "transcript", "live_session_id": 5, "lines": [{"ts_ms": 1, "role": "viewer", "text": "hi"}]}
    source = FakeTranscriptSource({5: transcript})
    bus, reporter = _reporter(sender, retry_delays_ms=(1, 1), transcript_source=source)
    try:
        for live_session_id in (5, 6):
            await bus.emit(
                CoreEvents.STREAMER_RECAP_WRITTEN,
                StreamerRecapWrittenPayload(
                    live_session_id=live_session_id, started_at_ms=1, ended_at_ms=2, recap_text="挖了钻石"
                ),
                source="test",
            )
        await _wait_until(lambda: not reporter._push_tasks and len(sender.bodies) == 3)
        assert source.loaded == [5, 6]
        pushed = [body for body in sender.bodies if body["type"] == "transcript"]
        assert len(pushed) == 1 and pushed[0]["lines"] == transcript["lines"] and pushed[0]["sent_at_ms"] == 42

        broken = FakeTranscriptSource({}, broken=True)
        sender2 = FakeSender()
        bus2, reporter2 = _reporter(sender2, transcript_source=broken)
        await bus2.emit(
            CoreEvents.STREAMER_RECAP_WRITTEN,
            StreamerRecapWrittenPayload(live_session_id=7, started_at_ms=1, ended_at_ms=2, recap_text="x"),
            source="test",
        )
        await _wait_until(lambda: not reporter2._push_tasks and broken.loaded == [7])
        assert [body["type"] for body in sender2.bodies] == ["recap"]
        await reporter2.stop()
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
    await bus.emit(
        CoreEvents.STREAMER_RECAP_WRITTEN,
        StreamerRecapWrittenPayload(live_session_id=1, started_at_ms=1, ended_at_ms=2, recap_text="x"),
        source="test",
    )
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
    # 装配完成即已接线：默认配置下开播与回顾事件都有订阅者
    assert isinstance(reporter, LiveStatusReporter)
    assert bus.get_listeners_count(CoreEvents.LIVE_STARTED) == 1
    assert bus.get_listeners_count(CoreEvents.STREAMER_RECAP_WRITTEN) == 1
    # 没有数据库时只推近况与回顾，不推整场记录
    assert reporter._transcript_source is None


@pytest.mark.parametrize(
    ("overrides", "recap_pushed"),
    [({}, True), ({"send_recap": True}, True), ({"send_recap": False}, False)],
    ids=["default", "recap-on", "recap-off"],
)
async def test_composition_root_passes_send_recap_switch_to_reporter(
    overrides: Dict[str, Any], recap_pushed: bool, loguru_capture: Any
) -> None:
    """组合根把 ``[maibot_sync].send_recap`` 传给上报器：关掉时回顾事件没有订阅者（回顾不会离开本机），
    默认与显式打开时照旧；开播近况不受开关影响，接线日志也按开关如实列出会推的内容。"""
    bus = EventBus()
    maibot_sync = {"enabled": True, "url": "http://127.0.0.1:8767/live-status", "token": "t", **overrides}
    reporter = app_main._build_maibot_sync_reporter(bus, {"maibot_sync": maibot_sync})
    assert isinstance(reporter, LiveStatusReporter)
    try:
        assert bus.get_listeners_count(CoreEvents.STREAMER_RECAP_WRITTEN) == (1 if recap_pushed else 0)
        assert bus.get_listeners_count(CoreEvents.LIVE_STARTED) == 1
    finally:
        await reporter.stop()

    (wired,) = [r["message"] for r in loguru_capture.records if "直播近况同步已接线" in r["message"]]
    assert ("场次回顾" in wired) is recap_pushed and "开播 / 换环节 / 收播" in wired
