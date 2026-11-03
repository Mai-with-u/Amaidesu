"""共享长期笔记：正本在本地，写完发布给 MaiBot 插件，读前收下 QQ 里记下的约定。

真本地存储（临时目录）+ 插件替身（``FakeNotesRemote``）：
- 彩排场的笔记不出本地
- 启动后第一次读就把渲染好的笔记发布一次，没变就不重发
- 读之前收下插件那边待收的约定（留底），发布成功后才回报已收；约定重复交来只并一次
- 写入照常核对本地版本，写完发布；恢复旧版本也发布
- MaiBot 没开时读写照常走本地，后台按退避补做发布、收约定与回报
- HTTP 接口：三个动作的请求与应答、读不懂的约定跳过、两边名字对不上告警一次
- 组合根只在开了共享笔记时换成共享存储

渲染与收约定由组合根注入主播记忆的条目格式；这里用替身（``render_view`` / ``append_promises``），
只验证存储在对的时机调用它们、把结果写回本地并发布出去。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import main as app_main
from src.modules.maibot_sync import HttpNotesRemote, PendingPromise, SharedNotesStore
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.long_memory import LongMemoryConflict, LongMemoryStore, content_version
from tests.config.helpers import wait_until
from tests.modules.maibot_sync.fakes import FakeNotesRemote, append_promises, render_view

NOTES = "## 老梗与口头禅\n- 嘴硬〔始于 #1 · 最近 #1〕\n\n## 答应过的事\n- 答应挖钻石〔未兑现 · 始于 #1 · 最近 #1〕"
QQ_PROMISE = "答应群友小明下次直播唱歌"
QQ_LINE = f"- {QQ_PROMISE}〔未兑现 · 来自 QQ群〕"


class _Sleeps:
    """后台补做的等待替身：记下每次等多久，不真等。

    ``blocking`` 时一直等下去（后台补做不插手其他用例的断言，收尾时随 ``close`` 取消）；
    否则立即返回，``on_sleep`` 在第 n 次等待时执行。
    """

    def __init__(self, *, blocking: bool = True) -> None:
        self.delays: List[float] = []
        self.on_sleep: Dict[int, Callable[[], None]] = {}
        self._blocking = blocking

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        if self._blocking:
            await asyncio.Event().wait()
        action = self.on_sleep.get(len(self.delays))
        if action is not None:
            action()


Factory = Callable[..., Tuple[SharedNotesStore, LongMemoryStore]]


@pytest.fixture
async def make_shared(tmp_path: Path) -> AsyncGenerator[Factory, None]:
    """装一个共享存储（本地存储在临时目录）；用例结束时统一收尾，停掉后台补做。"""
    created: List[SharedNotesStore] = []

    def factory(remote: FakeNotesRemote, sleeps: Optional[_Sleeps] = None) -> Tuple[SharedNotesStore, LongMemoryStore]:
        local = LongMemoryStore(tmp_path / "memory")
        shared = SharedNotesStore(
            local=local, remote=remote, render=render_view, absorb=append_promises, sleep=sleeps or _Sleeps()
        )
        created.append(shared)
        return shared, local

    yield factory
    for shared in created:
        await shared.close()


async def test_rehearsal_notes_stay_local(make_shared: Factory) -> None:
    remote = FakeNotesRemote()
    remote.record(QQ_PROMISE)
    shared, local = make_shared(remote)

    await shared.write("rehearsal", "彩排里记的", reason="WebUI 保存")

    assert await shared.read("rehearsal") == "彩排里记的" == await local.read("rehearsal")
    assert remote.calls == 0 and remote.published is None


async def test_first_read_publishes_rendered_notes_once(make_shared: Factory) -> None:
    remote = FakeNotesRemote()
    shared, local = make_shared(remote)
    await local.write("live", NOTES)

    assert await shared.read("live") == NOTES
    assert remote.publishes == [render_view(NOTES)]

    await shared.read("live")
    assert len(remote.publishes) == 1  # 笔记没变就不重发


async def test_qq_promises_are_absorbed_then_acknowledged_after_publishing(make_shared: Factory) -> None:
    """读之前收下 QQ 里记下的约定：并进本地并留底，发布含它的新版本后回报已收；回报丢了重交一次也只并一次。"""
    remote = FakeNotesRemote()
    shared, local = make_shared(remote)
    await local.write("live", NOTES)
    first = remote.record(QQ_PROMISE)

    text, version = await shared.read_versioned("live")

    assert text == NOTES + "\n" + QQ_LINE == await local.read("live") and version == content_version(text)
    entry = (await local.list_history("live"))[0]
    assert entry.reason == "收下 MaiBot 那边记下的约定" and entry.changes == [f"收下：{QQ_PROMISE}"]
    assert remote.published == render_view(text) and remote.acks == [[first]] and remote.inbox == []

    again = remote.record(QQ_PROMISE)
    assert await shared.read("live") == text
    assert remote.acks[-1] == [again] and len(await local.list_history("live")) == 2  # 没再写本地


async def test_promises_are_not_acknowledged_until_published(make_shared: Factory) -> None:
    """刚取完约定插件就断了：约定已并进本地，但发布没成就不回报，补发成功后再回报。"""
    remote = FakeNotesRemote()
    shared, local = make_shared(remote)
    first = remote.record(QQ_PROMISE)
    remote.fail_publish = True

    text = await shared.read("live")

    assert QQ_LINE in text and remote.acks == [] and [item.id for item in remote.inbox] == [first]
    remote.fail_publish = False
    assert await shared.flush() is True
    assert remote.published == render_view(text) == render_view(await local.read("live"))
    assert remote.acks == [[first]]


async def test_writes_check_local_version_and_publish(make_shared: Factory) -> None:
    """收播整理带版本号写：本地这期间被改过就照常报冲突、不发布；写成功后发布新版本。"""
    remote = FakeNotesRemote()
    shared, local = make_shared(remote)
    _text, version = await shared.read_versioned("live")
    await local.write("live", "人在别处改的")

    with pytest.raises(LongMemoryConflict):
        await shared.write("live", NOTES, expected_version=version)
    assert remote.publishes == [""]  # 只有启动后第一次读时发布的空笔记

    saved = await shared.write("live", NOTES, reason="收播整理（场次 #2）", session_id=2)
    assert saved == NOTES and remote.published == render_view(NOTES)
    assert (await local.list_history("live"))[0].session_id == 2


async def test_offline_writes_stay_local_and_retry_backs_off(make_shared: Factory) -> None:
    """MaiBot 没开时照常收播整理；后台按退避补做，上线后收下这期间的约定、发布并回报。"""
    remote = FakeNotesRemote()
    sleeps = _Sleeps(blocking=False)
    shared, local = make_shared(remote, sleeps)
    first = remote.record(QQ_PROMISE)
    remote.down = True
    sleeps.on_sleep[3] = lambda: setattr(remote, "down", False)  # 第三次等待之后 MaiBot 上线

    await shared.write("live", NOTES, reason="收播整理（场次 #2）")
    assert await local.read("live") == NOTES and remote.published is None
    await wait_until(lambda: bool(remote.acks), what="后台补做完成")

    assert sleeps.delays == [30.0, 120.0, 600.0]
    assert await local.read("live") == NOTES + "\n" + QQ_LINE
    assert remote.published == render_view(NOTES + "\n" + QQ_LINE) and remote.acks == [[first]]
    await shared.close()
    assert remote.closed


async def test_restore_publishes_restored_version(make_shared: Factory) -> None:
    remote = FakeNotesRemote()
    shared, local = make_shared(remote)
    await shared.write("live", NOTES, reason="WebUI 保存")
    await shared.write("live", NOTES + "\n- 改坏了", reason="WebUI 保存")
    latest = (await local.list_history("live"))[0]

    assert await shared.restore("live", latest.id) == NOTES
    assert remote.published == render_view(NOTES)
    assert await shared.restore("live", "不存在") is None


# ---------------------------------------------------------------------------
# HTTP 接口
# ---------------------------------------------------------------------------


async def test_http_remote_contract_and_name_check(loguru_capture: Any) -> None:
    bodies: List[Dict[str, Any]] = []
    pending: List[Any] = [{"id": "a1", "text": QQ_PROMISE, "source": "QQ群"}, {"id": "", "text": "缺编号"}, "乱写的"]

    async def handle(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != "Bearer secret":
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        body = await request.json()
        bodies.append(body)
        names = {"maibot": ["昔涟"], "match": False}
        if body["action"] == "promises":
            return web.json_response({"ok": True, "promises": pending, "names": names})
        return web.json_response({"ok": True, "names": names})

    app = web.Application()
    app.router.add_post("/notes", handle)
    server = TestServer(app)
    await server.start_server()
    try:
        url = str(server.make_url("/notes"))
        remote = HttpNotesRemote(url=url, token="secret", timeout_ms=2_000, persona_name="麦麦")
        await remote.publish("给 QQ 看的笔记")
        assert await remote.promises() == [PendingPromise(id="a1", text=QQ_PROMISE, source="QQ群")]
        await remote.acknowledge(["a1"])
        assert bodies == [
            {"action": "publish", "text": "给 QQ 看的笔记", "persona_name": "麦麦"},
            {"action": "promises", "persona_name": "麦麦"},
            {"action": "ack", "ids": ["a1"], "persona_name": "麦麦"},
        ]

        warnings = [r["message"] for r in loguru_capture.records if r["level"] == "WARNING"]
        assert sum("读不懂的约定" in message for message in warnings) == 2
        name_warnings = [message for message in warnings if "昔涟" in message]
        assert len(name_warnings) == 1 and "麦麦" in name_warnings[0]  # 同一组名字只告警一次

        wrong = HttpNotesRemote(url=url, token="nope", timeout_ms=2_000)
        with pytest.raises(RuntimeError, match="HTTP 401"):
            await wrong.promises()
        await remote.close()
        await wrong.close()
    finally:
        await server.close()


async def test_composition_root_no_longer_shares_notes(store: SQLiteDatabase) -> None:
    """阶段 3b 停用了共享长期笔记：不管配置怎么写，组合根都只给本地文件、不会再构造共享存储。

    长期记忆的写入与注入由记忆账本取代，MEMORY.md 只剩文件本体（阶段 4 删除）；
    [maibot_sync].share_notes 这个配置键随之失效。
    """
    sync = {"enabled": True, "url": "http://127.0.0.1:8767/live-status", "token": "t"}
    assert app_main._build_long_memory_store({}, store) == (store.long_memory, None)
    assert app_main._build_long_memory_store({"maibot_sync": sync}, store) == (store.long_memory, None)
