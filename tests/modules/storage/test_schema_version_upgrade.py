"""Schema 版本机制：新库版本链、旧库单调升级、私有表废除，以及 v6/v9/v11/v12/v14 迁移语义"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator


import pytest

from src.modules.storage.database import SchemaVersionDowngradeError, SQLiteDatabase
from src.modules.storage.migrations import SCHEMA_MIGRATIONS
from src.modules.storage.migrations.v13_llm_context_breakdown import migrate as v13_migrate
from src.modules.storage.migrations.v14_llm_request_preview_column import migrate as v14_migrate
from src.modules.storage.schema import _GIFTS_SQL, SCHEMA_VERSION, build_schema_sql

# 真实 v1 形状的冻结建库 DDL（引入 SCHEMA_VERSION 的第一次提交原文），用于整链升级测试
_V1_SCHEMA_PATH = Path(__file__).parent / "fixtures" / "v1_schema.sql"


@asynccontextmanager
async def _opened(path: Path, **kwargs) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(path, **kwargs)
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def _rollback_to(path: Path, version: int, *statements: str) -> None:
    """建当前库后把版本记录回退到 version，并执行造旧形状的语句。"""
    async with _opened(path) as old:
        await old.execute(f"DELETE FROM schema_migrations WHERE version > {version}")
        for sql in statements:
            await old.execute(sql)
        assert await old.get_schema_version() == version


async def test_fresh_db_records_full_version_chain_without_private_tables(db_path: Path) -> None:
    # 严格 +1 版本纪律：1..SCHEMA_VERSION 每版都在迁移注册表内
    assert set(SCHEMA_MIGRATIONS) == set(range(1, SCHEMA_VERSION + 1))
    async with _opened(db_path) as store:
        rows = await store.execute("SELECT version FROM schema_migrations ORDER BY version")
        assert [r["version"] for r in rows] == list(range(1, SCHEMA_VERSION + 1))
        # 私有表机制已废除：所有表统一进入缺一即拒启闸门
        tables = await store.execute("SELECT name FROM sqlite_master WHERE type='table'")
        assert [r["name"] for r in tables if str(r["name"]).startswith("_")] == []
        await store.assert_schema_ready()
        # 当前形状（v25）：旧记忆的两张表与场次备忘录列都已退役
        for retired in ("session_recaps", "viewer_profiles"):
            assert await store.table_exists(retired) is False
        session_columns = {r["name"] for r in await store.execute("PRAGMA table_info(live_sessions)")}
        assert "notebook_text" not in session_columns


def test_build_schema_sql_is_current_shape() -> None:
    sql = build_schema_sql()
    for marker in (
        "CREATE TABLE IF NOT EXISTS guards",
        "UNIQUE(platform, account_id)",
        "CREATE TABLE IF NOT EXISTS viewer_names",
        "preview_text",
        # v17 清理后不再出现的遗留列（死列 / 出库列）
        "rundown_segments",
    ):
        assert marker in sql
    # 旧记忆（v25 退役）不再出现在建表 DDL 里：表与场次备忘录列都已删除
    for gone in (
        "breakdown_json",
        "usage_raw_json",
        "segments_json",
        "tool_result",
        "CREATE TABLE IF NOT EXISTS viewer_profiles",
        "CREATE TABLE IF NOT EXISTS session_recaps",
        "notebook_text",
    ):
        assert gone not in sql
    # gifts 已改名 quantity（viewers 的 gift_count 属不同语义）；扁平事实私有表与观众事实中间表均已移除
    assert "gift_count" not in _GIFTS_SQL
    assert "_memory_facts" not in sql and "viewer_facts" not in sql


async def test_newer_schema_version_is_rejected(db_path: Path) -> None:
    """库版本高于代码期望时硬错：旧代码继续用会按旧建库语句重建已删的表。"""
    async with _opened(db_path) as store:
        await store.execute(
            "INSERT INTO schema_migrations(version, applied_at_ms) VALUES (?, ?)",
            (SCHEMA_VERSION + 1, 0),
        )

    with pytest.raises(SchemaVersionDowngradeError):
        async with _opened(db_path):
            pass


async def test_old_db_upgrades_monotonically_keeping_data(db_path: Path) -> None:
    async with _opened(db_path) as old:
        await old.chat.insert_live_chat(
            live_session_id=7, timestamp_ms=1_000, sender_role="viewer", content="升级前的话", message_type="danmaku"
        )
    await _rollback_to(db_path, 1)

    async with _opened(db_path) as reopened:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
        assert [r["content"] for r in await reopened.execute("SELECT content FROM live_chat")] == ["升级前的话"]


def _seed_real_v1_database(path: Path) -> None:
    """用冻结的 v1 DDL 建库并放几行旧数据（版本表只记到 1）。"""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_V1_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_migrations(version, applied_at_ms) VALUES (1, 0)")
        conn.execute(
            "INSERT INTO live_sessions(id, stream_id, platform, started_at_ms, ended_at_ms, title, updated_at_ms)"
            " VALUES (1, 'room', 'bilibili', 1000, 2000, '老场次', 2000)"
        )
        conn.execute(
            "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, sender_id, sender_name,"
            " content, message_type, simulated)"
            " VALUES (1, 1100, 'viewer', '12345', '老观众', '升级前的观众弹幕', 'danmaku', 0)"
        )
        conn.execute(
            "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, content, message_type, simulated)"
            " VALUES (1, 1200, 'assistant', '升级前的主播台词', 'danmaku', 0)"
        )
        conn.execute(
            "INSERT INTO viewers(user_id, user_name, message_count, last_active_ms) VALUES ('12345', '老观众', 3, 1500)"
        )
        conn.execute(
            "INSERT INTO viewers(user_id, user_name, message_count, last_active_ms) VALUES ('67890', '另一位', 1, 1400)"
        )
        conn.execute(
            "INSERT INTO gifts(live_session_id, timestamp_ms, user_id, user_name, gift_name, gift_count)"
            " VALUES (1, 1300, '12345', '老观众', '辣条', 2)"
        )
        conn.commit()
    finally:
        conn.close()


async def test_real_v1_shape_upgrades_to_current_version_keeping_data(db_path: Path) -> None:
    """从真实 v1 形状（冻结 DDL，11 张初始表）一路升到当前版本。

    与"最新库回退版本号"的模拟不同：这里旧表真的只有 v1 那几列，整条 2..SCHEMA_VERSION
    迁移链都会在真实旧形状上执行；断言数据保留、观众身份按 v18 回填、v23 按历史弹幕回填
    来访计数、v6 换掉议程表、v25 退役旧记忆表，以及迁移前备份落在同目录。
    """
    _seed_real_v1_database(db_path)

    async with _opened(db_path) as store:
        assert await store.get_schema_version() == SCHEMA_VERSION
        await store.assert_schema_ready()
        for gone in ("agenda_plan", "agenda_runtime", "session_recaps", "viewer_profiles"):
            assert await store.table_exists(gone) is False
        chats = await store.execute("SELECT content, viewer_id, id_kind FROM live_chat ORDER BY id")
        assert [row["content"] for row in chats] == ["升级前的观众弹幕", "升级前的主播台词"]
        assert (chats[0]["viewer_id"], chats[0]["id_kind"]) == (1, "account")
        viewers = await store.execute("SELECT platform, account_id, user_name, sessions_count FROM viewers ORDER BY id")
        assert [(row["platform"], row["account_id"]) for row in viewers] == [
            ("bilibili", "12345"),
            ("bilibili", "67890"),
        ]
        # v23：按历史观众弹幕回填正式场来访计数（场次 1 是 kind=live）
        assert viewers[0]["sessions_count"] == 1
        (gift,) = await store.execute("SELECT user_id, viewer_id FROM gifts")
        assert (gift["user_id"], gift["viewer_id"]) == ("12345", 1)

    backups = list((db_path.parent / "backups").glob("*-pre-v*.db"))
    assert len(backups) == 1, "整链升级前应生成一份迁移前备份"

    # 再开一次：已是当前版本，不重跑迁移、不再生成备份
    async with _opened(db_path) as reopened:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
    assert len(list((db_path.parent / "backups").glob("*-pre-v*.db"))) == 1


async def test_v6_to_v7_drops_memory_profiles_idempotently(db_path: Path) -> None:
    await _rollback_to(
        db_path,
        6,
        "CREATE TABLE _memory_profiles (person_id TEXT PRIMARY KEY, updated_at_ms INTEGER NOT NULL)",
        "INSERT INTO _memory_profiles VALUES ('u_1', 123)",
    )
    for _ in range(2):
        async with _opened(db_path) as reopened:
            assert await reopened.get_schema_version() == SCHEMA_VERSION
            assert await reopened.table_exists("_memory_profiles") is False


def _make_v9_schema(conn: sqlite3.Connection) -> None:
    """构造 v9 形状的存量库（付费明细无金额列、单键 viewers、_memory_facts 在位）。"""
    conn.executescript(
        """
        CREATE TABLE live_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            live_session_id INTEGER NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            sender_role TEXT NOT NULL,
            sender_id TEXT,
            sender_name TEXT,
            content TEXT NOT NULL,
            message_type TEXT NOT NULL,
            message_id TEXT,
            reply_to_message_id TEXT,
            tool_result TEXT,
            simulated INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            live_session_id INTEGER NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            gift_name TEXT NOT NULL,
            gift_count INTEGER NOT NULL,
            simulated INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE super_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            live_session_id INTEGER NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            amount REAL NOT NULL,
            message TEXT NOT NULL,
            simulated INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE viewers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL UNIQUE,
            user_name TEXT NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            gift_count INTEGER NOT NULL DEFAULT 0,
            replied_count INTEGER NOT NULL DEFAULT 0,
            interaction_count INTEGER NOT NULL DEFAULT 0,
            last_active_ms INTEGER NOT NULL
        );
        CREATE TABLE _memory_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            importance INTEGER NOT NULL DEFAULT 0,
            timestamp_ms INTEGER NOT NULL
        );
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at_ms INTEGER NOT NULL
        );
        INSERT INTO schema_migrations(version, applied_at_ms) VALUES (1, 0);
        """
    )
    # v2..v9 之间无数据变换的其他版本回调也需跑：直接借注册表从 1 推进到 9 更真实——
    # 这里只造"v9 形状"的表结构，版本记录手动置 9，v10 回调作用于该形状。
    conn.execute("DELETE FROM schema_migrations")
    conn.execute("INSERT INTO schema_migrations(version, applied_at_ms) VALUES (9, 0)")
    # 存量业务数据
    conn.execute(
        "INSERT INTO gifts(live_session_id, timestamp_ms, user_id, user_name, gift_name, gift_count, simulated)"
        " VALUES (1, 100, 'u_old', '老观众', '辣条', 3, 0)"
    )
    conn.execute(
        "INSERT INTO super_chats(live_session_id, timestamp_ms, user_id, user_name, amount, message, simulated)"
        " VALUES (1, 200, 'u_old', '老观众', 50.0, '加油', 0)"
    )
    conn.execute(
        "INSERT INTO viewers(user_id, user_name, message_count, interaction_count, last_active_ms)"
        " VALUES ('u_old', '老观众', 5, 7, 300)"
    )
    conn.execute(
        "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, content, message_type, simulated)"
        " VALUES (1, 150, 'viewer', '存量弹幕', 'danmaku', 0)"
    )
    conn.execute("INSERT INTO _memory_facts(text, timestamp_ms) VALUES ('旧事实', 100)")


async def test_v9_to_v10_migration_semantics(db_path: Path) -> None:
    """v9 存量库 → v10：付费明细补全、金额统一金瓜子、身份复合键、扁平事实废弃、画像表就位。"""
    conn = sqlite3.connect(db_path)
    _make_v9_schema(conn)
    conn.commit()
    conn.close()

    async with _opened(db_path) as store:
        assert await store.get_schema_version() == SCHEMA_VERSION
        gift = (await store.execute("SELECT * FROM gifts"))[0]
        assert (gift["quantity"], gift["platform"], gift["currency"]) == (3, "bilibili", "")
        # 金额元 → 金瓜子（50.0 元 → 50000）
        sc = (await store.execute("SELECT * FROM super_chats"))[0]
        assert (sc["total_price"], sc["currency"], sc["platform"]) == (50_000, "bilibili_gold_coin", "bilibili")
        viewer = (await store.execute("SELECT * FROM viewers"))[0]
        assert (viewer["platform"], viewer["paid_count"], viewer["paid_amount"]) == ("bilibili", 0, 0)
        unique_cols = set()
        for idx in await store.execute("PRAGMA index_list(viewers)"):
            if idx["unique"]:
                cols = await store.execute(f"PRAGMA index_info({idx['name']})")
                unique_cols.add(tuple(c["name"] for c in cols))
        assert ("platform", "account_id") in unique_cols
        # 存量明细按旧身份回填到观众：u_old 不是纯数字也不是 UUID，按账号 ID 保留
        assert (viewer["account_id"], gift["viewer_id"], gift["id_kind"]) == ("u_old", viewer["id"], "account")
        assert (await store.execute("SELECT * FROM live_chat"))[0]["platform"] == "bilibili"
        assert await store.table_exists("_memory_facts") is False
        assert await store.table_exists("viewer_facts") is False  # 随 v15 退役：旧库升级后不应再有该表
        # 一路升到当前版本后，旧记忆的画像与回顾表也已被 v25 清掉
        for table in ("viewer_profiles", "session_recaps"):
            assert await store.table_exists(table) is False
        assert await store.table_exists("guards") is True
        session_columns = {r["name"] for r in await store.execute("PRAGMA table_info(live_sessions)")}
        assert "notebook_text" not in session_columns

    async with _opened(db_path) as again:
        assert [r["total_price"] for r in await again.execute("SELECT total_price FROM super_chats")] == [50_000]


_V12_LLM_REQUESTS_SQL = """
CREATE TABLE llm_requests (
    request_id          TEXT PRIMARY KEY,
    timestamp_ms        INTEGER NOT NULL,
    profile_name        TEXT NOT NULL DEFAULT '',
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
    reasoning_tokens    INTEGER NOT NULL DEFAULT 0,
    cost                REAL NOT NULL DEFAULT 0,
    success             INTEGER NOT NULL DEFAULT 1,
    error               TEXT,
    latency_ms          INTEGER NOT NULL DEFAULT 0,
    usage_raw_json      TEXT
)
""".strip()


def test_v12_to_v13_adds_breakdown_column() -> None:
    """v13 钩子给 v12 形状旧表补 breakdown_json 列（幂等）；该列随后被 v17 清理退役。"""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_V12_LLM_REQUESTS_SQL)
    conn.execute(
        "INSERT INTO llm_requests(request_id, timestamp_ms, profile_name, model_name, prompt_tokens)"
        " VALUES ('r_old', 1000, 'replyer', 'model-a', 100)"
    )
    for _ in range(2):
        v13_migrate(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(llm_requests)")}
        assert "breakdown_json" in cols
        row = conn.execute("SELECT request_id, breakdown_json FROM llm_requests").fetchone()
        assert row == ("r_old", None)
    conn.close()


def test_v14_adds_preview_column_idempotently() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        _V12_LLM_REQUESTS_SQL.replace("usage_raw_json      TEXT", "usage_raw_json TEXT, breakdown_json TEXT")
    )
    conn.execute("INSERT INTO llm_requests (request_id, timestamp_ms) VALUES ('req-old', 1000)")
    for _ in range(2):
        v14_migrate(conn)
        assert conn.execute("SELECT preview_text FROM llm_requests").fetchall() == [("",)]


async def test_v11_drops_sim_window_column_keeping_personas(db_path: Path) -> None:
    async with _opened(db_path) as store:
        await store.sim.insert_sim_persona(
            user_id="viewer",
            user_nickname="老观众",
            role="veteran",
            personality="耐心",
            speaking_style="简洁",
            messages_generated=23,
        )
    # 让 v11 在重开时真正重跑一次（不受 SCHEMA_VERSION 后续推进影响）
    await _rollback_to(
        db_path,
        10,
        "ALTER TABLE sim_personas ADD COLUMN context_window_size INTEGER",
        "UPDATE sim_personas SET context_window_size=5",
    )
    for _ in range(2):
        async with _opened(db_path) as reopened:
            columns = await reopened.execute("PRAGMA table_info(sim_personas)")
            assert "context_window_size" not in {row["name"] for row in columns}
            rows = await reopened.sim.list_sim_personas()
            assert [(r.user_nickname, r.messages_generated) for r in rows] == [("老观众", 23)]
