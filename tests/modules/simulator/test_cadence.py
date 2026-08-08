"""测试 CadenceGenerator - 泊松节奏 + 状态机"""

import asyncio

from src.modules.simulator.cadence import (
    CadenceGenerator,
)
from src.modules.simulator.config_schema import (
    SimulatorConfigSchema,
)


def test_initial_state():
    cfg = SimulatorConfigSchema()
    cg = CadenceGenerator(cfg, seed=42)
    assert cg.get_state() == "WARMUP"


def test_burst_trigger():
    cfg = SimulatorConfigSchema()
    cg = CadenceGenerator(cfg, seed=42)
    result = cg.trigger_burst()
    assert result is True or cg.get_state() == "BURST"


def test_burst_min_interval():
    cfg = SimulatorConfigSchema(burst_min_interval_s=30.0)
    cg = CadenceGenerator(cfg, seed=42)
    cg.trigger_burst()
    result = cg.trigger_burst()
    assert result is False


def test_notify_wakes_from_idle():
    cfg = SimulatorConfigSchema()
    cg = CadenceGenerator(cfg, seed=42)
    cg._state = "IDLE"  # type: ignore[attr-defined]
    cg.notify_streamer_activity()
    assert cg.get_state() == "NORMAL"


def test_poisson_distribution():
    cfg = SimulatorConfigSchema(base_rate_per_minute=6.0)
    cg = CadenceGenerator(cfg, seed=42)
    cg._state = "NORMAL"  # type: ignore[attr-defined]
    delays = []
    for _ in range(100):
        d = asyncio.run(cg.next_delay_seconds())
        delays.append(d)
    mean = sum(delays) / len(delays)
    assert 5.0 < mean < 20.0


def test_burst_state_snapshot():
    cfg = SimulatorConfigSchema()
    cg = CadenceGenerator(cfg, seed=42)
    burst = cg.get_burst_state()
    assert burst.is_active is False


def test_update_config():
    cfg = SimulatorConfigSchema(base_rate_per_minute=6.0)
    cg = CadenceGenerator(cfg, seed=42)
    new_cfg = SimulatorConfigSchema(base_rate_per_minute=12.0)
    cg.update_config(new_cfg)
    assert cg.config.base_rate_per_minute == 12.0  # type: ignore[attr-defined]
