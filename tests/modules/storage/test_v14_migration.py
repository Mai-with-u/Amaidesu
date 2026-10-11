"""v14 迁移单测：``llm_requests`` 补列表预览摘要列（preview_text）。

覆盖：
- 新库建表即含 preview_text 列（默认空串）
- v13 形状旧库升级：ALTER 补列、存量行默认空串、版本推进、重复执行幂等
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.schema import SCHEMA_VERSION
from src.modules.storage.migrations.v14_llm_request_preview_column import migrate as v14_migrate

# v13 形状的 llm_requests DDL（无 preview_text 列）
_V13_LLM_REQUESTS_SQL = """
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
    usage_raw_json      TEXT,
    breakdown_json      TEXT
)
"""


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="v14-migration-"))
    yield td
    shutil.rmtree(td, ignore_errors=True)


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def test_new_database_has_preview_column(temp_dir: Path) -> None:
    """新库建表即含 preview_text 列，SCHEMA_VERSION 已推进到 14。"""
    assert SCHEMA_VERSION == 14
    db = SQLiteDatabase(temp_dir / "fresh.db")
    import asyncio

    asyncio.run(db.initialize())
    try:
        conn = sqlite3.connect(temp_dir / "fresh.db")
        assert _column_exists(conn, "llm_requests", "preview_text")
        conn.close()
    finally:
        asyncio.run(db.close())


def test_v13_database_upgrade_adds_column(temp_dir: Path) -> None:
    """v13 形状旧库：v14 回调补列、存量行默认空串、幂等。"""
    db_path = temp_dir / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_V13_LLM_REQUESTS_SQL)
    conn.execute(
        "INSERT INTO llm_requests (request_id, timestamp_ms, profile_name) VALUES ('req-old', 1000, 'planner')"
    )
    conn.commit()
    assert not _column_exists(conn, "llm_requests", "preview_text")

    v14_migrate(conn)
    assert _column_exists(conn, "llm_requests", "preview_text")
    row = conn.execute("SELECT preview_text FROM llm_requests WHERE request_id = 'req-old'").fetchone()
    assert row[0] == ""

    # 幂等：重复执行不再变更
    v14_migrate(conn)
    row = conn.execute("SELECT preview_text FROM llm_requests WHERE request_id = 'req-old'").fetchone()
    assert row[0] == ""
    conn.close()
