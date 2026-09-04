"""SQLiteAgendaStore - AgendaStore 协议的 SQLite 实现（Wave 6 / §1.7 Storage 适配）

§1.7 持久化定案：
- 原始大纲 → ``agenda_plan`` 表（只读基准；本模块不直接写，由 agenda_loader.load → parse_agenda_toml 注入）
- 运行进度 → ``agenda_runtime`` 表（Agent 改；本模块提供 CRUD）
- 持久化走已定 Storage Schema（11 schema 表 + schema_migrations）；无需新 Storage 接口

本模块把 ``agenda_runtime`` 表的读写封装成 duck-typed 协议 ``AgendaStore``，
供 ``AgendaState.persist_runtime()`` / ``AgendaState.restore_runtime()`` 调用。

实现要点：
- ``load_agenda_plan`` 不实现（W6 不在 Storage 中存储原始大纲；由 agenda_loader 直接解析 TOML）
- ``dump_agenda_runtime`` / ``append_agenda_runtime`` / ``delete_agenda_runtime`` 提供完整 CRUD
- 全部操作走 SQLiteStore.execute() / execute_fetchone()（线程安全 + async 友好）
"""

from __future__ import annotations

from typing import Any, List, Optional

from src.modules.logging import get_logger

__all__ = ["SQLiteAgendaStore"]


class SQLiteAgendaStore:
    """AgendaStore 协议的 SQLite 实现。

    用法：
        store = SQLiteAgendaStore(sqlite_store)
        rows = await store.dump_agenda_runtime(agenda_id="agenda_2025_xmas")

    注：``agenda_id`` 是业务标识（对应 Agenda.agenda_id），在 SQLite 中映射到
    ``agenda_runtime.live_session_id``（约定：live_session_id 字段存 agenda_id
    以避免引入新表）；后续可扩展独立的 agenda_id 列。
    """

    def __init__(self, sqlite_store: Any) -> None:
        """初始化。

        Args:
            sqlite_store: ``SQLiteStore`` 实例（提供 async execute / execute_fetchone）
        """
        self._store = sqlite_store
        self._logger = get_logger("SQLiteAgendaStore")

    # --------- interface methods (duck-typed AgendaStore) ---------

    async def load_agenda_plan(self, agenda_id: str) -> Optional[dict]:
        """加载 agenda_plan 行。W6 不实现原始大纲持久化（保留接口兼容性）。"""
        return None  # 原始大纲走 TOML 解析，不入 SQLite

    async def dump_agenda_runtime(self, agenda_id: str) -> List[dict]:
        """从 agenda_runtime 表读取所有运行进度行。

        Args:
            agenda_id: 业务标识（Agenda.agenda_id）

        Returns:
            行 dict 列表（每个含 label / order / starts_at_ms / expected_ms / done / current / note / inserted_by）
        """
        sql = (
            'SELECT label, "order", starts_at_ms, expected_ms, done, current, note, inserted_by '
            'FROM agenda_runtime WHERE live_session_id = ? ORDER BY "order"'
        )
        try:
            rows = await self._store.execute(sql, (agenda_id,))
            return [dict(row) for row in rows]
        except Exception as exc:
            self._logger.warning(f"dump_agenda_runtime 失败: {exc}")
            return []

    async def save_agenda_runtime(self, runtime_rows: List[dict]) -> None:
        """覆盖式写入 runtime 行（先删后插）。

        Args:
            runtime_rows: 行 dict 列表（每个含 live_session_id / label / order / ...）
        """
        if not runtime_rows:
            return
        agenda_id = runtime_rows[0].get("live_session_id", "")
        if not agenda_id:
            self._logger.warning("save_agenda_runtime: 缺 live_session_id，跳过")
            return
        try:
            await self._store.execute(
                "DELETE FROM agenda_runtime WHERE live_session_id = ?",
                (agenda_id,),
            )
            await self.append_agenda_runtime(runtime_rows)
        except Exception as exc:
            self._logger.warning(f"save_agenda_runtime 失败: {exc}")

    async def append_agenda_runtime(self, runtime_rows: List[dict]) -> None:
        """追加新行（INSERT OR REPLACE）。

        Args:
            runtime_rows: 行 dict 列表
        """
        for row in runtime_rows:
            sql = (
                "INSERT OR REPLACE INTO agenda_runtime "
                '(live_session_id, plan_id, label, "order", starts_at_ms, expected_ms, done, current, note, inserted_by) '
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )
            params = (
                row.get("live_session_id", ""),
                row.get("plan_id"),
                row.get("label", ""),
                row.get("order", 0),
                row.get("starts_at_ms", 0),
                row.get("expected_ms", 0),
                row.get("done", 0),
                row.get("current", 0),
                row.get("note"),
                row.get("inserted_by", "ai"),
            )
            try:
                await self._store.execute(sql, params)
            except Exception as exc:
                self._logger.warning(f"append_agenda_runtime 单行失败: {exc}")

    async def delete_agenda_runtime(self, agenda_id: str) -> None:
        """删除指定 agenda_id 的全部 runtime 行。

        Args:
            agenda_id: 业务标识
        """
        try:
            await self._store.execute(
                "DELETE FROM agenda_runtime WHERE live_session_id = ?",
                (agenda_id,),
            )
        except Exception as exc:
            self._logger.warning(f"delete_agenda_runtime 失败: {exc}")
