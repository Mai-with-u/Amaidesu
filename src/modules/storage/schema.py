"""
存储 Schema 定义

本模块是全部 SQLite 表的**单一事实源**：26 张业务表（12 张核心直播表 +
2 张模拟器运行时表 + 5 张流程单域表 + 1 张 LLM 请求历史表 +
1 张游戏生物名册表 + 1 张不记录名单表 + 2 张记忆账本表 +
1 张游戏社交事实表）+ ``schema_migrations``。
任何建表 DDL 都必须落在这里，不允许业务模块自带 ``CREATE TABLE``——否则
表结构游离于 ``SCHEMA_VERSION`` 版本管理之外，迁移机制无法覆盖。

## 命名硬规则
- 时间字段一律 ``*_ms``（毫秒 int）
- 场次叫 ``live_sessions``，消息流叫 ``live_chat``（live chat 行业标准）
- ``live_sessions`` 一行 = 一场直播（有开始/结束边界）；房间/状态
  静态属性（``stream_id`` 普通属性列，**不参与主键语义**，一房多场）
- ``live_sessions.source`` 标记场次来源（manual=手动 / replay=模拟器回放 /
  generate=模拟器生成 / legacy=历史遗留行）
- ``live_sessions.kind`` 标记场次类型（live=正式，面向真实观众 / rehearsal=
  彩排，模拟、回放与调试）；与来源正交，开场时声明。主播记忆按类型分两条线，
  对外同步与统计只认正式场
- ``live_chat.message_id`` 与 ``live_chat.reply_to_message_id`` 构成"主播发言
  回复了哪条观众弹幕"的关联键（互动分析数据面）
- ``live_chat.sender_role`` 取值：``viewer``（观众弹幕）/ ``assistant``（主播
  发言）/ ``partner``（联动对象发言——不计观众统计）
- ``live_chat`` / ``gifts`` / ``super_chats`` / ``guards`` 表加 ``simulated``
  贯穿列（模拟数据用 False 默认 / True 标记，消费方 WHERE ``simulated=0``
  排除模拟数据）；``simulated`` 与 ``platform`` 正交——模拟器造的是 B 站
  格式数据（platform=bilibili + simulated=1），过滤假数据靠 simulated
- **观众 = ``viewers`` 一行**（一行一人，按平台），``viewers.id`` 是内部观众
  ID，明细四表与画像经 ``viewer_id`` 引用它。外部身份记在同一行：
  ``account_id``（平台账号 ID，B 站 uid）与 ``open_id`` + ``open_app_id``
  （开放平台发给某个应用的 ID）。明细行另存收到时的原始外部 ID（``sender_id`` /
  ``user_id``）及其种类 ``id_kind``。platform 是本项目自定的稳定键：平台名
  （``bilibili`` / ``douyin`` / …）+ 调试保留字 ``console``，由采集器作为
  装配期常量注入；不同平台的观众始终是不同行，付费/统计按平台隔离
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
- 启动时先执行建表汇总、再跑迁移：旧库里已经存在的表不会被重建，迁移才补上的列
  在建表汇总执行那一刻还不存在。所以给既有表新加的列，索引和触发器只写在那一版
  迁移里，不写进建表汇总（新库照样从版本 0 跑完整条迁移链，索引一样建得出来）
- ``list_expected_tables()`` 返回启动自检必须存在的业务表名（缺一即拒启）
- 私有表机制已废除：所有表都进 ``list_expected_tables()`` 统一闸门
"""

from __future__ import annotations

from typing import List

# 当前 Schema 版本——改动表结构时必须同步升级
SCHEMA_VERSION: int = 38


# =============================================================================
# 业务表 + schema_migrations
# =============================================================================
# - live_sessions           场次 + 直播实时状态（一场一行）
# - live_chat               全量直播消息流（行业 live chat）
# - gifts                   礼物明细（独立副表）
# - super_chats             SC 明细（独立副表）
# - guards                  大航海开通/续费明细（购买事件表）
# - topics                  话题（每行一个，1NF）
# - viewers                 观众（一行一人，记外部 ID 与跨场客观数字）
# - viewer_names            观众昵称记录（含曾用名）
# - forget_list             不记录名单（发过"忘了我"的观众外部 ID 密钥摘要）
# - game_events             游戏里程碑事件
# - game_creatures          游戏生物名册（游戏 Agent 记住的同伴：主播起的名字、死没死，按世界分开）
# - memory_vectors_*        记忆检索的向量索引（档案行、承诺、台词、游戏事件各一张；派生物）
# - memory_fts_*            记忆检索的全文索引（FTS5 虚表，同上四类；派生物）
# - memory_cue_log          线索卡记录（推了哪张、谁触发的、用上没有）
# - memory_edits            记忆页人工改动记录（审计，同时是整理评测的标准答案）
# - viewer_candidates       观众候选（观众定下、她当场接受的事与她说欠着的承诺；人批准才进记忆）
# - game_players            游戏里的人（按游戏给的 UUID 认出的别的玩家）
# - game_player_interactions 和她的互动（打招呼、送东西、切磋、一起做的事、帮过忙）
# - game_chat               公屏游戏聊天（私聊不落；认不出是谁说的只占位）
# - player_candidates       玩家自述的待审候选（人批准才进记忆）
# - viewer_player_links     运营者确认的观众与玩家对应（只用来连带删除）
# - timeline_summary        摘要层
# - llm_usage               LLM 调用记录
# - llm_requests            LLM 请求历史（完整请求/响应，dashboard 历史页数据源）
# - sim_personas            模拟器常驻观众人设（运行时数据，WebUI 管理）
# - sim_gifts               模拟器礼物目录（运行时数据，WebUI 管理）
# - clip_segments           切片段（AI 提议 + 运营手调的时段，含切割状态与产物路径）
# - rundowns                流程单模板（元信息行）
# - rundown_segments        流程单环节行（凡环节皆行，seq 定序）
# - rundown_instances       流程单实例头（每场一行，崩溃恢复 + 时点快照）
# - rundown_instance_segments 流程单实例环节快照行
# - rundown_transitions     流程单变更事实行（append-only）
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
        # viewer_names —— 观众昵称记录
        + _VIEWER_NAMES_SQL
        + "\n"
        # forget_list —— 不记录名单
        + _FORGET_LIST_SQL
        + "\n"
        # commitments —— 承诺账本（她当众答应过、还没了结的事）
        + _COMMITMENTS_SQL
        + "\n"
        # archive_lines —— 场次档案（每场几行，供下次开播回想上一场）
        + _ARCHIVE_LINES_SQL
        + "\n"
        # archive_snapshots / archive_snapshot_lines —— 滚动档案快照（只引用档案行）
        + _ARCHIVE_SNAPSHOTS_SQL
        + "\n"
        # rundowns —— 流程单模板（元信息）
        + _RUNDOWNS_SQL
        + "\n"
        # rundown_segments —— 流程单环节行
        + _RUNDOWN_SEGMENTS_SQL
        + "\n"
        # rundown_instances —— 流程单实例头
        + _RUNDOWN_INSTANCES_SQL
        + "\n"
        # rundown_instance_segments —— 流程单实例环节快照行
        + _RUNDOWN_INSTANCE_SEGMENTS_SQL
        + "\n"
        # rundown_transitions —— 流程单变更事实行
        + _RUNDOWN_TRANSITIONS_SQL
        + "\n"
        # game_events —— 游戏里程碑
        + _GAME_EVENTS_SQL
        + "\n"
        # game_creatures —— 游戏生物名册
        + _GAME_CREATURES_SQL
        + "\n"
        # memory_vectors_* / memory_fts_* —— 记忆检索的派生索引（排在来源表之后：触发器要引用它们）
        + _MEMORY_INDEX_SQL
        + "\n"
        # memory_cue_log —— 线索卡记录
        + _MEMORY_CUE_LOG_SQL
        + "\n"
        # memory_edits —— 记忆页人工改动记录（排在档案行之后：删行触发器要引用它）
        + _MEMORY_EDITS_SQL
        + "\n"
        # viewer_candidates —— 观众候选（排在台词、档案行与承诺之后：外键要引用它们）
        + _VIEWER_CANDIDATES_SQL
        + "\n"
        # game_players 等 —— 游戏里的人（排在场次、台词与观众之后：外键要引用它们）
        + _GAME_PLAYERS_SQL
        + "\n"
        # game_social_facts —— 游戏社交事实（排在场次、台词与游戏公屏之后：外键要引用它们）
        + _GAME_SOCIAL_FACTS_SQL
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
        # clip_segments —— 切片段（排在 live_sessions 之后：外键要引用它）
        + _CLIP_SEGMENTS_SQL
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
        "viewer_names",
        "forget_list",
        "commitments",
        "archive_lines",
        "archive_snapshots",
        "archive_snapshot_lines",
        "rundowns",
        "rundown_segments",
        "rundown_instances",
        "rundown_instance_segments",
        "rundown_transitions",
        "game_events",
        "game_creatures",
        "memory_vectors_archive",
        "memory_vectors_commitment",
        "memory_vectors_speech",
        "memory_vectors_event",
        "memory_fts_archive",
        "memory_fts_commitment",
        "memory_fts_speech",
        "memory_fts_event",
        "memory_cue_log",
        "memory_edits",
        "viewer_candidates",
        "game_players",
        "game_player_interactions",
        "game_chat",
        "game_social_facts",
        "player_candidates",
        "viewer_player_links",
        "timeline_summary",
        "llm_usage",
        "llm_requests",
        "sim_personas",
        "sim_gifts",
        "clip_segments",
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
    kind            TEXT NOT NULL DEFAULT 'live' CHECK (kind IN ('live', 'rehearsal')),
    heat            INTEGER NOT NULL DEFAULT 0,
    viewer_count    INTEGER NOT NULL DEFAULT 0,
    audience_total  INTEGER NOT NULL DEFAULT 0,
    updated_at_ms   INTEGER NOT NULL,
    settled_at_ms   INTEGER,
    settle_attempts INTEGER NOT NULL DEFAULT 0,
    settle_report   TEXT,
    reviewed_at_ms  INTEGER,
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
    viewer_id        INTEGER,
    id_kind          TEXT NOT NULL DEFAULT '',
    content          TEXT NOT NULL,
    message_type     TEXT NOT NULL,
    message_id       TEXT,
    reply_to_message_id TEXT,
    simulated        INTEGER NOT NULL DEFAULT 0,
    utterance_id     TEXT,
    dropped_at_ms    INTEGER,
    memory_refs      TEXT,
    reply_player_id  INTEGER REFERENCES game_players(id) ON DELETE SET NULL
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
    viewer_id         INTEGER,
    id_kind           TEXT NOT NULL DEFAULT '',
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
    viewer_id         INTEGER,
    id_kind           TEXT NOT NULL DEFAULT '',
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
    viewer_id         INTEGER,
    id_kind           TEXT NOT NULL DEFAULT '',
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
    last_active_ms      INTEGER NOT NULL,
    sessions_count      INTEGER NOT NULL DEFAULT 0,
    last_session_id     INTEGER,
    prev_seen_ms        INTEGER,
    forget_requested_at_ms INTEGER,
    UNIQUE(platform, account_id),
    UNIQUE(platform, open_app_id, open_id)
);
""".strip()


# --- viewer_names —— 观众昵称记录 ---
# 每位观众用过的每个昵称一行（含当前昵称）；只在新建观众、改名、合并时写入。
# 当前昵称的最近出现时刻以 viewers.name_seen_ms 为准，本表不逐条消息刷新。

_VIEWER_NAMES_SQL = """
CREATE TABLE IF NOT EXISTS viewer_names (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_id        INTEGER NOT NULL,
    user_name        TEXT NOT NULL,
    first_seen_ms    INTEGER NOT NULL,
    last_seen_ms     INTEGER NOT NULL,
    UNIQUE(viewer_id, user_name)
);
""".strip()


# --- forget_list —— 不记录名单 ---
# 存"忘了我"发令者外部 ID 的带密钥摘要 HMAC-SHA256(库外密钥, 种类|外部ID)，不落明文外部 ID。

_FORGET_LIST_SQL = """
CREATE TABLE IF NOT EXISTS forget_list (
    id_hash BLOB PRIMARY KEY,
    at_ms   INTEGER NOT NULL
);
""".strip()


# --- commitments —— 承诺账本 ---
# 她当众答应过、还没了结的事：每个正式场下播后由整理找新的、了结旧的。
# quote 是她播出原句的逐字片段（整理时对着台词行校验子串），台账因此自带证据，
# 不依赖台词行还在不在；evidence_chat_id 指向那句台词的 live_chat 行，原话所在
# 场次被删时这一列成为空引用，读方按“原句所在场次已删除”处理。
# 状态三分：open 未了结、closed 已了结（做完或放弃，看了结原话）、void 运营者判误记。
# 判误记不删行而转 void：墓碑让之后每次“找新承诺”都能认出同一件事，不重复登记。
# 不存“谁改的”：模型只会插入新行、把 open 改成 closed，运营者改过的行模型不再碰。
# save_id / save_name 是挂了游戏的承诺在哪个存档里答应的（整理时由代码按本场游戏事件判定，
# 空串 = 不分存档或存档不明）：换了存档时，别的存档里的承诺不再当成眼前的事推给她。
# keywords 是被动想起的触发词（JSON 字符串列表，同档案行）：观众提到这些词时把这条承诺推给她。
# target_player_id 是对某位游戏玩家的承诺（→ game_players，玩家被删时承诺一起删）；对全体或对观众时为空。
# evidence_game_chat_id 是她在游戏公屏打出去的那句（→ game_chat）：对玩家的承诺证据也可以是公屏上
# 她自己打的字，这时 evidence_chat_id 为空；一句公屏只登记一条，重新整理时按它对齐编号。
# 这两列的索引由 v32 迁移建（两列都是 v32 才补的，旧库执行建表汇总时它们还不存在）。

_COMMITMENTS_SQL = """
CREATE TABLE IF NOT EXISTS commitments (
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
    keywords           TEXT NOT NULL DEFAULT '[]',
    target_player_id   INTEGER REFERENCES game_players(id) ON DELETE CASCADE,
    evidence_game_chat_id INTEGER REFERENCES game_chat(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status);
""".strip()


# --- archive_lines —— 场次档案 ---
# 每场正式场下播后写的本场新事（≤8 行），标题加她播出原句的逐字片段，写完不改（运营者可删单行）。
# 也是回想工具的检索单元；game 是这一行写的是哪个游戏世界里的事（建筑、进度、地点；与世界无关为空），
# save_id / save_name 是那个世界的存档（整理时由代码按本场游戏事件判定，空串 = 不分存档或存档不明）。
# 前几场仍然成立的事不复制进本场，由下面的快照引用原行。
# keywords 是被动想起的触发词（JSON 字符串列表：名字、地名、物件名、梗的原词，至多 4 个，
# 每个都在标题或原话里出现过）；place 是这件事挂在哪个记住的地点（地点名，只在 save_id 那个存档里成立）。
# human_locked = 1 表示运营者在记忆页改过这一行的标题、稳定标记、触发词或地点，或这一行是运营者新增的：
# 之后的整理不能去掉或替换它，只有人能动。原话与证据行永远不改，只能随删除一起删。
# source 是这一行的来源：streamer 是她自己说的（整理自动写、人新增的都是），viewer 是人批准的观众候选
# （观众定下、她当场接受的事；原话仍是她那句回应）。viewer_chat_id 是观众来源的行挂着的那条弹幕，
# 按外键级联：删观众、忘了我、按句删弹幕时这一行随之删除。viewer_word 是批准时从候选抄来的观众定下的
# 那个词，渲染原话时这一段不遮成"那位观众"（她自己说的行为空）。viewer_chat_id 的索引由 v31 迁移建
# （这一列是 v31 才补的，旧库执行建表汇总时它还不存在）。

_ARCHIVE_LINES_SQL = """
CREATE TABLE IF NOT EXISTS archive_lines (
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
);
CREATE INDEX IF NOT EXISTS idx_archive_lines_session ON archive_lines(live_session_id);
""".strip()


# --- archive_snapshots / archive_snapshot_lines —— 滚动档案快照 ---
# 每个整理过的正式场留一份快照：她下次开播该记得的事（≤12 行），每行引用一条 archive_lines，
# 不复制——原行被删（删单行、按句删台词、改彩排、删场次）时外键级联撤掉所有快照里的引用，
# 删观众时只改原行文本，快照照样看到遮过名的文本。archive_snapshots 记哪些场次写过快照
# （快照可以是空的；没有这一行的已整理场次是滚动之前的旧场次）。lasting 标名字、住处、关系
# 这类稳定的事；seen_at_ms 是最近一次写下或再提起这件事的那一场的开播时刻，超出上限时
# 最久没被再提的先出。pinned = 1 是运营者放回快照或新增的行：取舍时排在稳定的事之前，整理不会挤掉。

_ARCHIVE_SNAPSHOTS_SQL = """
CREATE TABLE IF NOT EXISTS archive_snapshots (
    live_session_id INTEGER PRIMARY KEY REFERENCES live_sessions(id) ON DELETE CASCADE,
    written_at_ms   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS archive_snapshot_lines (
    live_session_id INTEGER NOT NULL REFERENCES archive_snapshots(live_session_id) ON DELETE CASCADE,
    archive_line_id INTEGER NOT NULL REFERENCES archive_lines(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    lasting         INTEGER NOT NULL DEFAULT 0,
    seen_at_ms      INTEGER NOT NULL,
    pinned          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (live_session_id, archive_line_id)
);
CREATE INDEX IF NOT EXISTS idx_archive_snapshot_lines_line ON archive_snapshot_lines(archive_line_id);
""".strip()


_RUNDOWNS_SQL = """
CREATE TABLE IF NOT EXISTS rundowns (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
""".strip()


# --- rundown_segments —— 流程单环节行 ---
# 判据"凡环节皆行"：环节是查询面实体（编排页逐环节渲染、运行态按环节定位），
# 整段 JSON 塞单列无法按环节索引。``seq`` 定序（模板内顺序即播放顺序），
# ``(rundown_id, seg_id)`` 唯一保证 goto 定位键稳定。

_RUNDOWN_SEGMENTS_SQL = """
CREATE TABLE IF NOT EXISTS rundown_segments (
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
CREATE INDEX IF NOT EXISTS idx_rundown_segments_rundown ON rundown_segments(rundown_id, seq);
""".strip()


# --- rundown_instances / rundown_instance_segments —— 流程单实例双表 ---
# 实例头一场一行（live_session_id 主键），承载游标与计时锚点的时点快照：
# 崩溃恢复读一行即还原（快照式持久化，不做事件溯源重放）；
# 环节快照行随变更事务内全量替换，防模板漂移（恢复源 = 实例，不是模板）。
# 游标列名用 ``seg_index``——``index`` 是 SQL 关键字，直接用会踩坑。

_RUNDOWN_INSTANCES_SQL = """
CREATE TABLE IF NOT EXISTS rundown_instances (
    live_session_id              INTEGER PRIMARY KEY,
    template_id                  TEXT NOT NULL DEFAULT '',
    seg_index                    INTEGER NOT NULL DEFAULT -1,
    segment_started_at_ms        INTEGER NOT NULL DEFAULT 0,
    paused_at_ms                 INTEGER,
    rundown_started_at_ms        INTEGER NOT NULL DEFAULT 0,
    accumulated_pause_ms         INTEGER NOT NULL DEFAULT 0,
    accumulated_session_pause_ms INTEGER NOT NULL DEFAULT 0,
    updated_at_ms                INTEGER NOT NULL
);
""".strip()


_RUNDOWN_INSTANCE_SEGMENTS_SQL = """
CREATE TABLE IF NOT EXISTS rundown_instance_segments (
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
""".strip()


# --- rundown_transitions —— 流程单变更事实行（append-only）---
# 每次环节变更（load/append/goto/next/renew/pause/resume/finish/restore…）
# 一行事实：分析"实际停留 vs 预期停留"的数据面。``expected_ms`` 每行都填
# （当前环节预期时长，实际停留统计免 JOIN 模板表）；``task_description``
# 仅 append 行填（append 后模板行可能被改写，事实行存当时的描述）。

_RUNDOWN_TRANSITIONS_SQL = """
CREATE TABLE IF NOT EXISTS rundown_transitions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL,
    rundown_id      TEXT NOT NULL,
    action          TEXT NOT NULL,
    seg_id          TEXT NOT NULL DEFAULT '',
    seg_title       TEXT NOT NULL DEFAULT '',
    seg_index       INTEGER NOT NULL,
    by              TEXT NOT NULL,
    at_ms           INTEGER NOT NULL,
    expected_ms     INTEGER,
    task_description TEXT,
    updated_at_ms   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rundown_transitions_session ON rundown_transitions(live_session_id, at_ms);
""".strip()


# 游戏里程碑：save_id / save_name 是事件发生时角色所在的存档，由发布事件的游戏 Agent 或
# 采集器自己填（空串 = 这个游戏不分存档，或当时还没认出存档）。下播整理按它判定档案行与
# 承诺属于哪个存档，是"某一场玩过哪些存档"的唯一依据。initiator 只有委派账行（delegation_*）填：
# 发起这件委派的 Agent 注册名（运营直派为 operator），续播只认领主播自己派的；其余行为空串。
_GAME_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS game_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    game             TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    message          TEXT NOT NULL,
    scene            TEXT,
    timestamp_ms     INTEGER NOT NULL,
    save_id          TEXT NOT NULL DEFAULT '',
    save_name        TEXT NOT NULL DEFAULT '',
    initiator        TEXT NOT NULL DEFAULT ''
);
""".strip()


# 游戏 Agent 的生物名册：一行一只（角色自己的生物，或主播起过名的生物），按"游戏 + 世界 + 身份键"区分。
# world_key 是游戏报的世界身份编号（同一个 UUID 只在同一个世界里有意义），游戏没报时为空串；
# creature_key 有 UUID 时是 uuid:<UUID>，游戏没报 UUID 时是 look:<种类>|<皮肤>|<命名牌名>。
# died_ms 非空 = 已死；died_session_id / next_session_id 是死的那一场与之后的第一场，
# 主播只在这两场里被提起她的死，之后只留在名册里，名字不被安到别的生物身上。
_GAME_CREATURES_SQL = """
CREATE TABLE IF NOT EXISTS game_creatures (
    game             TEXT NOT NULL,
    world_key        TEXT NOT NULL,
    creature_key     TEXT NOT NULL,
    type             TEXT NOT NULL,
    kind             TEXT NOT NULL DEFAULT '',
    name             TEXT NOT NULL DEFAULT '',
    skin             TEXT NOT NULL DEFAULT '',
    owner            TEXT NOT NULL DEFAULT '',
    nickname         TEXT NOT NULL DEFAULT '',
    uuid             TEXT NOT NULL DEFAULT '',
    last_seen_ms     INTEGER NOT NULL DEFAULT 0,
    died_ms          INTEGER,
    death_note       TEXT NOT NULL DEFAULT '',
    died_session_id  INTEGER,
    next_session_id  INTEGER,
    updated_at_ms    INTEGER NOT NULL,
    PRIMARY KEY (game, world_key, creature_key)
);
""".strip()


# --- memory_vectors_* / memory_fts_* —— 记忆检索的派生索引 ---
# 回想与被动想起按两路查：全文（FTS5，按两个字一组切开后的词元，BM25 排序）与向量（嵌入，余弦）。
# 正本始终是档案行、承诺、她播出的台词、游戏里程碑与阶段汇报本身；这些表只是派生物，删掉随时能
# 从正本重算。来源分四类，各一张向量表、一张全文表，行号就是来源行的主键：
# - 向量表挂外键 ON DELETE CASCADE：删单行、按句删台词、删场次、改彩排时向量随来源行一起没，
#   不用每条删除路径另写代码；model 不同（换了嵌入模型）或 text_hash 对不上（正文变了）就重算。
# - 全文表是虚表、挂不了外键，改用触发器：来源行删掉时删同号全文行；正文被改（删观众替换昵称、
#   台词被标成没播出）时连同向量一起删掉，由索引任务按新正文重算。
# 触发器只看各表本来就有的正文列：旧库升级时先跑建表语句、后跑迁移，这里不能引用迁移才加的列。
# 全文表另要打开 FTS5 自己的 secure-delete（迁移里设），否则删掉的词元会留在影子表里。

_MEMORY_INDEX_SQL = """
CREATE TABLE IF NOT EXISTS memory_vectors_archive (
    archive_line_id INTEGER PRIMARY KEY REFERENCES archive_lines(id) ON DELETE CASCADE,
    model           TEXT NOT NULL,
    dim             INTEGER NOT NULL,
    text_hash       TEXT NOT NULL,
    vector          BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_vectors_commitment (
    commitment_id   INTEGER PRIMARY KEY REFERENCES commitments(id) ON DELETE CASCADE,
    model           TEXT NOT NULL,
    dim             INTEGER NOT NULL,
    text_hash       TEXT NOT NULL,
    vector          BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_vectors_speech (
    chat_id         INTEGER PRIMARY KEY REFERENCES live_chat(id) ON DELETE CASCADE,
    model           TEXT NOT NULL,
    dim             INTEGER NOT NULL,
    text_hash       TEXT NOT NULL,
    vector          BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_vectors_event (
    event_id        INTEGER PRIMARY KEY REFERENCES game_events(id) ON DELETE CASCADE,
    model           TEXT NOT NULL,
    dim             INTEGER NOT NULL,
    text_hash       TEXT NOT NULL,
    vector          BLOB NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts_archive USING fts5(tokens);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts_commitment USING fts5(tokens);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts_speech USING fts5(tokens);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts_event USING fts5(tokens);
CREATE TRIGGER IF NOT EXISTS trg_memory_index_archive_deleted AFTER DELETE ON archive_lines BEGIN
    DELETE FROM memory_fts_archive WHERE rowid = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_memory_index_archive_edited AFTER UPDATE OF text, quote ON archive_lines BEGIN
    DELETE FROM memory_fts_archive WHERE rowid = OLD.id;
    DELETE FROM memory_vectors_archive WHERE archive_line_id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_memory_index_commitment_deleted AFTER DELETE ON commitments BEGIN
    DELETE FROM memory_fts_commitment WHERE rowid = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_memory_index_commitment_edited AFTER UPDATE OF what, quote ON commitments BEGIN
    DELETE FROM memory_fts_commitment WHERE rowid = OLD.id;
    DELETE FROM memory_vectors_commitment WHERE commitment_id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_memory_index_speech_deleted AFTER DELETE ON live_chat BEGIN
    DELETE FROM memory_fts_speech WHERE rowid = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_memory_index_speech_edited AFTER UPDATE OF content, dropped_at_ms ON live_chat BEGIN
    DELETE FROM memory_fts_speech WHERE rowid = OLD.id;
    DELETE FROM memory_vectors_speech WHERE chat_id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_memory_index_event_deleted AFTER DELETE ON game_events BEGIN
    DELETE FROM memory_fts_event WHERE rowid = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_memory_index_event_edited AFTER UPDATE OF message ON game_events BEGIN
    DELETE FROM memory_fts_event WHERE rowid = OLD.id;
    DELETE FROM memory_vectors_event WHERE event_id = OLD.id;
END;
""".strip()


# --- memory_cue_log —— 线索卡记录 ---
# 每批 Planner 前推出去的每张线索卡一行：哪一场、什么时候、什么卡、被哪几路命中（JSON 列表：观众弹幕、
# 游戏地点、游戏情境、规则、她的台词……）、各路分数与触发词（JSON），她用上的时刻由"用上即停"回填。
# 只记条目编号与分数，不记观众身份。条目被删时外键级联删掉对应记录；场次被删时整场记录一起删。
# self_triggered = 只由她自己最近几句台词命中的卡（防止她自己越说越多，副作用指标单独统计它）。

_MEMORY_CUE_LOG_SQL = """
CREATE TABLE IF NOT EXISTS memory_cue_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    pushed_at_ms    INTEGER NOT NULL,
    card_kind       TEXT NOT NULL,
    archive_line_id INTEGER REFERENCES archive_lines(id) ON DELETE CASCADE,
    commitment_id   INTEGER REFERENCES commitments(id) ON DELETE CASCADE,
    sources         TEXT NOT NULL DEFAULT '[]',
    detail          TEXT NOT NULL DEFAULT '{}',
    self_triggered  INTEGER NOT NULL DEFAULT 0,
    used_at_ms      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_memory_cue_log_session ON memory_cue_log(live_session_id, pushed_at_ms);
""".strip()


# --- memory_edits —— 记忆页人工改动记录 ---
# 运营者在记忆页的每次操作一行：哪一场的记忆、改的是档案行还是快照行还是整场、什么动作（改标题、
# 标稳定、取消稳定、放回快照、新增、删除、标已审……动作名由仓储的封闭集合把关，表上不加约束，
# 以后加动作不用重建表）、改前改后的文本、时刻。既是审计，也是整理评测的标准答案。
# 与档案同寿命：场次被删时整场记录级联删除。archive_line_id 不挂外键：行被删（删单行、按句删台词、
# 改彩排）时记录留着动作与时刻，改前改后的文本由触发器当场清空，不留被删的内容。

_MEMORY_EDITS_SQL = """
CREATE TABLE IF NOT EXISTS memory_edits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    target          TEXT NOT NULL,
    archive_line_id INTEGER,
    action          TEXT NOT NULL,
    before_text     TEXT NOT NULL DEFAULT '',
    after_text      TEXT NOT NULL DEFAULT '',
    at_ms           INTEGER NOT NULL,
    candidate_id    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_memory_edits_session ON memory_edits(live_session_id, at_ms);
CREATE INDEX IF NOT EXISTS idx_memory_edits_line ON memory_edits(archive_line_id);
CREATE TRIGGER IF NOT EXISTS trg_memory_edits_line_deleted AFTER DELETE ON archive_lines BEGIN
    UPDATE memory_edits SET before_text = '', after_text = '' WHERE archive_line_id = OLD.id;
END;
""".strip()


# --- viewer_candidates —— 观众候选 ---
# 下播整理列出的、要人批准才进记忆的两类事：viewer_defined 是观众定下、她当场接受却没复述的名字、设定、
# 梗（标题不写昵称）；claimed_missing 是她说"上次答应过""欠着的"、承诺账上却找不到的事。批准前对她
# 完全不可见：简报、线索卡、回想、检索索引都只读 archive_lines 与 commitments，不读这张表。
# 每条挂在两条台词记录上：观众那条弹幕（viewer_chat_id；她说欠着的一类只在她是回应某条弹幕时才有）
# 与她那句回应（reply_chat_id），两条都按外键级联——删观众、忘了我、按句删弹幕或台词、删场次时候选
# 随之删除。status 是 pending 待审 / approved 已批 / rejected 已驳回（驳回的留着，同一条以后不再列）；
# 批准后生成的档案行或承诺记在 archive_line_id / commitment_id，那一行再被删时置空。待审的按时间过期。
# word 是观众定下的那个名字或梗词（整理时核对过在标题与那条弹幕原文里都原样出现；她说欠着的一类为空）：
# 批准来的档案行渲染时，她那句回应里和它相同的那一段不遮成"那位观众"。

_VIEWER_CANDIDATES_SQL = """
CREATE TABLE IF NOT EXISTS viewer_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    title           TEXT NOT NULL,
    viewer_chat_id  INTEGER REFERENCES live_chat(id) ON DELETE CASCADE,
    viewer_quote    TEXT NOT NULL DEFAULT '',
    reply_chat_id   INTEGER NOT NULL REFERENCES live_chat(id) ON DELETE CASCADE,
    reply_quote     TEXT NOT NULL,
    game            TEXT,
    save_id         TEXT NOT NULL DEFAULT '',
    save_name       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at_ms   INTEGER NOT NULL,
    decided_at_ms   INTEGER,
    archive_line_id INTEGER REFERENCES archive_lines(id) ON DELETE SET NULL,
    commitment_id   INTEGER REFERENCES commitments(id) ON DELETE SET NULL,
    word            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_viewer_candidates_session ON viewer_candidates(live_session_id, status);
CREATE INDEX IF NOT EXISTS idx_viewer_candidates_viewer_chat ON viewer_candidates(viewer_chat_id);
CREATE INDEX IF NOT EXISTS idx_viewer_candidates_reply_chat ON viewer_candidates(reply_chat_id);
""".strip()


# --- game_players 等 —— 游戏里的人 ---
# 她在游戏里遇到的别的玩家按"游戏 + UUID"认：同一个 UUID 改了名仍是同一行（names 记见过的名字，
# JSON 列表），同名不同 UUID 是两行；saves 是见过他的存档（JSON 列表，每项 save_id / save_name / online_mode，
# 最后一项是那台服务器验不验账号 on / off / unknown，早先的项没有它按 unknown 读）。
# 只自动记和她发生过的互动（game_player_interactions：打招呼按场合并计数，fact 是结构化事实的 JSON）；
# 公屏聊天落 game_chat（私聊不落；direction 是 player 玩家说的 / streamer 她说的 / other 认不出是谁说的，
# 最后一种不存原话，只占一行位置），和弹幕一样受保留期管。玩家说的关于他自己的事列进 player_candidates
# 待审，挂在他那句公屏聊天和她的回应（公屏那句或播出的台词）上。viewer_player_links 是运营者确认的
# 观众与玩家对应，只用来连带删除，从不告诉她。forget_requested_at_ms 是他在游戏公屏发"忘了我"的时刻：
# 和观众同一个宽限期，到期删这位玩家与关联观众，期间他发撤回短语就清掉。玩家一行被删时，以上几张表与对他的承诺随外键一起删；
# 她回应他的台词留着，live_chat.reply_player_id 置空（这一列的索引由 v32 迁移建）。

_GAME_PLAYERS_SQL = """
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
    forget_requested_at_ms INTEGER,
    UNIQUE(game, uuid)
);
CREATE TABLE IF NOT EXISTS game_player_interactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       INTEGER NOT NULL REFERENCES game_players(id) ON DELETE CASCADE,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    save_id         TEXT NOT NULL DEFAULT '',
    save_name       TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL,
    fact            TEXT NOT NULL DEFAULT '{}',
    at_ms           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_game_player_interactions_player ON game_player_interactions(player_id, at_ms);
CREATE INDEX IF NOT EXISTS idx_game_player_interactions_session ON game_player_interactions(live_session_id, kind);
CREATE TABLE IF NOT EXISTS game_chat (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    game            TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('player', 'streamer', 'other')),
    player_id       INTEGER REFERENCES game_players(id) ON DELETE CASCADE,
    content         TEXT NOT NULL DEFAULT '',
    save_id         TEXT NOT NULL DEFAULT '',
    save_name       TEXT NOT NULL DEFAULT '',
    at_ms           INTEGER NOT NULL,
    message_id      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_game_chat_session ON game_chat(live_session_id, at_ms);
CREATE INDEX IF NOT EXISTS idx_game_chat_player ON game_chat(player_id);
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
);
CREATE INDEX IF NOT EXISTS idx_player_candidates_player ON player_candidates(player_id, status);
CREATE INDEX IF NOT EXISTS idx_player_candidates_session ON player_candidates(live_session_id);
CREATE TABLE IF NOT EXISTS viewer_player_links (
    viewer_id       INTEGER NOT NULL REFERENCES viewers(id) ON DELETE CASCADE,
    player_id       INTEGER NOT NULL REFERENCES game_players(id) ON DELETE CASCADE,
    confirmed_at_ms INTEGER NOT NULL,
    PRIMARY KEY (viewer_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_viewer_player_links_player ON viewer_player_links(player_id);
""".strip()


# --- game_social_facts —— 游戏社交事实 ---
# 一行 = 一条"当前游戏环境里人与人怎么相处"的可核对事实，与承诺账本同构：行级状态、证据可核、
# 人工锁。六类：setting 环境形态 / members 成员关系 / sharing 共享惯例 / works 进行中的事 /
# her_role 她的角色 / open_question 待弄清。
# 依据行按消息号查回：模型填它看得见的消息号（弹幕 live_chat.message_id；公屏 game_chat.message_id，
# 采集时发的随机短串，由 v38 迁移给 game_chat 补列与 (live_session_id, message_id) 索引），工具查回行号写进证据列。
# text 是一句事实陈述，不带评价词；quote 是依据的原文片段——非空时必须是对应聊天行的逐字子串，
# 空引文行只允许 her_role 与 open_question 两类（归纳类，且只有下播整理能产出）。
# evidence_chat_id → live_chat（弹幕出处）、evidence_game_chat_id → game_chat（游戏公屏出处），
# 两者都随来源行删除置空（ON DELETE SET NULL）：事实保留，证据可能查不回。
# status 只有 active（现行，注入）与 superseded（被取代，留档备查不再注入）；superseded_by 回填
# 取代它的新行号——事实过时用取代表达，不物理删除。
# human_locked = 1 表示运营者改过这一行：之后的整理与工具都不得改它或取代它，只能删。
# save_id 是这个存档（Mod world.id）的编号，空串 = 该游戏不分环境、整游戏一本；save_name 是显示名。

_GAME_SOCIAL_FACTS_SQL = """
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
);
CREATE INDEX IF NOT EXISTS idx_game_social_facts_scope ON game_social_facts(game, save_id, status);
CREATE INDEX IF NOT EXISTS idx_game_social_facts_status ON game_social_facts(status);
""".strip()


_TIMELINE_SUMMARY_SQL = """
CREATE TABLE IF NOT EXISTS timeline_summary (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    start_ms         INTEGER NOT NULL,
    end_ms           INTEGER NOT NULL,
    summary          TEXT NOT NULL
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
    reasoning_tokens   INTEGER NOT NULL DEFAULT 0,
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
# usage 拆平为多列以便 SQL 聚合（statistics/费用汇总）。正文大字段
# （request_params / reasoning_content / tool_calls / usage_raw_json /
# breakdown_json）v14 起出库到 logs/llm_requests/ 诊断文件，v17 起
# 死列 DROP（实测全库非空为零，回退价值消失）；``response_content``
# 保留——活跃写入的正文存档（详情页消费，纯文本非序列化结构）。
# ``preview_text`` 为列表页 Prompt 预览摘要（写侧从最后一条有正文的消息
# 截取，新行专用）；``image_count`` 为注入截图计数（写侧从消息图像片段
# 统计，正文出库后列表徽标唯一来源）。``profile_name`` 替换历史
# ``client_type`` 列。

_LLM_REQUESTS_SQL = """
CREATE TABLE IF NOT EXISTS llm_requests (
    request_id          TEXT PRIMARY KEY,
    timestamp_ms        INTEGER NOT NULL,
    profile_name        TEXT NOT NULL DEFAULT '',
    model_name          TEXT NOT NULL DEFAULT '',
    response_content    TEXT,
    prompt_tokens       INTEGER NOT NULL DEFAULT 0,
    completion_tokens   INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens    INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens   INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens    INTEGER NOT NULL DEFAULT 0,
    cost                REAL NOT NULL DEFAULT 0,
    success             INTEGER NOT NULL DEFAULT 1,
    error               TEXT,
    latency_ms          INTEGER NOT NULL DEFAULT 0,
    preview_text        TEXT NOT NULL DEFAULT '',
    image_count         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_llm_requests_ts ON llm_requests(timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_llm_requests_model ON llm_requests(model_name);
""".strip()


_CLIP_SEGMENTS_SQL = """
CREATE TABLE IF NOT EXISTS clip_segments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    origin          TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('ai', 'manual')),
    start_ms        INTEGER NOT NULL,
    end_ms          INTEGER NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'suggested'
                    CHECK (status IN ('suggested', 'confirmed', 'cutting', 'done', 'failed', 'dismissed')),
    output_path     TEXT NOT NULL DEFAULT '',
    error_message   TEXT NOT NULL DEFAULT '',
    created_at_ms   INTEGER NOT NULL,
    updated_at_ms   INTEGER NOT NULL,
    CHECK (end_ms >= start_ms)
);
CREATE INDEX IF NOT EXISTS idx_clip_segments_session ON clip_segments(live_session_id, start_ms);
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
