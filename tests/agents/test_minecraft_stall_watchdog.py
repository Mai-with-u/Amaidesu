"""挂起站桩的无进展看门狗：让出后超上限（30 秒）没有任何目标进展 → 叫醒重新评估。

红绿回归的"红"：看门狗接线删掉后，本文件的用例必然失败（挂起永不超时唤醒，
长挂站桩 125 分钟的旧病复发无报警）。
"""

from __future__ import annotations

import pytest

from src.modules.events.payloads.game import GamePayload
from tests.agents.minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _resp,
    _wait_until,
    make_minecraft_agent,
)


class _Clock:
    """注入假时钟（秒），测试直接拨针。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _reports(bus) -> list:
    return [
        call.args[1]
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "no_progress"
    ]


def _texts(llm) -> str:
    return "\n".join(str(m.get("content") or "") for m in llm.seen[-1])


def _make_script():
    """顺序剧本：下达目标 → 让出；唤醒/摘要等分支一律回纯文本（不含工具调用）。"""
    rounds = iter(
        [
            _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})]),
            _resp("目标在后台跑，结束这一轮等通知。"),
        ]
    )

    def script(messages):
        text = str(messages[-1].get("content") or "")
        if "重新评估" in text or "没有任何进展" in text:
            # 看门狗唤醒：确认目标还在正常推进 → 结束这一轮继续等（最小开销动作）
            return _resp("目标还在正常推进，继续等。")
        if "摘要" in text or "总结" in text:
            return _resp("（无新增事实）")
        try:
            return next(rounds)
        except StopIteration:
            return _resp("继续等。")

    return script


@pytest.mark.asyncio
async def test_suspension_without_progress_wakes_model_after_limit() -> None:
    """让出 30 秒无任何目标进展 → 注入重新评估唤醒 + 发 no_progress 事件。"""
    clock = _Clock()
    agent, llm, bus = await make_minecraft_agent(FakeMaicraft(), _make_script(), clock=clock)
    agent.receive_delegation(instruction="砍树", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    # 模型让出（无工具调用结束这一轮）→ 宿主挂起等目标事件
    await _wait_until(lambda: agent.batch_active is False)
    assert agent._wait_progress.standing

    # 拨针过上限：30 秒无进展，看门狗必须叫醒（GoalWatch 事件页轮询会带着假时钟走检查）
    clock.now += 31
    await _wait_until(lambda: "[系统] 挂起的目标" in _texts(llm))
    assert _reports(bus), "无进展唤醒必须发 game.suspend.no_progress 事件（复盘哨兵数据源）"

    # 唤醒后 Agent 确认继续（无工具结束轮）→ 重新挂起、重新计时
    await _wait_until(lambda: agent.batch_active is False)
    assert agent._wait_progress.standing
    clock.now += 10
    # 同一段站桩刚被叫醒过，10 秒内不重复叫醒
    count_before = len(_reports(bus))
    await _wait_until(lambda: agent.batch_active is False)
    assert len(_reports(bus)) == count_before
    await agent.stop()


@pytest.mark.asyncio
async def test_goal_progress_resets_the_stall_clock() -> None:
    """站桩期间目标有状态变化（进展）→ 计时重置，不到上限不叫醒。"""
    clock = _Clock()
    server = FakeMaicraft()
    agent, llm, bus = await make_minecraft_agent(server, _make_script(), clock=clock)
    agent.receive_delegation(instruction="砍树", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: agent.batch_active is False)

    # 20 秒（不到上限）时目标来一次状态变化（进展）→ 计时重置
    clock.now += 20
    server._append("resumed", 1, "砍了 3 块")
    await _wait_until(lambda: agent._wait_progress.stalled_seconds() < 5)
    clock.now += 20
    # 若计时未重置，此刻已超 30 秒；有进展重置后不应叫醒
    assert not _reports(bus)
    await agent.stop()
