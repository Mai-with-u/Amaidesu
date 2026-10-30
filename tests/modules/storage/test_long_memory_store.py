"""LongMemoryStore：长期记忆纯文本文件的读写。

- 文件不存在读回空串；写后读回去掉首尾空白的全文
- 正式与彩排各一份，互不覆盖；落在数据库同目录的 memory/ 下
- 写出 LF + UTF-8 无 BOM，人手加的 BOM 也能读；原子替换不留临时文件
- 每次写入留底（旧全文快照 + 来由与改动），可按记录恢复；版本号对不上的写入被拒
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.long_memory import LongMemoryConflict, LongMemoryStore


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


def _ticking_clock(start: int = 1_000) -> Callable[[], int]:
    """每读一次前进 1 毫秒的假时钟：留底编号可预期、互不相同。"""
    state = {"now": start}

    def clock() -> int:
        state["now"] += 1
        return state["now"]

    return clock


async def test_every_write_leaves_snapshot_and_change_log(tmp_path: Path) -> None:
    """每次写入把旧全文存成快照、记下来由与改动；内容不变不留底；恢复也是一次可再撤回的写入。"""
    store = LongMemoryStore(tmp_path / "memory", clock=_ticking_clock())
    await store.write("live", "第一版", reason="收播整理", changes=["新增：第一版"], session_id=3)
    await store.write("live", "第二版", reason="WebUI 保存", changes=["改写：第一版 → 第二版"])
    await store.write("live", "第二版", reason="没变化")

    history = await store.list_history("live")
    assert [(e.reason, e.session_id, e.has_snapshot) for e in history] == [
        ("WebUI 保存", None, True),
        ("收播整理", 3, False),  # 第一次写之前没有文件，没有快照
    ]
    assert history[0].changes == ["改写：第一版 → 第二版"]
    assert await store.read_snapshot("live", history[0].id) == "第一版"
    assert await store.read_snapshot("live", history[1].id) is None
    assert await store.read_snapshot("rehearsal", history[0].id) is None  # 两条线的留底互不相干

    assert await store.restore("live", history[0].id) == "第一版"
    assert await store.read("live") == "第一版"
    latest = (await store.list_history("live"))[0]
    assert latest.reason.startswith("恢复到记录") and await store.read_snapshot("live", latest.id) == "第二版"
    assert await store.restore("live", "不存在的编号") is None


async def test_write_with_stale_version_is_rejected(tmp_path: Path) -> None:
    """读出之后文件被别人改过：带旧版本号的写入抛冲突，文件保持别人改的样子。"""
    store = LongMemoryStore(tmp_path / "memory")
    await store.write("live", "原文")
    _text, version = await store.read_versioned("live")
    await store.write("live", "人在 WebUI 里改了")

    with pytest.raises(LongMemoryConflict):
        await store.write("live", "合并结果", expected_version=version)
    assert await store.read("live") == "人在 WebUI 里改了"

    _text, fresh = await store.read_versioned("live")
    assert await store.write("live", "合并结果", expected_version=fresh) == "合并结果"


async def test_history_keeps_only_recent_entries_and_skips_broken_lines(tmp_path: Path) -> None:
    store = LongMemoryStore(tmp_path / "memory", history_keep=2, clock=_ticking_clock())
    for i in range(4):
        await store.write("live", f"第{i}版")
    history_dir = tmp_path / "memory" / "history" / "live"
    assert len(await store.list_history("live")) == 2
    assert len(list(history_dir.glob("*.md"))) == 2  # 超出保留数的快照一并删掉

    changes = history_dir / "changes.jsonl"
    changes.write_bytes(changes.read_bytes() + "这一行不是 JSON\n".encode("utf-8"))
    assert len(await store.list_history("live")) == 2
