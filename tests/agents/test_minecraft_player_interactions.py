"""别的玩家和角色的互动：肢体动作、切磋读成结构化的玩家互动，只给存储记账。

覆盖：
- 带玩家 UUID 的肢体动作记一次打招呼（连同做了哪几种动作），朝她扔东西的另记送东西（那半句话）
- 没带 UUID 的认不出是谁，不记
- 切磋先认结果里 Mod 给的对手 UUID（按观察编号点的也认得出），旧版 Mod 不带时退回下令时按名字点的对手；
  收手时按怎么收的手记谁赢、各打中几下；没打起来的不记
- 采集器发完叙事再发互动；互动事件不进主播时间线、不促发决策
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from src.agents.minecraft.maicraft import GoalRun
from src.agents.minecraft.player_interactions import (
    SelfChatLog,
    SparOpponents,
    body_language_interactions,
    spar_interaction,
)
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameSelfChattedPayload
from src.modules.events.payloads.game_player import GamePlayerInteractedPayload
from tests.agents.streamer.helpers import make_streamer_agent

_ALEX = {"name": "Alex", "uuid": "0f0e0d0c-1111-2222-3333-444455556666", "id": "e4"}


def _body(message: str, player: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    event: Dict[str, Any] = {"kind": "player_body_language", "message": message}
    if player is not None:
        event["player"] = player
    return event


def test_body_language_is_a_greeting_with_the_actions_seen() -> None:
    message = "玩家 Alex（e4）看着你蹲起 4 下，拿出铁剑，对着你挥了 2 下；他在你前方 3 格"
    (greet,) = body_language_interactions(_body(message, _ALEX), save_id="w1", save_name="生存服")
    assert (greet.kind, greet.player_uuid, greet.player_name) == ("greet", _ALEX["uuid"], "Alex")
    assert greet.actions == ["蹲起", "挥手", "拿出东西"]
    assert (greet.save_id, greet.save_name, greet.fact) == ("w1", "生存服", {})


def test_tossing_something_to_her_is_also_a_gift() -> None:
    message = "玩家 Alex（e4）朝你扔了 3 个面包（e9），落在你左侧 1 格；他在你左前方 2 格"
    greet, gift = body_language_interactions(_body(message, _ALEX))
    assert greet.actions == ["扔东西"]
    assert (gift.kind, gift.fact) == ("gift", {"said": "朝我扔了 3 个面包"})


def test_without_a_uuid_nobody_is_recorded() -> None:
    assert body_language_interactions(_body("玩家 Alex 看着你蹲起 2 下；他在你前方 3 格")) == []
    assert body_language_interactions(_body("玩家 Alex 看着你蹲起 2 下", {"name": "Alex"})) == []


def _run(goal_id: int, details: Optional[Dict[str, Any]], ability: str = "fight", state: str = "finished") -> GoalRun:
    result = {"status": "done", "summary": "结束了", "details": details}
    return GoalRun(
        goal_id=goal_id, ability=ability, purpose="", state=state, question=None, result=result, doing="", data={}
    )


def test_her_public_chat_is_taken_once_when_it_went_out() -> None:
    log = SelfChatLog()
    sent = _run(1, {"message": "来就来", "submitted": True}, ability="maicraft:chat")
    assert log.take(sent) == "来就来"
    assert log.take(sent) == "", "回复和事件流两头都看到结束：只取一次"


@pytest.mark.parametrize(
    "run",
    [
        _run(2, {"message": "来就来", "submitted": False}, ability="chat"),
        _run(3, {"message": "过来", "submitted": True, "to": "e5", "delivered": True}, ability="chat"),
        _run(4, {"message": "/time set day", "submitted": True}, ability="chat"),
        _run(5, {"message": "来就来", "submitted": True}, ability="fight"),
        _run(6, {"message": "来就来", "submitted": True}, ability="chat", state="running"),
    ],
    ids=["没交出去", "对生物说的", "游戏命令", "不是说话", "还没结束"],
)
def test_lines_that_did_not_go_to_public_chat_are_not_taken(run: GoalRun) -> None:
    assert SelfChatLog().take(run) == ""


def _spar_goal(target: Dict[str, Any], spar: Any = True) -> Dict[str, Any]:
    return {"goal": {"ability": "fight", "target": target, "parameters": {"spar": spar}}}


def test_only_spars_against_a_named_player_remember_the_opponent() -> None:
    opponents = SparOpponents()
    opponents.note(1, _spar_goal({"kind": "player", "name": "Alex"}))
    opponents.note(2, _spar_goal({"kind": "seen", "id": "e4"}))
    opponents.note(3, _spar_goal({"kind": "player", "name": "Steve"}, spar=False))
    opponents.note(4, {"goal": {"ability": "mine", "target": {"kind": "player", "name": "Alex"}}})
    assert [opponents.pop(goal_id) for goal_id in (1, 2, 3, 4)] == ["Alex", "", "", ""]
    assert opponents.pop(1) == "", "收手后就忘掉"


@pytest.mark.parametrize(
    ("ended_by", "result"),
    [("they_at_line", "won"), ("finishing_blow", "won"), ("me_at_line", "lost"), ("they_left", "even")],
)
def test_a_finished_spar_records_who_won_and_the_score(ended_by: str, result: str) -> None:
    details = {"hits": 5, "spar": {"ended_by": ended_by, "hits_taken": 3}}
    interaction = spar_interaction(_run(1, details), "Alex", save_id="w1")
    assert interaction is not None
    assert (interaction.kind, interaction.player_name, interaction.player_uuid) == ("spar", "Alex", "")
    assert interaction.fact == {"result": result, "my_hits": 5, "their_hits": 3}
    assert interaction.save_id == "w1"


def test_the_opponent_in_the_result_wins_over_the_name_she_called() -> None:
    """结果里带了 Mod 认出的对手：按他的 UUID 记，名字也用结果里的；按观察编号点的（没记名字）同样记。"""
    spar = {"ended_by": "me_at_line", "hits_taken": 4, "opponent": {"name": "Alex", "uuid": _ALEX["uuid"]}}
    for named in ("Alex_old", ""):
        interaction = spar_interaction(_run(1, {"hits": 2, "spar": spar}), named)
        assert interaction is not None
        assert (interaction.player_uuid, interaction.player_name) == (_ALEX["uuid"], "Alex")
        assert interaction.fact == {"result": "lost", "my_hits": 2, "their_hits": 4}


def test_an_opponent_without_a_uuid_falls_back_to_the_name_she_called() -> None:
    spar = {"ended_by": "they_left", "opponent": {"name": "Alex"}}
    interaction = spar_interaction(_run(1, {"spar": spar}), "Alex")
    assert interaction is not None and (interaction.player_uuid, interaction.player_name) == ("", "Alex")
    assert spar_interaction(_run(1, {"spar": spar}), "") is None, "没 UUID 也没点名：不知道和谁打的"


def test_no_spar_record_without_an_opponent_or_a_fight() -> None:
    details = {"hits": 0, "spar": {"ended_by": "cancelled"}}
    assert spar_interaction(_run(1, details), "Alex") is None
    assert spar_interaction(_run(1, {"spar": {"ended_by": "they_at_line"}}), "") is None
    assert spar_interaction(_run(1, None), "Alex") is None


async def test_interactions_never_reach_her_timeline_or_wake_her() -> None:
    """玩家互动只给存储记账：主播不订阅，不进时间线、不促发决策（同一个动作她已从叙事那条知道了）。"""
    bus = EventBus()
    agent = make_streamer_agent({"proactive": {"enabled": True}}, event_bus=bus)
    agent._subscribe_events()
    agent._live_active = True
    try:
        for kind in ("greet", "gift", "spar"):
            payload = GamePlayerInteractedPayload(
                game="minecraft", kind=kind, player_uuid=_ALEX["uuid"], player_name="Alex"
            )
            await bus.emit(CoreEvents.GAME_PLAYER_INTERACTED, payload, source="TestGameAgent")
        # 她自己在公屏打出去的话同样只落库：不当成别人的聊天回到她的时间线
        said = GameSelfChattedPayload(game="minecraft", message="来就来")
        await bus.emit(CoreEvents.GAME_SELF_CHATTED, said, source="TestGameAgent")
        await bus.wait_until_drained()

        assert agent._proactive_signals.game_pending is False
        materials = await agent._scope.open_window()
        assert materials.timeline_notes == [] and materials.game_batch.events == ()
    finally:
        await bus.cleanup()
