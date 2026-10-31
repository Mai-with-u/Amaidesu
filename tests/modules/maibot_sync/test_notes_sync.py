"""共享长期笔记：正本在 MaiBot 插件、本地 MEMORY.md 当缓存。

真本地存储（临时目录）+ 正本替身（``FakeNotesRemote``）：
- 彩排场的笔记不出本地；第一次连上把本地已有的笔记推上去，新装的从正本取回
- 本地没改过时采纳正本（QQ 里记下的约定）并留底；带版本号的写入在正本被改过时报冲突
- MaiBot 没开时读写都落本地、照常可用，后台按退避补推；两边都改过时三方合并
- 不带版本号的写入（WebUI 保存、恢复旧版本）把正本这期间的改动合并进来
- HTTP 正本：读写契约、版本冲突、两边名字对不上告警一次
- 组合根只在开了共享笔记时换成共享存储

三方合并由组合根注入主播记忆的条目合并；这里用按行取并集的简单合并，只验证存储
把"上次同步的版本 / 本地 / 正本"三份交给它、并把结果推上去。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import main as app_main
from src.modules.maibot_sync import HttpNotesRemote, NotesConflict, SharedNotesStore
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.long_memory import LongMemoryConflict, LongMemoryStore, content_version
from tests.config.helpers import wait_until
from tests.modules.maibot_sync.fakes import FakeNotesRemote

# "答应过的事"排在最后：QQ 里记下的约定追加到这一节，也就是全文末尾
NOTES = "## 老梗与口头禅\n- 嘴硬〔始于 #1 · 最近 #1〕\n\n## 答应过的事\n- 答应挖钻石〔未兑现 · 始于 #1 · 最近 #1〕"
QQ_LINE = "- 答应群友小明下次直播唱歌〔未兑现 · 来自 QQ群〕"


class _UnionReconcile:
    """按行取并集的三方合并：保留本地，再补上对方新加的行；记下每次收到的三份全文。"""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, str, str]] = []

    def __call__(self, base: str, local: str, remote: str) -> Tuple[str, List[str]]:
        self.calls.append((base, local, remote))
        known = set(base.splitlines()) | set(local.splitlines())
        added = [line for line in remote.splitlines() if line not in known]
        return "\n".join([*local.splitlines(), *added]).strip(), [f"对方新增：{line}" for line in added]


class _Sleeps:
    """后台补推的等待替身：记下每次等多久，不真等。

    ``blocking`` 时一直等下去（后台补推不插手其他用例的断言，收尾时随 ``close`` 取消）；
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


Factory = Callable[..., Tuple[SharedNotesStore, LongMemoryStore, _UnionReconcile]]


@pytest.fixture
async def make_shared(tmp_path: Path) -> AsyncGenerator[Factory, None]:
    """装一个共享存储（本地存储在临时目录）；用例结束时统一收尾，停掉后台补推。"""
    created: List[SharedNotesStore] = []

    def factory(
        remote: FakeNotesRemote, sleeps: Optional[_Sleeps] = None
    ) -> Tuple[SharedNotesStore, LongMemoryStore, _UnionReconcile]:
        local = LongMemoryStore(tmp_path / "memory")
        reconcile = _UnionReconcile()
        shared = SharedNotesStore(
            local=local,
            remote=remote,
            reconcile=reconcile,
            state_path=tmp_path / "memory" / "maibot" / "synced.md",
            sleep=sleeps or _Sleeps(),
        )
        created.append(shared)
        return shared, local, reconcile

    yield factory
    for shared in created:
        await shared.close()


async def test_rehearsal_notes_stay_local(make_shared: Factory) -> None:
    remote = FakeNotesRemote(NOTES)
    shared, local, _reconcile = make_shared(remote)

    await shared.write("rehearsal", "彩排里记的", reason="WebUI 保存")

    assert await shared.read("rehearsal") == "彩排里记的" == await local.read("rehearsal")
    assert remote.fetches == 0 and remote.pushes == []


async def test_first_contact_pushes_local_notes_then_adopts_qq_additions(make_shared: Factory) -> None:
    """只用过 Amaidesu 的笔记第一次连上就推给正本；之后 QQ 里记下的约定读回本地并留底。"""
    remote = FakeNotesRemote()
    shared, local, _reconcile = make_shared(remote)
    await local.write("live", NOTES)

    assert await shared.read("live") == NOTES
    assert remote.text == NOTES and remote.pushes[0]["reason"] == "补推本地改动"

    remote.append(QQ_LINE)
    text, version = await shared.read_versioned("live")
    assert QQ_LINE in text and version == content_version(text)
    assert await local.read("live") == text
    entry = (await local.list_history("live"))[0]
    assert entry.reason == "从 MaiBot 同步" and entry.changes == [f"对方新增：{QQ_LINE}"]

    await shared.read("live")
    assert len(await local.list_history("live")) == 2  # 没有新改动就不再留底


async def test_fresh_install_takes_notes_from_maibot(make_shared: Factory) -> None:
    remote = FakeNotesRemote(NOTES)
    shared, local, _reconcile = make_shared(remote)

    assert await shared.read("live") == NOTES == await local.read("live")
    assert remote.pushes == []


async def test_versioned_write_conflicts_when_maibot_changed_since_read(make_shared: Factory) -> None:
    """收播整理读完笔记、模型还在想时，QQ 里记下了一件约定：写入报冲突，在新版本上重做后两边一致。"""
    remote = FakeNotesRemote(NOTES)
    shared, local, _reconcile = make_shared(remote)
    _text, version = await shared.read_versioned("live")
    remote.append(QQ_LINE)

    with pytest.raises(LongMemoryConflict):
        await shared.write("live", NOTES + "\n- 新梗〔始于 #2 · 最近 #2〕", expected_version=version)
    assert await local.read("live") == NOTES  # 没写本地

    text, version = await shared.read_versioned("live")
    merged = text + "\n- 新梗〔始于 #2 · 最近 #2〕"
    saved = await shared.write("live", merged, expected_version=version, reason="收播整理（场次 #2）", session_id=2)

    assert saved == merged == remote.text == await local.read("live")
    assert (await local.list_history("live"))[0].session_id == 2


async def test_offline_writes_stay_local_and_merge_when_maibot_is_back(make_shared: Factory) -> None:
    """MaiBot 没开时照常开播与收播整理；连上后与这期间 QQ 里的改动三方合并，两边一致。"""
    remote = FakeNotesRemote(NOTES)
    shared, local, reconcile = make_shared(remote)
    await shared.read("live")
    remote.down = True

    text, version = await shared.read_versioned("live")
    assert text == NOTES  # 用本地缓存
    offline = NOTES + "\n- 新梗〔始于 #2 · 最近 #2〕"
    await shared.write("live", offline, expected_version=version, reason="收播整理（场次 #2）")
    assert await local.read("live") == offline and remote.pushes == []

    remote.down = False
    remote.append(QQ_LINE)
    assert await shared.flush() is True

    base, local_seen, remote_seen = reconcile.calls[-1]
    assert (base, local_seen) == (NOTES, offline) and remote_seen.endswith(QQ_LINE)
    assert remote.text == await local.read("live") == offline + "\n" + QQ_LINE
    assert remote.pushes[-1]["reason"] == "与 MaiBot 上的正本合并"
    assert await shared.flush() is True  # 已经没有待推的


async def test_background_retry_backs_off_until_maibot_is_back(make_shared: Factory) -> None:
    remote = FakeNotesRemote(NOTES)
    sleeps = _Sleeps(blocking=False)
    shared, local, _reconcile = make_shared(remote, sleeps)
    _text, version = await shared.read_versioned("live")
    remote.down = True
    sleeps.on_sleep[3] = lambda: setattr(remote, "down", False)  # 第三次等待之后 MaiBot 上线

    await shared.write("live", NOTES + "\n- 新梗〔始于 #2〕", expected_version=version)
    await wait_until(lambda: bool(remote.pushes), what="后台补推成功")

    assert sleeps.delays == [30.0, 120.0, 600.0]
    assert remote.text == await local.read("live")
    await shared.close()
    assert remote.closed


async def test_unversioned_save_keeps_qq_additions_since_last_sync(make_shared: Factory) -> None:
    """WebUI 保存不带版本号：正本自上次同步以来记下的约定合并进来，不被整份覆盖掉。"""
    remote = FakeNotesRemote(NOTES)
    shared, local, _reconcile = make_shared(remote)
    await shared.read("live")
    remote.append(QQ_LINE)

    edited = NOTES + "\n- 床名叫咕咕床"
    saved = await shared.write("live", edited, reason="WebUI 保存")

    assert saved == remote.text == await local.read("live") == edited + "\n" + QQ_LINE
    entry = (await local.list_history("live"))[0]
    assert entry.reason == "WebUI 保存" and entry.changes == [f"对方新增：{QQ_LINE}"]


async def test_push_conflict_while_aligning_is_refetched(make_shared: Factory) -> None:
    """补推本地改动的那一刻 QQ 里又记下一件：重新拉正本合并后再推，不丢任何一边。"""
    remote = FakeNotesRemote()
    shared, local, _reconcile = make_shared(remote)
    await local.write("live", NOTES)
    remote.before_push = lambda: remote.append(QQ_LINE)

    text = await shared.read("live")

    assert text == remote.text == await local.read("live") == NOTES + "\n" + QQ_LINE


async def test_restore_writes_through_maibot(make_shared: Factory) -> None:
    remote = FakeNotesRemote()
    shared, local, _reconcile = make_shared(remote)
    await shared.write("live", NOTES, reason="WebUI 保存")
    await shared.write("live", NOTES + "\n- 改坏了", reason="WebUI 保存")
    latest = (await local.list_history("live"))[0]

    assert await shared.restore("live", latest.id) == NOTES
    assert remote.text == NOTES == await local.read("live")
    assert await shared.restore("live", "不存在") is None


# ---------------------------------------------------------------------------
# HTTP 正本
# ---------------------------------------------------------------------------


async def test_http_remote_contract_conflict_and_name_check(loguru_capture: Any) -> None:
    state: Dict[str, Any] = {"text": NOTES, "bodies": []}

    async def handle(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != "Bearer secret":
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        body = await request.json()
        state["bodies"].append(body)
        names = {"maibot": ["昔涟"], "match": False}
        if body["action"] == "put":
            if body["expected_version"] != content_version(state["text"]):
                return web.json_response({"ok": False, "error": "版本对不上"}, status=409)
            state["text"] = body["text"]
        return web.json_response({"ok": True, "text": state["text"], "names": names})

    app = web.Application()
    app.router.add_post("/notes", handle)
    server = TestServer(app)
    await server.start_server()
    try:
        url = str(server.make_url("/notes"))
        remote = HttpNotesRemote(url=url, token="secret", timeout_ms=2_000, persona_name="麦麦")
        fetched = await remote.fetch()
        assert (fetched.text, fetched.version) == (NOTES, content_version(NOTES))
        assert state["bodies"][0] == {"action": "get", "persona_name": "麦麦"}

        pushed = await remote.push(
            NOTES + "\n- 新梗", expected_version=fetched.version, reason="收播整理", changes=["新增"]
        )
        assert pushed.text == NOTES + "\n- 新梗" and state["bodies"][1]["changes"] == ["新增"]
        with pytest.raises(NotesConflict):
            await remote.push("x", expected_version=fetched.version, reason="", changes=[])

        warnings = [r for r in loguru_capture.records if r["level"] == "WARNING" and "昔涟" in r["message"]]
        assert len(warnings) == 1 and "麦麦" in warnings[0]["message"]  # 同一组名字只告警一次

        wrong = HttpNotesRemote(url=url, token="nope", timeout_ms=2_000)
        with pytest.raises(RuntimeError, match="HTTP 401"):
            await wrong.fetch()
        await remote.close()
        await wrong.close()
    finally:
        await server.close()


async def test_composition_root_shares_notes_only_when_enabled(store: SQLiteDatabase) -> None:
    sync = {"enabled": True, "url": "http://127.0.0.1:8767/live-status", "token": "t"}
    assert app_main._build_long_memory_store({}, store) == (store.long_memory, None)
    assert app_main._build_long_memory_store({"maibot_sync": {**sync, "share_notes": False}}, store)[1] is None

    config = {"maibot_sync": sync, "agents": {"streamer": {"persona": {"bot_name": "麦麦"}}}}
    backend, shared = app_main._build_long_memory_store(config, store)
    assert isinstance(shared, SharedNotesStore) and backend is shared
    assert backend.path_of("live") == store.long_memory.path_of("live")
    await shared.close()
