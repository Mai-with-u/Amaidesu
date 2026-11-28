"""单实例进程锁与 Dashboard 端口冲突防孤儿进程：双开立停且装配失败自动清理已启组件。"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest

import main as app_main
from src.modules.config.core_schemas import DashboardConfig
from src.modules.dashboard import server as server_module
from src.modules.dashboard.instance_lock import (
    RESTART_HANDOFF_ENV,
    InstanceAlreadyRunningError,
    SingleInstanceLock,
    resolve_instance_lock_path,
)
from src.modules.dashboard.server import DashboardPortInUseError, DashboardServer
from src.modules.dashboard.shutdown import ShutdownJournal
from src.modules.events import EventBus


class _FakeLogStreamer:
    """记录是否已收口停止的日志流替身。"""

    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class _StubConfigService:
    """只提供公开读取方法的极简配置服务替身，避免在测试里改写 ConfigService 私有字段。"""

    def __init__(self, config: Dict[str, Any], collectors_section: Dict[str, Any] | None = None) -> None:
        self.main_config = config
        self._collectors_section = collectors_section or {}

    def get_section(self, name: str, default: Any = None) -> Any:
        return self.main_config.get(name, default)

    def get_file_section(self, name: str) -> Dict[str, Any]:
        if name == "collectors":
            return dict(self._collectors_section)
        return {}


def _minimal_config(db_path: Path, *, dashboard_enabled: bool = True) -> Dict[str, Any]:
    return {
        "general": {"platform_id": "test"},
        "context": {},
        "events": {"history_size": 100},
        "dashboard": {"enabled": dashboard_enabled, "auto_open_browser": False},
        "logging": {"level": "WARNING"},
        "subtitle": {"enabled": False},
        "diagnostics": {"enabled": False},
        "interceptors": {},
        "simulator": {"enabled": False},
        "tools": {},
        "agents": {},
        "memory": {},
        "storage": {},
        "sqlite": {"db_path": str(db_path)},
        "background": {},
        "llm_providers": [],
        "llm": {"client_type": "", "model": ""},
        "llm_fast": {"client_type": "", "model": ""},
    }


def test_single_instance_lock_rejects_duplicate_and_reacquires_after_release(tmp_path: Path) -> None:
    """同路径单实例锁拒绝二次获取并带出持有者 PID，释放后可再次获取，不同数据库目录互不干扰。"""
    lock_a = SingleInstanceLock(tmp_path / "dir_a" / "amaidesu.lock")
    lock_a_dup = SingleInstanceLock(tmp_path / "dir_a" / "amaidesu.lock")
    lock_b = SingleInstanceLock(tmp_path / "dir_b" / "amaidesu.lock")

    lock_a.acquire()
    try:
        assert lock_a.is_acquired is True
        # 不同目录的锁互不冲突（保障并行测试与隔离实例）
        lock_b.acquire()
        assert lock_b.is_acquired is True
        lock_b.release()

        with pytest.raises(InstanceAlreadyRunningError) as exc_info:
            lock_a_dup.acquire()
        assert exc_info.value.lock_path == lock_a.lock_path
        assert exc_info.value.holder_pid == os.getpid()
        assert lock_a_dup.is_acquired is False
    finally:
        lock_a.release()
        lock_b.release()

    # 首个实例释放后，新实例可正常重新获取
    assert lock_a.is_acquired is False
    lock_a_dup.acquire()
    try:
        assert lock_a_dup.is_acquired is True
    finally:
        lock_a_dup.release()


def test_single_instance_lock_waits_for_handoff_and_succeeds_after_delayed_release(tmp_path: Path) -> None:
    """热重启交接传入等待时长时，旧锁在等待期间释放后新实例无缝接管（使用假时钟与回调，零真实 sleep）。"""
    lock_path = tmp_path / "handoff" / "amaidesu.lock"
    old_lock = SingleInstanceLock(lock_path)
    new_lock = SingleInstanceLock(lock_path)
    old_lock.acquire()

    now_s = [0.0]
    sleeps: List[float] = []

    def fake_sleep(dt: float) -> None:
        sleeps.append(dt)
        now_s[0] += dt
        if len(sleeps) == 2:
            old_lock.release()

    try:
        new_lock.acquire(
            wait_timeout_s=5.0,
            poll_interval_s=0.1,
            monotonic_fn=lambda: now_s[0],
            sleep_fn=fake_sleep,
        )
        assert new_lock.is_acquired is True
        assert len(sleeps) == 2
    finally:
        old_lock.release()
        new_lock.release()


def test_single_instance_lock_wait_timeout_still_raises(tmp_path: Path) -> None:
    """热重启等待超时而旧锁仍未释放时，抛出 InstanceAlreadyRunningError 并带出持有者 PID。"""
    lock_path = tmp_path / "timeout" / "amaidesu.lock"
    old_lock = SingleInstanceLock(lock_path)
    new_lock = SingleInstanceLock(lock_path)
    old_lock.acquire()

    now_s = [0.0]

    def fake_sleep(dt: float) -> None:
        now_s[0] += dt

    try:
        with pytest.raises(InstanceAlreadyRunningError) as exc_info:
            new_lock.acquire(
                wait_timeout_s=0.3,
                poll_interval_s=0.1,
                monotonic_fn=lambda: now_s[0],
                sleep_fn=fake_sleep,
            )
        assert exc_info.value.holder_pid == os.getpid()
        assert new_lock.is_acquired is False
    finally:
        old_lock.release()
        new_lock.release()


async def test_graceful_restart_passes_handoff_env_and_releases_instance_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DashboardServer.graceful_restart 拉起新进程时透传热重启交接环境变量，并在停机清理末尾释放单实例锁。"""
    spawned_kwargs: List[Dict[str, Any]] = []
    exits: List[int] = []

    def fake_popen(args: Any, **kwargs: Any) -> object:
        del args
        spawned_kwargs.append(kwargs)
        return object()

    monkeypatch.setattr(server_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_module, "RESTART_SPAWN_DELAY_S", 0.0)

    lock = SingleInstanceLock(tmp_path / "restart.lock")
    lock.acquire()
    server = DashboardServer(
        event_bus=object(),  # type: ignore[arg-type]
        config_service=object(),  # type: ignore[arg-type]
        dashboard_config=DashboardConfig(),
        exit_func=lambda code: exits.append(code),
        shutdown_journal=ShutdownJournal(directory=tmp_path),
        instance_lock=lock,
    )
    try:
        await server.graceful_restart()
        assert len(spawned_kwargs) == 1
        assert spawned_kwargs[0]["env"][RESTART_HANDOFF_ENV] == "1"
        assert lock.is_acquired is False
        assert exits == [0]
    finally:
        lock.release()


def test_resolve_instance_lock_path_follows_sqlite_db_directory(tmp_path: Path) -> None:
    """锁文件路径随 [sqlite].db_path 解析到数据库同目录的 amaidesu.lock。"""
    db_file = tmp_path / "custom" / "live.db"
    resolved = resolve_instance_lock_path({"sqlite": {"db_path": str(db_file)}}, base_dir=tmp_path)
    assert resolved == db_file.with_name("amaidesu.lock")

    default_resolved = resolve_instance_lock_path({}, base_dir=tmp_path)
    assert default_resolved == (tmp_path / "data" / "amaidesu.lock").resolve()


@pytest.mark.parametrize(
    "start_error",
    [
        DashboardPortInUseError("Dashboard 端口绑定失败（127.0.0.1:60214 被占用）", host="127.0.0.1", port=60214),
        OSError(errno.EADDRINUSE, "Address already in use"),
    ],
    ids=["dashboard-port-in-use", "raw-eaddrinuse"],
)
async def test_start_dashboard_reraises_port_in_use_instead_of_swallowing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start_error: Exception
) -> None:
    """Dashboard 端口被占用时 _start_dashboard 向上抛出中止启动并停止日志流，绝不吞异常留下孤儿进程。"""

    class _BusyDashboardServer:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def start(self) -> None:
            raise start_error

    monkeypatch.setattr("main.DashboardServer", _BusyDashboardServer)
    log_streamer = _FakeLogStreamer()
    bus = EventBus()
    cfg = _minimal_config(tmp_path / "busy.db")
    try:
        with pytest.raises(type(start_error)):
            await app_main._start_dashboard(
                {"enabled": True, "auto_open_browser": False},
                True,
                bus,
                _StubConfigService(cfg),  # type: ignore[arg-type]
                log_streamer=log_streamer,
            )
        assert log_streamer.stopped is True
    finally:
        await bus.cleanup()


async def test_start_dashboard_degrades_on_non_port_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非端口占用的普通异常仍按降级禁用处理并返回 None，不误杀正常启动。"""

    class _BrokenDashboardServer:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def start(self) -> None:
            raise RuntimeError("unexpected non-port failure")

    monkeypatch.setattr("main.DashboardServer", _BrokenDashboardServer)
    bus = EventBus()
    cfg = _minimal_config(tmp_path / "broken.db")
    try:
        server = await app_main._start_dashboard(
            {"enabled": True, "auto_open_browser": False},
            False,
            bus,
            _StubConfigService(cfg),  # type: ignore[arg-type]
        )
        assert server is None
    finally:
        await bus.cleanup()


async def test_start_dashboard_handles_windows_10048_chained_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """被包装在 RuntimeError.__cause__ 中的 Windows 10048 同样被识别为端口占用并中止启动。"""
    win_err = OSError(0, "Only one usage of each socket address is normally permitted")
    win_err.winerror = 10048  # type: ignore[attr-defined]
    wrapped = RuntimeError("Dashboard 端口绑定失败")
    wrapped.__cause__ = win_err

    class _WinBusyDashboardServer:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def start(self) -> None:
            raise wrapped

    monkeypatch.setattr("main.DashboardServer", _WinBusyDashboardServer)
    log_streamer = _FakeLogStreamer()
    bus = EventBus()
    cfg = _minimal_config(tmp_path / "winbusy.db")
    try:
        with pytest.raises(RuntimeError, match="端口绑定失败"):
            await app_main._start_dashboard(
                {"enabled": True, "auto_open_browser": False},
                True,
                bus,
                _StubConfigService(cfg),  # type: ignore[arg-type]
                log_streamer=log_streamer,
            )
        assert log_streamer.stopped is True
    finally:
        await bus.cleanup()


async def test_create_app_components_aborts_before_collectors_when_instance_lock_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单实例锁已被同机另一实例持有时，无论生产默认模式（dev_webui=False）还是开发模式，都在建库与启动采集器之前立即中止。"""
    db_path = tmp_path / "prod.db"
    cfg = _minimal_config(db_path, dashboard_enabled=True)
    collector_started: List[bool] = []

    class _SpyCollectorManager:
        def __init__(self) -> None:
            pass

        async def start_all(self) -> None:
            collector_started.append(True)

    monkeypatch.setattr("main.CollectorManager", _SpyCollectorManager)

    holder = SingleInstanceLock(db_path.with_name("amaidesu.lock"))
    holder.acquire()
    try:
        with pytest.raises(InstanceAlreadyRunningError):
            await app_main.create_app_components(
                config=cfg,
                config_service=_StubConfigService(cfg, collectors_section={"enabled": []}),  # type: ignore[arg-type]
                dev_webui=False,
                log_streamer_persist=False,
                startup_effects=False,
            )
        assert collector_started == []
        assert not db_path.exists()
    finally:
        holder.release()


async def test_create_app_components_cleans_up_started_collectors_and_lock_on_dashboard_port_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dashboard 端口冲突导致装配中止时，组合根自动执行反向清理：停止并清理已启动的采集器并释放单实例锁。"""
    db_path = tmp_path / "cleanup.db"
    cfg = _minimal_config(db_path, dashboard_enabled=True)
    lifecycle: List[str] = []

    async def _noop(self: Any, *args: Any, **kwargs: Any) -> None:
        del self, args, kwargs

    class _SpyCollectorManager:
        def __len__(self) -> int:
            return 0

        async def start_all(self) -> None:
            lifecycle.append("collector.start_all")

        async def stop_all(self) -> None:
            lifecycle.append("collector.stop_all")

        async def cleanup_all(self) -> None:
            lifecycle.append("collector.cleanup_all")

    class _BusyDashboardServer:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def start(self) -> None:
            raise DashboardPortInUseError(
                "Dashboard 端口绑定失败（127.0.0.1:60214 被占用）",
                host="127.0.0.1",
                port=60214,
                dev_mode=True,
            )

    monkeypatch.setattr("src.modules.llm.engine.LLMManager.setup", _noop)
    monkeypatch.setattr("src.modules.llm.engine.LLMManager.cleanup", _noop)
    monkeypatch.setattr("main.CollectorManager", _SpyCollectorManager)
    monkeypatch.setattr("main.DashboardServer", _BusyDashboardServer)

    with pytest.raises(DashboardPortInUseError):
        await app_main.create_app_components(
            config=cfg,
            config_service=_StubConfigService(cfg, collectors_section={"enabled": []}),  # type: ignore[arg-type]
            dev_webui=True,
            log_streamer_persist=False,
            startup_effects=False,
        )

    assert lifecycle == ["collector.start_all", "collector.stop_all", "collector.cleanup_all"]

    # 单实例锁也已在失败收口时释放，后续新实例可以正常重新获取
    recheck = SingleInstanceLock(db_path.with_name("amaidesu.lock"))
    recheck.acquire()
    recheck.release()


async def test_create_app_components_consumes_restart_handoff_env_and_wires_lock_to_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """携带热重启交接环境变量启动时，组合根立即消费该环境变量避免向子进程泄漏，并把单实例锁注入 DashboardServer。"""
    db_path = tmp_path / "handoff_wire.db"
    cfg = _minimal_config(db_path, dashboard_enabled=True)
    captured_dashboard_kwargs: List[Dict[str, Any]] = []

    async def _noop(self: Any, *args: Any, **kwargs: Any) -> None:
        del self, args, kwargs

    class _RecordingDashboardServer:
        def __init__(self, **kwargs: Any) -> None:
            captured_dashboard_kwargs.append(kwargs)
            self.widget_service = None
            self.ws_handler = None
            # 与真实 DashboardServer 的约定一致：组合根交进来的日志流由服务在 stop 时停掉。
            # 替身不接管的话，组装函数启动的真日志流会一直留在测试进程里，干扰同进程后面的测试
            self.log_streamer = kwargs.get("log_streamer")

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            if self.log_streamer is not None:
                await self.log_streamer.stop()

        async def cleanup(self) -> None:
            return None

        def get_url(self) -> str:
            return "http://127.0.0.1:0"

    monkeypatch.setenv(RESTART_HANDOFF_ENV, "1")
    monkeypatch.setattr("src.modules.llm.engine.LLMManager.setup", _noop)
    monkeypatch.setattr("src.modules.llm.engine.LLMManager.cleanup", _noop)
    monkeypatch.setattr("main.DashboardServer", _RecordingDashboardServer)

    components = await app_main.create_app_components(
        config=cfg,
        config_service=_StubConfigService(cfg),  # type: ignore[arg-type]
        dev_webui=False,
        log_streamer_persist=False,
        startup_effects=False,
    )
    try:
        assert RESTART_HANDOFF_ENV not in os.environ
        assert components.instance_lock is not None
        assert components.instance_lock.is_acquired is True
        assert captured_dashboard_kwargs[-1]["instance_lock"] is components.instance_lock
    finally:
        await app_main.run_shutdown(components)
    assert components.instance_lock.is_acquired is False
