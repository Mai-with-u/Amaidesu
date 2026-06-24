"""
time_utils 模块单元测试

测试内容：
- now_ms() 返回 int 类型、13 位数量级
- elapsed_ms() 计算经过时长
- format_duration_ms() 各种时长的格式化输出
- ms_to_datetime() 毫秒转 datetime 对象

运行: uv run pytest tests/modules/test_time_utils.py -v
"""

import time
from datetime import datetime, timezone

from src.modules.time_utils import (
    elapsed_ms,
    format_duration_ms,
    ms_to_datetime,
    now_ms,
)


# =============================================================================
# now_ms 测试
# =============================================================================


class TestNowMs:
    """now_ms() 单元测试"""

    def test_returns_int(self) -> None:
        """now_ms() 返回值类型必须是 int"""
        result = now_ms()
        assert isinstance(result, int), f"expected int, got {type(result).__name__}"

    def test_returns_13_digit_magnitude(self) -> None:
        """now_ms() 返回值应该是 13 位数量级（毫秒时间戳）"""
        result = now_ms()
        magnitude = len(str(result))
        assert magnitude == 13, f"expected 13 digits, got {magnitude}"

    def test_value_within_unix_ms_range(self) -> None:
        """now_ms() 返回值应该在合理 Unix 毫秒范围内（2020-2100 年）"""
        result = now_ms()
        # 2020-01-01 = 1577836800000
        # 2100-01-01 = 4102444800000
        assert 1577836800000 <= result <= 4102444800000, f"value {result} out of expected range"

    def test_monotonically_increasing(self) -> None:
        """连续调用 now_ms() 应该单调不减"""
        a = now_ms()
        b = now_ms()
        assert b >= a, f"now_ms() decreased: {a} -> {b}"


# =============================================================================
# elapsed_ms 测试
# =============================================================================


class TestElapsedMs:
    """elapsed_ms() 单元测试"""

    def test_returns_int(self) -> None:
        """elapsed_ms() 返回值类型必须是 int"""
        result = elapsed_ms(now_ms())
        assert isinstance(result, int)

    def test_immediate_call_returns_near_zero(self) -> None:
        """立即调用 elapsed_ms(刚记录的 start) 应该返回接近 0 的值"""
        start = now_ms()
        result = elapsed_ms(start)
        # 允许 5ms 之内的执行误差
        assert 0 <= result < 50, f"expected ~0, got {result}"

    def test_after_100ms_sleep(self) -> None:
        """sleep 100ms 后 elapsed_ms 应该返回约 100"""
        start = now_ms()
        time.sleep(0.1)
        result = elapsed_ms(start)
        # 90-200ms 都是合理范围
        assert 90 <= result <= 200, f"expected ~100ms, got {result}"

    def test_after_1_second_sleep(self) -> None:
        """sleep 1s 后 elapsed_ms 应该返回约 1000"""
        start = now_ms()
        time.sleep(1.0)
        result = elapsed_ms(start)
        # 900-1100ms 范围都可接受
        assert 900 <= result <= 1100, f"expected ~1000ms, got {result}"

    def test_future_start_returns_zero(self) -> None:
        """如果 start_ms 在未来，elapsed_ms 应该返回 0（不返回负数）"""
        future = now_ms() + 60_000  # 1 分钟后
        result = elapsed_ms(future)
        assert result == 0, f"expected 0 for future start, got {result}"

    def test_zero_start_ms(self) -> None:
        """elapsed_ms(0) 应该返回大致等于 now_ms 的值（自 epoch 以来的毫秒数）"""
        result = elapsed_ms(0)
        # 距 epoch 大约 56+ 年的毫秒数，应该在 1.5e12 到 5e12 之间
        assert 1_500_000_000_000 < result < 5_000_000_000_000, f"expected ~now_ms, got {result}"


# =============================================================================
# format_duration_ms 测试
# =============================================================================


class TestFormatDurationMs:
    """format_duration_ms() 单元测试"""

    def test_zero(self) -> None:
        """0 毫秒应该格式化为 '0ms'"""
        assert format_duration_ms(0) == "0ms"

    def test_milliseconds_range(self) -> None:
        """小于 1 秒应该显示为 Xms"""
        assert format_duration_ms(1) == "1ms"
        assert format_duration_ms(500) == "500ms"
        assert format_duration_ms(999) == "999ms"

    def test_seconds_range(self) -> None:
        """小于 1 分钟应该显示为带 1 位小数的秒"""
        result = format_duration_ms(1500)
        # 1.5s 或 2s 都可接受
        assert result in ("1.5s", "2s"), f"unexpected format: {result}"

    def test_seconds_range_decimal(self) -> None:
        """秒级别应该保留 1 位小数"""
        result = format_duration_ms(12345)  # 12.345s
        assert result == "12.3s", f"expected '12.3s', got {result}"

    def test_minutes_range(self) -> None:
        """小于 1 小时应该显示为 Xm Ys"""
        # 5m 23s = 323s = 323000ms
        assert format_duration_ms(323_000) == "5m 23s"
        # 1m 0s
        assert format_duration_ms(60_000) == "1m 0s"

    def test_hours_range(self) -> None:
        """大于等于 1 小时应该显示为 Xh Ym Zs"""
        # 1h 23m 45s = 5025s = 5025000ms
        assert format_duration_ms(5_025_000) == "1h 23m 45s"
        # 2h 0m 0s
        assert format_duration_ms(7_200_000) == "2h 0m 0s"

    def test_negative_returns_zero_string(self) -> None:
        """负数应该返回 '0ms'（不应该输出负号）"""
        assert format_duration_ms(-100) == "0ms"


# =============================================================================
# ms_to_datetime 测试
# =============================================================================


class TestMsToDatetime:
    """ms_to_datetime() 单元测试"""

    def test_returns_datetime(self) -> None:
        """ms_to_datetime() 应该返回 datetime 对象"""
        result = ms_to_datetime(0)
        assert isinstance(result, datetime)

    def test_epoch_zero(self) -> None:
        """0 毫秒应该对应 UTC 1970-01-01"""
        result = ms_to_datetime(0)
        assert result == datetime(1970, 1, 1, tzinfo=timezone.utc)

    def test_returns_utc_timezone(self) -> None:
        """返回值必须带 UTC 时区（避免本地时间歧义）"""
        result = ms_to_datetime(now_ms())
        assert result.tzinfo == timezone.utc

    def test_known_timestamp(self) -> None:
        """已知时间戳的转换正确性"""
        # 2024-01-01 00:00:00 UTC = 1704067200000 ms
        result = ms_to_datetime(1704067200000)
        assert result == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_roundtrip_via_now_ms(self) -> None:
        """ms_to_datetime(now_ms()) 的结果应该在当前时间附近"""
        before = now_ms()
        result = ms_to_datetime(before)
        after = now_ms()
        # 转换出的 datetime 的毫秒数应该在 [before, after] 区间内
        assert before <= int(result.timestamp() * 1000) <= after


# =============================================================================
# 集成测试：函数协作
# =============================================================================


class TestIntegration:
    """函数之间的协作测试"""

    def test_now_then_elapsed(self) -> None:
        """now_ms() → elapsed_ms() 的端到端测试"""
        start = now_ms()
        time.sleep(0.05)  # 50ms
        elapsed = elapsed_ms(start)
        assert 40 <= elapsed <= 150, f"unexpected elapsed: {elapsed}ms"

    def test_format_now_elapsed(self) -> None:
        """组合使用：测量 elapsed 并格式化为可读字符串"""
        start = now_ms()
        time.sleep(0.05)
        formatted = format_duration_ms(elapsed_ms(start))
        # 50ms 左右应该格式化为 "50ms" 或 "0.1s"
        assert formatted.endswith("ms") or formatted.endswith("s")
