"""跨平台单实例进程文件锁：在采集器与外部连接启动之前拦截同机双开，防止孤儿进程抢占直播间连接。

设计原则：
- 锁文件与数据库同目录（``<db_dir>/amaidesu.lock``）：同机共享同一数据库的生产双开必定命中同一把锁，
  而测试或独立隔离目录使用不同数据库路径时互不干扰；
- 使用操作系统内核级非阻塞文件锁（Windows 走 ``msvcrt.locking``，POSIX 走 ``fcntl.flock``）：
  进程异常崩溃或被强杀时操作系统自动回收句柄并释放锁，不会残留假死锁文件；
- 字节 0 专作锁定哨兵（``b"#"``），持有者 PID 写在字节 1 之后：Windows 的 ``msvcrt.locking`` 为强制字节锁，
  错开锁字节与 PID 区后，第二实例在抢锁失败时仍能安全读取到运行中实例的真实 PID；
- 普通双开走 ``wait_timeout_s=0.0`` 立即拒绝；WebUI 热重启交接（``AMAIDESU_RESTART_HANDOFF``）允许有限退避等待旧进程释锁。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, Optional

if os.name == "nt":
    import msvcrt
else:
    import fcntl

__all__ = [
    "DEFAULT_LOCK_FILENAME",
    "RESTART_HANDOFF_ENV",
    "RESTART_HANDOFF_WAIT_S",
    "InstanceAlreadyRunningError",
    "SingleInstanceLock",
    "resolve_instance_lock_path",
]

DEFAULT_LOCK_FILENAME = "amaidesu.lock"
#: WebUI 一键重启（graceful_restart）传给新进程的交接环境变量名
RESTART_HANDOFF_ENV = "AMAIDESU_RESTART_HANDOFF"
#: 热重启交接期新进程等待旧进程完成停机并释放单实例锁的上限（秒）
RESTART_HANDOFF_WAIT_S = 15.0
#: 锁文件字节 0 的锁定哨兵标记（与字节 1 之后的 PID 区错开，避免 Windows 强制字节锁阻断读取 PID）
_LOCK_SENTINEL = b"#"


class InstanceAlreadyRunningError(RuntimeError):
    """同机已有另一 Amaidesu 实例持有进程锁，拒绝双开启动。"""

    def __init__(self, message: str, *, lock_path: Path, holder_pid: Optional[int] = None) -> None:
        super().__init__(message)
        self.lock_path = lock_path
        self.holder_pid = holder_pid


def resolve_instance_lock_path(config: Dict[str, Any], *, base_dir: Optional[Path] = None) -> Path:
    """按 ``[sqlite].db_path`` 推导单实例锁文件路径（与数据库同目录的 ``amaidesu.lock``）。"""
    root = base_dir.resolve() if base_dir is not None else Path.cwd().resolve()
    sqlite_cfg = config.get("sqlite") if isinstance(config, dict) else None
    raw_db = sqlite_cfg.get("db_path") if isinstance(sqlite_cfg, dict) else None
    if isinstance(raw_db, str) and raw_db.strip():
        db_path = Path(raw_db.strip())
        if not db_path.is_absolute():
            db_path = (root / db_path).resolve()
    else:
        db_path = (root / "data" / "amaidesu.db").resolve()
    return db_path.with_name(DEFAULT_LOCK_FILENAME)


def _read_pid_safely(handle: BinaryIO) -> Optional[int]:
    """从字节 1 之后读取持有者 PID（避开字节 0 强制字节锁区域）；读取失败或格式无效返回 None。"""
    try:
        handle.seek(1)
        raw = handle.read(64).decode("ascii", errors="ignore").strip()
        return int(raw) if raw.isdigit() else None
    except OSError:
        return None


class SingleInstanceLock:
    """基于操作系统文件描述符的非阻塞单实例独占锁（支持可选交接等待）。"""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = Path(lock_path)
        self._handle: Optional[BinaryIO] = None

    @property
    def is_acquired(self) -> bool:
        """当前对象是否已持有单实例锁。"""
        return self._handle is not None

    def _try_acquire_once(self) -> None:
        """执行单次非阻塞抢锁；已被其他实例持有时抛出 ``InstanceAlreadyRunningError``。"""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        # 用 a+b 打开避免抢锁失败前截断正在运行实例写入的 PID 信息
        handle = open(self.lock_path, "a+b")
        try:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            holder_pid = _read_pid_safely(handle)
            handle.close()
            pid_hint = f"（PID={holder_pid}）" if holder_pid is not None else ""
            raise InstanceAlreadyRunningError(
                f"检测到已有 Amaidesu 实例正在运行{pid_hint}，锁文件: {self.lock_path}",
                lock_path=self.lock_path,
                holder_pid=holder_pid,
            ) from exc

        # 抢锁成功后：字节 0 写哨兵、字节 1 起写当前进程 PID，写完回到偏移 0 保证后续解锁偏移一致
        handle.seek(0)
        handle.truncate(0)
        handle.write(_LOCK_SENTINEL + f"{os.getpid()}\n".encode("ascii"))
        handle.flush()
        handle.seek(0)
        self._handle = handle

    def acquire(
        self,
        *,
        wait_timeout_s: float = 0.0,
        poll_interval_s: float = 0.1,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        """获取单实例锁；默认非阻塞立即失败，传入 ``wait_timeout_s > 0`` 时按轮询退避等待旧进程释锁。"""
        if self._handle is not None:
            return
        try:
            self._try_acquire_once()
            return
        except InstanceAlreadyRunningError as first_exc:
            if wait_timeout_s <= 0:
                raise
            last_error = first_exc

        deadline = monotonic_fn() + wait_timeout_s
        step = max(poll_interval_s, 0.001)
        while True:
            remaining = deadline - monotonic_fn()
            if remaining <= 0:
                raise last_error
            sleep_fn(min(step, remaining))
            try:
                self._try_acquire_once()
                return
            except InstanceAlreadyRunningError as exc:
                last_error = exc

    def release(self) -> None:
        """幂等释放单实例锁并关闭文件句柄。"""
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                handle.close()
            except OSError:
                pass

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()

    def __del__(self) -> None:
        self.release()
