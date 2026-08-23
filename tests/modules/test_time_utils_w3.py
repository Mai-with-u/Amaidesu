"""
time_utils W3 增量测试（§1.53 9d 定案）

新增 ``ms_to_s`` / ``s_to_ms`` 纯函数（仅在 MemoryProvider 接缝调用）。
补到既有 ``test_time_utils.py`` 外，避免修改既有测试。
"""

from __future__ import annotations

from src.modules.time_utils import ms_to_s, s_to_ms


# =============================================================================
# ms_to_s / s_to_ms 纯函数（§1.53 9d）
# =============================================================================


def test_ms_to_s_zero() -> None:
    assert ms_to_s(0) == 0


def test_ms_to_s_one_second() -> None:
    assert ms_to_s(1000) == 1


def test_ms_to_s_truncates_partial() -> None:
    """毫秒小数部分截断（整数除法）。"""
    assert ms_to_s(1500) == 1
    assert ms_to_s(1999) == 1
    assert ms_to_s(2000) == 2


def test_ms_to_s_negative_returns_zero() -> None:
    """负值保护。"""
    assert ms_to_s(-1) == 0
    assert ms_to_s(-1000) == 0
    assert ms_to_s(-999_999) == 0


def test_s_to_ms_zero() -> None:
    assert s_to_ms(0) == 0


def test_s_to_ms_basic() -> None:
    assert s_to_ms(1) == 1000
    assert s_to_ms(2) == 2000
    assert s_to_ms(60) == 60_000


def test_s_to_ms_negative_returns_zero() -> None:
    assert s_to_ms(-1) == 0
    assert s_to_ms(-100) == 0


def test_round_trip_s_to_ms_to_s() -> None:
    """s → ms → s 恒等。"""
    for s in (0, 1, 60, 3600, 86400):
        assert ms_to_s(s_to_ms(s)) == s
