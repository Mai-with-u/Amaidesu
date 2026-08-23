"""
存储 Schema 定义（Wave 3 / §1.50 / §1.53 9b 定案）

本模块是 11 张表的**单一事实源**，对应权威文档
``.omo/drafts/amaidesu-v2-storage-schema.md``。如需变更表结构，先修改该
权威文档再同步本文件。

## 命名硬规则
- 时间字段一律 ``*_ms``（毫秒 int，对齐 §1.44）
- 场次叫 ``live_sessions``，消息流叫 ``live_chat``（§1.6 live chat 行业标准）
- ``live_chat`` / ``gifts`` / ``super_chats`` 表加 ``simulated`` 贯穿列
  （§1.6 用户拍板：模拟数据用 False 默认 / True 标记，消费方 WHERE
  ``simulated=0`` 排除模拟数据）

## Schema 迁移机制
- ``schema_migrations(version PK, applied_at_ms)``（复用 MaiBot 模式）
- ``SCHEMA_VERSION`` 常量 = 当前权威版本
- ``build_schema_sql()`` 返回完整建表 DDL（IF NOT EXISTS 幂等）
- ``list_expected_tables()`` 返回期望表名（启动自检用）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

# 当前 Schema 版本——改动表结构时必须同步升级（见 AGENTS.md §"配置 Schema 变更规则"）
SCHEMA_VERSION: int = 1


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    """单次迁移记录（MaiBot 模式）"""

    version: int
    applied_at_ms: int


# =============================================================================
# 11 张表 + schema_migrations（按权威文档顺序）
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
        # schema_migrations —— 版本管理
        + _SCHEMA_MIGRATIONS_SQL
    )


def list_expected_tables() -> List[str]:
    """返回期望的全部表名（含 schema_migrations），用于启动自检/测试断言。"""
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
        "schema_migrations",
    ]


# =============================================================================
# 单表 DDL（私有，便于维护与单表测试）
# =============================================================================


_LIVE_SESSIONS_SQL = """
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


_SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version        INTEGER PRIMARY KEY,
    applied_at_ms  INTEGER NOT NULL
);
""".strip()


__all__ = [
    "SCHEMA_VERSION",
    "SchemaMigration",
    "build_schema_sql",
    "list_expected_tables",
]
