"""v3 → v4：场次主键语义修正（一房多场）+ 回复关联列。

- ``live_sessions`` 增加 ``source`` 列：旧库经 ADD COLUMN 补列时默认
  ``'legacy'``——所有存量行都是"房间号哈希映射"时代的遗留数据，天然标记；
  新建库的 DDL 默认 ``'manual'``（回调检测到列已存在则跳过）。
- 存量遗留行收口：``ended_at_ms`` 为空的补为 ``updated_at_ms``（历史行
  没有可重建的结束边界，以其最后活动时刻封闭，避免永远显示"进行中"）。
- ``live_chat`` 增加 ``message_id`` / ``reply_to_message_id`` 列与消息 ID 索引。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：列存在性检查保证对新建库与已迁移库安全。"""
    if not column_exists(conn, "live_sessions", "source"):
        conn.execute("ALTER TABLE live_sessions ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy'")
    # 遗留行封闭（幂等：COALESCE 保留已有结束时间）
    conn.execute("UPDATE live_sessions SET ended_at_ms = updated_at_ms WHERE source = 'legacy' AND ended_at_ms IS NULL")
    if not column_exists(conn, "live_chat", "message_id"):
        conn.execute("ALTER TABLE live_chat ADD COLUMN message_id TEXT")
    if not column_exists(conn, "live_chat", "reply_to_message_id"):
        conn.execute("ALTER TABLE live_chat ADD COLUMN reply_to_message_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_live_chat_message_id ON live_chat(message_id)")


__all__ = ["migrate"]
