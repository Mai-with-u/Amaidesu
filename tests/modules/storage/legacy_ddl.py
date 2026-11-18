"""迁移测试共享的旧时代表结构（legacy DDL）。

各时代的手抄 DDL 收拢在此：每个迁移测试文件原来各自内嵌一份旧版 CREATE TABLE，
抄写漂移不会让测试失败；集中到这里后同族测试引用同一份文本，测试与被迁移的
历史形态同源。 ``open_legacy_db`` 建一个带版本表的内存库并执行给定 DDL。

注意：迁移链 v1 是显式 no-op（初始表由最新 ``build_schema_sql()`` 补齐），版本链
上没有可重放的历史基线，无法实现"从版本起点重放建库"的 ``legacy_db_at(version)``；
本模块是退路——按时代收拢 DDL 文本，供测试按需引用。
"""

from __future__ import annotations

import sqlite3


def open_legacy_db(*ddl_scripts: str) -> sqlite3.Connection:
    """建内存库，建好版本表后执行给定旧时代 DDL，返回连接（调用方负责关闭）。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at_ms INTEGER NOT NULL)")
    for script in ddl_scripts:
        conn.executescript(script)
    conn.commit()
    return conn


V11_LLM_TABLES_SQL = """
CREATE TABLE llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER,
    model_name TEXT NOT NULL,
    assign_name TEXT,
    profile_name TEXT,
    provider_name TEXT NOT NULL,
    request_type TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    cache_hit_tokens INTEGER NOT NULL,
    cache_miss_tokens INTEGER NOT NULL,
    cost REAL NOT NULL,
    duration_ms INTEGER NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    request_id TEXT
);
CREATE TABLE llm_requests (
    request_id TEXT PRIMARY KEY,
    timestamp_ms INTEGER NOT NULL,
    client_type TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL DEFAULT '',
    request_params TEXT,
    response_content TEXT,
    reasoning_content TEXT,
    tool_calls TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    latency_ms INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at_ms INTEGER NOT NULL);
"""


V16_SCHEMA = """
CREATE TABLE rundowns (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    segments_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
CREATE TABLE rundown_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rundown_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    seg_id TEXT NOT NULL,
    title TEXT NOT NULL,
    task_description TEXT NOT NULL,
    expected_ms INTEGER NOT NULL,
    min_duration_ms INTEGER,
    notes TEXT,
    UNIQUE(rundown_id, seg_id)
);
CREATE TABLE llm_requests (
    request_id TEXT PRIMARY KEY,
    timestamp_ms INTEGER NOT NULL,
    profile_name TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL DEFAULT '',
    request_params TEXT,
    response_content TEXT,
    reasoning_content TEXT,
    tool_calls TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    usage_raw_json TEXT,
    breakdown_json TEXT,
    preview_text TEXT NOT NULL DEFAULT '',
    image_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE live_chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    content TEXT NOT NULL,
    message_type TEXT NOT NULL,
    tool_result TEXT,
    simulated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE timeline_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    summary TEXT NOT NULL,
    tags TEXT
);
"""


V17_SCHEMA = """
CREATE TABLE viewers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL,
    user_name TEXT NOT NULL, message_count INTEGER NOT NULL DEFAULT 0, gift_count INTEGER NOT NULL DEFAULT 0,
    replied_count INTEGER NOT NULL DEFAULT 0, interaction_count INTEGER NOT NULL DEFAULT 0,
    paid_count INTEGER NOT NULL DEFAULT 0, paid_amount INTEGER NOT NULL DEFAULT 0,
    last_active_ms INTEGER NOT NULL, UNIQUE(platform, user_id)
);
CREATE TABLE viewer_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, user_id TEXT NOT NULL,
    profile_text TEXT NOT NULL, last_compressed_at_ms INTEGER NOT NULL DEFAULT 0,
    updated_at_ms INTEGER NOT NULL, UNIQUE(platform, user_id)
);
CREATE TABLE viewer_names (
    id INTEGER PRIMARY KEY AUTOINCREMENT, viewer_id INTEGER NOT NULL, user_name TEXT NOT NULL,
    first_seen_ms INTEGER NOT NULL, last_seen_ms INTEGER NOT NULL, UNIQUE(viewer_id, user_name)
);
CREATE TABLE live_chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT, live_session_id INTEGER NOT NULL, timestamp_ms INTEGER NOT NULL,
    platform TEXT NOT NULL DEFAULT '', sender_role TEXT NOT NULL, sender_id TEXT, sender_name TEXT,
    simulated INTEGER NOT NULL DEFAULT 0, content TEXT NOT NULL, message_type TEXT NOT NULL
);
CREATE TABLE gifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, live_session_id INTEGER NOT NULL, timestamp_ms INTEGER NOT NULL,
    platform TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL, user_name TEXT NOT NULL,
    gift_name TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1, simulated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE super_chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT, live_session_id INTEGER NOT NULL, timestamp_ms INTEGER NOT NULL,
    platform TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL, user_name TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '', simulated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE guards (
    id INTEGER PRIMARY KEY AUTOINCREMENT, live_session_id INTEGER NOT NULL, timestamp_ms INTEGER NOT NULL,
    platform TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL, user_name TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 0
);
"""


V19_SCHEMA = """
CREATE TABLE live_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_ms INTEGER NOT NULL,
    ended_at_ms   INTEGER,
    updated_at_ms INTEGER NOT NULL
);
CREATE TABLE session_recaps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL UNIQUE,
    started_at_ms   INTEGER NOT NULL,
    ended_at_ms     INTEGER NOT NULL,
    recap_text      TEXT NOT NULL,
    created_at_ms   INTEGER NOT NULL
);
"""


V20_SCHEMA = """
CREATE TABLE live_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_ms INTEGER NOT NULL,
    ended_at_ms   INTEGER,
    updated_at_ms INTEGER NOT NULL
);
"""


V25_SCHEMA = """
CREATE TABLE game_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    game             TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    message          TEXT NOT NULL,
    scene            TEXT,
    timestamp_ms     INTEGER NOT NULL
);
CREATE TABLE commitments (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    what               TEXT NOT NULL,
    game               TEXT,
    created_at_ms      INTEGER NOT NULL,
    updated_at_ms      INTEGER NOT NULL
);
CREATE TABLE archive_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    text             TEXT NOT NULL,
    quote            TEXT NOT NULL,
    evidence_chat_id INTEGER NOT NULL
);
"""


V26_SCHEMA = """
CREATE TABLE archive_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    text             TEXT NOT NULL,
    quote            TEXT NOT NULL,
    evidence_chat_id INTEGER NOT NULL,
    game             TEXT,
    save_id          TEXT NOT NULL DEFAULT '',
    save_name        TEXT NOT NULL DEFAULT ''
);
"""


V27_SCHEMA = """
CREATE TABLE live_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_ms   INTEGER NOT NULL,
    ended_at_ms     INTEGER,
    kind            TEXT NOT NULL DEFAULT 'live',
    settled_at_ms   INTEGER,
    settle_attempts INTEGER NOT NULL DEFAULT 0,
    settle_report   TEXT
);
CREATE TABLE archive_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    text             TEXT NOT NULL,
    quote            TEXT NOT NULL,
    evidence_chat_id INTEGER NOT NULL,
    game             TEXT,
    save_id          TEXT NOT NULL DEFAULT '',
    save_name        TEXT NOT NULL DEFAULT '',
    from_session_id  INTEGER,
    lasting          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_archive_lines_from_session ON archive_lines(from_session_id);
"""


V28_SCHEMA = """
CREATE TABLE live_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_ms INTEGER NOT NULL
);
CREATE TABLE live_chat (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL,
    sender_role     TEXT NOT NULL,
    content         TEXT NOT NULL,
    dropped_at_ms   INTEGER
);
CREATE TABLE commitments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    what          TEXT NOT NULL,
    quote         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'open',
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
CREATE TABLE archive_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    text             TEXT NOT NULL,
    quote            TEXT NOT NULL,
    evidence_chat_id INTEGER NOT NULL,
    game             TEXT,
    save_id          TEXT NOT NULL DEFAULT '',
    save_name        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE game_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL,
    game            TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    message         TEXT NOT NULL,
    timestamp_ms    INTEGER NOT NULL
);
"""


V29_SCHEMA = """
CREATE TABLE live_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_ms INTEGER NOT NULL,
    settle_report TEXT
);
CREATE TABLE archive_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    text             TEXT NOT NULL,
    quote            TEXT NOT NULL,
    evidence_chat_id INTEGER NOT NULL
);
CREATE TABLE archive_snapshots (
    live_session_id INTEGER PRIMARY KEY REFERENCES live_sessions(id) ON DELETE CASCADE,
    written_at_ms   INTEGER NOT NULL
);
CREATE TABLE archive_snapshot_lines (
    live_session_id INTEGER NOT NULL REFERENCES archive_snapshots(live_session_id) ON DELETE CASCADE,
    archive_line_id INTEGER NOT NULL REFERENCES archive_lines(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    lasting         INTEGER NOT NULL DEFAULT 0,
    seen_at_ms      INTEGER NOT NULL,
    PRIMARY KEY (live_session_id, archive_line_id)
);
"""


V30_SCHEMA = """
CREATE TABLE live_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at_ms INTEGER NOT NULL);
CREATE TABLE live_chat (id INTEGER PRIMARY KEY AUTOINCREMENT, live_session_id INTEGER, content TEXT);
CREATE TABLE commitments (id INTEGER PRIMARY KEY AUTOINCREMENT, what TEXT NOT NULL);
CREATE TABLE archive_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    text             TEXT NOT NULL,
    quote            TEXT NOT NULL,
    evidence_chat_id INTEGER NOT NULL,
    human_locked     INTEGER NOT NULL DEFAULT 0
);
"""


V30_ARCHIVE_LINES = """
CREATE TABLE archive_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    text             TEXT NOT NULL,
    quote            TEXT NOT NULL,
    evidence_chat_id INTEGER NOT NULL,
    game             TEXT,
    save_id          TEXT NOT NULL DEFAULT '',
    save_name        TEXT NOT NULL DEFAULT '',
    keywords         TEXT NOT NULL DEFAULT '[]',
    place            TEXT NOT NULL DEFAULT '',
    human_locked     INTEGER NOT NULL DEFAULT 0
);
"""


V31_COMMITMENTS = """
CREATE TABLE commitments (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    what               TEXT NOT NULL,
    quote              TEXT NOT NULL DEFAULT '',
    evidence_chat_id   INTEGER UNIQUE,
    viewer_id          INTEGER,
    game               TEXT,
    save_id            TEXT NOT NULL DEFAULT '',
    save_name          TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'void')),
    created_session_id INTEGER,
    close_chat_id      INTEGER,
    close_quote        TEXT,
    created_at_ms      INTEGER NOT NULL,
    updated_at_ms      INTEGER NOT NULL,
    keywords           TEXT NOT NULL DEFAULT '[]'
);
"""


V31_LIVE_CHAT = """
CREATE TABLE live_chat (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    timestamp_ms     INTEGER NOT NULL,
    platform         TEXT NOT NULL DEFAULT '',
    sender_role      TEXT NOT NULL,
    sender_id        TEXT,
    sender_name      TEXT,
    viewer_id        INTEGER,
    id_kind          TEXT NOT NULL DEFAULT '',
    content          TEXT NOT NULL,
    message_type     TEXT NOT NULL,
    message_id       TEXT,
    reply_to_message_id TEXT,
    simulated        INTEGER NOT NULL DEFAULT 0,
    utterance_id     TEXT,
    dropped_at_ms    INTEGER,
    memory_refs      TEXT
);
"""


V32_MEMORY_EDITS = """
CREATE TABLE memory_edits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    target          TEXT NOT NULL,
    archive_line_id INTEGER,
    action          TEXT NOT NULL,
    before_text     TEXT NOT NULL DEFAULT '',
    after_text      TEXT NOT NULL DEFAULT '',
    at_ms           INTEGER NOT NULL
);
"""


V37_GAME_CHAT = """
CREATE TABLE game_chat (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    game            TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('player', 'streamer', 'other')),
    player_id       INTEGER REFERENCES game_players(id) ON DELETE CASCADE,
    content         TEXT NOT NULL DEFAULT '',
    save_id         TEXT NOT NULL DEFAULT '',
    save_name       TEXT NOT NULL DEFAULT '',
    at_ms           INTEGER NOT NULL
);
"""


V7_LLM_TABLES_SQL = """
CREATE TABLE llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER,
    model_name TEXT NOT NULL,
    assign_name TEXT,
    profile_name TEXT,
    provider_name TEXT NOT NULL,
    request_type TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL,
    duration_ms INTEGER NOT NULL,
    timestamp_ms INTEGER NOT NULL
);
CREATE TABLE llm_requests (
    request_id TEXT PRIMARY KEY,
    timestamp_ms INTEGER NOT NULL,
    client_type TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL DEFAULT '',
    request_params TEXT,
    response_content TEXT,
    reasoning_content TEXT,
    tool_calls TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    latency_ms INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at_ms INTEGER NOT NULL);
"""


V15_1_VIEWER_FACTS_VIEWER_NAMES = """CREATE TABLE viewer_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, user_id TEXT NOT NULL,
    fact_text TEXT NOT NULL, source_message_id TEXT NOT NULL DEFAULT '', created_at_ms INTEGER NOT NULL
);
CREATE TABLE viewer_names (
    id INTEGER PRIMARY KEY AUTOINCREMENT, viewer_id INTEGER NOT NULL, user_name TEXT NOT NULL,
    first_seen_ms INTEGER NOT NULL, last_seen_ms INTEGER NOT NULL, UNIQUE(viewer_id, user_name)
);
INSERT INTO viewer_facts(platform, user_id, fact_text, created_at_ms) VALUES ('bilibili', 'u1', '喜欢数码', 1);
INSERT INTO viewer_names(viewer_id, user_name, first_seen_ms, last_seen_ms) VALUES (1, '甲', 1, 2);
"""

V16_1_LLM_REQUESTS = """CREATE TABLE llm_requests (
    request_id TEXT PRIMARY KEY, timestamp_ms INTEGER NOT NULL,
    profile_name TEXT NOT NULL DEFAULT '', model_name TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0, preview_text TEXT NOT NULL DEFAULT ''
);
INSERT INTO llm_requests(request_id, timestamp_ms, model_name, prompt_tokens)
    VALUES ('req-old', 1, 'm1', 64);
"""

V23_1_LIVE_SESSIONS_LIVE_CHAT = """CREATE TABLE live_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id       TEXT NOT NULL DEFAULT '',
    platform        TEXT NOT NULL DEFAULT 'unknown',
    started_at_ms   INTEGER NOT NULL,
    ended_at_ms     INTEGER,
    title           TEXT,
    source          TEXT NOT NULL DEFAULT 'manual',
    kind            TEXT NOT NULL DEFAULT 'live' CHECK (kind IN ('live', 'rehearsal')),
    heat            INTEGER NOT NULL DEFAULT 0,
    viewer_count    INTEGER NOT NULL DEFAULT 0,
    audience_total  INTEGER NOT NULL DEFAULT 0,
    updated_at_ms   INTEGER NOT NULL,
    notebook_text   TEXT NOT NULL DEFAULT ''
);
CREATE TABLE live_chat (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    timestamp_ms     INTEGER NOT NULL,
    platform         TEXT NOT NULL DEFAULT '',
    sender_role      TEXT NOT NULL,
    sender_id        TEXT,
    sender_name      TEXT,
    viewer_id        INTEGER,
    id_kind          TEXT NOT NULL DEFAULT '',
    content          TEXT NOT NULL,
    message_type     TEXT NOT NULL,
    message_id       TEXT,
    reply_to_message_id TEXT,
    simulated        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE viewers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    platform            TEXT NOT NULL DEFAULT '',
    account_id          TEXT,
    open_id             TEXT,
    open_app_id         TEXT,
    user_name           TEXT NOT NULL,
    name_seen_ms        INTEGER NOT NULL DEFAULT 0,
    simulated           INTEGER NOT NULL DEFAULT 0,
    link_status         TEXT NOT NULL DEFAULT 'confirmed',
    link_candidate_id   INTEGER,
    message_count       INTEGER NOT NULL DEFAULT 0,
    gift_count          INTEGER NOT NULL DEFAULT 0,
    replied_count       INTEGER NOT NULL DEFAULT 0,
    interaction_count   INTEGER NOT NULL DEFAULT 0,
    paid_count          INTEGER NOT NULL DEFAULT 0,
    paid_amount         INTEGER NOT NULL DEFAULT 0,
    last_active_ms      INTEGER NOT NULL
);
"""

V24_1_LIVE_SESSIONS_SESSION_RECAPS = """CREATE TABLE live_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id       TEXT NOT NULL DEFAULT '',
    platform        TEXT NOT NULL DEFAULT 'unknown',
    started_at_ms   INTEGER NOT NULL,
    ended_at_ms     INTEGER,
    title           TEXT,
    source          TEXT NOT NULL DEFAULT 'manual',
    kind            TEXT NOT NULL DEFAULT 'live' CHECK (kind IN ('live', 'rehearsal')),
    heat            INTEGER NOT NULL DEFAULT 0,
    viewer_count    INTEGER NOT NULL DEFAULT 0,
    audience_total  INTEGER NOT NULL DEFAULT 0,
    updated_at_ms   INTEGER NOT NULL,
    notebook_text   TEXT NOT NULL DEFAULT ''
);
CREATE TABLE session_recaps (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL UNIQUE,
    started_at_ms  INTEGER NOT NULL,
    ended_at_ms    INTEGER NOT NULL,
    recap_text     TEXT NOT NULL,
    created_at_ms  INTEGER NOT NULL
);
INSERT INTO live_sessions (id, started_at_ms, ended_at_ms, updated_at_ms)
    VALUES (1, 1000, 2000, 2000), (2, 3000, 4000, 4000), (3, 5000, NULL, 5000);
"""

V25_1_SESSION_RECAPS_VIEWER_PROFILES = """CREATE TABLE session_recaps (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id        INTEGER NOT NULL UNIQUE,
    started_at_ms          INTEGER NOT NULL,
    ended_at_ms            INTEGER NOT NULL,
    recap_text             TEXT NOT NULL,
    created_at_ms          INTEGER NOT NULL,
    long_memory_merged_at_ms INTEGER,
    settled_at_ms          INTEGER
);
CREATE TABLE viewer_profiles (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_id             INTEGER NOT NULL UNIQUE,
    profile_text          TEXT NOT NULL,
    last_compressed_at_ms INTEGER NOT NULL DEFAULT 0,
    updated_at_ms         INTEGER NOT NULL
);
"""

V25_2_LIVE_SESSIONS_SESSION_RECAPS = """CREATE TABLE live_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    notebook_text TEXT NOT NULL DEFAULT ''
);
CREATE TABLE session_recaps (id INTEGER PRIMARY KEY, live_session_id INTEGER NOT NULL);
CREATE TABLE viewer_profiles (id INTEGER PRIMARY KEY, viewer_id INTEGER NOT NULL);
"""

V41_1_RUNDOWN_SEGMENTS_RUNDOWN_INSTANCE_SEGMENTS = """CREATE TABLE rundown_segments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    rundown_id       TEXT NOT NULL,
    seq              INTEGER NOT NULL,
    seg_id           TEXT NOT NULL,
    title            TEXT NOT NULL,
    task_description TEXT NOT NULL,
    expected_ms      INTEGER NOT NULL,
    min_duration_ms  INTEGER,
    notes            TEXT,
    UNIQUE(rundown_id, seg_id)
);
CREATE TABLE rundown_instance_segments (
    live_session_id  INTEGER NOT NULL,
    seq              INTEGER NOT NULL,
    seg_id           TEXT NOT NULL,
    title            TEXT NOT NULL,
    task_description TEXT NOT NULL,
    expected_ms      INTEGER NOT NULL,
    min_duration_ms  INTEGER,
    notes            TEXT,
    PRIMARY KEY(live_session_id, seq)
);
INSERT INTO rundown_segments (rundown_id, seq, seg_id, title, task_description, expected_ms)
VALUES ('legacy_rd', 0, 'opening', '开场', '打招呼', 180000);
INSERT INTO rundown_instance_segments (live_session_id, seq, seg_id, title, task_description, expected_ms)
VALUES (1, 0, 'opening', '开场', '打招呼', 180000);
"""


V39_COMMITMENTS = """CREATE TABLE commitments (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    what                  TEXT NOT NULL,
    quote                 TEXT NOT NULL DEFAULT '',
    evidence_chat_id      INTEGER UNIQUE,
    viewer_id             INTEGER,
    game                  TEXT,
    save_id               TEXT NOT NULL DEFAULT '',
    save_name             TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'void')),
    created_session_id    INTEGER,
    close_chat_id         INTEGER,
    close_quote           TEXT,
    created_at_ms         INTEGER NOT NULL,
    updated_at_ms         INTEGER NOT NULL,
    keywords              TEXT NOT NULL DEFAULT '[]',
    target_player_id      INTEGER REFERENCES game_players(id) ON DELETE CASCADE,
    evidence_game_chat_id INTEGER REFERENCES game_chat(id) ON DELETE SET NULL
)"""

V43_ARCHIVE_LINES = """CREATE TABLE archive_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    text             TEXT NOT NULL,
    quote            TEXT NOT NULL,
    evidence_chat_id INTEGER NOT NULL,
    game             TEXT,
    save_id          TEXT NOT NULL DEFAULT '',
    save_name        TEXT NOT NULL DEFAULT '',
    keywords         TEXT NOT NULL DEFAULT '[]',
    place            TEXT NOT NULL DEFAULT '',
    human_locked     INTEGER NOT NULL DEFAULT 0,
    source           TEXT NOT NULL DEFAULT 'streamer',
    viewer_chat_id   INTEGER REFERENCES live_chat(id) ON DELETE CASCADE,
    viewer_word      TEXT NOT NULL DEFAULT ''
)"""

V43_ARCHIVE_SNAPSHOT_LINES = """CREATE TABLE archive_snapshot_lines (
    live_session_id INTEGER NOT NULL REFERENCES archive_snapshots(live_session_id) ON DELETE CASCADE,
    archive_line_id INTEGER NOT NULL REFERENCES archive_lines(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    lasting         INTEGER NOT NULL DEFAULT 0,
    seen_at_ms      INTEGER NOT NULL,
    pinned          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (live_session_id, archive_line_id)
)"""

V21_LIVE_SESSIONS_MINIMAL = (
    """CREATE TABLE live_sessions (id INTEGER PRIMARY KEY, notebook_text TEXT NOT NULL DEFAULT '')"""
)

V32_MINIMAL_TABLES = """CREATE TABLE live_sessions (id INTEGER PRIMARY KEY);
CREATE TABLE viewers (id INTEGER PRIMARY KEY);"""


V25_LIVE_SESSIONS_MINIMAL = """CREATE TABLE live_sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 started_at_ms INTEGER NOT NULL,
 updated_at_ms INTEGER NOT NULL)"""
