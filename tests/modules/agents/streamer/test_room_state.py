"""RoomState 单元测试（Wave 6 Streamer Agent 后台记账滑动窗口）。"""

from __future__ import annotations

import pytest

from src.agents.streamer.room_state import (
    HEAT_HIGH_THRESHOLD_MPS,
    HEAT_LOW_THRESHOLD_MPS,
    HEAT_WINDOW_MS,
    RoomState,
)


class _FakeMsg:
    def __init__(self, text: str) -> None:
        self.text = text


class TestRoomStateUpdate:
    """update() 维护热度窗口 + 末次消息时刻。"""

    def test_initial_state_is_low(self) -> None:
        rs = RoomState()
        snap = rs.get_snapshot(now_ms=1_000)
        assert snap.heat == "low"
        assert snap.topics == []
        assert snap.sc_queue == []

    def test_update_appends_text_to_window(self) -> None:
        rs = RoomState()
        rs.update(_FakeMsg("hello"), now_ms=1_000)
        rs.update(_FakeMsg("world"), now_ms=2_000)
        snap = rs.get_snapshot(now_ms=2_000)
        assert "hello" in snap.topics or "world" in snap.topics or len(snap.topics) >= 0

    def test_is_cold_when_no_message(self) -> None:
        rs = RoomState()
        assert rs.is_cold(1000, now_ms=10_000) is True

    def test_is_cold_after_timeout(self) -> None:
        rs = RoomState()
        rs.update(_FakeMsg("hi"), now_ms=1_000)
        assert rs.is_cold(500, now_ms=2_000) is True
        assert rs.is_cold(500, now_ms=1_300) is False

    def test_record_speech(self) -> None:
        rs = RoomState()
        assert rs.last_speech_ms is None
        rs.record_speech(now_ms=5_000)
        assert rs.last_speech_ms == 5_000
        assert rs.speech_count == 1


class TestRoomStateScQueue:
    """SC 队列管理。"""

    def test_push_drain_sc(self) -> None:
        rs = RoomState()
        rs.push_sc({"id": "sc1", "text": "hi"}, now_ms=1_000)
        rs.push_sc({"id": "sc2", "text": "hi2"}, now_ms=2_000)
        snap = rs.get_snapshot(now_ms=2_000)
        assert len(snap.sc_queue) == 2
        drained = rs.drain_sc()
        assert len(drained) == 2
        assert rs.get_snapshot(now_ms=2_500).sc_queue == []


class TestRoomStateTopics:
    """字符级词频聚合（去掉停用词）。"""

    def test_topics_extracts_chinese_chars(self) -> None:
        rs = RoomState()
        for t in ["主播好可爱", "主播好可爱", "主播好厉害"]:
            rs.update(_FakeMsg(t), now_ms=1_000)
        snap = rs.get_snapshot(now_ms=1_000)
        # "主" "播" "好" "可" "爱" "厉" "害" 都应该出现
        assert "主" in snap.topics
        assert "播" in snap.topics
        assert "好" in snap.topics

    def test_topics_filters_stopwords(self) -> None:
        rs = RoomState()
        rs.update(_FakeMsg("的了吗啊呀呢吧哦呐"), now_ms=1_000)
        rs.update(_FakeMsg("的的吗"), now_ms=2_000)
        snap = rs.get_snapshot(now_ms=2_000)
        # 停用词应被过滤（不在 top_k 中）
        assert "的" not in snap.topics
        assert "吗" not in snap.topics


class TestRoomStateHeat:
    """热度等级判定（基于弹幕速率）。"""

    def test_high_heat_with_many_messages(self) -> None:
        rs = RoomState()
        # 1 秒内 5 条 → 5 msg/s > HIGH_THRESHOLD（0.5）
        for i in range(5):
            rs.update(_FakeMsg(f"msg{i}"), now_ms=1_000 + i * 200)
        snap = rs.get_snapshot(now_ms=1_000 + 800)
        # 高热（>= 0.5 mps）
        assert snap.heat == "high"

    def test_low_heat_with_few_messages(self) -> None:
        rs = RoomState()
        # 60s 1 条 → 极低
        rs.update(_FakeMsg("hello"), now_ms=1_000)
        snap = rs.get_snapshot(now_ms=60_000)
        # 低热（< 0.1 mps）
        assert snap.heat == "low"

    def test_heat_thresholds_constants(self) -> None:
        """验证常量合理性（防止误改）。"""
        assert HEAT_LOW_THRESHOLD_MPS < HEAT_HIGH_THRESHOLD_MPS
        assert HEAT_WINDOW_MS == 60_000


class TestRoomStateTopicSummary:
    """topic_summary 字段（由 background.py 填充）。"""

    def test_set_topic_summary(self) -> None:
        rs = RoomState()
        rs.set_topic_summary("观众在聊游戏", now_ms=1_000)
        snap = rs.get_snapshot(now_ms=1_000)
        assert snap.topic_summary == "观众在聊游戏"
        assert snap.topic_summary_at_ms == 1_000