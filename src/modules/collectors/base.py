"""
BaseCollector —— 采集器抽象基类（Wave 3 / §1.52）

采集器（§1.52 框架层第三种角色）= 流型感知者，世界→系统入口，主动推事件。
- 与 Input/Output 阶段的旧名"input collector"是同一概念的事件名演化
- 不是工具（非同步/异步二类）
- 协议面：start / stop / cleanup 生命周期
- emit 由 EventBus 注入；构造器注入 pattern

Wave 3 仅定义抽象基类；具体采集器（bilibili/console/stt/mock）由 W5 迁移。
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Optional

from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

# EventBus 类型提示（避免循环依赖；实际注入由 CollectorManager 完成）
EventBusLike = Any

logger = get_logger("BaseCollector")


class CollectorState(str, Enum):
    """采集器状态机（与 Agent 状态机镜像）"""

    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERRORED = "errored"


class BaseCollector:
    """采集器抽象基类。

    注：本类不通过 abc.ABC 强制子类覆写方法——只是约定层面的"基类"。
    所有方法均有合理默认实现，子类按需覆盖（start / stop / cleanup 钩子）。
    """

    # 元数据（子类必须覆写）
    name: str = ""
    description: str = ""

    def __init__(self, *, event_bus: Optional[EventBusLike] = None) -> None:
        if not self.name:
            self.name = self.__class__.__name__.lower()
        self._state: CollectorState = CollectorState.CREATED
        self._event_bus = event_bus
        self._lock = asyncio.Lock()
        self._started_at_ms: int = 0
        self._stopped_at_ms: int = 0
        logger.debug(f"Collector '{self.name}' 构造完成（描述: {self.description[:40] or '<空>'}…）")

    # ---------------- 生命周期 ----------------

    async def start(self) -> None:
        async with self._lock:
            if self._state not in (CollectorState.CREATED, CollectorState.STOPPED, CollectorState.ERRORED):
                logger.warning(f"Collector '{self.name}' start() 状态不正确: {self._state}")
                return
            self._state = CollectorState.STARTING
        try:
            await self._on_start()
        except Exception as exc:  # noqa: BLE001 - 边界
            self._state = CollectorState.ERRORED
            logger.error(f"Collector '{self.name}' 启动失败: {exc}", exc_info=True)
            raise
        async with self._lock:
            self._state = CollectorState.RUNNING
            self._started_at_ms = now_ms()
        logger.info(f"Collector '{self.name}' 已启动")

    async def stop(self) -> None:
        async with self._lock:
            if self._state == CollectorState.STOPPED:
                return
            self._state = CollectorState.STOPPING
        try:
            await self._on_stop()
        except Exception as exc:  # noqa: BLE001 - 边界
            self._state = CollectorState.ERRORED
            logger.error(f"Collector '{self.name}' 停止失败: {exc}", exc_info=True)
            raise
        async with self._lock:
            self._state = CollectorState.STOPPED
            self._stopped_at_ms = now_ms()
        logger.info(f"Collector '{self.name}' 已停止")

    async def cleanup(self) -> None:
        await self._on_cleanup()
        logger.debug(f"Collector '{self.name}' 资源已清理")

    # ----- 子类可选钩子 -----

    async def _on_start(self) -> None:
        """子类启动钩子（如打开 WebSocket 连接）"""
        return None  # 默认空操作：子类覆写

    async def _on_stop(self) -> None:
        """子类停止钩子（关闭连接）"""
        return None  # 默认空操作：子类覆写

    async def _on_cleanup(self) -> None:
        """子类资源清理钩子"""
        return None  # 默认空操作：子类覆写

    # ---------------- emit 封装 ----------------

    async def emit_event(
        self,
        event_name: str,
        payload,
        source: Optional[str] = None,
    ) -> None:
        """封装 emit：子类直接调，无需关心 bus 是否为 None。"""
        if self._event_bus is None:
            logger.debug(f"Collector '{self.name}' 无 EventBus，跳过 emit: {event_name}")
            return
        await self._event_bus.emit(
            event_name,
            payload,
            source=source or self.name,
        )

    # ---------------- 健康 / 心跳 ----------------

    @property
    def state(self) -> CollectorState:
        return self._state

    def is_running(self) -> bool:
        return self._state == CollectorState.RUNNING

    def set_event_bus(self, event_bus: Optional[EventBusLike]) -> None:
        """事后注入（生命周期内的 EventBus 接入）"""
        self._event_bus = event_bus

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name='{self.name}' state={self._state.value}>"


__all__ = ["BaseCollector", "CollectorState"]
