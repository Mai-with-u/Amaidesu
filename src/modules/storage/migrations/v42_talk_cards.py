"""v41 → v42：建话题卡表 talk_cards 与卡种索引。

话题卡是主播杂谈冷场时的自备素材（提问箱 / 往场趣事 / 二选一态度），运营在编排台
维护，主播在没人发弹幕时抽来开话头。新表无旧行；建表语句在这里留一份定稿副本，
建表汇总以后改动不影响这一版迁移做过的事。
"""

from __future__ import annotations

import sqlite3

_TALK_CARDS_SQL = (
    """
CREATE TABLE IF NOT EXISTS talk_cards (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    kind               TEXT NOT NULL CHECK (kind IN ('marshmallow', 'episode', 'opinion')),
    title              TEXT NOT NULL,
    body               TEXT NOT NULL DEFAULT '',
    asker              TEXT NOT NULL DEFAULT '',
    tags_json          TEXT NOT NULL DEFAULT '[]',
    enabled            INTEGER NOT NULL DEFAULT 1,
    used_in_session_id INTEGER,
    used_at_ms         INTEGER,
    created_at_ms      INTEGER NOT NULL,
    updated_at_ms      INTEGER NOT NULL
)
""",
    "CREATE INDEX IF NOT EXISTS idx_talk_cards_kind ON talk_cards(kind, enabled)",
)


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：建话题卡表与卡种索引。"""
    for statement in _TALK_CARDS_SQL:
        conn.execute(statement)
