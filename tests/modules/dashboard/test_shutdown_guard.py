"""重启时的停机守卫测试：清理超时要留证并强制退出，不能让旧进程永远活着。

覆盖三件事：
- 停机现场日志能落盘（运行期日志广播在停机第二步就摘了，此处之后无处可去）；
- 清理序列卡住时，守卫在时限内把线程栈留在盘上并请求退出；
- 重启编排确实接上了守卫：清理走完就退出，拉不起新进程则不退出。
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from typing import Any, List

import aiohttp
import pytest

import main as app_main
from src.modules.config.core_schemas import DashboardConfig
from src.modules.dashboard import server as server_module
from src.modules.dashboard.instance_lock import SingleInstanceLock
from src.modules.dashboard.server import RESTART_RESUME_SESSION_ENV, DashboardServer
from src.modules.dashboard.shutdown import ShutdownGuard, ShutdownJournal
from src.modules.events import EventBus
from src.modules.llm.engine import LLMManager
from src.modules.llm.payload import Response, Usage
from src.modules.logging.log_streamer import LogStreamer
from src.modules.storage.database import SQLiteDatabase


# --------------------------------------------------------------------- #
# 停机现场日志                                                          #
# --------------------------------------------------------------------- #


def test_journal_record_writes_to_file(tmp_path: Path) -> None:
    journal = ShutdownJournal(directory=tmp_path)
    journal.record("进入停机清理", timeout_s=30.0)

    written = list(tmp_path.glob("*.log"))
    assert len(written) == 1
    content = written[0].read_text(encoding="utf-8")
    assert "进入停机清理" in content
    assert "timeout_s=30.0" in content


def test_journal_dump_stacks_appends_thread_state(tmp_path: Path) -> None:
    journal = ShutdownJournal(directory=tmp_path)
    path = journal.dump_stacks("停机清理超时")

    assert path is not None
    content = path.read_text(encoding="utf-8")
    assert "线程栈现场" in content
    assert "停机清理超时" in content


# --------------------------------------------------------------------- #
# 停机守卫                                                              #
# --------------------------------------------------------------------- #


async def test_guard_completes_sequence_in_time(tmp_path: Path) -> None:
    guard = ShutdownGuard(timeout_s=5.0, journal=ShutdownJournal(directory=tmp_path))
    calls: List[str] = []

    async def sequence() -> None:
        calls.append("cleaned")

    assert await guard.run(sequence) is True
    assert calls == ["cleaned"]
    assert "停机清理完成" in next(tmp_path.glob("*.log")).read_text(encoding="utf-8")


async def test_guard_times_out_records_stacks_and_exits(tmp_path: Path) -> None:
    """清理卡住时：留证 + 请求退出，不让旧进程继续活着。"""
    exits: List[int] = []

    guard = ShutdownGuard(
        timeout_s=0.05,
        journal=ShutdownJournal(directory=tmp_path),
        exit_func=lambda code: exits.append(code),
    )

    async def hanging() -> None:
        await asyncio.sleep(3600)

    assert await guard.run(hanging) is False
    assert exits == [0]

    content = next(tmp_path.glob("*.log")).read_text(encoding="utf-8")
    assert "停机清理超时" in content
    assert "线程栈现场" in content


async def test_guard_passes_sequence_exception_through(tmp_path: Path) -> None:
    guard = ShutdownGuard(timeout_s=5.0, journal=ShutdownJournal(directory=tmp_path))

    async def failing() -> None:
        raise RuntimeError("清理步骤炸了")

    with pytest.raises(RuntimeError, match="清理步骤炸了"):
        await guard.run(failing)

    assert "停机清理异常" in next(tmp_path.glob("*.log")).read_text(encoding="utf-8")


# --------------------------------------------------------------------- #
# 重启编排                                                              #
# --------------------------------------------------------------------- #


def _make_server(exit_func: Any = None, journal: Any = None) -> DashboardServer:
    return DashboardServer(
        event_bus=object(),
        config_service=object(),
        dashboard_config=DashboardConfig(),
        exit_func=exit_func,
        shutdown_journal=journal,
    )


async def test_restart_spawns_new_process_then_exits(monkeypatch: Any, tmp_path: Path) -> None:
    spawned: List[Any] = []
    exits: List[int] = []

    def fake_popen(args: Any, **kwargs: Any) -> object:
        spawned.append(args)
        return object()

    monkeypatch.setattr(server_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_module, "RESTART_SPAWN_DELAY_S", 0.0)

    server = _make_server(
        exit_func=lambda code: exits.append(code),
        journal=ShutdownJournal(directory=tmp_path),
    )
    await server.graceful_restart()

    assert len(spawned) == 1
    assert exits == [0]
    assert "停机清理完成" in next(tmp_path.glob("*.log")).read_text(encoding="utf-8")


async def test_restart_keeps_process_alive_when_spawn_fails(monkeypatch: Any, tmp_path: Path) -> None:
    """拉不起新进程就不能退：退了就没有实例在服务了。"""
    exits: List[int] = []

    def failing_popen(args: Any, **kwargs: Any) -> object:
        raise OSError("拉不起新进程")

    monkeypatch.setattr(server_module.subprocess, "Popen", failing_popen)
    monkeypatch.setattr(server_module, "RESTART_SPAWN_DELAY_S", 0.0)

    server = _make_server(
        exit_func=lambda code: exits.append(code),
        journal=ShutdownJournal(directory=tmp_path),
    )
    await server.graceful_restart()

    assert exits == []


class _FakeRepo:
    """仓储替身：记录被关了几次，可选在关闭时抛异常。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.close_count = 0
        self._fail = fail

    async def close(self) -> None:
        self.close_count += 1
        if self._fail:
            raise RuntimeError("关不掉")


async def test_restart_closes_every_injected_repository(monkeypatch: Any, tmp_path: Path) -> None:
    """每个注入的仓储都要被关掉，不能只关第一个就收手。"""
    monkeypatch.setattr(server_module.subprocess, "Popen", lambda *a, **k: object())
    monkeypatch.setattr(server_module, "RESTART_SPAWN_DELAY_S", 0.0)

    repos = [_FakeRepo() for _ in range(4)]
    server = DashboardServer(
        event_bus=object(),
        config_service=object(),
        dashboard_config=DashboardConfig(),
        exit_func=lambda code: None,
        shutdown_journal=ShutdownJournal(directory=tmp_path),
        viewer_repo=repos[0],
        chat_repo=repos[1],
        llm_repo=repos[2],
        rundown_repo=repos[3],
    )
    await server.graceful_restart()

    assert [repo.close_count for repo in repos] == [1, 1, 1, 1]


async def test_restart_closes_remaining_repositories_when_one_fails(monkeypatch: Any, tmp_path: Path) -> None:
    """一个仓储关不掉不该连累其余的——漏关的连接会带着未落盘的事务留在盘上。"""
    monkeypatch.setattr(server_module.subprocess, "Popen", lambda *a, **k: object())
    monkeypatch.setattr(server_module, "RESTART_SPAWN_DELAY_S", 0.0)

    failing = _FakeRepo(fail=True)
    healthy = _FakeRepo()
    server = DashboardServer(
        event_bus=object(),
        config_service=object(),
        dashboard_config=DashboardConfig(),
        exit_func=lambda code: None,
        shutdown_journal=ShutdownJournal(directory=tmp_path),
        viewer_repo=failing,
        chat_repo=healthy,
    )
    await server.graceful_restart()

    assert healthy.close_count == 1


async def test_restart_passes_active_session_env_and_omits_when_idle(monkeypatch: Any, tmp_path: Path) -> None:
    """热重启拉起新进程时：有进行中场次透传 RESTART_RESUME_SESSION_ENV，无进行中场次不透传。"""
    captured_envs: List[dict[str, str]] = []

    def fake_popen(_args: Any, **kwargs: Any) -> object:
        captured_envs.append(dict(kwargs["env"]))
        return object()

    monkeypatch.setattr(server_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_module, "RESTART_SPAWN_DELAY_S", 0.0)

    class _StubSessionManager:
        def __init__(self, active_pk: int | None) -> None:
            self.active_pk = active_pk

    active_server = DashboardServer(
        event_bus=object(),  # type: ignore[arg-type]
        config_service=object(),  # type: ignore[arg-type]
        dashboard_config=DashboardConfig(),
        session_manager=_StubSessionManager(80),
        exit_func=lambda _code: None,
        shutdown_journal=ShutdownJournal(directory=tmp_path),
    )
    await active_server.graceful_restart()
    assert captured_envs[-1].get(RESTART_RESUME_SESSION_ENV) == "80"

    monkeypatch.setenv(RESTART_RESUME_SESSION_ENV, "999")
    idle_server = DashboardServer(
        event_bus=object(),  # type: ignore[arg-type]
        config_service=object(),  # type: ignore[arg-type]
        dashboard_config=DashboardConfig(),
        session_manager=_StubSessionManager(None),
        exit_func=lambda _code: None,
        shutdown_journal=ShutdownJournal(directory=tmp_path),
    )
    await idle_server.graceful_restart()
    assert RESTART_RESUME_SESSION_ENV not in captured_envs[-1]


async def test_restart_shutdown_waits_for_in_flight_llm_before_closing_sqlite(monkeypatch: Any, tmp_path: Path) -> None:
    """停机序：SQLite 必须晚于 Agent 停止与在途 LLM 出账落库收敛才关闭，绝不报连接管理器已关闭丢账。"""
    db = SQLiteDatabase(tmp_path / "shutdown_llm.db")
    await db.initialize()
    bus = EventBus()

    llm_started = asyncio.Event()
    release_llm = asyncio.Event()
    order: List[str] = []

    class _SlowLLMClient:
        async def generate(self, **_kwargs: Any) -> Response:
            llm_started.set()
            await release_llm.wait()
            order.append("llm_returned")
            return Response(
                success=True,
                content="ok",
                model="m1",
                usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )

        async def cleanup(self) -> None:
            order.append("llm_cleanup")

    class _SpyAgentManager:
        async def stop_supervisor(self) -> None:
            order.append("agent_stop_supervisor")

        async def stop_all(self) -> None:
            order.append("agent_stop_all")
            # 模拟停机触发后在途 LLM 调用在短暂收尾后返回并落账
            release_llm.set()

        async def cleanup_all(self) -> None:
            order.append("agent_cleanup_all")

    class _TrackingDatabase:
        """包装真实 SQLiteDatabase 以记录 close 时机。"""

        def __init__(self, inner: SQLiteDatabase) -> None:
            self._inner = inner

        async def close(self) -> None:
            order.append("db_close")
            await self._inner.close()

    llm = LLMManager(
        llm_repo=db.llm,
        live_session_resolver=lambda: None,
        client_factory=lambda _cfg: _SlowLLMClient(),
    )
    await llm.setup(
        {
            "llm_providers": [{"name": "p1", "client_type": "openai", "api_key": "k", "base_url": "http://localhost"}],
            "llm_models": [{"name": "m1", "api_provider": "p1", "model_identifier": "m1"}],
            "llm_profiles": {"minecraft": {"model_list": ["m1"]}},
        }
    )

    # 先发起一笔在途 LLM 调用，待其真正进入 client.generate 后再触发热重启停机
    in_flight_task = asyncio.create_task(llm.generate("hello", profile="minecraft"))
    await llm_started.wait()

    components = app_main.AppComponents(
        event_bus=bus,
        llm_service=llm,
        dashboard_server=None,
        event_recorder=None,
        collector_manager=None,
        agent_manager=_SpyAgentManager(),  # type: ignore[arg-type]
        simulator_service=None,
        database=_TrackingDatabase(db),  # type: ignore[arg-type]
        storage_ledger=None,
        session_manager=None,  # type: ignore[arg-type]
        tool_registry=None,
        health_monitor=None,
        task_tracker=None,
        tts_diagnostics_recorder=None,
        maibot_sync_reporter=None,
    )

    monkeypatch.setattr(server_module.subprocess, "Popen", lambda *a, **k: object())
    monkeypatch.setattr(server_module, "RESTART_SPAWN_DELAY_S", 0.0)
    exits: List[int] = []
    server = DashboardServer(
        event_bus=bus,
        config_service=object(),  # type: ignore[arg-type]
        dashboard_config=DashboardConfig(),
        exit_func=lambda code: exits.append(code),
        shutdown_journal=ShutdownJournal(directory=tmp_path),
        shutdown_callback=lambda: app_main.run_shutdown(components, settle_active_session=False),
    )

    await server.graceful_restart()
    response = await in_flight_task
    assert response.success is True
    assert exits == [0]
    assert order.index("agent_stop_all") < order.index("llm_cleanup") < order.index("db_close")
    assert order.index("llm_returned") < order.index("db_close")

    # 重新打开同一库文件，验证在途 LLM 账目已真实落库未丢
    verify_db = SQLiteDatabase(tmp_path / "shutdown_llm.db")
    await verify_db.initialize()
    try:
        usage_rows = await verify_db.execute("SELECT model_name, total_tokens FROM llm_usage")
        assert len(usage_rows) == 1
        assert usage_rows[0]["total_tokens"] == 15
    finally:
        await verify_db.close()


async def test_standalone_dashboard_shutdown_stops_agents_and_waits_llm_before_closing_repo(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """未注入组合根回调时，独立 DashboardServer 停机仍按“停 Agent → 停控制台 → 等 LLM → 关仓储”执行。"""
    monkeypatch.setattr(server_module.subprocess, "Popen", lambda *a, **k: object())
    monkeypatch.setattr(server_module, "RESTART_SPAWN_DELAY_S", 0.0)
    events: List[str] = []

    class _StubAgentManager:
        async def stop_supervisor(self) -> None:
            events.append("agent.stop_supervisor")

        async def stop_all(self) -> None:
            events.append("agent.stop_all")

        async def cleanup_all(self) -> None:
            events.append("agent.cleanup_all")

    class _StubLLM:
        async def wait_in_flight_settled(self, *, timeout_s: float = 5.0) -> bool:
            del timeout_s
            events.append("llm.wait_settled")
            return True

        async def cleanup(self) -> None:
            events.append("llm.cleanup")

    class _OrderedRepo:
        async def close(self) -> None:
            events.append("repo.close")

    server = DashboardServer(
        event_bus=object(),  # type: ignore[arg-type]
        config_service=object(),  # type: ignore[arg-type]
        dashboard_config=DashboardConfig(),
        agent_manager=_StubAgentManager(),
        llm_manager=_StubLLM(),
        llm_repo=_OrderedRepo(),
        exit_func=lambda _code: None,
        shutdown_journal=ShutdownJournal(directory=tmp_path),
    )
    await server.graceful_restart()

    assert events == [
        "agent.stop_supervisor",
        "agent.stop_all",
        "agent.cleanup_all",
        "llm.wait_settled",
        "llm.cleanup",
        "repo.close",
    ]


async def test_dashboard_stop_exits_cleanly_with_active_ws_client(tmp_path: Path) -> None:
    """存在存活的浏览器 /ws 长连时，DashboardServer.stop 主动断开连接并快速优雅退出（不卡满 5s 强取消）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]

    bus = EventBus()
    log_streamer = LogStreamer(min_level="WARNING", persist=False, persist_dir=str(tmp_path))
    server = DashboardServer(
        event_bus=bus,
        config_service=object(),  # type: ignore[arg-type]
        dashboard_config=DashboardConfig(host="127.0.0.1", port=free_port, auto_open_browser=False),
        log_streamer=log_streamer,
    )
    await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(f"http://127.0.0.1:{server.port}/ws") as ws:
                welcome = await asyncio.wait_for(ws.receive_json(), timeout=3.0)
                assert welcome["type"] == "connected"
                # 带着存活的 /ws 长连执行停机：必须在 2.5s 内优雅退出，绝不卡满 5s 超时
                await asyncio.wait_for(server.stop(), timeout=2.5)
    finally:
        await server.cleanup()
        await bus.cleanup()


@pytest.mark.parametrize(
    "shutdown_error",
    [asyncio.CancelledError("internal cancel"), RuntimeError("cleanup exploded")],
    ids=["cancelled-error", "runtime-error"],
)
async def test_restart_still_releases_lock_and_exits_when_shutdown_raises(
    monkeypatch: Any, tmp_path: Path, shutdown_error: BaseException
) -> None:
    """新进程已拉起后，即便停机清理抛 CancelledError 或普通异常，仍必须释放单实例锁并调用 exit_func(0) 绝不滞留。"""
    monkeypatch.setattr(server_module.subprocess, "Popen", lambda *a, **k: object())
    monkeypatch.setattr(server_module, "RESTART_SPAWN_DELAY_S", 0.0)

    exits: List[int] = []
    lock = SingleInstanceLock(tmp_path / "restart_fail.lock")
    lock.acquire()

    async def _failing_shutdown() -> None:
        raise shutdown_error

    server = DashboardServer(
        event_bus=object(),  # type: ignore[arg-type]
        config_service=object(),  # type: ignore[arg-type]
        dashboard_config=DashboardConfig(),
        exit_func=lambda code: exits.append(code),
        shutdown_journal=ShutdownJournal(directory=tmp_path),
        instance_lock=lock,
        shutdown_callback=_failing_shutdown,
    )
    try:
        await server.graceful_restart()
        assert lock.is_acquired is False
        assert exits == [0]
        log_text = next(tmp_path.glob("*.log")).read_text(encoding="utf-8")
        assert "热重启清理中断" in log_text
    finally:
        lock.release()


async def test_run_shutdown_distinguishes_external_cancel_from_internal_component_cancel(tmp_path: Path) -> None:
    """run_shutdown：外部超时取消立即向上传播不拖延；内部组件抛 CancelledError 在热重启模式下跑完关库且不重抛。"""
    db_closed = False
    hang_entered = asyncio.Event()
    never = asyncio.Event()

    class _HangingCollector:
        async def stop_all(self) -> None:
            hang_entered.set()
            await never.wait()

        async def cleanup_all(self) -> None:
            return None

    class _SpyDB:
        async def close(self) -> None:
            nonlocal db_closed
            db_closed = True

    # 场景 1：外部取消（如 ShutdownGuard wait_for 到点）必须立刻中断传播，不被 safe_log 吞掉继续跑后续步骤
    external_components = app_main.AppComponents(
        event_bus=None,  # type: ignore[arg-type]
        llm_service=None,  # type: ignore[arg-type]
        dashboard_server=None,
        event_recorder=None,
        collector_manager=_HangingCollector(),  # type: ignore[arg-type]
        agent_manager=None,
        simulator_service=None,
        database=_SpyDB(),  # type: ignore[arg-type]
        storage_ledger=None,
        session_manager=None,  # type: ignore[arg-type]
        tool_registry=None,
        health_monitor=None,
        task_tracker=None,
        tts_diagnostics_recorder=None,
        maibot_sync_reporter=None,
    )
    task = asyncio.create_task(
        app_main.run_shutdown(external_components, settle_active_session=False, reraise_cancelled=False)
    )
    await hang_entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert db_closed is False

    # 场景 2：某子组件自身抛出 CancelledError（非外部取消）且 reraise_cancelled=False → 记日志后继续完成关库并正常返回
    class _CancellingAgentManager:
        async def stop_supervisor(self) -> None:
            return None

        async def stop_all(self) -> None:
            raise asyncio.CancelledError("subtask cancelled")

        async def cleanup_all(self) -> None:
            return None

    lock = SingleInstanceLock(tmp_path / "internal_cancel.lock")
    lock.acquire()
    internal_components = app_main.AppComponents(
        event_bus=None,  # type: ignore[arg-type]
        llm_service=None,  # type: ignore[arg-type]
        dashboard_server=None,
        event_recorder=None,
        collector_manager=None,
        agent_manager=_CancellingAgentManager(),  # type: ignore[arg-type]
        simulator_service=None,
        database=_SpyDB(),  # type: ignore[arg-type]
        storage_ledger=None,
        session_manager=None,  # type: ignore[arg-type]
        tool_registry=None,
        health_monitor=None,
        task_tracker=None,
        tts_diagnostics_recorder=None,
        maibot_sync_reporter=None,
        instance_lock=lock,
    )
    try:
        await app_main.run_shutdown(internal_components, settle_active_session=False, reraise_cancelled=False)
        assert db_closed is True
        assert lock.is_acquired is False
    finally:
        lock.release()
