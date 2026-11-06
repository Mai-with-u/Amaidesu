"""下播整理：取料、三次调用的代码校验、失败重试与整理报告。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator, List, Optional, Tuple

import pytest

from src.agents.streamer.memory.settle import MAX_ATTEMPTS, SessionSettler, SettleError, parse_json_object
from src.modules.llm.payload import Response
from src.modules.prompts import PromptManager
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import SettleReport
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
        archive_repo=store.archive,
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
    # 三次调用各一份提示词，第三次只在本场之前有未了结承诺时才调；都走单独的整理档位
    assert [call["method"] for call in llm.calls] == ["generate", "generate", "generate"]
    assert {call["profile"] for call in llm.calls} == {"settle"}


async def test_close_call_asks_for_all_four_kinds_of_closure(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """了结那次调用的问法要列全四种了结，与了结系统提示词一致。

    只问"做完了或不做了"时，她当众坦白"那个我真弄不来"的承诺了结不了，一直留在简报里。
    """
    session_id = await _ended_session(store)
    old = await store.commitments.insert_commitment(what="画生日贺图", quote="下播给你画张生日贺图", now_ms_value=100)
    assert old is not None
    await _speech(store, session_id, "生日贺图那个我是真弄不来，跟你说声抱歉", timestamp_ms=1_100)

    llm = _reply(
        '{"archive": []}',
        '{"commitments": []}',
        '{"closed": [{"id": %d, "line": 1, "quote": "生日贺图那个我是真弄不来"}]}' % old,
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    close_prompt = llm.calls[2]["input"]
    for kind in ("做完了", "不做了", "坦白做不到", "换了个方式兑现"):
        assert kind in close_prompt
    closed = await store.commitments.get_commitment(commitment_id=old)
    assert closed is not None and closed.status == "closed"


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


async def test_game_commitment_sticks_to_enabled_game_without_session_events(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """开错后马上重开的一两分钟里一条游戏事件都没记下：承诺明明是游戏里的事，挂到启用了的游戏
    Agent 上照样算数，不会因为挂不上游戏而进常驻简报、不玩这个游戏时也被念叨。"""
    session_id = await _ended_session(store)
    await _speech(store, session_id, "这口气我记着了，下次接着救那块墓碑的女仆")
    llm = _reply(
        '{"archive": []}',
        '{"commitments": [{"line": 1, "quote": "下次接着救那块墓碑的女仆",'
        ' "what": "接着救墓碑女仆", "to": "everyone", "game": "Minecraft", "same_as": null}]}',
    )
    settler = _settler(store, llm, prompt_manager, games=["Minecraft"])

    assert await settler.settle_session(session_id) is True
    commitments = await store.commitments.list_commitments()
    assert len(commitments) == 1 and commitments[0].game == "Minecraft"


async def test_archive_and_commitments_carry_the_save_they_were_said_in(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """一场里从老档换到新档：挂游戏的档案与承诺按那句话说出口的时刻记下存档，不挂游戏的不记；
    模型看到事件标着存档、换过存档有说明、旧承诺标着挂在哪个存档。"""
    session_id = await _ended_session(store)
    old = await store.commitments.insert_commitment(
        what="把北边的桥修完",
        quote="桥我下次一定修完",
        game="Minecraft",
        save_id="a",
        save_name="老档",
        now_ms_value=100,
    )
    assert old is not None
    for at_ms, save_id, save_name, message in (
        (1_150, "a", "老档", "麦田收完了"),
        (1_500, "b", "新档", "进了新世界"),
    ):
        await store.events.insert_game_event(
            live_session_id=session_id,
            game="Minecraft",
            event_type="milestone",
            message=message,
            timestamp_ms=at_ms,
            save_id=save_id,
            save_name=save_name,
        )
    await _speech(store, session_id, "麦子全收进仓库了，一粒没剩", timestamp_ms=1_200)
    await _speech(store, session_id, "新世界出生点旁边就有村子，下回先去村里换绿宝石", timestamp_ms=1_600)
    await _speech(store, session_id, "今天的猜谜大家猜得好准", timestamp_ms=1_700)
    llm = _reply(
        '{"archive": ['
        '{"line": 1, "title": "Minecraft：麦子收完", "quote": "麦子全收进仓库了", "game": "Minecraft"},'
        '{"line": 2, "title": "Minecraft：出生点旁边有村子", "quote": "新世界出生点旁边就有村子", "game": "Minecraft「新档」"},'
        '{"line": 3, "title": "猜谜环节", "quote": "今天的猜谜大家猜得好准", "game": null}]}',
        '{"commitments": [{"line": 2, "quote": "下回先去村里换绿宝石",'
        ' "what": "去村里换绿宝石", "to": "everyone", "game": "Minecraft", "same_as": null}]}',
        '{"closed": []}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True

    lines = await store.archive.list_lines(live_session_id=session_id)
    assert [(line.text, line.game, line.save_id, line.save_name) for line in lines] == [
        ("Minecraft：麦子收完", "Minecraft", "a", "老档"),
        ("Minecraft：出生点旁边有村子", "Minecraft", "b", "新档"),
        ("猜谜环节", None, "", ""),
    ]
    added = [item for item in await store.commitments.list_commitments(statuses=["open"]) if item.id != old]
    assert [(item.what, item.save_id, item.save_name) for item in added] == [("去村里换绿宝石", "b", "新档")]
    commitment_prompt = llm.calls[1]["input"]
    assert "[Minecraft「老档」] 麦田收完了" in commitment_prompt
    assert "Minecraft 这一场待过不止一个存档：「老档」「新档」" in commitment_prompt
    assert (
        "#%d 「把北边的桥修完」（当初的原话「桥我下次一定修完」；挂在 Minecraft 的存档「老档」）" % old
        in commitment_prompt
    )


async def test_archive_lines_get_checked_keywords_and_places(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """模型提名触发词与地点，代码核对：触发词要在标题或原话里；地点要说到了地名、或说话时就在那里。"""
    session_id = await _ended_session(store)
    for kind, place, at_ms in (
        ("remembered", "海边营地", 1_050),
        ("near", "海边营地", 1_100),
        ("left", "海边营地", 1_400),
    ):
        await store.events.insert_place_mark(
            live_session_id=session_id, game="Minecraft", kind=kind, place=place, timestamp_ms=at_ms
        )
    await _speech(store, session_id, "灯塔终于盖好了，晚上亮起来真好看", timestamp_ms=1_200)
    await _speech(store, session_id, "回海边营地的路上捡到一只小猫", timestamp_ms=1_500)
    await _speech(store, session_id, "这只猫就叫团子吧", timestamp_ms=1_600)
    llm = _reply(
        '{"archive": ['
        '{"line": 1, "title": "Minecraft：灯塔盖好了", "quote": "灯塔终于盖好了", "game": "Minecraft",'
        ' "keywords": ["灯塔", "晚上亮", "房子"], "place": "海边营地"},'
        '{"line": 2, "title": "Minecraft：捡到小猫", "quote": "捡到一只小猫", "game": "Minecraft",'
        ' "keywords": ["小猫"], "place": "海边营地"},'
        '{"line": 3, "title": "Minecraft：猫叫团子", "quote": "这只猫就叫团子吧", "game": "Minecraft",'
        ' "keywords": ["团子"], "place": "海边营地"}]}',
        '{"commitments": []}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True

    lines = await store.archive.list_lines(live_session_id=session_id)
    assert [(line.text, line.keywords, line.place) for line in lines] == [
        # 说这句话时就在营地跟前；"晚上亮"只在整句台词里、"房子"哪都没有，都不在标题和原话里，丢掉
        ("Minecraft：灯塔盖好了", ["灯塔"], "海边营地"),
        # 已经离开营地，但台词里说到了这个地名
        ("Minecraft：捡到小猫", ["小猫"], "海边营地"),
        # 已经离开、也没说到地名：不挂地点
        ("Minecraft：猫叫团子", ["团子"], ""),
    ]
    assert "【本场记住的地点】\n海边营地" in llm.calls[0]["input"]


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
    # 不新建，但也不静默丢：整理报告里看得见是哪一行、因为点名了哪条旧承诺
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settle_report is not None
    reasons = [item["reason"] for item in json.loads(row.settle_report)["unregistered"]]
    assert reasons == ["这一行提到了旧承诺 约#5，本场没有了结，按又提起旧事处理，未登记"]


async def test_new_way_on_a_line_naming_a_closed_commitment_is_registered(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """她当众说旧的做不到、改用新办法兑现：旧的那条本场了结，同一行里新答应的事照常登记。"""
    session_id = await _ended_session(store)
    old = await store.commitments.insert_commitment(what="画一张头像", quote="下次给你画张头像", now_ms_value=100)
    assert old is not None
    line = "头像我是真画不了，下次用方块给你拼一幅"
    await _speech(store, session_id, line, memory_refs=f'["约#{old}"]')

    llm = _reply(
        '{"archive": []}',
        '{"commitments": [{"line": 1, "quote": "下次用方块给你拼一幅", "what": "用方块拼一幅头像",'
        ' "to": "everyone", "game": null, "same_as": null}]}',
        '{"closed": [{"id": %d, "line": 1, "quote": "头像我是真画不了"}]}' % old,
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True
    closed = await store.commitments.get_commitment(commitment_id=old)
    assert closed is not None and closed.status == "closed"
    opened = await store.commitments.list_commitments(statuses=["open"])
    assert [item.what for item in opened] == ["用方块拼一幅头像"]


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
# 滚动档案快照：对上一份逐行挑留、去掉、替换，只引用原行
# ---------------------------------------------------------------------------


async def _previous_archive(store: SQLiteDatabase) -> int:
    """上一场（滚动之前的旧场次）：取了名、种了菜、炉子没点火。"""
    previous = await _ended_session(store, started_at_ms=1_000)
    for evidence, (title, quote) in enumerate(
        (
            ("Minecraft：女仆取名团子", "从今天起你就叫团子"),
            ("Minecraft：菜地围好了", "菜地我自己围的"),
            ("Minecraft：炉子还没点火", "炉子做好了还没点火"),
        ),
        start=901,
    ):
        await store.archive.insert_line(
            live_session_id=previous, text=title, quote=quote, evidence_chat_id=evidence, game="minecraft"
        )
    return previous


def _snapshot_view(snapshot: Any) -> List[Tuple[int, str, bool]]:
    return [(item.line.live_session_id, item.line.text, item.lasting) for item in snapshot.lines]


async def test_snapshot_keeps_old_rows_as_they_are_and_replaces_with_this_sessions_quote(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """名字与菜地留下（名字重标稳定），"还没点火"换成这一场原话写的新行：快照引用上一场那两行本身，
    本场名下只有替换出的新行；替换写进整理报告。"""
    previous = await _previous_archive(store)
    before = await store.archive.list_lines(live_session_id=previous)
    session_id = await _ended_session(store, started_at_ms=5_000_000)
    await _speech(store, session_id, "炉子点上火了，海带烤上啦", timestamp_ms=5_000_100)

    llm = _reply(
        '{"previous": [{"old": 1, "op": "keep", "lasting": true},'
        ' {"old": 3, "op": "replace", "line": 1, "title": "Minecraft：炉子点火烤海带", "quote": "炉子点上火了"}],'
        ' "archive": []}',
        '{"commitments": []}',
    )
    settler = _settler(store, llm, prompt_manager)

    assert await settler.settle_session(session_id) is True

    prompt = llm.calls[0]["input"]
    assert "【上一份档案】" in prompt
    assert "旧1 [上一场] Minecraft：女仆取名团子（原话「从今天起你就叫团子」；在 minecraft）" in prompt
    (replacement,) = await store.archive.list_lines(live_session_id=session_id)
    assert (replacement.text, replacement.quote) == ("Minecraft：炉子点火烤海带", "炉子点上火了")
    snapshot = await store.archive.get_snapshot(live_session_id=session_id)
    assert _snapshot_view(snapshot) == [
        (session_id, "Minecraft：炉子点火烤海带", False),
        (previous, "Minecraft：女仆取名团子", True),
        (previous, "Minecraft：菜地围好了", False),
    ]
    assert [item.line for item in snapshot.lines[1:]] == before[:2], "留下的行一个字节都不变"
    row = await store.sessions.get_live_session(live_session_id=session_id)
    report = SettleReport.model_validate_json(row.settle_report or "{}")
    assert [(change.action, change.what) for change in report.snapshot_changes] == [
        ("replace", "Minecraft：炉子还没点火")
    ]


async def test_dropping_an_old_row_needs_evidence_from_this_session(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """拿"一次性遭遇"当理由的去掉（哪怕指了本场台词）、拿"这场没提到"当理由的去掉都不采纳，
    旧行照留；理由写明了结且有本场证据的才去掉；认不出的编号也只进整理报告。"""
    await _previous_archive(store)
    session_id = await _ended_session(store, started_at_ms=5_000_000)
    await _speech(store, session_id, "那个炉子我拆了，不要了", timestamp_ms=5_000_100)
    llm = _reply(
        '{"previous": [{"old": 1, "op": "drop", "reason": "一次性遭遇", "line": 1},'
        ' {"old": 2, "op": "drop", "reason": "这场没提到", "line": 1},'
        ' {"old": 3, "op": "drop", "reason": "了结：她说炉子拆了", "line": 1}, {"old": 9, "op": "drop"}],'
        ' "archive": []}',
        '{"commitments": []}',
    )

    assert await _settler(store, llm, prompt_manager).settle_session(session_id) is True

    snapshot = await store.archive.get_snapshot(live_session_id=session_id)
    assert [item.line.text for item in snapshot.lines] == ["Minecraft：女仆取名团子", "Minecraft：菜地围好了"]
    row = await store.sessions.get_live_session(live_session_id=session_id)
    report = SettleReport.model_validate_json(row.settle_report or "{}")
    assert [(change.action, change.what) for change in report.snapshot_changes] == [
        ("refused", "Minecraft：女仆取名团子"),
        ("refused", "Minecraft：菜地围好了"),
        ("drop", "Minecraft：炉子还没点火"),
        ("refused", "旧9"),
    ]
    assert report.snapshot_changes[2].quote == "那个炉子我拆了，不要了"


async def test_thin_session_and_empty_output_keep_the_previous_snapshot(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """又薄又乱的一场，模型什么都没挑（输出里没有 previous）：上一份原样留着，一行都不少。"""
    await _previous_archive(store)
    session_id = await _ended_session(store, started_at_ms=5_000_000)
    await _speech(store, session_id, "诶又卡了，我重开一下", timestamp_ms=5_000_100)
    settler = _settler(store, _reply("{}", '{"commitments": []}'), prompt_manager)

    assert await settler.settle_session(session_id) is True
    snapshot = await store.archive.get_snapshot(live_session_id=session_id)
    assert [item.line.text for item in snapshot.lines] == [
        "Minecraft：女仆取名团子",
        "Minecraft：菜地围好了",
        "Minecraft：炉子还没点火",
    ]
    assert await store.archive.list_lines(live_session_id=session_id) == []


async def test_previous_that_is_not_a_list_fails_the_session(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """previous 不是列表按输出读不懂处理：整场不提交、重试次数加一，不写本场快照，简报仍读上一份。"""
    previous = await _previous_archive(store)
    session_id = await _ended_session(store, started_at_ms=5_000_000)
    await _speech(store, session_id, "今天就聊聊天", timestamp_ms=5_000_100)
    settler = _settler(store, _reply('{"previous": "全部", "archive": []}'), prompt_manager)

    assert await settler.settle_session(session_id) is False
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settled_at_ms is None and row.settle_attempts == 1
    assert "previous 不是列表" in (row.settle_report or "")
    assert await store.archive.get_snapshot(live_session_id=session_id) is None
    assert (await store.archive.latest_snapshot()).owner_session_id == previous


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


# ---------------------------------------------------------------------------
# 离线评测器跟得上生产整理器
# ---------------------------------------------------------------------------


def test_memory_eval_dry_self_check_passes() -> None:
    """离线评测器（scripts/memory_eval）回放生产整理器，仓储用替身：生产取料改了接口而替身没跟上，
    旧承诺就静默缺席，发版门槛的 live 评测里"了结""又说了一遍"这类用例全部失真。dry 自检不调模型，
    按生产流程把四份样本集过一遍，失败就说明替身或样本跟生产对不上了。"""
    script = Path(__file__).resolve().parents[3] / "scripts" / "memory_eval" / "run_eval.py"
    completed = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=300, check=False
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]


def test_archive_game_eval_dry_self_check_passes() -> None:
    """档案挂游戏的评测器（scripts/memory_eval/archive_game.py）同样回放生产整理器：档案提示词、
    取料或档案校验改了而评测没跟上，live 量出的游戏归属准确率就不是生产的真实水平。"""
    script = Path(__file__).resolve().parents[3] / "scripts" / "memory_eval" / "archive_game.py"
    completed = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=300, check=False
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]


def test_archive_rolling_eval_dry_self_check_passes() -> None:
    """档案快照多场序列评测器（scripts/memory_eval/archive_rolling.py）在真实库上跑生产整理器：快照的
    写入、取舍或档案提示词改了而评测没跟上，live 量出的遗忘率就不是生产的真实水平。"""
    script = Path(__file__).resolve().parents[3] / "scripts" / "memory_eval" / "archive_rolling.py"
    completed = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=300, check=False
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]


# 假端点每次都回同一份空档案；用量取固定值，测试按它核对每次调用都记上了
_FAKE_USAGE = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}


class _FakeChatCompletionsHandler(BaseHTTPRequestHandler):
    """本机假的 OpenAI 兼容端点：收到任何对话补全请求都回一份空档案，并带上 token 用量。"""

    def do_POST(self) -> None:  # noqa: N802 - http.server 规定的方法名
        # 先把请求体读完再回话，免得客户端在写请求时被提前断开
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps(
            {
                "id": "fake",
                "object": "chat.completion",
                "created": 0,
                "model": "fake-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"previous": [], "archive": []}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": _FAKE_USAGE,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 覆盖基类的同名参数
        # 每次请求的访问日志对测试没有信息量，不往输出里打
        return


@pytest.fixture()
def fake_model_endpoint() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeChatCompletionsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _run_eval_live_once(script_name: str, base_url: str, out: Path) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).resolve().parents[3] / "scripts" / "memory_eval" / script_name
    # 本机配了代理时，发往 127.0.0.1 的请求也绕开代理，直达假端点
    env = {**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    return subprocess.run(
        [
            sys.executable,
            str(script),
            "--live",
            "--limit",
            "1",
            "--out",
            str(out),
            "--model",
            "fake-model",
            "--api-key",
            "fake-key",
            "--base-url",
            base_url,
            "--timeout",
            "30",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
        env=env,
    )


def test_archive_rolling_eval_live_path_reaches_the_model(fake_model_endpoint: str, tmp_path: Path) -> None:
    """档案快照多场序列评测器的 live 入口真能把请求发出去、把用量记下来。

    它借用找承诺评测器的模型调用；那边改了调用参数而这边没跟上时，每场"写档案"都在发请求之前
    抛错，被整理器记成整理失败——报告看起来像模型把稳定的事全忘了，门槛评测量到的就不是提示词。
    dry 自检不走这条路，所以用本机假端点跑一条序列，只看请求到没到、用量记没记；假端点回的是空
    档案，遗忘率本来就不达标，不看指标。"""
    out = tmp_path / "rolling.json"
    completed = _run_eval_live_once("archive_rolling.py", fake_model_endpoint, out)
    assert out.exists(), completed.stdout[-3000:] + completed.stderr[-3000:]
    report = json.loads(out.read_text(encoding="utf-8"))
    settle_failures = [problem for case in report["cases"] for problem in case["problems"] if "整理失败" in problem]
    assert settle_failures == []
    usage = report["usage"]
    assert usage["calls"] >= 1 and usage["missing_calls"] == 0
    assert usage["total_tokens"] == _FAKE_USAGE["total_tokens"] * usage["calls"]


def test_archive_game_eval_live_path_reaches_the_model(fake_model_endpoint: str, tmp_path: Path) -> None:
    """档案挂游戏评测器的 live 入口同样借用找承诺评测器的模型调用，参数没跟上时每条用例都记成调用
    失败，量出的游戏归属准确率是 0 而不是提示词的真实水平。"""
    out = tmp_path / "game.json"
    completed = _run_eval_live_once("archive_game.py", fake_model_endpoint, out)
    assert out.exists(), completed.stdout[-3000:] + completed.stderr[-3000:]
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["errors"] == []
    assert report["usage"]["calls"] == 1 and report["usage"]["missing_calls"] == 0
    assert report["usage"]["total_tokens"] == _FAKE_USAGE["total_tokens"]


# 例句与评测台词共用的连续字数达到这么多，就算"同一句"：短于它的多是"下次一定"这类说法标签，本来就该共用。
_SHARED_RUN_CHARS = 6


def test_memory_prompt_examples_stay_out_of_eval_datasets() -> None:
    """记忆提示词里的例句不能出现在评测样本的台词里。

    评测量的是提示词对没见过的说法能不能判对：例句和样本是同一句话时，这条样本必然判对，
    召回与误登记率被它抬高，发版门槛第 1 步就量不出真实泛化能力。真实场次里抓到的说法留在
    评测集，提示词只写类型说明，配另外编的例句。
    """
    root = Path(__file__).resolve().parents[3]
    # （样本集名, 台词）：只拿台词比对，样本集名只用来报出是哪一份——名字里的英文词会撞上 JSON 示例的键
    speech: List[Tuple[str, str]] = []
    for dataset in sorted((root / "scripts" / "memory_eval" / "datasets").glob("*.jsonl")):
        for raw in dataset.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                record = json.loads(raw)
                # 多场序列样本的台词按场次分开放
                turns = [
                    *record.get("speech", []),
                    *(turn for session in record.get("sessions", []) for turn in session.get("speech", [])),
                ]
                speech.extend((dataset.stem, line["text"]) for line in turns)
    shared: List[str] = []
    for prompt in sorted((root / "src" / "agents" / "streamer" / "prompts").glob("memory_*.md")):
        # 例句写在半角双引号里；JSON 示例里的键值带冒号或逗号，排除掉
        for example in re.findall(r'"([^"\n{}:,]+)"', prompt.read_text(encoding="utf-8")):
            runs = {example[i : i + _SHARED_RUN_CHARS] for i in range(len(example) - _SHARED_RUN_CHARS + 1)}
            shared.extend(
                f"{prompt.name}「{example}」↔ {stem}:{text}"
                for stem, text in speech
                if any(run in text for run in runs)
            )
    assert shared == []


# ---------------------------------------------------------------------------
# 观众候选：观众定下、她当场接受的事，人批准才进记忆
# ---------------------------------------------------------------------------


async def test_viewer_candidates_are_listed_from_her_replies_and_kept_out_of_memory(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """档案那次调用顺带列候选：只认她回复真实观众弹幕的那几行，两段原话逐字核对；
    含个人信息的整条丢掉只计数；候选进待审区，不进档案行，报告里记下列了几条。"""
    session_id = await _ended_session(store)
    named = await _viewer_message(store, session_id, content="以后打卡就说麦麦早吧", message_id="m1")
    await _speech(store, session_id, "好呀就这么定了", reply_to_message_id="m1", timestamp_ms=1_100)
    await _viewer_message(store, session_id, content="我住在幸福小区三栋", message_id="m2")
    await _speech(store, session_id, "记住啦", reply_to_message_id="m2", timestamp_ms=1_200)
    await _viewer_message(store, session_id, content="叫它年糕", message_id="m3", simulated=True)
    await _speech(store, session_id, "年糕也行", reply_to_message_id="m3", timestamp_ms=1_300)

    llm = _reply(
        '{"archive": [], "viewer_candidates": ['
        '{"line": 1, "title": "和观众3一起把打卡口号定成麦麦早", "viewer_quote": "打卡就说麦麦早", "quote": "就这么定了"},'
        '{"line": 2, "title": "观众住哪", "viewer_quote": "我住在幸福小区三栋", "quote": "记住啦"},'
        '{"line": 3, "title": "狗叫年糕", "viewer_quote": "叫它年糕", "quote": "年糕也行"},'
        '{"line": 1, "title": "又列一遍", "viewer_quote": "麦麦早", "quote": "好呀"}]}',
        '{"commitments": []}',
    )
    assert await _settler(store, llm, prompt_manager).settle_session(session_id) is True

    (candidate,) = await store.viewer_candidates.list_candidates()
    assert (candidate.title, candidate.viewer_chat_id, candidate.viewer_quote, candidate.reply_quote) == (
        "和观众一起把打卡口号定成麦麦早",
        named,
        "打卡就说麦麦早",
        "就这么定了",
    ), "代号换成观众；同一行只列一条"
    assert await store.archive.list_lines(live_session_id=session_id) == [], "批准前不进档案"
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settle_report is not None
    report = SettleReport.model_validate_json(row.settle_report)
    assert (report.candidates_listed, report.candidates_personal_dropped) == ({"viewer_defined": 1}, 1)
    assert [item.reason for item in report.unregistered if item.kind == "candidate"] == [
        "引用的不是她回复观众弹幕的那一行"
    ], "模拟观众说的不算观众定下的"
    assert "幸福小区" not in row.settle_report, "个人信息不写进报告"


async def test_rejected_candidates_and_archived_lines_are_not_listed_again(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    session_id = await _ended_session(store)
    await _viewer_message(store, session_id, content="小羊叫雪球", message_id="m1")
    await _speech(store, session_id, "好，这只羊就叫雪球", reply_to_message_id="m1", timestamp_ms=1_100)
    await _viewer_message(store, session_id, content="猫叫芝麻吧", message_id="m2")
    await _speech(store, session_id, "芝麻好听", reply_to_message_id="m2", timestamp_ms=1_200)
    candidates = (
        '"viewer_candidates": ['
        '{"line": 1, "title": "小羊取名雪球", "viewer_quote": "小羊叫雪球", "quote": "就叫雪球"},'
        '{"line": 2, "title": "猫取名芝麻", "viewer_quote": "猫叫芝麻", "quote": "芝麻好听"}]'
    )
    llm = _reply(
        '{"archive": [{"line": 1, "title": "小羊取名雪球", "quote": "这只羊就叫雪球"}], ' + candidates + "}",
        '{"commitments": []}',
    )
    assert await _settler(store, llm, prompt_manager).settle_session(session_id) is True
    (candidate,) = await store.viewer_candidates.list_candidates()
    assert candidate.title == "猫取名芝麻", "她自己说出来、已经写成档案的不再列"
    await store.viewer_candidates.reject(candidate_id=candidate.id, at_ms=8_000)

    # 重新整理：驳回过的同一条弹幕不再列
    await store.sessions.requeue_settlement(live_session_id=session_id)
    llm = _reply('{"archive": [], ' + candidates.replace("小羊取名雪球", "给小羊起名雪球") + "}", '{"commitments": []}')
    assert await _settler(store, llm, prompt_manager).settle_session(session_id) is True
    assert [(item.title, item.status) for item in await store.viewer_candidates.list_candidates(status=None)] == [
        ("给小羊起名雪球", "pending"),
        ("猫取名芝麻", "rejected"),
    ]
