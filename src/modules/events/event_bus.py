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

    # 普通订阅（接收字典）
    def handle_command(event_name: str, data: dict, source: str):
        command = data.get("command")
        logger.debug(f"Received: {command}")

    event_bus.on("command_router.received", handle_command)

    # 类型化订阅（接收 Pydantic Model 对象）
    async def handle_command_typed(event_name: str, data: CommandRouterData, source: str):
        command = data.command  # IDE 可以自动提示
        logger.debug(f"Received: {command}")

    event_bus.on("command_router.received", handle_command_typed, model_class=CommandRouterData)
"""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from src.modules.events.names import CoreEvents
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
    - original_handler: 原始处理器函数（用于 on_typed 的取消订阅）
    """

    handler: Callable
    priority: int = 100
    error_count: int = 0
    last_error: Optional[str] = None
    original_handler: Optional[Callable] = None  # 用于 on_typed，存储用户提供的原始处理器


class EventBus:
    """
    增强的事件总线

    核心功能:
    - 发布/订阅模式
    - 错误隔离(单个handler异常不影响其他)
    - 优先级控制(按priority排序执行)
    - 统计功能(跟踪emit、错误、执行时间)
    - 生命周期管理(cleanup方法)
    - 请求-响应模式(通过request方法)
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
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._active_emits: Dict[str, asyncio.Event] = {}  # 跟踪活跃的 emit 操作
        self.logger = get_logger("EventBus")
        self.logger.debug(f"EventBus 初始化完成 (stats={enable_stats}, validation=enabled)")

    def _short_event_name(self, event_name: str) -> str:
        """
        将事件名称转换为简短形式

        映射规则：
        - data.raw -> data
        - data.message -> message
        - decision.intent -> intent
        - output.params -> params

        Args:
            event_name: 完整事件名称（如 "data.raw.generated"）

        Returns:
            简短名称（如 "raw"）
        """
        parts = event_name.split(".")
        if len(parts) >= 2:
            # 返回第二部分（例如：data.raw -> raw, decision.intent -> intent）
            return parts[1]
        return event_name

    async def emit(self, event_name: str, data: BaseModel, source: str = "unknown", error_isolate: bool = True) -> None:
        """
        发布类型安全的事件

        Args:
            event_name: 事件名称
            data: Pydantic Model 实例（自动序列化为 dict）
            source: 事件源（通常是发布者的类名）
            error_isolate: 是否隔离错误（True 时单个 handler 异常不影响其他）

        Raises:
            TypeError: 如果 data 不是 BaseModel 实例
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

        # 打印事件信息（INFO 级别，简洁格式）
        # 为 RawDataPayload 添加特殊处理，提取用户名
        short_name = self._short_event_name(event_name)

        # 动态导入 RawDataPayload 进行类型检查（避免循环导入）
        try:
            from src.modules.events.payloads.input import RawDataPayload

            is_raw_data = isinstance(data, RawDataPayload)
        except ImportError:
            is_raw_data = False

        if is_raw_data:
            # RawDataPayload: 从 content 中提取用户名
            if isinstance(data.content, dict):
                user_name = data.content.get("user_name", "")
                text = data.content.get("text", str(data.content))
            else:
                user_name = ""
                text = str(data.content)

            # 格式化用户名
            user_part = f" [{user_name}]" if user_name else ""

            # 截断长文本
            if len(text) > 50:
                text = text[:47] + "..."

            # 格式: [event] source [user_name]: text
            self.logger.info(f"[{short_name}] {data.source}{user_part}: {text}")
        else:
            # 其他 Payload 使用默认格式
            self.logger.info(f"[{short_name}] {source}: {data}")

        # 更新统计
        if self.enable_stats:
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
                    await asyncio.gather(*tasks, return_exceptions=True)

                # 更新统计
                if self.enable_stats:
                    execution_time = (time.time() - start_time) * 1000
                    self._stats[event_name].total_execution_time_ms += execution_time
            finally:
                # 标记完成并从活跃列表中移除
                complete_event.set()
                self._active_emits.pop(emit_id, None)

        # 在后台任务中执行
        asyncio.create_task(emit_with_tracking())

    async def emit_sync(
        self, event_name: str, data: BaseModel, source: str = "unknown", error_isolate: bool = True
    ) -> None:
        """
        同步版本的事件发布，主要用于测试场景

        与 emit() 的区别：
        - emit(): 后台执行，不等待监听器完成（生产环境推荐）
        - emit_sync(): 等待所有监听器执行完成（测试环境使用）

        Args:
            event_name: 事件名称
            data: 事件数据（必须是 Pydantic BaseModel）
            source: 事件源标识
            error_isolate: 是否隔离错误（True 时单个监听器错误不影响其他）
        """
        # === 1. 事件名称验证 ===
        if not isinstance(event_name, str):
            raise TypeError(f"事件名称必须是字符串，当前类型: {type(event_name)}")

        if not event_name:
            raise ValueError("事件名称不能为空")

        if event_name not in CoreEvents.ALL_EVENTS:
            self.logger.warning(
                f"发布的事件 '{event_name}' 不在 CoreEvents 定义中。"
                f"请使用对应的事件 Payload 类（如 src.core.events.payloads 中定义的类）"
            )

        # === 2. 数据序列化 ===
        dict_data = data.model_dump()

        # === 3. 数据验证 ===
        if self.enable_validation:
            self._validate_event_data(event_name, dict_data)

        handlers = self._handlers.get(event_name, [])
        if not handlers:
            self.logger.debug(f"事件 {event_name} 没有监听器")
            return

        # 按优先级排序（数字越小越优先）
        handlers = sorted(handlers, key=lambda h: h.priority)

        # 打印事件信息（INFO 级别，简洁格式）
        short_name = self._short_event_name(event_name)
        self.logger.info(f"[{short_name}] {source}: {data}")

        # === 4. 更新统计 ===
        if self.enable_stats:
            self._stats[event_name].emit_count += 1
            self._stats[event_name].last_emit_time = time.time()
            self._stats[event_name].listener_count = len(handlers)

        start_time = time.time()

        # === 5. 创建跟踪事件（复用现有机制）===
        complete_event = asyncio.Event()
        emit_id = f"{event_name}_{id(complete_event)}"
        self._active_emits[emit_id] = complete_event

        try:
            # === 6. 并发执行所有处理器 ===
            tasks = []
            for wrapper in handlers:
                task = asyncio.create_task(self._call_handler(wrapper, event_name, dict_data, source, error_isolate))
                tasks.append(task)

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            # === 7. 更新统计 ===
            if self.enable_stats:
                execution_time = (time.time() - start_time) * 1000
                self._stats[event_name].total_execution_time_ms += execution_time
        finally:
            # 标记完成并从活跃列表中移除
            complete_event.set()
            self._active_emits.pop(emit_id, None)

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

    def on(
        self, event_name: str, handler: Callable, model_class: Optional[Type[T]] = None, priority: int = 100
    ) -> None:
        """
        订阅事件（统一API）

        Args:
            event_name: 要监听的事件名称
            handler: 事件处理器函数
            model_class: (可选) 期望的数据模型类型（必须是 BaseModel 子类）
                         如果提供，EventBus 会自动将字典数据反序列化为该类型
            priority: 优先级(数字越小越优先,默认100)

        Example:
            ```python
            # 普通订阅（接收字典）
            event_bus.on("event.name", handler)

            # 类型化订阅（接收 Pydantic Model 对象）
            event_bus.on("event.name", handler, model_class=MessageReadyPayload)
            ```
        """
        if model_class is not None:
            return self._on_typed_impl(event_name, handler, model_class, priority)
        else:
            return self._on_raw_impl(event_name, handler, priority)

    def on_typed(self, event_name: str, handler: Callable, model_class: Type[T], priority: int = 100) -> None:
        """
        订阅类型化事件（便捷方法）

        这是 on() 方法的便捷版本，专门用于类型化订阅。
        等价于：event_bus.on(event_name, handler, model_class=model_class)

        Args:
            event_name: 要监听的事件名称
            handler: 事件处理器函数
            model_class: 期望的数据模型类型（必须是 BaseModel 子类）
            priority: 优先级(数字越小越优先,默认100)

        Example:
            ```python
            event_bus.on_typed("event.name", handler, MessageReadyPayload)
            ```
        """
        # 委托给 on() 方法，确保经过架构验证器
        return self.on(event_name, handler, model_class=model_class, priority=priority)

    def _on_raw_impl(self, event_name: str, handler: Callable, priority: int) -> None:
        """普通订阅实现"""
        wrapper = HandlerWrapper(handler=handler, priority=priority)
        self._handlers[event_name].append(wrapper)
        self.logger.debug(f"注册事件监听器: {event_name} -> {handler.__name__} (优先级: {priority})")

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
                self.logger.error(f"类型化事件反序列化失败 ({event_name}): {e}", exc_info=True)
            except Exception as e:
                self.logger.error(f"类型化事件处理器执行错误 ({event_name}): {e}", exc_info=True)
                raise

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

    async def cleanup(self):
        """
        清理EventBus

        标记为清理中，等待所有活跃的 emit 完成，然后清除所有监听器和统计信息。
        """
        self._is_cleanup = True

        # 取消所有待处理的请求
        for future in self._pending_requests.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()

        # 等待所有活跃的 emit 完成
        if self._active_emits:
            active_count = len(self._active_emits)
            self.logger.info(f"等待 {active_count} 个活跃的 emit 完成...")
            try:
                tasks = [event.wait() for event in self._active_emits.values()]
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=5.0)
                self.logger.info(f"所有 {active_count} 个 emit 已完成")
            except asyncio.TimeoutError:
                self.logger.warning(f"等待 emit 完成超时（5秒），仍有 {len(self._active_emits)} 个活跃")
            except Exception as e:
                self.logger.error(f"等待 emit 完成时发生错误: {e}", exc_info=True)

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

    async def request(self, event_name: str, data: BaseModel, timeout: float = 5.0) -> Any:
        """
        请求-响应模式（带超时）

        通过 EventBus 发送请求并等待响应。响应事件名格式为 "{event_name}.response.{uuid}"。

        Args:
            event_name: 请求事件名称
            data: 请求数据（Pydantic Model）
            timeout: 超时时间（秒），默认 5.0

        Returns:
            响应数据

        Raises:
            asyncio.TimeoutError: 请求超时
            TypeError: 如果 data 不是 BaseModel 实例
        """
        if self._is_cleanup:
            self.logger.warning(f"EventBus正在清理中，忽略请求: {event_name}")
            return None

        response_event = f"{event_name}.response.{uuid4()}"
        future = asyncio.Future()

        def handler(name, event_data, source):
            future.set_result(event_data)

        self.on(response_event, handler)
        self._pending_requests[response_event] = future

        try:
            await self.emit(event_name, data, source="EventBus.request")
            return await asyncio.wait_for(future, timeout)
        finally:
            self.off(response_event, handler)
            self._pending_requests.pop(response_event, None)
