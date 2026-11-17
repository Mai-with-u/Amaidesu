"""v46 → v47：观众候选补 ``viewer_id`` 外键列与熟客近况查询索引。

``viewer_candidates`` 新增 ``viewer_id``（引用 ``viewers(id) ON DELETE CASCADE``）：
- 下播整理列出的熟客近况候选（``kind = 'viewer_self'``）批准后原地留在 ``viewer_candidates``，
  场内按 ``(viewer_id, kind, status)`` 查询已批准近况生成熟客卡，不写进全局档案与承诺；
- 存量候选若挂着观众弹幕（``viewer_chat_id``），顺带从 ``live_chat.viewer_id`` 回填；
- 观众行被删除或执行"忘了我"时，挂在该观众上的候选随外键级联一并清除。

索引只建在本迁移脚本中：旧库升级时先跑建表汇总再跑迁移，建表汇总执行那一刻旧表还没有 ``viewer_id`` 列；
新建库同样顺序执行整条迁移链，索引照常建齐。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_viewer_candidates_viewer ON viewer_candidates(viewer_id, kind, status)"


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：补 ``viewer_candidates.viewer_id`` 列 → 回填存量行 → 建复合索引。"""
    if not table_exists(conn, "viewer_candidates"):
        return
    if not column_exists(conn, "viewer_candidates", "viewer_id"):
        conn.execute(
            "ALTER TABLE viewer_candidates ADD COLUMN viewer_id INTEGER REFERENCES viewers(id) ON DELETE CASCADE"
        )
    if table_exists(conn, "live_chat") and table_exists(conn, "viewers"):
        conn.execute(
            "UPDATE viewer_candidates"
            " SET viewer_id = ("
            "SELECT v.id FROM live_chat c JOIN viewers v ON v.id = c.viewer_id"
            " WHERE c.id = viewer_candidates.viewer_chat_id"
            ")"
            " WHERE viewer_id IS NULL AND viewer_chat_id IS NOT NULL"
        )
    conn.execute(_INDEX_SQL)
