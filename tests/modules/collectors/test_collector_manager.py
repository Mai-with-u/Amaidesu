"""
CollectorManager / BaseCollector 单元测试（Wave 3 / §1.52）

覆盖：
- 注册 / 去重
- start_all / stop_all / cleanup_all 批量
- 健康检查 is_all_healthy / get_unhealthy
- 与 AgentManager 设计对称
"""

from __future__ import annotations

from typing import Iterable

import pytest

from src.modules.collectors import BaseCollector, CollectorManager
from src.modules.collectors.base import CollectorState


class _SampleCollector(BaseCollector):
    name = "sample_collector"
    description = "Sample Collector for testing"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.started = 0
        self.stopped = 0

    async def _on_start(self) -> None:
        self.started += 1

    async def _on_stop(self) -> None:
        self.stopped += 1

    def set_event_bus(self, event_bus):
        super().set_event_bus(event_bus)


@pytest.fixture
def sample_collector() -> _SampleCollector:
    return _SampleCollector()


@pytest.fixture
def manager() -> CollectorManager:
    return CollectorManager()


def test_register_dedup(manager: CollectorManager, sample_collector: _SampleCollector) -> None:
    assert manager.register(sample_collector) is True
    # 重复同名 → 跳过
    other = _SampleCollector()
    other.name = "sample_collector"
    assert manager.register(other) is False
    # 仍是第一个
    assert manager.get("sample_collector") is sample_collector


async def test_lifecycle_batch(manager: CollectorManager, sample_collector: _SampleCollector) -> None:
    manager.register(sample_collector)
    await manager.start_all()
    assert sample_collector.state == CollectorState.RUNNING
    assert sample_collector.started == 1

    await manager.stop_all()
    assert sample_collector.state == CollectorState.STOPPED
    assert sample_collector.stopped == 1


async def test_health_check(manager: CollectorManager, sample_collector: _SampleCollector) -> None:
    manager.register(sample_collector)
    await manager.start_all()
    assert manager.is_all_healthy() is True
    assert manager.get_unhealthy() == []


async def test_unhealthy_detection() -> None:
    """state=ERRORED 视为 unhealthy。"""

    class _ErrorCollector(BaseCollector):
        name = "err"
        description = "always errors"

        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._state = CollectorState.ERRORED

    mgr = CollectorManager()
    c = _ErrorCollector()
    mgr.register(c)
    assert mgr.is_all_healthy() is False
    assert "err" in mgr.get_unhealthy()


def test_contains_and_len(manager: CollectorManager, sample_collector: _SampleCollector) -> None:
    manager.register(sample_collector)
    assert "sample_collector" in manager
    assert "absent" not in manager
    assert len(manager) == 1


async def test_unregister_requires_stop(manager: CollectorManager, sample_collector: _SampleCollector) -> None:
    manager.register(sample_collector)
    await sample_collector.start()
    assert manager.unregister("sample_collector") is False
    await sample_collector.stop()
    assert manager.unregister("sample_collector") is True


def test_list_collectors_and_running(manager: CollectorManager, sample_collector: _SampleCollector) -> None:
    manager.register(sample_collector)
    assert manager.list_collectors() == ["sample_collector"]
    assert manager.list_running() == []
