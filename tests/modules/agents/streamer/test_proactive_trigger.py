"""ProactiveTrigger 单元测试（Wave 6 Streamer Agent 主动发言纯规则判定）。

迁移自 ``tests/stages/decision/test_proactive_trigger.py``，verbatim 保留核心逻辑。
"""

from __future__ import annotations

from src.agents.streamer.proactive_trigger import ProactiveTrigger


class _MockRoomState:
    def __init__(self, last_speech_ms, last_message_ms=None):
        self.last_speech_ms = last_speech_ms
        self.last_message_ms = last_message_ms
        self._snapshot = {"topic_summary": "topic"}

    def is_cold(self, cold_timeout_ms, *, now_ms):
        if self.last_message_ms is None:
            return True
        return (now_ms - self.last_message_ms) > cold_timeout_ms

    def get_snapshot(self, *, now_ms):
        return self._snapshot


class TestProactiveTriggerDisabled:
    """总开关 disabled 时永不触发。"""

    def test_disabled_returns_none(self):
        t = ProactiveTrigger({"enabled": False})
        room = _MockRoomState(last_speech_ms=None)
        assert t.should_trigger(room, now_ms=10_000) is None


class TestProactiveTriggerOutlinePending:
    """agenda_pending 立即触发 agenda（仅受总开关约束）。"""

    def test_agenda_pending_always_fires(self):
        t = ProactiveTrigger({"enabled": True})
        room = _MockRoomState(last_speech_ms=9_999)  # 刚说过话
        reason = t.should_trigger(room, now_ms=10_000, agenda_pending=True)
        assert reason == "agenda"

    def test_agenda_pending_respects_disabled(self):
        t = ProactiveTrigger({"enabled": False})
        room = _MockRoomState(last_speech_ms=None)
        reason = t.should_trigger(room, now_ms=10_000, agenda_pending=True)
        assert reason is None


class TestProactiveTriggerExternal:
    """external_pending 最高优先级（受 min_interval / max_per_hour / topic 约束）。"""

    def test_external_too_soon_returns_none(self):
        t = ProactiveTrigger(
            {
                "enabled": True,
                "min_interval_ms": 120_000,
            }
        )
        room = _MockRoomState(last_speech_ms=5_000_000)
        reason = t.should_trigger(room, now_ms=5_030_000, external_pending=True)
        assert reason is None  # 刚说完话 30s，未达 120s 间隔

    def test_external_after_min_interval_fires(self):
        t = ProactiveTrigger(
            {
                "enabled": True,
                "min_interval_ms": 120_000,
                "topic_required": False,
            }
        )
        room = _MockRoomState(last_speech_ms=5_000_000)
        reason = t.should_trigger(room, now_ms=5_200_000, external_pending=True)
        assert reason == "external"

    def test_external_no_topic_returns_none(self):
        t = ProactiveTrigger(
            {
                "enabled": True,
                "min_interval_ms": 0,
                "topic_required": True,
            }
        )
        room = _MockRoomState(last_speech_ms=None)
        room._snapshot = {"topic_summary": ""}
        reason = t.should_trigger(room, now_ms=10_000_000, external_pending=True)
        assert reason is None


class TestProactiveTriggerOutlineReady:
    """agenda_ready 绕过 min_interval/max_per_hour/topic_required，但有独立间隔。"""

    def test_agenda_ready_after_interval_fires(self):
        t = ProactiveTrigger(
            {
                "enabled": True,
                "agenda_speech_interval_ms": 3_000,
            }
        )
        room = _MockRoomState(last_speech_ms=10_000_000)
        reason = t.should_trigger(room, now_ms=10_005_000, agenda_ready=True)
        assert reason == "agenda"

    def test_agenda_ready_too_soon_returns_none(self):
        t = ProactiveTrigger(
            {
                "enabled": True,
                "agenda_speech_interval_ms": 3_000,
            }
        )
        room = _MockRoomState(last_speech_ms=10_000_000)
        reason = t.should_trigger(room, now_ms=10_001_000, agenda_ready=True)
        assert reason is None


class TestProactiveTriggerCold:
    """cold 触发：房间处于冷场状态。"""

    def test_cold_fires_when_room_cold(self):
        t = ProactiveTrigger(
            {
                "enabled": True,
                "cold_timeout_ms": 45_000,
                "min_interval_ms": 120_000,
                "topic_required": False,
                "schedule_interval_ms": 0,
            }
        )
        room = _MockRoomState(last_speech_ms=8_000_000, last_message_ms=8_000_000)
        # now = 9_000_000 时 last_message 已过 1m，触发冷场
        reason = t.should_trigger(room, now_ms=9_000_000)
        assert reason == "cold"

    def test_cold_no_topic_returns_none(self):
        t = ProactiveTrigger(
            {
                "enabled": True,
                "cold_timeout_ms": 45_000,
                "topic_required": True,
            }
        )
        room = _MockRoomState(last_speech_ms=8_000_000, last_message_ms=8_000_000)
        room._snapshot = {"topic_summary": ""}
        reason = t.should_trigger(room, now_ms=9_000_000)
        assert reason is None


class TestProactiveTriggerSchedule:
    """schedule 触发：定时间隔到点 + 当前冷场。"""

    def test_schedule_interval_reached_and_cold_fires(self):
        t = ProactiveTrigger(
            {
                "enabled": True,
                "schedule_interval_ms": 300_000,
                "schedule_only_cold": True,
                "topic_required": False,
                "min_interval_ms": 0,
            }
        )
        room = _MockRoomState(last_speech_ms=10_000_000, last_message_ms=10_000_000)
        # now = 10_400_000 → 距 last_trigger 400s > 300s；last_message 已过 400s 冷场
        reason = t.should_trigger(room, now_ms=10_400_000)
        assert reason == "schedule"

    def test_schedule_not_cold_returns_none(self):
        t = ProactiveTrigger(
            {
                "enabled": True,
                "schedule_interval_ms": 300_000,
                "schedule_only_cold": True,
                "topic_required": False,
                "min_interval_ms": 0,
            }
        )
        # last_message 刚到（不冷场）
        room = _MockRoomState(last_speech_ms=10_000_000, last_message_ms=10_390_000)
        reason = t.should_trigger(room, now_ms=10_400_000)
        assert reason is None


class TestProactiveTriggerRecord:
    """record_trigger 更新内部状态。"""

    def test_record_trigger_updates_state(self):
        t = ProactiveTrigger({"enabled": True, "max_per_hour": 10})
        t.record_trigger("cold", now_ms=10_000_000)
        # record 后内部状态更新（hourly 历史追加）
        # 调用 should_trigger 时会按 max_per_hour 限制
        # 简单验证：record_trigger 不抛异常
        assert t._last_trigger_ms == 10_000_000

    def test_record_trigger_multiple(self):
        t = ProactiveTrigger({"enabled": True, "max_per_hour": 10})
        for i in range(3):
            t.record_trigger("cold", now_ms=10_000_000 + i * 1000)
        assert len(t._trigger_history) == 3


class TestProactiveTriggerPriority:
    """优先级：external > agenda > schedule > cold。"""

    def test_external_outranks_cold(self):
        """当 external_pending=True + 房间冷场时，external 优先。"""
        t = ProactiveTrigger(
            {
                "enabled": True,
                "min_interval_ms": 0,
                "cold_timeout_ms": 1,
                "topic_required": False,
            }
        )
        room = _MockRoomState(last_speech_ms=None, last_message_ms=None)
        reason = t.should_trigger(room, now_ms=10_000_000, external_pending=True)
        assert reason == "external"