"""本文件测生物名册：自己的生物自动建档、主播起名、在不在身边、死讯与墓碑认出是谁，以及按复盘时间线回放。

复盘（审计 P2-5）：主播给身边的女仆起名"阿缕"，系统没记下"阿缕是哪一只"；女仆被苦力怕炸死后，
主播把死讯当陌生人、对着她的墓碑问"是你熟人吗"，收播还记成"阿缕中途不见了"。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Sequence

from src.agents.minecraft.creature_book import CreatureBook, creature_from_record, creature_record
from src.agents.minecraft.glance import STATUS_AWAY, STATUS_DEAD, STATUS_NEARBY, glance_brief, glance_scene
from src.agents.minecraft.maicraft import MaicraftReply
from src.agents.minecraft.tools import STREAMER_GUIDE
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.game_chat import GameChatPayload
from src.modules.storage.database import SQLiteDatabase
from src.modules.tools.models import ToolInvocation

from .minecraft_v1_fakes import FakeMaicraft, make_minecraft_agent

_MAID_SKIN = {"skin_id": "touhou_little_maid:winefox_momo", "skin_name": "莫莫酒狐"}


def _maid(scene_id: str = "e3", distance: int = 2, owner: str = "你的") -> Dict[str, Any]:
    return {
        "id": scene_id,
        "type": "touhou_little_maid:maid",
        "direction": "后方",
        "distance": distance,
        "hostile": False,
        "traits": {"驯服": owner},
    }


def _scene(
    *entities: Dict[str, Any], maids: Sequence[Dict[str, Any]] = (), graves: Sequence[Dict[str, Any]] = ()
) -> Dict[str, Any]:
    return {
        "entities": list(entities),
        "mod_things": {"maid": list(maids), "maid_tombstone": list(graves)},
    }


def _with_maid() -> Dict[str, Any]:
    sheep = {"id": "e1", "type": "minecraft:sheep", "distance": 12, "hostile": False}
    return _scene(_maid(), sheep, maids=[{"id": "e3", **_MAID_SKIN}])


def _with_grave() -> Dict[str, Any]:
    grave = {"id": "e8", "type": "touhou_little_maid:tombstone", "direction": "后方", "distance": 3, "hostile": False}
    return _scene(grave, graves=[{"id": "e8", "maid_name": "莫莫酒狐"}])


def test_own_creatures_are_registered_and_strangers_are_not() -> None:
    book = CreatureBook()
    stray = {"id": "e5", "type": "minecraft:wolf", "distance": 6}
    book.observe(_scene(_maid(), stray, maids=[{"id": "e3", **_MAID_SKIN}]), 1_000)

    [maid] = book.creatures()
    assert maid.status == STATUS_NEARBY and maid.game_name == "莫莫酒狐"
    assert maid.label() == "你的女仆（莫莫酒狐皮肤）"
    assert book.status_view() == [{"who": "你的女仆（莫莫酒狐皮肤）", "status": "在身边"}]


def test_naming_finds_the_one_creature_and_refuses_when_unsure() -> None:
    book = CreatureBook()
    book.observe(_with_maid(), 1_000)

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
    theirs = _maid("e4", distance=5, owner="别人的")
    book.observe(_scene(_maid(), theirs, maids=[{"id": "e3", **_MAID_SKIN}, {"id": "e4", **_MAID_SKIN}]), 1_000)
    assert book.name("女仆", "阿缕").named is not None, "一只是自己的就挑自己的"

    both_wild = _scene(
        {"id": "e6", "type": "minecraft:cat", "direction": "左侧", "distance": 4},
        {"id": "e7", "type": "minecraft:cat", "direction": "右侧", "distance": 6},
    )
    book.observe(both_wild, 2_000)
    unsure = book.name("猫", "花花")
    assert unsure.named is None and "分不清是哪一只" in unsure.reason and "在左侧" in unsure.reason


def test_out_of_sight_is_a_state_not_an_event() -> None:
    """离开视野只改状态：起过名的在速览里说一句"这会儿没在身边"，没起名的不提。"""
    book = CreatureBook()
    book.observe(_with_maid(), 1_000)
    book.observe(_scene(), 2_000)
    assert book.creatures()[0].status == STATUS_AWAY
    assert book.status_view() == [], "没起名、没看到的自己的生物不提"

    book.observe(_with_maid(), 3_000)
    book.name("你的女仆", "阿缕")
    book.observe(_scene(), 4_000)
    [item] = book.status_view()
    assert item["status"] == STATUS_AWAY and item["named"] is True
    assert "阿缕（你的女仆，莫莫酒狐皮肤）这会儿没在身边" in glance_brief({"creatures": book.status_view()})


def test_a_death_message_is_recognized_only_when_it_is_surely_hers() -> None:
    book = CreatureBook()
    book.observe(_with_maid(), 1_000)

    assert book.note_death("莫莫酒狐 的好感度提升了", 2_000) is None, "以她的名字开头但不是死讯"
    assert book.note_death("Steve被苦力怕炸死了", 2_000) is None, "别人的死讯不归她"
    dead = book.note_death("莫莫酒狐被苦力怕炸死了", 2_000)
    assert dead is not None and dead.status == STATUS_DEAD and dead.death_note == "被苦力怕炸死了"
    assert book.note_death("莫莫酒狐被苦力怕炸死了", 2_100) is None, "同一次死只认一次"

    twins = CreatureBook()
    twins.observe(
        _scene(_maid(), _maid("e4", distance=5), maids=[{"id": "e3", **_MAID_SKIN}, {"id": "e4", **_MAID_SKIN}]),
        1_000,
    )
    assert len(twins.creatures()) == 1, "没有 UUID 时同皮肤的两只拼出同一个身份键"


def test_her_grave_confirms_the_death_and_is_called_by_her_name() -> None:
    book = CreatureBook()
    book.observe(_with_maid(), 1_000)
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

    book.observe(_with_maid(), 3_000)
    assert maid.status == STATUS_NEARBY, "又看到她（认错了或被复活）就当活着"


def test_game_agent_and_streamer_are_told_about_names() -> None:
    """游戏 Agent 拿到"名字 → 是哪一只、此刻怎样"，主播的玩法说明写明起名要登记、已死就是死了。"""
    book = CreatureBook()
    book.observe(_with_maid(), 1_000)
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
    server.scene_view = _with_maid()
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
    book.observe(_with_maid(), 1_000)
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
    book.observe(_scene(wolf), 3_000)
    taken = book.name("狼", "阿缕")
    assert taken.named is None and "它已经死了，名字留给它" in taken.reason


def test_changes_are_handed_out_once_and_a_reload_restores_the_roster() -> None:
    """只有落库的事实变了才交出去写回；按落库的行读回来，名字还在，此刻在不在身边等下一次观察。"""
    book = CreatureBook()
    book.load("w1", [])
    book.observe(_with_maid(), 1_000)
    changed, removed = book.take_changes()
    assert [creature.skin for creature in changed] == ["莫莫酒狐"] and removed == []
    book.observe(_with_maid(), 2_000)
    assert book.take_changes() == ([], []), "只是又看到一次，不用写回"

    book.name("你的女仆", "阿缕")
    book.name("羊", "咩咩")
    book.name("咩咩", "")
    changed, removed = book.take_changes()
    assert [creature.nickname for creature in changed] == ["阿缕"] and removed == ["look:minecraft:sheep||"]

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
        server.scene_view = _with_maid()
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
