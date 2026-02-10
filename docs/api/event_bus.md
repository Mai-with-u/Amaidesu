# EventBus API

## 概述

EventBus 是一个类型安全的发布-订阅事件总线，用于跨域通信（Input Domain、Decision Domain、Output Domain）。

**核心特性**：
- **类型安全**：强制要求 Pydantic BaseModel 作为事件数据
- **错误隔离**：单个处理器异常不影响其他处理器
- **优先级控制**：支持处理器优先级排序
- **统计功能**：跟踪事件发布、错误率、执行时间
- **生命周期管理**：支持优雅清理和等待活跃事件完成

## 快速开始

### 导入路径

```python
# EventBus 类
from src/modules/events/event_bus import EventBus

# 事件名称常量（避免硬编码字符串）
from src/modules/events/names import CoreEvents

# 事件 Payload 类型（Pydantic BaseModel）
from src/modules/events/payloads import (
    RawDataPayload,
    MessageReadyPayload,
    IntentPayload,
    # ... 更多 Payload 类型
)
```

### 基本用法

```python
from src/modules/events/event_bus import EventBus
from src/modules/events/names import CoreEvents
from src.modules.events.payloads import MessageReadyPayload

# 创建 EventBus 实例
event_bus = EventBus(enable_stats=True)

# 订阅事件（使用 on_typed 获取类型提示）
async def handle_message(event_name: str, payload: MessageReadyPayload, source: str):
    print(f"收到消息: {payload.message['text']}")

event_bus.on_typed(
    CoreEvents.NORMALIZATION_MESSAGE_READY,
    handle_message,
    MessageReadyPayload
)

# 发布事件（必须使用 Pydantic BaseModel）
await event_bus.emit(
    CoreEvents.NORMALIZATION_MESSAGE_READY,
    MessageReadyPayload(
        message={"text": "你好", "source": "console"},
        source="console"
    ),
    source="MyProvider"
)
```

## 核心 API

### emit() - 发布事件

发布类型安全的事件到 EventBus。

**签名**：
```python
async def emit(
    event_name: str,
    data: BaseModel,
    source: str = "unknown",
    error_isolate: bool = True
) -> None
```

**参数**：
- `event_name` (str): 事件名称（推荐使用 `CoreEvents` 常量）
- `data` (BaseModel): 事件数据（**必须是 Pydantic BaseModel 实例**）
- `source` (str): 事件源标识符（通常是发布者的类名）
- `error_isolate` (bool): 是否隔离错误（默认 True）

**返回值**：None（异步后台执行）

**异常**：
- `TypeError`: 如果 `data` 不是 Pydantic BaseModel 实例

**示例**：
```python
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import RawDataPayload

# 发布原始数据事件
await event_bus.emit(
    CoreEvents.PERCEPTION_RAW_DATA_GENERATED,
    RawDataPayload(
        content="用户输入的文本",
        source="console_input",
        data_type="text"
    ),
    source="ConsoleInputProvider"
)
```

**类型安全强制**：
```python
# ❌ 错误：不能使用字典
await event_bus.emit("test.event", {"key": "value"})

# ❌ 错误：不能使用 None
await event_bus.emit("test.event", None)

# ✅ 正确：使用 Pydantic BaseModel
from pydantic import BaseModel, Field

class MyEventPayload(BaseModel):
    message: str = Field(..., description="消息内容")

await event_bus.emit("test.event", MyEventPayload(message="hello"))
```

### on() - 订阅事件（同步方法）

订阅事件，处理器接收字典格式的数据。

**签名**：
```python
def on(
    event_name: str,
    handler: Callable,
    priority: int = 100
) -> None
```

**参数**：
- `event_name` (str): 要监听的事件名称
- `handler` (Callable): 事件处理器函数
  - 签名：`async def handler(event_name: str, data: Dict[str, Any], source: str)`
- `priority` (int): 优先级（数字越小越优先，默认 100）

**返回值**：None

**示例**：
```python
# 定义处理器（接收字典数据）
async def handle_message(event_name: str, data: dict, source: str):
    text = data.get("message", {}).get("text", "")
    print(f"收到消息: {text}")

# 订阅事件
event_bus.on(CoreEvents.NORMALIZATION_MESSAGE_READY, handle_message)
```

### on_typed() - 订阅类型化事件（推荐）

订阅事件，处理器接收类型化的 Pydantic Model 对象。

**签名**：
```python
def on_typed(
    event_name: str,
    handler: Callable,
    model_class: Type[BaseModel],
    priority: int = 100
) -> None
```

**参数**：
- `event_name` (str): 要监听的事件名称
- `handler` (Callable): 事件处理器函数
  - 签名：`async def handler(event_name: str, data: BaseModel, source: str)`
- `model_class` (Type[BaseModel]): 期望的数据模型类型
- `priority` (int): 优先级（数字越小越优先，默认 100）

**返回值**：None

**优势**：
- **类型提示**：IDE 可以自动补全字段
- **自动反序列化**：EventBus 自动将字典转换为 Pydantic Model
- **类型安全**：Pydantic 验证数据格式

**示例**：
```python
from src.modules.events.payloads import MessageReadyPayload

# 定义处理器（接收类型化对象）
async def handle_message(event_name: str, payload: MessageReadyPayload, source: str):
    # IDE 可以自动补全字段
    text = payload.message.get("text", "")
    print(f"收到消息: {text}")

# 订阅类型化事件
event_bus.on_typed(
    CoreEvents.NORMALIZATION_MESSAGE_READY,
    handle_message,
    MessageReadyPayload
)
```

**on vs on_typed 对比**：

| 特性 | on() | on_typed() |
|------|------|------------|
| 数据格式 | Dict[str, Any] | Pydantic BaseModel |
| 类型提示 | 无 | 有（IDE 自动补全） |
| 数据验证 | 无 | 有（Pydantic 验证） |
| 推荐场景 | 简单场景 | 生产环境（推荐） |

### off() - 取消订阅

取消事件订阅。

**签名**：
```python
def off(event_name: str, handler: Callable) -> None
```

**参数**：
- `event_name` (str): 事件名称
- `handler` (Callable): 要移除的处理器函数

**返回值**：None

**示例**：
```python
# 定义处理器
async def my_handler(event_name, data, source):
    pass

# 订阅
event_bus.on("test.event", my_handler)

# 取消订阅
event_bus.off("test.event", my_handler)
```

**注意**：
- `off()` 同时支持 `on()` 和 `on_typed()` 注册的处理器
- 如果事件没有监听器了，会自动删除该事件条目

### cleanup() - 清理 EventBus

优雅清理 EventBus，等待所有活跃事件完成。

**签名**：
```python
async def cleanup() -> None
```

**行为**：
1. 标记为清理中（忽略新的事件发布）
2. 取消所有待处理的请求
3. 等待所有活跃的 emit 完成（最多 5 秒）
4. 清除所有监听器和统计信息

**示例**：
```python
# 应用关闭时清理
await event_bus.cleanup()
```

### clear() - 清除所有监听器

立即清除所有事件监听器和统计信息（不等待活跃事件）。

**签名**：
```python
def clear() -> None
```

**示例**：
```python
# 测试场景：重置状态
event_bus.clear()
```

## 辅助方法

### 统计功能

```python
# 获取单个事件的统计
stats = event_bus.get_stats("test.event")
if stats:
    print(f"发布次数: {stats.emit_count}")
    print(f"监听器数量: {stats.listener_count}")
    print(f"错误次数: {stats.error_count}")
    print(f"最后发布时间: {stats.last_emit_time}")
    print(f"总执行时间: {stats.total_execution_time_ms}ms")

# 获取所有事件的统计
all_stats = event_bus.get_all_stats()
for event_name, stats in all_stats.items():
    print(f"{event_name}: {stats.emit_count} 次")

# 重置统计
event_bus.reset_stats("test.event")  # 重置单个事件
event_bus.reset_stats()  # 重置所有事件
```

### 查询方法

```python
# 获取监听器数量
count = event_bus.get_listeners_count("test.event")

# 列出所有已注册的事件
events = event_bus.list_events()
print(f"已注册事件: {events}")
```

## 核心事件

### Input Domain 事件

| 事件名 | 常量 | Payload | 发布者 | 订阅者 |
|--------|------|---------|--------|--------|
| 原始数据生成 | `CoreEvents.PERCEPTION_RAW_DATA_GENERATED` | `RawDataPayload` | InputProvider | InputDomain |
| 消息就绪 | `CoreEvents.NORMALIZATION_MESSAGE_READY` | `MessageReadyPayload` | InputDomain | DecisionManager |

**示例**：
```python
from src.modules.events.payloads import RawDataPayload

# 发布原始数据
await event_bus.emit(
    CoreEvents.PERCEPTION_RAW_DATA_GENERATED,
    RawDataPayload(
        content="用户输入",
        source="console_input",
        data_type="text"
    ),
    source="ConsoleInputProvider"
)
```

### Decision Domain 事件

| 事件名 | 常量 | Payload | 发布者 | 订阅者 |
|--------|------|---------|--------|--------|
| 决策请求 | `CoreEvents.DECISION_REQUEST` | `DecisionRequestPayload` | DecisionManager | DecisionProvider |
| 意图生成 | `CoreEvents.DECISION_INTENT_GENERATED` | `IntentPayload` | DecisionProvider | ExpressionGenerator |
| 响应生成 | `CoreEvents.DECISION_RESPONSE_GENERATED` | `DecisionResponsePayload` | MaiCoreDecisionProvider | - |

**示例**：
```python
from src.modules.events.payloads import IntentPayload

# 发布意图
await event_bus.emit(
    CoreEvents.DECISION_INTENT_GENERATED,
    IntentPayload(
        intent_id="intent_123",
        action="reply",
        confidence=0.9
    ),
    source="MyDecisionProvider"
)
```

### Output Domain 事件

| 事件名 | 常量 | Payload | 发布者 | 订阅者 |
|--------|------|---------|--------|--------|
| 参数生成 | `CoreEvents.EXPRESSION_PARAMETERS_GENERATED` | `ParametersGeneratedPayload` | ExpressionGenerator | OutputProvider |
| 渲染完成 | `CoreEvents.RENDER_COMPLETED` | `RenderCompletedPayload` | OutputProvider | - |
| 渲染失败 | `CoreEvents.RENDER_FAILED` | `RenderFailedPayload` | OutputProvider | - |

### 系统事件

| 事件名 | 常量 | Payload | 说明 |
|--------|------|---------|------|
| 启动 | `CoreEvents.CORE_STARTUP` | `StartupPayload` | 系统启动时 |
| 关闭 | `CoreEvents.CORE_SHUTDOWN` | `ShutdownPayload` | 系统关闭时 |
| 错误 | `CoreEvents.CORE_ERROR` | `ErrorPayload` | 系统错误时 |

完整的事件列表请查看 `src/modules/events/names.py`。

## 高级用法

### 优先级控制

处理器按优先级顺序执行（数字越小越优先）。

```python
# 高优先级处理器（先执行）
async def high_priority_handler(event_name, data, source):
    print("高优先级")

# 低优先级处理器（后执行）
async def low_priority_handler(event_name, data, source):
    print("低优先级")

# 注册（priority 数值越小越优先）
event_bus.on("test.event", high_priority_handler, priority=10)
event_bus.on("test.event", low_priority_handler, priority=100)

# 执行顺序：high_priority_handler -> low_priority_handler
await event_bus.emit("test.event", MyEventPayload(message="test"))
```

**默认优先级**：100

### 错误隔离

单个处理器异常不影响其他处理器（默认启用）。

```python
async def failing_handler(event_name, data, source):
    raise ValueError("处理器异常")

async def normal_handler(event_name, data, source):
    print("正常执行")

event_bus.on("test.event", failing_handler, priority=10)
event_bus.on("test.event", normal_handler, priority=20)

# 即使 failing_handler 抛出异常，normal_handler 仍会执行
await event_bus.emit("test.event", MyEventPayload(message="test"))
```

**错误跟踪**：
```python
# 查看处理器错误统计
stats = event_bus.get_stats("test.event")
if stats:
    print(f"错误次数: {stats.error_count}")
```

### 并发执行

所有处理器并发执行（不等待前一个完成）。

```python
import asyncio

results = []

async def handler1(event_name, data, source):
    await asyncio.sleep(0.1)
    results.append("handler1")

async def handler2(event_name, data, source):
    await asyncio.sleep(0.05)
    results.append("handler2")

event_bus.on("test.event", handler1)
event_bus.on("test.event", handler2)

await event_bus.emit("test.event", MyEventPayload(message="test"))
await asyncio.sleep(0.2)

# 两个处理器并发执行，handler2 可能先完成
print(f"结果: {results}")
```

### 自定义 Payload 类型

定义自己的事件 Payload 类型。

```python
from pydantic import BaseModel, Field
from src/modules/events/payloads/base import BasePayload

class MyCustomPayload(BasePayload):
    """自定义事件 Payload"""

    message: str = Field(..., description="消息内容")
    count: int = Field(default=0, description="计数")
    user_id: str = Field(..., description="用户ID")

    def _debug_fields(self):
        """自定义调试输出字段"""
        return ["message", "count"]

# 使用自定义 Payload
await event_bus.emit(
    "my.custom.event",
    MyCustomPayload(message="hello", count=42, user_id="user123"),
    source="MyProvider"
)

# 订阅时获取类型提示
async def handle_custom(event_name: str, payload: MyCustomPayload, source: str):
    print(f"消息: {payload.message}, 计数: {payload.count}")

event_bus.on_typed("my.custom.event", handle_custom, MyCustomPayload)
```

## 生命周期管理

### 清理 EventBus

```python
# 应用关闭时
async def shutdown():
    # 1. 停止所有 Provider
    await provider_manager.stop_all()

    # 2. 等待活跃事件完成（最多 5 秒）
    await event_bus.cleanup()

    # 3. 所有监听器和统计已清除
```

**cleanup() 行为**：
1. 标记为清理中（忽略新的事件发布）
2. 取消所有待处理的请求
3. 等待所有活跃的 emit 完成（最多 5 秒）
4. 清除所有监听器和统计信息

### 活跃事件跟踪

EventBus 自动跟踪活跃的 emit 操作，确保清理时等待它们完成。

```python
# 启动慢速处理器
async def slow_handler(event_name, data, source):
    await asyncio.sleep(2)  # 2秒处理时间

event_bus.on("test.event", slow_handler)

# 发布事件（后台执行）
await event_bus.emit("test.event", MyEventPayload(message="test"))

# 立即清理（会等待慢速处理器完成）
await event_bus.cleanup()  # 等待约 2 秒
```

## 最佳实践

### 1. 使用 CoreEvents 常量

```python
# ❌ 错误：硬编码字符串
await event_bus.emit("normalization.message_ready", payload)

# ✅ 正确：使用常量
from src/modules/events/names import CoreEvents
await event_bus.emit(CoreEvents.NORMALIZATION_MESSAGE_READY, payload)
```

### 2. 使用 on_typed() 获取类型提示

```python
# ❌ 不推荐：使用 on()
async def handler(event_name, data, source):
    text = data.get("message", {}).get("text", "")  # 无类型提示

event_bus.on("test.event", handler)

# ✅ 推荐：使用 on_typed()
async def handler(event_name, payload: MessageReadyPayload, source):
    text = payload.message.get("text")  # 有类型提示

event_bus.on_typed("test.event", handler, MessageReadyPayload)
```

### 3. 继承 BasePayload

```python
# ❌ 不推荐：直接继承 BaseModel
from pydantic import BaseModel

class MyEvent(BaseModel):
    message: str

# ✅ 推荐：继承 BasePayload（获得调试输出）
from src/modules/events/payloads/base import BasePayload

class MyEvent(BasePayload):
    message: str

    def _debug_fields(self):
        return ["message"]
```

### 4. 提供有意义的 source

```python
# ❌ 不推荐：默认 source
await event_bus.emit("test.event", payload)

# ✅ 推荐：提供类名
await event_bus.emit("test.event", payload, source="ConsoleInputProvider")
```

### 5. 处理异常

```python
async def handler(event_name, data, source):
    try:
        # 业务逻辑
        process_data(data)
    except Exception as e:
        # 记录日志，不要让异常传播
        logger.error(f"处理器错误: {e}", exc_info=True)

event_bus.on("test.event", handler)
```

## 常见问题

### Q: EventBus 不支持哪些功能？

- ❌ **通配符订阅**（如 `decision.*`）：不支持，必须订阅具体事件
- ❌ **emit_typed()**：只有 `emit()` 和 `on_typed()`，没有 `emit_typed()`
- ❌ **同步 emit**：`emit()` 始终是异步方法
- ❌ **字典数据**：`emit()` 强制要求 Pydantic BaseModel

### Q: 如何选择 on() 和 on_typed()？

- **简单场景**：使用 `on()`（快速开发）
- **生产环境**：使用 `on_typed()`（类型安全）

### Q: emit() 是阻塞还是非阻塞？

**非阻塞**：`emit()` 在后台执行处理器，立即返回。

```python
# 非阻塞：立即返回
await event_bus.emit("test.event", payload)

print("立即执行")  # 不等待处理器完成
```

如果需要等待处理器完成，使用 asyncio.sleep()：

```python
await event_bus.emit("test.event", payload)
await asyncio.sleep(0.1)  # 等待处理器完成
```

### Q: 如何调试事件流？

```python
# 启用 EventBus 日志
import logging
logging.getLogger("EventBus").setLevel(logging.DEBUG)

# 查看统计
stats = event_bus.get_all_stats()
for event_name, stat in stats.items():
    print(f"{event_name}: {stat.emit_count} 次, {stat.error_count} 错误")
```

## 相关文档

- [事件系统](../architecture/event-system.md) - EventBus 在架构中的作用
- [数据流规则](../architecture/data-flow.md) - 3域数据流约束
- [Provider 开发](../development/provider-guide.md) - Provider 中使用 EventBus
- [测试指南](../development/testing-guide.md) - 测试 EventBus 相关代码

## 源码参考

- **EventBus 实现**：`src/modules/events/event_bus.py`
- **事件名称常量**：`src/modules/events/names.py`
- **Payload 定义**：`src/modules/events/payloads/`
- **单元测试**：`tests/modules/events/test_event_bus.py`
