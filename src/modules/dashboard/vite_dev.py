"""Vite 开发服务器子进程管理

开发模式下拉起/终止 ``dashboard/`` 目录的 Vite dev server（npm 子进程）。
由 DashboardServer 按需持有可选实例，仅 dev_mode 生效。
"""

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Optional, Sequence

from src.modules.logging import get_logger

logger = get_logger("ViteDevServer")


async def terminate_process_tree(process: asyncio.subprocess.Process, *, force: bool = False) -> None:
    """终止子进程及其派生的后代进程。

    npm 会把 vite 本体派成孙进程：只终止 npm 杀不到它，孙进程会继续占着端口，
    让下一次启动的 vite 漂到别的端口去，而前端代理仍指着原来那个端口。

    - Windows 没有"杀进程组"，用 ``taskkill /T`` 递归终止子进程树；
    - POSIX 侧子进程自成进程组（起进程时 ``start_new_session``），整组一起收。
    """
    pid = process.pid
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
        return
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        if pgid == pid:
            os.killpg(pgid, signal.SIGKILL if force else signal.SIGTERM)
        else:
            # 不是进程组组长：杀组会误伤同组的其他进程，只收这一个
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


class ViteDevServer:
    """Vite 开发服务器子进程的启停管理。"""

    def __init__(
        self,
        dashboard_dir: Path,
        port: int,
        backend_host: str,
        backend_port: int,
        command: Optional[Sequence[str]] = None,
    ) -> None:
        self.dashboard_dir = dashboard_dir
        self.port = port
        # 后端实际监听地址：Vite 代理 /api 与 /ws 的目标，改端口后必须同步
        self.backend_host = backend_host
        self.backend_port = backend_port
        # 启动命令可注入：默认 npm run dev，注入时用于换掉要拉起的开发服务器
        self._command = list(command) if command is not None else None
        self._process: Optional[asyncio.subprocess.Process] = None
        self._log_file = None

    async def start(self) -> None:
        """启动 Vite 开发服务器子进程（npm run dev；启动失败只记日志不抛出）。"""
        if not self.dashboard_dir.exists():
            logger.error(f"dashboard 目录不存在: {self.dashboard_dir}")
            return

        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        command = self._command if self._command is not None else [npm_cmd, "run", "dev"]
        logger.info(f"开发模式：启动 Vite 开发服务器 (cwd={self.dashboard_dir}, port={self.port})")
        try:
            # 子进程输出落文件而非 DEVNULL：vite 启动失败（端口占用/依赖缺失）时后端可追溯
            self._log_file = open(self.dashboard_dir / ".vite-dev.log", "w", encoding="utf-8")
            # 通配监听地址不能作为代理目标，统一回落 localhost
            proxy_host = "localhost" if self.backend_host in ("0.0.0.0", "::", "") else self.backend_host
            env = {
                **os.environ,
                "DASHBOARD_VITE_PORT": str(self.port),
                "DASHBOARD_BACKEND_HOST": proxy_host,
                "DASHBOARD_BACKEND_PORT": str(self.backend_port),
            }
            self._process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self.dashboard_dir),
                env=env,
                stdout=self._log_file,
                stderr=asyncio.subprocess.STDOUT,
                # 独立进程组：停止时能连 vite 本体（孙进程）一起收掉
                start_new_session=True,
            )
            logger.info(
                f"Vite 已启动 (pid={self._process.pid})，访问 http://localhost:{self.port}，输出: .vite-dev.log"
            )
        except FileNotFoundError:
            logger.error(f"未找到 {command[0]}，请先安装 Node.js 与 npm")
            self._process = None
        except Exception as e:
            logger.exception(f"启动 Vite 失败: {e}")
            self._process = None

    async def stop(self) -> None:
        """终止 Vite 开发服务器及其子进程树（5s 未退则强制 kill）。"""
        if not self._process:
            if self._log_file:
                self._log_file.close()
                self._log_file = None
            return
        logger.info(f"停止 Vite 开发服务器 (pid={self._process.pid})...")
        try:
            await terminate_process_tree(self._process)
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Vite 未在 5s 内退出，强制 kill")
                await terminate_process_tree(self._process, force=True)
                await self._process.wait()
        except Exception as e:
            logger.exception(f"停止 Vite 失败: {e}")
        finally:
            self._process = None
            if self._log_file:
                self._log_file.close()
                self._log_file = None
