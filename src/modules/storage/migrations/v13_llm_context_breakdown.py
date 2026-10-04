"""v12 → v13：LLM 请求历史补上下文分段占用列。

``llm_requests`` + ``breakdown_json``（TEXT 可空）：一次成功调用的上下文
分段占用解剖（system / messages / tools 三段 token 估算与明细），监控面板
"上下文水位"弹出明细的数据源。写入方为 ``LLMManager._post_success``（估算
失败或上游未回报 usage 时留 NULL，不阻断调用链）；历史行不回填。

新建库 DDL 已含该列，回调以列存在性检查保证对旧库升级与重复执行安全。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：``llm_requests`` 补 ``breakdown_json`` 列。"""
    if not table_exists(conn, "llm_requests"):
        return
    if not column_exists(conn, "llm_requests", "breakdown_json"):
        conn.execute("ALTER TABLE llm_requests ADD COLUMN breakdown_json TEXT")


__all__ = ["migrate"]
