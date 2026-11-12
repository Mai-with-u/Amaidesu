"""承诺过期与【还惦记的事】（D2-3）：两档过期、再提起恢复、不常驻不推卡但回想可查。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

import pytest

from src.agents.streamer.memory.brief import build_brief
from src.agents.streamer.memory.cues import MemoryCueProvider
from src.agents.streamer.memory.index import MemoryIndexer
from src.agents.streamer.memory.recall import MemoryRecallService
from src.agents.streamer.memory.search import MemorySearch
from src.agents.streamer.memory.settle import SessionSettler
from src.modules.events.payloads.room import RoomMessageUser
from src.modules.llm.payload import Response
from src.modules.storage.database import SQLiteDatabase
from src.modules.types.game_save import GameSave
from tests.agents.streamer.helpers import stub_prompt
from tests.modules.llm.fakes import FakeLLMManager
from tests.modules.storage.helpers import make_room_message

_NOW = 1_700_000_000_000
_MIN_MS = 5 * 60_000


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "ttl.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def _closed_live(store: SQLiteDatabase, index: int) -> int:
    start = _NOW - (10 - index) * 10_000_000
    sid = await store.sessions.insert_live_session(started_at_ms=start, kind="live")
    await store.sessions.close_live_session(live_session_id=sid, ended_at_ms=start + 3_600_000)
    await store.sessions.mark_settle_done(live_session_id=sid, settled_at_ms=start + 3_700_000)
    return sid


async def test_short_term_and_three_session_stale_commitments_turn_dormant_yet_stay_recallable(
    store: SQLiteDatabase,
) -> None:
    """带时间词隔一场即搁下，一般承诺连续三场未提即搁下；搁下后不进简报、不推观众卡，但回想照查。"""
    s1 = await _closed_live(store, 1)
    s2 = await _closed_live(store, 2)
    s3 = await _closed_live(store, 3)
    s4 = await _closed_live(store, 4)
    s_cur = await store.sessions.insert_live_session(started_at_ms=_NOW, kind="live")

    # s1 登记的一般承诺：隔过 s2、s3、s4 三场没再提 → 搁了很久
    c_old = await store.commitments.insert_commitment(
        what="清理矿井墓碑遗物",
        quote="找机会把矿井墓碑遗物清回来",
        game="minecraft",
        save_id="w1",
        save_name="橡木谷",
        created_session_id=s1,
    )
    # s3 登记的带"明天"的承诺：隔过 s4 一场没再提 → 搁了很久
    c_time_stale = await store.commitments.insert_commitment(
        what="明天做盾牌",
        quote="明天上线先做一面盾牌",
        game="minecraft",
        save_id="w1",
        save_name="橡木谷",
        created_session_id=s3,
    )
    # s4（紧邻上一场）登记的带"下次"的承诺：还没隔过任何场次 → 仍在【还惦记的事】
    c_time_fresh = await store.commitments.insert_commitment(
        what="下次把树场补满",
        quote="下次上线把树场剩下四格补满",
        game="minecraft",
        save_id="w1",
        save_name="橡木谷",
        created_session_id=s4,
    )
    # s2 登记的一般承诺（最近三场是 s4/s3/s2，仍在三场内）→ 仍在【还惦记的事】
    c_within_three = await store.commitments.insert_commitment(
        what="修通后山铁路",
        quote="有空把后山铁路修通",
        game="minecraft",
        save_id="w1",
        save_name="橡木谷",
        created_session_id=s2,
    )
    # 别的存档上的活跃承诺：折进顶部别的存档行，不进眼下世界的【还惦记的事】
    await store.commitments.insert_commitment(
        what="扩建沙漠神殿基地",
        quote="回头扩建沙漠基地",
        game="minecraft",
        save_id="w_other",
        save_name="沙丘档",
        created_session_id=s4,
    )

    # 观众私向过期承诺：开口也不再推卡
    v_res = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_v1",
        user_name="观众乙",
        timestamp_ms=_NOW - 50_000_000,
        name_match_window_ms=0,
    )
    await store.commitments.insert_commitment(
        what="今晚给观众乙唱首歌",
        quote="今晚收播前唱首歌",
        viewer_id=v_res.viewer_id,
        created_session_id=s3,
    )

    brief = await build_brief(
        archive_repo=store.archive,
        commitments_repo=store.commitments,
        min_session_ms=_MIN_MS,
        live_session_id=s_cur,
        current_saves={"minecraft": GameSave(id="w1", name="橡木谷")},
    )
    assert brief.commitment_ids == (c_time_fresh, c_within_three)
    assert "下次上线把树场剩下四格补满" in brief.text
    assert "有空把后山铁路修通" in brief.text
    assert "清理矿井墓碑遗物" not in brief.text and "明天上线先做一面盾牌" not in brief.text
    assert "另有 2 件搁了很久，可以用 memory_recall 查" in brief.text
    assert "还在 minecraft「沙丘档」里做过 1 件事" in brief.text

    # 观众乙开口时，已过期的私向承诺不推卡
    cues = await MemoryCueProvider(
        commitments_repo=store.commitments,
        chat_repo=store.chat,
        viewer_repo=store.viewers,
        archive_repo=store.archive,
        current_session_provider=lambda: s_cur,
        min_session_ms=_MIN_MS,
    ).collect(
        batch=[make_room_message(user=RoomMessageUser(id="u_v1", name="观众乙"), content="晚上好", message_id="m1")]
    )
    assert cues.card_count == 0

    # 回想工具仍能查到搁了很久的承诺（不删）
    await MemoryIndexer(
        repo=store.memory_index,
        embedding=None,
        embedding_note="",
        clock=lambda: _NOW + 120_000,
    ).run_once()
    recall = MemoryRecallService(
        chat_repo=store.chat,
        commitments_repo=store.commitments,
        archive_repo=store.archive,
        current_session_provider=lambda: s_cur,
        search=MemorySearch(repo=store.memory_index, embedding=None),
    )
    found = await recall.recall(query="矿井墓碑遗物")
    assert c_old is not None and c_time_stale is not None
    assert "未了结" in found and "清理矿井墓碑遗物" in found


async def test_settle_advances_last_mentioned_session_when_referenced_or_same_as(
    store: SQLiteDatabase,
) -> None:
    """下播整理把本场台词点名引用或模型认出重申（same_as）的未了结承诺推进到本场，使其重新回到活跃区。"""
    s1 = await _closed_live(store, 1)
    await _closed_live(store, 2)
    await _closed_live(store, 3)

    c_cited = await store.commitments.insert_commitment(
        what="搭一座山顶灯塔",
        quote="以后搭一座山顶灯塔",
        created_session_id=s1,
    )
    c_same = await store.commitments.insert_commitment(
        what="整理音乐歌单",
        quote="回头整理一份音乐歌单",
        created_session_id=s1,
    )
    assert c_cited is not None and c_same is not None

    # 第 4 场：第 1 句点名了 约#c_cited，第 2 句没点名但重申了音乐歌单（模型标 same_as）
    s4_start = _NOW - 5_000_000
    s4 = await store.sessions.insert_live_session(started_at_ms=s4_start, kind="live")
    await store.chat.insert_live_chat(
        live_session_id=s4,
        timestamp_ms=s4_start + 10_000,
        sender_role="assistant",
        content="山顶灯塔那事我还没忘，找材料就去弄",
        message_type="speech",
        memory_refs=json.dumps([f"约#{c_cited}"], ensure_ascii=False),
    )
    await store.chat.insert_live_chat(
        live_session_id=s4,
        timestamp_ms=s4_start + 20_000,
        sender_role="assistant",
        content="音乐歌单我这两天一定整理出来",
        message_type="speech",
    )
    await store.sessions.close_live_session(live_session_id=s4, ended_at_ms=s4_start + 3_600_000)

    llm = FakeLLMManager(
        responses=[
            Response(success=True, content=json.dumps({"archive": [], "previous": []})),
            Response(
                success=True,
                content=json.dumps(
                    {
                        "commitments": [
                            {
                                "line": 2,
                                "what": "整理音乐歌单",
                                "quote": "音乐歌单我这两天一定整理出来",
                                "to": "everyone",
                                "same_as": c_same,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            ),
            Response(success=True, content=json.dumps({"closed": []})),
        ]
    )
    settler = SessionSettler(
        sessions_repo=store.sessions,
        chat_repo=store.chat,
        commitments_repo=store.commitments,
        archive_repo=store.archive,
        llm_service=llm,
        prompt_manager=stub_prompt(),
        min_session_ms=_MIN_MS,
    )
    assert await settler.settle_session(s4)

    row_cited = await store.commitments.get_commitment(commitment_id=c_cited)
    row_same = await store.commitments.get_commitment(commitment_id=c_same)
    assert row_cited is not None and row_cited.last_mentioned_session_id == s4
    assert row_same is not None and row_same.last_mentioned_session_id == s4

    # 到了第 5 场开播：这两条原本在 s1 的旧承诺因在 s4 被提起，重新出现在【还惦记的事】
    s5 = await store.sessions.insert_live_session(started_at_ms=_NOW, kind="live")
    brief = await build_brief(
        archive_repo=store.archive,
        commitments_repo=store.commitments,
        min_session_ms=_MIN_MS,
        live_session_id=s5,
    )
    assert set(brief.commitment_ids) == {c_cited, c_same}
