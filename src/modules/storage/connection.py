"""
SQLite 连接管理器

按线程管理连接 + WAL + SAVEPOINT 嵌套事务 + 受管事务深度。

要点：
- 统一 PRAGMA 配置（journal_mode=WAL / synchronous=NORMAL / cache_size=-64000 / temp_store=MEMORY / foreign_keys=ON）
- savepoint 命名前缀用 ``amaidesu_tx_`` 标识本项目事务，避免与其他系统的 savepoint 冲突
- 关库只关空闲连接；正在事务中的连接由持有它的线程在事务收尾后自行关闭，
  关库线程永远不碰别的线程正在用的连接
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock, Thread, current_thread
from typing import Dict, Iterator, Optional, Set


class ManagedSQLiteConnection(sqlite3.Connection):
    """在显式事务中延迟业务方法自行触发的提交和回滚。

    受管事务行为：
    - 当处于受管事务作用域（transaction scope）中时，业务调用 ``commit()`` 不会真正提交，
      业务调用 ``rollback()`` 仅登记"该层需要回滚"
    - ``transaction()`` 上下文退出时按 SAVEPOINT 嵌套语义处理回滚/提交
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._managed_transaction_depth: int = 0
        self._managed_rollback_requests: Set[int] = set()
        self._savepoint_counter: int = 0

    def commit(self) -> None:  # type: ignore[override]
        if self._managed_transaction_depth > 0:
            return
        super().commit()

    def rollback(self) -> None:  # type: ignore[override]
        if self._managed_transaction_depth > 0:
            self._managed_rollback_requests.add(self._managed_transaction_depth)
            return
        super().rollback()

    def next_savepoint_name(self) -> str:
        self._savepoint_counter += 1
        return f"amaidesu_tx_{self._savepoint_counter}"

    def begin_managed_scope(self) -> int:
        self._managed_transaction_depth += 1
        return self._managed_transaction_depth

    def end_managed_scope(self, scope_depth: int) -> bool:
        rollback_requested = scope_depth in self._managed_rollback_requests
        self._managed_rollback_requests.discard(scope_depth)
        self._managed_transaction_depth -= 1
        return rollback_requested

    def force_commit(self) -> None:
        super().commit()

    def force_rollback(self) -> None:
        super().rollback()


class SQLiteConnectionManager:
    """按线程管理 SQLite 连接，并统一事务提交与回滚边界。

    用法（典型 asyncio 调用方）：
        manager = SQLiteConnectionManager(Path("data/amaidesu.db"))
        with manager.transaction() as conn:
            conn.execute("INSERT INTO ...", (...))
        # 通过异步层 SQLiteDatabase/仓储封装后，调用方无需手动 to_thread
    """

    def __init__(self, db_path: Path, *, timeout: float = 30.0) -> None:
        self.db_path = db_path
        self.timeout = timeout
        self._connections: Dict[Thread, ManagedSQLiteConnection] = {}
        # 每个线程当前进行中的事务层数（含嵌套）；关库时据此区分空闲连接与在用连接
        self._active_scopes: Dict[Thread, int] = {}
        self._lock = RLock()
        self._closed = False

    def connection(self) -> ManagedSQLiteConnection:
        """取当前线程的连接（不存在则新建）。

        只有经 ``transaction()`` 使用的连接受关库保护（关库时不会被别的线程关掉）；
        业务读写一律走 ``transaction()``。
        """
        with self._lock:
            return self._connection_locked(current_thread())

    def _connection_locked(self, owner_thread: Thread) -> ManagedSQLiteConnection:
        # 关库后只放行关库前已在事务中的线程（它要开嵌套事务把手头的工作做完），
        # 其余新的取连接请求一律拒绝
        if self._closed and owner_thread not in self._active_scopes:
            raise RuntimeError("SQLite 连接管理器已关闭")
        self._prune_inactive_connections_locked(owner_thread)
        connection = self._connections.get(owner_thread)
        if connection is None:
            connection = self._create_connection()
            self._connections[owner_thread] = connection
        return connection

    def _create_connection(self) -> ManagedSQLiteConnection:
        connection = sqlite3.connect(
            str(self.db_path),
            timeout=self.timeout,
            check_same_thread=False,
            factory=ManagedSQLiteConnection,
        )
        connection.row_factory = sqlite3.Row
        # 统一 PRAGMA 配置
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA cache_size=-64000")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        # 登记在用 -> 开事务或 SAVEPOINT -> 业务读写 -> 提交或回滚 -> 撤销登记；
        # 登记覆盖整个事务期间，关库请求在此期间到达时这条连接不会被别的线程关掉
        connection = self._enter_scope()
        try:
            savepoint_name: Optional[str] = None
            if connection.in_transaction:
                # 嵌套：走 SAVEPOINT（任意深度嵌套事务安全）
                savepoint_name = connection.next_savepoint_name()
                connection.execute(f"SAVEPOINT {savepoint_name}")
            else:
                connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            scope_depth = connection.begin_managed_scope()
            try:
                yield connection
            except BaseException:
                self._rollback_scope(connection, savepoint_name, scope_depth)
                raise
            else:
                rollback_requested = connection.end_managed_scope(scope_depth)
                if rollback_requested:
                    self._rollback_scope_without_depth_change(connection, savepoint_name)
                    raise RuntimeError("事务中的操作请求了回滚")
                if savepoint_name is not None:
                    connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")
                else:
                    connection.force_commit()
        finally:
            self._exit_scope()

    def _enter_scope(self) -> ManagedSQLiteConnection:
        """登记当前线程进入一层事务，返回它的连接。"""
        owner_thread = current_thread()
        with self._lock:
            connection = self._connection_locked(owner_thread)
            self._active_scopes[owner_thread] = self._active_scopes.get(owner_thread, 0) + 1
            return connection

    def _exit_scope(self) -> None:
        """当前线程退出一层事务；关库期间最外层事务收尾时，由本线程自己关闭连接。"""
        owner_thread = current_thread()
        with self._lock:
            depth = self._active_scopes[owner_thread] - 1
            if depth > 0:
                self._active_scopes[owner_thread] = depth
                return
            del self._active_scopes[owner_thread]
            # 未关库：连接留给本线程下次复用；已关库：close_all 当时跳过了这条在用连接，
            # 现在事务已提交或回滚完毕，轮到本线程把它关掉
            if not self._closed:
                return
            connection = self._connections.pop(owner_thread, None)
        if connection is not None:
            connection.close()

    @staticmethod
    def _rollback_scope(
        connection: ManagedSQLiteConnection,
        savepoint_name: Optional[str],
        scope_depth: int,
    ) -> None:
        connection.end_managed_scope(scope_depth)
        SQLiteConnectionManager._rollback_scope_without_depth_change(connection, savepoint_name)

    @staticmethod
    def _rollback_scope_without_depth_change(
        connection: ManagedSQLiteConnection,
        savepoint_name: Optional[str],
    ) -> None:
        if savepoint_name is not None:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        else:
            connection.force_rollback()

    def close_current(self) -> None:
        owner_thread = current_thread()
        with self._lock:
            connection = self._connections.pop(owner_thread, None)
        if connection is not None:
            connection.close()

    def close_all(self) -> None:
        """关库（幂等）：立即关闭空闲连接，在用连接留给持有线程在事务收尾后自行关闭。

        连接按线程持有。停机时常有后台写入（如流程单快照写穿）还在线程池里执行，
        取消对应的 asyncio 任务也停不下已进线程池的同步调用；关库线程若这时关掉
        那个线程的连接，正在执行的语句会访问已释放的内存，整个进程访问违例退出。
        所以本方法只关没有事务在进行的连接，在用的由 ``_exit_scope`` 收尾；关库后
        新事务一律拒绝，已在事务中的线程仍可开嵌套事务做完手头的工作。本方法不等待
        在用连接，返回时它们可能还开着。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            idle_threads = [thread for thread in self._connections if thread not in self._active_scopes]
            idle_connections = [self._connections.pop(thread) for thread in idle_threads]
        for connection in idle_connections:
            connection.close()

    def prune_inactive_connections(self) -> int:
        """关闭已结束线程持有的连接，返回本次回收数量。"""
        with self._lock:
            return self._prune_inactive_connections_locked(current_thread())

    def _prune_inactive_connections_locked(self, owner_thread: Thread) -> int:
        inactive_threads = [
            thread for thread in self._connections if thread is not owner_thread and not thread.is_alive()
        ]
        for thread in inactive_threads:
            self._connections.pop(thread).close()
        return len(inactive_threads)

    @property
    def current(self) -> Optional[sqlite3.Connection]:
        with self._lock:
            return self._connections.get(current_thread())

    @property
    def connection_count(self) -> int:
        with self._lock:
            return len(self._connections)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed


__all__ = ["ManagedSQLiteConnection", "SQLiteConnectionManager"]
