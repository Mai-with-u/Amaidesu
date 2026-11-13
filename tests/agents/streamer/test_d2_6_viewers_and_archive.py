"""观众在场认人、待确认身份场次数合并、档案防误记护栏与 B2 离线复核 CLI（D2-6 + B2）回归测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.review_archive_cli import run_cli
from src.agents.streamer.memory.cues import MemoryCueProvider
from src.agents.streamer.memory.settle import SessionSettler
from src.agents.streamer.room_state import RoomState
from src.modules.llm.payload import Response
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import PendingArchiveLine
from src.modules.types.session_kind import SESSION_KIND_LIVE
from tests.agents.streamer.helpers import stub_prompt
from tests.modules.llm.fakes import FakeLLMManager


@pytest.mark.asyncio
async def test_pending_identity_cluster_merges_sessions_and_enter_only_veteran_is_announced_once(
    tmp_path: Path,
) -> None:
    """待确认（pending）身份关联两行合并场次数达标；只进房不说话的老观众每场提示一次且与欢迎协同。"""
    db = SQLiteDatabase(tmp_path / "viewer_presence.db")
    await db.initialize()
    try:
        # 铺过去 3 个已完结正式场 s1, s2, s3 和当前场 s4
        s1 = await db.sessions.insert_live_session(started_at_ms=1_000_000, kind=SESSION_KIND_LIVE)
        s2 = await db.sessions.insert_live_session(started_at_ms=2_000_000, kind=SESSION_KIND_LIVE)
        s3 = await db.sessions.insert_live_session(started_at_ms=3_000_000, kind=SESSION_KIND_LIVE)
        for sid, end_ms in ((s1, 1_500_000), (s2, 2_500_000), (s3, 3_500_000)):
            await db.sessions.close_live_session(live_session_id=sid, ended_at_ms=end_ms)
        s4 = await db.sessions.insert_live_session(started_at_ms=10_000_000, kind=SESSION_KIND_LIVE)

        # 同名观众乙先以 account_id 在 s1、s2 发言（sessions_count=2）
        res_acc = await db.viewers.resolve_viewer(
            platform="bilibili",
            id_kind="account",
            external_id="uid-1001",
            user_name="观众乙",
            name_match_window_ms=0,
            timestamp_ms=1_100_000,
        )
        for sid, ts in ((s1, 1_100_000), (s2, 2_100_000)):
            await db.chat.insert_live_chat(
                live_session_id=sid,
                timestamp_ms=ts,
                platform="bilibili",
                sender_role="viewer",
                sender_id="uid-1001",
                sender_name="观众乙",
                content="早呀",
                message_type="danmaku",
                viewer_id=res_acc.viewer_id,
                id_kind="account",
            )
            await db.viewers.record_message(
                viewer_id=res_acc.viewer_id,
                timestamp_ms=ts,
                formal_session_id=sid,
            )

        # 后以 open_id 在 s3 发言（未开自动关联时限 -> pending 指向 res_acc.viewer_id，单行 sessions_count=1）
        res_open = await db.viewers.resolve_viewer(
            platform="bilibili",
            id_kind="open",
            external_id="open-2002",
            user_name="观众乙",
            name_match_window_ms=0,
            timestamp_ms=3_100_000,
        )
        assert res_open.outcome == "pending"
        await db.chat.insert_live_chat(
            live_session_id=s3,
            timestamp_ms=3_100_000,
            platform="bilibili",
            sender_role="viewer",
            sender_id="open-2002",
            sender_name="观众乙",
            content="晚上好",
            message_type="danmaku",
            viewer_id=res_open.viewer_id,
            id_kind="open",
        )
        await db.viewers.record_message(
            viewer_id=res_open.viewer_id,
            timestamp_ms=3_100_000,
            formal_session_id=s3,
        )

        # 单看任一行都不到 3 场（2 场 + 1 场），合并簇后应为 3 场
        merged = await db.viewers.resolve_presence_stats(
            platform="bilibili",
            external_id="open-2002",
            current_session_id=s4,
        )
        assert merged is not None
        assert merged.earlier_sessions == 3

        # 在第 4 场，观众乙只进房、一条弹幕都没发
        room_state = RoomState()
        room_state.record_enter("open-2002", "观众乙", now_ms=10_020_000)

        cues = MemoryCueProvider(
            commitments_repo=db.commitments,
            chat_repo=db.chat,
            viewer_repo=db.viewers,
            current_session_provider=lambda: s4,
            archive_repo=db.archive,
            clock=lambda: 10_050_000,
        )

        # 第一窗：空弹幕批次，但携带未欢迎进房名单 -> 给出"常来的 观众乙 进来了"且提醒别欢迎两遍
        round1 = await cues.collect(batch=[], recent_enters=room_state.recent_unwelcomed_enters(now_ms=10_050_000))
        assert "老观众在场" in round1.text
        assert "常来的 观众乙 进来了" in round1.text
        assert "别欢迎两遍" in round1.text

        # 同一场第二窗：即使主播还没来得及欢迎（仍在 recent_unwelcomed_enters），也不重复刷第二遍
        round2 = await cues.collect(batch=[], recent_enters=room_state.recent_unwelcomed_enters(now_ms=10_055_000))
        assert "常来的 观众乙 进来了" not in round2.text

        # 主播开口点名欢迎后，RoomState 自动核销进房名单
        room_state.note_spoken("欢迎观众乙来玩呀！")
        assert room_state.recent_unwelcomed_enters(now_ms=10_060_000) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_settler_rejects_question_quote_and_viewer_nickname_in_archive_title(tmp_path: Path) -> None:
    """下播整理代码拦截两类误记：① 疑问句引文不当事实证据；② 把已知观众昵称误写成事物名进标题。"""
    db = SQLiteDatabase(tmp_path / "archive_guard.db")
    await db.initialize()
    try:
        # 登记一位已知观众昵称（长度 >= 3，走脱敏通用占位名）
        await db.viewers.resolve_viewer(
            platform="bilibili",
            id_kind="account",
            external_id="uid-viewer-c",
            user_name="观众丙的长昵称",
            name_match_window_ms=0,
            timestamp_ms=900_000,
        )

        s1 = await db.sessions.insert_live_session(started_at_ms=1_000_000, kind=SESSION_KIND_LIVE)
        await db.chat.insert_live_chat(
            live_session_id=s1,
            timestamp_ms=1_010_000,
            sender_role="assistant",
            content="观众丙的长昵称？这名字听着跟我那两只羊像一家的",
            message_type="speech",
        )
        await db.chat.insert_live_chat(
            live_session_id=s1,
            timestamp_ms=1_020_000,
            sender_role="assistant",
            content="那座风车是不是已经转起来了？",
            message_type="speech",
        )
        await db.chat.insert_live_chat(
            live_session_id=s1,
            timestamp_ms=1_030_000,
            sender_role="assistant",
            content="今天大家聊了半天早餐吃咸豆花还是甜豆花",
            message_type="speech",
        )
        await db.sessions.close_live_session(live_session_id=s1, ended_at_ms=5_000_000)

        llm = FakeLLMManager(
            responses=[
                Response(
                    success=True,
                    content=json.dumps(
                        {
                            "previous": [],
                            "archive": [
                                {
                                    "line": 1,
                                    "title": "营地新羊叫观众丙的长昵称",
                                    "quote": "这名字听着跟我那两只羊像一家的",
                                    "game": "minecraft",
                                    "lasting": True,
                                },
                                {
                                    "line": 2,
                                    "title": "风车已经转起来了",
                                    "quote": "那座风车是不是已经转起来了？",
                                    "game": "minecraft",
                                    "lasting": False,
                                },
                                {
                                    "line": 3,
                                    "title": "直播间讨论咸甜豆花早餐",
                                    "quote": "今天大家聊了半天早餐吃咸豆花还是甜豆花",
                                    "game": None,
                                    "lasting": False,
                                },
                            ],
                            "viewer_candidates": [],
                        },
                        ensure_ascii=False,
                    ),
                ),
                Response(
                    success=True,
                    content=json.dumps({"commitments": [], "claimed_missing": [], "player_candidates": []}),
                ),
            ]
        )

        settler = SessionSettler(
            sessions_repo=db.sessions,
            chat_repo=db.chat,
            commitments_repo=db.commitments,
            archive_repo=db.archive,
            llm_service=llm,
            prompt_manager=stub_prompt(),
        )
        assert await settler.settle_session(s1) is True

        lines = await db.archive.list_lines(live_session_id=s1)
        assert len(lines) == 1
        assert lines[0].text == "直播间讨论咸甜豆花早餐"
        assert lines[0].game is None

        sess = await db.sessions.get_live_session(live_session_id=s1)
        assert sess is not None and sess.settle_report
        report = json.loads(sess.settle_report)
        reasons = [item.get("reason", "") for item in report.get("unregistered", [])]
        assert any("观众昵称" in r for r in reasons)
        assert any("疑问句" in r for r in reasons)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_review_archive_cli_deletes_line_via_memory_edits(tmp_path: Path) -> None:
    """B2 离线人工复核 CLI：按行号作废错档案行，从快照撤出并在 memory_edits 留下 delete 记录。"""
    db_path = tmp_path / "b2_cli.db"
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        s1 = await db.sessions.insert_live_session(started_at_ms=1_000_000, kind=SESSION_KIND_LIVE)
        c1 = await db.chat.insert_live_chat(
            live_session_id=s1,
            timestamp_ms=1_010_000,
            sender_role="assistant",
            content="这名字听着像一家的",
            message_type="speech",
        )
        await db.sessions.close_live_session(live_session_id=s1, ended_at_ms=4_000_000)
        await db.sessions.apply_settlement(
            live_session_id=s1,
            settled_at_ms=4_010_000,
            now_ms_value=4_010_000,
            archive_lines=[
                PendingArchiveLine(
                    text="营地新羊误记行",
                    quote="这名字听着像一家的",
                    evidence_chat_id=int(c1),
                    game="minecraft",
                    lasting=True,
                    in_snapshot=True,
                )
            ],
            commitments=[],
            closures=[],
        )
        snap_before = await db.archive.latest_snapshot(kind=SESSION_KIND_LIVE, min_duration_ms=0)
        assert snap_before is not None and len(snap_before.lines) == 1
        target_id = int(snap_before.lines[0].line.id)
    finally:
        await db.close()

    assert await run_cli(["inspect", "--db", str(db_path), "--line-id", str(target_id)]) == 0
    assert await run_cli(["delete", "--db", str(db_path), "--line-id", str(target_id)]) == 0

    db_check = SQLiteDatabase(db_path)
    await db_check.initialize()
    try:
        assert await db_check.archive.list_lines_by_ids(line_ids=[target_id]) == []
        snap_after = await db_check.archive.latest_snapshot(kind=SESSION_KIND_LIVE, min_duration_ms=0)
        assert snap_after is None or all(int(item.line.id) != target_id for item in snap_after.lines)
        assert await db_check.memory_review.count_edit_records(live_session_id=s1) == 1
    finally:
        await db_check.close()
