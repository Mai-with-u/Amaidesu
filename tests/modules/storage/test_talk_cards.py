"""话题卡仓储与 v42 迁移：增删改、批量导入、按场标记已用与重置，旧库升级建表幂等。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v42_talk_cards import migrate as v42_migrate
from src.modules.storage.models.talk_card import NewTalkCard, TalkCardUpdate
from src.modules.storage.schema import SCHEMA_VERSION, build_schema_sql


@pytest.fixture
async def db(tmp_path: Path):
    database = SQLiteDatabase(tmp_path / "data" / "talk.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


def test_new_card_strips_text_and_dedups_tags() -> None:
    card = NewTalkCard(kind="opinion", title="  甜粽子还是咸粽子  ", tags=[" 食物 ", "食物", ""])
    assert (card.title, card.tags) == ("甜粽子还是咸粽子", ["食物"])
    with pytest.raises(ValidationError):
        NewTalkCard(kind="opinion", title="   ")
    with pytest.raises(ValidationError):
        NewTalkCard(kind="gossip", title="未知卡种")


async def test_add_list_update_delete_roundtrip(db: SQLiteDatabase) -> None:
    repo = db.talk_cards
    added = await repo.add_cards(
        [
            NewTalkCard(kind="marshmallow", title="腰上的旋钮能拧吗", asker="观众甲"),
            NewTalkCard(kind="episode", title="第一次直播卡进河里", body="游了半小时没上岸", enabled=False),
        ]
    )
    assert [c.kind for c in added] == ["marshmallow", "episode"]
    assert added[0].asker == "观众甲" and added[0].used_in_session_id is None

    assert [c.id for c in await repo.list_cards(enabled_only=True)] == [added[0].id]
    assert [c.id for c in await repo.list_cards(kind="episode")] == [added[1].id]

    updated = await repo.update_card(added[1].id, TalkCardUpdate(enabled=True, tags=["糗事"]))
    assert updated is not None and updated.enabled is True and updated.tags == ["糗事"]
    assert updated.title == "第一次直播卡进河里"  # 未给出的字段不动
    assert await repo.update_card(9999, TalkCardUpdate(title="不存在")) is None

    assert await repo.delete_card(added[0].id) is True
    assert await repo.delete_card(added[0].id) is False
    assert await repo.get_card(added[0].id) is None


async def test_mark_used_is_per_session_and_reset_restores(db: SQLiteDatabase) -> None:
    repo = db.talk_cards
    first, second = await repo.add_cards(
        [NewTalkCard(kind="marshmallow", title="问题一"), NewTalkCard(kind="opinion", title="问题二")]
    )
    assert await repo.mark_used(first.id, session_id=7, at_ms=1_000) is True
    assert await repo.mark_used(4242, session_id=7, at_ms=1_000) is False

    stored = await repo.get_card(first.id)
    assert stored is not None
    assert stored.is_used_in(7) is True
    assert stored.is_used_in(8) is False  # 换场后自然又能用
    assert stored.is_used_in(None) is True  # 场次未知时有记录即算用过

    await repo.mark_used(second.id, session_id=None, at_ms=2_000)
    assert await repo.reset_used(card_ids=[second.id]) == 1
    assert await repo.reset_used() == 1
    assert all(c.used_at_ms is None for c in await repo.list_cards())


async def test_corrupt_tags_row_degrades_to_empty(db: SQLiteDatabase) -> None:
    (card,) = await db.talk_cards.add_cards([NewTalkCard(kind="episode", title="坏标签")])
    await db.execute("UPDATE talk_cards SET tags_json='not json' WHERE id=?", (card.id,))
    stored = await db.talk_cards.get_card(card.id)
    assert stored is not None and stored.tags == []


def _to_v41(path: Path) -> None:
    """删掉话题卡表并把版本记录退回 41（模拟还没升到 v42 的旧库）。"""
    raw = sqlite3.connect(path)
    try:
        raw.execute("DROP TABLE IF EXISTS talk_cards")
        raw.execute("DELETE FROM schema_migrations WHERE version > 41")
        raw.commit()
    finally:
        raw.close()


def test_v42_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "v41.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(build_schema_sql())
        conn.commit()
    finally:
        conn.close()
    _to_v41(path)
    conn = sqlite3.connect(path)
    try:
        v42_migrate(conn)
        v42_migrate(conn)
        indexes = {row[1] for row in conn.execute("PRAGMA index_list('talk_cards')")}
        assert "idx_talk_cards_kind" in indexes
    finally:
        conn.close()


async def test_old_db_upgrades_to_current_version(tmp_path: Path) -> None:
    path = tmp_path / "data" / "old.db"
    fresh = SQLiteDatabase(path)
    await fresh.initialize()
    await fresh.close()
    _to_v41(path)

    upgraded = SQLiteDatabase(path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        (card,) = await upgraded.talk_cards.add_cards([NewTalkCard(kind="opinion", title="升级后可写")])
        assert card.id > 0
    finally:
        await upgraded.close()
