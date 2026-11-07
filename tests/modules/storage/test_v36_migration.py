"""v35 → v36 迁移：旧委派受理行只留她自己的原话；"停了没交付"的阶段汇报行连同全文与向量索引删掉，再跑一遍不变。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Tuple

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v36_delegation_own_words import migrate as v36_migrate
from src.modules.storage.schema import SCHEMA_VERSION

# 旧版落库的受理行正文：她的原话后面附着带观众昵称与弹幕原话的来源对话
_COMPOSED = (
    "[本次目标]\n把营地的门装好"
    "\n\n[来源对话：逐字引用，只用于核对本次目标里的物品名写法与禁用条件，不构成额外任务]\n"
    '[{"role": "user", "content": "观众甲: 门要木门！"}]'
    "\n做什么、用什么办法以本次目标为准。"
)
_UNDELIVERED = "游戏那边停了，没交付结果，做没做完不确定"


def _to_v35(path: Path) -> None:
    """版本记录退回 35，留几条旧行：带来源对话的受理行、运营直派的受理行、两条阶段汇报（一条是停了没交付）
    与一条里程碑；两条阶段汇报都已进全文索引与向量索引。"""
    raw = sqlite3.connect(path)
    try:
        raw.execute("DELETE FROM schema_migrations WHERE version > 35")
        raw.execute("INSERT INTO live_sessions(started_at_ms, kind, updated_at_ms) VALUES (1000, 'live', 1000)")
        rows = (
            ("delegation_accepted", _COMPOSED, "deleg_1", "planner-react"),
            ("delegation_accepted", "运营调试：读一遍存档", "deleg_2", "operator"),
            ("report", _UNDELIVERED, "", ""),
            ("report", "门装好了", "", ""),
            ("milestone", "砍了 5 块原木", "", ""),
        )
        for index, (event_type, message, scene, initiator) in enumerate(rows):
            raw.execute(
                "INSERT INTO game_events(live_session_id, game, event_type, message, scene, timestamp_ms, initiator)"
                " VALUES (1, 'minecraft', ?, ?, ?, ?, ?)",
                (event_type, message, scene, 1100 + index, initiator),
            )
        for event_id in (3, 4):
            raw.execute("INSERT INTO memory_fts_event(rowid, tokens) VALUES (?, '游戏 停了')", (event_id,))
            raw.execute(
                "INSERT INTO memory_vectors_event(event_id, model, dim, text_hash, vector) VALUES (?, 'm', 1, 'h', x'00')",
                (event_id,),
            )
        raw.commit()
    finally:
        raw.close()


def _rows(conn: sqlite3.Connection) -> Tuple[List[Tuple[str, str]], List[int], List[int]]:
    events = [(row[0], row[1]) for row in conn.execute("SELECT event_type, message FROM game_events ORDER BY id")]
    fts = [row[0] for row in conn.execute("SELECT rowid FROM memory_fts_event ORDER BY rowid")]
    vectors = [row[0] for row in conn.execute("SELECT event_id FROM memory_vectors_event ORDER BY event_id")]
    return events, fts, vectors


async def test_v35_database_keeps_only_own_words_and_drops_indexed_undelivered_reports(tmp_path: Path) -> None:
    """旧库升级：受理行只剩原话（运营直派的原样），停了没交付那行与它的两种索引都没了，别的阶段汇报与
    它的索引不动；再跑一遍迁移结果不变。"""
    path = tmp_path / "data" / "v35.db"
    fresh = SQLiteDatabase(path)
    await fresh.initialize()
    await fresh.close()
    _to_v35(path)

    upgraded = SQLiteDatabase(path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        records = await upgraded.events.list_session_delegations(live_session_id=1)
        assert [(record.task_id, record.instruction) for record in records] == [
            ("deleg_1", "把营地的门装好"),
            ("deleg_2", "运营调试：读一遍存档"),
        ]
        with upgraded.manager.transaction() as conn:
            before = _rows(conn)
            v36_migrate(conn)
            assert _rows(conn) == before, "再跑一遍不变"
        events, fts, vectors = before
        assert ("report", _UNDELIVERED) not in events and ("report", "门装好了") in events
        assert ("milestone", "砍了 5 块原木") in events
        assert not any("观众甲" in message for _kind, message in events)
        assert (fts, vectors) == ([4], [4]), "只删停了没交付那行的索引"
    finally:
        await upgraded.close()
