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
        return [name for name, reg in self._collectors.items() if reg.collector.state == CollectorState.RUNNING]

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
