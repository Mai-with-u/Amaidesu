"""v4 → v5：事件历史与 LLM 请求历史入库（无数据变换）。

新增 event_history（语义域事件流，录制回放 + dashboard 事件历史持久层）与
llm_requests（LLM 请求完整历史，dashboard 历史页数据源）两张表及查询
热路径索引。由 ``build_schema_sql()`` 的 DDL 自动补齐，无存量数据变换，
本条目为显式 no-op。
"""

from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    """无数据变换；新表由 ``build_schema_sql()`` 的 DDL 自动补齐。"""
