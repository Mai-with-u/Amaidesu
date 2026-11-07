"""下播整理列出玩家自述：找承诺那次顺带列出，代码核对对谁、原话、关键词、个人信息与去重，写进待审表。

玩家在公屏说了关于他自己、下次还用得上的事，她当场接住了（直播里回应他，或在公屏回他），整理列成候选，
人批准了才进他的玩家卡。核对全部是代码：模型说错了对象、编了他没说过的话、写进个人信息的都进不了待审表。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

import pytest

from src.agents.streamer.memory.settle import SessionSettler
from src.modules.llm.payload import Response
from src.modules.prompts import PromptManager
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import SettleReport
from tests.modules.llm.fakes import FakeLLMManager

_DAY_MS = 24 * 60 * 60 * 1000


@pytest.fixture(scope="module")
def prompt_manager() -> PromptManager:
    manager = PromptManager(auto_scan_src=True)
    manager.load_all()
    return manager


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "player_candidates.db")
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
        game_player_repo=store.game_players,
    )


def _reply(*contents: str) -> FakeLLMManager:
    return FakeLLMManager(responses=[Response(success=True, content=content) for content in contents])


async def _scene(store: SQLiteDatabase) -> Dict[str, int]:
    """一场：玩家甲说盖了风车房、她在直播里接住；玩家乙让她叫他小乙、她在公屏回；乙又说了年龄；甲说养了狗。"""
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session, ended_at_ms=1_800_000)
    repo = store.game_players
    jia = await repo.meet_player(game="minecraft", uuid="uuid-jia", name="玩家甲", live_session_id=session, at_ms=1_010)
    yi = await repo.meet_player(game="minecraft", uuid="uuid-yi", name="玩家乙", live_session_id=session, at_ms=1_020)

    async def chat(direction: str, content: str, at_ms: int, player_id: Any = None) -> int:
        return await repo.add_game_chat(
            live_session_id=session,
            game="minecraft",
            direction=direction,
            content=content,
            player_id=player_id,
            at_ms=at_ms,
        )

    windmill = await chat("player", "我在河边盖了个风车房", 1_050, jia.id)
    spoken = await store.chat.insert_live_chat(
        live_session_id=session,
        timestamp_ms=1_100,
        sender_role="assistant",
        content="风车房听着好酷，改天去看看",
        message_type="speech",
        reply_player_id=jia.id,
    )
    nickname = await chat("player", "以后叫我小乙就行", 1_200, yi.id)
    answered = await chat("streamer", "好嘞小乙", 1_210)
    await chat("player", "我今年15岁", 1_300, yi.id)
    await chat("streamer", "哇那你还挺小", 1_310)
    await chat("player", "我养了只狗叫旺财", 1_400, jia.id)
    await chat("streamer", "玩家甲的狗真可爱", 1_410)
    return {
        "session": session,
        "jia": jia.id,
        "yi": yi.id,
        "windmill": windmill,
        "spoken": spoken,
        "nickname": nickname,
        "answered": answered,
    }


def _rows() -> List[Dict[str, Any]]:
    """模型列的五条：两条成立；一条对象错（公屏#1 是在回乙）；一条关键词不在他原话里；一条带年龄。"""
    return [
        {
            "player": "玩家1",
            "player_quote": "河边盖了个风车房",
            "line": 1,
            "chat": None,
            "quote": "风车房听着好酷",
            "title": "玩家1在河边盖了个风车房",
            "word": "风车房",
        },
        {
            "player": "玩家2",
            "player_quote": "以后叫我小乙就行",
            "line": None,
            "chat": "公屏#1",
            "quote": "好嘞小乙",
            "title": "他希望被叫作小乙",
            "word": "小乙",
        },
        {
            "player": "玩家1",
            "player_quote": "以后叫我小乙就行",
            "line": None,
            "chat": "公屏#1",
            "quote": "好嘞小乙",
            "title": "他希望被叫作小乙",
            "word": "小乙",
        },
        {
            "player": "玩家1",
            "player_quote": "我养了只狗叫旺财",
            "line": None,
            "chat": "公屏#3",
            "quote": "的狗真可爱",
            "title": "他养了只狗叫旺财",
            "word": "小狗",
        },
        {
            "player": "玩家2",
            "player_quote": "我今年15岁",
            "line": None,
            "chat": "公屏#2",
            "quote": "哇那你还挺小",
            "title": "他今年15岁",
            "word": "15岁",
        },
    ]


def _commitments_reply(rows: List[Dict[str, Any]]) -> str:
    return json.dumps({"commitments": [], "claimed_missing": [], "player_candidates": rows}, ensure_ascii=False)


async def _candidates(store: SQLiteDatabase) -> List[Dict[str, Any]]:
    with store.manager.transaction() as conn:
        rows = conn.execute(
            "SELECT player_id, title, player_chat_id, player_quote, reply_game_chat_id, reply_live_chat_id,"
            " reply_quote, status FROM player_candidates ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


async def test_player_self_statements_are_checked_and_listed_for_review(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    ids = await _scene(store)
    llm = _reply('{"archive": []}', _commitments_reply(_rows()))
    assert await _settler(store, llm, prompt_manager).settle_session(ids["session"]) is True

    prompt = llm.calls[1]["input"]
    assert "玩家1：我在河边盖了个风车房" in prompt, "她在直播里接住的那句公屏也给模型看"
    assert await _candidates(store) == [
        {
            "player_id": ids["jia"],
            "title": "他在河边盖了个风车房",
            "player_chat_id": ids["windmill"],
            "player_quote": "河边盖了个风车房",
            "reply_game_chat_id": None,
            "reply_live_chat_id": ids["spoken"],
            "reply_quote": "风车房听着好酷",
            "status": "pending",
        },
        {
            "player_id": ids["yi"],
            "title": "他希望被叫作小乙",
            "player_chat_id": ids["nickname"],
            "player_quote": "以后叫我小乙就行",
            "reply_game_chat_id": ids["answered"],
            "reply_live_chat_id": None,
            "reply_quote": "好嘞小乙",
            "status": "pending",
        },
    ]
    session = await store.sessions.get_live_session(live_session_id=ids["session"])
    assert session is not None and session.settle_report is not None
    report = SettleReport.model_validate_json(session.settle_report)
    assert report.candidates_listed == {"player_self": 2}
    assert report.candidates_personal_dropped == 1, "带年龄的整条丢掉，只计数"
    reasons = [item.reason for item in report.unregistered]
    assert any("不是她回应这位玩家" in reason for reason in reasons), "公屏#1 回的是乙，记到甲头上不列"
    assert any("关键词「小狗」" in reason for reason in reasons), "关键词不在他原话里不列"
    assert all("15" not in item.what for item in report.unregistered), "个人信息的内容不写进报告"


async def test_decided_statements_are_not_listed_again_and_pending_ones_expire(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """批准过的同一件事、驳回过的同一句话不再列；没人审的列出 14 天后删，审过的留着。"""
    ids = await _scene(store)
    rows = _rows()[:2]
    assert await _settler(store, _reply('{"archive": []}', _commitments_reply(rows)), prompt_manager).settle_session(
        ids["session"]
    )
    with store.manager.transaction() as conn:
        conn.execute("UPDATE player_candidates SET status='approved' WHERE player_id=?", (ids["jia"],))
        conn.execute("UPDATE player_candidates SET status='rejected' WHERE player_id=?", (ids["yi"],))
    assert await store.sessions.requeue_settlement(live_session_id=ids["session"]) is True
    assert await _settler(store, _reply('{"archive": []}', _commitments_reply(rows)), prompt_manager).settle_session(
        ids["session"]
    )
    assert [row["status"] for row in await _candidates(store)] == ["approved", "rejected"], "重新整理不重复列"

    with store.manager.transaction() as conn:
        conn.execute(
            "INSERT INTO player_candidates(live_session_id, player_id, title, player_chat_id, player_quote,"
            " reply_quote, created_at_ms) VALUES (?, ?, '他在山顶有座塔', ?, '山顶', '好高', 1000)",
            (ids["session"], ids["jia"], ids["windmill"]),
        )
    assert await store.game_players.expire_player_candidates(now_ms_value=1_000 + 14 * _DAY_MS) == 0
    assert await store.game_players.expire_player_candidates(now_ms_value=1_001 + 14 * _DAY_MS) == 1
    assert [row["status"] for row in await _candidates(store)] == ["approved", "rejected"]
