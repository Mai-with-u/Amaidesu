"""v33 → v34：观众候选记下观众定下的那个名字或梗词。

整理时模型另写一个 ``word``（观众定下的名字、梗词），代码核对过它在标题和那条弹幕原文里都原样出现，
原先核对完就丢了。批准后的档案行渲染时要把她那句回应里点到的那位观众的名字换成"那位观众"，
观众的名字和起的名字撞上（观众叫"团子"，定下"给小猫取名团子"）时，会把起的名字也换掉，
所以这一版给 ``viewer_candidates`` 加一列 ``word``，批准时抄到生成的档案行上（``archive_lines`` 加一列
``viewer_word``）：渲染时原话里和它相同的那一段不换。档案行自己带着这个词，读档案的仓储不用去查候选表
（候选批准前对她不可见，读记忆的仓储一律不碰候选表）。早先的候选与档案行这两列为空，原话照旧全遮。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：观众候选与档案行各补"观众定下的名字或梗词"一列，旧行为空串。"""
    if table_exists(conn, "viewer_candidates") and not column_exists(conn, "viewer_candidates", "word"):
        conn.execute("ALTER TABLE viewer_candidates ADD COLUMN word TEXT NOT NULL DEFAULT ''")
    if table_exists(conn, "archive_lines") and not column_exists(conn, "archive_lines", "viewer_word"):
        conn.execute("ALTER TABLE archive_lines ADD COLUMN viewer_word TEXT NOT NULL DEFAULT ''")
