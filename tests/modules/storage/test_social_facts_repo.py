"""SocialFactRepo 行为：登记、取代链、人工锁拒改、按环境过滤、按类计数。"""

from __future__ import annotations

from src.modules.storage.models.social_facts import NewSocialFact


async def test_insert_fact_keeps_active_row(store) -> None:
    fact_id = await store.social_facts.insert_fact(
        kind="members",
        text="Alex 在这个存档里帮忙修房子",
        quote="我帮忙修房子",
        game="minecraft",
        save_id="world-1",
        save_name="麦窝",
        created_session_id=1,
    )
    assert fact_id is not None
    fact = await store.social_facts.get_fact(fact_id=fact_id)
    assert fact is not None
    assert fact.kind == "members"
    assert fact.text == "Alex 在这个存档里帮忙修房子"
    assert fact.quote == "我帮忙修房子"
    assert fact.save_id == "world-1"
    assert fact.save_name == "麦窝"
    assert fact.status == "active"
    assert fact.superseded_by is None
    assert fact.human_locked == 0


async def test_non_inductive_kind_requires_quote(store) -> None:
    """非归纳类没有原文依据就不登记（凭空引证进不来）。"""
    assert await store.social_facts.insert_fact(kind="members", text="有人在帮忙", game="minecraft") is None
    assert await store.social_facts.insert_fact(kind="works", text="在刷铁机", game="minecraft") is None


async def test_inductive_kinds_may_have_empty_quote(store) -> None:
    """归纳类两行可以没有原文依据（只有下播整理能产出这种行）。"""
    her_role = await store.social_facts.insert_fact(kind="her_role", text="她是这局的工程", game="minecraft")
    question = await store.social_facts.insert_fact(kind="open_question", text="他们是不是在合伙", game="minecraft")
    assert her_role is not None
    assert question is not None


async def test_unknown_kind_is_rejected(store) -> None:
    assert await store.social_facts.insert_fact(kind="gossip", text="他说他很有钱", quote="他说他很有钱") is None


async def test_supersede_marks_old_row_and_links_new_row(store) -> None:
    old_id = await store.social_facts.insert_fact(kind="works", text="在刷铁机", quote="我去刷铁机", game="minecraft")
    assert old_id is not None
    new_id = await store.social_facts.insert_fact(
        kind="works", text="改成了挖钻石", quote="我去挖钻石", game="minecraft", supersedes_id=old_id
    )
    assert new_id is not None
    old = await store.social_facts.get_fact(fact_id=old_id)
    assert old is not None
    assert old.status == "superseded"
    assert old.superseded_by == new_id
    new = await store.social_facts.get_fact(fact_id=new_id)
    assert new is not None
    assert new.status == "active"
    # 被取代的行不再出现在现行事实里
    active = await store.social_facts.list_active_facts(game="minecraft")
    assert [fact.id for fact in active] == [new_id]


async def test_supersede_rejects_locked_row_and_other_kind(store) -> None:
    locked_id = await store.social_facts.insert_fact(
        kind="works", text="在刷铁机", quote="我去刷铁机", game="minecraft", human_locked=1
    )
    assert locked_id is not None
    # 人工锁的行不得被取代
    assert (
        await store.social_facts.insert_fact(
            kind="works", text="改成了挖钻石", quote="我去挖钻石", game="minecraft", supersedes_id=locked_id
        )
        is None
    )
    other_kind_id = await store.social_facts.insert_fact(
        kind="members", text="Alex 在帮忙", quote="我来帮忙", game="minecraft"
    )
    assert other_kind_id is not None
    # 取代只在同类之间发生
    assert (
        await store.social_facts.insert_fact(
            kind="works", text="在刷铁机", quote="我去刷铁机", game="minecraft", supersedes_id=other_kind_id
        )
        is None
    )


async def test_supersede_fact_refuses_locked_row(store) -> None:
    locked_id = await store.social_facts.insert_fact(
        kind="works", text="在刷铁机", quote="我去刷铁机", game="minecraft", human_locked=1
    )
    assert locked_id is not None
    assert await store.social_facts.supersede_fact(fact_id=locked_id, superseded_by=999) is False
    fact = await store.social_facts.get_fact(fact_id=locked_id)
    assert fact is not None
    assert fact.status == "active"


async def test_list_filters_by_game_and_save(store) -> None:
    scoped = await store.social_facts.insert_fact(
        kind="members", text="这个存档的事", quote="这个存档", game="minecraft", save_id="world-1"
    )
    other_save = await store.social_facts.insert_fact(
        kind="members", text="另一个存档的事", quote="另一个存档", game="minecraft", save_id="world-2"
    )
    whole_game = await store.social_facts.insert_fact(
        kind="setting", text="整游戏通用的环境形态", quote="整游戏", game="minecraft", save_id=""
    )
    other_game = await store.social_facts.insert_fact(
        kind="setting", text="别的游戏的环境形态", quote="别的游戏", game="stardew", save_id="world-1"
    )
    assert None not in (scoped, other_save, whole_game, other_game)

    rows = await store.social_facts.list_active_facts(game="minecraft", save_id="world-1")
    ids = {fact.id for fact in rows}
    assert scoped in ids
    assert whole_game in ids  # 空存档号的行对每个存档都成立
    assert other_save not in ids
    assert other_game not in ids

    all_rows = await store.social_facts.list_active_facts()
    assert {fact.id for fact in all_rows} == {scoped, other_save, whole_game, other_game}


async def test_count_facts_by_kind(store) -> None:
    await store.social_facts.insert_fact(kind="works", text="在刷铁机", quote="刷铁机", game="minecraft")
    await store.social_facts.insert_fact(kind="works", text="在挖矿", quote="挖矿", game="minecraft")
    await store.social_facts.insert_fact(kind="members", text="Alex 在帮忙", quote="帮忙", game="minecraft")
    await store.social_facts.insert_fact(kind="members", text="Bo 在帮忙", quote="帮忙", game="stardew")
    counts = await store.social_facts.count_facts_by_kind(game="minecraft")
    assert counts == {"works": 2, "members": 1}


async def test_insert_facts_batch_skips_invalid_rows(store) -> None:
    written = await store.social_facts.insert_facts(
        facts=[
            NewSocialFact(kind="works", text="在刷铁机", quote="刷铁机", game="minecraft"),
            NewSocialFact(kind="members", text="没有依据的行", game="minecraft"),
        ]
    )
    assert len(written) == 1
    rows = await store.social_facts.list_active_facts(game="minecraft")
    assert [fact.text for fact in rows] == ["在刷铁机"]
