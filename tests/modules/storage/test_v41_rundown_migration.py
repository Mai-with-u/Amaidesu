"""v40 → v41 存储迁移：流程单环节双表补齐 mode 与 beats_json 列，存量行无损且重复执行幂等。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v41_rundown_segment_mode_and_beats import migrate as v41_migrate
from src.modules.storage.models.rundown import Rundown, RundownInstance, RundownInstanceSegment, RundownSegment
from src.modules.storage.schema import SCHEMA_VERSION
from tests.modules.storage.legacy_ddl import (
    V41_1_RUNDOWN_SEGMENTS_RUNDOWN_INSTANCE_SEGMENTS,
)


def test_v41_callback_adds_columns_idempotently_and_keeps_legacy_rows() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(V41_1_RUNDOWN_SEGMENTS_RUNDOWN_INSTANCE_SEGMENTS)

    # 连续运行两次验证幂等
    v41_migrate(conn)
    v41_migrate(conn)

    seg_row = conn.execute("SELECT seg_id, mode, beats_json FROM rundown_segments").fetchone()
    inst_row = conn.execute("SELECT seg_id, mode, beats_json FROM rundown_instance_segments").fetchone()
    assert seg_row == ("opening", None, "[]")
    assert inst_row == ("opening", None, "[]")
    conn.close()


async def test_v41_full_chain_roundtrips_mode_and_beats(db_path: Path) -> None:
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        assert await db.get_schema_version() == SCHEMA_VERSION
        rd = Rundown(
            rundown_id="rd_talk",
            title="杂谈测试单",
            segments=[
                RundownSegment(
                    id="intro",
                    title="破冰",
                    task_description="打招呼",
                    expected_ms=240_000,
                    min_duration_ms=120_000,
                    mode="talk",
                    beats=["确认声音", "介绍主题"],
                ),
                RundownSegment(
                    id="play",
                    title="主线",
                    task_description="推进",
                    expected_ms=600_000,
                ),
            ],
        )
        await db.rundowns.upsert_rundown(rd)
        loaded = await db.rundowns.get_rundown("rd_talk")
        assert loaded is not None
        assert (loaded.segments[0].mode, loaded.segments[0].beats) == ("talk", ["确认声音", "介绍主题"])
        assert (loaded.segments[1].mode, loaded.segments[1].beats) == (None, [])

        snap = RundownInstance(
            live_session_id=9,
            template_id="rd_talk",
            seg_index=0,
            segment_started_at_ms=1_000,
            rundown_started_at_ms=1_000,
            updated_at_ms=1_000,
            segments=[
                RundownInstanceSegment(
                    seq=0,
                    seg_id="intro",
                    title="破冰",
                    task_description="打招呼",
                    expected_ms=240_000,
                    mode="talk",
                    beats=["确认声音", "介绍主题"],
                )
            ],
        )
        await db.rundown_instances.upsert(snap)
        restored = await db.rundown_instances.get_by_session(9)
        assert restored is not None
        assert (restored.segments[0].mode, restored.segments[0].beats) == ("talk", ["确认声音", "介绍主题"])
    finally:
        await db.close()
