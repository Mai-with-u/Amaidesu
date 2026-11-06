"""下播整理里对游戏玩家的承诺：原料里的玩家代号与公屏段，"对谁"的两条证据路径与对不上不登记。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Dict

import pytest

from src.agents.streamer.memory.settle import SessionSettler
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
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "players.db")
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
    """一场：Alex 在公屏邀她挖矿，她播出一句回应 Alex，又在公屏点名答应 Alex、紧接着回 Bob 一句。"""
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session, ended_at_ms=1_800_000)
    repo = store.game_players
    alex = await repo.meet_player(game="minecraft", uuid="uuid-a", name="Alex", live_session_id=session, at_ms=1_010)
    bob = await repo.meet_player(game="minecraft", uuid="uuid-b", name="Bob", live_session_id=session, at_ms=1_020)

    async def chat(direction: str, content: str, at_ms: int, player_id: Any = None) -> int:
        return await repo.add_game_chat(
            live_session_id=session,
            game="minecraft",
            direction=direction,
            content=content,
            player_id=player_id,
            save_id="w1",
            save_name="生存一档",
            at_ms=at_ms,
        )

    await chat("player", "麦麦明天一起挖矿吗", 1_050, alex.id)
    spoken = await store.chat.insert_live_chat(
        live_session_id=session,
        timestamp_ms=1_100,
        sender_role="assistant",
        content="好呀，明天上线就带你去挖矿",
        message_type="speech",
        reply_player_id=alex.id,
    )
    named = await chat("streamer", "Alex 周末我帮你把房子修好", 1_200)
    await chat("player", "我也要", 1_300, bob.id)
    follow = await chat("streamer", "行，下次给你也修一个", 1_310)
    await chat("player", "那我呢", 1_400, alex.id)
    await chat("other", "", 1_410)
    broken = await chat("streamer", "以后再说吧，下次请你吃蛋糕", 1_420)
    return {
        "session": session,
        "alex": alex.id,
        "bob": bob.id,
        "spoken": spoken,
        "named": named,
        "follow": follow,
        "broken": broken,
    }


async def test_material_has_player_codes_and_her_public_chat(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    ids = await _scene(store)
    llm = _reply('{"archive": []}', '{"commitments": []}')
    assert await _settler(store, llm, prompt_manager).settle_session(ids["session"]) is True

    prompt = llm.calls[1]["input"]
    assert "（回应玩家1）：好呀，明天上线就带你去挖矿" in prompt, "回应玩家的台词标上代号"
    assert "【游戏里的玩家】\n玩家1 = Alex；玩家2 = Bob" in prompt
    assert "公屏#1" in prompt and "你：Alex 周末我帮你把房子修好" in prompt
    assert "玩家2：我也要" in prompt and "（认不出是谁的一句）" in prompt
    assert prompt.index("公屏#1") > prompt.index("【本场她说的话】"), "公屏段排在台词之后"


async def test_commitments_to_players_need_a_matching_evidence_path(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    """台词行按记下的回应对象核对；公屏那句按点名或紧接着回话核对；对不上的不登记、进报告。"""
    ids = await _scene(store)
    llm = _reply(
        '{"archive": []}',
        '{"commitments": ['
        '{"line": 1, "chat": null, "quote": "明天上线就带你去挖矿", "what": "带 Alex 去挖矿", "to": "player",'
        ' "player": "玩家1", "game": "minecraft", "same_as": null},'
        '{"line": null, "chat": "公屏#1", "quote": "周末我帮你把房子修好", "what": "帮 Alex 修房子", "to": "player",'
        ' "player": "玩家1", "game": null, "same_as": null},'
        '{"line": null, "chat": "公屏#2", "quote": "下次给你也修一个", "what": "给 Bob 修房子", "to": "player",'
        ' "player": "玩家2", "game": null, "same_as": null},'
        '{"line": null, "chat": "公屏#3", "quote": "下次请你吃蛋糕", "what": "请 Alex 吃蛋糕", "to": "player",'
        ' "player": "玩家1", "game": null, "same_as": null}]}',
    )
    assert await _settler(store, llm, prompt_manager).settle_session(ids["session"]) is True

    rows = sorted(await store.commitments.list_commitments(), key=lambda row: row.id)
    assert [(row.what, row.target_player_id, row.evidence_chat_id, row.evidence_game_chat_id) for row in rows] == [
        ("带 Alex 去挖矿", ids["alex"], ids["spoken"], None),
        ("帮 Alex 修房子", ids["alex"], None, ids["named"]),
        ("给 Bob 修房子", ids["bob"], None, ids["follow"]),
    ]
    # 公屏那句的游戏与存档取它自己的
    assert (rows[1].game, rows[1].save_id, rows[1].save_name) == ("minecraft", "w1", "生存一档")
    session = await store.sessions.get_live_session(live_session_id=ids["session"])
    assert session is not None and session.settle_report is not None
    report = SettleReport.model_validate_json(session.settle_report)
    assert [item.reason for item in report.unregistered] == [
        "公屏这句没点这位玩家的名字，也不是紧接着他的话说的，未登记"
    ], "中间隔着认不出是谁的一句：看不出是在回 Alex"


async def test_wrong_player_or_public_chat_to_everyone_is_not_registered(
    store: SQLiteDatabase, prompt_manager: PromptManager
) -> None:
    ids = await _scene(store)
    llm = _reply(
        '{"archive": []}',
        '{"commitments": ['
        '{"line": 1, "quote": "明天上线就带你去挖矿", "what": "带 Bob 去挖矿", "to": "player", "player": "玩家2"},'
        '{"line": 1, "quote": "明天上线就带你去挖矿", "what": "带人挖矿", "to": "player", "player": "玩家9"},'
        '{"chat": "公屏#1", "quote": "周末我帮你把房子修好", "what": "修房子", "to": "everyone"}]}',
    )
    assert await _settler(store, llm, prompt_manager).settle_session(ids["session"]) is True

    assert await store.commitments.list_commitments() == []
    session = await store.sessions.get_live_session(live_session_id=ids["session"])
    assert session is not None and session.settle_report is not None
    reasons = [item.reason for item in SettleReport.model_validate_json(session.settle_report).unregistered]
    assert reasons == [
        "说的是对某位玩家答应，但这一行不是在回应这位玩家，未登记",
        "说的是对某位玩家答应，但玩家代号认不出（玩家9），未登记",
        "公屏上打的字只登记对某位玩家的承诺，未登记",
    ]


def test_game_players_eval_dry_self_check_passes() -> None:
    """ "游戏里的人"评测器（scripts/memory_eval/game_players.py）回放生产整理器：dry 用理想输出自检样本，
    并把她的每一句话和每一位玩家两两配对喂给生产校验器，代码放行的必须恰好是样本事实成立的那些
    （台词回应的是他、公屏点了他的名字或紧接着他的话）。核对规则或取料改了而评测没跟上，这里先红。"""
    script = Path(__file__).resolve().parents[3] / "scripts" / "memory_eval" / "game_players.py"
    completed = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=300, check=False
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]
