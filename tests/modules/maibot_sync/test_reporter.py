"""直播近况上报器：开播 / 换环节 / 收播快照、心跳、整场记录推送与重试、收尾与 HTTP 推送。

场次回顾推送随旧记忆退役（回顾由记忆账本取代），整场记录改为收播时直接读库推送。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import main as app_main
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.planner import PlannerDecisionPayload
from src.modules.events.payloads.rundown import RundownChangedPayload
from src.modules.maibot_sync import (
    HttpStatusSender,
    LiveStatusReporter,
    StatusRejected,
    TranscriptCandidate,
    TranscriptPushLog,
)


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

    async def list_catch_up_sessions(self, *, limit: int) -> List[TranscriptCandidate]:
        """缺省没有待补推的场次：只测收播推送的用例不受启动补推干扰。"""
        return []

    async def has_spoken_lines(self, live_session_id: int) -> bool:
        return True


class FakeCatchUpSource(FakeTranscriptSource):
    """启动补推的来源：给定候选（按开播倒序）与"没播出过台词"的场次；整场记录带结束时刻。"""

    def __init__(self, candidates: Iterable[Tuple[int, int]], *, silent: Iterable[int] = ()) -> None:
        super().__init__()
        self.candidates = [TranscriptCandidate(live_session_id=sid, ended_at_ms=end) for sid, end in candidates]
        self.ended = {sid: end for sid, end in candidates}
        self.silent = set(silent)
        self.listed_limits: List[int] = []

    async def load(self, live_session_id: int) -> Optional[Dict[str, Any]]:
        body = await super().load(live_session_id)
        return None if body is None else {**body, "ended_at_ms": self.ended[int(live_session_id)]}

    async def list_catch_up_sessions(self, *, limit: int) -> List[TranscriptCandidate]:
        self.listed_limits.append(limit)
        return list(self.candidates)

    async def has_spoken_lines(self, live_session_id: int) -> bool:
        return int(live_session_id) not in self.silent


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


def test_composition_root_keeps_push_marks_beside_the_database(tmp_path: Path) -> None:
    """开了整场记录且有库时，"已处理"标记文件放在库文件旁：没接上就没有启动补推。"""
    bus = EventBus()
    database = SimpleNamespace(sessions=object(), chat=object(), db_path=tmp_path / "amaidesu.db")
    config = {
        "maibot_sync": {
            "enabled": True,
            "url": "http://127.0.0.1:8767/live-status",
            "token": "t",
            "send_transcript": True,
        }
    }
    reporter = app_main._build_maibot_sync_reporter(bus, config, database=database)  # type: ignore[arg-type]
    assert isinstance(reporter, LiveStatusReporter)
    assert reporter._push_log is not None


# ---------------------------------------------------------------------------
# "已处理"标记与启动补推
# ---------------------------------------------------------------------------


async def test_push_log_appends_without_losing_existing_or_bad_lines(tmp_path: Path) -> None:
    """追加写：坏行原样留着、读时跳过；上次写到一半的半行不和新的一行粘在一起。"""
    path = tmp_path / "pushed.jsonl"
    path.write_text('{"session_id": 1, "ended_at_ms": 100}\n乱码一行\n{"session_id": 2, "ended_', encoding="utf-8")
    log = TranscriptPushLog(path)

    await log.mark(3, 300)

    assert await log.read() == {(1, 100), (3, 300)}
    assert await log.contains(3, 300) and not await log.contains(3, 301), "主键相同、结束时刻不同不算推过"
    assert "乱码一行" in path.read_text(encoding="utf-8"), "追加写不重写已有内容"


async def test_push_log_read_failure_is_raised_not_treated_as_empty(tmp_path: Path) -> None:
    """标记文件读不了（这里用同名目录占位）要报出来：当成"都没推过"会把最近的场次整批重推。"""
    log = TranscriptPushLog(tmp_path)
    with pytest.raises(OSError):
        await log.read()


@pytest.mark.parametrize(
    ("failures", "error", "marked"),
    [
        pytest.param(0, None, {(11, 9_000)}, id="delivered"),
        pytest.param(3, StatusRejected("HTTP 403"), {(11, 9_000)}, id="rejected"),
        pytest.param(99, None, set(), id="failed"),
    ],
)
async def test_transcript_push_writes_mark_unless_it_failed(
    tmp_path: Path, failures: int, error: Optional[Exception], marked: set
) -> None:
    """收播推整场记录：送到或被插件明确拒收都记"已处理"；暂时失败不记，留给下次启动补推。"""
    path = tmp_path / "pushed.jsonl"
    # 前两次推送是开播与收播近况；failures=3 时第三次（整场记录）被拒
    sender = FakeSender(failures=failures, error=error)
    source = FakeTranscriptSource({"type": "transcript", "live_session_id": 0, "ended_at_ms": 9_000, "lines": []})
    bus, reporter = _reporter(sender, retry_delays_ms=(1, 1, 1), transcript_source=source, push_log_path=path)
    try:
        await bus.emit(CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=11), source="test")
        await _end_live(bus, 11)
        await _signoff_round_done(bus, 11)
        await _wait_until(lambda: source.asked == [11] and not reporter._push_tasks)
    finally:
        await reporter.stop()
    assert await TranscriptPushLog(path).read() == marked


def _catch_up_reporter(
    sender: FakeSender, source: FakeCatchUpSource, path: Path, *, now_ms: int, window_ms: int
) -> LiveStatusReporter:
    """直接构造并接线（setup 时排出启动补推）；时钟与时间窗由用例给定。"""
    reporter = LiveStatusReporter(
        event_bus=EventBus(),
        sender=sender,
        retry_delays_ms=(1, 1, 1),
        transcript_source=source,
        push_log_path=path,
        catch_up_window_ms=window_ms,
        clock_ms=lambda: now_ms,
    )
    reporter.setup()
    return reporter


async def test_catch_up_pushes_unmarked_recent_sessions_oldest_first(tmp_path: Path) -> None:
    """启动补推：只补时间窗内、没标记、播出过台词的场次，按时间先后补，补完写标记。"""
    path = tmp_path / "pushed.jsonl"
    await TranscriptPushLog(path).mark(4, 4_000)
    # 候选按开播倒序：5 没推、4 已推、3 一句没说、2 没推、1 结束在时间窗之外
    source = FakeCatchUpSource([(5, 5_000), (4, 4_000), (3, 3_000), (2, 2_000), (1, 100)], silent=[3])
    sender = FakeSender()
    reporter = _catch_up_reporter(sender, source, path, now_ms=10_000, window_ms=9_000)
    try:
        await _wait_until(lambda: not reporter._push_tasks)
    finally:
        await reporter.stop()

    assert [body["live_session_id"] for body in sender.bodies] == [2, 5], "旧场先补，其余都不推"
    assert source.listed_limits == [20]
    assert await TranscriptPushLog(path).read() == {(4, 4_000), (2, 2_000), (5, 5_000)}


async def test_catch_up_stops_when_maibot_stays_unreachable(tmp_path: Path) -> None:
    """MaiBot 连不上：第一场重试完仍失败就整批停下，不逐场等满重试，也不写标记。"""
    path = tmp_path / "pushed.jsonl"
    source = FakeCatchUpSource([(2, 2_000), (1, 1_000)])
    sender = FakeSender(failures=99)
    reporter = _catch_up_reporter(sender, source, path, now_ms=3_000, window_ms=10_000)
    try:
        await _wait_until(lambda: not reporter._push_tasks)
    finally:
        await reporter.stop()

    assert sender.attempts == 4, "一场推一次加重试三次，第二场不再尝试"
    assert source.asked == [1]
    assert await TranscriptPushLog(path).read() == set()


async def test_catch_up_is_skipped_when_marks_cannot_be_read(tmp_path: Path) -> None:
    """标记文件读不了：分不清哪些推过，这次整批不补，不把最近的场次重推一遍。"""
    source = FakeCatchUpSource([(2, 2_000), (1, 1_000)])
    sender = FakeSender()
    reporter = _catch_up_reporter(sender, source, tmp_path, now_ms=3_000, window_ms=10_000)
    try:
        await _wait_until(lambda: not reporter._push_tasks)
    finally:
        await reporter.stop()

    assert sender.attempts == 0 and source.asked == []
