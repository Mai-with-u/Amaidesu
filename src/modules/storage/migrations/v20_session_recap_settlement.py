"""v19 → v20：``session_recaps`` 加收播整理进度两列。

收播整理按 回顾 → 长期记忆 → 观众画像 顺序跑。此前只有回顾行本身能说明
"这场整理过"，启动补写只认没有回顾的场次：停止 Agent 时整理若在回顾写完之后
被取消，这一场的长期记忆合并与画像更新就再也补不上。新增两列：

- ``long_memory_merged_at_ms``：本场已合并进长期记忆的时刻（空 = 还没并）。
  合并不是幂等的（同一份回顾并两次会在长期笔记里重复），靠它保证每场只并一次；
- ``settled_at_ms``：三步都走完的时刻（空 = 整理没做完，下次启动补完）。

存量回顾行一律视为已整理、已合并，两列都填回顾的写入时刻：升级前写成的回顾
走的是完整的收播整理（或旧版启动补写），不该在升级后把历史场次重跑一遍。

回填只在本次补列时执行：列已存在（新库或已迁移过）时跳过，重复执行不会把
新写的、尚未整理完的回顾误标成已整理。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

# 整理进度列：补列时存量行一律回填为回顾写入时刻
_PROGRESS_COLUMNS = ("long_memory_merged_at_ms", "settled_at_ms")


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：补整理进度两列，存量回顾行视为已整理完。"""
    if not table_exists(conn, "session_recaps"):
        return
    for column in _PROGRESS_COLUMNS:
        if column_exists(conn, "session_recaps", column):
            continue
        conn.execute(f"ALTER TABLE session_recaps ADD COLUMN {column} INTEGER")  # noqa: S608 列名为代码内常量
        conn.execute(f"UPDATE session_recaps SET {column}=created_at_ms")  # noqa: S608 列名为代码内常量


__all__ = ["migrate"]
