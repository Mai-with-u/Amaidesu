"""本文件测 MinecraftAgent 的重复通知去重主题：同样的没吃上说明只指回前面那份，历史被整理后重新完整展示。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List


from src.agents.minecraft.agent import MinecraftAgent

from .minecraft_v1_fakes import (
    FakeMaicraft,
    _resp,
    make_minecraft_agent,
)


# 饿了没吃上：每个来源没弄到的原因逐个列出，真实说明常有几千字
_HUNGRY_FAILURE = (
    "饿了但没吃上：还要 8 个能吃的（饿了，弄点吃的），问过各来源都拿不到；箱子：附近没有；"
    "打猎：附近没有动物；合成：身上没有材料；先回去继续干活，过一分钟再找吃的"
)


def _capture_injections(agent: MinecraftAgent) -> Callable[[], List[str]]:
    """返回"收通知"函数：把此刻还在队列里没被模型读到的通知出队收走，累计返回。

    通知交给模型的公开出口就是消息队列：出队即视为模型已读，之后去重判定看到的
    历史/队列状态由测试显式摆放（写进历史或放回队列）。任务置为进行中通知才会注入
    （开批判定收在 Agent 内，无公开入口——这处私有写保留，理由见测试报告登记）。
    """
    captured: List[str] = []
    agent._task_finished = False

    def collect() -> List[str]:
        while agent.queued_messages:
            _, content = agent._wake_queue.pop()
            captured.append(content)
        return captured

    return collect


def _need_unhandled(message: str, cursor: int) -> Dict[str, Any]:
    return {"kind": "need_unhandled", "message": message, "cursor": cursor}


async def test_a_repeated_need_unhandled_points_back_while_the_first_copy_is_in_history() -> None:
    """同样的没吃上说明再来一遍，前面那份还在历史里：只留一句指回去，不再整段抄。"""
    agent, _llm, _bus = await make_minecraft_agent(FakeMaicraft(), lambda messages: _resp("好"), live=False)
    collect = _capture_injections(agent)

    await agent._on_body_event(_need_unhandled(_HUNGRY_FAILURE, 1))
    captured = collect()
    assert len(captured) == 1 and "箱子：附近没有" in captured[0], "第一次完整展示"

    # 第一份已经交给模型读过：它在历史里
    agent._messages.append({"role": "user", "content": captured[0]})
    await agent._on_body_event(_need_unhandled(_HUNGRY_FAILURE, 2))
    captured = collect()

    assert len(captured) == 2
    assert "箱子：附近没有" not in captured[1], captured[1]
    assert "饿了但没吃上" in captured[1] and "没有新变化" in captured[1], captured[1]
    await agent.stop()


async def test_a_repeated_need_unhandled_points_back_while_the_first_copy_is_still_queued() -> None:
    """前面那份还在排队、模型没读到：一样不重抄，两条一起读到时第二条指回第一条。"""
    agent, _llm, _bus = await make_minecraft_agent(FakeMaicraft(), lambda messages: _resp("好"), live=False)
    collect = _capture_injections(agent)

    await agent._on_body_event(_need_unhandled(_HUNGRY_FAILURE, 1))
    first = collect()[0]
    agent._wake_queue.push_notice(first)  # 首份还没被读到：放回队列（私有访问保留，理由见测试报告登记）
    await agent._on_body_event(_need_unhandled(_HUNGRY_FAILURE, 2))
    captured = collect()

    # 重复的那条排在末位（放回队列的首份被再次收走，是同一份）
    assert "箱子：附近没有" not in captured[-1], captured[-1]
    await agent.stop()


async def test_a_repeated_need_unhandled_is_shown_in_full_once_the_first_copy_is_compacted_away() -> None:
    """历史整理把前面那份换成了摘要：指过去没有意义，重新完整展示。"""
    agent, _llm, _bus = await make_minecraft_agent(FakeMaicraft(), lambda messages: _resp("好"), live=False)
    collect = _capture_injections(agent)

    await agent._on_body_event(_need_unhandled(_HUNGRY_FAILURE, 1))
    first = collect()[0]
    agent._messages[:] = [
        {"role": "system", "content": "系统说明"},
        {"role": "user", "content": "[历史推理摘要·第 1 段，不能覆盖原始要求]\n饿了没吃上，箱子和野外都没有吃的"},
    ]
    await agent._on_body_event(_need_unhandled(_HUNGRY_FAILURE, 2))
    captured = collect()

    assert captured[1] == first, "前面那份已不在历史里，要完整再给一次"
    await agent.stop()


async def test_a_different_need_unhandled_and_other_body_notices_are_always_shown_in_full() -> None:
    """说明变了就是新事实；角色死亡这类通知每次都是新的，不套用指回前面那条。"""
    agent, _llm, _bus = await make_minecraft_agent(FakeMaicraft(), lambda messages: _resp("好"), live=False)
    collect = _capture_injections(agent)

    await agent._on_body_event(_need_unhandled(_HUNGRY_FAILURE, 1))
    captured = collect()
    agent._messages.append({"role": "user", "content": captured[0]})
    changed = _HUNGRY_FAILURE.replace("附近没有动物", "附近有牛但够不着")
    await agent._on_body_event(_need_unhandled(changed, 2))
    captured = collect()
    assert "够不着" in captured[1], "说明变了：完整展示"

    died = {"kind": "character_died", "message": "角色死了：在 (1, 64, 2)", "cursor": 3}
    await agent._on_body_event(died)
    captured = collect()
    agent._messages.append({"role": "user", "content": captured[2]})
    await agent._on_body_event(died)
    captured = collect()
    assert captured[3] == captured[2], "死亡通知不去重"
    await agent.stop()
