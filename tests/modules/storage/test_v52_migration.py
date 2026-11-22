"""v51 → v52 存储迁移与游戏玩家常用称呼（aliases）仓储、清洗、格式化与权限隔离单元测试。

严格只用 tmp_path / :memory:，绝不触碰真实库 data/amaidesu.db。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.game_player_ledger import GamePlayerLedger
from src.modules.storage.migrations.v52_game_player_aliases import migrate as v52_migrate
from src.modules.storage.models.game_players import (
    MAX_PLAYER_ALIAS_CHARS,
    MAX_PLAYER_ALIASES,
    format_player_display,
    normalize_player_aliases,
)
from src.modules.storage.repos.memory_review import MemoryReviewError
from src.modules.storage.schema import SCHEMA_VERSION

# v51 形态的 game_players 建表语句（尚无 aliases 列）
_V51_GAME_PLAYERS_SQL = """
CREATE TABLE game_players (
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
)
""".strip()


def test_v52_migration_callback_adds_aliases_column_and_is_idempotent() -> None:
    """回调级验证：无 aliases 列的旧表补列且存量行默认 '[]'；连续跑两次严格幂等；缺表静默安全。"""
    empty_conn = sqlite3.connect(":memory:")
    try:
        # 表尚未创建时不报错
        v52_migrate(empty_conn)
    finally:
        empty_conn.close()

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(
            """
            CREATE TABLE live_sessions (id INTEGER PRIMARY KEY);
            INSERT INTO live_sessions(id) VALUES (1);
            """
        )
        conn.execute(_V51_GAME_PLAYERS_SQL)
        conn.execute(
            "INSERT INTO game_players(id, game, uuid, name, names, saves, first_session_id, first_seen_ms,"
            " last_session_id, last_seen_ms) VALUES"
            " (1, 'minecraft', 'uuid-alpha', 'AlphaPlayer', '[\"AlphaPlayer\"]', '[]', 1, 1000, 1, 2000)"
        )

        # 连续执行两次：第一次补列，第二次通过 column_exists 直接跳过
        v52_migrate(conn)
        columns_after_first = [row[1] for row in conn.execute("PRAGMA table_info(game_players)")]
        v52_migrate(conn)
        columns_after_second = [row[1] for row in conn.execute("PRAGMA table_info(game_players)")]
        assert "aliases" in columns_after_first
        assert columns_after_second == columns_after_first

        row = conn.execute("SELECT id, uuid, name, aliases FROM game_players WHERE id=1").fetchone()
        assert row == (1, "uuid-alpha", "AlphaPlayer", "[]")
    finally:
        conn.close()


async def test_full_chain_upgrade_from_v51_to_v52_preserves_players_and_supports_aliases(tmp_path: Path) -> None:
    """整链级验证：构造真实 v51 旧表形状并回退版本号，重开数据库自动推进到当前版本且二次重开幂等。"""
    db_path = tmp_path / "upgrade_v52.db"

    # 先初始化完整库并写入一位存量玩家
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        session = await db.sessions.insert_live_session(started_at_ms=1_000, kind="live")
        player = await db.game_players.meet_player(
            game="minecraft",
            uuid="uuid-alpha",
            name="AlphaPlayer",
            live_session_id=session,
            save_id="w1",
            save_name="测试存档",
            at_ms=1_500,
        )
        player_id = player.id
    finally:
        await db.close()

    # 模拟真实 v51 旧库：重建无 aliases 列的 game_players 表并把版本记录回退至 51
    raw = sqlite3.connect(db_path)
    try:
        raw.execute("PRAGMA foreign_keys = OFF")
        raw.execute("ALTER TABLE game_players RENAME TO game_players_v52_backup")
        raw.execute(_V51_GAME_PLAYERS_SQL)
        raw.execute(
            "INSERT INTO game_players(id, game, uuid, name, names, saves, first_session_id, first_seen_ms,"
            " last_session_id, last_seen_ms, forget_requested_at_ms)"
            " SELECT id, game, uuid, name, names, saves, first_session_id, first_seen_ms,"
            " last_session_id, last_seen_ms, forget_requested_at_ms FROM game_players_v52_backup"
        )
        raw.execute("DROP TABLE game_players_v52_backup")
        raw.execute("DELETE FROM schema_migrations WHERE version > 51")
        raw.commit()
    finally:
        raw.close()

    # 重开触发 51 → 52 真实升级链
    upgraded = SQLiteDatabase(db_path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        loaded = await upgraded.game_players.get_player(player_id=player_id)
        assert loaded is not None
        assert (loaded.name, loaded.aliases) == ("AlphaPlayer", [])

        # 升级后运营者可正常写入常用称呼并持久化读回
        updated = await upgraded.game_players.set_aliases(
            player_id=player_id,
            aliases=[" 小阿尔 ", "AlphaPlayer", "小阿尔", "阿法"],
        )
        assert updated.aliases == ["小阿尔", "阿法"]
        assert updated.display_label() == "AlphaPlayer（常叫：小阿尔、阿法）"
    finally:
        await upgraded.close()

    backups = list((db_path.parent / "backups").glob("*-pre-v*.db"))
    assert len(backups) == 1, "首次升级前应生成一份迁移前备份"

    # 第二次重开：版本已最新，数据完好，不再重复备份
    reopened = SQLiteDatabase(db_path)
    await reopened.initialize()
    try:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
        again = await reopened.game_players.get_player(player_id=player_id)
        assert again is not None and again.aliases == ["小阿尔", "阿法"]
    finally:
        await reopened.close()
    assert len(list((db_path.parent / "backups").glob("*-pre-v*.db"))) == 1


@pytest.mark.parametrize(
    ("raw_aliases", "display_name", "expected_aliases", "expected_display"),
    [
        ([], "AlphaPlayer", [], "AlphaPlayer"),
        (["  ", "AlphaPlayer"], "AlphaPlayer", [], "AlphaPlayer"),
        (
            [" 小阿尔 ", "阿法", "小阿尔", "AlphaPlayer", "小  阿"],
            "AlphaPlayer",
            ["小阿尔", "阿法", "小 阿"],
            "AlphaPlayer（常叫：小阿尔、阿法、小 阿）",
        ),
    ],
)
def test_normalize_and_format_player_display(
    raw_aliases: list[str],
    display_name: str,
    expected_aliases: list[str],
    expected_display: str,
) -> None:
    """纯函数清洗与格式化：去空白、去重、保序、忽略与现名完全相同项，无别名返回原名。"""
    assert normalize_player_aliases(raw_aliases, display_name=display_name) == expected_aliases
    assert format_player_display(display_name, raw_aliases) == expected_display


async def test_set_aliases_validation_isolation_and_ledger_entry(tmp_path: Path) -> None:
    """仓储与落库器更新别名：校验边界、不触碰 viewer_player_links、改名不丢别名。"""
    db = SQLiteDatabase(tmp_path / "aliases_repo.db")
    await db.initialize()
    try:
        session = await db.sessions.insert_live_session(started_at_ms=1_000, kind="live")
        alpha = await db.game_players.meet_player(
            game="minecraft", uuid="uuid-alpha", name="AlphaPlayer", live_session_id=session, at_ms=1_100
        )
        bus = EventBus()
        ledger = GamePlayerLedger(bus, db.game_players)

        updated = await ledger.update_aliases(player_id=alpha.id, aliases=[" 小阿尔 ", "小贝替身", "AlphaPlayer"])
        assert updated.aliases == ["小阿尔", "小贝替身"]
        # 绝不触碰隐私隔离表 viewer_player_links
        assert await db.game_players.list_links() == []

        # 玩家改名后保留既有常用称呼，若现名恰好等于某常用称呼则读取时自动忽略
        renamed = await db.game_players.meet_player(
            game="minecraft", uuid="uuid-alpha", name="小阿尔", live_session_id=session, at_ms=1_200
        )
        assert renamed.aliases == ["小贝替身"]

        # 校验边界：不存在、超条数、超字数
        with pytest.raises(MemoryReviewError) as missing:
            await db.game_players.set_aliases(player_id=99999, aliases=["小阿尔"])
        assert missing.value.kind == "not_found"

        with pytest.raises(MemoryReviewError) as too_many:
            await db.game_players.set_aliases(
                player_id=alpha.id,
                aliases=[f"称呼{i}" for i in range(MAX_PLAYER_ALIASES + 1)],
            )
        assert too_many.value.kind == "invalid"

        with pytest.raises(MemoryReviewError) as too_long:
            await db.game_players.set_aliases(
                player_id=alpha.id,
                aliases=["长" * (MAX_PLAYER_ALIAS_CHARS + 1)],
            )
        assert too_long.value.kind == "invalid"
    finally:
        await db.close()
