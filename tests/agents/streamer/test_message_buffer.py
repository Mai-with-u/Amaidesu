"""MessageBuffer（Streamer Agent 内部批状态）：add/drain、强制与批满与窗口到期的触发判定、idle 补偿公式、批文本渲染

idle 补偿公式：idle_equivalent = min(idle_ms / avg_interval_ms, batch_max_size - 1)，equivalent = 实际条数 + idle_equivalent。
"""

from __future__ import annotations

import pytest

from src.agents.streamer.message_buffer import MessageBuffer
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser


def _msg(text: str = "hi", *, message_type: str = "danmaku") -> RoomMessagePayload:
    return RoomMessagePayload(message_type=message_type, user=RoomMessageUser(id="u_观众", name="观众"), content=text)


def test_add_force_flag_and_drain_resets_everything() -> None:
    buf = MessageBuffer()
    assert buf.is_empty is True and buf.size == 0 and buf.force is False
    buf.add(_msg("a"), arrival_ms=1_000, forced=False)
    buf.add(_msg("sc"), arrival_ms=1_500, forced=True)
    assert buf.size == 2 and buf.force is True

    assert [m.content for m in buf.drain()] == ["a", "sc"]
    assert buf.is_empty is True and buf.force is False  # drain 后清零


@pytest.mark.parametrize(
    ("config", "adds", "now_ms", "avg", "expected"),
    [
        ({"batch_window_ms": 3_000}, [(1_000, True)], 1_500, None, (True, "forced")),
        (
            {"batch_window_ms": 3_000, "batch_max_size": 3},
            [(1_000, False), (1_100, False), (1_200, False)],
            1_300,
            None,
            (True, "batch_full"),
        ),
        ({"batch_window_ms": 3_000}, [(1_000, False)], 1_500, None, (False, "window_not_expired")),
        (
            {"batch_window_ms": 3_000, "enable_idle_compensation": False},
            [(1_000, False)],
            5_000,
            None,
            (True, "window_expired"),
        ),
        (
            {"batch_window_ms": 3_000, "enable_idle_compensation": True},
            [(1_000, False)],
            5_000,
            None,
            (True, "window_expired"),
        ),  # 无均值：退化触发
        # 窗口到期但折算后 1 + min(1000/1000, 9) = 2 < 10：继续等
        (
            {"batch_window_ms": 1_000, "batch_max_size": 10, "enable_idle_compensation": True},
            [(1_000, False)],
            2_000,
            1_000,
            (False, "waiting_idle"),
        ),
        # 1 + min(9000/1000, 4) = 5 >= 5：补偿触发
        (
            {"batch_window_ms": 1_000, "batch_max_size": 5, "enable_idle_compensation": True},
            [(1_000, False)],
            10_000,
            1_000,
            (True, "idle_compensation"),
        ),
        # 折算封顶 batch_max_size-1，防止空批次触发：1 + min(99, 2) = 3 >= 3
        (
            {"batch_window_ms": 1_000, "batch_max_size": 3, "enable_idle_compensation": True},
            [(1_000, False)],
            100_000,
            1_000,
            (True, "idle_compensation"),
        ),
    ],
    ids=[
        "forced",
        "batch-full",
        "window-not-expired",
        "expired-no-compensation",
        "expired-no-average",
        "waiting-idle",
        "idle-threshold",
        "idle-capped",
    ],
)
def test_should_flush_decisions(config, adds, now_ms, avg, expected) -> None:
    buf = MessageBuffer(**config)
    for arrival_ms, forced in adds:
        buf.add(_msg(), arrival_ms=arrival_ms, forced=forced)
    flush, reason = buf.should_flush(now_ms, avg_interval_ms=avg)
    assert flush is expected[0] and expected[1] in reason


def test_render_batch_text() -> None:
    assert MessageBuffer.render_batch_text([]) == ""
    text = MessageBuffer.render_batch_text([_msg("hello"), _msg("world", message_type="super_chat")])
    assert "hello" in text and "world" in text
