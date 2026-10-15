"""ProactiveTrigger：主播主动发言的纯规则判定器（总开关、各触发源的优先级与限流豁免、记账规则）"""

from __future__ import annotations

import pytest

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


def _trigger(**config) -> ProactiveTrigger:
    return ProactiveTrigger({"enabled": True, **config})


def test_disabled_never_fires_except_for_reminders() -> None:
    disabled = ProactiveTrigger({"enabled": False})
    room = _Room(last_speech_ms=None)
    for flag in ("rundown_pending", "game_pending", "rundown_overdue"):
        assert disabled.should_trigger(room, now_ms=10_000, **{flag: True}) is None
    assert disabled.should_trigger(room, now_ms=10_000) is None
    # 运营递话催醒必达：总开关关闭 + hourly 上限已满 + 无话题，三道闸全部豁免
    gated = ProactiveTrigger({"enabled": False, "max_per_hour": 6, "topic_required": True})
    silent_topic = _Room(last_speech_ms=5_000_000, topic="")
    assert gated.should_trigger(silent_topic, now_ms=10_000_000, reminder_pending=True) == "reminder"


@pytest.mark.parametrize(("flag", "reason"), [("rundown_pending", "rundown"), ("game_pending", "game")])
def test_pending_sources_fire_immediately_and_bypass_the_three_rate_gates(flag: str, reason: str) -> None:
    trigger = _trigger(min_interval_ms=120_000, max_per_hour=6, topic_required=True)
    for i in range(6):
        trigger.record_trigger("cold", now_ms=9_000_000 + i)  # hourly 上限已满
    room = _Room(last_speech_ms=9_999_000, topic="")  # 刚说完话 + 无话题：三个拦截条件同时在场
    assert trigger.should_trigger(room, now_ms=10_000_000, **{flag: True}) == reason
    assert trigger.should_trigger(room, now_ms=10_000_000, rundown_pending=True, rundown_overdue=True) == "rundown"
    assert trigger._last_overdue_ms is None  # pending 先行返回，不消费 overdue 冷却


@pytest.mark.parametrize(
    ("config", "speech_ms", "now_ms", "topic", "expected"),
    [
        ({"min_interval_ms": 120_000}, 5_000_000, 5_030_000, "topic", None),  # 刚说完 30 秒，未达 120 秒间隔
        ({"min_interval_ms": 120_000, "topic_required": False}, 5_000_000, 5_200_000, "topic", "external"),
        ({"min_interval_ms": 0, "topic_required": True}, None, 10_000_000, "", None),  # 要求话题却没有话题
    ],
    ids=["too-soon", "after-min-interval", "no-topic"],
)
def test_external_is_highest_priority_but_obeys_interval_and_topic(config, speech_ms, now_ms, topic, expected) -> None:
    room = _Room(last_speech_ms=speech_ms, topic=topic)
    assert _trigger(**config).should_trigger(room, now_ms=now_ms, external_pending=True) == expected

    crowded = _trigger(min_interval_ms=0, cold_timeout_ms=1, topic_required=False)
    assert (
        crowded.should_trigger(_Room(None, None), now_ms=10_000_000, external_pending=True) == "external"
    )  # 优先于冷场


@pytest.mark.parametrize(
    ("now_ms", "expected"),
    [(10_005_000, "rundown"), (10_001_000, None)],  # 独立间隔 3 秒：到点触发，太近不触发
)
def test_rundown_ready_has_its_own_interval(now_ms: int, expected) -> None:
    trigger = _trigger(rundown_speech_interval_ms=3_000)
    assert trigger.should_trigger(_Room(last_speech_ms=10_000_000), now_ms=now_ms, rundown_ready=True) == expected


def test_overdue_alarm_has_double_cooldown_and_speech_debounce() -> None:
    trigger = _trigger(rundown_overdue_interval_ms=60_000)
    room = _Room(last_speech_ms=None)
    assert trigger.should_trigger(room, now_ms=10_000_000, rundown_overdue=True) == "rundown"
    assert trigger.should_trigger(room, now_ms=10_030_000, rundown_overdue=True) is None  # 冷却期内不重复提醒
    assert trigger.should_trigger(room, now_ms=10_061_000, rundown_overdue=True) == "rundown"

    debounced = _trigger(rundown_speech_interval_ms=3_000)
    recent = _Room(last_speech_ms=10_000_000)
    assert (
        debounced.should_trigger(recent, now_ms=10_001_000, rundown_overdue=True) is None
    )  # 刚发过言，即使超时也不提醒
    assert debounced.should_trigger(recent, now_ms=10_004_000, rundown_overdue=True) == "rundown"


@pytest.mark.parametrize(
    ("config", "room", "now_ms", "expected"),
    [
        (  # 冷场触发：last_message 已过 1 分钟
            {"cold_timeout_ms": 45_000, "min_interval_ms": 120_000, "topic_required": False, "schedule_interval_ms": 0},
            _Room(8_000_000, 8_000_000),
            9_000_000,
            "cold",
        ),
        ({"cold_timeout_ms": 45_000, "topic_required": True}, _Room(8_000_000, 8_000_000, topic=""), 9_000_000, None),
        (  # 定时到点且当前冷场
            {
                "schedule_interval_ms": 300_000,
                "schedule_only_cold": True,
                "topic_required": False,
                "min_interval_ms": 0,
            },
            _Room(10_000_000, 10_000_000),
            10_400_000,
            "schedule",
        ),
        (  # 定时到点但房间不冷（last_message 刚到）
            {
                "schedule_interval_ms": 300_000,
                "schedule_only_cold": True,
                "topic_required": False,
                "min_interval_ms": 0,
            },
            _Room(10_000_000, 10_390_000),
            10_400_000,
            None,
        ),
    ],
    ids=["cold", "cold-without-topic", "schedule", "schedule-room-not-cold"],
)
def test_cold_and_schedule_triggers(config, room, now_ms, expected) -> None:
    assert _trigger(**config).should_trigger(room, now_ms=now_ms) == expected


def test_reminder_is_blocked_only_by_the_anti_chain_interval() -> None:
    trigger = _trigger(min_interval_ms=120_000)
    assert trigger.should_trigger(_Room(9_999_000), now_ms=10_000_000, reminder_pending=True) is None  # 防接龙窗口内
    assert trigger.should_trigger(_Room(5_000_000), now_ms=10_000_000, reminder_pending=True) == "reminder"
    assert trigger.should_trigger(_Room(None), now_ms=10_000, reminder_pending=True) == "reminder"  # 从未发言恒通过


def test_content_driven_sources_do_not_use_up_the_hourly_budget() -> None:
    """rundown/game/reminder 本就绕过 max_per_hour；照常记账的话，话痨期几十个流程单窗会把额度记满，冷场/定时此后一小时内全被扣死。"""
    for reason in ("rundown", "game", "reminder"):
        trigger = _trigger(max_per_hour=6)
        for i in range(5):
            trigger.record_trigger(reason, now_ms=10_000_000 + i * 1000)
        assert (
            len(trigger._trigger_history) == 0 and trigger._last_trigger_ms == 10_004_000
        )  # 上次触发时刻仍要更新（schedule 依赖它）
    for reason in ("external", "schedule", "cold"):
        trigger = _trigger(max_per_hour=6)
        trigger.record_trigger(reason, now_ms=10_000_000)
        assert len(trigger._trigger_history) == 1  # 受限源照旧记账

    busy = _trigger(
        cold_timeout_ms=45_000, min_interval_ms=120_000, max_per_hour=6, topic_required=False, schedule_interval_ms=0
    )
    for i in range(6):
        busy.record_trigger("rundown", now_ms=10_000_000 + i * 60_000)
    room = _Room(last_speech_ms=10_300_000, last_message_ms=10_300_000)
    assert (
        busy.should_trigger(room, now_ms=10_500_000) == "cold"
    )  # 若 rundown 照旧记账，hourly 上限已满，这里会是 None（场次 21 教训）
