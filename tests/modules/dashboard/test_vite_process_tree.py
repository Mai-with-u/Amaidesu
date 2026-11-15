"""Vite 子进程树回收测试：停止开发服务器时不能只杀 npm、留下 vite 本体占着端口。

npm 会把 vite 本体派成孙进程。只终止 npm 的话孙进程会活下来继续监听端口，
下一次启动的 vite 只能漂到别的端口去，而前端代理仍指着原来那个端口。

测试用真实的进程树：父进程再起一个子进程，两者都长睡；停止父进程后断言
子进程也一并没了。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from src.modules.dashboard.vite_dev import ViteDevServer, terminate_process_tree

# 父进程脚本：再起一个子进程，把子进程 pid 写进文件，然后长睡
_PARENT_SCRIPT = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
with open({pid_file!r}, "w", encoding="utf-8") as handle:
    handle.write(str(child.pid))
time.sleep(120)
"""


def _process_alive(pid: int) -> bool:
    """进程是否还活着（跨平台）。"""
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            check=False,
        )
        # 任务管理器输出是本地代码页，非法字节用替代符跳过即可（pid 是 ASCII）
        stdout = completed.stdout.decode("utf-8", errors="replace")
        for line in stdout.splitlines():
            fields = line.split()
            if len(fields) > 1 and fields[1] == str(pid):
                return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for_pid_file(path: Path, timeout_s: float = 30.0) -> int:
    """等父进程把子进程 pid 写进文件。"""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return int(content)
        await asyncio.sleep(0.1)
    raise AssertionError("父进程未写出子进程 pid")


async def test_terminate_process_tree_kills_descendants(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    parent = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _PARENT_SCRIPT.format(pid_file=str(pid_file)),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        child_pid = await _wait_for_pid_file(pid_file)
        assert _process_alive(child_pid)

        await terminate_process_tree(parent)
        await asyncio.wait_for(parent.wait(), timeout=15.0)

        # 子进程是孙进程：父进程被收掉时必须一并收掉，否则它会一直占着端口
        for _ in range(50):
            if not _process_alive(child_pid):
                break
            await asyncio.sleep(0.1)
        assert not _process_alive(child_pid)
    finally:
        if parent.returncode is None:
            await terminate_process_tree(parent, force=True)


async def test_vite_stop_reclaims_descendant_processes(tmp_path: Path) -> None:
    """经由 ViteDevServer.stop() 也要收掉孙进程——它才是真正占端口的那个。"""
    pid_file = tmp_path / "child.pid"
    server = ViteDevServer(
        dashboard_dir=tmp_path,
        port=60315,
        backend_host="localhost",
        backend_port=60214,
        command=[sys.executable, "-c", _PARENT_SCRIPT.format(pid_file=str(pid_file))],
    )
    await server.start()
    child_pid = await _wait_for_pid_file(pid_file)
    assert _process_alive(child_pid)

    await server.stop()

    for _ in range(50):
        if not _process_alive(child_pid):
            break
        await asyncio.sleep(0.1)
    assert not _process_alive(child_pid)
