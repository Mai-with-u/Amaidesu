"""一次性主动触发信号的消费纪律：确认消费 vs 读即消费 vs 直播闸。"""

from __future__ import annotations

from src.agents.streamer.proactive_signals import ProactiveSignals
from src.agents.streamer.proactive_trigger import ProactiveTrigger


class _Room:
    """房间状态替身：last_message_ms 为 None 视为一直冷场。"""

    def __init__(self, last_speech_ms, last_message_ms=None, topic: str = "topic"):
        self.last_speech_ms = last_speech_ms
        self.last_message_ms = last_message_ms
        self._snapshot = {"topic_summary": topic}

    def is_cold(self, cold_timeout_ms, *, now_ms):
        return True if self.last_message_ms is None else (now_ms - self.last_message_ms) > cold_timeout_ms

    def get_snapshot(self, *, now_ms):
        return self._snapshot


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


# ---------------------------------------------------------------------------
# evaluate_tick：每 tick 判定序（读信号 → 折参 → 判定 → 确认消费）单点验证
# ---------------------------------------------------------------------------


def _evaluate(signals: ProactiveSignals, room: _Room, now: int, *, live: bool = True, **kwargs):
    """以默认最小配置构造触发器，跑一次完整 tick 判定。"""
    trigger = ProactiveTrigger(
        {
            "enabled": True,
            "schedule_interval_ms": 0,  # 关定时触发，聚焦信号路径
            "min_interval_ms": 120_000,
            "rundown_ready_interval_ms": 30_000,
            "rundown_overdue_interval_ms": 300_000,
            "rundown_speech_interval_ms": 3_000,
        }
    )
    return signals.evaluate_tick(
        live=live,
        trigger=trigger,
        room_state=room,
        now_ms=now,
        rundown_overdue=kwargs.get("rundown_overdue", False),
        rundown_ready=kwargs.get("rundown_ready", False),
        reminder_pending=kwargs.get("reminder_pending", False),
    )


def test_evaluate_tick_offline_gates_rundown_coord_states() -> None:
    """下播时调用方传入的流程单协调器状态（超时/供能中）被强制 False 化：
    未开播不说开场白/推进环节，判定不触发；一次性信号也不被消费。"""
    signals = ProactiveSignals()
    room = _Room(last_speech_ms=None, last_message_ms=9_999_000)  # 不冷场，排除 schedule/cold 干扰

    assert _evaluate(signals, room, 10_000, live=False, rundown_overdue=True, rundown_ready=True) is None, (
        "下播 tick 超时/就绪状态不得绕过开播闸"
    )
    assert not signals.game_pending and not signals.rundown_pending and not signals.external_pending


def test_evaluate_tick_offline_preserves_pending_until_live() -> None:
    """下播 tick 判定后 pending 信号保留，开播后首个 tick 补触发。"""
    signals = ProactiveSignals()
    signals.set_rundown()
    room = _Room(last_speech_ms=None, last_message_ms=9_999_000)

    assert _evaluate(signals, room, 10_000, live=False) is None
    assert signals.rundown_pending, "下播不消费"
    assert _evaluate(signals, room, 20_000, live=True) == "rundown"
    assert not signals.rundown_pending, "开播后确认消费"


def test_evaluate_tick_confirms_on_trigger_only_and_keeps_pending_on_preempt() -> None:
    """confirm 只在 reason 命中时消费（evaluate_tick 内部回写）：被 reminder
    抢先的 tick 里 game 信号保留，条件恢复后补触发——调用方无需再调 confirm。"""
    signals = ProactiveSignals()
    signals.set_game()
    room = _Room(last_speech_ms=None, last_message_ms=9_999_000)  # reminder 通道畅通

    assert _evaluate(signals, room, 10_000, reminder_pending=True) == "reminder"
    assert signals.game_pending, "被 reminder 抢先的 tick 里 pending 保留"

    assert _evaluate(signals, room, 20_000, reminder_pending=False) == "game"
    assert not signals.game_pending


def test_evaluate_tick_reads_once_per_tick_and_external_consumed_even_when_blocked() -> None:
    """每 tick 恰好一次读数：external 读即消费，即使本 tick 被公共前置拦下。"""
    signals = ProactiveSignals()
    signals.set_external()
    room = _Room(last_speech_ms=10_000, last_message_ms=10_000, topic="")  # 刚发言 + 无话题：全前置拦

    assert _evaluate(signals, room, 10_000) is None
    assert not signals.external_pending, "尽力而为：条件不满足即弃"
    assert signals.read_for_tick(live=True).external_pending is False
