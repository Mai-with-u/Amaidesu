"""
BaseCollector —— 采集器抽象基类

采集器 = 流型感知者，世界→系统入口，主动推事件。
- 与 Input/Output 阶段的旧名"input collector"是同一概念的事件名演化
- 不是工具（非同步/异步二类）
- 协议面：start / stop / cleanup 生命周期
- emit 由 EventBus 注入；构造器注入 pattern
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, AsyncIterator, Optional

from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
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
        # 主动推事件：collect() 生成器的后台消费任务（让内部 emit 真正执行）
        self._collect_task: Optional["asyncio.Task[Any]"] = None
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

    # ----- 主动推事件：collect() 后台消费任务 -----

    async def _start_collect_task(self) -> None:
        """启动后台任务消费 ``collect()`` 生成器（触发内部 emit 逻辑）。

        采集器在 ``start()`` 中调用；用户侧无需再迭代生成器。
        幂等：已启动则不重复创建。
        """
        if self._collect_task is not None and not self._collect_task.done():
            return
        self._collect_task = asyncio.create_task(self._consume_collect(), name=f"{self.name}-collect")

    async def _stop_collect_task(self) -> None:
        """取消并等待后台消费任务退出。"""
        task = self._collect_task
        self._collect_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug(f"Collector '{self.name}' 后台任务异常（已吞）", exc_info=True)

    async def _consume_collect(self) -> None:
        """迭代 ``collect()`` 生成器；collect 内部自行 emit 语义事件。

        带内部 emit 的采集器（bilibili/mock，``emit_semantic_events=True``）
        在 collect() 中自行 ``_emit_semantic_event``；无内部 emit 的采集器
        （screen/stt/console 类）由基类兜底转发。判定依据：采集器实例的
        ``_emit_semantic_events`` 配置（True → 内部负责；False/不存在 → 兜底）。
        """
        has_internal_emit = bool(getattr(self, "_emit_semantic_events", False))
        try:
            async for message in self.collect():
                if not has_internal_emit:
                    await self._emit_normalized_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 边界
            logger.error(f"Collector '{self.name}' collect() 后台消费循环异常: {exc}", exc_info=True)

    async def _emit_normalized_message(self, message: Any) -> None:
        """把 NormalizedMessage 标准化为 room.message.* 事件并 emit（兜底转发）。

        data_type → 事件映射（与 console collector 的 ``_MESSAGE_TYPE_TO_EVENT`` 一致）：
        - text → room.message.danmaku
        - gift → room.message.gift
        - super_chat → room.message.super_chat
        - guard → room.message.enter（大航海=进房语义）
        """
        data_type = getattr(message, "data_type", "text")
        event_map = {
            "text": ("room.message.danmaku", "danmaku"),
            "gift": ("room.message.gift", "gift"),
            "super_chat": ("room.message.super_chat", "super_chat"),
            "guard": ("room.message.enter", "enter"),
        }
        mapping = event_map.get(data_type)
        if mapping is None:
            logger.debug(f"未知 data_type '{data_type}'，跳过 emit")
            return

        event_name, message_type = mapping
        payload = RoomMessagePayload(
            live_session_id="console",
            message_type=message_type,  # type: ignore[arg-type]
            user=RoomMessageUser(
                id=str(getattr(message, "user_id", None) or "unknown"),
                name=str(getattr(message, "user_nickname", None) or "unknown"),
            ),
            content=str(getattr(message, "text", "") or ""),
            timestamp_ms=int(getattr(message, "timestamp_ms", 0) or 0),
        )
        await self.emit_event(event_name, payload)

    # ----- 子类可选钩子 -----

    def collect(self) -> "AsyncIterator[Any]":
        """采集数据循环（子类实现）。

        Returns:
            AsyncIterator[NormalizedMessage]：由基类 ``_consume_collect()``
            后台迭代（主动推事件模式）。具体采集器必须实现。
        """
        raise NotImplementedError(f"Collector '{self.name}' 未实现 collect()")

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
