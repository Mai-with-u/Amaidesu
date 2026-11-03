"""本文件测 MinecraftAgent 的看一眼主题：新任务开局宿主代读资料包、glance 现场视图与白话摘要、未连接时拒收委派。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from src.agents.minecraft.glance import entity_label, glance_brief, glance_scene
from src.agents.minecraft.maicraft import MaicraftReply, goal_run_of
from src.modules.llm.payload import Response

from .minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _reports,
    _resp,
    _texts,
    _wait_until,
    assemble_minecraft_agent,
    make_minecraft_agent,
)


async def test_look_around_and_remember_a_place_end_to_end() -> None:
    """WP-2.6 验收：委派"看一眼周围并记住一个地点"，开局资料就位，记地点当场完成，交付一次。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if not any(message.get("role") == "tool" and "记住了" in str(message.get("content")) for message in messages):
            return _resp(
                "周围有牛和箱子，记下这里",
                [_call("maicraft_execute", {"goal": {"ability": "remember", "parameters": {"name": "家"}}})],
            )
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了，这里记成了家"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="看一眼周围并记住一个地点", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    first = llm.seen[0]
    opening = [index for index, message in enumerate(first) if "[开局资料]" in str(message.get("content"))]
    assert opening, "新任务开局附上宿主代读的资料"
    message = first[opening[0]]
    assert message["role"] == "user" and "看一眼周围并记住一个地点" in str(first[opening[0] - 1]["content"])
    # 每项一段"### 调用名 参数"，读取顺序不变
    read_tools = [part["text"].lstrip("\n").split(" ")[1] for part in message["content"][1:]]
    assert read_tools == ["maicraft_observe", "maicraft_observe", "maicraft_lookup", "maicraft_goal"]
    # 官方 DeepSeek 核对工具调用 id、Gemini 3 核对签名：模型第一次响应前不能有宿主编出来的调用
    assert not any(m.get("role") == "assistant" or m.get("role") == "tool" for m in first)
    assert "minecraft:cow" in _texts(first) and "maicraft:remember" in _texts(first)
    assert all(tool["name"] != "maicraft_events" for tool in llm.tools_given[0]), "事件流不给模型"
    assert _reports(bus)[0].report_kind == "delivery"
    assert agent.tracked_goal_ids == [], "当场完成的目标不跟踪"
    await agent.stop()


async def test_glance_shows_body_nearby_things_and_running_goals() -> None:
    server = FakeMaicraft()
    agent, llm, bus = await make_minecraft_agent(server, lambda messages: _resp("好"))
    run = goal_run_of(
        {"goal_id": 4, "ability": "maicraft:gather", "purpose": "砍树", "state": "running", "doing": "走向树"}
    )
    assert run is not None
    agent._track_goal(run)

    view = await agent._glance()

    assert view["body"]["position"] == [4, 64, -7] and view["body"]["held"] == "minecraft:stone_axe"
    assert view["inventory"] == ["minecraft:oak_log ×6"]
    assert [entity["type"] for entity in view["entities"]] == ["minecraft:cow"], "远处的苦力怕不算身边"
    assert view["distant_entities"] == [
        "minecraft:creeper ×1",
        "minecraft:squid ×30",
        "minecraft:bat ×12",
        "minecraft:player（Steve） ×1",
    ], "远处的按种类计数：逐只列出的与只计数的合在一起，敌对的在前，玩家带名字"
    assert view["facilities"][0]["block"] == "minecraft:chest"
    assert view["work"]["goals"] == [
        {"ability": "maicraft:gather", "purpose": "砍树", "state": "running", "doing": "走向树"}
    ]
    # 一句话现场排在最前面：主播的现场卡只放这一句白话，不带数字和坐标
    assert list(view)[:2] == ["tool", "brief"]
    assert view["brief"] == (
        "在主世界的森林。白天，晴。血是满的，有点饿。手上拿着石斧。背包里有oak_log。"
        "身边还有牛。附近有箱子。远处还有苦力怕、鱿鱼、蝙蝠等。手上在砍树，眼下走向树。"
    )
    await agent.stop()


def test_glance_brief_is_plain_speech_without_numbers() -> None:
    """一句话现场：敌对生物带方位排在前头，有名字的带名字，没连上游戏照实说，全程不带数字。"""
    view = {
        "body": {"dimension": "minecraft:the_nether", "health": 4, "food": 6, "in_water": True},
        "biome": "minecraft:soul_sand_valley",
        "inventory": ["create:wrench ×1", "minecraft:diamond_pickaxe ×1"],
        "entities": [
            {"type": "minecraft:skeleton", "direction": "左后方", "distance": 8, "hostile": True},
            {"type": "minecraft:wolf", "name": "小白", "direction": "右侧", "distance": 3},
        ],
        "facilities": [{"block": "minecraft:red_bed", "distance": 5}],
        "work": {"state": "卡住了，正等我拿主意", "todo": []},
    }
    brief = glance_brief(view)
    assert brief == (
        "在下界的灵魂沙峡谷，泡在水里。血快没了，很饿。背包里有create:wrench、钻石镐。"
        "身边有骷髅在左后方，是敌对的。身边还有狼（叫小白）。附近有床。手上：卡住了，正等我拿主意。"
    )
    assert not any(char.isdigit() for char in brief)
    assert glance_brief({"unavailable": "游戏连接还没就绪", "work": {"state": "空闲"}}) == "游戏连接还没就绪"


def test_glance_distant_entities_only_counts_what_is_known_to_be_far() -> None:
    """远处计数只收确实在远处的：没距离的不算，坏计数不算，远处什么都没有时不写这一栏。"""
    scene = {
        "entities": [
            {"type": "minecraft:zombie", "distance": 30, "hostile": True},
            {"type": "minecraft:zombie", "distance": 50, "hostile": True},
            {"type": "minecraft:sheep"},
            {"type": "minecraft:pig", "distance": 10},
        ],
        "distant_entities": {"minecraft:squid": 3, "minecraft:bat": 0, "minecraft:cod": "很多", "minecraft:fox": True},
    }
    view = glance_scene(MaicraftReply(ok=True, data=scene))
    assert view["distant_entities"] == ["minecraft:zombie ×2", "minecraft:squid ×3"]
    assert [entity["type"] for entity in view["entities"]] == ["minecraft:pig"]

    nearby_only = glance_scene(MaicraftReply(ok=True, data={"entities": [{"type": "minecraft:pig", "distance": 10}]}))
    assert "distant_entities" not in nearby_only, "远处没有东西不写这一栏，上游没给也不写"


def _maid_scene() -> Dict[str, Any]:
    """MaiCraft 装了车万女仆时 observe(scene) 的样子：女仆与墓碑在 entities 里只有种类，身份在 mod_things 里同号给出。"""
    return {
        "entities": [
            {
                "id": "e3",
                "type": "touhou_little_maid:maid",
                "direction": "后方",
                "distance": 2,
                "hostile": False,
                "traits": {"驯服": "你的", "坐着": "是"},
            },
            {"id": "e7", "type": "touhou_little_maid:tombstone", "direction": "左侧", "distance": 5, "hostile": False},
            {"id": "e9", "type": "minecraft:wolf", "name": "小白", "distance": 8, "traits": {"驯服": "别人的"}},
            {"id": "e1", "type": "minecraft:sheep", "distance": 12, "hostile": False},
        ],
        "mod_things": {
            "maid": [
                {
                    "id": "e3",
                    "distance": 2,
                    "skin_id": "touhou_little_maid:winefox_momo",
                    "skin_name": "莫莫酒狐",
                    "work": "touhou_little_maid:idle",
                }
            ],
            "maid_tombstone": [{"id": "e7", "distance": 5, "maid_name": "莫莫酒狐"}],
            "gomoku_board": [{"id": "b2", "turn": "you"}],
        },
    }


def test_glance_tells_whose_creature_and_whose_grave_it_is() -> None:
    """复盘里女仆在身边一个多小时，速览只写"touhou_little_maid:maid"，死后的墓碑也认不出是谁的。
    现在驯服关系、坐着、女仆皮肤、墓碑上的名字都按观察编号并到同一只实体上；编号本身不交给主播。"""
    view = glance_scene(MaicraftReply(ok=True, data=_maid_scene()))

    maid, grave, wolf, sheep = view["entities"]
    assert maid == {
        "type": "touhou_little_maid:maid",
        "kind": "女仆",
        "skin": "莫莫酒狐",
        "owner": "你的",
        "sitting": True,
        "direction": "后方",
        "distance": 2,
        "hostile": False,
    }
    assert grave["kind"] == "女仆墓碑" and grave["grave_of"] == "莫莫酒狐"
    assert wolf["owner"] == "别人的" and wolf["name"] == "小白"
    assert sheep == {"type": "minecraft:sheep", "distance": 12, "hostile": False}
    assert not any("id" in entity for entity in view["entities"]), "观察编号不交给主播"

    brief = glance_brief(view)
    assert brief == (
        "你的女仆（莫莫酒狐皮肤，坐着）在身边。身边还有女仆墓碑（上面写着莫莫酒狐）、别人的狼（叫小白）、羊。"
    )


def test_glance_label_puts_the_nickname_first() -> None:
    """主播起过名的生物：名字当称呼，"你的女仆"和皮肤退到括号里当说明。"""
    maid = {"type": "touhou_little_maid:maid", "kind": "女仆", "skin": "莫莫酒狐", "owner": "你的"}
    assert entity_label(maid) == "你的女仆（莫莫酒狐皮肤）"
    assert entity_label({**maid, "nickname": "阿缕"}) == "阿缕（你的女仆，莫莫酒狐皮肤）"
    assert entity_label({"type": "minecraft:cow"}) == "牛"


def test_glance_without_mod_things_keeps_the_plain_view() -> None:
    """没装车万女仆（没有 mod_things）时照旧：只有种类与方位，驯服关系照样带上。"""
    scene = {"entities": [{"id": "e2", "type": "minecraft:cat", "distance": 3, "traits": {"驯服": "你的"}}]}
    view = glance_scene(MaicraftReply(ok=True, data=scene))
    assert view["entities"] == [{"type": "minecraft:cat", "owner": "你的", "distance": 3}]
    assert glance_brief(view) == "你的猫在身边。"


@pytest.mark.parametrize(
    "provider",
    [SimpleNamespace(list_tools=lambda: []), None, FakeMaicraft()],
    ids=["connected_but_no_tools", "no_provider", "connected_with_tools"],
)
async def test_a_delegation_is_refused_when_the_game_is_not_connected(provider: Any) -> None:
    """10-15 15:30 那场 maicraft 一个工具都没有，游戏 Agent 照样接了委派，拿截屏翻四块显示器找游戏窗口。
    现在直接拒收，原因原样回给派活的一方；连上（有工具）后照常接。私有 MCP 面经构造注入。"""
    agent = assemble_minecraft_agent(llm=MagicMock(), live=True, mcp_provider=provider)

    refused = agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    if provider is not None and len(list(provider.list_tools())):
        assert refused is None
        assert len(agent.queued_messages) == 1
    else:
        assert refused is not None and refused.startswith("游戏没连上")
        assert len(agent.queued_messages) == 0
