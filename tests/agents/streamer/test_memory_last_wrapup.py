"""开播【上次收尾】（D2-2）：确定性账面拼装、未整理场次说明、头两窗与第一件委派受理收闸。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.delegation_workbench import PLANNER_CALL_SOURCE
from src.agents.streamer.memory.last_wrapup import (
    UNSETTLED_PREVIOUS_NOTE,
    build_last_wrapup,
    classify_ending_delegation,
)
from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv
from src.agents.streamer.room_state import RoomState
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.events import DelegationRecord
from src.modules.tools.models import ToolExecutionResult, ToolSpec
from src.modules.tools.provider import make_provider_from_specs
from src.modules.tools.registry import ToolRegistry
from src.modules.types.game_save import GameSave
from tests.agents.streamer.helpers import ActiveSessionStub, make_streamer_agent
from tests.config.helpers import wait_until
from tests.modules.llm.fakes import FakeLLMManager

_NOW = 1_700_000_000_000
_MIN_MS = 5 * 60_000


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "wrapup.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


def test_classify_ending_delegation_recognizes_d1_and_legacy_auto_close_states() -> None:
    """识别 D1 三种下播自动收尾前缀、旧版无括号自动收尾、在途与中断态；场中已结单不列。"""
    base = {"live_session_id": 1, "task_id": "t1", "game": "minecraft", "initiator": PLANNER_CALL_SOURCE}

    doing = DelegationRecord(**base, status="cancelled", summary="下播自动收尾（当时在做）")
    todo_new = DelegationRecord(**base, status="cancelled", summary="下播自动收尾（待办，还没开工）")
    todo_shelved = DelegationRecord(**base, status="cancelled", summary="下播自动收尾（待办，搁下了，做到：挖好基坑）")
    legacy = DelegationRecord(**base, status="cancelled", summary="下播自动收尾")
    interrupted = DelegationRecord(**base, status="interrupted", summary="进程重启收尾")
    accepted = DelegationRecord(**base, status="accepted", summary="")
    queued = DelegationRecord(**base, status="queued", summary="")
    done = DelegationRecord(**base, status="succeeded", summary="正门装好了")
    cancelled_mid = DelegationRecord(**base, status="cancelled", summary="运营手动取消")

    assert classify_ending_delegation(doing) == "当时在做"
    assert classify_ending_delegation(todo_new) == "待办，还没开工"
    assert classify_ending_delegation(todo_shelved) == "待办，搁下了，做到：挖好基坑"
    assert classify_ending_delegation(legacy) == "下播时未做完"
    assert classify_ending_delegation(interrupted) == "当时在做，被重启打断"
    assert classify_ending_delegation(accepted) == "当时在做"
    assert classify_ending_delegation(queued) == "待办"
    assert classify_ending_delegation(done) is None
    assert classify_ending_delegation(cancelled_mid) is None


async def test_unsettled_previous_session_builds_deterministic_wrapup_and_skips_short_session(
    store: SQLiteDatabase,
) -> None:
    """上一场还没整理完、后面还夹了一场 4 分钟重开短场：跳过短场，从上一正式场确定性拼出存档、委派与最后台词。"""
    s_prev = await store.sessions.insert_live_session(started_at_ms=_NOW - 7_200_000, kind="live")
    await store.sessions.close_live_session(live_session_id=s_prev, ended_at_ms=_NOW - 3_600_000)

    # 4 分钟短场（不满 5 分钟门槛），不应顶掉真正的上一场
    s_short = await store.sessions.insert_live_session(started_at_ms=_NOW - 600_000, kind="live")
    await store.sessions.close_live_session(live_session_id=s_short, ended_at_ms=_NOW - 360_000)

    s_cur = await store.sessions.insert_live_session(started_at_ms=_NOW, kind="live")

    await store.events.insert_game_event(
        live_session_id=s_prev,
        game="minecraft",
        event_type="milestone",
        message="进世界",
        timestamp_ms=_NOW - 7_000_000,
        save_id="w1",
        save_name="橡木谷",
    )
    # 一条已交付（不列），一条当时在做，一条待办搁下，一条运营调试（不列）
    await store.events.insert_delegation_event(
        live_session_id=s_prev,
        game="minecraft",
        task_id="d_done",
        kind="accepted",
        text="搭木桥",
        timestamp_ms=_NOW - 6_500_000,
        initiator=PLANNER_CALL_SOURCE,
    )
    await store.events.insert_delegation_event(
        live_session_id=s_prev,
        game="minecraft",
        task_id="d_done",
        kind="succeeded",
        text="桥通了",
        timestamp_ms=_NOW - 6_000_000,
        initiator=PLANNER_CALL_SOURCE,
    )
    await store.events.insert_delegation_event(
        live_session_id=s_prev,
        game="minecraft",
        task_id="d_doing",
        kind="accepted",
        text="把树场剩下四格补满橡树苗",
        timestamp_ms=_NOW - 5_000_000,
        initiator=PLANNER_CALL_SOURCE,
    )
    await store.events.insert_delegation_event(
        live_session_id=s_prev,
        game="minecraft",
        task_id="d_doing",
        kind="cancelled",
        text="下播自动收尾（当时在做）",
        timestamp_ms=_NOW - 3_600_000,
        initiator=PLANNER_CALL_SOURCE,
    )
    await store.events.insert_delegation_event(
        live_session_id=s_prev,
        game="minecraft",
        task_id="d_todo",
        kind="accepted",
        text="在营地边开垦一块菜地",
        timestamp_ms=_NOW - 4_500_000,
        initiator=PLANNER_CALL_SOURCE,
    )
    await store.events.insert_delegation_event(
        live_session_id=s_prev,
        game="minecraft",
        task_id="d_todo",
        kind="cancelled",
        text="下播自动收尾（待办，搁下了，做到：锄好一半土）",
        timestamp_ms=_NOW - 3_600_000,
        initiator=PLANNER_CALL_SOURCE,
    )
    await store.events.insert_delegation_event(
        live_session_id=s_prev,
        game="minecraft",
        task_id="d_op",
        kind="accepted",
        text="运营后台调试移动",
        timestamp_ms=_NOW - 4_000_000,
        initiator="operator",
    )

    for idx, line_text in enumerate(["第一句早话", "第二句", "树苗还没凑齐", "剩下那四格明天上线再补", "大家晚安啦"]):
        await store.chat.insert_live_chat(
            live_session_id=s_prev,
            timestamp_ms=_NOW - 4_000_000 + idx * 1_000,
            sender_role="assistant",
            sender_name="主播",
            content=line_text,
            message_type="speech",
            utterance_id=f"u_{idx}",
        )
    # 被标记掉落未播出的队尾句不算她说过的话
    await store.chat.insert_live_chat(
        live_session_id=s_prev,
        timestamp_ms=_NOW - 3_990_000,
        sender_role="assistant",
        sender_name="主播",
        content="这句没播出去",
        message_type="speech",
        utterance_id="u_drop",
    )
    await store.chat.mark_utterance_dropped(utterance_id="u_drop", dropped_at_ms=_NOW - 3_989_000)

    res = await build_last_wrapup(
        sessions_repo=store.sessions,
        chat_repo=store.chat,
        event_repo=store.events,
        commitments_repo=store.commitments,
        min_session_ms=_MIN_MS,
        live_session_id=s_cur,
        current_saves={"minecraft": GameSave(id="w1", name="橡木谷")},
    )

    assert res.previous_session_id == s_prev
    assert not res.settled
    assert UNSETTLED_PREVIOUS_NOTE in res.text
    assert "上次最后在 minecraft「橡木谷」（就是眼下这个存档）" in res.text
    assert "下播时未完的游戏活（minecraft·当时在做）：把树场剩下四格补满橡树苗" in res.text
    assert "下播时未完的游戏活（minecraft·待办，搁下了，做到：锄好一半土）：在营地边开垦一块菜地" in res.text
    assert "搭木桥" not in res.text and "运营后台调试" not in res.text
    assert "「树苗还没凑齐」 / 「剩下那四格明天上线再补」 / 「大家晚安啦」" in res.text
    assert "第一句早话" not in res.text and "这句没播出去" not in res.text


async def test_settled_previous_session_includes_new_commitments_and_allows_planner_citation(
    store: SQLiteDatabase,
) -> None:
    """上一场已整理时：不带未整理提示，附带该场新登记的非私向承诺，且 Planner 首窗点名该承诺被放行。"""
    s_prev = await store.sessions.insert_live_session(started_at_ms=_NOW - 7_200_000, kind="live")
    await store.sessions.close_live_session(live_session_id=s_prev, ended_at_ms=_NOW - 3_600_000)
    await store.sessions.mark_settle_done(live_session_id=s_prev, settled_at_ms=_NOW - 3_500_000)

    c_open = await store.commitments.insert_commitment(
        what="把后山矿道修通",
        quote="下次上线先把后山矿道修通",
        game="minecraft",
        save_id="w1",
        save_name="橡木谷",
        created_session_id=s_prev,
        now_ms_value=_NOW - 3_500_000,
    )
    # 对某位具体观众的承诺不在开场对全场念
    v_res = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="v_100",
        user_name="观众甲",
        timestamp_ms=_NOW - 5_000_000,
        name_match_window_ms=0,
    )
    await store.commitments.insert_commitment(
        what="给观众甲搭小木屋",
        quote="下次给你搭小木屋",
        viewer_id=v_res.viewer_id,
        game="minecraft",
        save_id="w1",
        created_session_id=s_prev,
        now_ms_value=_NOW - 3_500_000,
    )

    res = await build_last_wrapup(
        sessions_repo=store.sessions,
        chat_repo=store.chat,
        event_repo=store.events,
        commitments_repo=store.commitments,
        min_session_ms=_MIN_MS,
        current_saves={"minecraft": GameSave(id="w1", name="橡木谷")},
    )
    assert res.settled
    assert UNSETTLED_PREVIOUS_NOTE not in res.text
    assert f"[约#{c_open}] 「下次上线先把后山矿道修通」" in res.text
    assert "观众甲" not in res.text
    assert res.commitment_ids == (c_open,)

    # 验证注入 Planner 参考段且点名 [约#c_open] 合法放行
    captured: List[Any] = []

    async def _fake_gen(messages: List[Any], **kwargs: Any) -> Response:
        captured.append(list(messages))
        return Response(
            success=True,
            content="",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="streamer_reply",
                    arguments={
                        "topic_summary": "开场接上次",
                        "reply_guidance": "提一句矿道",
                        "is_novel": True,
                        "use_memory": [f"约#{c_open}"],
                    },
                )
            ],
        )

    llm = MagicMock()
    llm.supports_vision = MagicMock(return_value=False)
    llm.generate = AsyncMock(side_effect=_fake_gen)
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYS")

    reg = ToolRegistry()
    reply_prov = MagicMock()
    reply_prov.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="streamer_reply",
            success=True,
            structured_content={"speech": "今天先把矿道弄通！", "emotion": {"name": "happy", "intensity": 0.5}},
        )
    )
    spec = ToolSpec(name="reply", description="主播发言出口", kind="sync", provider="streamer")
    reg.register_provider(make_provider_from_specs("streamer", [(spec, lambda inv: reply_prov.invoke(inv))]))

    planner = Planner(
        config=PlannerConfig(),
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=reg,
        env=PlannerEnv(
            reply_provider_factory=lambda: reply_prov,
            commitment_lookup=lambda cid: store.commitments.get_commitment(commitment_id=cid),
            opening_wrapup_provider=AsyncMock(return_value=res),
        ),
    )
    outcome = await planner.plan([], forced=True, proactive=True)
    assert outcome.replied
    assert outcome.memory_refs == [f"约#{c_open}"]
    ref_msg = captured[0][-1]["content"]
    assert "【上次收尾】" in ref_msg


async def test_agent_injects_wrapup_for_at_most_two_windows_or_until_first_delegation(
    store: SQLiteDatabase,
) -> None:
    """StreamerAgent 新开播头两窗注入【上次收尾】、第 3 窗停注；若第 1 窗后受理委派，第 2 窗提前停注；续播不注入。"""
    s_prev = await store.sessions.insert_live_session(started_at_ms=_NOW - 7_200_000, kind="live")
    await store.sessions.close_live_session(live_session_id=s_prev, ended_at_ms=_NOW - 3_600_000)
    await store.chat.insert_live_chat(
        live_session_id=s_prev,
        timestamp_ms=_NOW - 3_700_000,
        sender_role="assistant",
        sender_name="主播",
        content="明天上线接着搭水车",
        message_type="speech",
        utterance_id="u_prev",
    )

    s_cur = await store.sessions.insert_live_session(started_at_ms=_NOW, kind="live")
    session_stub = ActiveSessionStub(active_pk=s_cur, active_kind="live")
    bus = EventBus()
    llm = FakeLLMManager(response=Response(success=True, content="", tool_calls=[]))
    agent = make_streamer_agent(
        {"background": {"enabled": False}, "mouth": {"enabled": False}},
        llm=llm,
        event_bus=bus,
        tool_registry=ToolRegistry(),
        sessions_repo=store.sessions,
        chat_repo=store.chat,
        event_repo=store.events,
        commitments_repo=store.commitments,
        archive_repo=store.archive,
        session_manager=session_stub,
    )

    def _planner_refs() -> List[str]:
        return [
            "\n".join(str(msg.get("content", "")) for msg in call["input"] if msg.get("role") == "user")
            for call in llm.calls
            if call.get("profile") == "planner"
        ]

    await agent.start()
    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=s_cur, kind="live", started_at_ms=_NOW, resumed=False),
            source="test",
        )
        await wait_until(agent.is_live_active, what="首场开播就绪")

        # 第 1 窗、第 2 窗都有【上次收尾】，第 3 窗自动收闸（经公开递话入口唤醒决策窗）
        for round_no in (1, 2, 3):
            agent.receive_prompt(content=f"检查窗口{round_no}", source="test")
            await wait_until(lambda n=round_no: len(_planner_refs()) >= n, what=f"第{round_no}窗决策完成")

        refs = _planner_refs()
        assert len(refs) == 3
        assert "【上次收尾】" in refs[0] and "明天上线接着搭水车" in refs[0]
        assert "【上次收尾】" in refs[1]
        assert "【上次收尾】" not in refs[2]

        # 先结束当前场次再新开下一场：第 1 窗注入，紧接着本场落下一条委派账后，第 2 窗立即停注
        await bus.emit(
            CoreEvents.LIVE_ENDED,
            LiveEndedPayload(
                live_session_id=s_cur,
                kind="live",
                started_at_ms=_NOW,
                ended_at_ms=_NOW + 5_000,
                empty_discarded=True,
            ),
            source="test",
        )
        await wait_until(lambda: not agent.is_live_active(), what="首场下播收闸")

        s_next = await store.sessions.insert_live_session(started_at_ms=_NOW + 10_000, kind="live")
        session_stub.active_pk = s_next
        llm.calls.clear()
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=s_next, kind="live", started_at_ms=_NOW + 10_000, resumed=False),
            source="test",
        )
        await wait_until(agent.is_live_active, what="新场开播就绪")
        agent.receive_prompt(content="新场首窗", source="test")
        await wait_until(lambda: len(_planner_refs()) >= 1, what="新场第1窗决策完成")
        await store.events.insert_delegation_event(
            live_session_id=s_next,
            game="minecraft",
            task_id="t_cur",
            kind="accepted",
            text="搭水车",
            timestamp_ms=_NOW + 11_000,
            initiator=PLANNER_CALL_SOURCE,
        )
        agent.receive_prompt(content="派单后第二窗", source="test")
        await wait_until(lambda: len(_planner_refs()) >= 2, what="新场第2窗决策完成")
        next_refs = _planner_refs()
        assert "【上次收尾】" in next_refs[0]
        assert "【上次收尾】" not in next_refs[1]

        # 断线续播（resumed=True）：不注入【上次收尾】
        await bus.emit(
            CoreEvents.LIVE_ENDED,
            LiveEndedPayload(
                live_session_id=s_next,
                kind="live",
                started_at_ms=_NOW + 10_000,
                ended_at_ms=_NOW + 15_000,
                empty_discarded=True,
            ),
            source="test",
        )
        await wait_until(lambda: not agent.is_live_active(), what="新场下播收闸")
        llm.calls.clear()
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=s_next, kind="live", started_at_ms=_NOW + 10_000, resumed=True),
            source="test",
        )
        await wait_until(lambda: len(_planner_refs()) >= 1, what="续播接回窗完成")
        assert all("【上次收尾】" not in ref for ref in _planner_refs())
    finally:
        await agent.stop()
        await bus.cleanup()
