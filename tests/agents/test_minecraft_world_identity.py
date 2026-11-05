"""认出所在存档：旧版 Mod 不分存档、主菜单不算换、换存档时工作文档分开存、回老存档还原。"""

from __future__ import annotations

from typing import Any, Dict, List

from src.agents.minecraft.goals import GoalWatch
from src.agents.minecraft.maicraft import EventsPage, MaicraftReply, reply_of
from src.agents.minecraft.state import MinecraftAgentState
from src.agents.minecraft.world_identity import WorldIdentity, world_ref_of
from src.modules.events.payloads.game import GamePayload
from src.modules.tools.models import ToolInvocation
from src.modules.types.game_save import GameSave

from .minecraft_v1_fakes import FakeMaicraft, assemble_minecraft_agent, fake_bus


def _world(world_id: str, name: str = "") -> Dict[str, Any]:
    return {"world": {"id": world_id, "name": name, "source": "world_id_file"}}


def _page(world_fields: Dict[str, Any]) -> EventsPage:
    return EventsPage(
        stream_id="s", cursor=0, events=[], has_more=False, cursor_status="valid", world_fields=world_fields
    )


def _milestones(bus: Any) -> List[GamePayload]:
    return [
        call.args[1]
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "milestone"
    ]


# ---------------------------------------------------------------------------
# WorldIdentity
# ---------------------------------------------------------------------------


def test_old_mod_without_world_key_reports_no_save() -> None:
    """回复里没有 world 键是旧版 Mod：报不出存档，主播照旧不分存档。"""
    identity = WorldIdentity()
    assert identity.absorb({"stream_id": "s"}) is None
    assert identity.legacy and identity.game_save() is None
    assert identity.save_fields() == ("", "")


def test_unknown_until_identified_then_first_identification_is_not_a_switch() -> None:
    """还没读到或 world 为 null 时存档不明；进程启动后第一次认出只是认出，不算换。"""
    identity = WorldIdentity()
    assert identity.game_save() == GameSave()
    assert identity.absorb({"world": None}) is None
    assert identity.game_save() == GameSave()

    change = identity.absorb(_world("aaa", "老家"))

    assert change is not None and change.before is None and not change.switched
    assert identity.game_save() == GameSave(id="aaa", name="老家")
    assert identity.save_fields() == ("aaa", "老家")


def test_main_menu_and_reentering_same_world_is_not_a_switch() -> None:
    """回主菜单（null）再进同一个存档：不算换；进另一个存档才算，before 是上一个认出的。"""
    identity = WorldIdentity()
    identity.absorb(_world("aaa", "老家"))
    assert identity.absorb({"world": None}) is None
    assert identity.game_save() == GameSave()
    assert identity.absorb(_world("aaa", "老家")) is None

    identity.absorb({"world": None})
    change = identity.absorb(_world("bbb", "新的世界"))

    assert change is not None and change.switched
    assert change.before is not None and change.before.label() == "老家"
    assert change.after.label() == "新的世界"


def test_observation_updates_save_but_never_decides_old_mod() -> None:
    """observe(self) 只更新眼下的存档：某次回复缺 world 键不算旧版 Mod，已认出的存档不退回不分。"""
    identity = WorldIdentity()
    identity.absorb(_world("aaa", "老家"))

    assert identity.absorb_observation({"name": "角色"}) is None
    assert not identity.legacy
    assert identity.game_save() == GameSave(id="aaa", name="老家")

    change = identity.absorb_observation(_world("bbb", "新的世界"))
    assert change is not None and change.switched
    assert identity.game_save() == GameSave(id="bbb", name="新的世界")

    # 事件页说没有 world 键才是旧版 Mod；之后 observe(self) 带着 world 也不翻回来
    old_mod = WorldIdentity()
    old_mod.absorb({"stream_id": "s"})
    old_mod.absorb_observation(_world("bbb", "新的世界"))
    assert old_mod.legacy and old_mod.game_save() is None


def test_page_missing_world_after_seeing_it_means_not_in_world() -> None:
    """见过带 world 的页之后，某页缺了这个键：MaiCraft 不在世界里时 world 为 null、Gson 不写 null 字段，
    按不在世界里处理（存档不明），不退回旧版 Mod；重进同一个存档不算换，进别的存档照常算换。"""
    identity = WorldIdentity()
    identity.absorb(_world("aaa", "老家"))

    assert identity.absorb({"stream_id": "s"}) is None
    assert not identity.legacy
    assert identity.game_save() == GameSave() and identity.save_fields() == ("", "")

    assert identity.absorb(_world("aaa", "老家")) is None
    assert identity.game_save() == GameSave(id="aaa", name="老家")

    identity.absorb({"stream_id": "s"})
    change = identity.absorb(_world("bbb", "新的世界"))
    assert change is not None and change.switched and change.before is not None
    assert change.before.label() == "老家"


def test_world_null_page_also_counts_as_seeing_the_key() -> None:
    """头一页就带 world: null 也说明是新版 Mod：之后缺键同样按不在世界里，不当旧版。"""
    identity = WorldIdentity()
    identity.absorb({"world": None})

    assert identity.absorb({"stream_id": "s"}) is None
    assert not identity.legacy and identity.game_save() == GameSave()


def test_world_ref_reading_tolerates_bad_shapes() -> None:
    """编号为空或形状不对按不明；名字压成一行、截短；没有名字时用编号前几位称呼。"""
    assert world_ref_of(None) is None
    assert world_ref_of({"id": "  "}) is None
    assert world_ref_of("aaa") is None
    ref = world_ref_of({"id": "0123456789abcdef", "name": "  新的\n世界  "})
    assert ref is not None and ref.name == "新的 世界"
    nameless = world_ref_of({"id": "0123456789abcdef"})
    assert nameless is not None and nameless.label() == "编号 01234567"


# ---------------------------------------------------------------------------
# MinecraftAgentState：工作文档按存档分开
# ---------------------------------------------------------------------------


def test_state_adopts_unowned_docs_then_shelves_and_restores_per_world() -> None:
    """还没归属的文档归第一个认出的存档；换存档收起旧的、新存档是空的；回老存档还原。"""
    state = MinecraftAgentState()
    state.set_notebook("老家的箱子在门口")
    assert state.switch_world("aaa") is False  # 第一次认出：手上的就是这个存档的
    assert state.notebook == "老家的箱子在门口"
    state.set_todos([{"content": "封屋顶", "status": "pending"}])
    state.add_report("delivery", "屋子盖好了")

    assert state.switch_world("bbb") is True
    assert state.to_dict() == {"todo": [], "notebook": "", "recent_reports": []}
    state.set_notebook("新世界出生点有村庄")

    assert state.switch_world("aaa") is True
    assert state.notebook == "老家的箱子在门口"
    assert [item.content for item in state.todos] == ["封屋顶"]
    assert state.reports == [{"kind": "delivery", "content": "屋子盖好了", "scene": ""}]
    assert state.switch_world("aaa") is False
    assert state.switch_world("") is False
    assert state.shelved["bbb"].notebook == "新世界出生点有村庄"


# ---------------------------------------------------------------------------
# GoalWatch 把每一页交出去
# ---------------------------------------------------------------------------


async def test_goal_watch_hands_every_page_with_its_world() -> None:
    server = FakeMaicraft()
    seen: List[Dict[str, Any]] = []

    async def call(name: str, arguments: Dict[str, Any]) -> MaicraftReply:
        return reply_of(await server.invoke(ToolInvocation(tool_name=name, arguments=arguments, source="test")))

    async def on_page(page: EventsPage) -> None:
        seen.append(dict(page.world_fields))

    async def ignore(*_args: Any) -> None:
        return None

    watch = GoalWatch(
        call=call,
        connected=lambda: True,
        on_goal_changed=ignore,
        on_goal_gone=ignore,
        on_body_event=ignore,
        on_page=on_page,
        wait_ms=0,
    )
    await watch.step()  # 旧版 Mod：页里没有 world 键
    server.enter_world("aaa", "老家")
    await watch.step()
    server.leave_world()
    await watch.step()

    assert seen == [{}, _world("aaa", "老家"), {"world": None}]


# ---------------------------------------------------------------------------
# MinecraftAgent：认出存档、切换工作文档、通知任务、发换存档里程碑
# ---------------------------------------------------------------------------


async def test_agent_switches_docs_notifies_task_and_announces_switch() -> None:
    bus = fake_bus()
    agent = assemble_minecraft_agent(bus=bus)
    state = agent._mc_state

    await agent._on_events_page(_page(_world("aaa", "老家")))
    assert agent._tool_provider.current_save() == GameSave(id="aaa", name="老家")
    assert _milestones(bus) == []  # 第一次认出不发
    state.set_notebook("老家的屋顶还漏风")
    await agent._emit_game_event("milestone", "屋子盖好了")
    assert (_milestones(bus)[-1].save_id, _milestones(bus)[-1].save_name) == ("aaa", "老家")

    agent._task_finished = False  # 手上有个在老家接的任务
    await agent._on_events_page(_page({"world": None}))  # 回主菜单
    assert agent._tool_provider.current_save() == GameSave()
    await agent._on_events_page(_page(_world("bbb", "新的世界")))

    assert state.notebook == ""
    assert agent._tool_provider.current_save() == GameSave(id="bbb", name="新的世界")
    notices = [str(item) for item in agent._message_queue]
    assert any("新的世界" in text and "老家" in text and "escalation" in text for text in notices)
    switch = _milestones(bus)[-1]
    assert switch.message == "从存档「老家」换到了「新的世界」"
    assert (switch.save_id, switch.save_name) == ("bbb", "新的世界")

    await agent._on_events_page(_page(_world("aaa", "老家")))
    assert state.notebook == "老家的屋顶还漏风"


async def test_agent_without_task_switches_quietly_and_old_mod_stays_unsplit() -> None:
    """空闲时换存档不给任务发通知；旧版 Mod 一直不分存档，工作文档不动。"""
    bus = fake_bus()
    agent = assemble_minecraft_agent(bus=bus)
    await agent._on_events_page(_page(_world("aaa")))
    await agent._on_events_page(_page(_world("bbb")))
    assert len(agent._message_queue) == 0
    assert len(_milestones(bus)) == 1

    legacy = assemble_minecraft_agent(bus=fake_bus())
    legacy._mc_state.set_notebook("笔记")
    await legacy._on_events_page(_page({}))
    assert legacy._tool_provider.current_save() is None
    assert legacy._mc_state.notebook == "笔记" and legacy._mc_state.world_id == ""
