"""拿了别人家的吃的：others_food_taken 事件读成条目、给主播的一句话与按世界的记账。

覆盖：
- 给主播的话：知道主人的点名，区域按区域名对号，说不清是谁的按地方对号；坐标去掉，回应口径带上
- 结构化字段缺失或写错时按说不清是谁的算，只留原话
- 记账按世界分开，每个世界只留最近几笔
- 游戏 Agent 闲着时也记下，放进任务事实，主人来问时对得上号
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.agents.minecraft.others_food import OTHERS_FOOD_TAKEN, FoodTakenLog, food_taken_of, spoken_line

from src.modules.llm.payload import Response

from .minecraft_v1_fakes import FakeMaicraft, _call, _resp, _texts, _wait_until, make_minecraft_agent


def _event(message: str, detail: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    event: Dict[str, Any] = {"kind": OTHERS_FOOD_TAKEN, "goal_id": -1, "message": message}
    if detail is not None:
        event[OTHERS_FOOD_TAKEN] = detail
    return event


def _player_take(owner: str = "Alex") -> Dict[str, Any]:
    return _event(
        f"饿到饱食度 3，从 {owner} 家的箱子（12, 64, -8）拿了 2 个面包",
        {
            "whose": "player",
            "owner": owner,
            "at": {"x": 12, "y": 64, "z": -8, "dimension": "minecraft:overworld"},
            "items": [{"item": "minecraft:bread", "count": 2}],
            "food": 3,
            "losing_health": False,
        },
    )


def test_owner_is_named_and_coordinates_are_dropped() -> None:
    """知道主人是谁：点名他来问时大方认下；坐标对主播没用，去掉，其余原话照留。"""
    line = spoken_line(food_taken_of(_player_take(), at_ms=1))
    assert line.startswith("饿到饱食度 3，从 Alex 家的箱子拿了 2 个面包。")
    assert "12, 64" not in line
    assert "Alex 来问，就大方认下是我拿的" in line and "俏皮" in line
    assert "道个歉" in line and "别把话说死" in line, "对方真生气、名字没核实时的口径一并带上"


def test_ownerless_takes_are_matched_by_place() -> None:
    """记在区域里的按区域名对号；看着像玩家放的、说不清是谁的按地方对号，不点名。"""
    region = food_taken_of(
        _event(
            "饿到饱食度 2，从记住的区域「南边农场」里的箱子（3, 70, 9）拿了 1 个苹果",
            {"whose": "region", "region": "南边农场", "at": {"x": 3, "y": 70, "z": 9}, "items": []},
        ),
        at_ms=1,
    )
    assert "「南边农场」那边箱子里少了吃的" in spoken_line(region)
    for whose, said in (("looks_player_made", "看着像玩家放的箱子"), ("unknown", "不知道是谁的箱子")):
        entry = food_taken_of(_event(f"饿到饱食度 2，从{said}拿了 1 个苹果", {"whose": whose, "items": []}), at_ms=1)
        assert "有人说那附近箱子里少了吃的" in spoken_line(entry), whose


def test_fields_are_kept_only_where_they_belong() -> None:
    """主人只在 whose=player 时算数，区域只在 whose=region 时算数；明细缺了按说不清算，只留原话。"""
    mixed = food_taken_of(
        _event("拿了吃的", {"whose": "region", "owner": "Steve", "region": "北边仓库", "food": True}), at_ms=1
    )
    assert (mixed.owner, mixed.region, mixed.food) == ("", "北边仓库", None)
    bare = food_taken_of(_event("饿到饱食度 1，从不知道是谁的箱子拿了 1 个面包", None), at_ms=1)
    assert (bare.whose, bare.message) == ("unknown", "饿到饱食度 1，从不知道是谁的箱子拿了 1 个面包")
    built = spoken_line(food_taken_of(_event("", {"whose": "player", "owner": "Steve", "items": []}), at_ms=1))
    assert built.startswith("饿急了，从Steve 家的箱子拿了吃的"), "原话缺了按字段拼一句"


def test_log_keeps_recent_takes_per_world() -> None:
    """每个世界只看自己的账，换了存档不把别处的算进来；每个世界只留最近几笔。"""
    log = FoodTakenLog(keep_per_world=2)
    for owner in ("Alex", "Steve", "Alex"):
        log.record("world-a", food_taken_of(_player_take(owner), at_ms=1))
    log.record("world-b", food_taken_of(_player_take("Steve"), at_ms=1))
    assert [entry["owner"] for entry in log.view("world-a")] == ["Steve", "Alex"]
    assert [entry["owner"] for entry in log.view("world-b")] == ["Steve"]
    assert log.view("world-c") == []
    assert log.view("world-a")[0]["at"] == {"x": 12, "y": 64, "z": -8, "dimension": "minecraft:overworld"}


async def test_agent_keeps_takes_in_task_facts_while_not_thinking() -> None:
    """主人多半过一阵才来问：模型没在想事时收到也记下，之后的任务事实里带着主人、位置和拿了什么。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp("等目标事件通知")
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])

    agent, llm, _ = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent.batch_active is False and len(llm.seen) == 2)
    assert "others_food_taken" not in agent._current_task_context()

    take = _player_take("Steve")
    server.body_event(OTHERS_FOOD_TAKEN, take["message"], {OTHERS_FOOD_TAKEN: take[OTHERS_FOOD_TAKEN]})
    await _wait_until(lambda: "others_food_taken" in agent._current_task_context())

    [entry] = agent._current_task_context()["others_food_taken"]
    assert entry["owner"] == "Steve" and entry["at"]["x"] == 12
    assert entry["items"] == [{"item": "minecraft:bread", "count": 2}]
    await agent.stop()


async def test_a_new_task_starts_knowing_what_was_taken() -> None:
    """上一件活交付后才拿的：新任务一开工就附上这笔，主播转来主人的问话时认得出是自己拿的。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过了"})])

    agent, llm, _ = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="看看周围", task_id="d-1")
    await _wait_until(lambda: agent.batch_active is False and len(llm.seen) == 1)

    take = _player_take("Steve")
    server.body_event(OTHERS_FOOD_TAKEN, take["message"], {OTHERS_FOOD_TAKEN: take[OTHERS_FOOD_TAKEN]})
    await _wait_until(lambda: "others_food_taken" in agent._current_task_context())
    agent.receive_delegation(instruction="Steve 在聊天里问是不是你拿了他箱子里的面包", task_id="d-2")
    await _wait_until(lambda: len(llm.seen) == 2)

    opened = _texts(llm.seen[-1])
    assert "[拿过别人家的吃的]" in opened and "Steve" in opened and "minecraft:bread" in opened
    await agent.stop()
