"""streamer_social_fact 工具：按消息号查回依据行、各条打回分支、回执字段与条件注册。

真库（临时 SQLite）：弹幕与公屏行按真实写入路径落库，工具经 Provider.invoke 调用。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from src.agents.streamer.planner_render import render_social_facts
from src.agents.streamer.tools.social_fact import (
    MAX_FACTS_PER_KIND,
    SocialFactToolProvider,
    build_social_fact_tool_spec,
)
from src.modules.storage.models.social_facts import SocialFact
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


async def test_viewer_danmaku_routes_to_candidate_channel_instead_of_active_facts(store) -> None:
    """观众弹幕作证不再直写跨场社交事实表：未当场回应时打回，已当场回应时落入待审候选（批准前不可见）。"""
    env = await _setup(store)
    before_reply = await _call(
        env["provider"], kind="members", text="观众乙和玩家甲合住基地", quote="和阿杰合住", evidence_message_id="dm1"
    )
    assert not before_reply.success
    assert "待审候选" in (before_reply.error_message or "")
    assert await store.social_facts.list_active_facts(game="minecraft") == []

    # 主播当场对 dm1 送达回应后，再登记转入 viewer_candidates 待审区，仍不直写 game_social_facts
    await store.chat.insert_live_chat(
        live_session_id=env["session"],
        timestamp_ms=1250,
        sender_role="assistant",
        content="好呀记下你们合住基地啦",
        message_type="speech",
        reply_to_message_id="dm1",
    )
    after_reply = await _call(
        env["provider"], kind="members", text="合住一个基地", quote="和阿杰合住", evidence_message_id="dm1"
    )
    assert after_reply.success, after_reply.error_message
    assert after_reply.structured_content["queued_candidate"] is True
    assert await store.social_facts.list_active_facts(game="minecraft") == []
    pending = await store.viewer_candidates.list_candidates(status="pending", live_session_id=env["session"])
    assert len(pending) == 1
    assert pending[0].id == after_reply.structured_content["candidate_id"]
    assert pending[0].title == "合住一个基地"


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
        "kind": "sharing",
        "text": "箱子是公用的",
        "quote": "箱子大家随便用",
        "evidence_message_id": "gc1",
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
    result = await _call(provider, kind="sharing", text="箱子公用", quote="箱子大家随便用", evidence_message_id="gc1")
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
    base: Dict[str, Any] = {"text": "箱子公用", "quote": "箱子大家随便用", "evidence_message_id": "gc1"}
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
        env["provider"], kind="members", text="合住一个基地", quote="箱子大家随便用", evidence_message_id="gc1"
    )
    assert not result.success
    assert "supersedes_id" in (result.error_message or "") and f"行号 {ids[0]}" in (result.error_message or "")
    result = await _call(
        env["provider"],
        kind="works",
        text="在修房子",
        quote="箱子大家随便用",
        evidence_message_id="gc1",
    )
    assert result.success, result.error_message


def test_registration_follows_repo_injection() -> None:
    """仓储注入才声明并注册社交事实工具；没注入时工具声明里没有它。"""
    from tests.agents.streamer.helpers import make_streamer_agent

    without = make_streamer_agent()
    assert _FULL not in {spec.full_name for spec in without.list_tools()}
    with_repo = make_streamer_agent(social_fact_repo=object())
    assert _FULL in {spec.full_name for spec in with_repo.list_tools()}


def _fact(
    fact_id: int,
    kind: str,
    *,
    locked: int = 0,
    updated: int = 0,
    status: str = "active",
    quote: str = "有原话依据",
    session_id: Optional[int] = None,
    evidence_chat_id: Optional[int] = None,
) -> SocialFact:
    return SocialFact(
        id=fact_id,
        game="minecraft",
        kind=kind,
        text=f"事实{fact_id}",
        quote=quote,
        evidence_chat_id=evidence_chat_id,
        status=status,
        human_locked=locked,
        created_session_id=session_id,
        updated_session_id=session_id,
        created_at_ms=updated,
        updated_at_ms=updated,
    )


def test_render_groups_by_kind_caps_and_orders_by_priority() -> None:
    """参考段：可核对现行行按类分组、总行数至多 12；无证据归纳行与未锁弹幕行不常驻；隔两场旧 works 降级殿后。"""
    facts = [_fact(i, "members", updated=i) for i in range(1, 11)]
    facts += [_fact(20, "works", updated=1), _fact(21, "her_role", updated=1)]
    facts += [
        _fact(30, "setting", locked=1, updated=0, quote=""),
        _fact(31, "members", status="superseded", updated=99),
    ]
    # 无引文且未人工锁定的模型归纳行、以及未批准的观众弹幕来源行均不进入每轮常驻参考段
    facts += [_fact(40, "her_role", quote="", updated=99), _fact(41, "sharing", evidence_chat_id=7, updated=99)]
    text = render_social_facts(facts, 100_000, lambda ms: "刚才")
    lines = [line for line in text.splitlines()[1:] if not line.startswith("（")]
    assert text.startswith("【游戏社交事实】")
    assert len(lines) == 12
    assert lines[0].startswith("★ 事实30")
    assert "事实20" in text and "事实21" in text
    assert "事实31" not in text and "事实40" not in text and "事实41" not in text
    assert "事实1（" not in text and "事实2（" in text  # 13 行可常驻现行，最旧的一条成员关系被挤掉
    assert render_social_facts([], 0, lambda ms: "") == ""


def test_stale_works_downgrades_to_tail_after_two_unconfirmed_sessions() -> None:
    """进行中的事（works）隔过两个已完结正式场仍未再证实时，降到参考段末尾并标待确认。"""
    stale_work = _fact(1, "works", updated=1_000, session_id=10)
    fresh_member = _fact(2, "members", updated=3_500, session_id=12)
    text = render_social_facts(
        [stale_work, fresh_member],
        5_000,
        lambda ms: "早先",
        current_session_id=13,
        recent_two_session_ids=(12, 11),
        second_recent_started_ms=2_000,
    )
    assert text.index("事实2") < text.index("事实1")
    assert "（待确认的旧进展）" in text
    assert "事实1（早先记的，已隔多场未再证实）" in text


async def test_downgraded_works_yields_slot_when_kind_cap_is_full(store) -> None:
    """进行中的事满 4 条时，已隔两场未再证实的旧 works 自动让位被新进展取代，不再拒收新进展。"""
    s1 = await store.sessions.insert_live_session(started_at_ms=1_000)
    s2 = await store.sessions.insert_live_session(started_at_ms=2_000)
    s3 = await store.sessions.insert_live_session(started_at_ms=3_000)
    await store.sessions.close_live_session(live_session_id=s1, ended_at_ms=1_500)
    await store.sessions.close_live_session(live_session_id=s2, ended_at_ms=2_500)
    await store.sessions.close_live_session(live_session_id=s3, ended_at_ms=3_500)
    s4 = await store.sessions.insert_live_session(started_at_ms=4_000)

    stale_id = await store.social_facts.insert_fact(
        kind="works",
        text="玩家甲在搭旧风车",
        quote="搭旧风车",
        game="minecraft",
        save_id="w1",
        created_session_id=s1,
        updated_session_id=s1,
        now_ms_value=1_200,
    )
    for idx in range(3):
        await store.social_facts.insert_fact(
            kind="works",
            text=f"近期工程{idx}",
            quote="近期",
            game="minecraft",
            save_id="w1",
            created_session_id=s3,
            updated_session_id=s3,
            now_ms_value=3_200 + idx,
        )

    player = await store.game_players.meet_player(
        game="minecraft", uuid="u-jia", name="玩家甲", live_session_id=s4, at_ms=4_100
    )
    await store.game_players.add_game_chat(
        live_session_id=s4,
        game="minecraft",
        direction="player",
        content="我现在开始修铁轨了",
        player_id=player.id,
        save_id="w1",
        save_name="麦窝",
        at_ms=4_200,
        message_id="gc-rail",
    )

    async def _recent_two(_sid: int):
        return [(s3, 3_000), (s2, 2_000)]

    provider = SocialFactToolProvider(
        repo=store.social_facts,
        session_provider=lambda: s4,
        game_scope_provider=lambda: ("minecraft", "w1", "麦窝"),
        recent_sessions_provider=_recent_two,
    )
    res = await _call(
        provider, kind="works", text="玩家甲开始修铁轨", quote="开始修铁轨", evidence_message_id="gc-rail"
    )
    assert res.success, res.error_message
    assert res.structured_content["superseded"] == stale_id


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
