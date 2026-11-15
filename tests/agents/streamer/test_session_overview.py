"""场次概览：取料、一次模型调用、写进 live_sessions.overview，以及没料与失败时的行为。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.agents.streamer.session_overview import SessionOverviewWriter
from src.modules.llm.payload import Response
from src.modules.prompts import PromptManager
from src.modules.storage.database import SQLiteDatabase
from tests.modules.llm.fakes import FakeLLMManager


@pytest.fixture(scope="module")
def prompt_manager() -> PromptManager:
    manager = PromptManager(auto_scan_src=True)
    manager.load_all()
    return manager


@pytest.fixture()
async def store(tmp_path: Path):
    db = SQLiteDatabase(tmp_path / "overview.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


def _writer(store: SQLiteDatabase, llm: Any, prompt_manager: PromptManager) -> SessionOverviewWriter:
    return SessionOverviewWriter(
        sessions_repo=store.sessions,
        topic_repo=store.topics,
        event_repo=store.events,
        llm_service=llm,
        prompt_manager=prompt_manager,
        clock=lambda: 9_999,
    )


async def _ended_session(store: SQLiteDatabase, *, started_at_ms: int = 1_700_000_000_000) -> int:
    session_id = await store.sessions.insert_live_session(started_at_ms=started_at_ms, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=started_at_ms + 7_200_000)
    return session_id


async def test_write_overview_calls_model_once_and_persists(store, prompt_manager) -> None:
    """有素材时调一次模型，把概要写进 overview 列；提示词里带着摘要与游戏事件。"""
    session_id = await _ended_session(store)
    await store.topics.insert_timeline_summary(
        live_session_id=session_id,
        start_ms=1_700_000_000_000,
        end_ms=1_700_000_060_000,
        summary="观众在问水车怎么搭",
    )
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="minecraft",
        event_type="milestone",
        message="完成任务：搭起一座水车",
        timestamp_ms=1_700_000_120_000,
    )
    llm = FakeLLMManager(Response(success=True, content="这场在造水车，中间翻车一次，后来跑酷。"))
    writer = _writer(store, llm, prompt_manager)

    assert await writer.write_overview(session_id) is True
    assert len(llm.calls) == 1
    assert llm.calls[0]["profile"] == "summary"
    prompt = str(llm.calls[0]["input"])
    assert "观众在问水车怎么搭" in prompt
    assert "完成任务：搭起一座水车" in prompt

    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None
    assert row.overview == "这场在造水车，中间翻车一次，后来跑酷。"


async def test_no_material_skips_model_call(store, prompt_manager) -> None:
    """既没有话题摘要也没有游戏事件时，不白花一次模型调用。"""
    session_id = await _ended_session(store)
    llm = FakeLLMManager(Response(success=True, content="不该被调用"))
    writer = _writer(store, llm, prompt_manager)

    assert await writer.write_overview(session_id) is False
    assert llm.calls == []
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.overview is None


async def test_missing_session_row_returns_false(store, prompt_manager) -> None:
    """场次行不在（已删）时直接放弃，不调模型。"""
    llm = FakeLLMManager(Response(success=True, content="不该被调用"))
    writer = _writer(store, llm, prompt_manager)

    assert await writer.write_overview(999_999) is False
    assert llm.calls == []


async def test_model_failure_raises_and_writes_nothing(store, prompt_manager) -> None:
    """模型调用失败：抛给调用方（它只记日志），库里不留半截概要。"""
    session_id = await _ended_session(store)
    await store.topics.insert_timeline_summary(
        live_session_id=session_id, start_ms=1_700_000_000_000, end_ms=1_700_000_060_000, summary="在闲聊"
    )
    llm = FakeLLMManager(fail=True)
    writer = _writer(store, llm, prompt_manager)

    with pytest.raises(RuntimeError):
        await writer.write_overview(session_id)
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.overview is None
