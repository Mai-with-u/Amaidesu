"""本文件测 MinecraftAgent 的目标暂停通知主题：暂停原因的取用优先级与重进世界分流。"""

from __future__ import annotations

import time
from typing import Any, Dict, List


from src.agents.minecraft.goal_ledger import pause_notice
from src.agents.minecraft.maicraft import goal_run_of
from src.modules.llm.payload import Response

from .minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _resp,
    _texts,
    _wait_until,
    make_minecraft_agent,
)


_ESCAPED = "已撤离，甩开了追兵（血快见底，先撤离保命），离打点 31 格；主任务停在半路等下一步指示"


def _pause_script(messages: List[Dict[str, Any]]) -> Response:
    """下达砍树后让出等待；收到暂停通知就上报主播停下，不在被暂停的目标上反复等。"""
    if "被暂停了" in _texts(messages):
        return _resp(calls=[_call("minecraft_report", {"kind": "escalation", "content": "主任务停在半路"})])
    if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
        return _resp(calls=[_call("minecraft_wait", {"reason": "等砍树"})])
    return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])


async def _paused_after(server: FakeMaicraft, doing: str, *, clock: Any = None) -> str:
    """砍树途中撤离：Mod 先发急事收尾，再把主任务挂成暂停；返回游戏 Agent 收到的暂停通知所在的那次推理。"""
    agent, llm, _bus = await make_minecraft_agent(server, _pause_script, clock=clock)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: agent.batch_active is False and len(llm.seen) == 2)

    server.body_event("temporary_task_finished", _ESCAPED)
    server.goals[1]["doing"] = doing
    server.pause(1)
    await _wait_until(lambda: any("被暂停了" in _texts(seen) for seen in llm.seen))
    await agent.stop()
    return next(_texts(seen) for seen in llm.seen if "被暂停了" in _texts(seen))


async def test_a_pause_after_escaping_carries_the_urgent_event_instead_of_a_reconnect_story() -> None:
    """10-15 直播：撤离后主任务被挂暂停，提示写死"重启游戏或重进世界后……"，模型四次以为游戏重连。
    Mod 没写暂停原因时，暂停通知带上暂停前那条急事收尾的话，不提重启和重进世界。"""
    woken = await _paused_after(FakeMaicraft(), "目标 maicraft:gather 已暂停（第 0 步）")

    assert "目标 1（maicraft:gather，砍树）被暂停了" in woken
    assert f"暂停前身体刚处理完一件急事：{_ESCAPED}" in woken
    assert "重进" not in woken and "重启" not in woken


async def test_a_pause_notice_judges_freshness_by_the_injected_clock() -> None:
    """急事是否新鲜由注入时钟判定：急事与暂停两刻都取自注入时钟，相隔超过新鲜期后急事不再拿来解释暂停。

    假时钟每被读一次前进 120 秒（超过 60 秒新鲜期）：急事收尾时读一次、暂停通知时再读一次，
    两刻必然相隔 120 秒——若时间戳直调系统单调时钟，两刻间隔近零、急事恒算新鲜，此断言即失败。
    """

    def fake_clock() -> float:
        now[0] += 120.0
        return now[0]

    now = [880.0]  # 首次读取返回 1000.0，第二次返回 1120.0
    server = FakeMaicraft()
    agent, llm, _bus = await make_minecraft_agent(server, _pause_script, clock=fake_clock)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: agent.batch_active is False and len(llm.seen) == 2)

    server.body_event("temporary_task_finished", _ESCAPED)
    server.goals[1]["doing"] = "目标 maicraft:gather 已暂停（第 0 步）"
    server.pause(1)
    await _wait_until(lambda: any("被暂停了" in _texts(seen) for seen in llm.seen))
    await agent.stop()
    woken = next(_texts(seen) for seen in llm.seen if "被暂停了" in _texts(seen))

    assert "Mod 没写暂停原因" in woken
    assert "暂停前身体刚处理完一件急事" not in woken


async def test_a_pause_reason_written_by_the_mod_comes_first() -> None:
    """Mod 在 doing 里写了暂停原因就照抄它，不再拿身体急事兜底。"""
    doing = "目标 maicraft:gather 已暂停（第 0 步）：撤离后离打点 31 格，主任务停在半路"
    woken = await _paused_after(FakeMaicraft(), doing)

    assert f"Mod 说：{doing}" in woken
    assert "暂停前身体刚处理完" not in woken


def test_only_a_changed_event_stream_is_called_reentering_the_world() -> None:
    """事件流换了编号后查到的暂停才说重进世界；隔久了的急事不拿来解释这次暂停。

    暂停通知是纯函数：急事收尾那条话由调用方（Agent 的暂停回调）取用一次即弃，
    用过即清是调用方的取值形态，这里只验通知文案对"新急事/隔久了的急事"的分流。
    """
    run = goal_run_of(
        {"goal_id": 3, "ability": "maicraft:build", "state": "paused", "doing": "目标 maicraft:build 已暂停（第 2 步）"}
    )
    assert run is not None

    reentered = pause_notice(run, True, (_ESCAPED, time.monotonic()), time.monotonic())
    assert "游戏重进了世界或重启过" in reentered and "已撤离" not in reentered
    # 恢复结论前先核对工作笔记：东西可能已放置或已消耗，"背包里没有"不等于丢了。
    assert "minecraft_get_work_log" in reentered and "背包里没有不等于丢了" in reentered

    stale = pause_notice(run, False, (_ESCAPED, time.monotonic() - 120), time.monotonic())
    assert "Mod 没写暂停原因" in stale and "已撤离" not in stale and "重进" not in stale
