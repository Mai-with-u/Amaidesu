"""v48 → v49 迁移：live_chat 新增情绪两列（回调幂等 + 整链升级 + 仓储往返）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v49_live_chat_emotion import migrate as v49_migrate
from src.modules.storage.schema import SCHEMA_VERSION, build_schema_sql


def _seed_v48_database(path: Path) -> None:
    """用去掉 live_chat 情绪两列的 v48 形状 DDL 建库，并写入存量主播台词行。"""
    v48_ddl = build_schema_sql().replace(
        "    memory_refs      TEXT,\n    emotion          TEXT,\n    emotion_intensity REAL,\n",
        "    memory_refs      TEXT,\n",
    )
    table_block = v48_ddl.split("CREATE TABLE IF NOT EXISTS live_chat")[1].split(");")[0]
    assert "emotion" not in table_block

    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(v48_ddl)
        for version in range(1, 49):
            conn.execute("INSERT INTO schema_migrations(version, applied_at_ms) VALUES (?, ?)", (version, 1_000))
        conn.execute(
            "INSERT INTO live_sessions(id, stream_id, platform, kind, started_at_ms, ended_at_ms, updated_at_ms)"
            " VALUES (1, 'room_1', 'bilibili', 'live', 1000, 2000, 2000)"
        )
        conn.execute(
            "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, content, message_type, utterance_id)"
            " VALUES (1, 1500, 'assistant', '旧场台词没有情绪列', 'speak', 'utt_old_1')"
        )
        conn.commit()
    finally:
        conn.close()


def test_v49_migrate_callback_is_idempotent_on_empty_and_v48_tables(tmp_path: Path) -> None:
    """回调级幂等：空库不报错；v48 表连续执行两次 v49_migrate 结果一致且不丢旧数据。"""
    empty_conn = sqlite3.connect(":memory:")
    try:
        v49_migrate(empty_conn)
        v49_migrate(empty_conn)
    finally:
        empty_conn.close()

    db_file = tmp_path / "callback_v48.db"
    _seed_v48_database(db_file)
    conn = sqlite3.connect(db_file)
    try:
        v49_migrate(conn)
        v49_migrate(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(live_chat)").fetchall()}
        assert {"emotion", "emotion_intensity"} <= cols
        row = conn.execute("SELECT content, emotion FROM live_chat WHERE utterance_id='utt_old_1'").fetchone()
        assert row == ("旧场台词没有情绪列", None)  # 旧数据情绪为 NULL，不回填
    finally:
        conn.close()


async def test_v49_full_chain_upgrade_and_repo_round_trip(tmp_path: Path) -> None:
    """整链升级：v48 库初始化后推进到当前版本、两列可用；仓储写入与查询带情绪往返。"""
    db_file = tmp_path / "chain_v48.db"
    _seed_v48_database(db_file)
    database = SQLiteDatabase(db_file)
    await database.initialize()
    try:
        conn = sqlite3.connect(db_file)
        try:
            version_row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            assert version_row[0] == SCHEMA_VERSION
            cols = {row[1] for row in conn.execute("PRAGMA table_info(live_chat)").fetchall()}
            assert {"emotion", "emotion_intensity"} <= cols
        finally:
            conn.close()

        # v48 时代的旧行经仓储读出：情绪为 None（旧数据中性兜底）
        old_lines = await database.chat.list_session_speech_for_settle(live_session_id=1)
        assert len(old_lines) == 1 and old_lines[0].emotion is None

        # 新写入带情绪的台词行，仓储往返取回
        await database.chat.insert_live_chat(
            live_session_id=1,
            timestamp_ms=3000,
            sender_role="assistant",
            content="这句带着开心",
            message_type="speak",
            utterance_id="utt_new_1",
            emotion="happy",
            emotion_intensity=0.8,
        )
        lines = await database.chat.list_session_speech_for_settle(live_session_id=1)
        assert len(lines) == 2
        new_line = next(line for line in lines if line.content == "这句带着开心")
        assert new_line.emotion == "happy" and new_line.emotion_intensity == 0.8
    finally:
        await database.close()
