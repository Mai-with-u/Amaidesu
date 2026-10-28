"""话题摘要落库：timeline_summary 追加一段摘要历史，topics 是最新一轮的快照投影"""

from __future__ import annotations

import pytest

from src.agents.streamer.background import BackgroundMaintainer
from src.agents.streamer.room_state import RoomState
from src.modules.storage.database import SQLiteDatabase
from src.agents.streamer.config import StreamerBackgroundConfig
from tests.agents.streamer.helpers import stub_prompt


class _FakeSessionManager:
    async def resolve_pk(self) -> int:
        return 777


async def test_persist_appends_timeline_and_refreshes_topic_snapshot(store: SQLiteDatabase) -> None:
    room_state = RoomState()
    room_state.set_topic_summary("占位", now_ms=1)
    maintainer = BackgroundMaintainer(
        StreamerBackgroundConfig(summary_interval_ms=60_000),
        room_state=room_state,
        sessions_repo=store.sessions,
        chat_repo=store.chat,
        topic_repo=store.topics,
        session_manager=_FakeSessionManager(),
        prompt_manager=stub_prompt(),
    )
    await maintainer._persist_topic_snapshot("第一轮摘要", now_ms=60_000, previous_summary_ms=0)
    await maintainer._persist_topic_snapshot("第二轮摘要", now_ms=120_000, previous_summary_ms=60_000)

    # 场次主键经 LiveSessionManager 解析（跨表可 JOIN）；首次窗口起点回退一个摘要间隔
    timeline = await store.execute("SELECT * FROM timeline_summary ORDER BY id")
    rows = [(t["live_session_id"], t["start_ms"], t["end_ms"], t["summary"]) for t in timeline]
    assert rows == [(777, 0, 60_000, "第一轮摘要"), (777, 60_000, 120_000, "第二轮摘要")]

    # 无关键词时只有摘要句行，旧一轮被清除
    (topic,) = await store.execute("SELECT * FROM topics")
    assert (topic["source"], topic["label"]) == ("summary", "第二轮摘要")
    assert topic["score"] == pytest.approx(1.0)


async def test_persist_skipped_without_topic_repo() -> None:
    maintainer = BackgroundMaintainer(
        StreamerBackgroundConfig(), room_state=RoomState(), topic_repo=None, prompt_manager=stub_prompt()
    )
    await maintainer._persist_topic_snapshot("摘要", now_ms=1_000, previous_summary_ms=0)
