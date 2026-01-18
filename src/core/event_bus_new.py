"""
增强的事件总线实现

增加了以下功能:
- 错误隔离机制(单个handler异常不影响其他)
- 优先级控制(handler可设置priority,数字越小越优先)
- 统计功能(emit/on调用计数、错误率、执行时间)
- 生命周期管理(cleanup方法)
"""

from typing import Any, Callable, Dict, List, Optional
import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass

from src.utils.logger import get_logger


@dataclass
class EventStats:
    """
    事件统计信息

    Attributes:
        emit_count: 发布次数
        listener_count: 监听器数量
        error_count: 错误次数
        last_emit_time: 最后发布时间(Unix时间戳,秒)
        last_error_time: 最后错误时间(Unix时间戳,秒)
        total_execution_time_ms: 总执行时间(毫秒)
    """

    emit_count: int = 0
    listener_count: int = 0
    error_count: int = 0
    last_emit_time: float = 0
    last_error_time: float = 0
    total_execution_time_ms: float = 0


@dataclass
class HandlerWrapper:
    """
    事件处理器包装器

    包含处理器函数和元数据:
    - priority: 优先级(数字越小越优先)
    - error_count: 错误次数
    - last_error: 最后错误信息
    """

    handler: Callable
    priority: int = 100
    error_count: int = 0
    last_error: Optional[str] = None


class EventBus:
    """
    增强的事件总线

    核心功能:
    - 发布/订阅模式
    - 错误隔离(单个handler异常不影响其他)
    - 优先级控制(按priority排序执行)
    - 统计功能(跟踪emit、错误、执行时间)
    - 生命周期管理(cleanup方法)
    """

    def __init__(self, enable_stats: bool = True):
        """
        初始化事件总线

        Args:
            enable_stats: 是否启用统计功能
        """
        self._handlers: Dict[str, List[HandlerWrapper]] = defaultdict(list)
        self._stats: Dict[str, EventStats] = defaultdict(lambda: EventStats())
        self.enable_stats = enable_stats
        self._is_cleanup = False
        self.logger = get_logger("EventBus")
        self.logger.debug("EventBus 初始化完成")

    async def emit(self, event_name: str, data: Any, source: str = "unknown", error_isolate: bool = True) -> None:
        """
        发布事件

        Args:
            event_name: 事件名称
            data: 事件数据
            source: 事件源(通常是发布者的类名)
            error_isolate: 是否隔离错误(True时单个handler异常不影响其他)
        """
        if self._is_cleanup:
            self.logger.warning(f"EventBus正在清理中，忽略事件: {event_name}")
            return

        handlers = self._handlers.get(event_name, [])
        if not handlers:
            self.logger.debug(f"事件 {event_name} 没有监听器")
            return

        # 按优先级排序(数字越小越优先)
        handlers = sorted(handlers, key=lambda h: h.priority)

        self.logger.info(f"发布事件 {event_name} (来源: {source}, 监听器: {len(handlers)})")

        # 更新统计
        if self.enable_stats:
            self._stats[event_name].emit_count += 1
            self._stats[event_name].last_emit_time = time.time()
            self._stats[event_name].listener_count = len(handlers)

        start_time = time.time()

        # 并发执行所有处理器
        tasks = []
        for wrapper in handlers:
            task = asyncio.create_task(self._call_handler(wrapper, event_name, data, source, error_isolate))
            tasks.append(task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # 更新统计
        if self.enable_stats:
            execution_time = (time.time() - start_time) * 1000
            self._stats[event_name].total_execution_time_ms += execution_time

    async def _call_handler(
        self, wrapper: HandlerWrapper, event_name: str, data: Any, source: str, error_isolate: bool
    ):
        """
        调用事件处理器

        Args:
            wrapper: 处理器包装器
            event_name: 事件名称
            data: 事件数据
            source: 事件源
            error_isolate: 是否隔离错误
        """
        try:
            if asyncio.iscoroutinefunction(wrapper.handler):
                await wrapper.handler(event_name, data, source)
            else:
                # 同步处理器在线程池中执行
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, wrapper.handler, event_name, data, source)
        except Exception as e:
            wrapper.error_count += 1
            wrapper.last_error = str(e)

            if error_isolate:
                self.logger.error(f"事件处理器执行错误 (事件: {event_name}, 来源: {source}): {e}", exc_info=True)
                # 更新统计
                if self.enable_stats:
                    self._stats[event_name].error_count += 1
                    self._stats[event_name].last_error_time = time.time()
            else:
                raise

    def on(self, event_name: str, handler: Callable, priority: int = 100) -> None:
        """
        订阅事件

        Args:
            event_name: 要监听的事件名称
            handler: 事件处理器函数
            priority: 优先级(数字越小越优先,默认100)
        """
        wrapper = HandlerWrapper(handler=handler, priority=priority)
        self._handlers[event_name].append(wrapper)
        self.logger.debug(f"注册事件监听器: {event_name} -> {handler.__name__} (优先级: {priority})")

    def off(self, event_name: str, handler: Callable) -> None:
        """
        取消订阅

        Args:
            event_name: 事件名称
            handler: 要移除的事件处理器函数
        """
        handlers = self._handlers.get(event_name, [])
        for i, wrapper in enumerate(handlers):
            if wrapper.handler == handler:
                handlers.pop(i)
                self.logger.debug(f"移除事件监听器: {event_name} -> {handler.__name__}")
                break

        # 如果该事件没有监听器了，删除该条目
        if not handlers:
            del self._handlers[event_name]

    def clear(self) -> None:
        """
        清除所有事件监听器和统计信息
        """
        self._handlers.clear()
        self._stats.clear()
        self.logger.info("已清除所有事件监听器和统计信息")

    async def cleanup(self):
        """
        清理EventBus

        标记为清理中，并清除所有监听器和统计信息。
        """
        self._is_cleanup = True
        await asyncio.sleep(0.1)  # 等待处理的事件完成
        self.clear()
        self.logger.info("EventBus已清理")

    def get_listeners_count(self, event_name: str) -> int:
        """
        获取指定事件的监听器数量

        Args:
            event_name: 事件名称

        Returns:
            监听器数量
        """
        return len(self._handlers.get(event_name, []))

    def list_events(self) -> List[str]:
        """
        列出所有已注册的事件

        Returns:
            事件名称列表
        """
        return list(self._handlers.keys())

    def get_stats(self, event_name: str) -> Optional[EventStats]:
        """
        获取事件统计信息

        Args:
            event_name: 事件名称

        Returns:
            事件统计信息(如果启用统计)
        """
        if not self.enable_stats:
            return None
        return self._stats.get(event_name)

    def get_all_stats(self) -> Dict[str, EventStats]:
        """
        获取所有事件统计信息

        Returns:
            所有事件统计信息
        """
        if not self.enable_stats:
            return {}
        return dict(self._stats)

    def reset_stats(self, event_name: Optional[str] = None):
        """
        重置统计信息

        Args:
            event_name: 事件名称，如果为None则重置所有
        """
        if event_name:
            self._stats[event_name] = EventStats()
        else:
            self._stats.clear()
