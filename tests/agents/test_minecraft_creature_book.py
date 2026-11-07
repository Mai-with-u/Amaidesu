"""本文件测生物名册：自己的生物自动建档、主播起名、在不在身边、死讯与墓碑认出是谁，以及按复盘时间线回放。

复盘（审计 P2-5）：主播给身边的女仆起名"阿缕"，系统没记下"阿缕是哪一只"；女仆被苦力怕炸死后，
主播把死讯当陌生人、对着她的墓碑问"是你熟人吗"，收播还记成"阿缕中途不见了"。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

from src.agents.minecraft.creature_book import CreatureBook, creature_from_record, creature_record
from src.agents.minecraft.glance import STATUS_AWAY, STATUS_DEAD, STATUS_NEARBY, glance_brief, glance_scene
from src.agents.minecraft.maicraft import MaicraftReply
from src.agents.minecraft.tools import STREAMER_GUIDE
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.game_chat import GameChatMention, GameChatPayload
from src.modules.storage.database import SQLiteDatabase
from src.modules.tools.models import ToolInvocation

from .minecraft_v1_fakes import (
    MAID_SKIN,
    FakeMaicraft,
    maid_entity,
    make_minecraft_agent,
    scene_with,
    scene_with_maid,
)


def _with_grave() -> Dict[str, Any]:
    grave = {"id": "e8", "type": "touhou_little_maid:tombstone", "direction": "后方", "distance": 3, "hostile": False}
    return scene_with(grave, graves=[{"id": "e8", "maid_name": "莫莫酒狐"}])


def test_own_creatures_are_registered_and_strangers_are_not() -> None:
    book = CreatureBook()
    stray = {"id": "e5", "type": "minecraft:wolf", "distance": 6}
    book.observe(scene_with(maid_entity(), stray, maids=[{"id": "e3", **MAID_SKIN}]), 1_000)

    [maid] = book.creatures()
    assert maid.status == STATUS_NEARBY and maid.game_name == "莫莫酒狐"
    assert maid.label() == "你的女仆（莫莫酒狐皮肤）"
    assert book.status_view() == [{"who": "你的女仆（莫莫酒狐皮肤）", "status": "在身边"}]


def test_naming_finds_the_one_creature_and_refuses_when_unsure() -> None:
    book = CreatureBook()
    book.observe(scene_with_maid(), 1_000)

    assert book.name("牛", "阿哞").named is None, "身边没有牛就不登记"
    named = book.name("你的女仆", "阿缕").named
    assert named is not None and named.label() == "阿缕（你的女仆，莫莫酒狐皮肤）"
    assert book.nicknames_by_scene_id() == {"e3": "阿缕"}
    assert book.name("阿缕", "阿缕").named is not None, "按已起的名字找得到它，同名重起不算占用"

    # 路过的羊也能起名：起了名才进名册
    assert book.name("羊", "咩咩").named is not None and len(book.creatures()) == 2
    taken = book.name("羊", "阿缕")
    assert taken.named is None and "已经是阿缕（你的女仆，莫莫酒狐皮肤）的名字" in taken.reason
    assert book.name("咩咩", "").named is not None and book.nicknames_by_scene_id() == {"e3": "阿缕"}
    assert len(book.creatures()) == 1, "路人的名字取消了就不再记它"


def test_two_matching_creatures_pick_your_own_or_ask_which_one() -> None:
    book = CreatureBook()
    theirs = maid_entity("e4", distance=5, owner="别人的")
    book.observe(scene_with(maid_entity(), theirs, maids=[{"id": "e3", **MAID_SKIN}, {"id": "e4", **MAID_SKIN}]), 1_000)
    assert book.name("女仆", "阿缕").named is not None, "一只是自己的就挑自己的"

    both_wild = scene_with(
        {"id": "e6", "type": "minecraft:cat", "direction": "左侧", "distance": 4},
        {"id": "e7", "type": "minecraft:cat", "direction": "右侧", "distance": 6},
    )
    book.observe(both_wild, 2_000)
    unsure = book.name("猫", "花花")
    assert unsure.named is None and "分不清是哪一只" in unsure.reason and "在左侧" in unsure.reason


def test_naming_someone_elses_maid_by_who_tamed_it_keeps_the_identity() -> None:
    """速览写成"玩家甲的女仆"时，主播照这个称呼起名找得到那一只；名册里驯服关系与身份不变（仍是"别人的"），
    称呼带上驯服者；驯服者不落库。"""
    book = CreatureBook()
    theirs = maid_entity("e4", distance=5, owner="别人的", tamer="玩家甲")
    book.observe(scene_with(maid_entity(), theirs, maids=[{"id": "e3", **MAID_SKIN}, {"id": "e4", **MAID_SKIN}]), 1_000)

    named = book.name("玩家甲的女仆", "小红").named
    assert named is not None and named.owner == "别人的"
    assert named.label() == "小红（玩家甲的女仆，莫莫酒狐皮肤）"
    assert creature_record(named).owner == "别人的" and not hasattr(creature_record(named), "tamer")
    restored = creature_from_record(creature_record(named))
    assert restored.label() == "小红（别人的女仆，莫莫酒狐皮肤）", "重启后要等再看到它才知道驯服者"


def test_out_of_sight_is_a_state_not_an_event() -> None:
    """离开视野只改状态：起过名的在速览里说一句"这会儿没在身边"，没起名的不提。"""
    book = CreatureBook()
    book.observe(scene_with_maid(), 1_000)
    book.observe(scene_with(), 2_000)
    assert book.creatures()[0].status == STATUS_AWAY
    assert book.status_view() == [], "没起名、没看到的自己的生物不提"

    book.observe(scene_with_maid(), 3_000)
    book.name("你的女仆", "阿缕")
    book.observe(scene_with(), 4_000)
    [item] = book.status_view()
    assert item["status"] == STATUS_AWAY and item["named"] is True
    assert "阿缕（你的女仆，莫莫酒狐皮肤）这会儿没在身边" in glance_brief({"creatures": book.status_view()})


def test_a_death_message_is_recognized_only_when_it_is_surely_hers() -> None:
    book = CreatureBook()
    book.observe(scene_with_maid(), 1_000)

    assert book.note_death("莫莫酒狐 的好感度提升了", 2_000) is None, "以她的名字开头但不是死讯"
    assert book.note_death("Steve被苦力怕炸死了", 2_000) is None, "别人的死讯不归她"
    dead = book.note_death("莫莫酒狐被苦力怕炸死了", 2_000)
    assert dead is not None and dead.status == STATUS_DEAD and dead.death_note == "被苦力怕炸死了"
    assert book.note_death("莫莫酒狐被苦力怕炸死了", 2_100) is None, "同一次死只认一次"

    twins = CreatureBook()
    twins.observe(
        scene_with(
            maid_entity(), maid_entity("e4", distance=5), maids=[{"id": "e3", **MAID_SKIN}, {"id": "e4", **MAID_SKIN}]
        ),
        1_000,
    )
    assert len(twins.creatures()) == 1, "没有 UUID 时同皮肤的两只拼出同一个身份键"


def test_her_grave_confirms_the_death_and_is_called_by_her_name() -> None:
    book = CreatureBook()
    book.observe(scene_with_maid(), 1_000)
    book.name("你的女仆", "阿缕")
    book.observe(_with_grave(), 2_000)

    [maid] = book.creatures()
    assert maid.status == STATUS_DEAD and maid.grave_nearby
    assert book.nicknames_by_scene_id() == {"e8": "阿缕"}, "墓碑按上面的名字对到死者"
    view = glance_scene(MaicraftReply(ok=True, data=_with_grave()), book.nicknames_by_scene_id())
    view["creatures"] = book.status_view()
    assert glance_brief(view) == (
        "阿缕（你的女仆，莫莫酒狐皮肤）死了，墓碑就在身边。身边还有阿缕的女仆墓碑（上面写着莫莫酒狐）。"
    )

    book.observe(scene_with_maid(), 3_000)
    assert maid.status == STATUS_NEARBY, "又看到她（认错了或被复活）就当活着"


def test_game_agent_and_streamer_are_told_about_names() -> None:
    """游戏 Agent 拿到"名字 → 是哪一只、此刻怎样"，主播的玩法说明写明起名要登记、已死就是死了。"""
    book = CreatureBook()
    book.observe(scene_with_maid(), 1_000)
    assert book.named_view() == {}
    book.name("你的女仆", "阿缕")
    assert book.named_view() == {"阿缕": "你的女仆（莫莫酒狐皮肤），在身边"}
    book.note_death("莫莫酒狐被苦力怕炸死了", 2_000)
    assert book.named_view() == {"阿缕": "你的女仆（莫莫酒狐皮肤），已死：被苦力怕炸死了"}

    assert "minecraft_name_creature" in STREAMER_GUIDE and "已死的就是死了" in STREAMER_GUIDE


async def test_replay_of_the_audit_timeline() -> None:
    """按复盘时间线回放：21:40 起名 → 22:02:53 最后一次在身边 → 22:03:00 死讯 → 22:03:13 墓碑。

    每个时刻主播的一句话现场与游戏进展事件都要说对：起名后用名字称呼，死讯当场成为一条进展，
    之后的速览写明她死了、墓碑就在身边，不再有"阿缕在身后"可供误读。
    """
    server = FakeMaicraft()
    server.scene_view = scene_with_maid()
    agent, _, bus = await make_minecraft_agent(server)

    brief = (await agent._glance())["brief"]
    assert "你的女仆（莫莫酒狐皮肤）在身边" in brief

    result = await agent._tool_registry.invoke(
        ToolInvocation(tool_name="minecraft_name_creature", arguments={"who": "你的女仆", "nickname": "阿缕"})
    )
    assert result.success and result.structured_content["named"] == "阿缕（你的女仆，莫莫酒狐皮肤）"
    assert "阿缕（你的女仆，莫莫酒狐皮肤）在身边" in (await agent._glance())["brief"]
    assert agent._current_task_context()["named_creatures"] == {"阿缕": "你的女仆（莫莫酒狐皮肤），在身边"}

    death = GameChatPayload(game="minecraft", kind="system", content="莫莫酒狐被苦力怕炸死了")
    await agent._on_game_chat(CoreEvents.GAME_CHAT_RECEIVED, death, "maicraft_chat")
    milestones = [
        call.args[1]
        for call in bus.emit.await_args_list
        if call.args[0] == CoreEvents.GAME_MILESTONE and isinstance(call.args[1], GamePayload)
    ]
    assert [payload.message for payload in milestones] == ["阿缕（你的女仆，莫莫酒狐皮肤）被苦力怕炸死了"]

    server.scene_view = deepcopy(_with_grave())
    view = await agent._glance()
    assert "阿缕（你的女仆，莫莫酒狐皮肤）被苦力怕炸死了，墓碑就在身边" in view["brief"]
    assert "阿缕的女仆墓碑（上面写着莫莫酒狐）" in view["brief"]
    assert view["creatures"][0]["status"] == STATUS_DEAD and view["creatures"][0]["note"] == "被苦力怕炸死了"

    player_chat = GameChatPayload(game="minecraft", kind="player", sender="Steve", content="莫莫酒狐被苦力怕炸死了")
    await agent._on_game_chat(CoreEvents.GAME_CHAT_RECEIVED, player_chat, "maicraft_chat")
    assert len([c for c in bus.emit.await_args_list if c.args[0] == CoreEvents.GAME_MILESTONE]) == 1, (
        "玩家打的字不当死讯，同一次死也不重复发"
    )
    await agent.stop()


def test_a_death_is_mentioned_in_its_session_and_the_next_one_only() -> None:
    """第 52 场死的：52 场和下播后、53 场里还提；54 场起只留在名册里占着名字。"""
    book = CreatureBook()
    book.load("w1", [])
    book.start_session(52)
    book.observe(scene_with_maid(), 1_000)
    book.name("你的女仆", "阿缕")
    book.note_death("莫莫酒狐被苦力怕炸死了", 2_000)
    assert book.status_view()[0]["status"] == STATUS_DEAD
    book.end_session()
    assert book.status_view(), "下播后、下一场开播前还提"
    book.start_session(53)
    assert book.status_view() and "阿缕" in book.named_view()

    book.end_session()
    book.start_session(54)
    assert book.status_view() == [] and book.named_view() == {}
    wolf = {"id": "e5", "type": "minecraft:wolf", "distance": 3, "traits": {"驯服": "你的"}}
    book.observe(scene_with(wolf), 3_000)
    taken = book.name("狼", "阿缕")
    assert taken.named is None and "它已经死了，名字留给它" in taken.reason


def test_changes_are_handed_out_once_and_a_reload_restores_the_roster() -> None:
    """只有落库的事实变了才交出去写回；按落库的行读回来，名字还在，此刻在不在身边等下一次观察。"""
    book = CreatureBook()
    book.load("w1", [])
    book.observe(scene_with_maid(), 1_000)
    changed, removed = book.take_changes()
    assert [creature.skin for creature in changed] == ["莫莫酒狐"] and removed == []
    book.observe(scene_with_maid(), 2_000)
    assert book.take_changes() == ([], []), "只是又看到一次，不用写回"

    book.name("你的女仆", "阿缕")
    book.name("羊", "咩咩")
    book.name("咩咩", "")
    changed, removed = book.take_changes()
    assert [creature.nickname for creature in changed] == ["阿缕"] and removed == ["uuid:a1"]

    again = CreatureBook()
    again.load("w1", [creature_from_record(creature_record(creature)) for creature in book.creatures()])
    [maid] = again.creatures()
    assert maid.nickname == "阿缕" and maid.status == STATUS_AWAY and maid.last_seen_ms == 2_000


async def test_names_survive_a_restart_and_stay_in_their_world(db_path: Path) -> None:
    """起过名的女仆：Agent 重建后还认得；换到别的世界，名册是那个世界的，不串。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        server = FakeMaicraft()
        server.scene_view = scene_with_maid()
        server.self_view["world_key"] = "w1"
        agent, _, _ = await make_minecraft_agent(server, creature_repo=db.game_creatures)
        await agent._glance()
        assert (await agent._name_creature("你的女仆", "阿缕"))["success"]
        await agent.stop()

        again, _, _ = await make_minecraft_agent(server, creature_repo=db.game_creatures)
        assert "阿缕（你的女仆，莫莫酒狐皮肤）在身边" in (await again._glance())["brief"]
        server.self_view["world_key"] = "w2"
        assert "阿缕" not in (await again._glance())["brief"]
        await again.stop()
        assert [r.nickname for r in await db.game_creatures.list_creatures(game="minecraft", world_key="w1")] == [
            "阿缕"
        ]
    finally:
        await db.close()


async def test_roster_follows_world_id_and_adopts_rows_under_old_key(db_path: Path) -> None:
    """新版 Mod 按 world.id 分名册：旧版本存在旧编号名下的名字搬过来，换到别的存档不串，还没认出时不换。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        server = FakeMaicraft()
        server.scene_view = scene_with_maid()
        server.self_view["world_key"] = "old-hash"
        agent, _, _ = await make_minecraft_agent(server, creature_repo=db.game_creatures)
        await agent._glance()
        assert (await agent._name_creature("你的女仆", "阿缕"))["success"]
        await agent.stop()

        # Mod 升级：同一个世界有了世界编号，旧版本的数据在 old-hash 名下
        server.enter_world("id-home", "老家")
        again, _, _ = await make_minecraft_agent(server, creature_repo=db.game_creatures)
        assert "阿缕（你的女仆，莫莫酒狐皮肤）在身边" in (await again._glance())["brief"]
        assert [r.nickname for r in await db.game_creatures.list_creatures(game="minecraft", world_key="id-home")] == [
            "阿缕"
        ]
        assert await db.game_creatures.list_creatures(game="minecraft", world_key="old-hash") == []

        # 新建的存档：world_key 与 world.id 相同，没有可搬的，名册是空的
        server.enter_world("id-new", "新的世界")
        server.self_view["world_key"] = "id-new"
        assert "阿缕" not in (await again._glance())["brief"]
        assert again._creatures.world_key == "id-new"

        # 回主菜单：认不出世界，名册不换
        server.leave_world()
        await again._glance()
        assert again._creatures.world_key == "id-new"
        await again.stop()
    finally:
        await db.close()


_M1 = "5f2b9c1e-0d4a-4c1b-9e7f-1a2b3c4d5e6f"
_M2 = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"


def _twins() -> Dict[str, Any]:
    """同皮肤的两只自己的女仆，游戏给了各自的 UUID。"""
    first = {**maid_entity("e3"), "uuid": _M1}
    second = {**maid_entity("e4", distance=5), "direction": "左侧", "uuid": _M2}
    return scene_with(first, second, maids=[{"id": "e3", **MAID_SKIN}, {"id": "e4", **MAID_SKIN}])


def test_same_skin_twins_are_told_apart_by_uuid() -> None:
    """同皮肤的两只各记一条；死讯按点名的 UUID、墓碑按上面带的 UUID 认到各自那一只，名字不串。"""
    book = CreatureBook()
    book.observe(_twins(), 1_000)
    assert len(book.creatures()) == 2
    unsure = book.name("你的女仆", "阿缕")
    assert unsure.named is None and "分不清是哪一只" in unsure.reason and "在左侧" in unsure.reason

    dead = book.note_death("莫莫酒狐被苦力怕炸死了", 2_000, victim_uuid=_M2, victim_name="莫莫酒狐")
    assert dead is not None and dead.uuid == _M2 and dead.death_note == "被苦力怕炸死了"
    assert book.note_death("莫莫酒狐被苦力怕炸死了", 2_000) is None, "没有点名时同名的两只分不清，宁可不认"
    assert book.note_death("Steve被苦力怕炸死了", 2_000, victim_uuid="0000", victim_name="Steve") is None

    grave = {"id": "e8", "type": "touhou_little_maid:tombstone", "distance": 2}
    book.observe(scene_with(grave, graves=[{"id": "e8", "maid_name": "莫莫酒狐", "maid_uuid": _M1}]), 3_000)
    alive_then_dead = {creature.uuid: creature.status for creature in book.creatures()}
    assert alive_then_dead == {_M1: STATUS_DEAD, _M2: STATUS_DEAD}


def test_an_old_record_moves_under_the_uuid_when_the_mod_starts_reporting_it() -> None:
    """旧版 Mod 时按长相记下并起了名的女仆，升级后第一次带着 UUID 出现：名字跟到 UUID 下，旧键删掉。"""
    book = CreatureBook()
    book.load("", [])
    book.observe(scene_with_maid(), 1_000)
    book.name("你的女仆", "阿缕")
    book.take_changes()
    upgraded = scene_with_maid()
    upgraded["entities"][0]["uuid"] = _M1
    book.observe(upgraded, 2_000)
    [maid] = book.creatures()
    assert (maid.key, maid.nickname) == (f"uuid:{_M1}", "阿缕")
    changed, removed = book.take_changes()
    assert [creature.key for creature in changed] == [f"uuid:{_M1}"] and removed == [
        "look:touhou_little_maid:maid|touhou_little_maid:winefox_momo|"
    ]


def test_a_creature_the_game_cannot_tell_apart_is_not_named() -> None:
    """没被驯服、没挂命名牌的野生动物游戏不给 UUID，同种的每一只都一样：不登记，说清怎么办。"""
    book = CreatureBook()
    book.observe(scene_with({"id": "e2", "type": "minecraft:cow", "direction": "前方", "distance": 4}), 1_000)
    refused = book.name("牛", "哞哞")
    assert refused.named is None and "挂个命名牌或驯服它" in refused.reason and book.creatures() == []


async def test_death_broadcast_is_attributed_by_the_victim_uuid() -> None:
    """死亡播报带模板编号与点名：按开头写出名字的死者的 UUID 认；不是死亡模板的不认，点名的是别人也不认。"""
    server = FakeMaicraft()
    server.scene_view = _twins()
    server.self_view["name"] = "Alex"
    agent, _, bus = await make_minecraft_agent(server)
    view = await agent._glance()
    assert view["self_account"] == "Alex" and "Alex" not in view["brief"]

    def milestones() -> list[str]:
        return [
            call.args[1].message
            for call in bus.emit.await_args_list
            if call.args[0] == CoreEvents.GAME_MILESTONE and isinstance(call.args[1], GamePayload)
        ]

    maid, creeper = (
        GameChatMention(name="莫莫酒狐", type="touhou_little_maid:maid", id=_M2),
        GameChatMention(name="苦力怕", type="minecraft:creeper", id="0f0e0d0c-0b0a-4908-8706-050403020100"),
    )
    not_death = GameChatPayload(
        game="minecraft", kind="system", content="莫莫酒狐被苦力怕炸死了", message_key="chat.type.text", mentions=[maid]
    )
    await agent._on_game_chat(CoreEvents.GAME_CHAT_RECEIVED, not_death, "maicraft_chat")
    killer_only = GameChatPayload(
        game="minecraft",
        kind="system",
        content="Steve被苦力怕炸死了",
        message_key="death.attack.explosion.player",
        mentions=[GameChatMention(name="Steve", id="8667ba71-b85a-4004-af54-457a9734eed7"), creeper],
    )
    await agent._on_game_chat(CoreEvents.GAME_CHAT_RECEIVED, killer_only, "maicraft_chat")
    assert milestones() == []

    death = GameChatPayload(
        game="minecraft",
        kind="system",
        content="莫莫酒狐被苦力怕炸死了",
        message_key="death.attack.explosion.player",
        mentions=[maid, creeper],
    )
    await agent._on_game_chat(CoreEvents.GAME_CHAT_RECEIVED, death, "maicraft_chat")
    assert milestones() == ["你的女仆（莫莫酒狐皮肤）被苦力怕炸死了"]
    assert [c.uuid for c in agent._creatures.creatures() if c.status == STATUS_DEAD] == [_M2]
    await agent.stop()
