from typing import Any, Callable, Dict, List, Optional
import asyncio
import time
from collections import defaultdict
from src.utils.logger import get_logger


class EventBus:
    """简单的事件总线实现，专门用于插件间通信"""

    def __init__(self):
        """初始化事件总线"""
        self._handlers: Dict[str, List[Callable]] = defaultdict(list)
        self.logger = get_logger("EventBus")
        self.logger.debug("EventBus 初始化完成")

    async def emit(self, event_name: str, data: Any, source: str = "unknown") -> None:
        """
        发布事件

        Args:
            event_name: 事件名称
            data: 事件数据
            source: 事件源（通常是发布者的类名）
        """
        handlers = self._handlers.get(event_name, [])
        if not handlers:
            self.logger.info(f"事件 {event_name} 没有监听器")
            return

        self.logger.info(f"发布事件 {event_name} (来源: {source}, 监听器: {len(handlers)})")

        # 并发执行所有处理器
        tasks = []
        for handler in handlers:
            task = asyncio.create_task(self._call_handler(handler, event_name, data, source))
            tasks.append(task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _call_handler(self, handler: Callable, event_name: str, data: Any, source: str):
        """
        调用事件处理器

        Args:
            handler: 事件处理器函数
            event_name: 事件名称
            data: 事件数据
            source: 事件源
        """
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event_name, data, source)
            else:
                # 同步处理器在线程池中执行
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, handler, event_name, data, source)
        except Exception as e:
            self.logger.error(f"事件处理器执行错误 (事件: {event_name}, 来源: {source}): {e}", exc_info=True)

    def on(self, event_name: str, handler: Callable) -> None:
        """
        订阅事件

        Args:
            event_name: 要监听的事件名称
            handler: 事件处理器函数
        """
        self._handlers[event_name].append(handler)
        self.logger.debug(f"注册事件监听器: {event_name} -> {handler.__name__}")

    def off(self, event_name: str, handler: Callable) -> None:
        """
        取消订阅

        Args:
            event_name: 事件名称
            handler: 要移除的事件处理器函数
        """
        handlers = self._handlers.get(event_name, [])
        if handler in handlers:
            handlers.remove(handler)
            self.logger.debug(f"移除事件监听器: {event_name} -> {handler.__name__}")

            # 如果该事件没有监听器了，删除该条目
            if not handlers:
                del self._handlers[event_name]

    def clear(self) -> None:
        """清除所有事件监听器"""
        self._handlers.clear()
        self.logger.info("已清除所有事件监听器")

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