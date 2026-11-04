CREATE TABLE IF NOT EXISTS live_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id       TEXT NOT NULL,
    platform        TEXT NOT NULL,
    started_at_ms   INTEGER NOT NULL,
    ended_at_ms     INTEGER,
    title           TEXT,
    heat            INTEGER NOT NULL DEFAULT 0,
    viewer_count    INTEGER NOT NULL DEFAULT 0,
    audience_total  INTEGER NOT NULL DEFAULT 0,
    updated_at_ms   INTEGER NOT NULL,
    CHECK (ended_at_ms IS NULL OR ended_at_ms >= started_at_ms)
);
CREATE TABLE IF NOT EXISTS live_chat (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    timestamp_ms     INTEGER NOT NULL,
    sender_role      TEXT NOT NULL,
    sender_id        TEXT,
    sender_name      TEXT,
    content          TEXT NOT NULL,
    message_type     TEXT NOT NULL,
    tool_result      TEXT,
    simulated        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS gifts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    timestamp_ms     INTEGER NOT NULL,
    user_id          TEXT NOT NULL,
    user_name        TEXT NOT NULL,
    gift_name        TEXT NOT NULL,
    gift_count       INTEGER NOT NULL,
    simulated        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS super_chats (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    timestamp_ms     INTEGER NOT NULL,
    user_id          TEXT NOT NULL,
    user_name        TEXT NOT NULL,
    amount           REAL NOT NULL,
    message          TEXT NOT NULL,
    simulated        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS topics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    label            TEXT NOT NULL,
    source           TEXT NOT NULL,
    score            REAL NOT NULL,
    trend            REAL NOT NULL,
    duration_ms      INTEGER NOT NULL,
    count            INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS viewers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT NOT NULL UNIQUE,
    user_name           TEXT NOT NULL,
    message_count       INTEGER NOT NULL DEFAULT 0,
    gift_count          INTEGER NOT NULL DEFAULT 0,
    replied_count       INTEGER NOT NULL DEFAULT 0,
    interaction_count   INTEGER NOT NULL DEFAULT 0,
    last_active_ms      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS agenda_plan (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    label            TEXT NOT NULL,
    "order"          INTEGER NOT NULL,
    starts_at_ms     INTEGER NOT NULL,
    expected_ms      INTEGER NOT NULL,
    note             TEXT,
    created_by       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agenda_runtime (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    plan_id          INTEGER,
    label            TEXT NOT NULL,
    "order"          INTEGER NOT NULL,
    starts_at_ms     INTEGER NOT NULL,
    expected_ms      INTEGER NOT NULL,
    done             INTEGER NOT NULL DEFAULT 0,
    current          INTEGER NOT NULL DEFAULT 0,
    note             TEXT,
    inserted_by      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS game_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    game             TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    message          TEXT NOT NULL,
    scene            TEXT,
    timestamp_ms     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS timeline_summary (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    start_ms         INTEGER NOT NULL,
    end_ms           INTEGER NOT NULL,
    summary          TEXT NOT NULL,
    tags             TEXT
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id    INTEGER,
    model_name         TEXT NOT NULL,
    assign_name        TEXT,
    profile_name       TEXT,
    provider_name      TEXT NOT NULL,
    request_type       TEXT NOT NULL,
    prompt_tokens      INTEGER NOT NULL,
    completion_tokens  INTEGER NOT NULL,
    total_tokens       INTEGER NOT NULL,
    cache_hit_tokens   INTEGER NOT NULL,
    cache_miss_tokens  INTEGER NOT NULL,
    cost               REAL NOT NULL,
    duration_ms        INTEGER NOT NULL,
    timestamp_ms       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_migrations (
    version        INTEGER PRIMARY KEY,
    applied_at_ms  INTEGER NOT NULL
);