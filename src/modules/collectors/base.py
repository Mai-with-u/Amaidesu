"""
BaseCollector —— 采集器抽象基类

采集器 = 流型感知者，世界→系统入口，主动推事件。
- 不是工具（非同步/异步二类）
- 协议面：start / stop / cleanup 生命周期
- emit 由 EventBus 注入；构造器注入 pattern
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, AsyncIterator, Dict, Optional

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms

# EventBus 类型提示（避免循环依赖；实际注入由 CollectorManager 完成）
EventBusLike = Any

logger = get_logger("BaseCollector")

# message_type → room.message.* 语义事件名（CoreEvents 为单一词汇源）。
# 事件名末段 == message_type，由 EventBus 的判别一致性校验兜底；
# 各采集器不得自带私有映射表，统一走 ``publish_room_message``。
_MESSAGE_TYPE_TO_EVENT: Dict[str, str] = {
    "danmaku": CoreEvents.ROOM_MESSAGE_DANMAKU,
    "enter": CoreEvents.ROOM_MESSAGE_ENTER,
    "gift": CoreEvents.ROOM_MESSAGE_GIFT,
    "super_chat": CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
    "guard": CoreEvents.ROOM_MESSAGE_GUARD,
    "partner_speech": CoreEvents.ROOM_MESSAGE_PARTNER_SPEECH,
}


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
        """启动采集器（模板方法，子类不得覆写）。

        锁保护下的状态迁移全部留在本方法：子类只覆写 ``_on_start`` 钩子，
        启动前的耗时准备（如模型加载）也在钩子内阻塞完成，不引入额外状态。
        """
        async with self._lock:
            if self._state not in (CollectorState.CREATED, CollectorState.STOPPED, CollectorState.ERRORED):
                logger.warning(f"Collector '{self.name}' start() 状态不正确: {self._state}")
                return
            self._state = CollectorState.STARTING
        try:
            await self._on_start()
        except Exception as exc:  # noqa: BLE001 - 边界
            self._state = CollectorState.ERRORED
            logger.exception(f"Collector '{self.name}' 启动失败: {exc}")
            raise
        async with self._lock:
            self._state = CollectorState.RUNNING
            self._started_at_ms = now_ms()
        logger.info(f"Collector '{self.name}' 已启动")

    async def stop(self) -> None:
        """停止采集器（模板方法，子类不得覆写）；收尾动作放 ``_on_stop`` 钩子。"""
        async with self._lock:
            if self._state == CollectorState.STOPPED:
                return
            self._state = CollectorState.STOPPING
        try:
            await self._on_stop()
        except Exception as exc:  # noqa: BLE001 - 边界
            self._state = CollectorState.ERRORED
            logger.exception(f"Collector '{self.name}' 停止失败: {exc}")
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
                logger.debug(f"Collector '{self.name}' 后台任务异常（已吞）", exc=True)

    async def _consume_collect(self) -> None:
        """迭代 ``collect()`` 生成器（采集器自产自发）。

        采集器在 collect() 内部自行构造事件载荷并 ``emit_event``；本基类
        只负责驱动生成器迭代与异常隔离，不做任何消息转换或兜底转发。
        """
        try:
            async for _message in self.collect():
                pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 边界
            logger.exception(f"Collector '{self.name}' collect() 后台消费循环异常: {exc}")
        else:
            # 生成器自然结束（数据源关闭，如 stdin EOF）：运行态随之收束为
            # STOPPED，避免"循环已退出但采集器仍表现为运行中"
            async with self._lock:
                if self._state == CollectorState.RUNNING:
                    self._state = CollectorState.STOPPED

    # ----- 子类可选钩子 -----

    def collect(self) -> "AsyncIterator[Any]":
        """采集数据循环（子类实现）。

        Returns:
            采集器自行产出事件载荷并 emit 后的异步迭代器；由基类
            ``_consume_collect()`` 后台迭代驱动。具体采集器必须实现。
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

    async def publish_room_message(self, payload: RoomMessagePayload) -> bool:
        """深入口：RoomMessagePayload → ``room.message.*`` 语义事件扇出。

        收拢各采集器漂移的私有映射：按载荷的 ``message_type`` 选 CoreEvents
        语义事件名发布。统一兜底策略——未知 ``message_type`` 记 warning（不抛
        异常）；身份键（``user.id``）缺失宁缺毋滥直接丢弃，兜底 id 会让多个
        无 id 用户退化成同一行、身份互相混淆；emit 失败记 warning 不中断采集。

        Args:
            payload: 直播间行为流载荷

        Returns:
            是否真的发布了一条语义事件（丢弃/未知类型/emit 失败均为 False）
        """
        if not payload.user.id:
            logger.warning(
                f"Collector '{self.name}' 载荷缺失身份键 user.id，丢弃（防多用户退化混淆）: "
                f"message_type={payload.message_type}"
            )
            return False
        event_name = _MESSAGE_TYPE_TO_EVENT.get(payload.message_type)
        if event_name is None:
            logger.warning(f"Collector '{self.name}' 未知 message_type，跳过 emit: {payload.message_type}")
            return False
        try:
            await self.emit_event(event_name, payload)
        except Exception as exc:  # noqa: BLE001 - emit 边界，失败不影响后续采集
            logger.warning(f"Collector '{self.name}' emit 语义事件失败: {event_name}", exc=exc)
            return False
        return True

    async def emit_event(
        self,
        event_name: str,
        payload: BasePayload,
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
