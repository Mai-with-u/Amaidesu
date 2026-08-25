"""
CollectorManager —— 采集器生命周期管理（Wave 3 / §1.52）

设计对称 AgentManager：
- 注册 / 启动 / 停止 / cleanup
- start_all / stop_all / cleanup_all
- 健康监控（运行中状态）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.modules.collectors.base import BaseCollector, CollectorState
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

logger = get_logger("CollectorManager")


@dataclass(slots=True)
class CollectorRegistration:
    """采集器注册项"""

    name: str
    collector: BaseCollector
    description: str = ""
    registered_at_ms: int = 0


class CollectorManager:
    """采集器生命周期管理。"""

    def __init__(self) -> None:
        self._collectors: Dict[str, CollectorRegistration] = {}
        self._lock = asyncio.Lock()

    # -------------------- 注册 --------------------

    def register(self, collector: BaseCollector, *, description: Optional[str] = None) -> bool:
        if not collector.name:
            logger.warning(f"Collector 缺少 name（class={type(collector).__name__}），跳过")
            return False
        if collector.name in self._collectors:
            logger.debug(f"Collector '{collector.name}' 已注册，跳过")
            return False
        self._collectors[collector.name] = CollectorRegistration(
            name=collector.name,
            collector=collector,
            description=description or collector.description,
            registered_at_ms=now_ms(),
        )
        logger.info(f"Collector '{collector.name}' 已注册")
        return True

    def unregister(self, name: str) -> bool:
        reg = self._collectors.get(name)
        if reg is None:
            return False
        if reg.collector.state not in (CollectorState.STOPPED, CollectorState.CREATED, CollectorState.ERRORED):
            logger.warning(f"Collector '{name}' 未停止，不能 unregister")
            return False
        self._collectors.pop(name, None)
        logger.info(f"Collector '{name}' 已从管理器移除")
        return True

    # -------------------- 查询 --------------------

    def get(self, name: str) -> Optional[BaseCollector]:
        reg = self._collectors.get(name)
        return reg.collector if reg is not None else None

    def list_collectors(self) -> List[str]:
        return list(self._collectors.keys())

    def list_running(self) -> List[str]:
        """返回运行中的采集器。

        兼容层状态优先：具体 Collectors（W5 迁移前）覆写 start()/stop() 维护
        自身 ``is_started`` 标志而绕过基类状态机，基类 ``state`` 停在 CREATED；故
        以 ``is_started`` 为准，基类 RUNNING 状态作参考。
        """
        running: List[str] = []
        for name, reg in self._collectors.items():
            collector = reg.collector
            is_running = getattr(collector, "is_started", False) or collector.state == CollectorState.RUNNING
            if is_running:
                running.append(name)
        return running

    # -------------------- 动态启停（Dashboard 组件管理调用） --------------------

    def get_collector_by_name(self, name: str) -> Optional[BaseCollector]:
        """按注册名查找已加载的 Collector 实例（段名=注册名）。"""
        reg = self._collectors.get(name)
        return reg.collector if reg is not None else None

    async def start_collector(self, name: str) -> bool:
        """启动（或重启）单个已注册 Collector 的运行任务。"""
        reg = self._collectors.get(name)
        if reg is None:
            return False
        collector = reg.collector
        if getattr(collector, "is_started", False) or collector.state == CollectorState.RUNNING:
            return True
        try:
            await collector.start()
        except Exception as exc:  # noqa: BLE001 - 边界
            logger.error(f"Collector '{name}' 单实例启动失败: {exc}", exc_info=True)
            return False
        return getattr(collector, "is_started", False) or collector.state == CollectorState.RUNNING

    async def stop_collector(self, name: str) -> bool:
        """停止单个 Collector：调用 stop()（不 unregister）。"""
        reg = self._collectors.get(name)
        if reg is None:
            return False
        try:
            await reg.collector.stop()
        except Exception as exc:  # noqa: BLE001 - 边界
            logger.error(f"Collector '{name}' 单实例停止失败: {exc}", exc_info=True)
            return False
        return True

    async def enable_collector(
        self, name: str, config: Optional[dict] = None, event_bus: Optional[object] = None
    ) -> bool:
        """动态启用 Collector：实例化（配置段名）→ 注册 → 启动。

        段名 = 注册名：实例化后强制对齐实例 name 为段名，保证
        list_collectors()/get_by_name() 与配置 enabled 列表一致。

        Args:
            name: 配置段名（如 "mock_danmaku"）。
            config: 子段配置 dict。
            event_bus: 可选 EventBus（注入采集器）。
        """
        from src.modules.collectors.factory import instantiate_collector

        instance = instantiate_collector(name, config or {}, event_bus=event_bus)
        if instance is None:
            logger.warning(f"未实现的 Collector: {name}")
            return False
        if instance.name != name:
            instance.name = name
        if not self.register(instance):
            return False
        return await self.start_collector(name)

    async def disable_collector(self, name: str) -> bool:
        """动态停用 Collector：停止 → unregister。"""
        if not await self.stop_collector(name):
            return False
        self.unregister(name)
        logger.info(f"Collector '{name}' 已动态停用")
        return True

    def __len__(self) -> int:
        return len(self._collectors)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._collectors

    # -------------------- 生命周期 --------------------

    async def start_all(self) -> None:
        for name, reg in self._collectors.items():
            try:
                await reg.collector.start()
            except Exception as exc:  # noqa: BLE001 - 边界
                logger.error(f"Collector '{name}' 启动失败: {exc}", exc_info=True)

    async def stop_all(self) -> None:
        """按注册逆序停止（LIFO）。"""
        for name in reversed(list(self._collectors.keys())):
            reg = self._collectors.get(name)
            if reg is None:
                continue
            try:
                await reg.collector.stop()
            except Exception as exc:  # noqa: BLE001 - 边界
                logger.error(f"Collector '{name}' 停止失败: {exc}", exc_info=True)

    async def cleanup_all(self) -> None:
        for name, reg in self._collectors.items():
            try:
                await reg.collector.cleanup()
            except Exception as exc:  # noqa: BLE001 - 边界
                logger.error(f"Collector '{name}' cleanup 失败: {exc}", exc_info=True)

    # -------------------- 健康监控 --------------------

    def is_all_healthy(self) -> bool:
        """全部已注册采集器处于 RUNNING/STOPPED/CREATED 视为健康（ERRORED -> False）。"""
        for reg in self._collectors.values():
            if reg.collector.state == CollectorState.ERRORED:
                return False
        return True

    def get_unhealthy(self) -> List[str]:
        return [name for name, reg in self._collectors.items() if reg.collector.state == CollectorState.ERRORED]


__all__ = ["CollectorManager", "CollectorRegistration"]
