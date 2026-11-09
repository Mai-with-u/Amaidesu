"""v37 → v38：游戏社交事实存储——建 game_social_facts 表与两个索引。

这一版只建存储，不改任何读写行为：

- ``game_social_facts``：一行 = 一条"当前游戏环境里人与人怎么相处"的可核对事实。
  六类（环境形态 / 成员关系 / 共享惯例 / 进行中的事 / 她的角色 / 待弄清），带依据原文
  与证据行引用（弹幕 → live_chat、游戏公屏 → game_chat）；事实过时用取代表达
  （status 置 superseded 并回填 superseded_by），旧行留档备查。

- ``game_chat.message_id``：公屏消息号（采集时发的随机短串），主播视野里公屏行末尾的 ``[id:…]``
  就是它；登记社交事实时模型填消息号，工具按"本场 + 消息号"查回行号。旧行补空串（没有号，查不到）。
  索引 ``(live_session_id, message_id)`` 只建在这里：建表汇总执行那一刻旧库还没有这一列。

- 回想索引第五类来源：``memory_vectors_social_fact``（向量，外键级联）与 ``memory_fts_social_fact``
  （全文虚表，删行、改正文由两个触发器清掉对应索引行），只收现行行。

社交事实表旧行无（新表）。建表语句在这里留一份定稿副本：建表汇总以后改动，不影响这一版迁移做过的事。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

_GAME_SOCIAL_FACTS_SQL = (
    """
CREATE TABLE IF NOT EXISTS game_social_facts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    game                  TEXT NOT NULL,
    save_id               TEXT NOT NULL DEFAULT '',
    save_name             TEXT NOT NULL DEFAULT '',
    kind                  TEXT NOT NULL,
    text                  TEXT NOT NULL,
    quote                 TEXT NOT NULL DEFAULT '',
    evidence_chat_id      INTEGER REFERENCES live_chat(id) ON DELETE SET NULL,
    evidence_game_chat_id INTEGER REFERENCES game_chat(id) ON DELETE SET NULL,
    status                TEXT NOT NULL DEFAULT 'active',
    superseded_by         INTEGER REFERENCES game_social_facts(id) ON DELETE SET NULL,
    human_locked          INTEGER NOT NULL DEFAULT 0,
    created_session_id    INTEGER,
    updated_session_id    INTEGER,
    created_at_ms         INTEGER NOT NULL,
    updated_at_ms         INTEGER NOT NULL
)
""",
    "CREATE INDEX IF NOT EXISTS idx_game_social_facts_scope ON game_social_facts(game, save_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_game_social_facts_status ON game_social_facts(status)",
    """
CREATE TABLE IF NOT EXISTS memory_vectors_social_fact (
    social_fact_id  INTEGER PRIMARY KEY REFERENCES game_social_facts(id) ON DELETE CASCADE,
    model           TEXT NOT NULL,
    dim             INTEGER NOT NULL,
    text_hash       TEXT NOT NULL,
    vector          BLOB NOT NULL
)
""",
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts_social_fact USING fts5(tokens)",
    """
CREATE TRIGGER IF NOT EXISTS trg_memory_index_social_fact_deleted AFTER DELETE ON game_social_facts BEGIN
    DELETE FROM memory_fts_social_fact WHERE rowid = OLD.id;
END
""",
    """
CREATE TRIGGER IF NOT EXISTS trg_memory_index_social_fact_edited AFTER UPDATE OF text, quote ON game_social_facts BEGIN
    DELETE FROM memory_fts_social_fact WHERE rowid = OLD.id;
    DELETE FROM memory_vectors_social_fact WHERE social_fact_id = OLD.id;
END
""",
)


_GAME_CHAT_MESSAGE_INDEX = "CREATE INDEX IF NOT EXISTS idx_game_chat_message ON game_chat(live_session_id, message_id)"


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：公屏补消息号列与索引，建游戏社交事实表与两个索引（缺被引用的表就跳过）。"""
    if table_exists(conn, "game_chat"):
        if not column_exists(conn, "game_chat", "message_id"):
            conn.execute("ALTER TABLE game_chat ADD COLUMN message_id TEXT NOT NULL DEFAULT ''")
        conn.execute(_GAME_CHAT_MESSAGE_INDEX)
    if not table_exists(conn, "live_chat"):
        return
    for statement in _GAME_SOCIAL_FACTS_SQL:
        conn.execute(statement)
