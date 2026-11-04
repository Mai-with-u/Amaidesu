"""本文件测 MinecraftAgent 的急停与急取消主题：撤单先经宿主直连通道让游戏立刻停手、急停注入叫停理由（可选连委派单一起撤）。"""

from __future__ import annotations

from typing import List

from src.agents.minecraft.maicraft import goal_run_of
from src.modules.llm.payload import Response

from .minecraft_v1_fakes import (
    FakeMaicraft,
    _resp,
    make_minecraft_agent,
)


def _script_good(messages: List) -> Response:
    return _resp("好")


async def test_cancel_task_now_stops_tracked_goals_at_the_mod_first() -> None:
    """急取消：逐个向游戏直连发目标取消（Mod 立刻停手），随后账面写 cancelled + 注入停手通知。"""
    server = FakeMaicraft()
    agent, _llm, _bus = await make_minecraft_agent(server, _script_good)
    for goal_id in (1, 2):
        server.goals[goal_id] = {"goal_id": goal_id, "ability": "maicraft:gather", "state": "running"}
        run = goal_run_of(server.goals[goal_id])
        assert run is not None
        agent._track_goal(run)
    agent._delegations.absorb("deleg_1")

    assert await agent.cancel_task_now("deleg_1", source="operator") is True

    cancels = server.calls_to("goal")
    assert {"operation": "cancel", "goal_id": 1} in cancels
    assert {"operation": "cancel", "goal_id": 2} in cancels
    await agent.stop()


async def test_cancel_task_now_tolerates_single_goal_failure() -> None:
    """单个目标直连取消失败（游戏侧目标已不在）只跳过，不阻断其余目标、不抛异常。"""
    server = FakeMaicraft()
    agent, _llm, _bus = await make_minecraft_agent(server, _script_good)
    for goal_id in (1, 2):
        run = goal_run_of({"goal_id": goal_id, "ability": "maicraft:gather", "state": "running"})
        assert run is not None
        agent._track_goal(run)
    agent._delegations.absorb("deleg_1")

    assert await agent.cancel_task_now("deleg_1", source="operator") is True
    assert len(server.calls_to("goal")) == 2, "两个目标都尝试过直连取消"
    await agent.stop()


async def test_stop_actions_injects_reason_and_keeps_delegations_by_default() -> None:
    """急停：直连取消所有在跑目标 + 注入带理由原文的叫停通知；默认不动委派单。"""
    server = FakeMaicraft()
    agent, _llm, _bus = await make_minecraft_agent(server, _script_good)
    server.goals[7] = {"goal_id": 7, "ability": "maicraft:gather", "state": "running"}
    run = goal_run_of(server.goals[7])
    assert run is not None
    agent._track_goal(run)
    agent._delegations.absorb("deleg_9")

    assert await agent.stop_actions("别再挖了，来门口接待") is True

    assert {"operation": "cancel", "goal_id": 7} in server.calls_to("goal")
    contents = [item[1] for item in agent._wake_queue if isinstance(item, tuple)]
    assert any("操作员叫停了当前动作" in content and "别再挖了，来门口接待" in content for content in contents)
    assert agent._delegations.tracked_ids() == ("deleg_9",), "默认不撤委派单"
    await agent.stop()


async def test_stop_actions_without_reason_uses_plain_notice() -> None:
    """理由为空时注入不含理由段的叫停通知。"""
    server = FakeMaicraft()
    agent, _llm, _bus = await make_minecraft_agent(server, _script_good)

    assert await agent.stop_actions() is True
    contents = [item[1] for item in agent._wake_queue if isinstance(item, tuple)]
    assert any(content == "[系统] 操作员叫停了当前动作。" for content in contents)
    await agent.stop()


async def test_stop_actions_with_cancel_delegations_revokes_tracked_delegations() -> None:
    """急停带撤单开关：名下委派单一并撤（账面 cancelled + 出追踪清单）。"""
    server = FakeMaicraft()
    agent, _llm, _bus = await make_minecraft_agent(server, _script_good)
    agent._delegations.absorb("deleg_9")

    assert await agent.stop_actions("", cancel_delegations=True) is True

    assert agent._delegations.tracked_ids() == ()
    await agent.stop()
