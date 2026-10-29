"""LongMemoryStore：长期记忆纯文本文件的读写。

- 文件不存在读回空串；写后读回去掉首尾空白的全文
- 正式与彩排各一份，互不覆盖；落在数据库同目录的 memory/ 下
- 写出 LF + UTF-8 无 BOM，人手加的 BOM 也能读；原子替换不留临时文件
"""

from __future__ import annotations

from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.long_memory import LongMemoryStore


async def test_read_missing_then_roundtrip_per_kind(tmp_path: Path) -> None:
    store = LongMemoryStore(tmp_path / "memory")
    assert await store.read("live") == ""

    saved = await store.write("live", "  ## 答应过的事\n- 我答应老王挖钻石（未兑现）  \n")
    assert saved == "## 答应过的事\n- 我答应老王挖钻石（未兑现）"
    await store.write("rehearsal", "调试时记的")

    assert await store.read("live") == saved
    assert await store.read("rehearsal") == "调试时记的"
    assert store.path_of("live").name == "MEMORY.md"
    assert store.path_of("rehearsal").name == "MEMORY.rehearsal.md"
    assert not list((tmp_path / "memory").glob("*.tmp"))  # 原子替换不留临时文件


async def test_file_is_lf_utf8_and_hand_edits_with_bom_are_readable(tmp_path: Path) -> None:
    store = LongMemoryStore(tmp_path / "memory")
    await store.write("live", "第一行\n第二行")
    raw = store.path_of("live").read_bytes()
    assert raw == "第一行\n第二行\n".encode("utf-8") and b"\r\n" not in raw

    store.path_of("live").write_bytes("﻿人手改过".encode("utf-8"))  # 记事本另存会带 BOM
    assert await store.read("live") == "人手改过"
    assert await store.write("live", "") == "" and await store.read("live") == ""


async def test_database_store_sits_next_to_db_file(store: SQLiteDatabase, db_path: Path) -> None:
    assert store.long_memory.root == db_path.parent / "memory"
