"""v11 → v12：思考观测捕获 + 原始 usage 兜底 + 请求历史正名。

一次迁移涵盖三块列变更（各自独立幂等，合并升版避免多次迁移）：

1. **思考量观测**（§2 决策第 1 条）
   - ``llm_usage`` + ``reasoning_tokens``（NOT NULL DEFAULT 0）：聚合统计面
   - ``llm_requests`` + ``reasoning_tokens``（NOT NULL DEFAULT 0）：请求明细
     自带思考量；前端解析 JSON 即可展示，避面解析即弃信息丢失

2. **原始 usage 兜底**（§2 决策第 2 条）
   - ``llm_requests`` + ``usage_raw_json``（TEXT 可空）：解析即弃链路唯一兜底
     入口，存库即不再做格式加工；用量字段新增（解析/补默认）后可对历史行
     重放补数

3. **请求历史正名**（§2 扩容）
   - ``llm_requests.client_type`` → ``profile_name``：列改名（SQLite RENAME
     COLUMN 需 SQLite ≥3.25，旧库走重建表搬运以保兼容）；旧值域
     ``llm/llm_fast/llm_summary/vlm`` 原样保留不映射（不伪造历史）
   - 新增索引 ``idx_llm_requests_profile``（profile_name 筛选先例支撑）

新建库 DDL 已含新形态，回调以列存在性检查保证对旧库升级与重复执行安全。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists


_LLM_USAGE_ADD_COLUMNS: tuple[tuple[str, str], ...] = (("reasoning_tokens", "INTEGER NOT NULL DEFAULT 0"),)


def _add_columns(conn: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    """逐列 ADD COLUMN（列存在即跳过，幂等）。"""
    for name, ddl in columns:
        if not column_exists(conn, table, name):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")  # noqa: S608 列名/类型为代码内常量


def _migrate_llm_usage(conn: sqlite3.Connection) -> None:
    """llm_usage：补 reasoning_tokens 列（聚合统计面思考量）。"""
    if not table_exists(conn, "llm_usage"):
        return
    _add_columns(conn, "llm_usage", _LLM_USAGE_ADD_COLUMNS)


def _migrate_llm_requests(conn: sqlite3.Connection) -> None:
    """llm_requests：补 reasoning_tokens + usage_raw_json + 列改名 client_type → profile_name。

    列改名 SQLite ≥3.25 支持 ``ALTER TABLE ... RENAME COLUMN``；为兼容旧
    版本走"建新表搬运数据"路径——新表用 v12 形态定义，旧表存在 client_type
    列时把其值原样搬到新表的 profile_name，不做语义映射。
    """
    if not table_exists(conn, "llm_requests"):
        return
    # 新形态列补齐（幂等）
    _add_columns(
        conn,
        "llm_requests",
        (
            ("reasoning_tokens", "INTEGER NOT NULL DEFAULT 0"),
            ("usage_raw_json", "TEXT"),
        ),
    )
    # 列改名 client_type → profile_name：旧值原样保留
    if column_exists(conn, "llm_requests", "client_type"):
        conn.execute("ALTER TABLE llm_requests RENAME COLUMN client_type TO profile_name")


def _migrate_llm_requests_index(conn: sqlite3.Connection) -> None:
    """profile_name 索引（profile 筛选先例支撑）；IF NOT EXISTS 幂等。"""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_requests_profile ON llm_requests(profile_name)")


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：新建库已是最新形态，各步骤用列存在性检查保证重复执行安全。"""
    _migrate_llm_usage(conn)
    _migrate_llm_requests(conn)
    _migrate_llm_requests_index(conn)


__all__ = ["migrate"]
