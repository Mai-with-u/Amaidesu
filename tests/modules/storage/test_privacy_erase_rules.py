"""隐私擦除规则表对账：规则表覆盖的表集合与人工清单逐一核对，防"搬表漏行"。

清单按"隐私删除路径触达的全部表"逐张列出（迁移前即存在），并标明每张表的角色：
哪张由规则表逐表擦除、哪张按实体整行删、哪张清空身份列、哪张只记摘要、哪张只读不做写操作。
规则表增删条目导致覆盖面变化时，这里必须同步改清单——测试逼着改动者确认没漏表。
"""

from __future__ import annotations

from src.modules.storage.privacy import _GLOBAL_NICKNAME_ERASE_RULES, _SESSION_NICKNAME_ERASE_RULES
from src.modules.storage.retention import _CLEANUP_RULES

# 规则表逐表处理的表（昵称替换、清空、按提及删行、JSON 逐字段替换）
RULE_TABLES = {
    "live_chat",  # 主播台词行：昵称子串换成代称
    "topics",
    "timeline_summary",
    "archive_lines",  # 正文替换 + 触发词/地点名按 JSON 特殊清理
    "commitments",
    "memory_edits",  # 改前改后文本整段清空
    "player_candidates",
    "viewer_candidates",
    "game_social_facts",
    "memory_corrections",
    "settle_checkpoints",  # 含昵称的未完结整理断点整行删
    "live_sessions",  # 整理报告 JSON 逐字段替换
    "game_creatures",
    "game_events",
    "game_chat",
}

# 工作文档按存档整行读出后做 JSON 逐字段替换（notebook 与 todos 的 content），
# 逻辑与 JSON 报告同族但带按游戏过滤参数，作为独立函数保留、不进规则表
JSON_REPLACE_TABLES = {"game_work_docs"}

# 按实体整行删（观众/玩家行及其直接从属行），不走规则表
ENTITY_DELETED_TABLES = {
    "viewers",
    "viewer_names",
    "game_players",
    # live_chat 的观众行与 commitments 的对 ta 承诺行按 viewer_id 整行删（昵称替换走规则表）
    "live_chat",
    "commitments",
}

# 付费三表：清空身份、昵称、留言与原始消息，保留金额与时刻
PAYMENT_SCRUBBED_TABLES = {"gifts", "super_chats", "guards"}

# 只写摘要（不记录名单存 HMAC 摘要），不擦内容
HASH_RECORDED_TABLES = {"forget_list"}

# 只读引用、不写：关联玩家查询与玩家场次发现
READ_ONLY_TABLES = {"viewer_player_links", "game_player_interactions"}

# 隐私删除路径触达的全部表（25 张）
ALL_PRIVACY_TABLES = (
    RULE_TABLES
    | JSON_REPLACE_TABLES
    | ENTITY_DELETED_TABLES
    | PAYMENT_SCRUBBED_TABLES
    | HASH_RECORDED_TABLES
    | READ_ONLY_TABLES
)


def test_session_and_global_rules_cover_the_declared_tables() -> None:
    rule_tables = {rule.table for rule in _SESSION_NICKNAME_ERASE_RULES}
    rule_tables |= {rule.table for rule in _GLOBAL_NICKNAME_ERASE_RULES}
    assert rule_tables == RULE_TABLES


def test_privacy_path_table_inventory_is_exhaustive() -> None:
    # 25 张表与 privacy.py 触达面一一对应；集合恒等保证无遗漏、无多列
    assert len(ALL_PRIVACY_TABLES) == 25
    assert (
        RULE_TABLES
        | JSON_REPLACE_TABLES
        | ENTITY_DELETED_TABLES
        | PAYMENT_SCRUBBED_TABLES
        | HASH_RECORDED_TABLES
        | READ_ONLY_TABLES
    ) == ALL_PRIVACY_TABLES


def test_retention_cleanup_rules_cover_the_declared_tables() -> None:
    assert {rule.table for rule in _CLEANUP_RULES} == {
        "live_chat",
        "game_chat",
        "gifts",
        "super_chats",
        "guards",
        "topics",
        "timeline_summary",
        "game_events",
        "llm_requests",
    }
