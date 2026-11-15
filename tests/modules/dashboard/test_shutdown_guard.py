"""重启时的停机守卫测试：清理超时要留证并强制退出，不能让旧进程永远活着。

覆盖三件事：
- 停机现场日志能落盘（运行期日志广播在停机第二步就摘了，此处之后无处可去）；
- 清理序列卡住时，守卫在时限内把线程栈留在盘上并请求退出；
- 重启编排确实接上了守卫：清理走完就退出，拉不起新进程则不退出。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, List

import pytest

from src.modules.config.core_schemas import DashboardConfig
from src.modules.dashboard import server as server_module
from src.modules.dashboard.server import DashboardServer
from src.modules.dashboard.shutdown import ShutdownGuard, ShutdownJournal


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
