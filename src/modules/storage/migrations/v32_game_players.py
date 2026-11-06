"""v31 → v32：游戏里的人——按游戏给的 UUID 认玩家。

她在游戏里遇到的别的玩家，按游戏给每个账号的 UUID 认：同一个 UUID 改了名仍是同一个人，同名不同
UUID 是两个人。只自动记和她发生过的互动（打招呼、送东西、切磋、一起做的事、帮过忙），玩家说他自己
的事要人批准。这一版只建存储：

- ``game_players``：一位玩家一行，按"游戏 + UUID"唯一；记最近一次的名字、见过的名字、见过的存档、
  首次与最近一次见到的场次和时刻。
- ``game_player_interactions``：和她的一次互动（打招呼按场合并计数），挂玩家与场次，两者被删时级联。
- ``game_chat``：公屏游戏聊天落库（私聊不落）。玩家说的挂玩家；她说的不挂玩家；认不出是谁的
  （没有 UUID、不在线的借名消息）只占一行位置、不存原话，用来判断"她这句之前还有没有别人插话"。
  挂场次与玩家，级联删除；和弹幕一样受保留期管。
- ``player_candidates``：玩家自述的待审候选，挂在他那句公屏聊天和她的回应上（公屏那句或播出的
  台词），三处被删时级联。
- ``viewer_player_links``：运营者确认的"这位玩家是直播间的哪位观众"，只用来连带删除，从不告诉她。
- ``commitments.target_player_id``：对某位游戏玩家的承诺，玩家被删时承诺一起删。
- ``commitments.evidence_game_chat_id``：对玩家的承诺证据是她在游戏公屏打出去的那句时，指向那一行；
  一句公屏只登记一条（唯一索引），重新整理时按它对齐编号。
- ``live_chat.reply_player_id``：她这句台词在回应哪位玩家（这一窗只有一位玩家开过口时才记），
  玩家被删时置空。

既有表新加的三列由这里补，它们的索引也只建在这里：启动时先执行建表汇总、后跑迁移，建表汇总执行
那一刻旧库里还没有这三列。建表语句在这里留一份定稿副本：建表汇总以后改动，不影响这一版迁移做过的事。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

# (表, 列, 列定义)：带外键的列只能以空值为默认补进来（SQLite 的限制），旧行正好都不挂玩家
_NEW_COLUMNS = (
    ("commitments", "target_player_id", "INTEGER REFERENCES game_players(id) ON DELETE CASCADE"),
    ("commitments", "evidence_game_chat_id", "INTEGER REFERENCES game_chat(id) ON DELETE SET NULL"),
    ("live_chat", "reply_player_id", "INTEGER REFERENCES game_players(id) ON DELETE SET NULL"),
)

_COLUMN_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_commitments_target_player ON commitments(target_player_id)",
    # 一句公屏只登记一条承诺：整理落库按它做"同一句原地更新"的冲突键
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_commitments_evidence_game_chat ON commitments(evidence_game_chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_live_chat_reply_player ON live_chat(reply_player_id)",
)

_TABLES_SQL = (
    """
CREATE TABLE IF NOT EXISTS game_players (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    game             TEXT NOT NULL,
    uuid             TEXT NOT NULL,
    name             TEXT NOT NULL,
    names            TEXT NOT NULL DEFAULT '[]',
    saves            TEXT NOT NULL DEFAULT '[]',
    first_session_id INTEGER REFERENCES live_sessions(id) ON DELETE SET NULL,
    first_seen_ms    INTEGER NOT NULL,
    last_session_id  INTEGER REFERENCES live_sessions(id) ON DELETE SET NULL,
    last_seen_ms     INTEGER NOT NULL,
    UNIQUE(game, uuid)
)
""",
    """
CREATE TABLE IF NOT EXISTS game_player_interactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       INTEGER NOT NULL REFERENCES game_players(id) ON DELETE CASCADE,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    save_id         TEXT NOT NULL DEFAULT '',
    save_name       TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL,
    fact            TEXT NOT NULL DEFAULT '{}',
    at_ms           INTEGER NOT NULL
)
""",
    "CREATE INDEX IF NOT EXISTS idx_game_player_interactions_player ON game_player_interactions(player_id, at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_game_player_interactions_session"
    " ON game_player_interactions(live_session_id, kind)",
    """
CREATE TABLE IF NOT EXISTS game_chat (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    game            TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('player', 'streamer', 'other')),
    player_id       INTEGER REFERENCES game_players(id) ON DELETE CASCADE,
    content         TEXT NOT NULL DEFAULT '',
    save_id         TEXT NOT NULL DEFAULT '',
    save_name       TEXT NOT NULL DEFAULT '',
    at_ms           INTEGER NOT NULL
)
""",
    "CREATE INDEX IF NOT EXISTS idx_game_chat_session ON game_chat(live_session_id, at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_game_chat_player ON game_chat(player_id)",
    """
CREATE TABLE IF NOT EXISTS player_candidates (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id    INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    player_id          INTEGER NOT NULL REFERENCES game_players(id) ON DELETE CASCADE,
    title              TEXT NOT NULL,
    player_chat_id     INTEGER NOT NULL REFERENCES game_chat(id) ON DELETE CASCADE,
    player_quote       TEXT NOT NULL,
    reply_game_chat_id INTEGER REFERENCES game_chat(id) ON DELETE CASCADE,
    reply_live_chat_id INTEGER REFERENCES live_chat(id) ON DELETE CASCADE,
    reply_quote        TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending',
    created_at_ms      INTEGER NOT NULL,
    decided_at_ms      INTEGER
)
""",
    "CREATE INDEX IF NOT EXISTS idx_player_candidates_player ON player_candidates(player_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_player_candidates_session ON player_candidates(live_session_id)",
    """
CREATE TABLE IF NOT EXISTS viewer_player_links (
    viewer_id       INTEGER NOT NULL REFERENCES viewers(id) ON DELETE CASCADE,
    player_id       INTEGER NOT NULL REFERENCES game_players(id) ON DELETE CASCADE,
    confirmed_at_ms INTEGER NOT NULL,
    PRIMARY KEY (viewer_id, player_id)
)
""",
    "CREATE INDEX IF NOT EXISTS idx_viewer_player_links_player ON viewer_player_links(player_id)",
)


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：建玩家相关五张表 → 承诺补玩家与公屏证据两列、台词补一列玩家引用，连同索引。"""
    for statement in _TABLES_SQL:
        conn.execute(statement)
    for table, column, definition in _NEW_COLUMNS:
        if table_exists(conn, table) and not column_exists(conn, table, column):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")  # noqa: S608 常量表名列定义
    for statement in _COLUMN_INDEXES:
        table = statement.split(" ON ")[1].split("(")[0]
        if table_exists(conn, table):
            conn.execute(statement)
