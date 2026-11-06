"""观众候选仓储：整理写进待审区（本场待审的整份替换）、批准、驳回、过期。

批准观众定下的写成一行观众来源的档案行并放进最新快照；批准她说欠着的登记成未了结承诺；
驳回的留着当去重依据；待审的只按时间过期。
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, List, Tuple

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import PendingArchiveLine, PendingViewerCandidate
from src.modules.storage.privacy import PrivacyService
from src.modules.storage.repos.memory_review import MemoryReviewError
from src.modules.storage.repos.viewer_candidates import CANDIDATE_EXPIRE_MS, replace_session_candidates


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "candidates.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def _settled_session(store: SQLiteDatabase) -> Tuple[int, int, int, int]:
    """一场整理过、存下了快照的正式场：观众起名、她回应、她又说欠大家一个故事。

    返回 (场次, 观众弹幕, 她的回应, 她说欠着的那句)。
    """
    session_id = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=600_000)
    said = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_100,
        sender_role="viewer",
        content="叫它团子吧",
        message_type="danmaku",
    )
    reply = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_200,
        sender_role="assistant",
        content="好，就叫团子",
        message_type="text",
    )
    owed = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_300,
        sender_role="assistant",
        content="上次欠大家一个鬼故事",
        message_type="text",
    )
    await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=[PendingArchiveLine(text="挖到铁矿", quote="铁矿", evidence_chat_id=owed + 100)],
        commitments=[],
        closures=[],
        settled_at_ms=9_999,
        now_ms_value=9_999,
    )
    return session_id, said, reply, owed


def _pending(said: int, reply: int, owed: int) -> List[PendingViewerCandidate]:
    return [
        PendingViewerCandidate(
            kind="viewer_defined",
            title="猫叫团子",
            viewer_chat_id=said,
            viewer_quote="叫它团子",
            reply_chat_id=reply,
            reply_quote="就叫团子",
        ),
        PendingViewerCandidate(
            kind="claimed_missing", title="给大家讲鬼故事", reply_chat_id=owed, reply_quote="欠大家一个鬼故事"
        ),
    ]


def _write(store: SQLiteDatabase, session_id: int, items: List[PendingViewerCandidate], at_ms: int = 10_000) -> int:
    with store.manager.transaction() as conn:
        return replace_session_candidates(conn, live_session_id=session_id, candidates=items, created_at_ms=at_ms)


def _edits(store: SQLiteDatabase) -> List[Tuple[str, str, str, str]]:
    with store.manager.transaction() as conn:
        rows = conn.execute("SELECT target, action, before_text, after_text FROM memory_edits ORDER BY id").fetchall()
    return [(row[0], row[1], row[2], row[3]) for row in rows]


async def test_settlement_rewrite_replaces_only_pending_candidates(store: SQLiteDatabase) -> None:
    session_id, said, reply, owed = await _settled_session(store)
    assert _write(store, session_id, _pending(said, reply, owed)) == 2
    pending = await store.viewer_candidates.list_candidates()
    rejected_id = next(item.id for item in pending if item.kind == "claimed_missing")
    await store.viewer_candidates.reject(candidate_id=rejected_id, at_ms=11_000)

    # 重新整理：本场待审的整份替换，驳回的留着
    assert _write(store, session_id, _pending(said, reply, owed)[:1]) == 1
    assert [(item.kind, item.status) for item in await store.viewer_candidates.list_candidates(status=None)] == [
        ("viewer_defined", "pending"),
        ("claimed_missing", "rejected"),
    ]
    assert await store.viewer_candidates.rejected_keys() == ([], ["给大家讲鬼故事"])
    assert await store.viewer_candidates.count_pending() == 1


async def test_approving_a_viewer_defined_candidate_writes_a_pinned_viewer_line(store: SQLiteDatabase) -> None:
    session_id, said, reply, owed = await _settled_session(store)
    _write(store, session_id, _pending(said, reply, owed))
    candidate = next(item for item in await store.viewer_candidates.list_candidates() if item.kind == "viewer_defined")

    approved = await store.viewer_candidates.approve(
        candidate_id=candidate.id, title="和观众一起给猫取名团子", lasting=True, at_ms=12_000
    )
    assert (approved.status, approved.decided_at_ms, approved.title) == ("approved", 12_000, "和观众一起给猫取名团子")
    (line,) = await store.archive.list_lines_by_ids(line_ids=[approved.archive_line_id or 0])
    assert (line.source, line.viewer_chat_id, line.evidence_chat_id, line.quote, line.human_locked) == (
        "viewer",
        said,
        reply,
        "就叫团子",
        True,
    ), "原话是她那句回应的逐字片段，挂着那条弹幕"
    snapshot = await store.archive.get_snapshot(live_session_id=session_id)
    assert snapshot is not None and (
        snapshot.lines[0].line.id,
        snapshot.lines[0].pinned,
        snapshot.lines[0].lasting,
    ) == (
        line.id,
        True,
        True,
    )
    assert _edits(store) == [("candidate", "approve", "猫叫团子", "和观众一起给猫取名团子")]

    with pytest.raises(MemoryReviewError) as again:
        await store.viewer_candidates.approve(candidate_id=candidate.id, at_ms=13_000)
    assert again.value.kind == "conflict"


async def test_approving_a_claimed_missing_candidate_registers_an_open_commitment(store: SQLiteDatabase) -> None:
    session_id, said, reply, owed = await _settled_session(store)
    _write(store, session_id, _pending(said, reply, owed))
    candidate = next(item for item in await store.viewer_candidates.list_candidates() if item.kind == "claimed_missing")

    approved = await store.viewer_candidates.approve(candidate_id=candidate.id, at_ms=12_000)
    commitment = await store.commitments.get_commitment(commitment_id=approved.commitment_id or 0)
    assert commitment is not None
    assert (commitment.what, commitment.quote, commitment.evidence_chat_id, commitment.status) == (
        "给大家讲鬼故事",
        "欠大家一个鬼故事",
        owed,
        "open",
    )
    assert commitment.created_session_id == session_id


async def test_pending_candidates_expire_after_fourteen_days_only(store: SQLiteDatabase) -> None:
    session_id, said, reply, owed = await _settled_session(store)
    _write(store, session_id, _pending(said, reply, owed), at_ms=10_000)
    first = (await store.viewer_candidates.list_candidates())[0]
    await store.viewer_candidates.reject(candidate_id=first.id, at_ms=11_000)

    assert await store.viewer_candidates.expire_pending(now_ms_value=10_000 + CANDIDATE_EXPIRE_MS) == 0
    assert await store.viewer_candidates.expire_pending(now_ms_value=10_001 + CANDIDATE_EXPIRE_MS) == 1
    assert [item.status for item in await store.viewer_candidates.list_candidates(status=None)] == ["rejected"]


async def test_forget_me_drops_candidates_naming_the_viewer_and_clears_their_edits(
    store: SQLiteDatabase, tmp_path: Path
) -> None:
    """她回应别人时提到了被删的那位：候选挂的是别人的弹幕、外键删不到，删除函数整条删掉；
    驳回过的也删，改动记录里它的标题清空、候选编号留着；没提到名字的候选不动。"""
    session_id, said, reply, owed = await _settled_session(store)
    viewer = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_ajie",
        user_name="阿杰",
        timestamp_ms=1_050,
        name_match_window_ms=0,
    )
    naming = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_400,
        sender_role="assistant",
        content="阿杰也说就叫团子",
        message_type="text",
    )
    items = [
        PendingViewerCandidate(
            kind="viewer_defined",
            title="猫叫团子",
            viewer_chat_id=said,
            viewer_quote="叫它团子",
            reply_chat_id=naming,
            reply_quote="阿杰也说就叫团子",
        ),
        PendingViewerCandidate(
            kind="claimed_missing", title="给阿杰讲鬼故事", reply_chat_id=owed, reply_quote="欠大家一个鬼故事"
        ),
        PendingViewerCandidate(
            kind="viewer_defined",
            title="狗叫豆豆",
            viewer_chat_id=said,
            viewer_quote="叫它团子",
            reply_chat_id=reply,
            reply_quote="就叫团子",
        ),
    ]
    _write(store, session_id, items)
    rows = await store.viewer_candidates.list_candidates(status="pending")
    rejected = next(row for row in rows if row.title == "给阿杰讲鬼故事")
    await store.viewer_candidates.reject(candidate_id=rejected.id, at_ms=11_000)

    privacy = PrivacyService(store.manager, db_path=store.db_path, key_path=tmp_path / "secrets" / "forget.key")
    await privacy.initialize()
    try:
        assert await privacy.delete_viewer(viewer_id=viewer.viewer_id) is True
    finally:
        await privacy.close()

    left = await store.viewer_candidates.list_candidates(status=None)
    assert [row.title for row in left] == ["狗叫豆豆"]
    with store.manager.transaction() as conn:
        edit = conn.execute("SELECT action, before_text, candidate_id FROM memory_edits").fetchone()
    assert (edit[0], edit[1], edit[2]) == ("reject", "", rejected.id)
