"""v17 迁移单测：流程单模板拆行 / 四新表 / 七处死列清理。

覆盖（重点验证**存量旧库实际写回落盘**，不只测新库建表）：
- 新库 DDL 即最新形态：四新表在位、七处遗留列不存在，SCHEMA_VERSION = 17
- v16 形状旧库升级：``segments_json`` 行拆正确、key_points 拼进
  ``task_description``（空描述直写 / 非空描述另起一行"要点："）、旧列消失、
  ``llm_requests`` / ``live_chat`` / ``timeline_summary`` 死列 DROP
- 回调幂等：重复执行不报错、数据不重复
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v17_rundown_rows_and_column_cleanup import migrate as v17_migrate
from src.modules.storage.schema import SCHEMA_VERSION


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="v17-migration-"))
    yield td
    shutil.rmtree(td, ignore_errors=True)


# v16 形状的存量库（含待迁移数据：segments_json、五出库列、tool_result、tags）
_V16_SCHEMA = """
CREATE TABLE rundowns (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    segments_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
CREATE TABLE rundown_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rundown_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    seg_id TEXT NOT NULL,
    title TEXT NOT NULL,
    task_description TEXT NOT NULL,
    expected_ms INTEGER NOT NULL,
    min_duration_ms INTEGER,
    notes TEXT,
    UNIQUE(rundown_id, seg_id)
);
CREATE TABLE llm_requests (
    request_id TEXT PRIMARY KEY,
    timestamp_ms INTEGER NOT NULL,
    profile_name TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL DEFAULT '',
    request_params TEXT,
    response_content TEXT,
    reasoning_content TEXT,
    tool_calls TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    usage_raw_json TEXT,
    breakdown_json TEXT,
    preview_text TEXT NOT NULL DEFAULT '',
    image_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE live_chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    content TEXT NOT NULL,
    message_type TEXT NOT NULL,
    tool_result TEXT,
    simulated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE timeline_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    summary TEXT NOT NULL,
    tags TEXT
);
""".strip()

_SEGMENTS_JSON = json.dumps(
    [
        {
            "id": "opening",
            "title": "开场问候",
            "task_description": "向观众打招呼",
            "key_points": ["问好", "说明首播"],
            "expected_ms": 180_000,
            "min_duration_ms": 60_000,
            "notes": "参考开场白",
        },
        {
            "id": "chat",
            "title": "互动闲聊",
            "task_description": "",
            "key_points": ["回应弹幕", "抛出话题"],
            "expected_ms": 600_000,
            "min_duration_ms": None,
            "notes": None,
        },
        {
            "id": "plain",
            "title": "无要点环节",
            "task_description": "自由发挥",
            "key_points": [],
            "expected_ms": 300_000,
            "min_duration_ms": None,
            "notes": None,
        },
    ],
    ensure_ascii=False,
)


def _make_v16_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(_V16_SCHEMA)
    conn.execute(
        "INSERT INTO rundowns (id, title, segments_json, created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?)",
        ("rd_old", "存量流程单", _SEGMENTS_JSON, 1, 2),
    )
    conn.execute(
        "INSERT INTO llm_requests (request_id, timestamp_ms, model_name, response_content)"
        " VALUES ('req-old', 1, 'm1', '正文存档保留')"
    )
    conn.execute(
        "INSERT INTO live_chat (live_session_id, timestamp_ms, content, message_type) VALUES (1, 1, '弹幕', 'danmaku')"
    )
    conn.execute(
        "INSERT INTO timeline_summary (live_session_id, start_ms, end_ms, summary) VALUES (1, 1, 2, '摘要')"
    )
    conn.commit()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_new_database_has_v17_shape(temp_dir: Path) -> None:
    """新库：DDL 即最新形态（四新表在位、七处死列不存在）。"""
    assert SCHEMA_VERSION == 17
    db = SQLiteDatabase(temp_dir / "fresh.db")
    asyncio.run(db.initialize())
    try:
        conn = sqlite3.connect(temp_dir / "fresh.db")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("rundown_segments", "rundown_instances", "rundown_instance_segments", "rundown_transitions"):
            assert table in tables
        assert "request_params" not in _columns(conn, "llm_requests")
        assert "breakdown_json" not in _columns(conn, "llm_requests")
        assert "segments_json" not in _columns(conn, "rundowns")
        assert "tool_result" not in _columns(conn, "live_chat")
        assert "tags" not in _columns(conn, "timeline_summary")
        conn.close()
    finally:
        asyncio.run(db.close())


def test_v16_database_upgrade_unpacks_rows_and_drops_dead_columns(temp_dir: Path) -> None:
    """v16 形状旧库升级：行拆正确、key_points 并入描述、七处列消失、存量数据不丢。"""
    db_path = temp_dir / "old.db"
    _make_v16_db(db_path)

    db = SQLiteDatabase(db_path)
    asyncio.run(db.initialize())
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # 模板拆行：按 seq 定序、标量列齐
        rows = conn.execute(
            "SELECT seq, seg_id, title, task_description, expected_ms, min_duration_ms, notes"
            " FROM rundown_segments WHERE rundown_id='rd_old' ORDER BY seq"
        ).fetchall()
        assert [(r["seq"], r["seg_id"]) for r in rows] == [(0, "opening"), (1, "chat"), (2, "plain")]
        first, second, third = rows
        # 非空描述：要点另起一行"要点：a、b、c"
        assert first["task_description"] == "向观众打招呼\n要点：问好、说明首播"
        # 空描述：要点直接写入
        assert second["task_description"] == "要点：回应弹幕、抛出话题"
        # 无要点：原样保留
        assert third["task_description"] == "自由发挥"
        assert (first["expected_ms"], first["min_duration_ms"], first["notes"]) == (180_000, 60_000, "参考开场白")

        # 元信息保留、segments_json 列消失
        assert "segments_json" not in _columns(conn, "rundowns")
        header = conn.execute("SELECT id, title FROM rundowns").fetchone()
        assert (header["id"], header["title"]) == ("rd_old", "存量流程单")

        # llm_requests 五出库列消失，正文存档列与存量行保留
        for gone in ("request_params", "reasoning_content", "tool_calls", "usage_raw_json", "breakdown_json"):
            assert gone not in _columns(conn, "llm_requests")
        req = conn.execute("SELECT response_content, preview_text FROM llm_requests").fetchone()
        assert (req["response_content"], req["preview_text"]) == ("正文存档保留", "")

        # live_chat.tool_result / timeline_summary.tags 消失，存量行保留
        assert "tool_result" not in _columns(conn, "live_chat")
        assert conn.execute("SELECT COUNT(*) FROM live_chat").fetchone()[0] == 1
        assert "tags" not in _columns(conn, "timeline_summary")
        assert conn.execute("SELECT summary FROM timeline_summary").fetchone()[0] == "摘要"

        # 四新表存在且为空（接线由后续切片承担）
        for table in ("rundown_instances", "rundown_instance_segments", "rundown_transitions"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        conn.close()
    finally:
        asyncio.run(db.close())


def test_v17_migration_is_idempotent_on_v16_database(temp_dir: Path) -> None:
    """回调幂等：对已升级库重复执行不报错、不重复插行。"""
    db_path = temp_dir / "idem.db"
    _make_v16_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at_ms INTEGER NOT NULL);"
        "INSERT INTO schema_migrations VALUES (16, 0);"
    )
    for _ in range(3):
        v17_migrate(conn)
    assert conn.execute("SELECT COUNT(*) FROM rundown_segments").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM rundown_transitions").fetchone()[0] == 0
    conn.close()


def test_dirty_segments_json_row_is_skipped_not_fatal(temp_dir: Path) -> None:
    """历史脏行（segments_json 无法解析）跳过插行，不让迁移整体失败。"""
    db_path = temp_dir / "dirty.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_V16_SCHEMA)
    conn.execute(
        "INSERT INTO rundowns (id, title, segments_json, created_at_ms, updated_at_ms) VALUES ('rd_bad', '坏行', '{not json', 1, 2)"
    )
    conn.commit()
    v17_migrate(conn)
    assert conn.execute("SELECT COUNT(*) FROM rundown_segments").fetchone()[0] == 0
    assert "segments_json" not in _columns(conn, "rundowns")
    conn.close()
