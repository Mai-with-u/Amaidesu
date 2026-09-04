"""MessageBuffer 单元测试（Streamer Agent 内部批状态）。

重点验证 idle 补偿公式 verbatim 保留（唯一改写禁区）。
"""

from __future__ import annotations

from src.agents.streamer.message_buffer import MessageBuffer
from src.modules.types.base.normalized_message import NormalizedMessage


def _msg(text: str = "hi", *, data_type: str = "text", importance: float = 0.5) -> NormalizedMessage:
    return NormalizedMessage(
        text=text,
        source="test",
        data_type=data_type,
        importance=importance,
    )


class TestMessageBufferBasic:
    """基本 add / drain / properties。"""

    def test_initial_state_is_empty(self) -> None:
        buf = MessageBuffer()
        assert buf.is_empty is True
        assert buf.size == 0
        assert buf.force is False

    def test_add_then_drain(self) -> None:
        buf = MessageBuffer()
        buf.add(_msg("a"), arrival_ms=1_000)
        buf.add(_msg("b"), arrival_ms=1_500)
        assert buf.size == 2

        msgs = buf.drain()
        assert len(msgs) == 2
        assert msgs[0].text == "a"
        assert msgs[1].text == "b"
        assert buf.is_empty is True
        assert buf.force is False

    def test_add_force_flag_propagates(self) -> None:
        buf = MessageBuffer()
        buf.add(_msg("a"), arrival_ms=1_000, forced=False)
        buf.add(_msg("sc"), arrival_ms=1_500, forced=True)
        assert buf.force is True

        buf.drain()
        assert buf.force is False  # drain 后清零


class TestMessageBufferShouldFlush:
    """should_flush 判定 + idle 补偿公式。"""

    def test_forced_always_flushes(self) -> None:
        buf = MessageBuffer(batch_window_ms=3_000)
        buf.add(_msg("a"), arrival_ms=1_000, forced=True)
        flush, reason = buf.should_flush(1_500)
        assert flush is True
        assert reason == "forced"

    def test_batch_full_flushes(self) -> None:
        buf = MessageBuffer(batch_window_ms=3_000, batch_max_size=3)
        for i in range(3):
            buf.add(_msg(f"m{i}"), arrival_ms=1_000 + i * 100)
        flush, reason = buf.should_flush(1_300)
        assert flush is True
        assert reason == "batch_full"

    def test_window_not_expired_does_not_flush(self) -> None:
        buf = MessageBuffer(batch_window_ms=3_000)
        buf.add(_msg("a"), arrival_ms=1_000)
        flush, reason = buf.should_flush(1_500)  # 仅 0.5s
        assert flush is False
        assert reason == "window_not_expired"

    def test_window_expired_no_compensation_flushes(self) -> None:
        """enable_idle_compensation=False → 窗口到期即触发（无补偿折算）。"""
        buf = MessageBuffer(batch_window_ms=3_000, enable_idle_compensation=False)
        buf.add(_msg("a"), arrival_ms=1_000)
        flush, reason = buf.should_flush(5_000)
        assert flush is True
        assert reason == "window_expired"

    def test_window_expired_with_compensation_no_avg_flushes(self) -> None:
        """enable_idle_compensation=True 但 avg_interval_ms=None → 退化触发。"""
        buf = MessageBuffer(batch_window_ms=3_000, enable_idle_compensation=True)
        buf.add(_msg("a"), arrival_ms=1_000)
        flush, reason = buf.should_flush(5_000, avg_interval_ms=None)
        assert flush is True
        assert reason == "window_expired"

    def test_idle_compensation_formula_verbatim(self) -> None:
        """idle 补偿公式 verbatim：
        idle_equivalent = min(idle_ms / avg_interval_ms, batch_max_size - 1)
        equivalent_count = actual_size + idle_equivalent
        """
        buf = MessageBuffer(batch_window_ms=1_000, batch_max_size=10, enable_idle_compensation=True)
        # arrival_ms=1_000 → first_arrival_ms=1_000, last_arrival_ms=1_000
        buf.add(_msg("a"), arrival_ms=1_000)
        # now_ms=2_000: window_expired = (2000 - 1000) >= 1000 = True
        # idle_ms = 2000 - 1000 = 1000；avg=1000；idle_equiv = min(1.0, 9) = 1.0
        # equivalent = 1 + 1.0 = 2.0 < 10 → 不触发
        flush, reason = buf.should_flush(2_000, avg_interval_ms=1_000)
        assert flush is False
        assert "waiting_idle" in reason

    def test_idle_compensation_reaches_threshold(self) -> None:
        """idle 折算 + 实际条数 >= batch_max_size → 触发。"""
        buf = MessageBuffer(batch_window_ms=1_000, batch_max_size=5, enable_idle_compensation=True)
        buf.add(_msg("a"), arrival_ms=1_000)
        # now=10_000: idle_ms = 10_000 - 1_000 = 9_000; idle_equiv = min(9, 4) = 4
        # equivalent = 1 + 4 = 5 >= 5 → 触发
        flush, reason = buf.should_flush(10_000, avg_interval_ms=1_000)
        assert flush is True
        assert reason == "idle_compensation"

    def test_idle_compensation_caps_at_max_minus_one(self) -> None:
        """idle_equivalent 封顶 batch_max_size-1（防止空批次触发）。"""
        buf = MessageBuffer(batch_window_ms=1_000, batch_max_size=3, enable_idle_compensation=True)
        buf.add(_msg("a"), arrival_ms=1_000)
        # idle_ms = 99999; idle_equiv = min(99999/1000, 3-1) = 2
        # equivalent = 1 + 2 = 3 >= 3 → 触发
        flush, _ = buf.should_flush(100_000, avg_interval_ms=1_000)
        assert flush is True


class TestMessageBufferRender:
    """render_batch_text 静态方法。"""

    def test_render_empty(self) -> None:
        text = MessageBuffer.render_batch_text([])
        assert text == ""

    def test_render_with_messages(self) -> None:
        msgs = [
            _msg("hello"),
            _msg("world", data_type="super_chat", importance=0.9),
        ]
        text = MessageBuffer.render_batch_text(msgs)
        assert "hello" in text
        assert "world" in text
