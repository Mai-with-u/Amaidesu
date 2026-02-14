"""
增强的事件总线实现

增加了以下功能:
- 错误隔离机制(单个handler异常不影响其他)
- 优先级控制(handler可设置priority,数字越小越优先)
- 统计功能(emit/on调用计数、错误率、执行时间)
- 生命周期管理(cleanup方法)
- 类型化订阅支持(通过 model_class 参数自动反序列化)

类型化订阅使用示例:
    from src.modules.events.payloads import CommandRouterData

    # 类型化订阅（接收 Pydantic Model 对象）
    async def handle_command_typed(event_name: str, data: CommandRouterData, source: str):
        command = data.command  # IDE 可以自动提示
        logger.debug(f"Received: {command}")

    event_bus.on("command_router.received", handle_command_typed, model_class=CommandRouterData)
"""

import asyncio
import copy
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from src.modules.events.registry import EventRegistry
from src.modules.logging import get_logger

T = TypeVar("T", bound=BaseModel)


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
    - handler: 处理器函数
    - priority: 优先级(数字越小越优先)
    - error_count: 错误次数
    - last_error: 最后错误信息
    - original_handler: 原始处理器函数（用于取消订阅）
    """

    handler: Callable
    priority: int = 100
    error_count: int = 0
    last_error: Optional[str] = None
    original_handler: Optional[Callable] = None  # 存储用户提供的原始处理器


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
        self.enable_validation = True  # 固定开启验证
        self._is_cleanup = False
        self._active_emits: Dict[str, asyncio.Event] = {}  # 跟踪活跃的 emit 操作
        self._background_tasks: set = set()  # 跟踪后台任务
        self._stats_lock = asyncio.Lock()  # 保护统计数据的并发访问
        self.logger = get_logger("EventBus")
        self.logger.debug(f"EventBus 初始化完成 (stats={enable_stats}, validation=enabled)")

    def _format_event_log(self, event_name: str, data: BaseModel, source: str) -> str:
        """格式化事件日志"""
        # 尝试使用 Payload 的 get_log_format() 方法
        if hasattr(data, "get_log_format"):
            result = data.get_log_format()
            if result is not None:
                text, user_name, extra = result
                user_part = f" [{user_name}]" if user_name else ""
                extra_part = f" {extra}" if extra else ""
                return f"[{event_name}] {data.source}{user_part}: {text}{extra_part}"

        # 默认格式
        return f"[{event_name}] {source}: {data}"

    async def emit(
        self, event_name: str, data: BaseModel, source: str = "unknown", error_isolate: bool = True, wait: bool = False
    ) -> None:
        """
        发布类型安全的事件

        Args:
            event_name: 事件名称
            data: Pydantic Model 实例（自动序列化为 dict）
            source: 事件源（通常是发布者的类名）
            error_isolate: 错误隔离策略
                - True: 错误被隔离并记录，单个 handler 异常不会影响其他 handler 的执行
                - False: 第一个异常会传播到调用者，中断所有 handler 的执行
            wait: 是否等待所有监听器执行完成
                - False: 在后台任务中执行，不等待完成（默认）
                - True: 等待所有监听器执行完成后再返回

        Raises:
            TypeError: 如果 data 不是 BaseModel 实例
            Exception: 当 error_isolate=False 且处理器执行出错时抛出
        """
        if self._is_cleanup:
            self.logger.warning(f"EventBus正在清理中，忽略事件: {event_name}")
            return

        # 强制类型检查
        if not isinstance(data, BaseModel):
            raise TypeError(
                f"EventBus.emit() 要求 data 参数必须是 Pydantic BaseModel 实例，"
                f"收到类型: {type(data).__name__}。"
                f"请使用对应的事件 Payload 类（如 src.core.events.payloads 中定义的类）"
            )

        # 将 Pydantic Model 序列化为 dict
        dict_data = data.model_dump()

        # === 数据验证 ===
        if self.enable_validation:
            self._validate_event_data(event_name, dict_data)

        handlers = self._handlers.get(event_name, [])
        if not handlers:
            self.logger.debug(f"事件 {event_name} 没有监听器")
            return

        # 按优先级排序（数字越小越优先）
        handlers = sorted(handlers, key=lambda h: h.priority)

        # 打印事件信息（INFO 级别）
        log_message = self._format_event_log(event_name, data, source)
        self.logger.info(log_message)

        # 更新统计（使用锁保护）
        if self.enable_stats:
            async with self._stats_lock:
                self._stats[event_name].emit_count += 1
                self._stats[event_name].last_emit_time = time.time()
                self._stats[event_name].listener_count = len(handlers)

        start_time = time.time()

        # 创建跟踪事件
        complete_event = asyncio.Event()
        emit_id = f"{event_name}_{id(complete_event)}"
        self._active_emits[emit_id] = complete_event

        # 定义带跟踪的 emit 逻辑
        async def emit_with_tracking():
            try:
                # 并发执行所有处理器
                tasks = []
                for wrapper in handlers:
                    task = asyncio.create_task(
                        self._call_handler(wrapper, event_name, dict_data, source, error_isolate)
                    )
                    tasks.append(task)

                if tasks:
                    if error_isolate:
                        # 错误隔离模式：捕获所有异常，但不重新抛出
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        # 检查是否有异常（仅用于统计）
                        for result in results:
                            if isinstance(result, Exception):
                                # 异常已在 _call_handler 中处理，这里不需要额外操作
                                pass
                    else:
                        # 非隔离模式：让第一个异常传播到调用者
                        results = await asyncio.gather(*tasks, return_exceptions=False)
                        # 如果有异常，gather 会自动抛出，不需要额外处理

                # 更新统计（使用锁保护）
                if self.enable_stats:
                    execution_time = (time.time() - start_time) * 1000
                    async with self._stats_lock:
                        self._stats[event_name].total_execution_time_ms += execution_time
            finally:
                # 标记完成并从活跃列表中移除
                complete_event.set()
                self._active_emits.pop(emit_id, None)

        # 根据 wait 参数决定执行方式
        if wait:
            # 等待完成
            await emit_with_tracking()
        else:
            # 在后台任务中执行并跟踪
            task = asyncio.create_task(emit_with_tracking())
            self._background_tasks.add(task)
            task.add_done_callback(lambda t: self._background_tasks.discard(t))

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
            # 更新处理器级别的错误统计（不需要锁，因为每个 handler 独立）
            wrapper.error_count += 1
            wrapper.last_error = str(e)

            if error_isolate:
                self.logger.error(f"事件处理器执行错误 (事件: {event_name}, 来源: {source}): {e}", exc_info=True)
                # 更新统计（使用锁保护）
                if self.enable_stats:
                    async with self._stats_lock:
                        self._stats[event_name].error_count += 1
                        self._stats[event_name].last_error_time = time.time()
            else:
                raise

    def on(self, event_name: str, handler: Callable, model_class: Type[T], priority: int = 100) -> None:
        """
        订阅类型化事件

        EventBus 强制要求类型化订阅，所有订阅必须指定 model_class。

        Args:
            event_name: 要监听的事件名称
            handler: 事件处理器函数
            model_class: 期望的数据模型类型（必须是 BaseModel 子类）
                         EventBus 会自动将字典数据反序列化为该类型
            priority: 优先级(数字越小越优先,默认100)

        Example:
            ```python
            # 类型化订阅（接收 Pydantic Model 对象）
            event_bus.on("event.name", handler, model_class=MessageReadyPayload)
            ```
        """
        return self._on_typed_impl(event_name, handler, model_class, priority)

    def _on_typed_impl(self, event_name: str, handler: Callable, model_class: Type[T], priority: int) -> None:
        """类型化订阅实现（自动反序列化）"""
        # 注册事件类型到 EventRegistry
        try:
            EventRegistry.register_core_event(event_name, model_class)
        except ValueError:
            self.logger.debug(f"事件 '{event_name}' 不符合核心事件命名规范")

        # 创建包装器，自动反序列化
        async def typed_wrapper(event_name: str, dict_data: Dict[str, Any], source: str):
            try:
                typed_data = model_class.model_validate(dict_data)
                if asyncio.iscoroutinefunction(handler):
                    await handler(event_name, typed_data, source)
                else:
                    handler(event_name, typed_data, source)
            except ValidationError as e:
                # 验证错误：数据格式不匹配
                self.logger.error(
                    f"类型化事件数据验证失败 ({event_name}, 期望类型: {model_class.__name__}): {e}",
                    exc_info=False,  # 不需要完整堆栈，ValidationError 已包含详细信息
                )
            except Exception as e:
                # 处理器执行错误：记录但不传播（保持与其他处理器一致）
                self.logger.error(
                    f"类型化事件处理器执行错误 ({event_name}, 处理器: {handler.__name__}): {e}", exc_info=True
                )
                # 更新错误计数
                wrapper.error_count += 1
                wrapper.last_error = str(e)
                # 注意：这里不重新抛出异常，保持与 error_isolate=True 一致的行为
                # 如果需要传播异常，应该通过 error_isolate 参数控制

        wrapper = HandlerWrapper(handler=typed_wrapper, priority=priority, original_handler=handler)
        self._handlers[event_name].append(wrapper)
        self.logger.debug(
            f"注册类型化事件监听器: {event_name} -> {handler.__name__} "
            f"(类型: {model_class.__name__}, 优先级: {priority})"
        )

    def off(self, event_name: str, handler: Callable) -> None:
        """
        取消订阅

        Args:
            event_name: 事件名称
            handler: 要移除的事件处理器函数（可以是原始处理器或包装后的处理器）
        """
        handlers = self._handlers.get(event_name, [])
        for i, wrapper in enumerate(handlers):
            # 检查是否匹配（支持 on_typed 的原始处理器）
            if wrapper.handler == handler or wrapper.original_handler == handler:
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

    async def cleanup(self, timeout: float = 5.0, force: bool = False):
        """
        清理 EventBus

        Args:
            timeout: 等待活跃 emit 完成的超时时间（秒）
                     如果某些 emit 操作需要较长时间完成，可以增加此值
            force: 是否强制清理（即使有活跃任务）
                   - False: 等待所有活跃 emit 完成（默认）
                   - True: 即使有活跃 emit 也立即清理，可能中断正在进行的操作
        """
        self._is_cleanup = True

        # 等待所有活跃的 emit 完成
        if self._active_emits:
            active_count = len(self._active_emits)
            self.logger.info(f"等待 {active_count} 个活跃的 emit 完成...")
            try:
                tasks = [event.wait() for event in self._active_emits.values()]
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
                self.logger.info(f"所有 {active_count} 个 emit 已完成")
            except asyncio.TimeoutError:
                remaining = len(self._active_emits)
                if not force:
                    self.logger.error(
                        f"等待 emit 完成超时（{timeout}秒），仍有 {remaining} 个活跃。"
                        f"如需强制清理，请调用 cleanup(force=True)"
                    )
                    # 不继续清理，恢复状态
                    self._is_cleanup = False
                    return
                else:
                    self.logger.warning(f"等待 emit 完成超时（{timeout}秒），强制清理 {remaining} 个活跃任务")
            except Exception as e:
                self.logger.error(f"等待 emit 完成时发生错误: {e}", exc_info=True)

        # 后台任务处理
        if self._background_tasks:
            bg_count = len(self._background_tasks)
            self.logger.warning(f"cleanup 时仍有 {bg_count} 个后台任务未完成，可能被取消")
            try:
                await asyncio.wait_for(asyncio.gather(*self._background_tasks, return_exceptions=True), timeout=2.0)
                self.logger.info("所有后台任务已完成")
            except asyncio.TimeoutError:
                self.logger.warning("等待后台任务超时")

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
            事件统计信息的深拷贝(如果启用统计)，避免外部修改
        """
        if not self.enable_stats:
            return None
        stats = self._stats.get(event_name)
        if stats is None:
            return None
        # 返回深拷贝以避免外部修改影响内部数据
        return copy.deepcopy(stats)

    def get_all_stats(self) -> Dict[str, EventStats]:
        """
        获取所有事件统计信息

        Returns:
            所有事件统计信息的深拷贝字典，避免外部修改
        """
        if not self.enable_stats:
            return {}
        # 返回深拷贝以避免外部修改影响内部数据
        return {k: copy.deepcopy(v) for k, v in self._stats.items()}

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

    def _validate_event_data(self, event_name: str, data: Any) -> None:
        """
        验证事件数据

        策略：
        - 已注册事件：验证数据格式
        - 未注册事件：仅警告，不阻断
        """
        model = EventRegistry.get(event_name)

        if model is None:
            # 未注册事件
            if not event_name.startswith("plugin.") and not event_name.startswith("internal."):
                self.logger.debug(f"未注册的非插件事件: {event_name}")
            return

        # 已注册事件：验证数据
        try:
            if isinstance(data, BaseModel):
                # 已经是 Pydantic Model，跳过验证
                return
            elif isinstance(data, dict):
                # 字典数据，尝试验证
                model.model_validate(data)
            else:
                self.logger.warning(f"事件 {event_name} 数据类型不支持验证: {type(data).__name__}")
        except ValidationError as e:
            self.logger.warning(f"事件数据验证失败 ({event_name}): {e.error_count()} 个错误")
            for error in e.errors():
                self.logger.debug(f"  - {error['loc']}: {error['msg']}")
