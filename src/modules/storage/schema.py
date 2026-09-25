"""
存储 Schema 定义

本模块是全部 SQLite 表的**单一事实源**：16 张业务表（11 张核心直播表 +
2 张模拟器运行时表 + 1 张流程单表 + 2 张观众画像表）+ ``schema_migrations``。
任何建表 DDL 都必须落在这里，不允许业务模块自带 ``CREATE TABLE``——否则
表结构游离于 ``SCHEMA_VERSION`` 版本管理之外，迁移机制无法覆盖。

## 命名硬规则
- 时间字段一律 ``*_ms``（毫秒 int）
- 场次叫 ``live_sessions``，消息流叫 ``live_chat``（live chat 行业标准）
- ``live_sessions`` 一行 = 一场直播（有开始/结束边界）；房间/状态
  静态属性（``stream_id`` 普通属性列，**不参与主键语义**，一房多场）
- ``live_sessions.source`` 标记场次来源（manual=手动 / replay=模拟器回放 /
  legacy=历史遗留行）
- ``live_chat.message_id`` 与 ``live_chat.reply_to_message_id`` 构成"主播发言
  回复了哪条观众弹幕"的关联键（互动分析数据面）
- ``live_chat.sender_role`` 取值：``viewer``（观众弹幕）/ ``assistant``（主播
  发言）/ ``partner``（联动对象发言——不计观众统计）
- ``live_chat`` / ``gifts`` / ``super_chats`` / ``guards`` 表加 ``simulated``
  贯穿列（模拟数据用 False 默认 / True 标记，消费方 WHERE ``simulated=0``
  排除模拟数据）；``simulated`` 与 ``platform`` 正交——模拟器造的是 B 站
  格式数据（platform=bilibili + simulated=1），过滤假数据靠 simulated
- **观众身份键 = ``(platform, user_id)`` 复合键**（观众画像/统计/明细表统一）。
  platform 是本项目自定的稳定键：平台名（``bilibili`` / ``douyin`` / …）+
  调试保留字 ``console``（控制台输入无 simulated 标记，靠 platform 隔离
  身份；模拟器数据归 bilibili，由 simulated 区分真假），由采集器作为
  装配期常量注入。不同平台账号视为不同的人，付费/统计天然按平台隔离，
  不跨平台聚合
- 付费明细三表（gifts / super_chats / guards）金额单位 = **平台最小虚拟
  货币单位**（B 站金瓜子，1000 金瓜子 = 1 元），取值口径 = 标价（实付另记
  ``paid_price``）；``currency`` 带平台前缀（``bilibili_gold_coin`` /
  ``bilibili_silver_coin``），银瓜子（免费礼物）照常落库、付费统计按
  currency 过滤。付费事件不可复刻，三表存 ``raw_data`` 兜底（可修复解析后
  重放补数）；弹幕可复刻故不存
- 所有数据库表统一纳管：无 ``_`` 私有前缀表（私有表机制已废除——伪隔离，
  物理同库无隔离机制，且"免检"是伪豁免）

## Schema 迁移机制
- ``schema_migrations(version PK, applied_at_ms)``
- ``SCHEMA_VERSION`` 常量 = 当前权威版本
- 版本迁移注册表在 ``migrations/`` 包（一版本一文件，严格 +1，每版必有
  条目）。``SQLiteDatabase`` 推进版本时从注册表按序执行；回调原地修改、幂等，
  用列存在性检查保证对新建库与已迁移库安全
- ``build_schema_sql()`` 返回完整建表 DDL（IF NOT EXISTS 幂等，含最新列）
- ``list_expected_tables()`` 返回启动自检必须存在的业务表名（缺一即拒启）
- 私有表机制已废除：所有表都进 ``list_expected_tables()`` 统一闸门
"""

from __future__ import annotations

from typing import List

# 当前 Schema 版本——改动表结构时必须同步升级
SCHEMA_VERSION: int = 10


# =============================================================================
# 业务表 + schema_migrations
# =============================================================================
# - live_sessions           场次 + 直播实时状态（一场一行）
# - live_chat               全量直播消息流（行业 live chat）
# - gifts                   礼物明细（独立副表）
# - super_chats             SC 明细（独立副表）
# - guards                  大航海开通/续费明细（购买事件表）
# - topics                  话题（每行一个，1NF）
# - viewers                 观众统计（跨场客观数字）
# - viewer_facts            观众事实（画像原料：从弹幕/SC 提取的"关于观众的事实"）
# - viewer_profiles         观众画像（LLM 压缩后的画像文本，主播认人的依据）
# - game_events             游戏里程碑事件
# - timeline_summary        摘要层
# - llm_usage               LLM 调用记录
# - llm_requests            LLM 请求历史（完整请求/响应，dashboard 历史页数据源）
# - sim_personas            模拟器常驻观众人设（运行时数据，WebUI 管理）
# - sim_gifts               模拟器礼物目录（运行时数据，WebUI 管理）
# - rundowns                流程单（rundown 子系统）
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
        # guards —— 大航海开通/续费明细
        + _GUARDS_SQL
        + "\n"
        # topics —— 话题
        + _TOPICS_SQL
        + "\n"
        # viewers —— 观众统计
        + _VIEWERS_SQL
        + "\n"
        # viewer_facts —— 观众事实（画像原料）
        + _VIEWER_FACTS_SQL
        + "\n"
        # viewer_profiles —— 观众画像
        + _VIEWER_PROFILES_SQL
        + "\n"
        # rundowns —— 流程单
        + _RUNDOWNS_SQL
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
        # sim_personas —— 模拟器常驻观众人设
        + _SIM_PERSONAS_SQL
        + "\n"
        # sim_gifts —— 模拟器礼物目录
        + _SIM_GIFTS_SQL
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
        "guards",
        "topics",
        "viewers",
        "viewer_facts",
        "viewer_profiles",
        "rundowns",
        "game_events",
        "timeline_summary",
        "llm_usage",
        "llm_requests",
        "sim_personas",
        "sim_gifts",
        "schema_migrations",
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
    platform         TEXT NOT NULL DEFAULT '',
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
CREATE INDEX IF NOT EXISTS idx_live_chat_session_ts ON live_chat(live_session_id, timestamp_ms);
""".strip()


_GIFTS_SQL = """
CREATE TABLE IF NOT EXISTS gifts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id   INTEGER NOT NULL,
    timestamp_ms      INTEGER NOT NULL,
    platform          TEXT NOT NULL DEFAULT '',
    user_id           TEXT NOT NULL,
    user_name         TEXT NOT NULL,
    gift_id           INTEGER NOT NULL DEFAULT 0,
    gift_name         TEXT NOT NULL,
    quantity          INTEGER NOT NULL,
    unit_price        INTEGER NOT NULL DEFAULT 0,
    total_price       INTEGER NOT NULL DEFAULT 0,
    paid_price        INTEGER NOT NULL DEFAULT 0,
    currency          TEXT NOT NULL DEFAULT '',
    guard_level       INTEGER NOT NULL DEFAULT 0,
    fans_medal_level  INTEGER NOT NULL DEFAULT 0,
    fans_medal_name   TEXT NOT NULL DEFAULT '',
    combo_id          TEXT NOT NULL DEFAULT '',
    combo_count       INTEGER NOT NULL DEFAULT 0,
    combo_gift        INTEGER NOT NULL DEFAULT 0,
    blind_gift_id     INTEGER NOT NULL DEFAULT 0,
    msg_id            TEXT NOT NULL DEFAULT '',
    raw_data          TEXT,
    simulated         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_gifts_user ON gifts(user_id);
""".strip()


_SUPER_CHATS_SQL = """
CREATE TABLE IF NOT EXISTS super_chats (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id   INTEGER NOT NULL,
    timestamp_ms      INTEGER NOT NULL,
    platform          TEXT NOT NULL DEFAULT '',
    user_id           TEXT NOT NULL,
    user_name         TEXT NOT NULL,
    message           TEXT NOT NULL,
    total_price       INTEGER NOT NULL DEFAULT 0,
    currency          TEXT NOT NULL DEFAULT '',
    start_time        INTEGER NOT NULL DEFAULT 0,
    end_time          INTEGER NOT NULL DEFAULT 0,
    guard_level       INTEGER NOT NULL DEFAULT 0,
    fans_medal_level  INTEGER NOT NULL DEFAULT 0,
    fans_medal_name   TEXT NOT NULL DEFAULT '',
    message_id        TEXT NOT NULL DEFAULT '',
    raw_data          TEXT,
    simulated         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_super_chats_user ON super_chats(user_id);
""".strip()


_GUARDS_SQL = """
CREATE TABLE IF NOT EXISTS guards (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id   INTEGER NOT NULL,
    timestamp_ms      INTEGER NOT NULL,
    platform          TEXT NOT NULL DEFAULT '',
    user_id           TEXT NOT NULL,
    user_name         TEXT NOT NULL,
    guard_level       INTEGER NOT NULL DEFAULT 0,
    guard_num         INTEGER NOT NULL DEFAULT 0,
    guard_unit        TEXT NOT NULL DEFAULT '',
    total_price       INTEGER NOT NULL DEFAULT 0,
    currency          TEXT NOT NULL DEFAULT '',
    fans_medal_level  INTEGER NOT NULL DEFAULT 0,
    fans_medal_name   TEXT NOT NULL DEFAULT '',
    msg_id            TEXT NOT NULL DEFAULT '',
    raw_data          TEXT,
    simulated         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_guards_user ON guards(user_id);
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
    platform            TEXT NOT NULL DEFAULT '',
    user_id             TEXT NOT NULL,
    user_name           TEXT NOT NULL,
    message_count       INTEGER NOT NULL DEFAULT 0,
    gift_count          INTEGER NOT NULL DEFAULT 0,
    replied_count       INTEGER NOT NULL DEFAULT 0,
    interaction_count   INTEGER NOT NULL DEFAULT 0,
    paid_count          INTEGER NOT NULL DEFAULT 0,
    paid_amount         INTEGER NOT NULL DEFAULT 0,
    last_active_ms      INTEGER NOT NULL,
    UNIQUE(platform, user_id)
);
""".strip()


# --- viewer_facts —— 观众事实（画像原料）---
# 每行一条"关于某观众的事实"（由后台循环从弹幕/SC 批提取），归属程序化：
# 提取输出 message_id → 批内消息 → (platform, user_id)，不靠 LLM 报人名。
# 身份快照/付费明细等结构化原料直读明细表，不入本表。

_VIEWER_FACTS_SQL = """
CREATE TABLE IF NOT EXISTS viewer_facts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    platform           TEXT NOT NULL,
    user_id            TEXT NOT NULL,
    fact_text          TEXT NOT NULL,
    source_message_id  TEXT NOT NULL DEFAULT '',
    created_at_ms      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_viewer_facts_identity ON viewer_facts(platform, user_id);
CREATE INDEX IF NOT EXISTS idx_viewer_facts_created ON viewer_facts(created_at_ms);
""".strip()


# --- viewer_profiles —— 观众画像 ---
# 每观众一行 LLM 压缩画像；``last_compressed_at_ms`` 是增量压缩水位
# （只把水位后的新原料喂给下次压缩）。有画像才注入 planner——主播由此认人。

_VIEWER_PROFILES_SQL = """
CREATE TABLE IF NOT EXISTS viewer_profiles (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    platform               TEXT NOT NULL,
    user_id                TEXT NOT NULL,
    profile_text           TEXT NOT NULL,
    last_compressed_at_ms  INTEGER NOT NULL DEFAULT 0,
    updated_at_ms          INTEGER NOT NULL,
    UNIQUE(platform, user_id)
);
""".strip()


_RUNDOWNS_SQL = """
CREATE TABLE IF NOT EXISTS rundowns (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    segments_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
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
    timestamp_ms       INTEGER NOT NULL,
    request_id         TEXT
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
    unit_price       INTEGER NOT NULL DEFAULT 0,
    created_at_ms    INTEGER NOT NULL,
    updated_at_ms    INTEGER NOT NULL
);
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
    cache_hit_tokens    INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens   INTEGER NOT NULL DEFAULT 0,
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
# 版本迁移回调已迁出至 ``migrations/`` 包（一版本一文件），
# 注册表见 ``src/modules/storage/migrations/__init__.py``。
# =============================================================================


__all__ = [
    "SCHEMA_VERSION",
    "build_schema_sql",
    "list_expected_tables",
]
