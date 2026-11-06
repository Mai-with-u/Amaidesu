"""本文件测 Minecraft 的地点动静：记地点、忘地点成功时发"记住""忘掉"，速览时认出"走近""离开"。

地点动静只发给存储给下播整理认地点：不改游戏 Agent 怎么做事，读位置不多读游戏。
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

from src.agents.minecraft.place_marks import (
    PLACE_NEAR_REFRESH_S,
    NearPlaceTracker,
    PlaceMark,
    place_change_of,
)
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_place import GamePlacePayload
from src.modules.llm.payload import Response
from src.modules.types.game_situation import GameSituation, NearbyPlace

from .minecraft_v1_fakes import FakeMaicraft, _call, _reports, _resp, _wait_until, make_minecraft_agent

_OVERWORLD = "minecraft:overworld"


def _situation(*places: tuple[str, int]) -> GameSituation:
    return GameSituation(places=tuple(NearbyPlace(name=name, distance=distance) for name, distance in places))


def _goal(**parameters: Any) -> Dict[str, Any]:
    return {"goal": {"ability": "remember", "parameters": parameters}}


def test_place_change_of_tells_remember_from_forget_and_skips_regions() -> None:
    assert place_change_of(_goal(name="家")) == PlaceMark(kind="remembered", place="家")
    assert place_change_of(_goal(name=" 家 ", operation="forget")) == PlaceMark(kind="forgotten", place="家")
    assert place_change_of(_goal(name="小明的地盘", operation="region")) is None, "圈地盘不是记住的地点"
    assert place_change_of(_goal()) is None
    assert place_change_of({}) is None


def test_near_tracker_notes_arrival_once_per_refresh_and_notes_leaving() -> None:
    """走到跟前记一条；一直待着五分钟内不再记；换到另一处记新的；走出范围记离开；情境不明不记也不改状态。"""
    now = [0.0]
    tracker = NearPlaceTracker(lambda: now[0])

    assert tracker.observe("a", _situation(("家", 30))) == [], "24 格外不算到了跟前"
    assert tracker.observe("a", _situation(("家", 12))) == [PlaceMark(kind="near", place="家", distance=12)]
    now[0] += PLACE_NEAR_REFRESH_S - 1
    assert tracker.observe("a", _situation(("家", 3))) == []
    assert tracker.observe("a", None) == []
    now[0] += 1
    assert tracker.observe("a", _situation(("家", 3))) == [PlaceMark(kind="near", place="家", distance=3)]
    assert tracker.observe("a", _situation(("矿洞口", 20), ("家", 40))) == [
        PlaceMark(kind="near", place="矿洞口", distance=20)
    ]
    assert tracker.observe("a", _situation(("矿洞口", 25))) == [PlaceMark(kind="left", place="矿洞口")]
    assert tracker.observe("a", _situation()) == [], "已经离开了，不重复记"


def test_near_tracker_does_not_note_leaving_a_place_of_the_previous_save() -> None:
    """换了存档：同名地点也按新存档重新记走近；旧存档的地点不记离开（那条会被记到新存档名下）。"""
    tracker = NearPlaceTracker(lambda: 0.0)
    assert tracker.observe("a", _situation(("家", 5))) == [PlaceMark(kind="near", place="家", distance=5)]
    assert tracker.observe("b", _situation(("家", 5))) == [PlaceMark(kind="near", place="家", distance=5)]
    assert tracker.observe("a", _situation()) == []


def _place_marks(bus: MagicMock) -> List[GamePlacePayload]:
    return [
        call.args[1]
        for call in bus.emit.await_args_list
        if call.args[0] == CoreEvents.GAME_PLACE_NOTED and isinstance(call.args[1], GamePlacePayload)
    ]


async def test_glance_notes_arriving_at_and_leaving_a_remembered_place_without_extra_reads() -> None:
    server = FakeMaicraft()
    server.enter_world("id-home", "老家")
    server.self_view["position"] = {"x": 8.0, "y": 64.0, "z": -3.0, "dimension": _OVERWORLD}
    server.world_memory_view["places"] = [
        {"name": "家", "position": {"x": 10, "y": 64, "z": -3, "dimension": _OVERWORLD}, "distance": 2}
    ]
    agent, _llm, bus = await make_minecraft_agent(server, lambda messages: _resp("好"), clock=lambda: 1000.0)

    await agent._glance()
    reads = list(server.calls_to("observe"))
    await agent._glance()
    assert [(m.kind, m.place, m.distance, m.save_id, m.save_name) for m in _place_marks(bus)] == [
        ("near", "家", 2, "id-home", "老家")
    ], "到了跟前记一条，接着看不重复记"
    assert server.calls_to("observe")[len(reads) :] == [{"what": "self"}, {"what": "scene"}], (
        "第二次速览照旧只看自己与周围，地点清单用缓存，没为地点动静多读游戏"
    )

    server.self_view["position"] = {"x": 200.0, "y": 64.0, "z": -3.0, "dimension": _OVERWORLD}
    await agent._glance()
    assert [(m.kind, m.place) for m in _place_marks(bus)][-1] == ("left", "家")
    await agent.stop()


async def test_a_successful_remember_is_noted_with_the_save_it_happened_in() -> None:
    server = FakeMaicraft()
    server.enter_world("id-home", "老家")

    def script(messages: List[Dict[str, Any]]) -> Response:
        if not any(message.get("role") == "tool" and "记住了" in str(message.get("content")) for message in messages):
            return _resp(
                calls=[_call("maicraft_execute", {"goal": {"ability": "remember", "parameters": {"name": "家"}}})]
            )
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "记好了，这里记成了家"})])

    agent, _llm, bus = await make_minecraft_agent(server, script, clock=lambda: 1000.0)
    agent.receive_delegation(instruction="把这里记成「家」", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    assert [(m.kind, m.place, m.save_id) for m in _place_marks(bus)] == [("remembered", "家", "id-home")]
    await agent.stop()
