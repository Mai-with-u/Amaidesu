"""挂起站桩的无进展看门狗：识别位移/步数进展、尊重自设计时器、连续叫醒指数退避、文案中性不默认导向取消。

涵盖第 63 场 10 次唤醒的逐类回归：
- 长途游泳持续位移（6 次误叫根因）→ 刷新计时，不再误唤醒大模型；
- 目标自身步数/动作阶段推进 → 刷新计时，不误唤醒；
- 游戏 Agent 主动设定 maicraft:timer 未到期 → 礼让自设计时器，不提前抢叫；
- 目标连第 0 步都被生存急事堵死（如水里做船被自卫抢占）且角色在逃跑位移、挂着长计时器 → 保留首次有效唤醒；
- 跟随/搜寻在原地真停住无位移无步进 → 正常在 30 秒触发唤醒；
- 同一组目标连续无进展唤醒后继续等待 → 30s → 60s → 120s → 240s 指数退避，有进展或换目标即回 30s；
- Mod 侧 stalled 事件不误刷宿主进展钟；
- 唤醒提示词陈述等待时长与当前状态，不把取消作为默认导向。
"""

from __future__ import annotations

import pytest

from src.agents.minecraft.wait_progress import WaitProgress, watchdog_wakeup_notice
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


def _no_progress_events(bus) -> list[GamePayload]:
    return [
        call.args[1]
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "no_progress"
    ]


def _texts(llm) -> str:
    return "\n".join(str(m.get("content") or "") for m in llm.seen[-1])


def _is_watchdog_wakeup(text: str) -> bool:
    return "[系统] 挂起的目标" in text and "核对现场进展" in text


def _make_script():
    """顺序剧本：下达目标 → 让出；唤醒/摘要等分支一律回纯文本继续等待（不含工具调用）。"""
    rounds = iter(
        [
            _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})]),
            _resp("目标在后台跑，结束这一轮等通知。"),
        ]
    )

    def script(messages):
        text = str(messages[-1].get("content") or "")
        if _is_watchdog_wakeup(text):
            return _resp("目标还在正常推进，继续等。")
        if "摘要" in text or "总结" in text:
            return _resp("（无新增事实）")
        try:
            return next(rounds)
        except StopIteration:
            return _resp("继续等。")

    return script


@pytest.mark.asyncio
async def test_suspension_without_progress_wakes_with_neutral_notice_and_exponential_backoff() -> None:
    """原地停滞无位移无步进：30 秒触发唤醒并带中性核对文案；确认继续等待后退避至 60 秒再唤醒。"""
    clock = _Clock()
    server = FakeMaicraft()
    agent, llm, bus = await make_minecraft_agent(server, _make_script(), clock=clock)
    agent.receive_delegation(instruction="砍树", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: agent.batch_active is False)
    assert agent.observation()["standing"] is True
    assert agent.observation()["stall_limit_seconds"] == 30.0

    # 第一次停滞满 30 秒：唤醒模型并发布 no_progress 事件
    clock.now += 31
    await _wait_until(lambda: _is_watchdog_wakeup(_texts(llm)))
    events = _no_progress_events(bus)
    assert len(events) == 1, "无进展唤醒必须发 game.suspend.no_progress 事件（复盘哨兵数据源）"
    assert "已 31 秒没有任何进展" in events[0].message
    last_prompt = _texts(llm)
    assert "可直接结束本轮继续等待" in last_prompt
    assert "仅在确认目标确实受阻无法推进时" in last_prompt

    # 唤醒后模型确认继续等待 → 重新站桩，同目标退避上限翻倍到 60 秒
    await _wait_until(lambda: agent.batch_active is False and agent.observation()["standing"] is True)
    assert agent.observation()["stall_limit_seconds"] == 60.0

    # 再过 35 秒（超过旧的 30 秒但未满退避后的 60 秒）：不再重复唤醒
    clock.now += 35
    server._new_event.set()
    await _wait_until(lambda: agent.observation()["stalled_seconds"] >= 35.0)
    assert len(_no_progress_events(bus)) == 1

    # 补足到 61 秒：触发第二次唤醒，退避继续升到 120 秒
    clock.now += 26
    server._new_event.set()
    await _wait_until(lambda: len(_no_progress_events(bus)) == 2)
    await _wait_until(lambda: agent.batch_active is False and agent.observation()["standing"] is True)
    assert agent.observation()["stall_limit_seconds"] == 120.0
    await agent.stop()


@pytest.mark.asyncio
async def test_goal_event_progress_resets_stall_clock_and_backoff() -> None:
    """站桩期间收到目标事件（resumed）→ 进展钟与连续退避一并归零，不到上限不叫醒。"""
    clock = _Clock()
    server = FakeMaicraft()
    agent, llm, bus = await make_minecraft_agent(server, _make_script(), clock=clock)
    agent.receive_delegation(instruction="砍树", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: agent.batch_active is False)

    # 先触发一次唤醒让退避升到 60s
    clock.now += 31
    server._new_event.set()
    await _wait_until(lambda: len(_no_progress_events(bus)) == 1)
    await _wait_until(lambda: agent.batch_active is False and agent.observation()["standing"] is True)
    assert agent.observation()["stall_limit_seconds"] == 60.0

    # 20 秒时目标来一次状态推进：进展钟与退避均归回初始 30s
    clock.now += 20
    server._append("resumed", 1, "砍了 3 块")
    await _wait_until(lambda: agent.observation()["stalled_seconds"] < 5)
    assert agent.observation()["stall_limit_seconds"] == 30.0

    clock.now += 20
    server._new_event.set()
    assert len(_no_progress_events(bus)) == 1
    await agent.stop()


@pytest.mark.asyncio
async def test_swimming_position_progress_suppresses_false_wakeups_until_actually_stopped() -> None:
    """第 63 场游泳回归：角色持续位移（每 30 秒游出十余格）不触发无进展唤醒；停在原地满 30 秒才唤醒。"""
    clock = _Clock()
    server = FakeMaicraft()
    agent, _llm, bus = await make_minecraft_agent(server, _make_script(), clock=clock)
    agent.receive_delegation(instruction="游去对岸", task_id="d-swim")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: agent.batch_active is False)
    server.goals[1]["doing"] = "第 1 步：游向 (-120, 62, 80)"

    # 连续 3 段各 31 秒：角色一直在往前游，每次到阈值核对现场都会认出位移进展，0 次误叫
    for step_idx in range(1, 4):
        server.self_view["position"] = {
            "x": 3.6 - step_idx * 18.0,
            "y": 62.0,
            "z": -7.2 + step_idx * 6.0,
            "dimension": "minecraft:overworld",
        }
        clock.now += 31
        server._new_event.set()
        await _wait_until(lambda: agent.observation()["stalled_seconds"] < 5)
        assert not _no_progress_events(bus), f"游泳推进第 {step_idx} 段不应误唤醒"

    # 角色随后卡在岸边方块前不再移动：满 31 秒后如期唤醒核对
    clock.now += 31
    server._new_event.set()
    await _wait_until(lambda: len(_no_progress_events(bus)) == 1)
    await agent.stop()


@pytest.mark.asyncio
async def test_goal_step_doing_change_counts_as_progress_without_moving() -> None:
    """原地多步合成/采掘：坐标不动但目标 doing 的步数变化时算有效进展，不误叫醒。"""
    clock = _Clock()
    server = FakeMaicraft()
    agent, _llm, bus = await make_minecraft_agent(server, _make_script(), clock=clock)
    agent.receive_delegation(instruction="原地加工木板与工作台", task_id="d-step")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: agent.batch_active is False)

    server.goals[1]["doing"] = "第 2 步：合成橡木木板 ×16"
    clock.now += 31
    server._new_event.set()
    await _wait_until(lambda: agent.observation()["stalled_seconds"] < 5)
    assert not _no_progress_events(bus)

    server.goals[1]["doing"] = "第 3 步：合成工作台"
    clock.now += 31
    server._new_event.set()
    await _wait_until(lambda: agent.observation()["stalled_seconds"] < 5)
    assert not _no_progress_events(bus)

    # 停在第 3 步不再变化满 31 秒：正常唤醒
    clock.now += 31
    server._new_event.set()
    await _wait_until(lambda: len(_no_progress_events(bus)) == 1)
    await agent.stop()


@pytest.mark.asyncio
async def test_agent_own_timer_suppresses_watchdog_unless_stuck_at_reflex_step_zero() -> None:
    """Agent 自设 maicraft:timer 期间看门狗礼让不抢叫；但目标被生存急事堵死在第 0 步时照常唤醒。"""
    clock = _Clock()
    server = FakeMaicraft()
    rounds = iter(
        [
            _resp(
                calls=[
                    _call(
                        "maicraft_execute",
                        {"goal": {"ability": "timer", "parameters": {"operation": "set", "after_seconds": 120}}},
                        call_id="c-timer",
                    ),
                    _call(
                        "maicraft_execute",
                        {"goal": {"ability": "gather", "purpose": "等树苗长成顺便砍树"}},
                        call_id="c-gather",
                    ),
                ]
            ),
            _resp("挂好 120 秒闹钟了，结束这一轮等闹钟或目标完成。"),
        ]
    )

    def script(messages):
        text = str(messages[-1].get("content") or "")
        if _is_watchdog_wakeup(text):
            return _resp("收到核对提示，先继续观察。")
        try:
            return next(rounds)
        except StopIteration:
            return _resp("继续等。")

    agent, llm, bus = await make_minecraft_agent(server, script, clock=clock)
    agent.receive_delegation(instruction="等树苗长成", task_id="d-timer")
    await _wait_until(lambda: agent.tracked_goal_ids == [2])
    await _wait_until(lambda: agent.batch_active is False)
    server.goals[2]["doing"] = "第 1 步：等待区域内树木可采"

    # 自设计时器 120s 未到期：过 45s、90s 都不应抢先叫醒
    for delta in (45, 45):
        clock.now += delta
        server._new_event.set()
        await _wait_until(lambda: agent.observation()["stalled_seconds"] >= 40)
        assert not _no_progress_events(bus), "自设 timer 未到期前看门狗必须礼让"

    # 第 63 场 23:43 水中造船对偶场景：目标被生存急事堵死在第 0 步，哪怕角色在逃跑位移且挂着长计时器也必须唤醒
    server.goals[2]["doing"] = "生存需求的临时任务：自卫（第 0 步）"
    server.self_view["position"] = {"x": 25.0, "y": 62.0, "z": -20.0, "dimension": "minecraft:overworld"}
    clock.now += 5
    server._new_event.set()
    await _wait_until(lambda: len(_no_progress_events(bus)) == 1)
    assert "生存需求的临时任务：自卫（第 0 步）" in _texts(llm)
    await agent.stop()


def test_wait_progress_unit_edges_and_mod_stalled_isolation() -> None:
    """单测边界：退避封顶 240s、换目标清退避、计时器宽限过期兜底、中性文案包含目标状态。"""
    clock = _Clock()
    wp = WaitProgress(clock)
    wp.stand([10], initial_pos=("minecraft:overworld", 0.0, 64.0, 0.0), initial_doings={10: "第 1 步：寻路"})

    # 连续告警封顶：30 -> 60 -> 120 -> 240 -> 240
    expected_limits = [30.0, 60.0, 120.0, 240.0, 240.0]
    for limit in expected_limits:
        assert wp.current_limit_s == limit
        clock.now += limit + 1
        assert wp.due() is True
        wp.note_alert()
        wp.resume()
        wp.stand([10], initial_pos=("minecraft:overworld", 0.0, 64.0, 0.0), initial_doings={10: "第 1 步：寻路"})

    # 换了目标集合后退避立即恢复 30s
    wp.resume()
    wp.stand([11], initial_pos=("minecraft:overworld", 0.0, 64.0, 0.0))
    assert wp.current_limit_s == 30.0

    # 中性文案不写"卡住了就取消它"这类默认取消导向，且带上当前状态
    notice = watchdog_wakeup_notice([11], 32.0, doings={11: "第 2 步：游向海面"})
    assert "11: 第 2 步：游向海面" in notice
    assert "卡住了就取消它" not in notice
