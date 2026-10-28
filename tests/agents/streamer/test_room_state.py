"""RoomState：热度滑动窗口、冷场判定、字符级话题聚合（去停用词）、话题摘要消费、观看人数推送"""

from __future__ import annotations

from src.agents.streamer.room_state import HEAT_HIGH_THRESHOLD_MPS, HEAT_LOW_THRESHOLD_MPS, HEAT_WINDOW_MS, RoomState


class _Msg:
    def __init__(self, text: str) -> None:
        self.content = text


def test_initial_state_cold_detection_speech_and_message_counting() -> None:
    room = RoomState()
    snap = room.get_snapshot(now_ms=1_000)
    assert snap.heat == "low" and snap.topics == [] and room.is_cold(1000, now_ms=10_000) is True  # 没有消息一直冷场
    assert room.last_speech_ms is None
    room.record_speech(now_ms=5_000)
    assert room.last_speech_ms == 5_000

    room.update(_Msg("hi"), now_ms=1_000)
    assert room.is_cold(500, now_ms=2_000) is True and room.is_cold(500, now_ms=1_300) is False
    room.update(_Msg("hi again"), now_ms=HEAT_WINDOW_MS + 10_000)
    assert room.total_message_count == 2  # 累计计数不随窗口裁剪回退


def test_topics_are_char_frequencies_without_stopwords() -> None:
    room = RoomState()
    for text in ("主播好可爱", "主播好可爱", "主播好厉害"):
        room.update(_Msg(text), now_ms=1_000)
    assert {"主", "播", "好"} <= set(room.get_snapshot(now_ms=1_000).topics)

    stop = RoomState()
    stop.update(_Msg("的了吗啊呀呢吧哦呐"), now_ms=1_000)
    stop.update(_Msg("的的吗"), now_ms=2_000)
    assert not {"的", "吗"} & set(stop.get_snapshot(now_ms=2_000).topics)  # 停用词不进 top_k


def test_heat_follows_message_rate_inside_the_sliding_window() -> None:
    assert HEAT_LOW_THRESHOLD_MPS < HEAT_HIGH_THRESHOLD_MPS and HEAT_WINDOW_MS == 60_000  # 防误改

    busy = RoomState()
    for i in range(5):  # 1 秒内 5 条 → 远超高热阈值
        busy.update(_Msg(f"msg{i}"), now_ms=1_000 + i * 200)
    assert busy.get_snapshot(now_ms=1_800).heat == "high"

    quiet = RoomState()
    quiet.update(_Msg("hello"), now_ms=1_000)
    assert quiet.get_snapshot(now_ms=60_000).heat == "low"  # 60 秒 1 条，低于低热阈值


def test_topic_summary_is_consumed_once_until_the_background_writes_a_new_one() -> None:
    room = RoomState()
    room.set_topic_summary("观众在聊游戏", now_ms=1_000)
    snap = room.get_snapshot(now_ms=1_000)
    assert (snap.topic_summary, snap.topic_summary_at_ms) == ("观众在聊游戏", 1_000)

    assert room.consume_topic_summary() == "观众在聊游戏"  # 决策面取走即清空，同一段摘要不在多轮请求里重复注入
    assert room.consume_topic_summary() == ""
    snap = room.get_snapshot(now_ms=2_000)
    assert (snap.topic_summary, snap.topic_summary_at_ms) == ("", 0)


def test_audience_total_is_zero_until_pushed_and_the_latest_push_wins() -> None:
    room = RoomState()
    snap = room.get_snapshot(now_ms=1_000)
    assert (snap.audience_total, snap.audience_total_at_ms) == (0, 0)  # 语义是"未知"而非"没人看"

    room.set_audience_total(100, now_ms=1_000)
    room.set_audience_total(150, now_ms=2_000)
    snap = room.get_snapshot(now_ms=3_000)
    assert (snap.audience_total, snap.audience_total_at_ms) == (150, 2_000)  # 变化即推，最后一次推送即当前状态
