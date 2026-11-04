"""v24 → v25：记忆改按账本记录之后，删掉旧的记忆存储。

这些都是账本上线后就不再被写入的旧结构（场次回顾、观众画像、本场备忘录），也不再进
任何提示词。本版是不可逆清理：

- ``session_recaps``：每场一份收播总结，已被场次档案与承诺账本取代；
- ``viewer_profiles``：观众印象文本，不再生成也不再注入；
- ``live_sessions.notebook_text``：本场备忘录全文，备忘录工具已下线。

旧长期记忆还把 ``MEMORY.md`` 与每次改写的历史留底写在库文件旁的 ``memory/`` 目录，
里面含观众昵称，表删掉后没有任何代码再读它，一并删除（用户已同意存量清空）。

删表在前、删列在后；重复执行安全（存在性检查）。v18 迁移里重建 ``viewer_profiles``
用的建表语句已冻结在该文件内，不依赖 schema.py。
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from src.modules.logging import get_logger
from src.modules.storage.migrations._common import column_exists, table_exists

logger = get_logger("storage.migrations.v25")

# 删掉的旧记忆表
_DROPPED_TABLES = ("session_recaps", "viewer_profiles")


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：删旧记忆的两张表与场次表上的备忘录列。"""
    for table in _DROPPED_TABLES:
        if table_exists(conn, table):
            conn.execute(f"DROP TABLE {table}")  # noqa: S608 表名为代码内常量
    if table_exists(conn, "live_sessions") and column_exists(conn, "live_sessions", "notebook_text"):
        conn.execute("ALTER TABLE live_sessions DROP COLUMN notebook_text")
    _drop_legacy_memory_dir(conn)


def _drop_legacy_memory_dir(conn: sqlite3.Connection) -> None:
    """删除库文件旁的旧记忆目录（``MEMORY.md`` 与历史留底快照）。"""
    db_file = ""
    for row in conn.execute("PRAGMA database_list").fetchall():
        if len(row) >= 3 and row[1] == "main" and row[2]:
            db_file = str(row[2])
            break
    if not db_file or db_file == ":memory:":
        return
    memory_dir = Path(db_file).parent / "memory"
    if not memory_dir.is_dir():
        return
    try:
        shutil.rmtree(memory_dir)
    except OSError as exc:  # 删不掉不挡升级，但要留可查线索让人工清理
        logger.warning(f"旧记忆目录删除失败，请手工清理: {memory_dir} ({exc})", exc=exc)


__all__ = ["migrate"]
