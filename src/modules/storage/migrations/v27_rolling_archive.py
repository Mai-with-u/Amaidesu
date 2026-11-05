"""v26 → v27：场次档案改为滚动累积，档案行记下"出自哪一场"与"是不是长期有效的事"。

常驻简报只读最近一份档案。档案原来一场一份、只写本场的事：碰上一场只播了十来分钟、中途
崩了几次的短场，前面几场的名字、关系、住处全从简报里掉出去。改成滚动以后，每次整理把
上一份档案里仍然成立的行接进本场档案，于是同一件事会出现在好几场的档案里，需要两列：

- ``from_session_id``：这一行说的事出自哪一场（接过来的行指向最初那一场；简报据此写
  "上一场 / 之前"，删场次、改彩排、删观众时据此连同接过去的副本一起处理）；
- ``lasting``：名字、关系、住处这类长期有效的事（滚动时先占位，不被新事挤掉）。

存量行不回填：``from_session_id`` 为空即"滚动之前写的旧档案，出自本行所在的那一场"，
``lasting`` 为 0。整理据"最近一份档案全是旧行"认出还没开始滚动，第一次滚动时连同更早几份
旧档案一起接。列已存在（新库或已迁移过）时跳过，没有这张表时什么都不做。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

# 要补的列（列名, 列定义）
_COLUMNS = (
    ("from_session_id", "INTEGER"),
    ("lasting", "INTEGER NOT NULL DEFAULT 0"),
)


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：给 archive_lines 补两列，存量行留空（出自本行所在场次、不算长期）。"""
    if not table_exists(conn, "archive_lines"):
        return
    for name, definition in _COLUMNS:
        if not column_exists(conn, "archive_lines", name):
            conn.execute(f"ALTER TABLE archive_lines ADD COLUMN {name} {definition}")  # noqa: S608 列名为代码内常量
    conn.execute("CREATE INDEX IF NOT EXISTS idx_archive_lines_from_session ON archive_lines(from_session_id)")


__all__ = ["migrate"]
