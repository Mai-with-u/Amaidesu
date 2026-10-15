"""v15 → v16：``llm_requests`` 补注入截图计数列。

``llm_requests`` + ``image_count``（INTEGER NOT NULL DEFAULT 0）：本次请求
messages 里图像片段（``{type:"image"}`` part）的数量，写侧从请求正文统计
落库。正文大字段自 v14 起出库到 ``logs/llm_requests/`` 诊断文件，列表行
无消息体可查，读侧无法事后计数——计数必须在写侧随行落库。WebUI 历史列
表以该列渲染"本次注入 N 张截图"徽标。

存量旧行按 0 处理（正文已出库，无法回填，徽标不显示）。新建库 DDL 已含
该列，回调以列存在性检查保证对旧库升级与重复执行安全。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：``llm_requests`` 补 ``image_count`` 列。"""
    if not table_exists(conn, "llm_requests"):
        return
    if not column_exists(conn, "llm_requests", "image_count"):
        conn.execute("ALTER TABLE llm_requests ADD COLUMN image_count INTEGER NOT NULL DEFAULT 0")


__all__ = ["migrate"]
