"""下播整理列出"她说欠着、账上却找不到的"：只进待审区，账上对得上的不列，驳回过的不再列，从不自动登记。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from src.agents.streamer.memory.settle import SessionSettler
from src.modules.llm.payload import Response
from src.modules.prompts import PromptManager
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import SettleReport
from tests.modules.llm.fakes import FakeLLMManager


@pytest.fixture(scope="module")
def prompt_manager() -> PromptManager:
    manager = PromptManager(auto_scan_src=True)
    manager.load_all()
    return manager


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "claimed.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


def _settler(store: SQLiteDatabase, llm: Any, prompt_manager: PromptManager) -> SessionSettler:
    return SessionSettler(
        sessions_repo=store.sessions,
        chat_repo=store.chat,
        commitments_repo=store.commitments,
        event_repo=store.events,
        archive_repo=store.archive,
        llm_service=llm,
        prompt_manager=prompt_manager,
        game_names_provider=lambda: ["minecraft"],
        clock=lambda: 7_777,
    )


def _reply(*contents: str) -> FakeLLMManager:
    return FakeLLMManager(responses=[Response(success=True, content=content) for content in contents])


async def _session(store: SQLiteDatabase) -> int:
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session, ended_at_ms=1_800_000)
    return session


async def _say(store: SQLiteDatabase, session: int, content: str, at_ms: int, reply_to: Any = None) -> int:
    return await store.chat.insert_live_chat(
        live_session_id=session,
        timestamp_ms=at_ms,
        sender_role="assistant",
        content=content,
        message_type="speech",
        reply_to_message_id=reply_to,
    )


async def _danmaku(store: SQLiteDatabase, session: int, content: str, message_id: str) -> int:
    return await store.chat.insert_live_chat(
        live_session_id=session,
        timestamp_ms=1_050,
        sender_role="viewer",
        content=content,
        message_type="danmaku",
        message_id=message_id,
        sender_id="u1",
        sender_name="阿杰",
        viewer_id=9,
    )


async def test_claimed_missing_goes_to_review_and_is_never_registered(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """账上找不到的列成候选（挂上她回的那条弹幕）；账上已有、已了结、本场新登记的都不列。"""
    session = await _session(store)
    closed = await store.commitments.insert_commitment(what="给大家讲鬼故事", quote="下次讲鬼故事", now_ms_value=10)
    assert closed is not None and await store.commitments.close_commitment(commitment_id=closed)
    asked = await _danmaku(store, session, "你上次说要唱歌的呢", "m1")
    line = await _say(store, session, "对哦，上次答应过你们要唱那首歌", 1_100, reply_to="m1")
    await _say(store, session, "鬼故事上次答应过的，讲过啦", 1_200)
    await _say(store, session, "还欠大家一个画画，下周六一定画", 1_300)

    llm = _reply(
        '{"archive": []}',
        '{"commitments": [{"line": 3, "quote": "下周六一定画", "what": "给大家画画", "to": "everyone"}],'
        ' "claimed_missing": ['
        '{"line": 1, "quote": "上次答应过你们要唱那首歌", "what": "给大家唱那首歌"},'
        '{"line": 2, "quote": "鬼故事上次答应过的", "what": "给大家讲鬼故事"},'
        '{"line": 3, "quote": "还欠大家一个画画", "what": "画画"},'
        '{"line": 1, "quote": "要唱那首歌", "what": "唱歌（又列一遍）"}]}',
    )
    assert await _settler(store, llm, prompt_manager).settle_session(session) is True

    prompt = llm.calls[1]["input"]
    assert f"【已了结的承诺】\n#{closed} 「给大家讲鬼故事」" in prompt, "已了结的标题也交给模型比对"
    (candidate,) = await store.viewer_candidates.list_candidates()
    assert (candidate.kind, candidate.title, candidate.reply_chat_id, candidate.reply_quote) == (
        "claimed_missing",
        "给大家唱那首歌",
        line,
        "上次答应过你们要唱那首歌",
    )
    assert (candidate.viewer_chat_id, candidate.viewer_quote) == (asked, "你上次说要唱歌的呢")
    assert [row.what for row in await store.commitments.list_commitments(statuses=["open"])] == ["给大家画画"], (
        "她说欠着的不自动登记，只有重新说定的那句按承诺登记"
    )
    row = await store.sessions.get_live_session(live_session_id=session)
    assert row is not None and row.settle_report is not None
    assert SettleReport.model_validate_json(row.settle_report).candidates_listed == {"claimed_missing": 1}


async def test_rejected_claims_are_not_listed_again_and_bad_field_does_not_fail_settle(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    session = await _session(store)
    await _say(store, session, "上次答应过你们要唱那首歌", 1_100)
    claimed = '"claimed_missing": [{"line": 1, "quote": "上次答应过你们要唱那首歌", "what": "给大家唱那首歌"}]'
    llm = _reply('{"archive": []}', '{"commitments": [], ' + claimed + "}")
    assert await _settler(store, llm, prompt_manager).settle_session(session) is True
    (candidate,) = await store.viewer_candidates.list_candidates()
    await store.viewer_candidates.reject(candidate_id=candidate.id, at_ms=8_000)

    # 重新整理：驳回过的同一句不再列（换了标题也不列）
    await store.sessions.requeue_settlement(live_session_id=session)
    llm = _reply('{"archive": []}', '{"commitments": [], ' + claimed.replace("给大家唱那首歌", "唱歌") + "}")
    assert await _settler(store, llm, prompt_manager).settle_session(session) is True
    assert [item.status for item in await store.viewer_candidates.list_candidates(status=None)] == ["rejected"]

    # 字段写坏了只当没有，整场整理照常完成
    await store.sessions.requeue_settlement(live_session_id=session)
    llm = _reply('{"archive": []}', '{"commitments": [], "claimed_missing": "没有"}')
    assert await _settler(store, llm, prompt_manager).settle_session(session) is True


def test_claimed_missing_eval_dry_self_check_passes() -> None:
    """ "她说欠着的"评测器（scripts/memory_eval/claimed_missing.py）回放生产整理器：找承诺提示词、取料或
    欠着的校验改了而评测没跟上，live 量出的列出率就不是生产的真实水平。dry 用理想输出过生产校验器。"""
    script = Path(__file__).resolve().parents[3] / "scripts" / "memory_eval" / "claimed_missing.py"
    completed = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=300, check=False
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]
