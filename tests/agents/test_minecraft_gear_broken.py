"""装备损坏事件的路由行为：任务进行中注入模型，批次间隙经素材发布器发给主播、不再静默丢弃。"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List

from src.agents.minecraft.agent import MinecraftAgent
from src.modules.events.payloads.game import GamePayload

from .minecraft_v1_fakes import FakeMaicraft, _resp, make_minecraft_agent


def _gear_broken(message: str = "装备用坏了，铁镐坏了", cursor: int = 1) -> Dict[str, Any]:
    return {"kind": "gear_broken", "message": message, "cursor": cursor}


def _capture_injections(agent: MinecraftAgent) -> Callable[[], List[str]]:
    """返回"收通知"函数：把此刻还在队列里没被模型读到的通知出队收走，累计返回。"""
    captured: List[str] = []
    agent._task_finished = False

    def collect() -> List[str]:
        while agent.queued_messages:
            _, content = agent._wake_queue.pop()
            captured.append(content)
        return captured

    return collect


def _attention_events(bus: Any) -> List[str]:
    """素材发布器经 _emit_game_event 发出的 attention_required 消息，按发射顺序。"""
    return [
        c.args[1].message
        for c in bus.emit.await_args_list
        if isinstance(c.args[1], GamePayload) and c.args[1].event_type == "attention_required"
    ]


async def test_gear_broken_is_injected_while_a_task_is_running() -> None:
    """任务进行中装备用坏：注入模型，让模型知道工具没了、可当场换工具或换办法。"""
    agent, _llm, _bus = await make_minecraft_agent(FakeMaicraft(), lambda messages: _resp("好"), live=False)
    collect = _capture_injections(agent)

    await agent._on_body_event(_gear_broken())
    captured = collect()
    assert len(captured) == 1
    assert captured[0].startswith("[装备] 装备用坏了，铁镐坏了。")
    assert "换一件好的或换种办法" in captured[0]
    await agent.stop()


async def test_gear_broken_reaches_the_streamer_between_batches_instead_of_being_dropped() -> None:
    """批次间隙装备用坏：不再走 attention_event 静默丢弃，素材发布器收到排程、发出原话。"""
    agent, _llm, bus = await make_minecraft_agent(FakeMaicraft(), lambda messages: _resp("好"), live=False)

    await agent._on_body_event(_gear_broken())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    messages = _attention_events(bus)
    assert messages == ["装备用坏了，铁镐坏了"], "Mod 原话直通主播素材"
    assert agent.queued_messages == [], "批次间隙不注入唤醒队列"
    await agent.stop()
