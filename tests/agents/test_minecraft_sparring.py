"""切磋打完：fight(spar=true) 的结果读成给主播的一句话，并在目标结束时发给主播。

覆盖：
- 各种收手方式说清输赢与各打中几下，口径写明俏皮带过、不提血量；血量数字不进这句话
- 没打起来或被取消的、不是切磋的结果不说
- 游戏 Agent 自己下的切磋结束时发一条 milestone 给主播，普通目标结束不发
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from src.agents.minecraft.maicraft import GoalRun
from src.agents.minecraft.sparring import spar_outcome_line
from src.modules.events.payloads.game import GamePayload
from src.modules.llm.payload import Response

from .minecraft_v1_fakes import FakeMaicraft, _call, _reports, _resp, _texts, _wait_until, make_minecraft_agent


def _spar_details(ended_by: str, hits: int = 5, taken: int = 3) -> Dict[str, Any]:
    return {
        "weapon": "minecraft:iron_sword",
        "hits": hits,
        "spar": {"ended_by": ended_by, "hits_taken": taken, "my_health": 9.5, "their_health": 6.0},
    }


def _run(details: Optional[Dict[str, Any]], purpose: str = "") -> GoalRun:
    result: Dict[str, Any] = {"status": "done", "summary": "切磋结束"}
    if details is not None:
        result["details"] = details
    return GoalRun(
        goal_id=7,
        ability="maicraft:fight",
        state="finished",
        purpose=purpose,
        question=None,
        doing="",
        result=result,
        data={},
    )


@pytest.mark.parametrize(
    ("ended_by", "verdict"),
    [
        ("they_at_line", "算我赢了"),
        ("finishing_blow", "算我赢了"),
        ("me_at_line", "算我输了"),
        ("they_stopped", "他先停手了"),
        ("they_left", "没分出胜负"),
        ("no_hits", "谁都没打中谁"),
    ],
)
def test_a_finished_spar_tells_who_won_and_the_score(ended_by: str, verdict: str) -> None:
    line = spar_outcome_line(_run(_spar_details(ended_by)))
    assert line is not None and line.startswith("切磋了一场：") and verdict in line
    assert "我打中他 5 下，他打中我 3 下" in line
    assert "俏皮" in line and "不提血量" in line
    assert "9.5" not in line and "6.0" not in line, "血量不进主播的话"


def test_the_purpose_is_kept_and_a_missing_score_is_left_out() -> None:
    details = {"spar": {"ended_by": "they_stopped"}}
    line = spar_outcome_line(_run(details, purpose="接 Alex 的切磋"))
    assert line is not None and line.startswith("切磋了一场（接 Alex 的切磋）：他先停手了。")


@pytest.mark.parametrize(
    "details",
    [None, {"weapon": "minecraft:iron_sword", "hits": 4}, _spar_details("cancelled"), {"spar": "坏了"}],
)
def test_no_line_when_it_was_not_a_spar_or_never_got_going(details: Optional[Dict[str, Any]]) -> None:
    assert spar_outcome_line(_run(details)) is None


def _milestones(bus: Any) -> List[str]:
    return [
        call.args[1].message
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "milestone"
    ]


async def test_the_streamer_hears_how_the_spar_went_when_it_ends() -> None:
    """游戏 Agent 下的切磋收手：发一条 milestone 告诉主播输赢；之后照常交付。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if "结束了" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "和 Alex 切磋完了"})])
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("minecraft_wait", {"reason": "切磋中"})])
        goal = {"ability": "fight", "target": {"kind": "player", "name": "Alex"}, "parameters": {"spar": True}}
        return _resp(calls=[_call("maicraft_execute", {"goal": goal})])

    agent, _llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="和 Alex 切磋一下", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1] and agent.batch_active is False)

    server.finish(1, summary="切磋结束：他到了停手线；我打中他 5 下，他打中我 3 下；我剩 9.5 点，他剩 6 点")
    server.goals[1]["result"]["details"] = _spar_details("they_at_line")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    assert _milestones(bus) == [
        "切磋了一场：他先到了停手线，算我赢了；我打中他 5 下，他打中我 3 下。切磋是玩，输赢都俏皮带过，开心优先，不提血量"
    ]
    await agent.stop()


async def test_an_ordinary_goal_ending_sends_no_spar_line() -> None:
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if "结束了" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "砍到了"})])
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather"}})])

    agent, _llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1] and agent.batch_active is False)
    server.finish(1)
    await _wait_until(lambda: len(_reports(bus)) == 1)
    assert _milestones(bus) == []
    await agent.stop()
