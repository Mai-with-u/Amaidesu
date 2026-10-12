"""测试 TokenBudgetController"""

from src.modules.simulator.token_budget import (
    TokenBudgetController,
)


def test_empty_state():
    b = TokenBudgetController(50000)
    assert not b.is_budget_exceeded()
    assert not b.is_budget_warning()
    assert b.get_usage_last_hour() == 0


def test_warning_threshold():
    b = TokenBudgetController(50000)
    b.record_usage(40000)
    assert b.is_budget_warning()
    assert not b.is_budget_exceeded()


def test_exceeded_threshold():
    b = TokenBudgetController(50000)
    b.record_usage(50001)
    assert b.is_budget_exceeded()


def test_remaining():
    b = TokenBudgetController(50000)
    b.record_usage(10000)
    assert b.get_remaining() == 40000


def test_reset():
    b = TokenBudgetController(50000)
    b.record_usage(40000)
    b.reset()
    assert b.get_usage_last_hour() == 0


def test_ignore_negative_or_zero_tokens():
    b = TokenBudgetController(50000)
    b.record_usage(0)
    b.record_usage(-10)
    assert b.get_usage_last_hour() == 0
