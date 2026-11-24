"""无玩家引导期自驱节奏控制（原地内耗逃逸阀）与长搜寻止损提示单元测试及集成回归测试。"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.agents.minecraft.solo_pacing import (
    DEFAULT_COOLDOWN_S,
    DEFAULT_SEARCH_STOP_LOSS_S,
    DEFAULT_SOLO_IDLE_WINDOW_S,
    SoloPacingTracker,
    build_search_stop_loss_hint,
    is_churn_or_maintenance_goal,
    is_search_like_ability,
)
from src.modules.agents.lead import LeadHandoverContext
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatPayload
from src.modules.llm.payload import Response
from tests.agents.minecraft_v1_fakes import (
    FakeMaicraft,
    ManualClock,
    _call,
    _resp,
    _texts,
    _wait_until,
    make_minecraft_agent,
    offline_config,
)


async def _instant_flush_wait(_ms: int) -> None:
    """聊天合批窗口零延迟替身：立即让出调度，不引入真实等待。"""
    return None


# ======================================================================
# 1. 纯函数与 SoloPacingTracker 单元测试
# ======================================================================


@pytest.mark.parametrize(
    ("ability", "purpose", "status", "expected"),
    [
        ("maicraft:gather", "砍树收集橡木", "done", False),
        ("maicraft:build", "搭建风车底座", "done", False),
        ("maicraft:obtain", "寻找红石矿", "failed", True),
        ("maicraft:obtain", "寻找红石矿", "cancelled", True),
        ("maicraft:gather", "挖铁矿", "partial", True),
        ("maicraft:sort_inventory", "整理物品", "done", True),
        ("maicraft:deposit", "把多余圆石存箱", "done", True),
        ("maicraft:use", "清包整理背包并修补镐子", "done", True),
    ],
)
def test_is_churn_or_maintenance_goal_classification(ability: str, purpose: str, status: str, expected: bool) -> None:
    """正确区分正常探索建造推进与失败/取消/清包修补类维护操作。"""
    assert is_churn_or_maintenance_goal(ability, purpose, status) is expected


@pytest.mark.parametrize(
    ("ability", "expected"),
    [
        ("obtain", True),
        ("maicraft:obtain", True),
        ("find", True),
        ("maicraft:gather", True),
        ("maicraft:explore", True),
        ("build", False),
        ("maicraft:chat", False),
        ("maicraft:craft", False),
    ],
)
def test_is_search_like_ability_recognition(ability: str, expected: bool) -> None:
    """识别带或不带命名空间前缀的搜寻/获取类能力。"""
    assert is_search_like_ability(ability) is expected


def test_build_search_stop_loss_hint_respects_threshold() -> None:
    """搜寻类目标未满 120 秒不出止损提示；达到或超过 120 秒产出开放式止损建议。"""
    assert (
        build_search_stop_loss_hint(
            search_labels=["maicraft:obtain（寻找绿宝石）"],
            max_elapsed_s=DEFAULT_SEARCH_STOP_LOSS_S - 1.0,
            stalled_s=30.0,
        )
        == ""
    )
    hint = build_search_stop_loss_hint(
        search_labels=["maicraft:obtain（寻找绿宝石）"],
        max_elapsed_s=DEFAULT_SEARCH_STOP_LOSS_S,
        stalled_s=35.0,
    )
    assert "maicraft:obtain（寻找绿宝石）" in hint
    assert "取消" in hint and "生态" in hint and "途径" in hint


def test_solo_pacing_triggers_on_small_radius_respects_cooldown_and_resets_on_expansion_or_interaction() -> None:
    """连续 15 分钟无外部引导且活动局限在 32 格内触发软提醒；受 15 分钟冷却约束；大幅位移或外部互动后重置。"""
    clock = ManualClock(1000.0)
    tracker = SoloPacingTracker(clock)
    assert tracker.maybe_build_nudge() is None

    tracker.enter_lead()
    tracker.note_position(("minecraft:overworld", 10.0, 64.0, 20.0))

    # 仅过 10 分钟：未满 15 分钟，不触发
    clock.advance(600.0)
    tracker.note_position(("minecraft:overworld", 22.0, 64.0, 25.0))
    assert tracker.maybe_build_nudge() is None

    # 满 15 分钟且仍在 32 格小范围内：触发开放式换口味软提醒
    clock.advance(300.0)
    first = tracker.maybe_build_nudge()
    assert first is not None
    assert first.startswith("[节奏提醒]")
    assert "FTB" in first and "联动机器" in first

    # 冷却期（< 15 分钟）内不再重复触发
    clock.advance(DEFAULT_COOLDOWN_S - 10.0)
    assert tracker.maybe_build_nudge() is None

    # 角色向外跑出 > 32 格：视为位置大幅拓展，自动重置无引导计时
    clock.advance(20.0)
    tracker.note_position(("minecraft:overworld", 80.0, 64.0, 20.0))
    assert tracker.maybe_build_nudge() is None

    # 需在新位置重新无引导满 15 分钟才再次触发
    clock.advance(DEFAULT_SOLO_IDLE_WINDOW_S - 1.0)
    assert tracker.maybe_build_nudge() is None
    clock.advance(1.0)
    second = tracker.maybe_build_nudge()
    assert second is not None and second.startswith("[节奏提醒]")

    # 收到外部互动（游戏公屏/观众建议/运营提醒）后重置计时与样本
    clock.advance(DEFAULT_COOLDOWN_S)
    tracker.note_external_interaction()
    tracker.note_position(("minecraft:overworld", 82.0, 64.0, 21.0))
    assert tracker.maybe_build_nudge() is None


def test_solo_pacing_triggers_on_high_churn_ratio_even_without_positions() -> None:
    """无位置样本时，连续 15 分钟无引导且近期失败/维护占比 >= 40%（样本数 >= 6）同样触发软提醒。"""
    clock = ManualClock(5000.0)
    tracker = SoloPacingTracker(clock)
    tracker.enter_lead()

    # 仅 5 个样本（低于最少 6 个门槛）：即便全失败也不触发
    for gid in range(1, 6):
        tracker.note_goal_finished(gid, "obtain", "找钻石", "failed")
    clock.advance(DEFAULT_SOLO_IDLE_WINDOW_S)
    assert tracker.maybe_build_nudge() is None

    # 补齐 6 个样本，其中重复 goal_id=1 应自动去重，不重复计数
    tracker.note_goal_finished(1, "obtain", "找钻石", "failed")
    assert tracker.maybe_build_nudge() is None
    tracker.note_goal_finished(6, "build", "建房", "done")
    nudge = tracker.maybe_build_nudge()
    assert nudge is not None
    assert "失败、取消或维护类杂活" in nudge


def test_search_stop_loss_hint_lists_only_overdue_search_goals_when_concurrent() -> None:
    """多搜寻目标并跑时，仅把自身已达止损阈值的旧目标写入提示，不误把刚启动的新目标标为达阈值。"""
    clock = ManualClock(6000.0)
    tracker = SoloPacingTracker(clock)

    # 先启动旧搜寻目标（obtain），145 秒后再启动新搜寻目标（gather）
    tracker.note_goal_started(1, "maicraft:obtain", "寻找绿宝石")
    clock.advance(145.0)
    tracker.note_goal_started(2, "maicraft:gather", "采集周边橡木")
    clock.advance(5.0)

    # 此时目标 1 已耗时 150s（>= 120s），目标 2 仅耗时 5s（< 120s）
    hint = tracker.search_stop_loss_hint([1, 2], stalled_s=35.0)
    assert "maicraft:obtain（寻找绿宝石）" in hint
    assert "maicraft:gather" not in hint
    assert "采集周边橡木" not in hint

    # 若仅剩刚启动 5 秒的新搜寻目标与已跑 200 秒的非搜寻建造目标并跑，不误对新搜寻目标出止损提示
    tracker.forget_goal(1)
    tracker.note_goal_started(3, "maicraft:build", "建造仓库")
    assert tracker.search_stop_loss_hint([2, 3], stalled_s=130.0) == ""


# ======================================================================
# 2. Agent 端到端集成回归：原地内耗提醒与长搜寻 120s 止损提示
# ======================================================================


async def test_agent_emits_solo_pacing_nudge_after_15m_idle_and_resets_on_suggestion() -> None:
    """主控期连续 15 分钟无外部引导且在小范围活动时，事件页巡检推送 [节奏提醒]；收到观众建议与玩家游戏公屏均重置计时。"""
    clock = ManualClock(2000.0)
    bus = EventBus(enable_stats=False)
    server = FakeMaicraft()
    obs_seq = [0]

    def script(messages: List[Dict[str, Any]]) -> Response:
        last_role = str(messages[-1].get("role") or "")
        last_text = str(messages[-1].get("content") or "")
        # 开局、收到直播间建议或收到玩家游戏公屏后各采一次自身坐标，确保小范围位置样本始终存在，
        # 从而严格检验“是否满 15 分钟无引导”而非因位置样本被清空而放空
        if last_role == "user" and (obs_seq[0] == 0 or "[直播间建议]" in last_text or "一起探险" in last_text):
            obs_seq[0] += 1
            return _resp("先看下自己位置", calls=[_call("maicraft_observe", {"what": "self"}, f"obs-{obs_seq[0]}")])
        return _resp("知道了，先等新事件")

    agent, llm, _ = await make_minecraft_agent(
        server,
        script,
        bus=bus,
        live=True,
        clock=clock,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=0,
        chat_flush_wait=_instant_flush_wait,
        config=offline_config(events_wait_ms=1000),
    )
    try:
        assert (
            await agent.enter_lead(
                LeadHandoverContext(segment_id="seg-free", segment_title="自由探索", segment_goal="自主游玩")
            )
            is True
        )
        await _wait_until(lambda: len(llm.seen) >= 2 and not agent.batch_active)
        calls_before = len(llm.seen)

        # 假时钟前进 15 分钟且无任何外部引导：事件页到来后自动投递 [节奏提醒] 唤醒推理
        clock.advance(DEFAULT_SOLO_IDLE_WINDOW_S + 5.0)
        server.body_event("sky_changed", "午后微风")
        await _wait_until(lambda: len(llm.seen) > calls_before and not agent.batch_active)
        baseline_nudges = _texts(llm.seen[-1]).count("[节奏提醒]")
        assert baseline_nudges == 1

        # 先推进 10 分钟再发观众建议：使后续再过 10 分钟时总跨度（20 分钟）已超过 15 分钟冷却，
        # 仅靠“建议重置了 15 分钟无引导计时”拦截新一轮 [节奏提醒]
        clock.advance(600.0)
        calls_after_nudge = len(llm.seen)
        assert await agent.receive_suggestion("去东边看看河谷地形") is True
        await _wait_until(lambda: len(llm.seen) > calls_after_nudge and not agent.batch_active)
        calls_after_sug = len(llm.seen)

        # 建议后推进 10 分钟（< 15 分钟）：真实触发事件页巡检并发送玩家游戏公屏唤醒推理，
        # 在非空调用列表上断言没有新增 [节奏提醒]
        clock.advance(600.0)
        events_before_poll = len(server.calls_to("events"))
        server.body_event("sky_changed", "傍晚微风")
        await _wait_until(lambda: len(server.calls_to("events")) > events_before_poll)
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(game="minecraft", kind="player", sender="同伴甲", sender_distance=8.0, content="一起探险"),
            source="test",
        )
        await bus.wait_until_drained()
        await _wait_until(lambda: len(llm.seen) > calls_after_sug and not agent.batch_active)
        recent_calls = llm.seen[calls_after_sug:]
        assert recent_calls
        assert "一起探险" in _texts(recent_calls[-1])
        assert all(_texts(messages).count("[节奏提醒]") == baseline_nudges for messages in recent_calls)

        # 玩家游戏公屏同样重置了 15 分钟无引导计时：再过 10 分钟触发事件页巡检仍不推新提醒，
        # 继续推进至满 15 分钟后再次触发第 2 条 [节奏提醒]
        calls_after_chat = len(llm.seen)
        clock.advance(600.0)
        events_before_mid_poll = len(server.calls_to("events"))
        server.body_event("sky_changed", "夜色渐浓")
        await _wait_until(lambda: len(server.calls_to("events")) > events_before_mid_poll)
        assert len(llm.seen) == calls_after_chat

        clock.advance(310.0)
        server.body_event("sky_changed", "星光闪烁")
        await _wait_until(lambda: len(llm.seen) > calls_after_chat and not agent.batch_active)
        assert _texts(llm.seen[-1]).count("[节奏提醒]") == baseline_nudges + 1
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_watchdog_appends_stop_loss_hint_for_long_search_goal_over_120s() -> None:
    """搜寻/获取类目标（obtain）总耗时超 120 秒触发无进展唤醒时，附带开放式止损建议；非搜寻类不附带。"""
    clock = ManualClock(3000.0)
    server = FakeMaicraft()
    rounds = iter(
        [
            _resp(
                "去找远处的萤石粉",
                calls=[_call("maicraft_execute", {"goal": {"ability": "obtain", "purpose": "搜寻萤石"}})],
            ),
            _resp("先在后台跑着，等待结果"),
        ]
    )

    def script(messages: List[Dict[str, Any]]) -> Response:
        text = str(messages[-1].get("content") or "")
        if "[系统] 挂起的目标" in text:
            return _resp("收到核对提醒，继续观察")
        try:
            return next(rounds)
        except StopIteration:
            return _resp("继续等")

    agent, llm, _bus = await make_minecraft_agent(server, script, clock=clock)
    try:
        agent.receive_delegation(instruction="去搜寻萤石", task_id="d-search")
        await _wait_until(lambda: agent.tracked_goal_ids == [1] and not agent.batch_active)

        # 31 秒第一次唤醒（< 120 秒）：仅给中性核对提示，不附止损建议
        clock.advance(31.0)
        server.wake_events()
        await _wait_until(lambda: "[系统] 挂起的目标" in _texts(llm.seen[-1]) and not agent.batch_active)
        first_wakeup = _texts(llm.seen[-1])
        assert "核对现场进展" in first_wakeup
        assert "搜寻/获取类目标" not in first_wakeup

        # 目标继续空耗到总耗时超过 120 秒（再推进 95 秒，超过退避后的 60s 上限）：唤醒附带开放式止损建议
        calls_before_second = len(llm.seen)
        clock.advance(95.0)
        server.wake_events()
        await _wait_until(lambda: len(llm.seen) > calls_before_second and not agent.batch_active)
        second_wakeup = _texts(llm.seen[-1])
        assert "核对现场进展" in second_wakeup
        assert "搜寻/获取类目标（maicraft:obtain（搜寻萤石））" in second_wakeup
        assert "改换生态群系" in second_wakeup
    finally:
        await agent.stop()


async def test_watchdog_does_not_append_search_stop_loss_for_non_search_goal() -> None:
    """非搜寻类长目标（如 build）即使总耗时超 120 秒触发无进展唤醒，也不误附搜寻止损建议。"""
    clock = ManualClock(4000.0)
    server = FakeMaicraft()
    rounds = iter(
        [
            _resp(
                "开始施工",
                calls=[_call("maicraft_execute", {"goal": {"ability": "build", "purpose": "建造主厂房"}})],
            ),
            _resp("施工中，等待完成"),
        ]
    )

    def script(_messages: List[Dict[str, Any]]) -> Response:
        try:
            return next(rounds)
        except StopIteration:
            return _resp("继续等")

    agent, llm, _bus = await make_minecraft_agent(server, script, clock=clock)
    try:
        agent.receive_delegation(instruction="建造主厂房", task_id="d-build")
        await _wait_until(lambda: agent.tracked_goal_ids == [1] and not agent.batch_active)

        clock.advance(130.0)
        server.wake_events()
        await _wait_until(lambda: "[系统] 挂起的目标" in _texts(llm.seen[-1]) and not agent.batch_active)
        wakeup_text = _texts(llm.seen[-1])
        assert "核对现场进展" in wakeup_text
        assert "搜寻/获取类目标" not in wakeup_text
    finally:
        await agent.stop()
