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
from src.modules.storage.repos.viewer_candidates import (
    CANDIDATE_EXPIRE_MS,
    VIEWER_SELF,
    replace_session_candidates,
)


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


async def test_approving_a_viewer_defined_candidate_writes_a_normal_viewer_line(store: SQLiteDatabase) -> None:
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
        False,
        True,
    ), "以普通行插在最前面：稳定照批准时标的，不标放回"
    assert await store.memory_review.count_human_edits(live_session_id=session_id) == 0, (
        "观众来源行重新整理时留着，不拦重新整理"
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


async def test_viewer_line_reads_back_with_the_source_viewers_names(store: SQLiteDatabase) -> None:
    """批准后的观众来源行：按挂着的那条弹幕带出发送者的名字（弹幕上的名、现名），快照、按编号、线索池都带；
    她自己说的行不带。原话照存不改。"""
    session_id = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=600_000)
    viewer = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_ajie",
        user_name="阿杰",
        timestamp_ms=1_050,
        name_match_window_ms=0,
    )
    said = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_100,
        sender_role="viewer",
        sender_name="阿杰在摸鱼",
        viewer_id=viewer.viewer_id,
        content="叫它团子吧",
        message_type="danmaku",
    )
    reply = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_200,
        sender_role="assistant",
        content="阿杰说得对，就叫团子",
        message_type="text",
    )
    await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=[PendingArchiveLine(text="挖到铁矿", quote="铁矿", evidence_chat_id=reply + 100)],
        commitments=[],
        closures=[],
        settled_at_ms=9_999,
        now_ms_value=9_999,
    )
    _write(
        store,
        session_id,
        [
            PendingViewerCandidate(
                kind="viewer_defined",
                title="猫叫团子",
                viewer_chat_id=said,
                viewer_quote="叫它团子",
                word="团子",
                reply_chat_id=reply,
                reply_quote="阿杰说得对，就叫团子",
            )
        ],
    )
    (candidate,) = await store.viewer_candidates.list_candidates()
    approved = await store.viewer_candidates.approve(
        candidate_id=candidate.id, title="猫叫团子", lasting=True, at_ms=12_000
    )
    line_id = approved.archive_line_id or 0

    (by_id,) = await store.archive.list_lines_by_ids(line_ids=[line_id])
    assert (by_id.quote, sorted(by_id.source_viewer_names)) == ("阿杰说得对，就叫团子", ["阿杰", "阿杰在摸鱼"])
    assert by_id.viewer_word == "团子", "批准时观众定下的词抄到档案行上，读档案不用回头查候选表"
    snapshot = await store.archive.get_snapshot(live_session_id=session_id)
    assert snapshot is not None
    named = {item.line.id: item.line.source_viewer_names for item in snapshot.lines}
    assert sorted(named[line_id]) == ["阿杰", "阿杰在摸鱼"]
    assert [names for line, names in named.items() if line != line_id] == [[]], "她自己说的行不带名字"
    by_session = {
        line.source: line.source_viewer_names for line in await store.archive.list_lines(live_session_id=session_id)
    }
    assert sorted(by_session["viewer"]) == ["阿杰", "阿杰在摸鱼"] and by_session["streamer"] == []


async def test_forget_me_masks_candidates_naming_the_viewer_and_drops_their_own(
    store: SQLiteDatabase, tmp_path: Path
) -> None:
    """她回应别人时提到了被删的那位：候选挂的是别人的弹幕，不整条删，名字换成"一位观众"；驳回过的照旧
    是驳回（同一条以后不再列），改动记录行留着、提到 ta 的文本清空。挂在 ta 自己弹幕上的整条删。"""
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
    # 阿杰自己的弹幕上挂着的一条：随 ta 的弹幕整条删
    own = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_500,
        sender_role="viewer",
        content="狗就叫旺财",
        message_type="danmaku",
        viewer_id=viewer.viewer_id,
    )
    own_reply = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_600,
        sender_role="assistant",
        content="好，狗叫旺财",
        message_type="text",
    )
    items.append(
        PendingViewerCandidate(
            kind="viewer_defined",
            title="狗叫旺财",
            viewer_chat_id=own,
            viewer_quote="狗就叫旺财",
            reply_chat_id=own_reply,
            reply_quote="狗叫旺财",
        )
    )
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

    left = {row.id: row for row in await store.viewer_candidates.list_candidates(status=None)}
    assert sorted((row.title, row.reply_quote, row.status) for row in left.values()) == [
        ("狗叫豆豆", "就叫团子", "pending"),
        ("猫叫团子", "一位观众也说就叫团子", "pending"),
        ("给一位观众讲鬼故事", "欠大家一个鬼故事", "rejected"),
    ], "挂在别人弹幕上的只换名字；挂在 ta 自己弹幕上的整条删"
    assert rejected.id in left, "驳回过的留着，同一条以后不再列"
    with store.manager.transaction() as conn:
        edit = conn.execute("SELECT action, before_text, candidate_id FROM memory_edits").fetchone()
    assert (edit[0], edit[1], edit[2]) == ("reject", "", rejected.id)


async def test_forget_me_masks_the_viewers_name_in_a_word_another_viewer_set(
    store: SQLiteDatabase, tmp_path: Path
) -> None:
    """别的观众拿 ta 的名字给小猫起名、批准成了档案行：ta 被删后标题、原话、定下的词三列都不再有 ta 的名字。"""
    session_id, said, reply, owed = await _settled_session(store)
    named = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_ajie",
        user_name="阿杰",
        timestamp_ms=1_050,
        name_match_window_ms=0,
    )
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_400,
        sender_role="viewer",
        content="我来了",
        message_type="danmaku",
        viewer_id=named.viewer_id,
    )
    other = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_500,
        sender_role="viewer",
        content="小猫叫阿杰吧",
        message_type="danmaku",
    )
    answer = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_600,
        sender_role="assistant",
        content="好，小猫就叫阿杰",
        message_type="text",
    )
    _write(
        store,
        session_id,
        [
            PendingViewerCandidate(
                kind="viewer_defined",
                title="给小猫取名阿杰",
                viewer_chat_id=other,
                viewer_quote="小猫叫阿杰",
                word="阿杰",
                reply_chat_id=answer,
                reply_quote="小猫就叫阿杰",
            )
        ],
    )
    (candidate,) = await store.viewer_candidates.list_candidates()
    approved = await store.viewer_candidates.approve(candidate_id=candidate.id, at_ms=12_000)

    privacy = PrivacyService(store.manager, db_path=store.db_path, key_path=tmp_path / "secrets" / "forget.key")
    await privacy.initialize()
    try:
        assert await privacy.delete_viewer(viewer_id=named.viewer_id) is True
    finally:
        await privacy.close()

    (line,) = await store.archive.list_lines_by_ids(line_ids=[approved.archive_line_id or 0])
    assert (line.text, line.quote, line.viewer_word) == ("给小猫取名一位观众", "小猫就叫一位观众", "一位观众")


async def test_forget_me_compares_percent_and_underscore_literally(store: SQLiteDatabase, tmp_path: Path) -> None:
    """名字里的 _、% 按原字比：删"阿_杰"不动写着"阿x杰"的别人的候选与改动记录。"""
    session_id, said, reply, owed = await _settled_session(store)
    viewer = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_underscore",
        user_name="阿_杰",
        timestamp_ms=1_050,
        name_match_window_ms=0,
    )
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_500,
        sender_role="viewer",
        content="我来了",
        message_type="danmaku",
        viewer_id=viewer.viewer_id,
    )
    _write(
        store,
        session_id,
        [
            PendingViewerCandidate(kind="claimed_missing", title="给阿x杰讲故事", reply_chat_id=owed, reply_quote="欠"),
            PendingViewerCandidate(kind="claimed_missing", title="给阿_杰唱歌", reply_chat_id=reply, reply_quote="就"),
        ],
    )
    row = next(item for item in await store.viewer_candidates.list_candidates(status="pending") if "x" in item.title)
    await store.viewer_candidates.reject(candidate_id=row.id, at_ms=11_000)

    privacy = PrivacyService(store.manager, db_path=store.db_path, key_path=tmp_path / "secrets" / "forget.key")
    await privacy.initialize()
    try:
        assert await privacy.delete_viewer(viewer_id=viewer.viewer_id) is True
    finally:
        await privacy.close()

    titles = sorted(item.title for item in await store.viewer_candidates.list_candidates(status=None))
    assert titles == ["给一位观众唱歌", "给阿x杰讲故事"], "真提到 ta 的照换，只是像的不动"
    assert [(edit[1], edit[2]) for edit in _edits(store)] == [("reject", "给阿x杰讲故事")]


async def test_viewer_self_candidate_lifecycle_coexists_revokes_deletes_and_stays_out_of_archive(
    store: SQLiteDatabase,
) -> None:
    """老观众近况候选：可与观众定下共享同一句回应；批准后留在候选表、不写档案与承诺；支持撤回待审与删除，驳回不影响全局去重。"""
    session_id, _said, _reply, _owed = await _settled_session(store)
    viewer = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_xiaoxia",
        user_name="小夏",
        timestamp_ms=1_050,
        name_match_window_ms=0,
    )
    chat_1 = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=2_100,
        sender_role="viewer",
        viewer_id=viewer.viewer_id,
        content="我平时做木工，这只小狗叫木头吧",
        message_type="danmaku",
    )
    reply_1 = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=2_200,
        sender_role="assistant",
        content="原来你做木工呀，那就叫木头",
        message_type="speech",
    )
    chat_2 = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=2_300,
        sender_role="viewer",
        viewer_id=viewer.viewer_id,
        content="我周末常去徒步",
        message_type="danmaku",
    )
    reply_2 = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=2_400,
        sender_role="assistant",
        content="徒步呼吸新鲜空气挺好",
        message_type="speech",
    )

    written = _write(
        store,
        session_id,
        [
            PendingViewerCandidate(
                kind="viewer_defined",
                title="小狗取名木头",
                viewer_chat_id=chat_1,
                viewer_quote="小狗叫木头",
                word="木头",
                reply_chat_id=reply_1,
                reply_quote="那就叫木头",
                viewer_id=viewer.viewer_id,
            ),
            PendingViewerCandidate(
                kind=VIEWER_SELF,
                title="ta 平时做木工",
                viewer_chat_id=chat_1,
                viewer_quote="平时做木工",
                word="木工",
                reply_chat_id=reply_1,
                reply_quote="原来你做木工呀",
                viewer_id=viewer.viewer_id,
            ),
            # 同 kind 同 reply_chat_id 第二条去重丢弃
            PendingViewerCandidate(
                kind=VIEWER_SELF,
                title="ta 喜欢做木工活",
                viewer_chat_id=chat_1,
                viewer_quote="做木工",
                word="木工",
                reply_chat_id=reply_1,
                reply_quote="原来你做木工呀",
                viewer_id=viewer.viewer_id,
            ),
            PendingViewerCandidate(
                kind=VIEWER_SELF,
                title="ta 周末常去徒步",
                viewer_chat_id=chat_2,
                viewer_quote="常去徒步",
                word="徒步",
                reply_chat_id=reply_2,
                reply_quote="徒步呼吸新鲜空气挺好",
                viewer_id=viewer.viewer_id,
            ),
        ],
        at_ms=10_000,
    )
    assert written == 3

    by_title = {item.title: item for item in await store.viewer_candidates.list_candidates()}
    archive_before = len(await store.archive.list_lines(live_session_id=session_id))
    commitments_before = await store.commitments.count_commitments()

    approved = await store.viewer_candidates.approve(
        candidate_id=by_title["ta 平时做木工"].id, title="ta 平时爱做木工", lasting=True, at_ms=12_000
    )
    assert (approved.status, approved.title, approved.archive_line_id, approved.commitment_id) == (
        "approved",
        "ta 平时爱做木工",
        None,
        None,
    )
    assert len(await store.archive.list_lines(live_session_id=session_id)) == archive_before
    assert await store.commitments.count_commitments() == commitments_before
    assert [n.title for n in await store.viewers.approved_viewer_notes(viewer_ids=[viewer.viewer_id])] == [
        "ta 平时爱做木工"
    ]

    # 非 viewer_self 或未批准的候选不可撤销批准
    with pytest.raises(MemoryReviewError) as conflict_kind:
        await store.viewer_candidates.revoke(candidate_id=by_title["小狗取名木头"].id, at_ms=12_500)
    assert conflict_kind.value.kind == "conflict"

    revoked = await store.viewer_candidates.revoke(candidate_id=approved.id, at_ms=13_000)
    assert (revoked.status, revoked.decided_at_ms, revoked.created_at_ms) == ("pending", None, 13_000)
    assert await store.viewers.approved_viewer_notes(viewer_ids=[viewer.viewer_id]) == []

    # 再次批准一条、驳回另一条：验证全局去重键与老观众个人去重键互不串扰
    await store.viewer_candidates.approve(candidate_id=approved.id, at_ms=14_000)
    await store.viewer_candidates.reject(candidate_id=by_title["ta 周末常去徒步"].id, at_ms=14_500)
    assert await store.viewer_candidates.rejected_keys() == ([], [])
    assert await store.sessions.rejected_candidate_keys() == ([], [])
    self_titles, rejected_chats = await store.sessions.decided_viewer_self_keys()
    assert sorted(self_titles[viewer.viewer_id]) == ["ta 周末常去徒步", "ta 平时爱做木工"]
    assert rejected_chats == [chat_2]

    # 删除候选（任意状态）与审计空文本保护
    await store.viewer_candidates.delete_candidate(candidate_id=by_title["ta 周末常去徒步"].id, at_ms=15_000)
    with pytest.raises(MemoryReviewError) as missing_err:
        await store.viewer_candidates.delete_candidate(candidate_id=by_title["ta 周末常去徒步"].id, at_ms=15_100)
    assert missing_err.value.kind == "not_found"
    assert all(before == "" and after == "" for _target, _action, before, after in _edits(store))


async def test_viewer_presence_stats_tiers_former_names_and_authored_archive_lines(
    store: SQLiteDatabase,
) -> None:
    """老观众分层（close/regular/none）、曾用名过滤与观众共同造梗档案行归属查询。"""
    s1 = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=s1, ended_at_ms=2_000)
    s2 = await store.sessions.insert_live_session(started_at_ms=3_000, kind="live")

    # 小夏：改过多次昵称（含占位名与单字名），上过舰（非模拟） -> close，曾用名只留最近 2 个合法非当前昵称
    v_close = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_close",
        user_name="阿",
        timestamp_ms=1_000,
        name_match_window_ms=0,
    )
    await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_close",
        user_name="未知用户",
        timestamp_ms=1_100,
        name_match_window_ms=0,
    )
    await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_close",
        user_name="旧名一号",
        timestamp_ms=1_200,
        name_match_window_ms=0,
    )
    await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_close",
        user_name="旧名二号",
        timestamp_ms=1_300,
        name_match_window_ms=0,
    )
    await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_close",
        user_name="旧名三号",
        timestamp_ms=1_400,
        name_match_window_ms=0,
    )
    await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_close",
        user_name="小夏",
        timestamp_ms=1_500,
        name_match_window_ms=0,
    )
    await store.execute(
        "INSERT INTO guards(live_session_id, timestamp_ms, platform, user_id, user_name, viewer_id, simulated)"
        " VALUES (?, 1600, 'bilibili', 'u_close', '小夏', ?, 0)",
        (s1, v_close.viewer_id),
    )

    stats_close = await store.viewers.resolve_presence_stats(viewer_id=v_close.viewer_id, current_session_id=s2)
    assert stats_close is not None
    assert stats_close.has_guard is True
    assert stats_close.familiarity_tier == "close"
    assert stats_close.former_names == ("旧名三号", "旧名二号")

    # 老陈：过往 2 场 + 被回复 3 次 -> regular；标上待遗忘后立即降为 none
    v_reg = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_reg",
        user_name="老陈",
        timestamp_ms=1_000,
        name_match_window_ms=0,
    )
    await store.execute(
        "UPDATE viewers SET sessions_count=2, last_session_id=?, replied_count=3 WHERE id=?",
        (s1, v_reg.viewer_id),
    )
    stats_reg = await store.viewers.resolve_presence_stats(viewer_id=v_reg.viewer_id, current_session_id=s2)
    assert stats_reg is not None and stats_reg.familiarity_tier == "regular"

    await store.execute("UPDATE viewers SET forget_requested_at_ms=2500 WHERE id=?", (v_reg.viewer_id,))
    stats_forgetting = await store.viewers.resolve_presence_stats(viewer_id=v_reg.viewer_id, current_session_id=s2)
    assert stats_forgetting is not None and stats_forgetting.familiarity_tier == "none"

    # 共同造梗档案行归属：只查挂在该观众弹幕上的 source='viewer' 档案行
    said_close = await store.chat.insert_live_chat(
        live_session_id=s1,
        timestamp_ms=1_700,
        sender_role="viewer",
        viewer_id=v_close.viewer_id,
        content="水獭叫圆圆吧",
        message_type="danmaku",
    )
    reply_close = await store.chat.insert_live_chat(
        live_session_id=s1,
        timestamp_ms=1_800,
        sender_role="assistant",
        content="好，水獭就叫圆圆",
        message_type="speech",
    )
    await store.sessions.apply_settlement(
        live_session_id=s1,
        archive_lines=[PendingArchiveLine(text="修好风车", quote="风车", evidence_chat_id=reply_close + 50)],
        commitments=[],
        closures=[],
        settled_at_ms=1_900,
        now_ms_value=1_900,
    )
    _write(
        store,
        s1,
        [
            PendingViewerCandidate(
                kind="viewer_defined",
                title="水獭取名圆圆",
                viewer_chat_id=said_close,
                viewer_quote="水獭叫圆圆",
                word="圆圆",
                reply_chat_id=reply_close,
                reply_quote="水獭就叫圆圆",
                viewer_id=v_close.viewer_id,
            )
        ],
    )
    (cand,) = await store.viewer_candidates.list_candidates()
    await store.viewer_candidates.approve(candidate_id=cand.id, at_ms=2_000)

    authored = await store.archive.list_viewer_authored_lines(viewer_ids=[v_close.viewer_id])
    assert [line.text for line in authored] == ["水獭取名圆圆"]
    assert await store.archive.list_viewer_authored_lines(viewer_ids=[v_reg.viewer_id]) == []
    assert await store.archive.list_viewer_authored_lines(viewer_ids=[]) == []
