"""模拟观众读取完整直播上下文，移除已失去作用的人设窗口列。"""

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    """保留观众人设与互动统计，新库和重复迁移均可直接通过。"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sim_personas)")}
    if "context_window_size" in columns:
        # 只移除输入裁剪配置，历史人物资料继续由同一行承载。
        conn.execute("ALTER TABLE sim_personas DROP COLUMN context_window_size")
