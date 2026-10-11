"""v13 → v14：``llm_requests`` 补列表预览摘要列。

``llm_requests`` + ``preview_text``（TEXT NOT NULL DEFAULT ''）：列表页
Prompt 预览摘要，写侧从请求正文最后一条有正文的消息截取落库。同批变更
中正文大字段（request_params / reasoning_content / tool_calls /
usage_raw_json / breakdown_json）出库到 ``logs/llm_requests/`` 诊断文件，
新行这些列一律 NULL，``preview_text`` 成为列表预览的唯一来源；存量旧行
正文仍在库列中（读侧降级回退），不回填摘要。

新建库 DDL 已含该列，回调以列存在性检查保证对旧库升级与重复执行安全。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：``llm_requests`` 补 ``preview_text`` 列。"""
    if not table_exists(conn, "llm_requests"):
        return
    if not column_exists(conn, "llm_requests", "preview_text"):
        conn.execute("ALTER TABLE llm_requests ADD COLUMN preview_text TEXT NOT NULL DEFAULT ''")


__all__ = ["migrate"]
