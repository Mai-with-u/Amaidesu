"""下播整理：取料、三次调用的代码校验、失败重试与整理报告。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, List, Optional

import pytest

from src.agents.streamer.memory.settle import MAX_ATTEMPTS, SessionSettler, SettleError, parse_json_object
from src.modules.llm.payload import Response
from src.modules.prompts import PromptManager
from src.modules.storage.database import SQLiteDatabase
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


def _settler(
    store: SQLiteDatabase,
    llm: Any,
    prompt_manager: PromptManager,
    *,
    games: Optional[List[str]] = None,
) -> SessionSettler:
    return SessionSettler(
        sessions_repo=store.sessions,
        chat_repo=store.chat,
        commitments_repo=store.commitments,
        event_repo=store.events,
        llm_service=llm,
        prompt_manager=prompt_manager,
        game_names_provider=lambda: ["Minecraft"] if games is None else games,
        clock=lambda: 7_777,
    )


async def _ended_session(store: SQLiteDatabase, *, kind: str = "live", started_at_ms: int = 1_000) -> int:
    session_id = await store.sessions.insert_live_session(started_at_ms=started_at_ms, kind=kind)
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=started_at_ms + 1_800_000)
    return session_id


async def _speech(
    store: SQLiteDatabase,
    session_id: int,
    content: str,
    *,
    timestamp_ms: int = 1_100,
    dropped_at_ms: Optional[int] = None,
    reply_to_message_id: Optional[str] = None,
    message_id: Optional[str] = None,
    viewer_id: Optional[int] = None,
    simulated: bool = False,
    memory_refs: Optional[str] = None,
) -> int:
    return await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=timestamp_ms,
        sender_role="assistant",
        content=content,
        message_type="speech",
        message_id=message_id,
        reply_to_message_id=reply_to_message_id,
        dropped_at_ms=dropped_at_ms,
        viewer_id=viewer_id,
        simulated=simulated,
        memory_refs=memory_refs,
    )


async def _viewer_message(
    store: SQLiteDatabase, session_id: int, *, content: str, message_id: str, simulated: bool = False
) -> int:
    return await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_050,
        sender_role="viewer",
        content=content,
        message_type="danmaku",
        message_id=message_id,
        sender_id="u1",
        sender_name="阿杰",
        viewer_id=9,
        simulated=simulated,
    )


def _reply(*contents: str) -> FakeLLMManager:
    return FakeLLMManager(responses=[Response(success=True, content=content) for content in contents])


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------


async def test_settle_writes_archive_new_commitment_and_closure(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    session_id = await _ended_session(store)
    old = await store.commitments.insert_commitment(what="继续炸 TNT", quote="下次接着炸 TNT", now_ms_value=100)
    assert old is not None
    await _speech(store, session_id, "下次接着讲那个鬼故事的结局", timestamp_ms=1_100)
    await _speech(store, session_id, "TNT 炸完啦", timestamp_ms=1_200)
    await store.events.insert_game_event(
        live_session_id=session_id,
        game="Minecraft",
        event_type="milestone",
        message="TNT 炸完了",
        timestamp_ms=1_250,
    )

    llm = _reply(
        '{"archive": [{"line": 1, "title": "答应了讲鬼故事的结局", "quote": "下次接着讲那个鬼故事的结局"}]}',
        '{"commitments": [{"line": 1, "quote": "下次接着讲那个鬼故事的结局",'
        ' "what": "接着讲鬼故事的结局", "to": "everyone", "game": "Minecraft", "same_as": null}]}',
        '{"closed": [{"id": %d, "line": 2, "quote": "TNT 炸完啦"}]}' % old,
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True

    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settled_at_ms == 7_777 and row.settle_report is None
    lines = await store.archive.list_lines(live_session_id=session_id)
    assert [line.text for line in lines] == ["答应了讲鬼故事的结局"]
    # 原料自带小标题时不再叠一层：评测脚本（scripts/memory_eval）按同一份提示词跑
    assert llm.calls[1]["input"].count("【本场玩过的游戏】") == 1
    commitments = await store.commitments.list_commitments(statuses=["open"])
    assert [item.what for item in commitments] == ["接着讲鬼故事的结局"]
    assert commitments[0].game == "Minecraft" and commitments[0].created_session_id == session_id
    closed = await store.commitments.get_commitment(commitment_id=old)
    assert closed is not None and closed.status == "closed" and closed.close_quote == "TNT 炸完啦"
    # 三次调用各一份提示词，第三次只在本场之前有未了结承诺时才调
    assert [call["method"] for call in llm.calls] == ["generate", "generate", "generate"]


async def test_material_numbers_lines_and_resolves_reply_target(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    session_id = await _ended_session(store)
    await _viewer_message(store, session_id, content="主播下次还炸 TNT 吗", message_id="m812")
    await _speech(store, session_id, "炸，下次接着炸", reply_to_message_id="m812")

    llm = _reply('{"archive": []}', '{"commitments": []}')
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    prompt = llm.calls[0]["input"]
    assert "#1 " in prompt and "你" in prompt
    assert "回复观众1「主播下次还炸 TNT 吗」" in prompt


# ---------------------------------------------------------------------------
# 校验：引文、游戏、对谁、不重复
# ---------------------------------------------------------------------------


async def test_quote_must_be_verbatim_else_reported(store: SQLiteDatabase, prompt_manager: PromptManager) -> None:
    session_id = await _ended_session(store)
    await _speech(store, session_id, "下次接着讲那个鬼故事的结局")
    llm = _reply(
        '{"archive": [{"line": 1, "title": "改写过的话", "quote": "下次讲鬼故事"}]}',
        '{"commitments": []}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    assert await store.archive.list_lines(live_session_id=session_id) == []
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settle_report is not None
    report = json.loads(row.settle_report)
    assert report["unregistered"][0]["kind"] == "archive"
    assert "逐字片段" in report["unregistered"][0]["reason"]


async def test_game_outside_session_is_dropped(store: SQLiteDatabase, prompt_manager: PromptManager) -> None:
    session_id = await _ended_session(store)
    await _speech(store, session_id, "等我把屋顶盖完就请大家来参观")
    llm = _reply(
        '{"archive": []}',
        '{"commitments": [{"line": 1, "quote": "等我把屋顶盖完就请大家来参观",'
        ' "what": "请大家参观屋顶", "to": "everyone", "game": "不是本场玩的", "same_as": null}]}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    commitments = await store.commitments.list_commitments()
    assert len(commitments) == 1 and commitments[0].game is None


async def test_commitment_to_person_without_reply_target_is_reported(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    session_id = await _ended_session(store)
    await _speech(store, session_id, "行，下次给你盖个小房子")
    llm = _reply(
        '{"archive": []}',
        '{"commitments": [{"line": 1, "quote": "行，下次给你盖个小房子",'
        ' "what": "给 ta 盖小房子", "to": "person", "game": null, "same_as": null}]}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    assert await store.commitments.count_commitments() == 0
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settle_report is not None
    report = json.loads(row.settle_report)
    assert report["unregistered"][0]["kind"] == "commitment"
    assert "没有回应任何弹幕" in report["unregistered"][0]["reason"]


async def test_commitment_to_person_with_reply_binds_viewer(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    session_id = await _ended_session(store)
    await _viewer_message(store, session_id, content="能不能给我盖个小房子", message_id="m900")
    await _speech(store, session_id, "行，下次给你盖个小房子", reply_to_message_id="m900")
    llm = _reply(
        '{"archive": []}',
        '{"commitments": [{"line": 1, "quote": "行，下次给你盖个小房子",'
        ' "what": "给 ta 盖小房子", "to": "person", "game": null, "same_as": null}]}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    commitments = await store.commitments.list_commitments()
    assert len(commitments) == 1 and commitments[0].viewer_id == 9


async def test_same_as_void_goes_to_report_and_open_is_skipped(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    session_id = await _ended_session(store)
    voided = await store.commitments.insert_commitment(what="判过误记的事", now_ms_value=100)
    opened = await store.commitments.insert_commitment(what="还没了结的事", now_ms_value=100)
    assert voided is not None and opened is not None
    await store.commitments.void_commitment(commitment_id=voided)
    await _speech(store, session_id, "那个明天再做吧")

    llm = _reply(
        '{"archive": []}',
        '{"commitments": ['
        '{"line": 1, "quote": "那个明天再做吧", "what": "明天再做", "to": "everyone",'
        ' "game": null, "same_as": %d},'
        '{"line": 1, "quote": "那个明天再做吧", "what": "又说一遍", "to": "everyone",'
        ' "game": null, "same_as": %d}]}' % (voided, opened),
        '{"closed": []}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    assert await store.commitments.count_commitments() == 2  # 只多出被作废那条的墓碑与原有那条
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settle_report is not None
    report = json.loads(row.settle_report)
    assert len(report["unregistered"]) == 1
    assert "已判误记" in report["unregistered"][0]["reason"]


async def test_memory_refs_line_is_not_registered_again(store: SQLiteDatabase, prompt_manager: PromptManager) -> None:
    session_id = await _ended_session(store)
    await _speech(store, session_id, "接着讲上次那个鬼故事", memory_refs='["约#5"]')
    llm = _reply(
        '{"archive": []}',
        '{"commitments": [{"line": 1, "quote": "接着讲上次那个鬼故事", "what": "讲鬼故事",'
        ' "to": "everyone", "game": null, "same_as": null}]}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    assert await store.commitments.count_commitments() == 0


async def test_dropped_speech_is_not_material(store: SQLiteDatabase, prompt_manager: PromptManager) -> None:
    session_id = await _ended_session(store)
    await _speech(store, session_id, "没送到观众面前的这句", dropped_at_ms=1_150)
    await _speech(store, session_id, "送到了的这句", timestamp_ms=1_300)
    llm = _reply(
        '{"archive": [{"line": 1, "title": "当成第 1 行", "quote": "没送到观众面前的这句"}]}',
        '{"commitments": []}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    prompt = llm.calls[0]["input"]
    assert "没送到观众面前的这句" not in prompt
    assert await store.archive.list_lines(live_session_id=session_id) == []


# ---------------------------------------------------------------------------
# 失败与调度
# ---------------------------------------------------------------------------


async def test_model_failure_records_attempt_and_reason(store: SQLiteDatabase, prompt_manager: PromptManager) -> None:
    session_id = await _ended_session(store)
    await _speech(store, session_id, "下次接着讲那个鬼故事的结局")
    settler = _settler(store, FakeLLMManager(fail=True), prompt_manager)

    assert await settler.settle_session(session_id) is False
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None
    assert row.settled_at_ms is None and row.settle_attempts == 1
    report = json.loads(row.settle_report or "{}")
    assert "模型调用失败" in report["failure_reason"]


async def test_settle_stops_after_attempt_limit(store: SQLiteDatabase, prompt_manager: PromptManager) -> None:
    session_id = await _ended_session(store)
    await _speech(store, session_id, "下次接着讲那个鬼故事的结局")
    settler = _settler(store, FakeLLMManager(fail=True), prompt_manager)

    for _ in range(MAX_ATTEMPTS):
        assert await settler.settle_session(session_id) is False
    llm_after = _settler(store, FakeLLMManager(), prompt_manager)
    assert await llm_after.settle_pending() == 0
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settle_attempts == MAX_ATTEMPTS


async def test_session_without_speech_is_settled_without_model_call(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    session_id = await _ended_session(store)
    llm = FakeLLMManager()
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    assert llm.calls == []
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settled_at_ms == 7_777


async def test_rehearsal_session_is_not_settled(store: SQLiteDatabase, prompt_manager: PromptManager) -> None:
    session_id = await _ended_session(store, kind="rehearsal")
    await _speech(store, session_id, "彩排里说的话")
    llm = FakeLLMManager()
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is False
    assert llm.calls == []
    assert await settler.settle_pending() == 0


async def test_settling_the_same_session_twice_writes_archive_lines_once(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """同一场被整理两次（重新整理与启动补跑撞车）：第二次直接跳过，不重复写。

    已整理场次在入口按 settled_at_ms 跳过（不白跑模型调用）；即使绕过入口，
    写入侧也按"已完成"抢占挡下（written 全为 0），库里只保留第一份整理结果。
    """
    session_id = await _ended_session(store)
    await _speech(store, session_id, "下次接着讲那个鬼故事的结局")
    archive = '{"archive": [{"line": 1, "title": "答应了讲鬼故事的结局", "quote": "下次接着讲那个鬼故事的结局"}]}'
    llm = _reply(archive, '{"commitments": []}', archive, '{"commitments": []}')
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    assert [line.text for line in await store.archive.list_lines(live_session_id=session_id)] == [
        "答应了讲鬼故事的结局"
    ]

    assert await settler.settle_session(session_id) is False, "已整理场次应直接跳过"
    lines = await store.archive.list_lines(live_session_id=session_id)
    assert [line.text for line in lines] == ["答应了讲鬼故事的结局"], "重复整理不重复写档案行"
    assert await store.commitments.count_commitments() == 0
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settled_at_ms == 7_777 and row.settle_report is None


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"archive": []}',
        '好的，给你结果：\n{"archive": []}',
        '\n```json\n{"archive": []}\n```\n',
    ],
)
def test_parse_json_object_tolerates_preamble_and_fences(text: str) -> None:
    assert parse_json_object(text) == {"archive": []}


@pytest.mark.parametrize("text", ["", "没有对象", "{不是合法 JSON}"])
def test_parse_json_object_rejects_unreadable(text: str) -> None:
    with pytest.raises(SettleError):
        parse_json_object(text)


# ---------------------------------------------------------------------------
# 编号写法、引文口径与重跑替换
# ---------------------------------------------------------------------------


async def test_same_as_accepts_hashed_commitment_refs_and_reports_unreadable(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """模型把旧承诺编号照抄成 约#N / #N 都要认；档#N 与乱写进未登记清单，不悄悄忽略。"""
    session_id = await _ended_session(store)
    opened = await store.commitments.insert_commitment(what="还没了结的事", now_ms_value=100)
    voided = await store.commitments.insert_commitment(what="判过误记的事", now_ms_value=100)
    assert opened is not None and voided is not None
    await store.commitments.void_commitment(commitment_id=voided)
    await _speech(store, session_id, "那个明天再做吧")

    llm = _reply(
        '{"archive": []}',
        '{"commitments": ['
        '{"line": 1, "quote": "那个明天再做吧", "what": "又说一遍作废的", "to": "everyone",'
        ' "game": null, "same_as": "约#%d"},'
        '{"line": 1, "quote": "那个明天再做吧", "what": "又说一遍没做完的", "to": "everyone",'
        ' "game": null, "same_as": "#%d"},'
        '{"line": 1, "quote": "那个明天再做吧", "what": "档案编号误填", "to": "everyone",'
        ' "game": null, "same_as": "档#%d"},'
        '{"line": 1, "quote": "那个明天再做吧", "what": "乱填编号", "to": "everyone",'
        ' "game": null, "same_as": "乱写"}]}' % (voided, opened, opened),
        '{"closed": []}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    assert await store.commitments.count_commitments() == 2, "已判误记与又说一遍都不该新建承诺"
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settle_report is not None
    reasons = [item["reason"] for item in json.loads(row.settle_report)["unregistered"]]
    assert any("已判误记" in reason for reason in reasons)
    assert sum("认不出" in reason for reason in reasons) == 2


async def test_closure_accepts_hashed_refs_and_reports_unreadable(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    session_id = await _ended_session(store)
    first = await store.commitments.insert_commitment(what="旧的 A", now_ms_value=100)
    second = await store.commitments.insert_commitment(what="旧的 B", now_ms_value=100)
    third = await store.commitments.insert_commitment(what="旧的 C", now_ms_value=100)
    assert first is not None and second is not None and third is not None
    await _speech(store, session_id, "TNT 炸完啦")

    llm = _reply(
        '{"archive": []}',
        '{"commitments": []}',
        '{"closed": ['
        '{"id": "约#%d", "line": 1, "quote": "TNT 炸完啦"},'
        '{"id": "#%d", "line": 1, "quote": "TNT 炸完啦"},'
        '{"id": "档#%d", "line": 1, "quote": "TNT 炸完啦"},'
        '{"id": "乱写", "line": 1, "quote": "TNT 炸完啦"}]}' % (first, second, third),
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    assert (await store.commitments.get_commitment(commitment_id=first)).status == "closed"
    assert (await store.commitments.get_commitment(commitment_id=second)).status == "closed"
    assert (await store.commitments.get_commitment(commitment_id=third)).status == "open", "认不出的编号不能乱指"
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settle_report is not None
    reasons = [item["reason"] for item in json.loads(row.settle_report)["unregistered"]]
    assert sum("认不出" in reason for reason in reasons) == 2


async def test_empty_literal_same_as_and_hashed_line_still_register(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """same_as 写成 "无" / "null" 按没填处理，照常登记新承诺；台词编号照抄成 "#1" 也认。

    给模型的台词逐行写作 #N，模型常把编号原样抄回；"不是同一件事"也常写成字符串。
    这两种写法以前分别会让承诺进未登记清单、被当成"引用的不是本场台词"。
    """
    session_id = await _ended_session(store)
    await _speech(store, session_id, "下次接着讲鬼故事")
    await _speech(store, session_id, "改天给大家唱首歌", timestamp_ms=1_200)

    llm = _reply(
        '{"archive": []}',
        '{"commitments": ['
        '{"line": "#1", "quote": "下次接着讲鬼故事", "what": "接着讲鬼故事", "to": "everyone",'
        ' "game": null, "same_as": "无"},'
        '{"line": 2, "quote": "改天给大家唱首歌", "what": "给大家唱首歌", "to": "everyone",'
        ' "game": null, "same_as": "null"}]}',
        '{"closed": []}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    rows = await store.commitments.list_commitments(statuses=["open"])
    assert sorted(row.what for row in rows) == ["接着讲鬼故事", "给大家唱首歌"]
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settle_report is None, "两条都正常登记，不该有未登记清单"


async def test_quote_accepts_compressed_whitespace(store: SQLiteDatabase, prompt_manager: PromptManager) -> None:
    """给模型看的是压缩空白后的台词，模型照抄压缩文本也要通过逐字引文校验。"""
    session_id = await _ended_session(store)
    await _speech(store, session_id, "下次   接着讲那个鬼故事的结局\n讲完再睡")

    llm = _reply(
        '{"archive": [{"line": 1, "title": "讲完鬼故事", "quote": "下次 接着讲那个鬼故事的结局"}]}',
        '{"commitments": [{"line": 1, "quote": "下次 接着讲那个鬼故事的结局",'
        ' "what": "讲完鬼故事", "to": "everyone", "game": null, "same_as": null}]}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    assert [line.text for line in await store.archive.list_lines(live_session_id=session_id)] == ["讲完鬼故事"]
    assert await store.commitments.count_commitments(statuses=["open"]) == 1


async def test_requeued_session_replaces_archive_and_commitments(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    session_id = await _ended_session(store)
    await _speech(store, session_id, "下次接着讲鬼故事", timestamp_ms=1_100)
    await _speech(store, session_id, "下次一起盖房子", timestamp_ms=1_200)
    llm = _reply(
        '{"archive": [{"line": 1, "title": "旧档案", "quote": "下次接着讲鬼故事"}]}',
        '{"commitments": [{"line": 1, "quote": "下次接着讲鬼故事", "what": "讲鬼故事",'
        ' "to": "everyone", "game": null, "same_as": null}]}',
        '{"archive": [{"line": 2, "title": "新档案", "quote": "下次一起盖房子"}]}',
        '{"commitments": [{"line": 2, "quote": "下次一起盖房子", "what": "盖房子",'
        ' "to": "everyone", "game": null, "same_as": null}]}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    assert await store.sessions.requeue_settlement(live_session_id=session_id)
    assert await settler.settle_session(session_id) is True

    lines = await store.archive.list_lines(live_session_id=session_id)
    assert [line.text for line in lines] == ["新档案"], "重新整理应整场替换档案行"
    open_whats = [row.what for row in await store.commitments.list_commitments(statuses=["open"])]
    assert open_whats == ["盖房子"], "本场上次登记且未了结的承诺应被替换，不重复登记"


async def test_type_change_during_settle_discards_result(store: SQLiteDatabase, prompt_manager: PromptManager) -> None:
    """整理途中场次被改成彩排：写入侧复核类型，整场结果作废，不记为整理失败。"""
    session_id = await _ended_session(store)
    await _speech(store, session_id, "下次接着讲鬼故事")
    gate = asyncio.Event()
    llm = FakeLLMManager(response=Response(success=True, content='{"archive": []}'), gate=gate)
    settler = _settler(store, llm, prompt_manager)

    task = asyncio.create_task(settler.settle_session(session_id))
    await llm.gate_reached.wait()
    await store.sessions.set_live_session_kind_with_ledger(
        live_session_id=session_id, kind="rehearsal", updated_at_ms=2_000
    )
    gate.set()

    assert await task is False
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.kind == "rehearsal" and row.settled_at_ms is None
    assert await store.archive.list_lines(live_session_id=session_id) == []
    assert await store.commitments.count_commitments() == 0


# ---------------------------------------------------------------------------
# 并发、等待与调度
# ---------------------------------------------------------------------------


async def test_concurrent_settles_of_same_session_share_model_calls(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    session_id = await _ended_session(store)
    await _speech(store, session_id, "下次接着讲鬼故事")
    gate = asyncio.Event()
    llm = FakeLLMManager(response=Response(success=True, content='{"archive": []}'), gate=gate)
    settler = _settler(store, llm, prompt_manager)

    first = asyncio.create_task(settler.settle_session(session_id))
    await llm.gate_reached.wait()
    second = asyncio.create_task(settler.settle_session(session_id))
    await asyncio.sleep(0)
    gate.set()

    assert await asyncio.gather(first, second) == [True, True]
    assert len(llm.calls) == 2, "同一场的并发整理应共享同一串模型调用（档案 + 承诺各一次）"


async def test_wait_settled_joins_inflight_and_returns_false_when_idle(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    session_id = await _ended_session(store)
    await _speech(store, session_id, "下次接着讲鬼故事")
    gate = asyncio.Event()
    llm = FakeLLMManager(response=Response(success=True, content='{"archive": []}'), gate=gate)
    settler = _settler(store, llm, prompt_manager)

    assert await settler.wait_settled(session_id) is False, "没有在途整理时立即返回"

    task = asyncio.create_task(settler.settle_session(session_id))
    await llm.gate_reached.wait()
    waiter = asyncio.create_task(settler.wait_settled(session_id))
    await asyncio.sleep(0)
    gate.set()

    assert await waiter is True
    assert await task is True


async def test_settle_pending_orders_by_start_time_and_continues_after_failure(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    later = await _ended_session(store, started_at_ms=3_000)
    earlier = await _ended_session(store, started_at_ms=1_000)
    middle = await _ended_session(store, started_at_ms=2_000)
    await _speech(store, earlier, "第一场的话", timestamp_ms=1_100)
    await _speech(store, middle, "第二场的话", timestamp_ms=2_100)
    await _speech(store, later, "第三场的话", timestamp_ms=3_100)
    llm = FakeLLMManager(
        responses=[
            Response(success=False, content=None, error="模型挂了"),
            Response(success=True, content='{"archive": []}'),
            Response(success=True, content='{"commitments": []}'),
            Response(success=True, content='{"archive": []}'),
            Response(success=True, content='{"commitments": []}'),
        ]
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_pending() == 2, "一场失败不挡后面的场"
    prompts = [call["input"] for call in llm.calls]
    assert "第一场的话" in prompts[0]
    assert "第二场的话" in prompts[1]
    assert "第三场的话" in prompts[3], "按开播时间从早到晚逐场整理"
    failed = await store.sessions.get_live_session(live_session_id=earlier)
    assert failed is not None and failed.settled_at_ms is None and failed.settle_attempts == 1
    for settled_id in (middle, later):
        row = await store.sessions.get_live_session(live_session_id=settled_id)
        assert row is not None and row.settled_at_ms == 7_777


# ---------------------------------------------------------------------------
# 规则分支：规-14 与规-12
# ---------------------------------------------------------------------------


async def test_closure_of_voided_commitment_is_rejected_and_logged(
    store: SQLiteDatabase, prompt_manager: PromptManager, loguru_capture: Any
) -> None:
    """模型想「了结」运营者已判误记的条目：状态不动，并留下可排查的日志。"""
    session_id = await _ended_session(store)
    voided = await store.commitments.insert_commitment(what="判过误记的事", now_ms_value=100)
    opened = await store.commitments.insert_commitment(what="还没了结的事", now_ms_value=100)
    assert voided is not None and opened is not None
    await store.commitments.void_commitment(commitment_id=voided)
    await _speech(store, session_id, "那个事我做完了")

    llm = _reply(
        '{"archive": []}',
        '{"commitments": []}',
        '{"closed": [{"id": %d, "line": 1, "quote": "那个事我做完了"},'
        ' {"id": %d, "line": 1, "quote": "那个事我做完了"}]}' % (voided, opened),
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    voided_row = await store.commitments.get_commitment(commitment_id=voided)
    assert voided_row is not None and voided_row.status == "void", "运营者判过误记的条目不能被模型了结"
    opened_row = await store.commitments.get_commitment(commitment_id=opened)
    assert opened_row is not None and opened_row.status == "closed", "未了结的承诺正常了结"
    assert any("不是未了结的旧承诺" in record["message"] for record in loguru_capture.records)


async def test_commitment_to_simulated_viewer_is_reported(store: SQLiteDatabase, prompt_manager: PromptManager) -> None:
    """规-12：回应的是模拟观众的弹幕时，不当成对真实观众的承诺。"""
    session_id = await _ended_session(store)
    await _viewer_message(store, session_id, content="能不能给我盖个小房子", message_id="m900", simulated=True)
    await _speech(store, session_id, "行，下次给你盖个小房子", reply_to_message_id="m900")
    llm = _reply(
        '{"archive": []}',
        '{"commitments": [{"line": 1, "quote": "行，下次给你盖个小房子",'
        ' "what": "给 ta 盖小房子", "to": "person", "game": null, "same_as": null}]}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    assert await store.commitments.count_commitments() == 0
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settle_report is not None
    reasons = [item["reason"] for item in json.loads(row.settle_report)["unregistered"]]
    assert any("模拟观众" in reason for reason in reasons)


async def test_cancelling_waiting_caller_does_not_cancel_settlement(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """调用方超时取消只取消自己的等待；共享的整理任务要继续跑完并写出结果。"""
    session_id = await _ended_session(store)
    await _speech(store, session_id, "下次接着讲鬼故事")
    gate = asyncio.Event()
    llm = FakeLLMManager(response=Response(success=True, content='{"archive": []}'), gate=gate)
    settler = _settler(store, llm, prompt_manager)

    runner = asyncio.create_task(settler.settle_session(session_id))
    await llm.gate_reached.wait()
    waiter = asyncio.create_task(settler.settle_session(session_id))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    gate.set()

    assert await runner is True
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settled_at_ms == 7_777
    assert len(llm.calls) == 2, "取消等待不应让整理重跑一遍模型调用"
