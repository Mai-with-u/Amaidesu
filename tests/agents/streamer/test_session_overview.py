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


async def test_write_overview_covers_early_mid_late_events_for_long_session(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """2.5 小时 200 条事件长场：场次概览原料同时覆盖前段跨维度、中段汇报与末段进展，不截断前半场。"""
    t0 = 1_700_000_000_000
    minute = 60_000
    session_id = await store.sessions.insert_live_session(started_at_ms=t0, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=t0 + 150 * minute)

    # 前段（第 8 分钟）跨维度 + 中段（第 70 分钟）阶段汇报
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="minecraft",
        event_type="dimension_changed",
        message="从主世界到了下界",
        timestamp_ms=t0 + 8 * minute,
    )
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="minecraft",
        event_type="report",
        message="交付：完成自动磨坊布线",
        timestamp_ms=t0 + 70 * minute,
    )
    # 后段（第 100–149 分钟）198 条密集常规事件，远超原先 DESC LIMIT 60 上限
    for idx in range(198):
        await store.events.insert_game_event(
            live_session_id=session_id,
            game="minecraft",
            event_type="attention_required" if idx % 2 else "milestone",
            message=f"末段常规事件 #{idx}",
            timestamp_ms=t0 + 100 * minute + idx * 15_000,
        )

    llm = FakeLLMManager(Response(success=True, content="本场先去下界探险，中段搭起磨坊，后半场继续推进。"))
    writer = _writer(store, llm, prompt_manager)

    assert await writer.write_overview(session_id) is True
    prompt = str(llm.calls[0]["input"])
    assert "从主世界到了下界" in prompt
    assert "交付：完成自动磨坊布线" in prompt
    assert "末段常规事件 #197" in prompt
    assert prompt.index("从主世界到了下界") < prompt.index("交付：完成自动磨坊布线") < prompt.index("末段常规事件 #197")


async def test_write_overview_includes_task_succeeded_and_succeeded_delegations_in_order(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """概览同时纳入失败里程碑、单步成功 task_succeeded 与交付委派，防止偏失败事件把已跑通机器写反。"""
    t0 = 1_700_000_000_000
    minute = 60_000
    session_id = await store.sessions.insert_live_session(started_at_ms=t0, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=t0 + 60 * minute)

    # 先有一条失败里程碑，随后通过单步 task_succeeded 与委派交付跑通机器
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="minecraft",
        event_type="milestone",
        message="使用动力压机未完成：站位不对",
        timestamp_ms=t0 + 5 * minute,
    )
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="minecraft",
        event_type="task_succeeded",
        message="使用动力压机：压出 4 块铁板",
        timestamp_ms=t0 + 12 * minute,
    )
    await store.events.insert_delegation_event(
        live_session_id=session_id,
        game="minecraft",
        task_id="deleg-mill",
        kind="accepted",
        text="接通自动磨坊",
        timestamp_ms=t0 + 15 * minute,
    )
    await store.events.insert_delegation_event(
        live_session_id=session_id,
        game="minecraft",
        task_id="deleg-mill",
        kind="succeeded",
        text="delivery: 磨坊已接通开始出面粉",
        timestamp_ms=t0 + 25 * minute,
    )

    llm = FakeLLMManager(Response(success=True, content="本场先遇压机站位问题，随后顺利压出铁板并接通磨坊。"))
    writer = _writer(store, llm, prompt_manager)

    assert await writer.write_overview(session_id) is True
    prompt = str(llm.calls[0]["input"])
    assert "使用动力压机未完成：站位不对" in prompt
    assert "完成：使用动力压机：压出 4 块铁板" in prompt
    assert "委派完成「接通自动磨坊」：磨坊已接通开始出面粉" in prompt
    assert (
        prompt.index("使用动力压机未完成：站位不对")
        < prompt.index("完成：使用动力压机：压出 4 块铁板")
        < prompt.index("委派完成「接通自动磨坊」：磨坊已接通开始出面粉")
    )


async def test_write_overview_does_not_swallow_short_delegation_summary_on_substring_report(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """短委派摘要不因偶然是某条无关 report 的子串而被去重吞掉；长摘要子串命中时仍正常去重。"""
    t0 = 1_700_000_000_000
    minute = 60_000
    session_id = await store.sessions.insert_live_session(started_at_ms=t0, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=t0 + 30 * minute)

    await store.events.insert_game_event(
        live_session_id=session_id,
        game="minecraft",
        event_type="report",
        message="交付：营地仓库整理好了，自动熔炉已点火投入运转",
        timestamp_ms=t0 + 10 * minute,
    )
    for task_id, instruction, summary, ended in (
        ("d-short", "给正门换门板", "delivery: 好了", t0 + 12 * minute),
        ("d-dup", "点亮自动熔炉", "delivery: 自动熔炉已点火投入运转", t0 + 14 * minute),
    ):
        await store.events.insert_delegation_event(
            live_session_id=session_id,
            game="minecraft",
            task_id=task_id,
            kind="accepted",
            text=instruction,
            timestamp_ms=t0 + 5 * minute,
        )
        await store.events.insert_delegation_event(
            live_session_id=session_id,
            game="minecraft",
            task_id=task_id,
            kind="succeeded",
            text=summary,
            timestamp_ms=ended,
        )

    llm = FakeLLMManager(Response(success=True, content="本场整理了仓库并更换门板。"))
    writer = _writer(store, llm, prompt_manager)

    assert await writer.write_overview(session_id) is True
    prompt = str(llm.calls[0]["input"])
    assert "委派完成「给正门换门板」：好了" in prompt
    assert "委派完成「点亮自动熔炉」" not in prompt
