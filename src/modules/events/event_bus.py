"""
事件总线（EventBus）——订阅广播通道

两条通道模型中的"广播"侧：把一条事件并发扇出给所有匹配的订阅者。
- 并发执行：同一事件的所有订阅者以独立任务并发运行，完成顺序不定
- 互不影响：一个订阅者抛异常会被计数并写 ERROR 日志，不影响其他订阅者
- 订阅者无顺序：不提供优先级或排序（有序处理属于拦截器管道，见 interceptors/）
- 只接受 async handler：同步函数在注册时被拒绝，避免阻塞事件循环

匹配规则：精确名订阅 + AMQP topic 风格通配（``*`` 恰好一层、``#`` 末尾多层），
详见 ``_is_wildcard_pattern`` 与 ``_match_wildcard``。

payload 契约：emit 只接受 Pydantic Model 实例。payload 类声明
``_DISCRIMINANT_FIELD`` 时（如 ``RoomMessagePayload.message_type``），emit 会
校验"事件名末段 == 判别字段值"，同族多注册的 payload 挂错事件名会直接报错。

类型化订阅使用示例:
    from src.modules.events.payloads import RoomMessagePayload, ToolResultPayload

    async def handle_danmaku(event_name: str, data: RoomMessagePayload, source: str):
        ...

    event_bus.on("room.message.danmaku", handle_danmaku, model_class=RoomMessagePayload)
    event_bus.on("tool.result.#", handle_any_tool_result, model_class=ToolResultPayload)
"""

import asyncio
import copy
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from src.modules.events.interceptors import EventInterceptor, InterceptorChain
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
    - error_count: 错误次数
    - last_error: 最后错误信息
    - original_handler: 原始处理器函数（用于取消订阅）
    """

    handler: Callable
    error_count: int = 0
    last_error: Optional[str] = None
    original_handler: Optional[Callable] = None  # 存储用户提供的原始处理器


class EventBus:
    """
    事件总线

    核心契约:
    - 发布/订阅模式，精确名 + AMQP topic 风格通配匹配
    - 订阅者并发执行、互不影响（异常计数 + 日志，不传播、不排序）
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
        # 拦截器链。默认空 ⇒ byte-identical 行为（emit 不调任何拦截器）
        self._interceptor_chain = InterceptorChain()
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

    @staticmethod
    def _is_wildcard_pattern(pattern: str) -> bool:
        """
        判断订阅模式是否为通配模式

        仅含字面量字符（含点号）的 pattern 视为精确匹配；含 ``*`` 或 ``#`` 时
        视为通配模式。仅在通配模式下才走 ``_match_wildcard`` 路径，避免对
        无通配订阅产生额外开销。
        """
        return "*" in pattern or "#" in pattern

    @staticmethod
    def _match_wildcard(pattern: str, event_name: str) -> bool:
        """
        AMQP topic 风格通配匹配

        - ``*`` 消耗**恰好一个** dot-separated token（单层）
        - ``#`` 仅在 pattern 末尾有效，消耗**≥0 个**剩余 token（多层，可匹配空）
        - 其它字面量 token 必须**逐字符相等**
        - 独立 ``#``（无前缀）匹配一切

        Examples:
            >>> EventBus._match_wildcard("room.*", "room.message")
            True
            >>> EventBus._match_wildcard("room.*", "room.message.danmaku")
            False
            >>> EventBus._match_wildcard("tool.result.#", "tool.result.speak")
            True
            >>> EventBus._match_wildcard("tool.result.#", "tool.result")
            True
            >>> EventBus._match_wildcard("tool.result.#", "tool.result.a.b.c")
            True
            >>> EventBus._match_wildcard("#", "anything.you.want")
            True
        """
        # 独立 #：匹配一切（包括空事件名；正常业务不会发布空名事件）
        if pattern == "#":
            return True

        pat_parts = pattern.split(".")
        evt_parts = event_name.split(".")

        # 先处理 # 后缀：pattern 必须以 # 结尾才有意义（前面已拦截单独 #）
        # 在 pat 末尾为 # 时，剩余 evt 全部视为被 # 消耗（含空）
        if pat_parts and pat_parts[-1] == "#":
            prefix = pat_parts[:-1]
            # 前缀必须按字面量匹配；前缀过长 ⇒ 必不可能匹配
            if len(prefix) > len(evt_parts):
                return False
            # 前缀短于或等于 evt 时，# 消耗剩余部分；只比对公共前缀段
            for p, e in zip(prefix, evt_parts[: len(prefix)], strict=False):
                if p == "*":
                    continue
                if p != e:
                    return False
            return True

        # 非 # 结尾：长度必须一致；逐段匹配（* 消耗 1 段）
        if len(pat_parts) != len(evt_parts):
            return False
        for p, e in zip(pat_parts, evt_parts, strict=True):
            if p == "*":
                continue
            if p != e:
                return False
        return True

    async def emit(self, event_name: str, data: BaseModel, source: str = "unknown") -> None:
        """
        发布事件（并发扇出，立即返回不等订阅者完成）

        Args:
            event_name: 事件名称
            data: Pydantic Model 实例（自动序列化为 dict）
            source: 事件源（通常是发布者的类名）

        Raises:
            TypeError: 如果 data 不是 BaseModel 实例
            ValueError: payload 类声明了 ``_DISCRIMINANT_FIELD`` 且事件名末段
                与判别字段值不一致

        分发流程：类型检查 → 判别校验 → ``model_dump()`` → 数据验证 →
        **拦截器链** → handler 并发分发。拦截器链任一环节显式返回 ``None``
        即丢弃事件：不更新统计、不调用任何 handler。handler 查找取精确键与
        所有通配 pattern 键的并集（按 HandlerWrapper 身份去重）；统计始终按
        真实 emit 的 event_name 入键（与通配 pattern 解耦）。
        """
        if self._is_cleanup:
            self.logger.warning(f"EventBus正在清理中，忽略事件: {event_name}")
            return

        # 强制类型检查
        if not isinstance(data, BaseModel):
            raise TypeError(
                f"EventBus.emit() 要求 data 参数必须是 Pydantic BaseModel 实例，"
                f"收到类型: {type(data).__name__}。"
                f"请使用对应的事件 Payload 类（如 src.modules.events.payloads 中定义的类）"
            )

        # 判别一致性校验：同族多注册 payload 的事件名末段必须等于判别字段值
        discriminant_field = getattr(data.__class__, "_DISCRIMINANT_FIELD", None)
        if discriminant_field is not None:
            tail = event_name.split(".")[-1]
            actual = getattr(data, discriminant_field, None)
            if actual != tail:
                raise ValueError(
                    f"事件名与判别字段不一致: 事件 '{event_name}' 末段为 '{tail}'，"
                    f"但 {data.__class__.__name__}.{discriminant_field}='{actual}'。"
                    f"同族 payload 的事件名末段必须等于判别字段值"
                )

        # 将 Pydantic Model 序列化为 dict
        dict_data = data.model_dump()

        # === 数据验证 ===
        if self.enable_validation:
            self._validate_event_data(event_name, dict_data)

        # === 拦截器链（在 handler 分发前；空链 ⇒ 字节级一致行为）===
        # 拦截器看到的是 model_dump() 后的 dict，与下游 handler 数据形态一致。
        if self._interceptor_chain:
            processed = await self._interceptor_chain.apply(event_name, dict_data, source)
            if processed is None:
                # 任一拦截器显式返回 None：丢弃事件，直接返回
                self.logger.debug(f"事件 {event_name} 被拦截器链丢弃（source={source}）")
                return
            dict_data = processed

        # 收集 handler：精确键 + 所有通配 pattern 键的并集（按身份去重）
        handlers = self._collect_handlers(event_name)
        if not handlers:
            self.logger.debug(f"事件 {event_name} 没有监听器")
            return

        # 打印事件信息（DEBUG 级别）
        log_message = self._format_event_log(event_name, data, source)
        self.logger.debug(log_message)

        # 更新统计（按真实 emit 的 event_name 入键，与通配 pattern 解耦）
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
                # 并发执行所有处理器；_call_handler 内部已捕获异常，
                # gather 仅作汇合点（return_exceptions 防御取消之外的漏网异常）
                tasks = [
                    asyncio.create_task(self._call_handler(wrapper, event_name, dict_data, source))
                    for wrapper in handlers
                ]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

                # 更新统计（使用锁保护）
                if self.enable_stats:
                    execution_time = (time.time() - start_time) * 1000
                    async with self._stats_lock:
                        self._stats[event_name].total_execution_time_ms += execution_time
            finally:
                # 标记完成并从活跃列表中移除
                complete_event.set()
                self._active_emits.pop(emit_id, None)

        # 在后台任务中执行并跟踪
        task = asyncio.create_task(emit_with_tracking())
        self._background_tasks.add(task)
        task.add_done_callback(lambda t: self._background_tasks.discard(t))

    def _collect_handlers(self, event_name: str) -> List[HandlerWrapper]:
        """
        收集事件的所有匹配 handler（精确键 + 通配 pattern 键并集）

        HandlerWrapper 按对象身份去重——同一 wrapper 被多个 pattern 引用时只取
        一次。返回顺序即注册顺序，无排序语义：订阅者并发执行，顺序无意义。
        """
        seen: Dict[int, HandlerWrapper] = {}

        # 精确键
        for wrapper in self._handlers.get(event_name, []):
            seen[id(wrapper)] = wrapper

        # 通配 pattern 键
        for pattern, handlers in self._handlers.items():
            if pattern == event_name:
                continue  # 已在精确键处理
            if not self._is_wildcard_pattern(pattern):
                continue  # 非通配且非精确键（防御）
            if not self._match_wildcard(pattern, event_name):
                continue
            for wrapper in handlers:
                seen[id(wrapper)] = wrapper

        return list(seen.values())

    async def _call_handler(self, wrapper: HandlerWrapper, event_name: str, data: Any, source: str) -> None:
        """
        调用事件处理器；异常在此处闭环：计数 + ERROR 日志，不向外传播

        Args:
            wrapper: 处理器包装器
            event_name: 事件名称
            data: 事件数据
            source: 事件源
        """
        try:
            await wrapper.handler(event_name, data, source)
        except Exception as e:
            # 处理器级别的错误记录（每个 handler 独立，无需锁）
            wrapper.error_count += 1
            wrapper.last_error = str(e)
            self.logger.error(f"事件处理器执行错误 (事件: {event_name}, 来源: {source}): {e}", exc_info=True)
            # 事件级统计（使用锁保护）
            if self.enable_stats:
                async with self._stats_lock:
                    self._stats[event_name].error_count += 1
                    self._stats[event_name].last_error_time = time.time()

    def on(self, event_name: str, handler: Callable, model_class: Type[T]) -> None:
        """
        订阅类型化事件

        EventBus 强制要求类型化订阅，所有订阅必须指定 model_class；
        handler 必须是协程函数（同步函数会阻塞事件循环，注册时直接拒绝）。

        Args:
            event_name: 要监听的事件名称
            handler: 事件处理器函数（必须为 async def）
            model_class: 期望的数据模型类型（必须是 BaseModel 子类）
                         EventBus 会自动将字典数据反序列化为该类型

        Raises:
            TypeError: handler 不是协程函数

        Example:
            ```python
            # 类型化订阅（接收 Pydantic Model 对象）
            event_bus.on("room.message.danmaku", handler, model_class=RoomMessagePayload)
            ```
        """
        if not asyncio.iscoroutinefunction(handler):
            raise TypeError(
                f"EventBus.on() 只接受 async handler，收到同步函数: {handler.__name__}。"
                f"同步处理会阻塞事件循环，请改为 async def"
            )

        # 创建包装器，自动反序列化。payload 验证失败在此处闭环（数据问题，
        # 记日志后放弃本条）；处理器执行异常原样冒泡，由 _call_handler 统一
        # 计数并写日志
        async def typed_wrapper(event_name: str, dict_data: Dict[str, Any], source: str):
            try:
                typed_data = model_class.model_validate(dict_data)
            except ValidationError as e:
                self.logger.error(
                    f"类型化事件数据验证失败 ({event_name}, 期望类型: {model_class.__name__}): {e}",
                    exc_info=False,  # 不需要完整堆栈，ValidationError 已包含详细信息
                )
                return
            await handler(event_name, typed_data, source)

        wrapper = HandlerWrapper(handler=typed_wrapper, original_handler=handler)
        self._handlers[event_name].append(wrapper)
        self.logger.debug(f"注册类型化事件监听器: {event_name} -> {handler.__name__} (类型: {model_class.__name__})")

    def off(self, event_name: str, handler: Callable) -> None:
        """
        取消订阅

        Args:
            event_name: 事件名称（或通配 pattern，如 ``tool.result.#``）
            handler: 要移除的事件处理器函数（可以是原始处理器或包装后的处理器）

        Notes:
            - 通配 pattern 订阅可直接 ``off("tool.result.#", handler)`` 移除
              （pattern 是 ``_handlers`` 的字典键，复用既有删除路径）
            - 同一 handler 注册到多个 pattern 时，``off`` 仅移除指定 pattern
              下的那条；其它 pattern 上的同 handler 引用需各自 ``off``
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
            self._handlers.pop(event_name, None)

    def add_interceptor(self, interceptor: "EventInterceptor") -> None:
        """
        注册一个事件拦截器

        拦截器对每次 ``emit`` 的事件，在数据验证后、handler 分发前被调用。
        可修改 payload（原地修改 / 返回新 dict）或显式返回 ``None`` 丢弃事件。

        默认空链 ⇒ ``emit`` 行为与未启用拦截器时字节级一致（``apply`` 短路）。

        Args:
            interceptor: 已实例化的 :class:`EventInterceptor` 子类
        """
        self._interceptor_chain.register(interceptor)
        self.logger.debug(f"EventBus 挂载拦截器: {interceptor.name}")

    def remove_interceptor(self, name: str) -> bool:
        """
        按 ``name`` 移除首个匹配的拦截器

        Args:
            name: ``EventInterceptor.name`` 标识

        Returns:
            是否实际移除（``False`` 表示未找到）
        """
        removed = self._interceptor_chain.unregister(name)
        if removed:
            self.logger.debug(f"EventBus 卸载拦截器: {name}")
        return removed

    def get_interceptor_names(self) -> List[str]:
        """
        返回当前已挂载拦截器名称列表（按执行顺序）

        主要用于测试与可观测性。
        """
        return [interceptor.name for interceptor in self._interceptor_chain._interceptors]  # noqa: SLF001

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
        return None if stats is None else copy.deepcopy(stats)

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
        提示未注册事件

        emit 的入参本身就是 Pydantic Model 实例（形状由类保证），此处不再对
        序列化结果做整类重复验证；仅对未注册事件写一条 debug 提示。
        """
        if EventRegistry.get(event_name) is None:
            self.logger.debug(f"未注册 Payload 的事件名 emit: {event_name}")
