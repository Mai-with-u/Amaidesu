"""
存储 Schema 定义

本模块是全部 SQLite 表的**单一事实源**：13 张业务表（11 张核心直播表 +
2 张模拟器运行时表）+ 模块私有表（当前为 SimpleMemory 的 ``_memory_facts``
/ ``_memory_profiles``）+ ``schema_migrations``。
任何建表 DDL 都必须落在这里，不允许业务模块自带 ``CREATE TABLE``——否则
表结构游离于 ``SCHEMA_VERSION`` 版本管理之外，迁移机制无法覆盖。

## 命名硬规则
- 时间字段一律 ``*_ms``（毫秒 int）
- 场次叫 ``live_sessions``，消息流叫 ``live_chat``（live chat 行业标准）
- ``live_sessions`` 一行 = 一场直播（有开始/结束边界）；房间/频道是场次之上的
  静态属性（``stream_id`` 普通属性列，**不参与主键语义**，一房多场）
- ``live_sessions.source`` 标记场次来源（manual=手动 / replay=模拟器回放 /
  legacy=历史遗留行）
- ``live_chat.message_id`` 与 ``live_chat.reply_to_message_id`` 构成"主播发言
  回复了哪条观众弹幕"的关联键（互动分析数据面）
- ``live_chat`` / ``gifts`` / ``super_chats`` 表加 ``simulated`` 贯穿列
  （模拟数据用 False 默认 / True 标记，消费方 WHERE ``simulated=0`` 排除模拟数据）
- 模块私有表以 ``_`` 前缀命名，表达"非业务数据平面、仅所属模块读写"

## Schema 迁移机制
- ``schema_migrations(version PK, applied_at_ms)``
- ``SCHEMA_VERSION`` 常量 = 当前权威版本
- ``SCHEMA_MIGRATIONS``：version → 迁移回调（原地修改、幂等）。``SQLiteStore``
  在推进版本时按序执行；回调内部用列存在性检查保证对新建库与已迁移库安全
- ``build_schema_sql()`` 返回完整建表 DDL（IF NOT EXISTS 幂等，含最新列）
- ``list_expected_tables()`` 返回启动自检必须存在的业务表名（不含私有表：
  私有表随所属模块后端启用与否而变化，不纳入"缺一即拒启"的闸门）
- ``list_private_tables()`` 返回模块私有表名（所属模块自检用）
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, Dict, List

# 当前 Schema 版本——改动表结构时必须同步升级
SCHEMA_VERSION: int = 5


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    """单次迁移记录"""

    version: int
    applied_at_ms: int


# =============================================================================
# 15 张业务表 + 模块私有表 + schema_migrations
# =============================================================================
# - live_sessions           场次 + 直播实时状态（一场一行）
# - live_chat               全量直播消息流（行业 live chat）
# - gifts                   礼物明细（独立副表）
# - super_chats             SC 明细（独立副表）
# - topics                  话题（每行一个，1NF）
# - viewers                 观众统计（跨场客观数字）
# - agenda_plan             原始大纲（基准，只读）
# - agenda_runtime          运行进度（Agent 改）
# - game_events             游戏里程碑事件
# - timeline_summary        摘要层
# - llm_usage               LLM 调用记录
# - llm_requests            LLM 请求历史（完整请求/响应，dashboard 历史页数据源）
# - event_history           语义域事件流（录制回放 + dashboard 事件历史持久层）
# - sim_personas            模拟器常驻观众人设（运行时数据，WebUI 管理）
# - sim_gifts               模拟器礼物目录（运行时数据，WebUI 管理）
# - _memory_facts           SimpleMemory 事实记忆（模块私有）
# - _memory_profiles        SimpleMemory 人物画像（模块私有）
# - schema_migrations       版本管理
# =============================================================================


def build_schema_sql() -> str:
    """返回完整建表 DDL（IF NOT EXISTS 幂等）。"""
    return (
        # live_sessions —— 场次 + 直播实时状态
        _LIVE_SESSIONS_SQL
        + "\n"
        # live_chat —— 全量直播消息流（带 simulated 贯穿列）
        + _LIVE_CHAT_SQL
        + "\n"
        # gifts —— 礼物明细（带 simulated 贯穿列）
        + _GIFTS_SQL
        + "\n"
        # super_chats —— SC 明细（带 simulated 贯穿列）
        + _SUPER_CHATS_SQL
        + "\n"
        # topics —— 话题
        + _TOPICS_SQL
        + "\n"
        # viewers —— 观众统计
        + _VIEWERS_SQL
        + "\n"
        # agenda_plan —— 原始大纲
        + _AGENDA_PLAN_SQL
        + "\n"
        # agenda_runtime —— 运行进度
        + _AGENDA_RUNTIME_SQL
        + "\n"
        # game_events —— 游戏里程碑
        + _GAME_EVENTS_SQL
        + "\n"
        # timeline_summary —— 摘要层
        + _TIMELINE_SUMMARY_SQL
        + "\n"
        # llm_usage —— LLM 调用记录
        + _LLM_USAGE_SQL
        + "\n"
        # llm_requests —— LLM 请求历史（完整请求/响应）
        + _LLM_REQUESTS_SQL
        + "\n"
        # event_history —— 语义域事件流（录制回放 + 事件历史持久层）
        + _EVENT_HISTORY_SQL
        + "\n"
        # sim_personas —— 模拟器常驻观众人设
        + _SIM_PERSONAS_SQL
        + "\n"
        # sim_gifts —— 模拟器礼物目录
        + _SIM_GIFTS_SQL
        + "\n"
        # _memory_facts / _memory_profiles —— SimpleMemory 模块私有表（含索引）
        + _MEMORY_FACTS_SQL
        + "\n"
        + _MEMORY_PROFILES_SQL
        + "\n"
        # schema_migrations —— 版本管理
        + _SCHEMA_MIGRATIONS_SQL
    )


def list_expected_tables() -> List[str]:
    """返回启动自检必须存在的业务表名（含 schema_migrations），缺一即拒启。"""
    return [
        "live_sessions",
        "live_chat",
        "gifts",
        "super_chats",
        "topics",
        "viewers",
        "agenda_plan",
        "agenda_runtime",
        "game_events",
        "timeline_summary",
        "llm_usage",
        "llm_requests",
        "event_history",
        "sim_personas",
        "sim_gifts",
        "schema_migrations",
    ]


def list_private_tables() -> List[str]:
    """返回模块私有表名（``_`` 前缀），供所属模块自检；不进入启动闸门。"""
    return [
        "_memory_facts",
        "_memory_profiles",
    ]


# =============================================================================
# 单表 DDL（私有，便于维护与单表测试）
# =============================================================================


_LIVE_SESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS live_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id       TEXT NOT NULL DEFAULT '',
    platform        TEXT NOT NULL DEFAULT 'unknown',
    started_at_ms   INTEGER NOT NULL,
    ended_at_ms     INTEGER,
    title           TEXT,
    source          TEXT NOT NULL DEFAULT 'manual',
    heat            INTEGER NOT NULL DEFAULT 0,
    viewer_count    INTEGER NOT NULL DEFAULT 0,
    audience_total  INTEGER NOT NULL DEFAULT 0,
    updated_at_ms   INTEGER NOT NULL,
    CHECK (ended_at_ms IS NULL OR ended_at_ms >= started_at_ms)
);
""".strip()


_LIVE_CHAT_SQL = """
CREATE TABLE IF NOT EXISTS live_chat (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    timestamp_ms     INTEGER NOT NULL,
    sender_role      TEXT NOT NULL,
    sender_id        TEXT,
    sender_name      TEXT,
    content          TEXT NOT NULL,
    message_type     TEXT NOT NULL,
    message_id       TEXT,
    reply_to_message_id TEXT,
    tool_result      TEXT,
    simulated        INTEGER NOT NULL DEFAULT 0
);
""".strip()


_GIFTS_SQL = """
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
""".strip()


_SUPER_CHATS_SQL = """
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
""".strip()


_TOPICS_SQL = """
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
""".strip()


_VIEWERS_SQL = """
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
""".strip()


_AGENDA_PLAN_SQL = """
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
""".strip()


_AGENDA_RUNTIME_SQL = """
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
""".strip()


_GAME_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS game_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    game             TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    message          TEXT NOT NULL,
    scene            TEXT,
    timestamp_ms     INTEGER NOT NULL
);
""".strip()


_TIMELINE_SUMMARY_SQL = """
CREATE TABLE IF NOT EXISTS timeline_summary (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    start_ms         INTEGER NOT NULL,
    end_ms           INTEGER NOT NULL,
    summary          TEXT NOT NULL,
    tags             TEXT
);
""".strip()


_LLM_USAGE_SQL = """
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
""".strip()


_SIM_PERSONAS_SQL = """
CREATE TABLE IF NOT EXISTS sim_personas (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT NOT NULL UNIQUE,
    user_nickname       TEXT NOT NULL,
    role                TEXT NOT NULL,
    personality         TEXT NOT NULL,
    speaking_style      TEXT NOT NULL,
    fans_medal_level    INTEGER NOT NULL DEFAULT 0,
    guard_level         INTEGER NOT NULL DEFAULT 0,
    context_window_size INTEGER,
    is_active           INTEGER NOT NULL DEFAULT 1,
    messages_generated  INTEGER NOT NULL DEFAULT 0,
    created_at_ms       INTEGER NOT NULL,
    updated_at_ms       INTEGER NOT NULL
);
""".strip()


_SIM_GIFTS_SQL = """
CREATE TABLE IF NOT EXISTS sim_gifts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    gift_id          TEXT NOT NULL UNIQUE,
    gift_name        TEXT NOT NULL,
    category         TEXT NOT NULL,
    weight           INTEGER NOT NULL DEFAULT 1,
    data_type        TEXT NOT NULL,
    sc_amount_rmb    INTEGER,
    created_at_ms    INTEGER NOT NULL,
    updated_at_ms    INTEGER NOT NULL
);
""".strip()


# --- SimpleMemory 模块私有表（关键词召回的事实记忆 + 人物画像）---
# 索引随表建立：召回按时间倒序取窗口、按来源过滤，两者都是热路径。

_MEMORY_FACTS_SQL = """
CREATE TABLE IF NOT EXISTS _memory_facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    text          TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT '',
    tags          TEXT NOT NULL DEFAULT '',
    importance    INTEGER NOT NULL DEFAULT 0,
    timestamp_ms  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_facts_timestamp ON _memory_facts(timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_memory_facts_source ON _memory_facts(source);
""".strip()


_MEMORY_PROFILES_SQL = """
CREATE TABLE IF NOT EXISTS _memory_profiles (
    person_id       TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    updated_at_ms   INTEGER NOT NULL
);
""".strip()


# --- event_history —— 语义域事件流（EventHistoryService 落库）---
# payload 存完整载荷 JSON（回放端按 event_name 取回后直接反序列化）；
# 按日查询与按事件名过滤都是热路径，时间与 (事件名, 时间) 建索引。

_EVENT_HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS event_history (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id     TEXT NOT NULL,
    event_name    TEXT NOT NULL,
    timestamp_ms  INTEGER NOT NULL,
    level         TEXT NOT NULL DEFAULT 'info',
    source        TEXT NOT NULL DEFAULT '',
    summary       TEXT NOT NULL DEFAULT '',
    payload       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_history_ts ON event_history(timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_event_history_name_ts ON event_history(event_name, timestamp_ms);
""".strip()


# --- llm_requests —— LLM 请求历史（RequestHistoryManager 落库）---
# usage 拆平为三列以便 SQL 聚合（statistics/费用汇总）；request_params 与
# tool_calls 结构不定，存 JSON 文本。dashboard 历史页按时间倒序分页查询。

_LLM_REQUESTS_SQL = """
CREATE TABLE IF NOT EXISTS llm_requests (
    request_id          TEXT PRIMARY KEY,
    timestamp_ms        INTEGER NOT NULL,
    client_type         TEXT NOT NULL DEFAULT '',
    model_name          TEXT NOT NULL DEFAULT '',
    request_params      TEXT,
    response_content    TEXT,
    reasoning_content   TEXT,
    tool_calls          TEXT,
    prompt_tokens       INTEGER NOT NULL DEFAULT 0,
    completion_tokens   INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    cost                REAL NOT NULL DEFAULT 0,
    success             INTEGER NOT NULL DEFAULT 1,
    error               TEXT,
    latency_ms          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_llm_requests_ts ON llm_requests(timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_llm_requests_model ON llm_requests(model_name);
""".strip()


_SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version        INTEGER PRIMARY KEY,
    applied_at_ms  INTEGER NOT NULL
);
""".strip()


# =============================================================================
# 版本迁移回调（version → 原地修改、幂等）
# =============================================================================
# SQLiteStore 推进 schema_migrations 版本时按序执行；回调内部用列存在性检查
# 保证对"新建库（DDL 已含最新列）"与"重复执行"都安全。


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """检查表列是否存在（PRAGMA table_info）。"""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608 表名为代码内常量
    return any(row[1] == column for row in rows)


def _migrate_v4_session_semantics(conn: sqlite3.Connection) -> None:
    """v3 → v4：场次主键语义修正（一房多场）+ 回复关联列。

    - ``live_sessions`` 增加 ``source`` 列：旧库经 ADD COLUMN 补列时默认
      ``'legacy'``——所有存量行都是"房间号哈希映射"时代的遗留数据，天然标记；
      新建库的 DDL 默认 ``'manual'``（回调检测到列已存在则跳过）。
    - 存量遗留行收口：``ended_at_ms`` 为空的补为 ``updated_at_ms``（历史行
      没有可重建的结束边界，以其最后活动时刻封闭，避免永远显示"进行中"）。
    - ``live_chat`` 增加 ``message_id`` / ``reply_to_message_id`` 列与消息 ID 索引。
    """
    if not _column_exists(conn, "live_sessions", "source"):
        conn.execute("ALTER TABLE live_sessions ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy'")
    # 遗留行封闭（幂等：COALESCE 保留已有结束时间）
    conn.execute("UPDATE live_sessions SET ended_at_ms = updated_at_ms WHERE source = 'legacy' AND ended_at_ms IS NULL")
    if not _column_exists(conn, "live_chat", "message_id"):
        conn.execute("ALTER TABLE live_chat ADD COLUMN message_id TEXT")
    if not _column_exists(conn, "live_chat", "reply_to_message_id"):
        conn.execute("ALTER TABLE live_chat ADD COLUMN reply_to_message_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_live_chat_message_id ON live_chat(message_id)")


SCHEMA_MIGRATIONS: Dict[int, Callable[[sqlite3.Connection], None]] = {
    4: _migrate_v4_session_semantics,
}


__all__ = [
    "SCHEMA_VERSION",
    "SCHEMA_MIGRATIONS",
    "SchemaMigration",
    "build_schema_sql",
    "list_expected_tables",
    "list_private_tables",
]
