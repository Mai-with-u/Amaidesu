"""CadenceGenerator（泊松节奏 + 状态机）与 TokenBudgetController（每小时 token 预算）"""

import asyncio

from src.modules.simulator.cadence import CadenceGenerator
from src.modules.simulator.config_schema import SimulatorConfigSchema
from src.modules.simulator.token_budget import TokenBudgetController


def test_cadence_state_machine_burst_cooldown_and_wakeup() -> None:
    cg = CadenceGenerator(SimulatorConfigSchema(burst_min_interval_s=30.0), seed=42)
    assert cg.get_state() == "WARMUP" and cg.get_burst_state().is_active is False

    assert cg.trigger_burst() is True or cg.get_state() == "BURST"
    assert cg.trigger_burst() is False  # 爆发间隔未过

    cg._state = "IDLE"  # type: ignore[attr-defined]
    cg.notify_streamer_activity()
    assert cg.get_state() == "NORMAL"  # 主播发言把冷清状态唤醒


def test_poisson_delays_average_near_rate_and_config_updates_live() -> None:
    cg = CadenceGenerator(SimulatorConfigSchema(base_rate_per_minute=6.0), seed=42)
    cg._state = "NORMAL"  # type: ignore[attr-defined]
    delays = [asyncio.run(cg.next_delay_seconds()) for _ in range(100)]
    assert 5.0 < sum(delays) / len(delays) < 20.0  # 每分钟 6 条 → 均值约 10 秒

    cg.update_config(SimulatorConfigSchema(base_rate_per_minute=12.0))
    assert cg.config.base_rate_per_minute == 12.0  # type: ignore[attr-defined]


def test_token_budget_thresholds_remaining_reset_and_ignored_usage() -> None:
    budget = TokenBudgetController(50000)
    assert not budget.is_budget_exceeded() and not budget.is_budget_warning() and budget.get_usage_last_hour() == 0

    budget.record_usage(0)
    budget.record_usage(-10)
    assert budget.get_usage_last_hour() == 0  # 非正数忽略

    budget.record_usage(10000)
    assert budget.get_remaining() == 40000
    budget.record_usage(30000)
    assert budget.is_budget_warning() and not budget.is_budget_exceeded()  # 40000 起预警
    budget.record_usage(10001)
    assert budget.is_budget_exceeded()

    budget.reset()
    assert budget.get_usage_last_hour() == 0
