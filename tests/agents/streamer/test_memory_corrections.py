"""场内记忆更正（D2-4）回归测试：工具校验、简报附注、承诺当场关闭与撤回、运营提醒常驻、下播整理衔接。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict
from unittest.mock import MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.memory.brief import build_brief
from src.agents.streamer.memory.settle import SessionSettler
from src.agents.streamer.streamer_agent import StreamerAgent
from src.agents.streamer.tools.memory_correct import MemoryCorrectionToolProvider
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.llm.payload import Response
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import PendingArchiveLine
from src.modules.tools import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.types.game_save import GameSave
from src.modules.types.session_kind import SESSION_KIND_LIVE
from tests.agents.streamer.helpers import stub_prompt
from tests.modules.llm.fakes import FakeLLMManager


async def _seed_prev_and_current_sessions(db: SQLiteDatabase) -> Dict[str, int]:
    """铺上一场（含快照与未了结承诺）与当前直播场。"""
    s1 = await db.sessions.insert_live_session(started_at_ms=1_000_000, kind=SESSION_KIND_LIVE)
    c_prev = await db.chat.insert_live_chat(
        live_session_id=s1,
        timestamp_ms=1_100_000,
        sender_role="assistant",
        sender_name="麦麦",
        content="河边那块墓碑我下次去收，营地东门还没封顶",
        message_type="speech",
    )
    c_curr = await db.chat.insert_live_chat(
        live_session_id=s1,
        timestamp_ms=1_200_000,
        sender_role="assistant",
        sender_name="麦麦",
        content="答应大家把河边木桥铺完",
        message_type="speech",
    )
    await db.sessions.close_live_session(live_session_id=s1, ended_at_ms=5_000_000)

    await db.sessions.apply_settlement(
        live_session_id=s1,
        settled_at_ms=5_010_000,
        now_ms_value=5_010_000,
        archive_lines=[
            PendingArchiveLine(
                text="Minecraft：营地东门还没封顶",
                quote="营地东门还没封顶",
                evidence_chat_id=int(c_prev),
                game="minecraft",
                save_id="save-a",
                save_name="新世界",
                lasting=False,
                in_snapshot=True,
            )
        ],
        commitments=[],
        closures=[],
        settle_report="{}",
    )
    comm_id = await db.commitments.insert_commitment(
        what="把河边木桥铺完",
        quote="答应大家把河边木桥铺完",
        evidence_chat_id=int(c_curr),
        viewer_id=None,
        game="minecraft",
        save_id="save-a",
        save_name="新世界",
        created_session_id=s1,
        now_ms_value=1_200_000,
    )
    assert comm_id is not None
    snap = await db.archive.latest_snapshot(kind=SESSION_KIND_LIVE, min_duration_ms=0)
    assert snap is not None and len(snap.lines) == 1
    line_id = int(snap.lines[0].line.id)

    s2 = await db.sessions.insert_live_session(started_at_ms=10_000_000, kind=SESSION_KIND_LIVE)
    return {"prev_session": s1, "curr_session": s2, "line_id": line_id, "commitment_id": int(comm_id)}


@pytest.mark.asyncio
async def test_memory_correct_tool_rejects_viewer_danmaku_and_applies_valid_evidence(tmp_path: Path) -> None:
    """观众弹幕作证一律打回；游戏回执与玩家公屏核对通过后当场改简报/关承诺，且支持人工撤回。"""
    db = SQLiteDatabase(tmp_path / "corr.db")
    await db.initialize()
    try:
        seeded = await _seed_prev_and_current_sessions(db)
        curr_session = seeded["curr_session"]
        line_id = seeded["line_id"]
        comm_id = seeded["commitment_id"]
        saves = {"minecraft": GameSave(id="save-a", name="新世界")}

        await db.chat.insert_live_chat(
            live_session_id=curr_session,
            timestamp_ms=10_010_000,
            sender_role="viewer",
            sender_name="观众乙",
            content="东门早就封好顶啦",
            message_type="danmaku",
            message_id="dm-99",
        )
        player = await db.game_players.meet_player(
            game="minecraft",
            uuid="uuid-player-a",
            name="玩家甲",
            live_session_id=curr_session,
            at_ms=10_015_000,
        )
        await db.game_players.add_game_chat(
            live_session_id=curr_session,
            game="minecraft",
            direction="player",
            player_id=player.id,
            content="河边木桥我已经帮你铺完啦",
            at_ms=10_020_000,
            message_id="gc-77",
        )
        await db.events.insert_game_event(
            live_session_id=curr_session,
            timestamp_ms=10_030_000,
            game="minecraft",
            event_type="milestone",
            message="里程碑达成：营地东门封顶完工",
            save_id="save-a",
            save_name="新世界",
        )

        brief = await build_brief(
            archive_repo=db.archive,
            commitments_repo=db.commitments,
            live_session_id=curr_session,
            chat_repo=db.chat,
            current_saves=saves,
            correction_repo=db.memory_corrections,
        )
        assert f"[档#{line_id}]" in brief.text
        assert f"[约#{comm_id}]" in brief.text

        refreshed_count = 0

        async def _on_written() -> None:
            nonlocal brief, refreshed_count
            refreshed_count += 1
            brief = await build_brief(
                archive_repo=db.archive,
                commitments_repo=db.commitments,
                live_session_id=curr_session,
                chat_repo=db.chat,
                current_saves=saves,
                correction_repo=db.memory_corrections,
            )

        provider = MemoryCorrectionToolProvider(
            correction_repo=db.memory_corrections,
            session_provider=lambda: curr_session,
            brief_archive_ids_provider=lambda: brief.archive_ids,
            commitments_repo=db.commitments,
            event_repo=db.events,
            game_player_repo=db.game_players,
            chat_repo=db.chat,
            overview_provider=lambda: "附近建筑：东门已装木屋顶",
            on_written=_on_written,
        )

        # 1. 弹幕类型作证直接打回
        r_dm = await provider.invoke(
            ToolInvocation(
                tool_name="streamer_memory_correct",
                arguments={
                    "target": f"档#{line_id}",
                    "verdict": "done",
                    "summary": "东门已封顶",
                    "evidence_kind": "danmaku",
                    "evidence_quote": "东门早就封好顶啦",
                },
            )
        )
        assert not r_dm.success
        assert "批准" in (r_dm.error_message or "")

        # 2. 冒填观众弹幕 message_id 伪装成 player_chat 也打回
        r_fake_player = await provider.invoke(
            ToolInvocation(
                tool_name="streamer_memory_correct",
                arguments={
                    "target": f"档#{line_id}",
                    "verdict": "done",
                    "summary": "东门已封顶",
                    "evidence_kind": "player_chat",
                    "evidence_quote": "东门早就封好顶啦",
                    "evidence_message_id": "dm-99",
                },
            )
        )
        assert not r_fake_player.success
        assert "观众弹幕" in (r_fake_player.error_message or "")

        # 3. 凭游戏回执更正档案行 → 简报当场附注"（本场更正：已做完，东门已封顶完工）"
        r_arch = await provider.invoke(
            ToolInvocation(
                tool_name="streamer_memory_correct",
                arguments={
                    "target": f"档#{line_id}",
                    "verdict": "done",
                    "summary": "东门已封顶完工",
                    "evidence_kind": "game_receipt",
                    "evidence_quote": "营地东门封顶完工",
                },
            )
        )
        assert r_arch.success
        assert refreshed_count == 1
        assert "（本场更正：做完了——东门已封顶完工）" in brief.text

        # 4. 凭玩家公屏更正承诺 → 承诺当场标 closed 且 close_source='correction'，退出【还惦记的事】
        r_comm = await provider.invoke(
            ToolInvocation(
                tool_name="streamer_memory_correct",
                arguments={
                    "target": f"约#{comm_id}",
                    "verdict": "done",
                    "summary": "玩家甲已帮铺完河边木桥",
                    "evidence_kind": "player_chat",
                    "evidence_quote": "河边木桥我已经帮你铺完啦",
                    "evidence_message_id": "gc-77",
                },
            )
        )
        assert r_comm.success
        comm_after = await db.commitments.get_commitment(commitment_id=comm_id)
        assert comm_after is not None
        assert comm_after.status == "closed"
        assert comm_after.close_source == "correction"
        assert f"[约#{comm_id}]" not in brief.text

        # 5. 人工撤回该承诺更正 → 承诺恢复 open、close_source 清空，并记入 memory_edits
        assert r_comm.structured_content is not None
        comm_corr_id = int(r_comm.structured_content["correction_id"])
        reverted = await db.memory_corrections.revert_correction(correction_id=comm_corr_id, reverted_at_ms=10_050_000)
        assert reverted is True
        comm_restored = await db.commitments.get_commitment(commitment_id=comm_id)
        assert comm_restored is not None
        assert comm_restored.status == "open"
        assert comm_restored.close_source == ""

        brief_after_revert = await build_brief(
            archive_repo=db.archive,
            commitments_repo=db.commitments,
            live_session_id=curr_session,
            chat_repo=db.chat,
            current_saves=saves,
            correction_repo=db.memory_corrections,
        )
        assert f"[约#{comm_id}]" in brief_after_revert.text
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_operator_prompt_persists_in_session_brief_across_multiple_windows(tmp_path: Path) -> None:
    """运营递话（source=operator）落成本场常驻更正提醒，跨多个决策窗不消失；跨 Agent 递话不混入。"""
    db = SQLiteDatabase(tmp_path / "op_note.db")
    await db.initialize()
    bus = EventBus()
    try:
        seeded = await _seed_prev_and_current_sessions(db)
        curr_session = seeded["curr_session"]

        session_mgr = MagicMock()
        session_mgr.active_pk = curr_session
        session_mgr.active_kind = SESSION_KIND_LIVE

        agent = StreamerAgent(
            config=StreamerConfig(),
            llm_manager=MagicMock(),
            prompt_manager=stub_prompt(),
            event_bus=bus,
            tool_registry=ToolRegistry(),
            sessions_repo=db.sessions,
            chat_repo=db.chat,
            commitments_repo=db.commitments,
            archive_repo=db.archive,
            event_repo=db.events,
            session_manager=session_mgr,
        )
        await agent.start()
        try:
            await bus.emit(
                CoreEvents.LIVE_STARTED,
                LiveStartedPayload(
                    live_session_id=curr_session,
                    started_at_ms=10_000_000,
                    kind=SESSION_KIND_LIVE,
                    resumed=False,
                ),
                source="test",
            )
            await bus.wait_until_drained()

            assert agent.receive_prompt(content="已经换了新世界，旧墓碑不用再找了", source="operator")
            assert agent.receive_prompt(content="内部状态同步消息", source="minecraft")

            # 第一窗重渲简报
            await agent.refresh_memory_brief()
            first_brief = agent.memory_brief_text
            assert "【运营本场提醒过】" in first_brief
            assert "已经换了新世界，旧墓碑不用再找了" in first_brief
            assert "内部状态同步消息" not in first_brief

            # 第二窗没有任何新递话，常驻简报顶部依然保留运营提醒
            await agent.refresh_memory_brief()
            second_brief = agent.memory_brief_text
            assert "【运营本场提醒过】" in second_brief
            assert "已经换了新世界，旧墓碑不用再找了" in second_brief
        finally:
            await agent.cleanup()
    finally:
        await bus.cleanup()
        await db.close()


@pytest.mark.asyncio
async def test_settler_accepts_backed_correction_drop_and_flags_unhandled_archive_correction(tmp_path: Path) -> None:
    """下播整理：有外部依据的更正可作 drop 依据，未处理的档案更正记入未登记清单并标 unhandled。"""
    db = SQLiteDatabase(tmp_path / "settle_corr.db")
    await db.initialize()
    try:
        s1 = await db.sessions.insert_live_session(started_at_ms=1_000_000, kind=SESSION_KIND_LIVE)
        c1 = await db.chat.insert_live_chat(
            live_session_id=s1,
            timestamp_ms=1_100_000,
            sender_role="assistant",
            sender_name="麦麦",
            content="旧世界河边墓碑还没捡",
            message_type="speech",
        )
        c2 = await db.chat.insert_live_chat(
            live_session_id=s1,
            timestamp_ms=1_110_000,
            sender_role="assistant",
            sender_name="麦麦",
            content="山顶灯塔也还没修",
            message_type="speech",
        )
        await db.sessions.close_live_session(live_session_id=s1, ended_at_ms=5_000_000)
        await db.sessions.apply_settlement(
            live_session_id=s1,
            settled_at_ms=5_005_000,
            now_ms_value=5_005_000,
            archive_lines=[
                PendingArchiveLine(
                    text="Minecraft：旧世界河边墓碑还没捡",
                    quote="旧世界河边墓碑还没捡",
                    evidence_chat_id=int(c1),
                    game="minecraft",
                    save_id="save-a",
                    save_name="新世界",
                    lasting=False,
                    in_snapshot=True,
                ),
                PendingArchiveLine(
                    text="Minecraft：山顶灯塔还没修",
                    quote="山顶灯塔也还没修",
                    evidence_chat_id=int(c2),
                    game="minecraft",
                    save_id="save-a",
                    save_name="新世界",
                    lasting=False,
                    in_snapshot=True,
                ),
            ],
            commitments=[],
            closures=[],
            settle_report="{}",
        )
        snap1 = await db.archive.latest_snapshot(kind=SESSION_KIND_LIVE, min_duration_ms=0)
        assert snap1 is not None and len(snap1.lines) == 2
        line_tomb = int(snap1.lines[0].line.id)
        line_tower = int(snap1.lines[1].line.id)

        # 第 2 场：开场 2 分钟内点名引用了一次旧档案；场内记了两条更正（更正1 针对墓碑，更正2 针对灯塔）
        s2 = await db.sessions.insert_live_session(started_at_ms=10_000_000, kind=SESSION_KIND_LIVE)
        await db.chat.insert_live_chat(
            live_session_id=s2,
            timestamp_ms=10_100_000,
            sender_role="assistant",
            sender_name="麦麦",
            content="今天在新世界重新探索啦",
            message_type="speech",
            memory_refs=json.dumps([f"档#{line_tomb}"], ensure_ascii=False),
        )
        corr1 = await db.memory_corrections.insert_and_apply(
            live_session_id=s2,
            target_kind="archive",
            archive_line_id=line_tomb,
            verdict="invalid",
            summary="已换新世界，旧墓碑不复存在",
            evidence_kind="operator",
            evidence_quote="已经换新世界了",
            source="streamer",
            created_at_ms=10_120_000,
        )
        corr2 = await db.memory_corrections.insert_and_apply(
            live_session_id=s2,
            target_kind="archive",
            archive_line_id=line_tower,
            verdict="done",
            summary="山顶灯塔已修好",
            evidence_kind="game_receipt",
            evidence_quote="山顶灯塔完工",
            source="streamer",
            created_at_ms=10_130_000,
        )
        await db.sessions.close_live_session(live_session_id=s2, ended_at_ms=14_000_000)

        llm = FakeLLMManager(
            responses=[
                Response(
                    success=True,
                    content=json.dumps(
                        {
                            "previous": [
                                {
                                    "old": 1,
                                    "op": "drop",
                                    "reason": "不成立：运营确认已换新世界",
                                    "correction": 1,
                                },
                                {"old": 2, "op": "keep"},
                            ],
                            "archive": [],
                            "viewer_candidates": [],
                        },
                        ensure_ascii=False,
                    ),
                ),
                Response(
                    success=True,
                    content=json.dumps(
                        {"commitments": [], "claimed_missing": [], "player_candidates": []},
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        settler = SessionSettler(
            sessions_repo=db.sessions,
            chat_repo=db.chat,
            commitments_repo=db.commitments,
            archive_repo=db.archive,
            event_repo=db.events,
            llm_service=llm,
            prompt_manager=stub_prompt(),
        )
        settled = await settler.settle_session(s2)
        assert settled is True

        first_prompt = llm.calls[0]["input"]
        assert "【本场更正（必须逐条核对处理）】" in first_prompt
        assert f"档#{line_tomb}（即上一份档案 旧1）" in first_prompt
        assert f"档#{line_tower}（即上一份档案 旧2）" in first_prompt

        snap2 = await db.archive.latest_snapshot(kind=SESSION_KIND_LIVE, min_duration_ms=0)
        assert snap2 is not None
        kept_ids = [int(s.line.id) for s in snap2.lines]
        assert line_tomb not in kept_ids
        assert line_tower in kept_ids

        corr_rows = {row.id: row for row in await db.memory_corrections.list_by_session(live_session_id=s2)}
        assert corr_rows[corr1.id].settled_disposition == "applied"
        assert corr_rows[corr2.id].settled_disposition == "unhandled"

        sess2 = await db.sessions.get_live_session(live_session_id=s2)
        assert sess2 is not None and sess2.settle_report
        report = json.loads(sess2.settle_report)
        unreg = report.get("unregistered") or []
        assert any(f"档#{line_tower}" in item.get("what", "") for item in unreg)
        se = report.get("side_effects") or {}
        assert se.get("opening_mentions_5m") == 1
        assert se.get("corrections_made") == 2
        assert se.get("settle_lag_ms") is not None and se.get("settle_lag_ms") >= 0
    finally:
        await db.close()
