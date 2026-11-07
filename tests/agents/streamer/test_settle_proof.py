"""下播整理核对"她说做好了"：游戏记录编号列给模型，档案与了结里的完成说法要引一条撑着它的记录，代码核对。

她嘴上说"门装好了"不等于游戏里真装好了；没核实到的完成说法写进档案，下一场她就带着一件没发生的事开播。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, List

import pytest

from src.agents.streamer.memory.settle import SessionSettler
from src.agents.streamer.memory.settle_proof import game_records, is_done_claim, proof_for, render_records
from src.modules.llm.payload import Response
from src.modules.prompts import PromptManager
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import SettleReport
from tests.modules.llm.fakes import FakeLLMManager

_MINUTE = 60_000


@pytest.mark.parametrize(
    ("text", "claimed"),
    [
        ("门装好啦，能推能关", True),
        ("钻石拿到了", True),
        ("门口那排花种下了", True),
        ("TNT 炸完啦", True),
        ("这事搞定", True),
        ("火把也插好了", True),
        ("三面墙立好了", True),
        ("钻石镐合成出来啦", True),
        ("海带还没烤完", False),
        ("门没装好", False),
        ("明天去挖铁", False),
        ("今天好累", False),
    ],
)
def test_done_claims_are_spotted_but_not_when_negated(text: str, claimed: bool) -> None:
    assert is_done_claim(text) is claimed


def _delegation(status: str, *, instruction: str, summary: str, ended_ms: int, game: str = "minecraft") -> Any:
    return SimpleNamespace(game=game, instruction=instruction, status=status, summary=summary, ended_ms=ended_ms)


def _event(event_type: str, message: str, at_ms: int, game: str = "minecraft") -> Any:
    return SimpleNamespace(game=game, event_type=event_type, message=message, timestamp_ms=at_ms)


def test_records_list_finished_delegations_and_successful_steps_in_time_order() -> None:
    """交付了的委派能作证，没交付的照列、标明不能作证；还没了结的不列；写着没做成的里程碑不列。"""
    records = game_records(
        [
            _event("milestone", "第 2 步（maicraft:use），用东西没有完成，需要换站位", 3 * _MINUTE),
            _event("milestone", "第 1 步（maicraft:obtain），拿到了 1 个熔炉", 2 * _MINUTE),
            _event("attention_required", "被僵尸打了一下", 2 * _MINUTE),
        ],
        [
            _delegation("succeeded", instruction="把门装上", summary="delivery: 正门装好了", ended_ms=5 * _MINUTE),
            _delegation("undelivered", instruction="种一排花", summary="停了但没交付结果", ended_ms=4 * _MINUTE),
            _delegation("accepted", instruction="去挖铁", summary="", ended_ms=0),
        ],
    )

    assert [(record.number, record.kind, record.text, record.proves) for record in records] == [
        (1, "里程碑", "第 1 步（maicraft:obtain），拿到了 1 个熔炉", True),
        (2, "委派", "委派「种一排花」停了没交付，不能作证", False),
        (3, "委派", "委派「把门装上」交付了：正门装好了", True),
    ]
    assert render_records(records).splitlines()[1].startswith("- 记录#1 ") and render_records([]) == ""


def test_proof_must_exist_prove_match_the_game_and_not_come_later() -> None:
    records = game_records(
        [_event("milestone", "火把插好了", 10 * _MINUTE), _event("report", "钻石挖到了", _MINUTE // 2, game="other")],
        [_delegation("succeeded", instruction="把门装上", summary="门装好了", ended_ms=_MINUTE)],
    )
    at_ms = 5 * _MINUTE

    assert proof_for("记录#1", records, game="minecraft", at_ms=at_ms) is None, "别的游戏的记录"
    assert proof_for("#2", records, game="minecraft", at_ms=at_ms) is not None
    assert proof_for("2", records, game="Minecraft", at_ms=at_ms) is not None, "游戏名不分大小写"
    assert proof_for("记录#3", records, game="minecraft", at_ms=at_ms) is None, "晚于她那句"
    assert proof_for("记录#3", records, game="minecraft", at_ms=9 * _MINUTE) is not None, "容两分钟"
    for ref in (None, "", "记录#99", "门装好那条"):
        assert proof_for(ref, records, game="minecraft", at_ms=at_ms) is None


# ---------------------------------------------------------------------------
# 接进整理
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prompt_manager() -> PromptManager:
    manager = PromptManager(auto_scan_src=True)
    manager.load_all()
    return manager


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "settle_proof.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def _scene(store: SQLiteDatabase) -> Dict[str, int]:
    """一场：派去装门交付了、派去种花停了没交付、火把的里程碑比她那句晚；三条挂游戏的旧承诺加一条唱歌的。"""
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session, ended_at_ms=60 * _MINUTE)
    events = store.events
    for task_id, kind, text, at_ms in (
        ("d-door", "accepted", "把营地的门装上", 2 * _MINUTE),
        ("d-flower", "accepted", "在门口种一排花", 2 * _MINUTE),
        ("d-door", "succeeded", "delivery: 正门装好了，能开能关", 5 * _MINUTE),
        ("d-flower", "undelivered", "停了但没交付结果", 6 * _MINUTE),
    ):
        await events.insert_delegation_event(
            live_session_id=session, game="Minecraft", task_id=task_id, kind=kind, text=text, timestamp_ms=at_ms
        )
    await events.insert_game_event(
        live_session_id=session,
        game="Minecraft",
        event_type="milestone",
        message="火把插好了",
        timestamp_ms=30 * _MINUTE,
    )
    ids: Dict[str, int] = {"session": session}
    for key, what, game in (("door", "把营地的门装上", "Minecraft"), ("flower", "在门口种花", "Minecraft")):
        commitment = await store.commitments.insert_commitment(what=what, quote=what, game=game, now_ms_value=100)
        assert commitment is not None
        ids[key] = commitment
    song = await store.commitments.insert_commitment(what="唱那首歌", quote="下次唱那首歌", now_ms_value=100)
    assert song is not None
    ids["song"] = song
    for content, at_ms in (
        ("门装好啦，能推能关", 7 * _MINUTE),
        ("花都种好了，门口一排", 8 * _MINUTE),
        ("火把也插好了", 9 * _MINUTE),
        ("海带还没烤完，等等", 10 * _MINUTE),
        ("那首歌我是真唱不来，跟大家说声抱歉", 11 * _MINUTE),
    ):
        await store.chat.insert_live_chat(
            live_session_id=session, timestamp_ms=at_ms, sender_role="assistant", content=content, message_type="speech"
        )
    return ids


def _archive(line: int, title: str, quote: str, evidence: Any) -> Dict[str, Any]:
    return {"line": line, "title": title, "quote": quote, "game": "Minecraft", "evidence": evidence}


async def test_done_claims_need_a_game_record_in_archive_and_closures(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """引了交付了的委派的留下；引没交付的、引比她那句晚的、没引的完成说法不写进档案、不了结，进整理报告；
    不是完成说法的、说做不到的照常。模型在档案与了结两次调用里都看得到编了号的游戏记录。"""
    ids = await _scene(store)
    archive = [
        _archive(1, "Minecraft：营地正门装好", "门装好啦", "记录#1"),
        _archive(2, "Minecraft：门口种了一排花", "花都种好了", "记录#2"),
        _archive(3, "Minecraft：火把插好", "火把也插好了", "记录#3"),
        _archive(4, "Minecraft：海带还没烤完", "海带还没烤完", None),
    ]
    closed = [
        {"id": ids["door"], "line": 1, "quote": "门装好啦", "evidence": "记录#1"},
        {"id": ids["flower"], "line": 2, "quote": "花都种好了", "evidence": None},
        {"id": ids["song"], "line": 5, "quote": "那首歌我是真唱不来", "evidence": None},
    ]
    llm = FakeLLMManager(
        responses=[
            Response(success=True, content=json.dumps({"archive": archive}, ensure_ascii=False)),
            Response(success=True, content='{"commitments": []}'),
            Response(success=True, content=json.dumps({"closed": closed}, ensure_ascii=False)),
        ]
    )
    settler = SessionSettler(
        sessions_repo=store.sessions,
        chat_repo=store.chat,
        commitments_repo=store.commitments,
        event_repo=store.events,
        archive_repo=store.archive,
        llm_service=llm,
        prompt_manager=prompt_manager,
        game_names_provider=lambda: ["Minecraft"],
        clock=lambda: 7_777,
    )

    assert await settler.settle_session(ids["session"]) is True

    for call in (llm.calls[0], llm.calls[2]):
        assert "- 记录#1 " in call["input"] and "委派「把营地的门装上」交付了：正门装好了" in call["input"]
    lines = await store.archive.list_lines(live_session_id=ids["session"])
    assert [line.text for line in lines] == ["Minecraft：营地正门装好", "Minecraft：海带还没烤完"]
    statuses: Dict[int, str] = {}
    for key in ("door", "flower", "song"):
        row = await store.commitments.get_commitment(commitment_id=ids[key])
        assert row is not None
        statuses[ids[key]] = row.status
    assert statuses == {ids["door"]: "closed", ids["flower"]: "open", ids["song"]: "closed"}
    session = await store.sessions.get_live_session(live_session_id=ids["session"])
    assert session is not None and session.settle_report is not None
    report = SettleReport.model_validate_json(session.settle_report)
    unproven: List[str] = [item.what for item in report.unregistered if "游戏记录里没核实到" in item.reason]
    assert unproven == ["Minecraft：门口种了一排花", "Minecraft：火把插好", f"了结 #{ids['flower']}"]
