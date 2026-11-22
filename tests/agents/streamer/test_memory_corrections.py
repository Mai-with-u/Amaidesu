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
from src.modules.agents import AgentManager, BaseAgent, LeadCoordinator, LeadHandoverContext
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.llm.payload import Response
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import PendingArchiveLine
from src.modules.storage.privacy import PrivacyService
from src.modules.tools import ToolInvocation, ToolSpec
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
    """观众弹幕作证一律打回；游戏回执与玩家游戏公屏核对通过后当场改简报/关承诺，且支持人工撤回。"""
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

        # 4. 凭玩家游戏公屏更正承诺 → 承诺当场标 closed 且 close_source='correction'，退出【还惦记的事】
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
        assert comm_id not in brief.commitment_ids
        assert f"- [约#{comm_id}] 本场已了结：玩家甲已帮铺完河边木桥（原约定：把河边木桥铺完）" in brief.text

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
        assert comm_id in brief_after_revert.commitment_ids
        assert f"[约#{comm_id}]" in brief_after_revert.text
        assert "本场已了结" not in brief_after_revert.text
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_operator_prompt_persists_in_session_brief_across_multiple_windows(tmp_path: Path) -> None:
    """运营递话（source=operator）落成本场常驻更正提醒，跨多个决策回合不消失；跨 Agent 递话不混入。"""
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

            # 第二窗没有任何新递话，记忆摘要顶部依然保留运营提醒
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


def test_memory_correct_visibility_follows_lead_agent_and_degrades_on_error() -> None:
    """streamer_memory_correct 默认仅主播可见；切游戏主控后刷新可见名单同步开放，回调异常降级为仅主播。"""
    current_lead = ""
    provider = MemoryCorrectionToolProvider(
        correction_repo=MagicMock(),
        session_provider=lambda: 1,
        brief_archive_ids_provider=lambda: (),
        lead_agent_provider=lambda: current_lead,
    )
    registry = ToolRegistry()
    registry.register_provider(provider)

    assert [s.full_name for s in registry.list_tools(for_agent="streamer")] == ["streamer_memory_correct"]
    assert registry.list_tools(for_agent="demo_game") == []

    current_lead = "demo_game"
    registry.refresh_provider_tools(provider)
    assert [s.full_name for s in registry.list_tools(for_agent="demo_game")] == ["streamer_memory_correct"]
    assert [s.full_name for s in registry.list_tools(for_agent="streamer")] == ["streamer_memory_correct"]

    current_lead = ""
    registry.refresh_provider_tools(provider)
    assert registry.list_tools(for_agent="demo_game") == []

    def _boom() -> str:
        raise RuntimeError("lead probe boom")

    broken_provider = MemoryCorrectionToolProvider(
        correction_repo=MagicMock(),
        session_provider=lambda: 1,
        brief_archive_ids_provider=lambda: (),
        lead_agent_provider=_boom,
    )
    broken_reg = ToolRegistry()
    broken_reg.register_provider(broken_provider)
    assert [s.full_name for s in broken_reg.list_tools(for_agent="streamer")] == ["streamer_memory_correct"]
    assert broken_reg.list_tools(for_agent="demo_game") == []


async def test_memory_correct_progress_updates_brief_and_rejects_archive(tmp_path: Path) -> None:
    """verdict=progress 仅适用于承诺（打回档案行），凭物品交付回执记录分期进展后保持 open 并在简报呈现，结案后转入本场已了结。"""
    db = SQLiteDatabase(tmp_path / "corr_progress.db")
    await db.initialize()
    try:
        seeded = await _seed_prev_and_current_sessions(db)
        curr_session = seeded["curr_session"]
        line_id = seeded["line_id"]
        comm_id = seeded["commitment_id"]
        saves = {"minecraft": GameSave(id="save-a", name="新世界")}

        await db.events.insert_game_event(
            live_session_id=curr_session,
            timestamp_ms=10_020_000,
            game="minecraft",
            event_type="item_delivery",
            message="已向河边桥头箱子存入橡木板 x32",
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

        async def _refresh() -> None:
            nonlocal brief
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
            on_written=_refresh,
        )

        # 对档案行传 progress 必须清晰打回
        r_arch = await provider.invoke(
            ToolInvocation(
                tool_name="streamer_memory_correct",
                arguments={
                    "target": f"档#{line_id}",
                    "verdict": "progress",
                    "summary": "铺了一半",
                    "evidence_kind": "self_seen",
                },
            )
        )
        assert not r_arch.success
        assert "仅适用于承诺" in (r_arch.error_message or "")

        # 凭物品交付回执记录本场进展：保持 open，返回清晰 effect，简报行尾附本场进展
        r_prog = await provider.invoke(
            ToolInvocation(
                tool_name="streamer_memory_correct",
                arguments={
                    "target": f"约#{comm_id}",
                    "verdict": "progress",
                    "summary": "已运送一半橡木板，待铺完",
                    "evidence_kind": "game_receipt",
                    "evidence_quote": "向河边桥头箱子存入橡木板 x32",
                },
            )
        )
        assert r_prog.success
        assert r_prog.structured_content is not None
        assert (
            f"约#{comm_id} 已记录本场进展（仍未了结）：已运送一半橡木板，待铺完" == r_prog.structured_content["effect"]
        )
        comm_mid = await db.commitments.get_commitment(commitment_id=comm_id)
        assert comm_mid is not None and comm_mid.status == "open"
        assert comm_id in brief.commitment_ids
        assert "（本场进展：已运送一半橡木板，待铺完）" in brief.text

        # 随后标 done 结案：立即从 open 转入本场已了结
        r_done = await provider.invoke(
            ToolInvocation(
                tool_name="streamer_memory_correct",
                arguments={
                    "target": f"约#{comm_id}",
                    "verdict": "done",
                    "summary": "河边木桥已全段铺完",
                    "evidence_kind": "self_seen",
                },
            )
        )
        assert r_done.success
        comm_final = await db.commitments.get_commitment(commitment_id=comm_id)
        assert comm_final is not None and comm_final.status == "closed"
        assert comm_id not in brief.commitment_ids
        assert f"- [约#{comm_id}] 本场已了结：河边木桥已全段铺完（原约定：把河边木桥铺完）" in brief.text
    finally:
        await db.close()


async def test_streamer_agent_lead_syncs_memory_correct_visibility_and_commitment_ledger(tmp_path: Path) -> None:
    """StreamerAgent 随主控切换刷新 streamer_memory_correct 可见性，并向 LeadCoordinator 暴露当前存档与在场玩家的权威承诺账本。"""

    class _LeadStubGame(BaseAgent):
        name = "demo_game"
        description = "测主控承诺账本与更正工具开放"
        supports_autonomous_lead = True

        def __init__(self) -> None:
            super().__init__(heartbeat_interval_ms=0)

        def list_tools(self) -> list[ToolSpec]:
            return []

        def can_take_lead(self) -> bool:
            return True

        async def enter_lead(self, context: LeadHandoverContext) -> bool:
            del context
            return True

        async def yield_lead(self, *, reason: str = "") -> str:
            del reason
            return ""

    db = SQLiteDatabase(tmp_path / "lead_ledger.db")
    await db.initialize()
    bus = EventBus()
    registry = ToolRegistry()
    try:
        # 铺上一场与本场：含 PlayerA 承诺、当前存档公开承诺、别的存档公开承诺、观众私向承诺
        s1 = await db.sessions.insert_live_session(started_at_ms=1_000_000, kind=SESSION_KIND_LIVE)
        await db.sessions.close_live_session(live_session_id=s1, ended_at_ms=5_000_000)
        s2 = await db.sessions.insert_live_session(started_at_ms=10_000_000, kind=SESSION_KIND_LIVE)

        player_a = await db.game_players.meet_player(
            game="demo_game", uuid="uuid-pa", name="PlayerA", live_session_id=s1, at_ms=2_000_000
        )
        viewer = await db.viewers.resolve_viewer(
            platform="bilibili",
            id_kind="account",
            external_id="v-1",
            user_name="观众甲",
            timestamp_ms=2_000_000,
            name_match_window_ms=0,
        )
        c_player = await db.commitments.insert_commitment(
            what="补还 3 颗钻石",
            quote="下次补还 3 颗钻石",
            game="demo_game",
            save_id="save-a",
            save_name="一档",
            created_session_id=s1,
            now_ms_value=2_100_000,
        )
        c_public = await db.commitments.insert_commitment(
            what="修通北岸木桥",
            quote="下次修通北岸木桥",
            game="demo_game",
            save_id="save-a",
            save_name="一档",
            created_session_id=s1,
            now_ms_value=2_200_000,
        )
        c_other_save = await db.commitments.insert_commitment(
            what="扩建二档谷仓",
            quote="二档扩建谷仓",
            game="demo_game",
            save_id="save-b",
            save_name="二档",
            created_session_id=s1,
            now_ms_value=2_300_000,
        )
        c_viewer = await db.commitments.insert_commitment(
            what="给观众甲唱一首歌",
            quote="下次给你唱歌",
            viewer_id=viewer.viewer_id,
            game="demo_game",
            save_id="save-a",
            created_session_id=s1,
            now_ms_value=2_400_000,
        )
        assert c_player and c_public and c_other_save and c_viewer
        with db.manager.transaction() as conn:
            conn.execute("UPDATE commitments SET target_player_id=? WHERE id=?", (player_a.id, c_player))

        mgr = AgentManager()
        game_agent = _LeadStubGame()
        mgr.register(game_agent)
        await game_agent.start()
        coord = LeadCoordinator(mgr, event_bus=bus, enabled=True)

        session_mgr = MagicMock()
        session_mgr.active_pk = s2
        session_mgr.active_kind = SESSION_KIND_LIVE

        agent = StreamerAgent(
            config=StreamerConfig(lead_mode="dynamic"),
            llm_manager=MagicMock(),
            prompt_manager=stub_prompt(),
            event_bus=bus,
            tool_registry=registry,
            sessions_repo=db.sessions,
            chat_repo=db.chat,
            commitments_repo=db.commitments,
            archive_repo=db.archive,
            event_repo=db.events,
            game_player_repo=db.game_players,
            viewer_repo=db.viewers,
            session_manager=session_mgr,
            lead_coordinator=coord,
        )
        await agent.start()
        try:
            # 1) 主播主控期：demo_game 看不到 streamer_memory_correct
            assert "streamer_memory_correct" in [s.full_name for s in registry.list_tools(for_agent="streamer")]
            assert "streamer_memory_correct" not in [s.full_name for s in registry.list_tools(for_agent="demo_game")]

            # 2) 切到游戏主控后：demo_game 动态获得 streamer_memory_correct 权限
            assert (
                await coord.request_game_lead(LeadHandoverContext(segment_id="play"), target="demo_game") == "demo_game"
            )
            assert "streamer_memory_correct" in [s.full_name for s in registry.list_tools(for_agent="demo_game")]

            # 3) 拉取权威承诺账本：含 PlayerA 承诺与同存档公开承诺，排除他档承诺与观众私向承诺
            ledger_1 = await coord.fetch_commitment_ledger(
                game="demo_game", save_id="save-a", player_uuids=("uuid-pa",)
            )
            open_ids = [item["id"] for item in ledger_1["open"]]
            assert open_ids == [f"约#{c_player}", f"约#{c_public}"]
            assert ledger_1["open"][0]["target"] == "PlayerA"
            assert ledger_1["open"][1]["target"] == "公共"
            assert ledger_1["closed_this_session"] == []

            # 4) 记录分期进展 -> 账本 open 条目立即附带 progress
            await db.memory_corrections.insert_and_apply(
                live_session_id=s2,
                target_kind="commitment",
                commitment_id=c_player,
                verdict="progress",
                summary="已还 2 颗，还欠 1 颗",
                evidence_kind="self_seen",
                created_at_ms=10_010_000,
            )
            ledger_2 = await coord.fetch_commitment_ledger(
                game="demo_game", save_id="save-a", player_uuids=("uuid-pa",)
            )
            assert ledger_2["open"][0]["progress"] == "已还 2 颗，还欠 1 颗"

            # 5) 标 done 结案 -> 退出 open，进入 closed_this_session
            await db.memory_corrections.insert_and_apply(
                live_session_id=s2,
                target_kind="commitment",
                commitment_id=c_player,
                verdict="done",
                summary="已还清 3 颗钻石",
                evidence_kind="self_seen",
                created_at_ms=10_020_000,
            )
            ledger_3 = await coord.fetch_commitment_ledger(
                game="demo_game", save_id="save-a", player_uuids=("uuid-pa",)
            )
            assert [item["id"] for item in ledger_3["open"]] == [f"约#{c_public}"]
            assert ledger_3["closed_this_session"] == [
                {"id": f"约#{c_player}", "what": "补还 3 颗钻石", "summary": "已还清 3 颗钻石"}
            ]

            # 6) 收权回主播后：demo_game 立即失去 streamer_memory_correct 可见性
            await coord.reclaim_streamer_lead(reason="test_done")
            assert "streamer_memory_correct" not in [s.full_name for s in registry.list_tools(for_agent="demo_game")]
        finally:
            await agent.cleanup()
            await game_agent.stop()
    finally:
        await bus.cleanup()
        await db.close()


async def test_commitment_ledger_filters_forgotten_players_with_real_privacy_service(tmp_path: Path) -> None:
    """真实 PrivacyService 下：写入 forget_list 或标记 forget_requested_at_ms 的玩家承诺在 open 与 closed_this_session 均严格隔离。"""
    db = SQLiteDatabase(tmp_path / "data" / "ledger_privacy.db")
    await db.initialize()
    privacy = PrivacyService(db.manager, db_path=db.db_path, key_path=tmp_path / "secrets" / "forget.key")
    await privacy.initialize()
    bus = EventBus()
    registry = ToolRegistry()
    try:
        s1 = await db.sessions.insert_live_session(started_at_ms=1_000_000, kind=SESSION_KIND_LIVE)
        await db.sessions.close_live_session(live_session_id=s1, ended_at_ms=5_000_000)
        s2 = await db.sessions.insert_live_session(started_at_ms=10_000_000, kind=SESSION_KIND_LIVE)

        player_a = await db.game_players.meet_player(
            game="demo_game", uuid="uuid-pa", name="PlayerA", live_session_id=s1, at_ms=2_000_000
        )
        player_b = await db.game_players.meet_player(
            game="demo_game", uuid="uuid-pb", name="PlayerB", live_session_id=s1, at_ms=2_000_000
        )
        player_c = await db.game_players.meet_player(
            game="demo_game", uuid="uuid-pc", name="PlayerC", live_session_id=s1, at_ms=2_000_000
        )

        # 每位玩家各有一条 open 承诺和一条本场 done 结案承诺
        async def _seed_player_pair(pid: int, tag: str) -> tuple[int, int]:
            c_open = await db.commitments.insert_commitment(
                what=f"补还 {tag} 钻石",
                quote=f"下次补还 {tag} 钻石",
                game="demo_game",
                save_id="save-a",
                save_name="一档",
                created_session_id=s1,
                now_ms_value=2_100_000,
            )
            c_done = await db.commitments.insert_commitment(
                what=f"归还 {tag} 铁镐",
                quote=f"下次归还 {tag} 铁镐",
                game="demo_game",
                save_id="save-a",
                save_name="一档",
                created_session_id=s1,
                now_ms_value=2_200_000,
            )
            assert c_open and c_done
            with db.manager.transaction() as conn:
                conn.execute("UPDATE commitments SET target_player_id=? WHERE id IN (?, ?)", (pid, c_open, c_done))
            await db.memory_corrections.insert_and_apply(
                live_session_id=s2,
                target_kind="commitment",
                commitment_id=c_done,
                verdict="done",
                summary=f"已归还 {tag} 铁镐",
                evidence_kind="self_seen",
                created_at_ms=10_010_000,
            )
            return int(c_open), int(c_done)

        pa_open, pa_done = await _seed_player_pair(player_a.id, "PlayerA")
        await _seed_player_pair(player_b.id, "PlayerB")
        await _seed_player_pair(player_c.id, "PlayerC")

        # PlayerB：通过真实 PrivacyService 写入 forget_list，并显式清空 forget_requested_at_ms 验证 is_player_forgotten 单独兜底
        disp = await privacy.process_incoming_player_chat(
            game="demo_game", uuid="uuid-pb", name="PlayerB", content="忘了我", at_ms=10_020_000
        )
        assert disp == "forget_command"
        with db.manager.transaction() as conn:
            conn.execute("UPDATE game_players SET forget_requested_at_ms=NULL WHERE id=?", (player_b.id,))
        assert await privacy.is_player_forgotten(game="demo_game", uuid="uuid-pb") is True

        # PlayerC：仅标记 forget_requested_at_ms（未进 forget_list）验证宽限期字段兜底
        with db.manager.transaction() as conn:
            conn.execute("UPDATE game_players SET forget_requested_at_ms=? WHERE id=?", (10_025_000, player_c.id))

        mgr = AgentManager()
        coord = LeadCoordinator(mgr, event_bus=bus, enabled=True)
        session_mgr = MagicMock()
        session_mgr.active_pk = s2
        session_mgr.active_kind = SESSION_KIND_LIVE

        agent = StreamerAgent(
            config=StreamerConfig(lead_mode="dynamic"),
            llm_manager=MagicMock(),
            prompt_manager=stub_prompt(),
            event_bus=bus,
            tool_registry=registry,
            sessions_repo=db.sessions,
            chat_repo=db.chat,
            commitments_repo=db.commitments,
            archive_repo=db.archive,
            event_repo=db.events,
            game_player_repo=db.game_players,
            privacy_service=privacy,
            session_manager=session_mgr,
            lead_coordinator=coord,
        )
        await agent.start()
        try:
            ledger = await coord.fetch_commitment_ledger(
                game="demo_game",
                save_id="save-a",
                player_uuids=("uuid-pa", "uuid-pb", "uuid-pc"),
            )
            assert [item["id"] for item in ledger["open"]] == [f"约#{pa_open}"]
            assert [item["id"] for item in ledger["closed_this_session"]] == [f"约#{pa_done}"]
        finally:
            await agent.cleanup()
    finally:
        await privacy.close()
        await bus.cleanup()
        await db.close()


async def test_brief_closed_commitments_isolates_viewer_other_save_and_forgotten_player(tmp_path: Path) -> None:
    """记忆摘要的本场已了结承诺严格隔离观众私向、他档与遗忘玩家，保留当前存档公开与未遗忘玩家承诺。"""
    db = SQLiteDatabase(tmp_path / "data" / "brief_isolation.db")
    await db.initialize()
    privacy = PrivacyService(db.manager, db_path=db.db_path, key_path=tmp_path / "secrets" / "forget.key")
    await privacy.initialize()
    try:
        s1 = await db.sessions.insert_live_session(started_at_ms=1_000_000, kind=SESSION_KIND_LIVE)
        await db.sessions.close_live_session(live_session_id=s1, ended_at_ms=5_000_000)
        s2 = await db.sessions.insert_live_session(started_at_ms=10_000_000, kind=SESSION_KIND_LIVE)

        player_a = await db.game_players.meet_player(
            game="demo_game", uuid="uuid-pa", name="PlayerA", live_session_id=s1, at_ms=2_000_000
        )
        player_b = await db.game_players.meet_player(
            game="demo_game", uuid="uuid-pb", name="PlayerB", live_session_id=s1, at_ms=2_000_000
        )
        player_c = await db.game_players.meet_player(
            game="demo_game", uuid="uuid-pc", name="PlayerC", live_session_id=s1, at_ms=2_000_000
        )
        viewer = await db.viewers.resolve_viewer(
            platform="bilibili",
            id_kind="account",
            external_id="v-99",
            user_name="观众乙",
            timestamp_ms=2_000_000,
            name_match_window_ms=0,
        )

        # 1) 当前存档公开承诺；2) 当前存档未遗忘玩家承诺；3) 观众私向承诺；4) 别的存档承诺；5) 触发“忘了我”的玩家承诺；6) 仅在 forget_list 的玩家承诺
        c_public = await db.commitments.insert_commitment(
            what="修通东岸木桥",
            game="demo_game",
            save_id="save-a",
            save_name="一档",
            created_session_id=s1,
            now_ms_value=2_100_000,
        )
        c_player_ok = await db.commitments.insert_commitment(
            what="补还 PlayerA 3 颗钻石",
            game="demo_game",
            save_id="save-a",
            save_name="一档",
            created_session_id=s1,
            now_ms_value=2_200_000,
        )
        c_viewer = await db.commitments.insert_commitment(
            what="给观众乙点一首歌",
            viewer_id=viewer.viewer_id,
            game="demo_game",
            save_id="save-a",
            save_name="一档",
            created_session_id=s1,
            now_ms_value=2_300_000,
        )
        c_other_save = await db.commitments.insert_commitment(
            what="扩建二档谷仓",
            game="demo_game",
            save_id="save-b",
            save_name="二档",
            created_session_id=s1,
            now_ms_value=2_400_000,
        )
        c_player_forgotten = await db.commitments.insert_commitment(
            what="帮 PlayerB 建塔楼",
            game="demo_game",
            save_id="save-a",
            save_name="一档",
            created_session_id=s1,
            now_ms_value=2_500_000,
        )
        c_player_list_only = await db.commitments.insert_commitment(
            what="帮 PlayerC 挖矿洞",
            game="demo_game",
            save_id="save-a",
            save_name="一档",
            created_session_id=s1,
            now_ms_value=2_600_000,
        )
        assert c_public and c_player_ok and c_viewer and c_other_save and c_player_forgotten and c_player_list_only
        with db.manager.transaction() as conn:
            conn.execute("UPDATE commitments SET target_player_id=? WHERE id=?", (player_a.id, c_player_ok))
            conn.execute("UPDATE commitments SET target_player_id=? WHERE id=?", (player_b.id, c_player_forgotten))
            conn.execute("UPDATE commitments SET target_player_id=? WHERE id=?", (player_c.id, c_player_list_only))

        for idx, (cid, summary) in enumerate(
            [
                (c_public, "东岸木桥已铺通"),
                (c_player_ok, "已还清 PlayerA 3 颗钻石"),
                (c_viewer, "已唱完点歌"),
                (c_other_save, "二档谷仓已扩建"),
                (c_player_forgotten, "塔楼已完工"),
                (c_player_list_only, "矿洞已挖好"),
            ],
            start=1,
        ):
            await db.memory_corrections.insert_and_apply(
                live_session_id=s2,
                target_kind="commitment",
                commitment_id=cid,
                verdict="done",
                summary=summary,
                evidence_kind="self_seen",
                created_at_ms=10_000_000 + idx * 1_000,
            )

        # PlayerB 本场发“忘了我”（同时写 forget_list 与 forget_requested_at_ms）
        assert (
            await privacy.process_incoming_player_chat(
                game="demo_game", uuid="uuid-pb", name="PlayerB", content="忘了我", at_ms=10_020_000
            )
            == "forget_command"
        )
        # PlayerC 仅保留 forget_list 记录（清掉 forget_requested_at_ms），验证 is_player_forgotten 生效
        assert (
            await privacy.process_incoming_player_chat(
                game="demo_game", uuid="uuid-pc", name="PlayerC", content="忘了我", at_ms=10_021_000
            )
            == "forget_command"
        )
        with db.manager.transaction() as conn:
            conn.execute("UPDATE game_players SET forget_requested_at_ms=NULL WHERE id=?", (player_c.id,))

        brief = await build_brief(
            archive_repo=db.archive,
            commitments_repo=db.commitments,
            live_session_id=s2,
            chat_repo=db.chat,
            current_saves={"demo_game": GameSave(id="save-a", name="一档")},
            correction_repo=db.memory_corrections,
            game_player_repo=db.game_players,
            privacy_service=privacy,
        )
        assert f"- [约#{c_public}] 本场已了结：东岸木桥已铺通（原约定：修通东岸木桥）" in brief.text
        assert (
            f"- [约#{c_player_ok}] 本场已了结：已还清 PlayerA 3 颗钻石（原约定：补还 PlayerA 3 颗钻石）" in brief.text
        )
        assert f"约#{c_viewer}" not in brief.text
        assert f"约#{c_other_save}" not in brief.text
        assert f"约#{c_player_forgotten}" not in brief.text
        assert f"约#{c_player_list_only}" not in brief.text
    finally:
        await privacy.close()
        await db.close()
