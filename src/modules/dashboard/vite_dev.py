"""Vite 开发服务器子进程管理

开发模式下拉起/终止 ``dashboard/`` 目录的 Vite dev server（npm 子进程）。
由 DashboardServer 按需持有可选实例，仅 dev_mode 生效。
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional

from src.modules.logging import get_logger

logger = get_logger("ViteDevServer")


class ViteDevServer:
    """Vite 开发服务器子进程的启停管理。"""

    def __init__(self, dashboard_dir: Path, port: int) -> None:
        self.dashboard_dir = dashboard_dir
        self.port = port
        self._process: Optional[asyncio.subprocess.Process] = None

    async def start(self) -> None:
        """启动 Vite 开发服务器子进程（npm run dev；启动失败只记日志不抛出）。"""
        if not self.dashboard_dir.exists():
            logger.error(f"dashboard 目录不存在: {self.dashboard_dir}")
            return

        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        logger.info(f"开发模式：启动 Vite 开发服务器 (cwd={self.dashboard_dir}, port={self.port})")
        try:
            self._process = await asyncio.create_subprocess_exec(
                npm_cmd,
                "run",
                "dev",
                cwd=str(self.dashboard_dir),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            logger.info(f"Vite 已启动 (pid={self._process.pid})，访问 http://localhost:{self.port}")
        except FileNotFoundError:
            logger.error(f"未找到 {npm_cmd}，请先安装 Node.js 与 npm")
            self._process = None
        except Exception as e:
            logger.error(f"启动 Vite 失败: {e}", exc_info=True)
            self._process = None

    async def stop(self) -> None:
        """终止 Vite 开发服务器子进程（5s 未退则强制 kill）。"""
        if not self._process:
            return
        logger.info(f"停止 Vite 开发服务器 (pid={self._process.pid})...")
        try:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Vite 未在 5s 内退出，强制 kill")
                self._process.kill()
                await self._process.wait()
        except Exception as e:
            logger.error(f"停止 Vite 失败: {e}", exc_info=True)
        finally:
            self._process = None
