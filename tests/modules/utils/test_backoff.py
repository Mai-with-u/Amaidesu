"""ExponentialBackoff 公式、封顶、复位与溢出防护的单测。"""

from __future__ import annotations

from src.modules.utils.backoff import ExponentialBackoff


def test_公式与_cap_封顶() -> None:
    backoff = ExponentialBackoff(base_delay_ms=5000, cap_delay_ms=60000)
    assert [backoff.next_delay_ms() for _ in range(7)] == [5000, 10000, 20000, 40000, 60000, 60000, 60000]


def test_reset_复位后回到_base() -> None:
    backoff = ExponentialBackoff(base_delay_ms=2000, cap_delay_ms=60000)
    backoff.next_delay_ms()
    backoff.next_delay_ms()
    backoff.reset()
    assert backoff.next_delay_ms() == 2000


def test_factor_1_5_的_序列() -> None:
    backoff = ExponentialBackoff(base_delay_ms=2000, cap_delay_ms=60000, factor=1.5)
    assert [backoff.next_delay_ms() for _ in range(5)] == [2000, 3000, 4500, 6750, 10125]


def test_max_exponent_防浮点溢出() -> None:
    backoff = ExponentialBackoff(base_delay_ms=5000, cap_delay_ms=60000, max_exponent=3)
    # 指数封顶后继续调用不再增长也不抛 OverflowError
    values = [backoff.next_delay_ms() for _ in range(10)]
    assert values[:4] == [5000, 10000, 20000, 40000]
    assert all(v == 40000 for v in values[4:])


def test_cap_先于_溢出_生效() -> None:
    # 极端参数：不做指数封顶时 factor ** n 远早于撞 cap 就会溢出
    backoff = ExponentialBackoff(base_delay_ms=60_000, cap_delay_ms=60_000, max_exponent=30)
    for _ in range(100):
        assert backoff.next_delay_ms() == 60_000
