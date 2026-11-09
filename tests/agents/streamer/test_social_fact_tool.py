"""streamer_social_fact 工具：按消息号查回依据行、各条打回分支、回执字段与条件注册。

真库（临时 SQLite）：弹幕与公屏行按真实写入路径落库，工具经 Provider.invoke 调用。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from src.agents.streamer.tools.social_fact import (
    MAX_FACTS_PER_KIND,
    SocialFactToolProvider,
    build_social_fact_tool_spec,
)
from src.modules.tools import ToolInvocation

_FULL = build_social_fact_tool_spec().full_name


async def _setup(store: Any) -> Dict[str, Any]:
    session = await store.sessions.insert_live_session(started_at_ms=1000)
    other = await store.sessions.insert_live_session(started_at_ms=500)
    viewer_chat = await store.chat.insert_live_chat(
        live_session_id=session,
        timestamp_ms=1100,
        sender_role="viewer",
        content="我和阿杰合住一个基地",
        message_type="danmaku",
        message_id="dm1",
    )
    await store.chat.insert_live_chat(
        live_session_id=session,
        timestamp_ms=1200,
        sender_role="assistant",
        content="你们合住一个基地呀",
        message_type="speak",
        message_id="self1",
    )
    await store.chat.insert_live_chat(
        live_session_id=other,
        timestamp_ms=600,
        sender_role="viewer",
        content="别场的话",
        message_type="danmaku",
        message_id="dm-old",
    )
    player = await store.game_players.meet_player(
        game="minecraft", uuid="u-alex", name="Alex", live_session_id=session, at_ms=1100
    )
    game_chat = await store.game_players.add_game_chat(
        live_session_id=session,
        game="minecraft",
        direction="player",
        content="箱子大家随便用",
        player_id=player.id,
        save_id="w1",
        save_name="麦窝",
        at_ms=1150,
        message_id="gc1",
    )
    await store.game_players.add_game_chat(
        live_session_id=session, game="minecraft", direction="other", content="", at_ms=1160, message_id="gc-blank"
    )
    for mid in ("dup", "dup"):
        await store.chat.insert_live_chat(
            live_session_id=session,
            timestamp_ms=1300,
            sender_role="viewer",
            content="重复号",
            message_type="danmaku",
            message_id=mid,
        )
    provider = SocialFactToolProvider(
        repo=store.social_facts,
        session_provider=lambda: session,
        game_scope_provider=lambda: ("minecraft", "w1", "麦窝"),
    )
    return {"provider": provider, "session": session, "viewer_chat": viewer_chat, "game_chat": game_chat}


async def _call(provider: SocialFactToolProvider, **args: Any):
    return await provider.invoke(ToolInvocation(tool_name=_FULL, arguments=args))


async def test_viewer_danmaku_evidence_is_written_with_row_number(store) -> None:
    env = await _setup(store)
    result = await _call(
        env["provider"], kind="members", text="观众和阿杰合住一个基地", quote="和阿杰合住", evidence_message_id="dm1"
    )
    assert result.success, result.error_message
    fact = await store.social_facts.get_fact(fact_id=result.structured_content["id"])
    assert fact is not None
    assert (fact.evidence_chat_id, fact.evidence_game_chat_id) == (env["viewer_chat"], None)
    assert (fact.game, fact.save_id, fact.created_session_id) == ("minecraft", "w1", env["session"])
    assert result.structured_content["counts"] == {"members": 1}


async def test_game_chat_evidence_uses_that_line_and_its_save(store) -> None:
    env = await _setup(store)
    result = await _call(
        env["provider"], kind="sharing", text="箱子是大家公用的", quote="箱子大家随便用", evidence_message_id="[id:gc1]"
    )
    assert result.success, result.error_message
    fact = await store.social_facts.get_fact(fact_id=result.structured_content["id"])
    assert fact is not None
    assert (fact.evidence_chat_id, fact.evidence_game_chat_id) == (None, env["game_chat"])
    assert (fact.save_id, fact.save_name) == ("w1", "麦窝")


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"kind": "gossip"}, "六类之一"),
        ({"text": ""}, "一句事实"),
        ({"quote": ""}, "quote 必填"),
        ({"evidence_message_id": ""}, "evidence_message_id 必填"),
        ({"evidence_message_id": "nope"}, "找不到消息号"),
        ({"evidence_message_id": "self1"}, "找不到消息号"),  # 她自己说的话不能作证
        ({"evidence_message_id": "dm-old"}, "找不到消息号"),  # 别场的话不算
        ({"evidence_message_id": "dup", "quote": "重复号"}, "不止一句"),
        ({"evidence_message_id": "gc-blank"}, "没有存原话"),
        ({"quote": "合租"}, "一字不差"),
        ({"supersedes_id": 9999}, "没有行号"),
    ],
)
async def test_bounces(store, args: Dict[str, Any], expected: str) -> None:
    env = await _setup(store)
    call: Dict[str, Any] = {
        "kind": "members",
        "text": "合住一个基地",
        "quote": "和阿杰合住",
        "evidence_message_id": "dm1",
    }
    call.update(args)
    result = await _call(env["provider"], **call)
    assert not result.success
    assert expected in (result.error_message or "")


async def test_no_session_bounces(store) -> None:
    env = await _setup(store)
    provider = SocialFactToolProvider(
        repo=store.social_facts, session_provider=lambda: None, game_scope_provider=lambda: ("", "", "")
    )
    result = await _call(provider, kind="members", text="合住", quote="和阿杰合住", evidence_message_id="dm1")
    assert not result.success and "不在直播" in (result.error_message or "")
    del env


async def _seed(store: Any, kind: str, *, locked: int = 0, game: str = "minecraft", save_id: str = "w1") -> int:
    fact_id = await store.social_facts.insert_fact(
        kind=kind, text=f"旧的{kind}", quote="旧", game=game, save_id=save_id, human_locked=locked
    )
    assert fact_id is not None
    return fact_id


async def test_supersede_rules(store) -> None:
    env = await _setup(store)
    base: Dict[str, Any] = {"text": "合住一个基地", "quote": "和阿杰合住", "evidence_message_id": "dm1"}
    locked = await _seed(store, "members", locked=1)
    other_kind = await _seed(store, "works")
    stale = await _seed(store, "members")
    newer = await store.social_facts.insert_fact(
        kind="members", text="更新", quote="更", game="minecraft", save_id="w1", supersedes_id=stale
    )

    result = await _call(env["provider"], kind="members", supersedes_id=locked, **base)
    assert not result.success and "运营者" in (result.error_message or "")
    result = await _call(env["provider"], kind="members", supersedes_id=other_kind, **base)
    assert not result.success and "同一类" in (result.error_message or "")
    result = await _call(env["provider"], kind="members", supersedes_id=stale, **base)
    assert not result.success and "已经被取代" in (result.error_message or "")

    result = await _call(env["provider"], kind="members", supersedes_id=newer, **base)
    assert result.success, result.error_message
    assert result.structured_content["superseded"] == newer
    old: Optional[Any] = await store.social_facts.get_fact(fact_id=newer)
    assert old is not None and old.status == "superseded"


async def test_kind_cap_counts_current_save_and_points_at_oldest(store) -> None:
    env = await _setup(store)
    ids = [await _seed(store, "members") for _ in range(MAX_FACTS_PER_KIND)]
    # 别的存档的行不占本存档的名额
    await _seed(store, "works", save_id="w2")
    result = await _call(
        env["provider"], kind="members", text="合住一个基地", quote="和阿杰合住", evidence_message_id="dm1"
    )
    assert not result.success
    assert "supersedes_id" in (result.error_message or "") and f"行号 {ids[0]}" in (result.error_message or "")
    result = await _call(
        env["provider"],
        kind="works",
        text="在修房子",
        quote="和阿杰合住",
        evidence_message_id="dm1",
    )
    assert result.success, result.error_message


def test_registration_follows_repo_injection() -> None:
    """仓储注入才声明并注册社交事实工具；没注入时工具声明里没有它。"""
    from tests.agents.streamer.helpers import make_streamer_agent

    without = make_streamer_agent()
    assert _FULL not in {spec.full_name for spec in without.list_tools()}
    with_repo = make_streamer_agent(social_fact_repo=object())
    assert _FULL in {spec.full_name for spec in with_repo.list_tools()}


def _fact(fact_id: int, kind: str, *, locked: int = 0, updated: int = 0, status: str = "active"):
    from src.modules.storage.models.social_facts import SocialFact

    return SocialFact(
        id=fact_id,
        game="minecraft",
        kind=kind,
        text=f"事实{fact_id}",
        status=status,
        human_locked=locked,
        created_at_ms=updated,
        updated_at_ms=updated,
    )


def test_render_groups_by_kind_caps_and_orders_by_priority() -> None:
    """参考段：现行行按类分组、总行数至多 12；人工锁 > works/her_role > 最近更新；被取代的不注入。"""
    from src.agents.streamer.planner_render import render_social_facts

    facts = [_fact(i, "members", updated=i) for i in range(1, 11)]
    facts += [_fact(20, "works", updated=1), _fact(21, "her_role", updated=1)]
    facts += [_fact(30, "setting", locked=1, updated=0), _fact(31, "members", status="superseded", updated=99)]
    text = render_social_facts(facts, 100_000, lambda ms: "刚才")
    lines = [line for line in text.splitlines()[1:] if not line.startswith("（")]
    assert text.startswith("【游戏社交事实】")
    assert len(lines) == 12
    assert lines[0].startswith("★ 事实30")
    assert "事实20" in text and "事实21" in text
    assert "事实31" not in text
    assert "事实1（" not in text and "事实2（" in text  # 13 行现行，最旧的一条成员关系被挤掉
    assert render_social_facts([], 0, lambda ms: "") == ""


async def test_recall_finds_active_social_fact(store) -> None:
    """回想索引多一类：现行社交事实查得到，被取代的查不到。"""
    from src.agents.streamer.memory.index import MemoryIndexer
    from src.agents.streamer.memory.recall import MemoryRecallService
    from src.agents.streamer.memory.search import MemorySearch

    old = await store.social_facts.insert_fact(kind="works", text="阿杰在修刷铁机", quote="修刷铁机", game="minecraft")
    await store.social_facts.insert_fact(
        kind="works", text="阿杰改去挖钻石", quote="挖钻石", game="minecraft", supersedes_id=old
    )
    indexer = MemoryIndexer(repo=store.memory_index, embedding=None, embedding_note="")
    await indexer.run_once()
    service = MemoryRecallService(
        chat_repo=store.chat,
        commitments_repo=store.commitments,
        archive_repo=store.archive,
        search=MemorySearch(repo=store.memory_index, embedding=None),
        social_fact_repo=store.social_facts,
    )
    text = await service.recall("阿杰")
    assert "游戏里的人（进行中的事）：阿杰改去挖钻石" in text
    assert "刷铁机" not in text
