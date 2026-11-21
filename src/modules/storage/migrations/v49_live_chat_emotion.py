"""v48 → v49：``live_chat`` 新增主播台词的情绪两列。

背景：情绪标签（``Emotion`` 枚举值）与强度随 ``streamer.speech`` 事件已在
流动（虚拟形象侧订阅消费），但落库时被丢弃——调试页整场回放要按句复现
当时的情绪合成，需要每句台词的情绪落库。新增两列，仅主播发言行（
``sender_role='assistant'``）写入，观众行保持 NULL。

幂等保证：执行前先用 ``table_exists`` 与 ``column_exists`` 检查，表缺失
跳过、列已存在时不重复执行 ``ALTER TABLE``，重复迁移与新建库均安全。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：为 ``live_chat`` 补 ``emotion`` / ``emotion_intensity`` 两列。"""
    if not table_exists(conn, "live_chat"):
        return
    if not column_exists(conn, "live_chat", "emotion"):
        conn.execute("ALTER TABLE live_chat ADD COLUMN emotion TEXT")
    if not column_exists(conn, "live_chat", "emotion_intensity"):
        conn.execute("ALTER TABLE live_chat ADD COLUMN emotion_intensity REAL")
