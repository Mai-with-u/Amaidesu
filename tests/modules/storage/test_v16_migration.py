"""v16 迁移单测：``llm_requests`` 补注入截图计数列。

覆盖：
- 新库建表即含 ``image_count``，SCHEMA_VERSION 推进到 16
- v15 形状旧库升级：补列默认 0，存量数据原样保留，重复执行幂等
- 写侧计数值经 repo 插入原样落库
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v16_llm_request_image_count import migrate as v16_migrate
from src.modules.storage.schema import SCHEMA_VERSION


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="v16-migration-"))
    yield td
    shutil.rmtree(td, ignore_errors=True)


def test_new_database_has_image_count(temp_dir: Path) -> None:
    """新库：DDL 已含 image_count 列，版本推进到 16。"""
    assert SCHEMA_VERSION == 16
    db = SQLiteDatabase(temp_dir / "fresh.db")
    asyncio.run(db.initialize())
    try:
        conn = sqlite3.connect(temp_dir / "fresh.db")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_requests)")}
        assert "image_count" in columns
        conn.close()
    finally:
        asyncio.run(db.close())


def test_v15_database_upgrade_adds_column_idempotent(temp_dir: Path) -> None:
    """v15 形状旧库：补列默认 0，存量行原样保留，回调幂等。"""
    db_path = temp_dir / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE llm_requests (
            request_id TEXT PRIMARY KEY, timestamp_ms INTEGER NOT NULL,
            profile_name TEXT NOT NULL DEFAULT '', model_name TEXT NOT NULL DEFAULT '',
            prompt_tokens INTEGER NOT NULL DEFAULT 0, preview_text TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO llm_requests(request_id, timestamp_ms, model_name, prompt_tokens)
            VALUES ('req-old', 1, 'm1', 64);
        """
    )
    conn.commit()

    v16_migrate(conn)
    row = conn.execute("SELECT model_name, prompt_tokens, image_count FROM llm_requests").fetchone()
    assert row == ("m1", 64, 0)

    # 幂等：再跑一次不报错、数据仍在
    v16_migrate(conn)
    assert conn.execute("SELECT COUNT(*) FROM llm_requests").fetchone()[0] == 1
    conn.close()


def test_insert_persists_image_count(temp_dir: Path) -> None:
    """写侧计数值经 LLMRepo 插入原样落库。"""
    db = SQLiteDatabase(temp_dir / "count.db")
    asyncio.run(db.initialize())
    try:
        asyncio.run(db.llm.insert_llm_request(request_id="req-img", timestamp_ms=1, image_count=3))
        row = asyncio.run(db.llm.get_llm_request_by_id("req-img"))
        assert row is not None and row["image_count"] == 3
    finally:
        asyncio.run(db.close())
