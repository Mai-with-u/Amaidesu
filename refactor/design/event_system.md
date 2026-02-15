# 事件系统变化

本文档详细对比重构前后的事件系统设计，解释新架构如何通过 EventBus 实现组件解耦。

## 事件系统总览

### 旧架构：可选的 EventBus

```
┌─────────────────────────────────────────────────────────────────┐
│                        AmaidesuCore                             │
│                                                                │
│  ┌────────────────┐     ┌────────────────┐                    │
│  │ 消息处理器注册  │     │ HTTP 处理器注册 │                    │
│  │ _message_handlers│    │_http_request_handlers│              │
│  └────────────────┘     └────────────────┘                    │
│                                                                │
│  ┌────────────────────────────────────────────────┐            │
│  │              EventBus (可选)                    │            │
│  │       event_bus.on() / event_bus.emit()        │            │
│  └────────────────────────────────────────────────┘            │
│                      ↑ 可能不存在                               │
└─────────────────────────────────────────────────────────────────┘
```

**特点**：
- EventBus 是可选功能
- 主要通过 `register_websocket_handler()` 分发消息
- 事件名使用字符串硬编码

### 新架构：强制的 EventBus

```
┌─────────────────────────────────────────────────────────────────┐
│                          EventBus                               │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │                   CoreEvents 枚举                        │  │
│   │   DATA_MESSAGE = "data.message"                         │  │
│   │   DECISION_INTENT = "decision.intent"                   │  │
│   │   OUTPUT_INTENT = "output.intent"                       │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                 │
│   ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  │
│   │ InputProvider  │  │DecisionProvider│  │ OutputProvider │  │
│   │    发布事件    │  │   订阅/发布    │  │    订阅事件    │  │
│   └────────────────┘  └────────────────┘  └────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**特点**：
- EventBus 是唯一的跨域通信机制
- 使用 `CoreEvents` 枚举避免拼写错误
- 事件与数据类型绑定

## EventBus 实现对比

### 旧架构实现

```python
# event_bus.py
class EventBus:
    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = defaultdict(list)

    def on(self, event_name: str, handler: Callable):
        """注册事件处理器"""
        self._handlers[event_name].append(handler)

    def off(self, event_name: str, handler: Callable):
        """移除事件处理器"""
        if handler in self._handlers[event_name]:
            self._handlers[event_name].remove(handler)

    async def emit(self, event_name: str, data: Any, source: str):
        """发布事件"""
        handlers = self._handlers.get(event_name, [])
        tasks = [asyncio.create_task(h(event_name, data, source))
                 for h in handlers]
        await asyncio.gather(*tasks, return_exceptions=True)
```

**问题**：
- 无类型安全
- 事件名使用字符串
- 无优先级支持
- 无统计功能

### 新架构实现

```python
# modules/events/event_bus.py
class EventBus:
    def __init__(self):
        self._handlers: Dict[str, List[HandlerInfo]] = {}
        self._stats = EventBusStats()

    async def subscribe(
        self,
        event_name: str,
        handler: Callable,
        priority: int = 0,
    ) -> str:
        """订阅事件，支持优先级"""
        handler_id = str(uuid.uuid4())
        handler_info = HandlerInfo(
            id=handler_id,
            handler=handler,
            priority=priority,
        )

        if event_name not in self._handlers:
            self._handlers[event_name] = []
        self._handlers[event_name].append(handler_info)
        # 按优先级排序
        self._handlers[event_name].sort(key=lambda x: x.priority)

        return handler_id

    async def emit(self, event_name: str, data: Any) -> None:
        """发布事件"""
        self._stats.record_emit(event_name)

        handlers = self._handlers.get(event_name, [])
        for handler_info in handlers:
            try:
                await handler_info.handler(data)
                self._stats.record_success(event_name)
            except Exception as e:
                self._stats.record_error(event_name, e)
                logger.error(f"事件处理错误: {event_name}", exc_info=True)
```

**改进**：
- 支持优先级
- 错误隔离
- 统计功能
- 返回订阅 ID 用于取消

## 事件名定义对比

### 旧架构：字符串硬编码

```python
# 插件中直接使用字符串
await self.event_bus.emit("custom_event", data, self.__class__.__name__)
self.event_bus.on("custom_event", self.handler)

# 容易拼写错误
await self.event_bus.emit("custome_event", data, ...)  # 拼写错误，运行时才发现
```

### 新架构：枚举常量

```python
# modules/events/names.py
class CoreEvents:
    """核心事件名称常量"""
    DATA_MESSAGE = "data.message"
    DECISION_INTENT = "decision.intent"
    OUTPUT_INTENT = "output.intent"  # 过滤后的 Intent

    # Provider 事件
    INPUT_PROVIDER_CONNECTED = "input.provider.connected"
    INPUT_PROVIDER_DISCONNECTED = "input.provider.disconnected"
    OUTPUT_PROVIDER_CONNECTED = "output.provider.connected"
    OUTPUT_PROVIDER_DISCONNECTED = "output.provider.disconnected"

# 使用枚举
await self.event_bus.emit(CoreEvents.DATA_MESSAGE, message)
await self.event_bus.subscribe(CoreEvents.DECISION_INTENT, handler)
```

**改进**：
- 编译时检查
- IDE 自动补全
- 避免拼写错误

## 核心事件对比

### 旧架构事件

旧架构没有标准化的事件定义，各插件自行定义事件名：

```python
# 插件 A
await self.event_bus.emit("text_processed", data, "PluginA")

# 插件 B
self.event_bus.on("text_process", handler)  # 拼写不一致

# 插件 C
await self.event_bus.emit("TTS_START", data, "PluginC")  # 风格不一致
```

### 新架构事件

新架构定义了核心事件流：

| 事件 | 常量 | 发布者 | 订阅者 | 数据类型 |
|------|------|--------|--------|---------|
| 标准化消息 | `DATA_MESSAGE` | InputProvider | DecisionProvider | `NormalizedMessage` |
| 决策意图 | `DECISION_INTENT` | DecisionProvider | OutputProviderManager | `Intent` |
| 过滤后意图 | `OUTPUT_INTENT` | OutputProviderManager | OutputProvider | `Intent` |
| 决策Provider连接 | `DECISION_PROVIDER_CONNECTED` | DecisionProviderManager | 任意 | - |
| 决策Provider断开 | `DECISION_PROVIDER_DISCONNECTED` | DecisionProviderManager | 任意 | - |
| 输入Provider连接 | `INPUT_PROVIDER_CONNECTED` | InputProviderManager | 任意 | - |
| 输入Provider断开 | `INPUT_PROVIDER_DISCONNECTED` | InputProviderManager | 任意 | - |
| 输出Provider连接 | `OUTPUT_PROVIDER_CONNECTED` | OutputProviderManager | 任意 | - |
| 输出Provider断开 | `OUTPUT_PROVIDER_DISCONNECTED` | OutputProviderManager | 任意 | - |

## 事件流对比

### 旧架构事件流

```
插件 A 发送消息
    ↓
AmaidesuCore.send_to_maicore()
    ↓
WebSocket → MaiCore
    ↓
WebSocket ← MaiCore
    ↓
AmaidesuCore._handle_maicore_message()
    ↓
分发到注册的 WebSocket 处理器
    ↓
插件 B 处理
    ↓
(可选) EventBus.emit("custom_event")
    ↓
(可选) 其他插件接收
```

**问题**：
- 事件与消息分发混合
- EventBus 只是可选的补充

### 新架构事件流

```
InputProvider 生成 NormalizedMessage
    ↓
EventBus.emit(DATA_MESSAGE)
    ↓
DecisionProvider 处理
    ↓
DecisionProvider 生成 Intent
    ↓
EventBus.emit(DECISION_INTENT)
    ↓
OutputProvider 处理
    ↓
(可选) EventBus.emit(自定义事件)
```

**改进**：
- EventBus 是主要通信机制
- 事件流清晰
- 类型安全

## 订阅机制对比

### 旧架构订阅

```python
class MyPlugin(BasePlugin):
    async def setup(self):
        # EventBus 可能为 None
        if self.event_bus:
            self.event_bus.on("my_event", self.handler)

    async def cleanup(self):
        # 需要手动移除
        if self.event_bus:
            self.event_bus.off("my_event", self.handler)
```

**问题**：
- 需要检查 EventBus 是否存在
- 手动管理订阅
- 无取消订阅的便捷方法

### 新架构订阅

```python
class MyProvider(OutputProvider):
    async def _setup_internal(self):
        # EventBus 通过依赖注入，保证存在
        self._subscription_id = await self.event_bus.subscribe(
            CoreEvents.DECISION_INTENT,
            self._handle_intent,
            priority=10,  # 可选优先级
        )

    async def _cleanup_internal(self):
        # 自动取消订阅
        if self._subscription_id:
            await self.event_bus.unsubscribe(self._subscription_id)
```

**改进**：
- EventBus 通过依赖注入
- 返回订阅 ID
- 支持优先级

## 错误处理对比

### 旧架构错误处理

```python
async def emit(self, event_name: str, data: Any, source: str):
    handlers = self._handlers.get(event_name, [])
    tasks = [asyncio.create_task(h(event_name, data, source))
             for h in handlers]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # 错误被静默吞掉，需要手动检查 results
```

### 新架构错误处理

```python
async def emit(self, event_name: str, data: Any) -> None:
    handlers = self._handlers.get(event_name, [])
    for handler_info in handlers:
        try:
            await handler_info.handler(data)
            self._stats.record_success(event_name)
        except Exception as e:
            self._stats.record_error(event_name, e)
            logger.error(
                f"事件处理器错误: {event_name}, handler: {handler_info.id}",
                exc_info=True
            )
            # 单个处理器错误不影响其他处理器
```

**改进**：
- 错误隔离
- 记录统计
- 详细的错误日志

## 统计功能

新架构添加了事件统计功能：

```python
class EventBusStats:
    def __init__(self):
        self._emit_counts: Dict[str, int] = defaultdict(int)
        self._success_counts: Dict[str, int] = defaultdict(int)
        self._error_counts: Dict[str, int] = defaultdict(int)
        self._last_errors: Dict[str, List[ExceptionInfo]] = {}

    def record_emit(self, event_name: str):
        self._emit_counts[event_name] += 1

    def record_success(self, event_name: str):
        self._success_counts[event_name] += 1

    def record_error(self, event_name: str, error: Exception):
        self._error_counts[event_name] += 1
        self._last_errors[event_name].append(ExceptionInfo(
            time=time.time(),
            error=error,
        ))

    def get_stats(self, event_name: str) -> EventStats:
        return EventStats(
            emit_count=self._emit_counts[event_name],
            success_count=self._success_counts[event_name],
            error_count=self._error_counts[event_name],
            last_errors=self._last_errors[event_name][-10:],
        )
```

## 迁移建议

### 事件名迁移

```python
# 旧代码
await self.event_bus.emit("text_processed", data, "MyPlugin")

# 新代码
from src.modules.events.names import CoreEvents

await self.event_bus.emit(CoreEvents.DATA_MESSAGE, message)
```

### 订阅迁移

```python
# 旧代码
class MyPlugin(BasePlugin):
    async def setup(self):
        if self.event_bus:
            self.event_bus.on("my_event", self.handler)

# 新代码
class MyProvider(OutputProvider):
    async def _setup_internal(self):
        self._sub_id = await self.event_bus.subscribe(
            CoreEvents.DECISION_INTENT,
            self._handle_intent,
        )
```

### 自定义事件

如果需要自定义事件，建议在 `modules/events/names.py` 中定义：

```python
class CoreEvents:
    # ... 现有事件 ...

    # 自定义事件
    MY_CUSTOM_EVENT = "custom.my_event"
```

---

*最后更新：2026-02-15*
