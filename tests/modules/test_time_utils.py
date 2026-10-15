"""time_utils：毫秒时间戳、经过时长、时长格式化与毫秒转 UTC datetime"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.modules.time_utils import elapsed_ms, format_duration_ms, ms_to_datetime, now_ms


def test_now_ms_is_a_13_digit_int_within_the_unix_ms_range_and_never_goes_back() -> None:
    first, second = now_ms(), now_ms()
    assert isinstance(first, int) and len(str(first)) == 13
    assert 1_577_836_800_000 <= first <= 4_102_444_800_000  # 2020-2100 年
    assert second >= first


def test_elapsed_ms_measures_from_start_and_clamps_future_start_to_zero() -> None:
    with patch("src.modules.time_utils.now_ms", return_value=10_000):
        assert elapsed_ms(9_900) == 100
        assert elapsed_ms(10_000) == 0
        assert elapsed_ms(70_000) == 0  # start 在未来：不返回负数
    assert 1_500_000_000_000 < elapsed_ms(0) < 5_000_000_000_000  # 自 epoch 起的毫秒数


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (0, "0ms"),
        (1, "1ms"),
        (999, "999ms"),
        (12_345, "12.3s"),
        (323_000, "5m 23s"),
        (60_000, "1m 0s"),
        (5_025_000, "1h 23m 45s"),
        (7_200_000, "2h 0m 0s"),
        (-100, "0ms"),  # 负数不输出负号
    ],
)
def test_format_duration_ms_picks_the_unit_by_magnitude(duration: int, expected: str) -> None:
    assert format_duration_ms(duration) == expected


def test_ms_to_datetime_returns_utc_aware_datetimes() -> None:
    assert ms_to_datetime(0) == datetime(1970, 1, 1, tzinfo=timezone.utc)
    assert ms_to_datetime(1_704_067_200_000) == datetime(2024, 1, 1, tzinfo=timezone.utc)
    stamp = now_ms()
    assert ms_to_datetime(stamp).tzinfo == timezone.utc  # 带时区，避免本地时间歧义
    assert int(ms_to_datetime(stamp).timestamp() * 1000) == stamp
