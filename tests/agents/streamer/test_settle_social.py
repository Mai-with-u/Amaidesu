"""下播整理第四次调用：更新游戏社交事实，顺带记下有原话作证的一起做的事与帮过忙。

真库 + 真提示词模板 + 按队列应答的假模型。覆盖：第四调路径（写入、取代、互动、未登记进报告）、
第四调失败时整场不提交并记失败、没接仓储时不跑第四调，以及逐条校验的打回分支。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.agents.streamer.memory.saves import SaveTimeline
from src.agents.streamer.memory.settle import SessionSettler
from src.agents.streamer.memory.settle_social import (
    MAX_FACTS_PER_KIND,
    SocialMaterial,
    build_social_material,
    validate_social_output,
)
from src.modules.llm.payload import Response
from src.modules.prompts import PromptManager
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.game_players import GameChatLine
from src.modules.storage.models.memory import SettleReport
from src.modules.storage.models.social_facts import SocialFact
from tests.modules.llm.fakes import FakeLLMManager


@pytest.fixture(scope="module")
def prompt_manager() -> PromptManager:
    manager = PromptManager(auto_scan_src=True)
    manager.load_all()
    return manager


@pytest.fixture()
async def store(tmp_path: Path):
    db = SQLiteDatabase(tmp_path / "settle.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


def _settler(store: SQLiteDatabase, llm: Any, prompt_manager: PromptManager, *, social: bool = True) -> SessionSettler:
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
        social_fact_repo=store.social_facts if social else None,
    )


async def _scene(store: SQLiteDatabase) -> Dict[str, int]:
    """一场：玩了 minecraft，Alex 在公屏说了两句，她播出一句台词；已有一条现行的进行中的事。"""
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session, ended_at_ms=1_801_000)
    await store.events.insert_game_event(
        live_session_id=session, game="minecraft", event_type="milestone", message="进了世界"
    )
    await store.chat.insert_live_chat(
        live_session_id=session, timestamp_ms=1_500, sender_role="assistant", content="好呀", message_type="speech"
    )
    player = await store.game_players.meet_player(
        game="minecraft", uuid="u-alex", name="Alex", live_session_id=session, at_ms=1_100
    )
    first = await store.game_players.add_game_chat(
        live_session_id=session,
        game="minecraft",
        direction="player",
        content="刷铁机修好了，我去挖钻石",
        player_id=player.id,
        at_ms=1_200,
        message_id="g1",
    )
    await store.game_players.add_game_chat(
        live_session_id=session,
        game="minecraft",
        direction="streamer",
        content="那我等你钻石回来一起做个剑",
        player_id=None,
        at_ms=1_250,
        message_id="g1b",
    )
    await store.game_players.add_game_chat(
        live_session_id=session,
        game="minecraft",
        direction="player",
        content="我们一起去下界吧",
        player_id=player.id,
        at_ms=1_300,
        message_id="g2",
    )
    old = await store.social_facts.insert_fact(
        kind="works", text="Alex 在修刷铁机", quote="修刷铁机", game="minecraft", created_session_id=session
    )
    assert old is not None
    return {"session": session, "player": player.id, "first_chat": first, "old": old}


def _answers(social: Any) -> FakeLLMManager:
    contents = [json.dumps({"previous": [], "archive": []}), json.dumps({"commitments": []})]
    responses = [Response(success=True, content=content) for content in contents]
    if isinstance(social, Response):
        responses.append(social)
    else:
        responses.append(Response(success=True, content=json.dumps(social, ensure_ascii=False)))
    return FakeLLMManager(responses=responses)


async def test_fourth_call_updates_facts_and_interactions(store, prompt_manager) -> None:
    env = await _scene(store)
    social = {
        "facts": [
            {
                "kind": "works",
                "text": "玩家1 改去挖钻石",
                "chat": "公屏#1",
                "quote": "我去挖钻石",
                "supersedes": env["old"],
            },
            {"kind": "her_role", "text": "她陪 Alex 跑腿", "chat": None, "quote": "", "supersedes": None},
            {"kind": "members", "text": "凭空的关系", "chat": None, "quote": "", "supersedes": None},
        ],
        "interactions": [
            {
                "player": "玩家1",
                "kind": "together",
                "what": "玩家1 一起去下界",
                "chat": "公屏#2",
                "quote": "一起去下界",
            },
            {"player": "玩家1", "kind": "helped", "what": "帮忙", "chat": "公屏#2", "quote": "没说过的话"},
        ],
    }
    llm = _answers(social)
    assert await _settler(store, llm, prompt_manager).settle_session(env["session"]) is True

    # 第四调的原料是紧凑集：玩家公屏与现有事实在，她的台词不在
    social_call = llm.calls[-1]
    assert "刷铁机修好了" in str(social_call["input"]) and f"事实#{env['old']}" in str(social_call["input"])
    assert "好呀" not in str(social_call["input"])
    # 公屏按时间序双侧行编排：她的公屏行标"她"插在对话流里（回答才看得出是在回答什么），
    # 但没有编号——证据编号只发给玩家的行
    social_input = str(social_call["input"])
    her_row = social_input.index("她：那我等你钻石回来一起做个剑")
    player_row = social_input.index("公屏#2")
    assert her_row < player_row and "公屏#" not in social_input[her_row:player_row]

    active = await store.social_facts.list_active_facts(game="minecraft")
    # 模型写的玩家代号在落库前还原回真名：代号只在这一个调用里有效，
    # 真名才能被下一次直播认出、被"忘了我"的删除按名字匹配到
    assert sorted(fact.text for fact in active) == ["Alex 改去挖钻石", "她陪 Alex 跑腿"]
    works = next(fact for fact in active if fact.kind == "works")
    assert works.evidence_game_chat_id == env["first_chat"]
    old = await store.social_facts.get_fact(fact_id=env["old"])
    assert old is not None and old.status == "superseded"

    interactions = await store.game_players.list_session_interactions(live_session_id=env["session"])
    assert [(item.kind, item.fact.get("what")) for item in interactions] == [("together", "Alex 一起去下界")]

    session = await store.sessions.get_live_session(live_session_id=env["session"])
    assert session is not None and session.settle_report
    report = SettleReport.model_validate_json(session.settle_report)
    missed = [item.what for item in report.unregistered if item.kind == "social_fact"]
    assert missed == ["凭空的关系", "帮忙"]


async def test_fourth_call_failure_fails_whole_session(store, prompt_manager) -> None:
    env = await _scene(store)
    llm = _answers(Response(success=False, content="", error="boom"))
    assert await _settler(store, llm, prompt_manager).settle_session(env["session"]) is False
    session = await store.sessions.get_live_session(live_session_id=env["session"])
    assert session is not None
    assert session.settled_at_ms is None and session.settle_attempts == 1
    # 整场不提交：旧行原样、没有新事实
    active = await store.social_facts.list_active_facts(game="minecraft")
    assert [fact.id for fact in active] == [env["old"]]


async def test_no_repo_means_no_fourth_call(store, prompt_manager) -> None:
    env = await _scene(store)
    contents = [json.dumps({"previous": [], "archive": []}), json.dumps({"commitments": []})]
    llm = FakeLLMManager(responses=[Response(success=True, content=content) for content in contents])
    assert await _settler(store, llm, prompt_manager, social=False).settle_session(env["session"]) is True
    assert len(llm.calls) == 2


def _line(line_id: int, content: str, player_id: int = 7) -> GameChatLine:
    return GameChatLine(
        id=line_id,
        live_session_id=1,
        game="minecraft",
        direction="player",
        player_id=player_id,
        content=content,
        save_id="w1",
        save_name="麦窝",
        at_ms=1_000 + line_id,
    )


def _fact(fact_id: int, kind: str, *, locked: int = 0) -> SocialFact:
    return SocialFact(
        id=fact_id,
        game="minecraft",
        save_id="w1",
        kind=kind,
        text=f"事实{fact_id}",
        quote="原",
        human_locked=locked,
        created_at_ms=1,
        updated_at_ms=1,
    )


def _material(facts: List[SocialFact]) -> SocialMaterial:
    return build_social_material(
        chat_lines=[_line(1, "箱子大家随便用")],
        interactions=[],
        facts=facts,
        player_names={7: "Alex"},
        records_text="",
        places_text="【本场记住的地点】\n没有",
        games=["minecraft"],
        saves=SaveTimeline(),
        started_at_ms=0,
        clock_text=lambda ms: "00:00",
    )


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ({"kind": "gossip", "text": "x", "chat": "公屏#1", "quote": "箱子"}, "六类"),
        ({"kind": "sharing", "text": "箱子公用", "chat": None, "quote": ""}, "必须引"),
        ({"kind": "sharing", "text": "箱子公用", "chat": "公屏#9", "quote": "箱子"}, "不是给出的"),
        ({"kind": "sharing", "text": "箱子公用", "chat": "公屏#1", "quote": "柜子"}, "一字不差"),
        ({"kind": "her_role", "text": "她管箱子", "chat": None, "quote": "箱子"}, "没引"),
        ({"kind": "sharing", "text": "箱子公用", "chat": "公屏#1", "quote": "箱子", "supersedes": 2}, "运营者定的"),
        ({"kind": "sharing", "text": "箱子公用", "chat": "公屏#1", "quote": "箱子", "supersedes": 3}, "不是同一类"),
        (
            {"kind": "sharing", "text": "箱子公用", "chat": "公屏#1", "quote": "箱子", "supersedes": 99},
            "不是给出的现行事实",
        ),
    ],
)
def test_validate_bounces(row: Dict[str, Any], reason: str) -> None:
    material = _material([_fact(2, "sharing", locked=1), _fact(3, "works")])
    check = validate_social_output({"facts": [row]}, material, live_session_id=1)
    assert check.facts == []
    assert len(check.missed) == 1 and reason in check.missed[0].reason


def test_validate_cap_and_scope() -> None:
    material = _material([_fact(10 + i, "sharing") for i in range(MAX_FACTS_PER_KIND)])
    row = {"kind": "sharing", "text": "箱子公用", "chat": "公屏#1", "quote": "箱子"}
    check = validate_social_output({"facts": [row]}, material, live_session_id=1)
    assert check.facts == [] and "要先取代旧的" in check.missed[0].reason
    check = validate_social_output({"facts": [{**row, "supersedes": 10}]}, material, live_session_id=1)
    (fact,) = check.facts
    assert (fact.game, fact.save_id, fact.evidence_game_chat_id, fact.supersedes_id) == ("minecraft", "w1", 1, 10)
