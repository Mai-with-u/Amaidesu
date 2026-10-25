"""一次性主动触发信号的消费纪律：确认消费 vs 读即消费 vs 直播闸。"""

from __future__ import annotations

from src.agents.streamer.proactive_signals import ProactiveSignals


def test_game_and_rundown_confirm_consumed_only_on_matching_reason() -> None:
    """game / rundown 确认消费：reason 命中才清零；被抢先（None / 其他
    reason）时保留，条件重新满足时补触发。"""
    signals = ProactiveSignals()
    signals.set_game()
    signals.set_rundown()

    tick = signals.read_for_tick(live=True)
    assert (tick.game_pending, tick.rundown_pending) is not None
    signals.confirm(None, tick)  # 总开关关闭 / 公共前置拦下：不清
    assert signals.game_pending and signals.rundown_pending

    tick = signals.read_for_tick(live=True)
    signals.confirm("reminder", tick)  # 被 reminder 抢先：不清
    assert signals.game_pending and signals.rundown_pending

    tick = signals.read_for_tick(live=True)
    signals.confirm("game", tick)  # 只有 game 引发了决策
    assert not signals.game_pending and signals.rundown_pending

    tick = signals.read_for_tick(live=True)
    signals.confirm("rundown", tick)  # rundown 随后也引发了决策
    assert not signals.rundown_pending


def test_external_is_consumed_on_read_best_effort() -> None:
    """external 读即消费：本 tick 条件不满足即弃，不补偿重试。"""
    signals = ProactiveSignals()
    signals.set_external()

    tick = signals.read_for_tick(live=True)
    assert tick.external_pending is True
    assert signals.external_pending is False, "读数即消费，与决策结果无关"

    # 下一 tick 读数已是 False——不因上一 tick 未触发而保留
    assert signals.read_for_tick(live=True).external_pending is False


def test_offline_read_is_blank_and_consumes_nothing() -> None:
    """下播读数全 False 且不消费：信号保留到开播后首个 tick 生效。"""
    signals = ProactiveSignals()
    signals.set_game()
    signals.set_rundown()
    signals.set_external()

    tick = signals.read_for_tick(live=False)
    assert (tick.external_pending, tick.rundown_pending, tick.game_pending) == (False, False, False)
    assert signals.game_pending and signals.rundown_pending and signals.external_pending

    tick = signals.read_for_tick(live=True)  # 开播后首个 tick
    assert (tick.external_pending, tick.rundown_pending, tick.game_pending) == (True, True, True)


def test_rundown_reason_from_timeout_does_not_clear_pending_signal() -> None:
    """rundown reason 可能来自超时/就绪分支：本 tick 没读到 rundown 信号时
    不清它（信号在场时优先级保证必然走信号分支，不会混淆）。"""
    signals = ProactiveSignals()
    signals.set_rundown()

    tick = signals.read_for_tick(live=True)
    assert tick.rundown_pending  # 本 tick 有信号，正常确认
    signals.confirm("rundown", tick)
    assert not signals.rundown_pending

    # 信号不在场的 tick，超时分支返回 "rundown" 也不影响（无事可清）
    tick = signals.read_for_tick(live=True)
    assert not tick.rundown_pending
    signals.confirm("rundown", tick)
