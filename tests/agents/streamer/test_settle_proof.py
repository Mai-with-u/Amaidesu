"""下播整理核对"她说做好了"：游戏记录编号列给模型，档案与了结里的完成说法要引撑着它的记录，代码核对。

她嘴上说"门装好了"不等于游戏里真装好了；没核实到的完成说法当事实写进档案，下一场她就带着一件没发生的事
开播。所以没核实到的写成"她说……（游戏记录里没核实到）"，了结不认。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, List

import pytest

from src.agents.streamer.memory.settle import SessionSettler
from src.agents.streamer.memory.settle_proof import (
    game_records,
    infer_proof_for_claim,
    is_done_claim,
    mark_unverified,
    proof_for,
    render_records,
)
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
        ("动力压机还没完全修好", False),
        ("打算把门装好", False),
        ("准备去拿到钻石", False),
        ("听说 Alex 把刷铁机修好了", False),
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
            _delegation("closed_by_initiator", instruction="铺木地板", summary="做完了", ended_ms=6 * _MINUTE),
            _delegation("accepted", instruction="去挖铁", summary="", ended_ms=0),
        ],
    )

    assert [(record.number, record.kind, record.text, record.proves) for record in records] == [
        (1, "里程碑", "第 1 步（maicraft:obtain），拿到了 1 个熔炉", True),
        (2, "委派", "委派「种一排花」停了没交付，不能作证", False),
        (3, "委派", "委派「把门装上」交付了：正门装好了", True),
        (4, "委派", "委派「铺木地板」她认定做完了，游戏那边没交付，不能作证", False),
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


def test_several_refs_pass_when_any_one_holds() -> None:
    """evidence 引了几条（顿号、空格隔开或 JSON 列表）时有一条撑得住就算；都撑不住才算没核实到。"""
    records = game_records(
        [_event("milestone", "火把插好了", 10 * _MINUTE), _event("report", "钻石挖到了", _MINUTE // 2, game="other")],
        [_delegation("succeeded", instruction="把门装上", summary="门装好了", ended_ms=_MINUTE)],
    )
    at_ms = 5 * _MINUTE

    for ref in ("记录#1、记录#2", "记录#3 记录#2", ["记录#1", "#2"], "#1，2"):
        held = proof_for(ref, records, game="minecraft", at_ms=at_ms)
        assert held is not None and held.number == 2, ref
    assert proof_for("记录#1、记录#3", records, game="minecraft", at_ms=at_ms) is None, "一条别的游戏、一条晚于她那句"
    assert proof_for("记录#1（23:10 交付）", records, game="other", at_ms=at_ms) is None, "认不出的那截不当编号"


def test_long_session_keeps_every_record_and_folds_consecutive_steps() -> None:
    """记录不截最近几条：长场前半段做成的也引得到。条数多时同一次执行里连续的几步并成一行，
    时刻取第一步（核对按它），显示成时段；夹着上报、步号从头开始，就是另一次执行。"""
    events = [_event("report", "开场先把正门装好了", _MINUTE)]
    for run in range(40):
        start = (10 + run * 5) * _MINUTE
        for step in range(1, 4):
            message = f"第 {step} 步（maicraft:obtain，第{run}次），拿到了 1 个 minecraft:dirt"
            events.append(_event("milestone", message, start + step * _MINUTE))
    events.append(_event("report", "最后把窗台的花种下了", 300 * _MINUTE))

    records = game_records(events, [])

    assert len(records) == 42, "开场那条上报 + 40 次执行各并成一行 + 收尾那条上报"
    assert records[0].text == "开场先把正门装好了" and records[-1].text == "最后把窗台的花种下了"
    folded = records[1]
    assert (folded.at_ms, folded.until_ms) == (11 * _MINUTE, 13 * _MINUTE) and folded.proves
    assert folded.text.startswith("连续 3 步：第 1 步（第0次），拿到了 1 个 dirt；第 2 步")
    assert "maicraft:" not in folded.text and "minecraft:" not in folded.text
    assert "- 记录#2 " in render_records(records) and "–" in render_records(records).splitlines()[2]
    assert proof_for("记录#1", records, game="minecraft", at_ms=2 * _MINUTE) is not None, "开场做成的还引得到"
    assert proof_for("记录#2", records, game="minecraft", at_ms=11 * _MINUTE) is not None, "并成一行按第一步核对"

    few = game_records(events[:7], [])
    assert [record.until_ms for record in few] == [0] * 7, "条数不多时一步一行，不并"


def test_unverified_claim_reads_as_her_words() -> None:
    assert mark_unverified("Minecraft：门口种了一排花") == "Minecraft：她说门口种了一排花（游戏记录里没核实到）"
    assert mark_unverified("窗台的花摆好了") == "她说窗台的花摆好了（游戏记录里没核实到）"


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
    """引了交付了的委派的照常写；引没交付的、引比她那句晚的完成说法写成"她说……（游戏记录里没核实到）"、
    不算稳定的事，整理报告记下条数；了结时没核实到的不了结、进未登记清单。不是完成说法的、说做不到的照常。
    模型在档案与了结两次调用里都看得到编了号的游戏记录。"""
    ids = await _scene(store)
    archive = [
        _archive(1, "Minecraft：营地正门装好", "门装好啦", "记录#1"),
        {**_archive(2, "Minecraft：门口种了一排花", "花都种好了", "记录#2"), "lasting": True},
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
    assert sorted(line.text for line in lines) == sorted(
        [
            "Minecraft：营地正门装好",
            "Minecraft：她说门口种了一排花（游戏记录里没核实到）",
            "Minecraft：她说火把插好（游戏记录里没核实到）",
            "Minecraft：海带还没烤完",
        ]
    )
    snapshot = await store.archive.get_snapshot(live_session_id=ids["session"])
    assert snapshot is not None
    marked = [item.lasting for item in snapshot.lines if "没核实到" in item.line.text]
    assert marked == [False, False], "模型标了稳定也不算：没核实到的不当稳定的事"
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
    assert unproven == [f"了结 #{ids['flower']}"] and report.unverified_claims == 2


async def test_unverified_claim_cannot_be_highlight_and_session_caps_highlights_at_two(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """下播整理硬护栏：未核实到的完成说法即使模型填了 highlight:true 也强制降为 False；
    单场通过核实的 highlight:true 最多保留前 2 条，第 3 条起自动降为 False。"""
    ids = await _scene(store)
    await store.chat.insert_live_chat(
        live_session_id=ids["session"],
        timestamp_ms=12 * _MINUTE,
        sender_role="assistant",
        content="刚才过桥直接一脚踩空摔进岩浆，全直播间都在笑我",
        message_type="speech",
    )
    await store.chat.insert_live_chat(
        live_session_id=ids["session"],
        timestamp_ms=13 * _MINUTE,
        sender_role="assistant",
        content="今天和大家一起把开场暗号对上了，特别默契",
        message_type="speech",
    )
    archive = [
        # 未核实的完成说法（种花停在 undelivered）：模型硬标 highlight:true 也必须降为 False，且不占 2 条名额
        {**_archive(2, "Minecraft：门口种了一排花", "花都种好了", "记录#2"), "highlight": True},
        # 第 1 条合法名场面（有交付委派记录#1 支撑）
        {**_archive(1, "Minecraft：营地正门装好", "门装好啦", "记录#1"), "highlight": True},
        # 第 2 条合法名场面（非完成说法的翻车经历）
        {
            "line": 6,
            "title": "Minecraft：过桥踩空摔进岩浆",
            "quote": "过桥直接一脚踩空摔进岩浆",
            "game": "Minecraft",
            "highlight": True,
            "evidence": None,
        },
        # 第 3 条模型标了 highlight:true：超出单场 2 条上限，自动降为普通档案行
        {
            "line": 7,
            "title": "和观众对上开场暗号",
            "quote": "和大家一起把开场暗号对上了",
            "game": None,
            "highlight": True,
            "evidence": None,
        },
    ]
    llm = FakeLLMManager(
        responses=[
            Response(success=True, content=json.dumps({"archive": archive}, ensure_ascii=False)),
            Response(success=True, content='{"commitments": []}'),
            Response(success=True, content='{"closed": []}'),
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
        clock=lambda: 8_888,
    )

    assert await settler.settle_session(ids["session"]) is True

    lines = await store.archive.list_lines(live_session_id=ids["session"])
    assert [(line.text, line.highlight) for line in lines] == [
        ("Minecraft：她说门口种了一排花（游戏记录里没核实到）", False),
        ("Minecraft：营地正门装好", True),
        ("Minecraft：过桥踩空摔进岩浆", True),
        ("和观众对上开场暗号", False),
    ]
    snapshot = await store.archive.get_snapshot(live_session_id=ids["session"])
    assert snapshot is not None
    assert [(item.line.text, item.highlight) for item in snapshot.lines] == [
        ("Minecraft：她说门口种了一排花（游戏记录里没核实到）", False),
        ("Minecraft：营地正门装好", True),
        ("Minecraft：过桥踩空摔进岩浆", True),
        ("和观众对上开场暗号", False),
    ]


async def test_dimension_changed_event_proves_cross_dimension_claim_in_settle(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """跨维度事件 dimension_changed 纳入可作证游戏记录，去下界/末地等完成说法不被误标未核实。"""
    records = game_records([_event("dimension_changed", "从主世界到了下界", 5 * _MINUTE)], [])
    assert [(r.number, r.kind, r.text, r.proves) for r in records] == [(1, "跨维度", "从主世界到了下界", True)]
    assert proof_for("记录#1", records, game="minecraft", at_ms=6 * _MINUTE) is not None

    session_id = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=30 * _MINUTE)
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="Minecraft",
        event_type="dimension_changed",
        message="从主世界到了下界",
        timestamp_ms=5 * _MINUTE,
    )
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=6 * _MINUTE,
        sender_role="assistant",
        content="终于穿过传送门来到下界啦",
        message_type="speech",
    )
    archive = [
        {
            "line": 1,
            "title": "Minecraft：抵达下界探索",
            "quote": "终于穿过传送门来到下界啦",
            "game": "Minecraft",
            "evidence": "记录#1",
        }
    ]
    llm = FakeLLMManager(
        responses=[
            Response(success=True, content=json.dumps({"archive": archive}, ensure_ascii=False)),
            Response(success=True, content='{"commitments": []}'),
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
        clock=lambda: 9_000,
    )

    assert await settler.settle_session(session_id) is True
    assert "跨维度：从主世界到了下界" in str(llm.calls[0]["input"])
    lines = await store.archive.list_lines(live_session_id=session_id)
    assert [line.text for line in lines] == ["Minecraft：抵达下界探索"]


def test_settle_prompts_enforce_persona_commitment_and_facility_rules(prompt_manager: PromptManager) -> None:
    """三份下播整理提示词收紧守护：区分设定自述与本场新事、过滤无对象畅想、纳入共用设施与环境限制。"""
    archive_prompt = prompt_manager.render("memory_archive_system")
    assert "角色固有设定背景的自述" in archive_prompt
    assert "并非本场实际发生的新事件或新发现" in archive_prompt
    assert "跨维度迁移" in archive_prompt

    commitment_prompt = prompt_manager.render("memory_commitment_system")
    assert "承诺须指向具体听众、联机同伴或明确的后续直播计划" in commitment_prompt
    assert "无具体对象、无实际执行意图的随口畅想" in commitment_prompt

    social_prompt = prompt_manager.render("memory_social_fact_system")
    assert "服务器或存档环境限制、工具使用边界" in social_prompt
    assert "共用设施操作惯例" in social_prompt


async def test_item_delivery_event_proves_archive_and_commitment_closure_in_settle(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """物品交付事件 item_delivery 纳入游戏记录（标签「交付」、proves=True），可撑起档案与了结旧承诺核验。"""
    records = game_records(
        [
            _event("item_delivery", "往 PlayerA 放的木桶存进了 3 个钻石", 4 * _MINUTE),
            _event("item_delivery", "朝 PlayerB 扔出钻石失败", 5 * _MINUTE),
        ],
        [],
    )
    assert [(r.number, r.kind, r.text, r.proves) for r in records] == [
        (1, "交付", "往 PlayerA 放的木桶存进了 3 个钻石", True),
    ]
    assert proof_for("记录#1", records, game="minecraft", at_ms=6 * _MINUTE) is not None

    session_id = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=30 * _MINUTE)
    comm_id = await store.commitments.insert_commitment(
        what="补还 PlayerA 三颗钻石",
        quote="下次还你三颗钻石",
        game="Minecraft",
        now_ms_value=100,
    )
    assert comm_id is not None
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="Minecraft",
        event_type="item_delivery",
        message="往 PlayerA 放的木桶存进了 3 个钻石",
        timestamp_ms=4 * _MINUTE,
    )
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=6 * _MINUTE,
        sender_role="assistant",
        content="三颗钻石已经放好进 PlayerA 的木桶里，这笔账搞定啦",
        message_type="speech",
    )
    archive = [
        {
            "line": 1,
            "title": "Minecraft：把三颗钻石放好进木桶",
            "quote": "三颗钻石已经放好进 PlayerA 的木桶里",
            "game": "Minecraft",
            "evidence": "记录#1",
        }
    ]
    closed = [
        {
            "id": comm_id,
            "line": 1,
            "quote": "三颗钻石已经放好进 PlayerA 的木桶里，这笔账搞定啦",
            "evidence": "记录#1",
        }
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
        clock=lambda: 9_500,
    )

    assert await settler.settle_session(session_id) is True
    assert "交付：往 PlayerA 放的木桶存进了 3 个钻石" in str(llm.calls[0]["input"])
    assert "交付：往 PlayerA 放的木桶存进了 3 个钻石" in str(llm.calls[2]["input"])
    lines = await store.archive.list_lines(live_session_id=session_id)
    assert [line.text for line in lines] == ["Minecraft：把三颗钻石放好进木桶"]
    comm_after = await store.commitments.get_commitment(commitment_id=comm_id)
    assert comm_after is not None and comm_after.status == "closed"


async def test_task_succeeded_proves_single_step_actions_while_bluffs_and_contradictions_stay_marked(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """单步成功事件 task_succeeded 进入记录#（含连续同文折叠），真实达成不误标、与失败矛盾或无依据吹牛仍拦截。"""
    # 连续同文单步完成自动折叠；含失败词的摘要不作证
    recs = game_records(
        [
            _event("task_succeeded", "对动力合成器使用成功，放入铁锭", 2 * _MINUTE),
            _event("task_succeeded", "对动力合成器使用成功，放入铁锭", 3 * _MINUTE),
            _event("task_succeeded", "河边钓鱼：钓到了 1 条鳕鱼", 5 * _MINUTE),
            _event("task_succeeded", "用东西失败", 6 * _MINUTE),
        ],
        [],
    )
    assert [(r.number, r.kind, r.text, r.proves, r.until_ms) for r in recs] == [
        (1, "完成", "对动力合成器使用成功，放入铁锭（共 2 次）", True, 3 * _MINUTE),
        (2, "完成", "河边钓鱼：钓到了 1 条鳕鱼", True, 0),
    ]

    # 端到端真库回归：验证高频告警不挤掉单步成功、真实成果不误标、无依据吹牛与失败矛盾仍标未核实
    session_id = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=40 * _MINUTE)
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="Minecraft",
        event_type="task_succeeded",
        message="河边钓鱼：钓到了 1 条鳕鱼",
        timestamp_ms=4 * _MINUTE,
    )
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="Minecraft",
        event_type="task_succeeded",
        message="动力压机运行：压出了 4 块铁板",
        timestamp_ms=5 * _MINUTE,
    )
    # 510 条高频 attention_required 排在后面，不应把前半场的 task_succeeded 挤出核验窗口
    for idx in range(510):
        await store.events.insert_game_event(
            live_session_id=session_id,
            game="Minecraft",
            event_type="attention_required",
            message=f"战斗告警 #{idx}",
            timestamp_ms=10 * _MINUTE + idx * 1_000,
        )
    await store.events.insert_delegation_event(
        live_session_id=session_id,
        game="Minecraft",
        task_id="d-bridge",
        kind="accepted",
        text="修好北岸木桥",
        timestamp_ms=2 * _MINUTE,
    )
    await store.events.insert_delegation_event(
        live_session_id=session_id,
        game="Minecraft",
        task_id="d-bridge",
        kind="undelivered",
        text="停了但没交付结果",
        timestamp_ms=6 * _MINUTE,
    )
    for content, at_ms in (
        ("在河边钓到一条鳕鱼啦", 7 * _MINUTE),
        ("动力压机修好压出铁板啦", 8 * _MINUTE),
        ("全套下界合金甲也打造出来啦", 9 * _MINUTE),
        ("北岸木桥已经修好啦", 10 * _MINUTE),
    ):
        await store.chat.insert_live_chat(
            live_session_id=session_id,
            timestamp_ms=at_ms,
            sender_role="assistant",
            content=content,
            message_type="speech",
        )

    archive = [
        # 显式引单步成功记录#1 -> 核实通过
        {
            "line": 1,
            "title": "Minecraft：河边钓到鳕鱼",
            "quote": "在河边钓到一条鳕鱼啦",
            "game": "Minecraft",
            "evidence": "记录#1",
        },
        # 漏填 evidence，但存在同游戏更早且无矛盾的 task_succeeded（动力压机压出铁板）-> 兜底核实通过不误标
        {
            "line": 2,
            "title": "Minecraft：动力压机修好压出铁板",
            "quote": "动力压机修好压出铁板啦",
            "game": "Minecraft",
            "evidence": None,
        },
        # 纯吹牛，记录里毫无根据 -> 仍标未核实
        {
            "line": 3,
            "title": "Minecraft：打造出全套下界合金甲",
            "quote": "全套下界合金甲也打造出来啦",
            "game": "Minecraft",
            "evidence": None,
        },
        # 与未交付委派 d-bridge 矛盾 -> 仍标未核实
        {
            "line": 4,
            "title": "Minecraft：北岸木桥修好了",
            "quote": "北岸木桥已经修好啦",
            "game": "Minecraft",
            "evidence": None,
        },
    ]
    llm = FakeLLMManager(
        responses=[
            Response(success=True, content=json.dumps({"archive": archive}, ensure_ascii=False)),
            Response(success=True, content='{"commitments": []}'),
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
        clock=lambda: 9_800,
    )

    assert await settler.settle_session(session_id) is True
    assert "完成：河边钓鱼：钓到了 1 条鳕鱼" in str(llm.calls[0]["input"])
    assert "完成：动力压机运行：压出了 4 块铁板" in str(llm.calls[0]["input"])
    lines = await store.archive.list_lines(live_session_id=session_id)
    assert [line.text for line in lines] == [
        "Minecraft：河边钓到鳕鱼",
        "Minecraft：动力压机修好压出铁板",
        "Minecraft：她说打造出全套下界合金甲（游戏记录里没核实到）",
        "Minecraft：她说北岸木桥修好了（游戏记录里没核实到）",
    ]


def test_infer_proof_rejects_broad_prefix_bluffs_and_supports_single_char_nouns_and_scoped_contradictions() -> None:
    """兜底核验不得被单个宽泛地标或修饰前缀穿透，同时正确识别单字核心物词且不被无关前缀失败记录误杀。"""
    bluff_records = game_records(
        [
            _event("task_succeeded", "营地门口采木头", 2 * _MINUTE),
            _event("milestone", "动力水车装好", 3 * _MINUTE),
            _event("dimension_changed", "从主世界到了下界", 4 * _MINUTE),
            _event("milestone", "发现下界要塞", 4 * _MINUTE + 10_000),
            _event("task_succeeded", "在矿洞采了 5 个圆石", 5 * _MINUTE),
        ],
        [],
    )
    # 宽泛前缀与单点局部重合均不可放行吹牛，且“门口”不误认作核心物词“门”
    assert (
        infer_proof_for_claim(
            "Minecraft：营地城堡盖好了 营地二楼城堡盖好了", bluff_records, game="Minecraft", at_ms=8 * _MINUTE
        )
        is None
    )
    assert (
        infer_proof_for_claim(
            "Minecraft：动力磨粉机做好了 动力磨粉机和压板机全做好了", bluff_records, game="Minecraft", at_ms=8 * _MINUTE
        )
        is None
    )
    assert (
        infer_proof_for_claim(
            "Minecraft：下界要塞刷怪笼拆完了 下界要塞刷怪笼拆完了", bluff_records, game="Minecraft", at_ms=8 * _MINUTE
        )
        is None
    )
    assert (
        infer_proof_for_claim(
            "Minecraft：矿洞里的刷怪笼和附魔台全拆完了", bluff_records, game="Minecraft", at_ms=8 * _MINUTE
        )
        is None
    )
    assert infer_proof_for_claim("门装好啦", bluff_records, game="Minecraft", at_ms=8 * _MINUTE) is None

    # 短单字核心物词（门/床/树）存在真实成功交付记录时可兜底核实
    short_noun_records = game_records(
        [
            _event("task_succeeded", "把木床摆在屋里", 2 * _MINUTE),
            _event("task_succeeded", "砍完后院橡树", 3 * _MINUTE),
        ],
        [
            _delegation(
                "succeeded",
                instruction="把营地的门装上",
                summary="delivery: 正门装好了，能开能关",
                ended_ms=4 * _MINUTE,
            )
        ],
    )
    door_hit = infer_proof_for_claim("门装好啦", short_noun_records, game="Minecraft", at_ms=6 * _MINUTE)
    assert door_hit is not None and "正门" in door_hit.text
    bed_hit = infer_proof_for_claim("床摆好了", short_noun_records, game="Minecraft", at_ms=6 * _MINUTE)
    assert bed_hit is not None and "木床" in bed_hit.text
    tree_hit = infer_proof_for_claim("树砍完了", short_noun_records, game="Minecraft", at_ms=6 * _MINUTE)
    assert tree_hit is not None and "橡树" in tree_hit.text

    # 共享宽泛前缀“动力”的不同设备失败记录，不得误杀已跑通的“动力压机”
    scoped_records = game_records(
        [
            _event("task_succeeded", "动力压机运行：压出了 4 块铁板", 5 * _MINUTE),
            _event("milestone", "北岸木桥搭好第一根柱子", 4 * _MINUTE),
        ],
        [
            _delegation("undelivered", instruction="修好动力磨粉机", summary="停了但没交付结果", ended_ms=6 * _MINUTE),
            _delegation("undelivered", instruction="修好北岸木桥", summary="停了但没交付结果", ended_ms=6 * _MINUTE),
        ],
    )
    press_hit = infer_proof_for_claim(
        "Minecraft：动力压机压出铁板 动力压机跑通压出铁板啦", scoped_records, game="Minecraft", at_ms=8 * _MINUTE
    )
    assert press_hit is not None and "压机" in press_hit.text
    # 指向同一事项“北岸木桥”的后续未交付记录仍须拦截
    assert (
        infer_proof_for_claim(
            "Minecraft：北岸木桥修好了 北岸木桥已经修好啦", scoped_records, game="Minecraft", at_ms=8 * _MINUTE
        )
        is None
    )


async def test_commitment_closure_requires_explicit_evidence_and_disallows_fuzzy_inference(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """旧承诺结案严禁靠语义兜底放行：未显式填写有效 evidence 时保持 open，而档案行仍可由真实单步成功兜底核实。"""
    session_id = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=30 * _MINUTE)
    comm_id = await store.commitments.insert_commitment(
        what="跑通动力压机压出铁板",
        quote="下次把动力压机跑通压出铁板",
        game="Minecraft",
        now_ms_value=100,
    )
    assert comm_id is not None
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="Minecraft",
        event_type="task_succeeded",
        message="动力压机运行：压出了 4 块铁板",
        timestamp_ms=4 * _MINUTE,
    )
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=6 * _MINUTE,
        sender_role="assistant",
        content="动力压机修好压出铁板啦",
        message_type="speech",
    )
    archive = [
        {
            "line": 1,
            "title": "Minecraft：动力压机修好压出铁板",
            "quote": "动力压机修好压出铁板啦",
            "game": "Minecraft",
            "evidence": None,
        }
    ]
    closed = [
        {
            "id": comm_id,
            "line": 1,
            "quote": "动力压机修好压出铁板啦",
            "evidence": None,
        }
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
        clock=lambda: 9_900,
    )

    assert await settler.settle_session(session_id) is True
    lines = await store.archive.list_lines(live_session_id=session_id)
    assert [line.text for line in lines] == ["Minecraft：动力压机修好压出铁板"]
    comm_after = await store.commitments.get_commitment(commitment_id=comm_id)
    assert comm_after is not None and comm_after.status == "open"
    session = await store.sessions.get_live_session(live_session_id=session_id)
    assert session is not None and session.settle_report is not None
    report = SettleReport.model_validate_json(session.settle_report)
    assert any(item.what == f"了结 #{comm_id}" and "游戏记录里没核实到" in item.reason for item in report.unregistered)


async def test_quote_rescue_and_time_order_rebind_avoid_early_preview_line(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """同一段引文在早先出发预告与事后结果汇报两行都出现时，不得错挂早先预告行而误判记录晚于发言；
    若早先发言的引文在事件之后从未再出现，反吹牛时间序红线仍旧生效。"""
    archive_prompt = prompt_manager.render("memory_archive_system")
    assert "结果发生之后、时刻不早于对应【游戏记录】的那一行" in archive_prompt

    session_id = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=60 * _MINUTE)
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="Minecraft",
        event_type="milestone",
        message="矿洞宝箱获得音乐唱片",
        timestamp_ms=20 * _MINUTE,
    )
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="Minecraft",
        event_type="item_delivery",
        message="从箱子取出了 3 个钻石",
        timestamp_ms=20 * _MINUTE + 17_000,
    )
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="Minecraft",
        event_type="task_succeeded",
        message="黑曜石门框搭建完成",
        timestamp_ms=24 * _MINUTE,
    )
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="Minecraft",
        event_type="milestone",
        message="清空秘银宝箱",
        timestamp_ms=35 * _MINUTE,
    )

    chat_early_preview = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=11 * _MINUTE,
        sender_role="assistant",
        content="出发下矿开箱，看看能不能摸到音乐唱片、凑齐三颗钻石",
        message_type="speech",
    )
    chat_early_portal = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=12 * _MINUTE,
        sender_role="assistant",
        content="回头把黑曜石门框搭好准备去下界",
        message_type="speech",
    )
    chat_early_bluff = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=13 * _MINUTE,
        sender_role="assistant",
        content="秘银宝箱已经全部清完啦",
        message_type="speech",
    )
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=19 * _MINUTE,
        sender_role="assistant",
        content="顺着矿洞继续往深处探索",
        message_type="speech",
    )
    chat_after_event = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=21 * _MINUTE,
        sender_role="assistant",
        content="真下矿开箱摸到音乐唱片、凑齐三颗钻石啦",
        message_type="speech",
    )
    chat_after_portal = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=25 * _MINUTE,
        sender_role="assistant",
        content="黑曜石门框搭好啦，随时可以点火",
        message_type="speech",
    )

    archive = [
        # 场景 1：行号偏移（填了相邻第 4 行而非第 5 行），全场检索兜底须落到事件后的第 5 行而非早 9 分钟的第 1 行
        {
            "line": 4,
            "title": "Minecraft：下矿开箱摸到音乐唱片、凑齐三颗钻石",
            "quote": "摸到音乐唱片、凑齐三颗钻石",
            "game": "Minecraft",
            "evidence": "记录#1、记录#2",
        },
        # 场景 2：模型直接填了早先预告第 2 行，但同文原话在第 6 行事后发言逐字存在且能通过核验 -> 改挂第 6 行
        {
            "line": 2,
            "title": "Minecraft：黑曜石门框搭好了",
            "quote": "黑曜石门框搭好",
            "game": "Minecraft",
            "evidence": "记录#3",
        },
        # 场景 3：早先发言原话在事件（35 分钟）后从未再次播出 -> 反吹牛红线仍拦截，保持原行并标未核实
        {
            "line": 3,
            "title": "Minecraft：秘银宝箱全部清完",
            "quote": "秘银宝箱已经全部清完啦",
            "game": "Minecraft",
            "evidence": "记录#4",
        },
    ]
    llm = FakeLLMManager(
        responses=[
            Response(success=True, content=json.dumps({"archive": archive}, ensure_ascii=False)),
            Response(success=True, content='{"commitments": []}'),
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
        clock=lambda: 10_000,
    )

    assert await settler.settle_session(session_id) is True
    lines = await store.archive.list_lines(live_session_id=session_id)
    assert [(line.text, line.evidence_chat_id) for line in lines] == [
        ("Minecraft：下矿开箱摸到音乐唱片、凑齐三颗钻石", chat_after_event),
        ("Minecraft：黑曜石门框搭好了", chat_after_portal),
        ("Minecraft：她说秘银宝箱全部清完（游戏记录里没核实到）", chat_early_bluff),
    ]
    assert all(line.evidence_chat_id not in (chat_early_preview, chat_early_portal) for line in lines)
    session = await store.sessions.get_live_session(live_session_id=session_id)
    assert session is not None and session.settle_report is not None
    report = SettleReport.model_validate_json(session.settle_report)
    assert report.unverified_claims == 1
    assert len(report.quote_rescues) == 2
    assert any("第 5 行" in note and "非所引第 4 行" in note for note in report.quote_rescues)
    assert any("第 6 行" in note and "非所引第 2 行" in note for note in report.quote_rescues)
