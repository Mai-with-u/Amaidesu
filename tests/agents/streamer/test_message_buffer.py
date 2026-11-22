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
            {
                "batch_window_ms": 1_000,
                "batch_max_size": 3,
                "enable_idle_compensation": True,
                "batch_max_wait_ms": 1_000_000,
            },
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


def test_max_wait_forces_flush_when_idle_compensation_keeps_waiting() -> None:
    """平均间隔很大时补偿折算不足，首条到达满 batch_max_wait_ms 仍强制放行。"""
    buf = MessageBuffer(batch_window_ms=3_000, batch_max_size=20, batch_max_wait_ms=30_000)
    buf.add(_msg("a"), arrival_ms=1_000)
    buf.add(_msg("b"), arrival_ms=20_000)
    flushed, reason = buf.should_flush(30_000, avg_interval_ms=60_000.0)
    assert flushed is False and reason.startswith("waiting_idle")
    assert buf.should_flush(31_000, avg_interval_ms=60_000.0) == (True, "window_expired")


def test_max_wait_not_reached_keeps_compensation_behavior() -> None:
    buf = MessageBuffer(batch_window_ms=3_000, batch_max_size=5, batch_max_wait_ms=30_000)
    buf.add(_msg("a"), arrival_ms=1_000)
    assert buf.should_flush(5_000, avg_interval_ms=1_000.0) == (True, "idle_compensation")
    assert buf.should_flush(4_100, avg_interval_ms=10_000.0)[0] is False


def test_max_wait_without_compensation_releases_at_window() -> None:
    """关闭补偿时窗口到期即放行，早于封顶；封顶不改变该路径。"""
    buf = MessageBuffer(batch_window_ms=3_000, enable_idle_compensation=False, batch_max_wait_ms=30_000)
    buf.add(_msg("a"), arrival_ms=1_000)
    assert buf.should_flush(4_000) == (True, "window_expired")
    assert buf.should_flush(40_000) == (True, "window_expired")


def test_quiet_window_fast_flushes_single_danmaku_and_merges_burst() -> None:
    """首条短窗触发 + 连发延展：单条弹幕 800ms 静默快放，连发弹幕在短窗内合并为一批且不被空窗补偿拖长。"""
    buf = MessageBuffer(
        batch_window_ms=3_000,
        batch_max_size=20,
        enable_idle_compensation=True,
        batch_max_wait_ms=30_000,
        quiet_window_ms=800,
    )
    # 单条弹幕：t=1000 到达，t=1799 仍等待，t=1800 静默满 800ms 立即放行（压缩原 3000ms 空等）
    buf.add(_msg("主播晚上好"), arrival_ms=1_000)
    assert buf.should_flush(1_799) == (False, "window_not_expired")
    assert buf.should_flush(1_800) == (True, "window_expired")
    assert [m.content for m in buf.drain()] == ["主播晚上好"]

    # 连发弹幕：t=10_000、t=10_450、t=10_900 到达，短窗随最后一条顺延合并 3 条，t=11_700 一次性放行
    buf.add(_msg("第一句"), arrival_ms=10_000)
    buf.add(_msg("第二句"), arrival_ms=10_450)
    assert buf.should_flush(10_800, avg_interval_ms=450.0) == (False, "window_not_expired")
    buf.add(_msg("第三句"), arrival_ms=10_900)
    assert buf.should_flush(11_699, avg_interval_ms=450.0) == (False, "window_not_expired")
    assert buf.should_flush(11_700, avg_interval_ms=450.0) == (True, "window_expired")
    assert [m.content for m in buf.drain()] == ["第一句", "第二句", "第三句"]


def test_quiet_window_respects_batch_window_cap_on_continuous_burst() -> None:
    """持续刷屏使静默窗口始终不满时，总等待仍由 batch_window_ms 封顶放行，不无限顺延。"""
    buf = MessageBuffer(batch_window_ms=3_000, batch_max_size=20, quiet_window_ms=800)
    for idx in range(7):
        buf.add(_msg(f"刷屏{idx}"), arrival_ms=1_000 + idx * 450)
    # 最后一条在 t=3700，距首条 t=1000 为 2700 < 3000，距末条 200 < 800 -> 继续等
    assert buf.should_flush(3_900) == (False, "window_not_expired")
    # 到达首条后满 3000ms（t=4000），即使距末条不足 800ms 也立即封顶放行
    assert buf.should_flush(4_000) == (True, "window_expired")


def test_quiet_window_bounded_by_smaller_batch_window() -> None:
    """当显式配置的 batch_window_ms 小于 quiet_window_ms 时按更小的 batch_window_ms 放行，不拖慢测试或低延迟配置。"""
    buf = MessageBuffer(batch_window_ms=100, batch_max_size=20, quiet_window_ms=800)
    buf.add(_msg("快测"), arrival_ms=1_000)
    assert buf.should_flush(1_099) == (False, "window_not_expired")
    assert buf.should_flush(1_100) == (True, "window_expired")
