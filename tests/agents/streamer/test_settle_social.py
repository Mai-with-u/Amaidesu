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
from src.agents.streamer.memory.settle_proof import GameRecord
from src.agents.streamer.memory.settle_social import (
    MAX_FACTS_PER_KIND,
    MAX_SETTLE_FACTS_PER_KIND,
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
    """一场：玩了 minecraft，Alex 在游戏公屏说了两句，她播出一句台词；已有一条现行的进行中的事。"""
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
                "chat": "游戏公屏#1",
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
                "chat": "游戏公屏#2",
                "quote": "一起去下界",
            },
            {"player": "玩家1", "kind": "helped", "what": "帮忙", "chat": "游戏公屏#2", "quote": "没说过的话"},
        ],
    }
    llm = _answers(social)
    assert await _settler(store, llm, prompt_manager).settle_session(env["session"]) is True

    # 第四调的原料是紧凑集：玩家游戏公屏与现有事实在，她的台词不在
    social_call = llm.calls[-1]
    assert "刷铁机修好了" in str(social_call["input"]) and f"事实#{env['old']}" in str(social_call["input"])
    assert "好呀" not in str(social_call["input"])
    # 游戏公屏按时间序双侧行编排：她的游戏公屏行标"她"插在对话流里（回答才看得出是在回答什么），
    # 但没有编号——证据编号只发给玩家的行
    social_input = str(social_call["input"])
    her_row = social_input.index("她：那我等你钻石回来一起做个剑")
    player_row = social_input.index("游戏公屏#2")
    assert her_row < player_row and "游戏公屏#" not in social_input[her_row:player_row]

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


def _fact(
    fact_id: int,
    kind: str,
    *,
    locked: int = 0,
    updated_at_ms: int = 1,
    created_session_id: int = 1,
    updated_session_id: int = 1,
) -> SocialFact:
    return SocialFact(
        id=fact_id,
        game="minecraft",
        save_id="w1",
        kind=kind,
        text=f"事实{fact_id}",
        quote="原",
        human_locked=locked,
        created_session_id=created_session_id,
        updated_session_id=updated_session_id,
        created_at_ms=updated_at_ms,
        updated_at_ms=updated_at_ms,
    )


def _material(
    facts: List[SocialFact],
    *,
    chat_lines: List[GameChatLine] | None = None,
    player_names: Dict[int, str] | None = None,
    game_records: List[GameRecord] | None = None,
) -> SocialMaterial:
    return build_social_material(
        chat_lines=chat_lines if chat_lines is not None else [_line(1, "箱子大家随便用")],
        interactions=[],
        facts=facts,
        player_names=player_names if player_names is not None else {7: "Alex"},
        records_text="",
        places_text="【本场记住的地点】\n没有",
        games=["minecraft"],
        saves=SaveTimeline(),
        started_at_ms=0,
        clock_text=lambda ms: "00:00",
        game_records=game_records or (),
    )


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ({"kind": "gossip", "text": "x", "chat": "游戏公屏#1", "quote": "箱子"}, "六类"),
        ({"kind": "sharing", "text": "箱子公用", "chat": None, "quote": ""}, "必须引"),
        ({"kind": "sharing", "text": "箱子公用", "chat": "游戏公屏#9", "quote": "箱子"}, "不是给出的"),
        ({"kind": "sharing", "text": "箱子公用", "chat": "游戏公屏#1", "quote": "柜子"}, "一字不差"),
        ({"kind": "her_role", "text": "她管箱子", "chat": None, "quote": "箱子"}, "没引"),
        (
            {"kind": "sharing", "text": "箱子公用", "chat": "游戏公屏#1", "quote": "箱子", "supersedes": 2},
            "运营者定的",
        ),
        (
            {"kind": "sharing", "text": "箱子公用", "chat": "游戏公屏#1", "quote": "箱子", "supersedes": 3},
            "不是同一类",
        ),
        (
            {"kind": "sharing", "text": "箱子公用", "chat": "游戏公屏#1", "quote": "箱子", "supersedes": 99},
            "不是给出的现行事实",
        ),
    ],
)
def test_validate_bounces(row: Dict[str, Any], reason: str) -> None:
    material = _material([_fact(2, "sharing", locked=1), _fact(3, "works")])
    check = validate_social_output({"facts": [row]}, material, live_session_id=1)
    assert check.facts == []
    assert len(check.missed) == 1 and reason in check.missed[0].reason


def test_validate_decouples_storage_from_display_budget() -> None:
    """存储真值与展示预算解耦：不再渲染满槽强迫取代提示，超过 4 条的新增有效事实直接保留 active、绝不伪造 supersedes。"""
    unlocked_facts = [
        _fact(10 + i, "sharing", updated_at_ms=1_000 + i * 100, updated_session_id=2) for i in range(MAX_FACTS_PER_KIND)
    ]
    material = _material(unlocked_facts)
    assert "已满" not in material.text
    assert "槽位占用提示" not in material.text

    # sharing 已有 4 条仍有效的规矩时，新增第 5 条直接写 active（supersedes_id 为 None），不误杀旧规矩
    row = {"kind": "sharing", "text": "AE网络就是仓库", "chat": "游戏公屏#1", "quote": "箱子"}
    check = validate_social_output({"facts": [row]}, material, live_session_id=5)
    assert check.missed == []
    assert len(check.facts) == 1
    assert (
        check.facts[0].game,
        check.facts[0].save_id,
        check.facts[0].evidence_game_chat_id,
        check.facts[0].supersedes_id,
    ) == ("minecraft", "w1", 1, None)

    # 现实中确实变更时，显式指定 supersedes 严格生效
    explicit = validate_social_output({"facts": [{**row, "supersedes": 12}]}, material, live_session_id=5)
    (fact,) = explicit.facts
    assert (fact.game, fact.save_id, fact.evidence_game_chat_id, fact.supersedes_id) == ("minecraft", "w1", 1, 12)

    # 全部 4 条均为运营者锁定时，新增独立有效的第 5 条仍可直接登记（不触碰任何锁定条目）
    locked_material = _material([_fact(20 + i, "sharing", locked=1) for i in range(MAX_FACTS_PER_KIND)])
    locked_check = validate_social_output({"facts": [row]}, locked_material, live_session_id=5)
    assert locked_check.missed == []
    assert len(locked_check.facts) == 1 and locked_check.facts[0].supersedes_id is None

    # members 已有 4 条时新增第 5 条同样直接登记，不再报满额拒收
    members_material = _material([_fact(30 + i, "members") for i in range(MAX_FACTS_PER_KIND)])
    members_check = validate_social_output(
        {"facts": [{"kind": "members", "text": "新成员关系", "chat": "游戏公屏#1", "quote": "箱子"}]},
        members_material,
        live_session_id=5,
    )
    assert members_check.missed == []
    assert len(members_check.facts) == 1 and members_check.facts[0].supersedes_id is None


@pytest.mark.parametrize(
    ("player_field", "chat_ref", "quote", "expected_pid"),
    [
        ("玩家2", "游戏公屏#2", "可以用/back回到上一个低点", 9),
        ("PlayerB", "游戏公屏#2", "可以用/back回到上一个低点", 9),
        ("  playerb  ", "游戏公屏#2", "可以用/back回到上一个低点", 9),
        ("PlayerA", "游戏公屏#1", "AE网络就是我们的仓库", 7),
        ("PlayerB", "游戏公屏#1", "AE网络就是我们的仓库", None),  # 真名与游戏公屏说话人不符 → 拒收
        ("UnknownPlayer", "游戏公屏#2", "可以用/back回到上一个低点", None),  # 未知真名 → 拒收
    ],
)
def test_interactions_accept_player_real_name_and_reject_mismatch(
    player_field: str, chat_ref: str, quote: str, expected_pid: int | None
) -> None:
    """互动 player 既支持“玩家N”代号也兼容真名反查（忽略前后空格与大小写），但必须严格对上游戏公屏说话人。"""
    material = _material(
        [],
        chat_lines=[
            _line(1, "AE网络就是我们的仓库", player_id=7),
            _line(2, "以防你不知道可以用/back回到上一个低点", player_id=9),
        ],
        player_names={7: "PlayerA", 9: "PlayerB"},
    )
    check = validate_social_output(
        {
            "facts": [],
            "interactions": [
                {"player": player_field, "kind": "helped", "what": "告知实用机制", "chat": chat_ref, "quote": quote}
            ],
        },
        material,
        live_session_id=75,
    )
    if expected_pid is None:
        assert check.interactions == []
        assert len(check.missed) == 1 and "那位玩家自己" in check.missed[0].reason
    else:
        assert check.missed == []
        assert len(check.interactions) == 1
        assert check.interactions[0].player_id == expected_pid


@pytest.mark.parametrize("kind", ["sharing", "setting", "open_question"])
def test_new_facts_never_auto_evict_existing_active_rules(kind: str) -> None:
    """sharing / setting / open_question 已有 4 条以上时，新事实绝不自动处决既有未锁定条目。"""
    facts = [
        _fact(1, kind, locked=1, updated_at_ms=500, updated_session_id=1),
        _fact(2, kind, locked=0, updated_at_ms=2_000, updated_session_id=3),
        _fact(3, kind, locked=0, updated_at_ms=1_500, updated_session_id=2),
        _fact(4, kind, locked=0, updated_at_ms=1_000, updated_session_id=75),
    ]
    material = _material(
        facts,
        chat_lines=[
            _line(1, "AE网络就是我们的仓库，可以用/back回到上一个低点", player_id=7),
            _line(2, "弄太近容易弄坏岩浆传递机器吧", player_id=9),
        ],
        player_names={7: "PlayerA", 9: "PlayerB"},
    )
    check = validate_social_output(
        {
            "facts": [
                {"kind": kind, "text": "高价值新事实一", "chat": "游戏公屏#1", "quote": "AE网络就是我们的仓库"},
                {
                    "kind": kind,
                    "text": "高价值新事实二",
                    "chat": "游戏公屏#2",
                    "quote": "弄太近容易弄坏岩浆传递机器吧",
                },
            ]
        },
        material,
        live_session_id=75,
    )
    assert check.missed == []
    # 两条新事实均以独立 active 写入，绝不自动把既有有效条目伪造成 superseded
    assert [fact.supersedes_id for fact in check.facts] == [None, None]


def test_build_social_material_slices_prompt_top_k_with_locked_and_newest_priority() -> None:
    """数据库现行事实很多时，结算原料输入在读取侧按锁定优先、更新时间倒序做有界 Top-K 切片，不膨胀提示词。"""
    locked_old = _fact(1, "sharing", locked=1, updated_at_ms=100, updated_session_id=1)
    many_unlocked = [
        _fact(10 + idx, "sharing", locked=0, updated_at_ms=1_000 + idx * 10, updated_session_id=2)
        for idx in range(MAX_SETTLE_FACTS_PER_KIND + 6)
    ]
    # 再配齐其他类别满额，触发单类公平配额截断（确保不会因总预算余量回填而放进全部 sharing）
    other_kinds = [
        _fact(100 + k_idx * 20 + idx, kind, locked=0, updated_at_ms=5_000 + idx)
        for k_idx, kind in enumerate(("setting", "members", "works", "her_role", "open_question"))
        for idx in range(MAX_SETTLE_FACTS_PER_KIND)
    ]
    material = _material([locked_old, *many_unlocked, *other_kinds])
    sharing_shown = [fact for fact in material.facts.values() if fact.kind == "sharing"]
    assert len(sharing_shown) == MAX_SETTLE_FACTS_PER_KIND
    assert locked_old.id in material.facts, "人工锁定事实必须优先保留在结算原料内"
    newest_id = many_unlocked[-1].id
    oldest_unlocked_id = many_unlocked[0].id
    assert newest_id in material.facts
    assert oldest_unlocked_id not in material.facts


async def test_e2e_settle_preserves_all_valid_sharing_rules_beyond_display_budget(store, prompt_manager) -> None:
    """端到端真库回归：已有 4 条有效共享规矩时，本场新学到第 5 条规矩后库里 5 条全部保持 active，零误杀。"""
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session, ended_at_ms=1_801_000)
    await store.events.insert_game_event(
        live_session_id=session, game="minecraft", event_type="milestone", message="进了世界", save_id="w1"
    )
    await store.chat.insert_live_chat(
        live_session_id=session, timestamp_ms=1_500, sender_role="assistant", content="收到啦", message_type="speech"
    )
    player = await store.game_players.meet_player(
        game="minecraft", uuid="u-rule", name="PlayerA", live_session_id=session, at_ms=1_100
    )
    await store.game_players.add_game_chat(
        live_session_id=session,
        game="minecraft",
        direction="player",
        content="二楼公共箱子里的木头随便拿",
        player_id=player.id,
        save_id="w1",
        save_name="麦窝",
        at_ms=1_200,
        message_id="g-rule",
    )
    existing_ids = []
    for idx in range(MAX_FACTS_PER_KIND):
        fid = await store.social_facts.insert_fact(
            kind="sharing",
            text=f"既有有效共享规矩{idx}",
            quote=f"既有原话{idx}",
            game="minecraft",
            save_id="w1",
            created_session_id=session,
            now_ms_value=1_000 + idx,
        )
        assert fid is not None
        existing_ids.append(fid)

    social = {
        "facts": [
            {
                "kind": "sharing",
                "text": "二楼公共箱子里的木头可以随便拿",
                "chat": "游戏公屏#1",
                "quote": "二楼公共箱子里的木头随便拿",
                "supersedes": None,
            }
        ],
        "interactions": [],
    }
    llm = _answers(social)
    assert await _settler(store, llm, prompt_manager).settle_session(session) is True
    active = await store.social_facts.list_active_facts(game="minecraft", save_id="w1")
    assert len(active) == MAX_FACTS_PER_KIND + 1
    for fid in existing_ids:
        row = await store.social_facts.get_fact(fact_id=fid)
        assert row is not None and row.status == "active"


def test_works_supersede_accepts_verified_game_record_and_rejects_invalid_uses() -> None:
    """works 取代既有活跃事实时允许引用经核实游戏记录结案；新建、非 works、未做成或错引文均拒收。"""
    old_works = _fact(50, "works", updated_at_ms=1_000, updated_session_id=73)
    locked_works = _fact(51, "works", locked=1, updated_at_ms=1_000, updated_session_id=73)
    old_sharing = _fact(52, "sharing", updated_at_ms=1_000, updated_session_id=73)
    records = [
        GameRecord(
            number=14,
            game="minecraft",
            at_ms=2_000,
            kind="里程碑",
            text="第 1 步（maicraft:travel，回广场橙子摊旁搭烧烤摊），出行，走到了目的地",
            proves=True,
        ),
        GameRecord(
            number=15,
            game="minecraft",
            at_ms=2_100,
            kind="委派",
            text="委派「铺广场」停了没交付，不能作证",
            proves=False,
        ),
        GameRecord(
            number=16,
            game="other_game",
            at_ms=2_200,
            kind="里程碑",
            text="别的游戏完成了建造",
            proves=True,
        ),
    ]
    material = _material([old_works, locked_works, old_sharing], game_records=records)

    # 正例 1：通过 chat="记录#14" 结案旧 works #50
    ok_chat = validate_social_output(
        {
            "facts": [
                {
                    "kind": "works",
                    "text": "广场石砖铺完了，橙子摊旁新搭了烧烤摊",
                    "chat": "记录#14",
                    "quote": "回广场橙子摊旁搭烧烤摊",
                    "supersedes": 50,
                }
            ]
        },
        material,
        live_session_id=75,
    )
    assert ok_chat.missed == []
    assert len(ok_chat.facts) == 1
    assert ok_chat.facts[0].supersedes_id == 50
    assert ok_chat.facts[0].evidence_game_chat_id is None
    assert ok_chat.facts[0].quote == "回广场橙子摊旁搭烧烤摊"
    assert (ok_chat.facts[0].game, ok_chat.facts[0].save_id) == ("minecraft", "w1")

    # 正例 2：通过显式 record 字段也可核验
    ok_record = validate_social_output(
        {
            "facts": [
                {
                    "kind": "works",
                    "text": "广场石砖铺完了，橙子摊旁新搭了烧烤摊",
                    "chat": None,
                    "record": "记录#14",
                    "quote": "回广场橙子摊旁搭烧烤摊",
                    "supersedes": 50,
                }
            ]
        },
        material,
        live_session_id=75,
    )
    assert ok_record.missed == [] and len(ok_record.facts) == 1 and ok_record.facts[0].supersedes_id == 50

    # 反例表：新建 works、非 works、未做成记录、跨游戏、错引文、未知记录、锁定目标均拒收
    bad_cases = [
        (
            {
                "kind": "works",
                "text": "新工程",
                "chat": "记录#14",
                "quote": "回广场橙子摊旁搭烧烤摊",
                "supersedes": None,
            },
            "只有取代既有事实",
        ),
        (
            {"kind": "sharing", "text": "共享", "chat": "记录#14", "quote": "回广场橙子摊旁搭烧烤摊", "supersedes": 52},
            "不是给出的玩家游戏公屏",
        ),
        (
            {"kind": "works", "text": "未交付", "chat": "记录#15", "quote": "铺广场", "supersedes": 50},
            "未做成，不能作证",
        ),
        (
            {"kind": "works", "text": "跨游戏", "chat": "记录#16", "quote": "别的游戏完成了建造", "supersedes": 50},
            "不是同一个游戏",
        ),
        (
            {"kind": "works", "text": "假引文", "chat": "记录#14", "quote": "记录里没有这句话", "supersedes": 50},
            "一字不差的片段",
        ),
        (
            {"kind": "works", "text": "不存在", "chat": "记录#99", "quote": "回广场", "supersedes": 50},
            "不是给出的游戏记录",
        ),
        (
            {"kind": "works", "text": "改锁定", "chat": "记录#14", "quote": "回广场橙子摊旁搭烧烤摊", "supersedes": 51},
            "运营者定的",
        ),
    ]
    for bad_row, expected_reason in bad_cases:
        res = validate_social_output({"facts": [bad_row]}, material, live_session_id=75)
        assert res.facts == [], f"不应放行: {bad_row}"
        assert len(res.missed) == 1 and expected_reason in res.missed[0].reason


async def test_e2e_settle_closes_works_via_game_record_and_accepts_real_name_interaction(store, prompt_manager) -> None:
    """端到端真库回归：无玩家游戏公屏作证时凭游戏记录结案旧 works，并接受真名互动与自动腾退满额 sharing。"""
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await store.sessions.close_live_session(live_session_id=session, ended_at_ms=1_801_000)
    await store.events.insert_game_event(
        live_session_id=session,
        game="minecraft",
        event_type="milestone",
        message="第 1 步（maicraft:maid_configure），同伴甲，改成了 1 项",
        timestamp_ms=1_200,
    )
    await store.chat.insert_live_chat(
        live_session_id=session,
        timestamp_ms=1_500,
        sender_role="assistant",
        content="同伴甲复活啦",
        message_type="speech",
    )
    player = await store.game_players.meet_player(
        game="minecraft", uuid="u-player-b", name="PlayerB", live_session_id=session, at_ms=1_100
    )
    await store.game_players.add_game_chat(
        live_session_id=session,
        game="minecraft",
        direction="player",
        content="以防你不知道可以用/back回到上一个低点",
        player_id=player.id,
        at_ms=1_300,
        message_id="g-back",
    )
    old_works = await store.social_facts.insert_fact(
        kind="works", text="同伴甲倒下了要去收遗物", quote="同伴甲倒下了", game="minecraft", created_session_id=session
    )
    assert old_works is not None

    social = {
        "facts": [
            {
                "kind": "works",
                "text": "同伴甲复活回来了，重新配置了它",
                "chat": "记录#1",
                "quote": "同伴甲，改成了 1 项",
                "supersedes": old_works,
            }
        ],
        "interactions": [
            {
                "player": "PlayerB",
                "kind": "helped",
                "what": "提醒可以用 /back 回死亡点",
                "chat": "游戏公屏#1",
                "quote": "以防你不知道可以用/back回到上一个低点",
            }
        ],
    }
    llm = _answers(social)
    assert await _settler(store, llm, prompt_manager).settle_session(session) is True

    old_row = await store.social_facts.get_fact(fact_id=old_works)
    assert old_row is not None and old_row.status == "superseded"
    active = await store.social_facts.list_active_facts(game="minecraft")
    assert [fact.text for fact in active] == ["同伴甲复活回来了，重新配置了它"]
    interactions = await store.game_players.list_session_interactions(live_session_id=session)
    assert [(item.player_id, item.kind, item.fact.get("what")) for item in interactions] == [
        (player.id, "helped", "提醒可以用 /back 回死亡点")
    ]


def test_restore_names_whole_token_and_stale_works_tagged_without_auto_supersede_and_multisave_scope() -> None:
    """10 人以上时'玩家1'不吃掉'玩家10'前缀；隔两场未再证实的旧 works 标注待核实但不自动顶替；跨多存档归纳行不误绑单存档。"""
    player_names = {idx: f"玩家名{idx:02d}" for idx in range(1, 13)}
    chat_lines = [
        GameChatLine(
            id=idx,
            live_session_id=5,
            game="minecraft",
            direction="player",
            player_id=idx,
            content=f"我是第{idx}号来集合修铁路",
            save_id="w1" if idx <= 6 else "w2",
            save_name="一档" if idx <= 6 else "二档",
            at_ms=5_000 + idx,
        )
        for idx in range(1, 13)
    ]
    stale_works = SocialFact(
        id=100,
        game="minecraft",
        save_id="w1",
        kind="works",
        text="旧工程",
        quote="旧",
        created_session_id=1,
        updated_session_id=1,
        created_at_ms=1_000,
        updated_at_ms=1_000,
    )
    fresh_works = [
        SocialFact(
            id=101 + i,
            game="minecraft",
            save_id="w1",
            kind="works",
            text=f"新工程{i}",
            quote="新",
            created_session_id=4,
            updated_session_id=4,
            created_at_ms=4_000 + i,
            updated_at_ms=4_000 + i,
        )
        for i in range(3)
    ]
    material = build_social_material(
        chat_lines=chat_lines,
        interactions=[],
        facts=[stale_works, *fresh_works],
        player_names=player_names,
        records_text="",
        places_text="【本场记住的地点】\n没有",
        games=["minecraft"],
        saves=SaveTimeline(),
        started_at_ms=5_000,
        clock_text=lambda ms: "00:00",
        current_session_id=5,
        recent_two_session_ids=(4, 3),
        second_recent_started_ms=3_000,
    )
    assert material.restore_names("玩家1 带 玩家10 和 玩家12 修路") == "玩家名01 带 玩家名10 和 玩家名12 修路"
    assert material.default_scope == ("minecraft", "", ""), "本场跨两个存档发言时，无引文归纳行不误绑最后一句的存档"
    assert "已隔多场未再证实，优先核实或取代" in material.text

    check = validate_social_output(
        {
            "facts": [
                {
                    "kind": "works",
                    "text": "玩家1 和 玩家10 修铁路",
                    "chat": "游戏公屏#1",
                    "quote": "来集合修铁路",
                    "supersedes": None,
                },
                {
                    "kind": "her_role",
                    "text": "她给 玩家10 打下手",
                    "chat": None,
                    "quote": "",
                    "supersedes": None,
                },
            ]
        },
        material,
        live_session_id=5,
    )
    assert check.missed == []
    assert len(check.facts) == 2
    assert check.facts[0].text == "玩家名01 和 玩家名10 修铁路"
    assert check.facts[0].supersedes_id is None
    assert check.facts[1].text == "她给 玩家名10 打下手"
    assert check.facts[1].save_id == ""
