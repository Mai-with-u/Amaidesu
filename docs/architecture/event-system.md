# 事件系统

Amaidesu 使用 EventBus 作为跨域通信机制，实现发布-订阅模式。

## 概述

EventBus 是项目的核心通信机制，支持：
- 发布-订阅模式
- 优先级队列
- 错误隔离
- 类型安全的事件数据
- 统计功能

## 核心概念

### 发布-订阅模式

```
发布者              EventBus              订阅者
  │                   │                    │
  ├─ publish(event) ─>│<── subscribe(event)┤
                      │                    │
                      ├─ 通知所有订阅者 ────┤
```

### 优点

- **松耦合**：发布者和订阅者无需直接引用
- **可扩展**：易于添加新的订阅者
- **错误隔离**：一个订阅者失败不影响其他
- **异步**：支持异步事件处理

## 基本使用

### 发布事件

```python
from src.core.events.names import CoreEvents

# 发布简单事件
await event_bus.emit(
    CoreEvents.NORMALIZATION_MESSAGE_READY,
    {"message": normalized_message}
)

# 发布带类型的事件
await event_bus.emit_typed(
    CoreEvents.DECISION_INTENT_GENERATED,
    IntentPayload(intent=intent)
)
```

### 订阅事件

```python
from src.core.events.names import CoreEvents

# 订阅事件
await event_bus.subscribe(
    CoreEvents.NORMALIZATION_MESSAGE_READY,
    self.handle_message
)

# 订阅带类型的事件
await event_bus.subscribe_typed(
    CoreEvents.DECISION_INTENT_GENERATED,
    self.handle_intent,
    IntentPayload
)

async def handle_message(self, payload):
    message = payload.message
    # 处理消息
```

### 取消订阅

```python
# 取消订阅
event_bus.unsubscribe(
    CoreEvents.NORMALIZATION_MESSAGE_READY,
    self.handle_message
)
```

## 核心事件

### 事件列表

| 事件名 | 发布者 | 订阅者 | 数据类型 |
|--------|--------|--------|---------|
| `perception.raw_data.generated` | InputProvider | InputDomain | `RawDataPayload` |
| `normalization.message_ready` | InputDomain | DecisionManager | `MessageReadyPayload` |
| `decision.intent_generated` | DecisionManager | ExpressionGenerator | `IntentPayload` |
| `expression.parameters_generated` | ExpressionGenerator | OutputProviderManager | `ExpressionParametersPayload` |
| `render.completed` | OutputProvider | - | `RenderCompletedPayload` |

### 事件常量

使用 `CoreEvents` 常量避免硬编码字符串：

```python
from src.core.events.names import CoreEvents

# ✅ 正确：使用常量
await event_bus.emit(CoreEvents.NORMALIZATION_MESSAGE_READY, data)

# ❌ 错误：硬编码字符串
await event_bus.emit("normalization.message_ready", data)
```

## 类型安全

### 事件 Payload

使用 Pydantic Model 确保类型安全：

```python
from pydantic import BaseModel
from src.core.events.payloads import MessageReadyPayload

class MessageReadyPayload(BaseModel):
    """消息就绪事件 Payload"""
    message: NormalizedMessage
    source: str
    timestamp: float = Field(default_factory=time.time)

# 订阅时指定类型
await event_bus.subscribe_typed(
    CoreEvents.NORMALIZATION_MESSAGE_READY,
    self.handle_message,
    MessageReadyPayload  # 类型检查
)

async def handle_message(self, payload: MessageReadyPayload):
    # payload 类型已知，IDE 有自动补全
    message = payload.message
```

## 优先级

### 设置优先级

```python
# 高优先级订阅（先处理）
await event_bus.subscribe(
    CoreEvents.NORMALIZATION_MESSAGE_READY,
    self.handle_critical,
    priority=100
)

# 普通优先级
await event_bus.subscribe(
    CoreEvents.NORMALIZATION_MESSAGE_READY,
    self.handle_normal,
    priority=50  # 默认值
)

# 低优先级订阅（后处理）
await event_bus.subscribe(
    CoreEvents.NORMALIZATION_MESSAGE_READY,
    self.handle_logging,
    priority=10
)
```

### 优先级顺序

高优先级 → 低优先级
```
priority=100 (critical)
     ↓
priority=50  (normal)
     ↓
priority=10  (logging)
```

## 错误处理

### 错误隔离

默认情况下，一个订阅者的错误不会影响其他订阅者：

```python
# 订阅者 A
async def handler_a(self, payload):
    raise ValueError("错误")  # 不会影响订阅者 B

# 订阅者 B（仍然会执行）
async def handler_b(self, payload):
    print("正常执行")
```

### 错误回调

监听错误事件：

```python
async def on_error(error_event):
    print(f"EventBus 错误: {error_event.error}")

await event_bus.subscribe("eventbus.error", on_error)
```

## 统计功能

### 获取统计信息

```python
stats = event_bus.get_stats()

print(f"已发布事件: {stats['total_published']}")
print(f"已处理事件: {stats['total_processed']}")
print(f"错误数: {stats['total_errors']}")
```

### 重置统计

```python
event_bus.reset_stats()
```

## 高级用法

### 通配符订阅

```python
# 订阅所有 decision.* 事件
await event_bus.subscribe(
    "decision.*",
    self.handle_all_decision_events
)

# 订阅所有事件（谨慎使用）
await event_bus.subscribe(
    "*",
    self.handle_all_events
)
```

### 一次性订阅

```python
# 只处理一次
async def handle_once():
    future = asyncio.Future()

    async def callback(payload):
        future.set_result(payload)

    await event_bus.subscribe(
        CoreEvents.NORMALIZATION_MESSAGE_READY,
        callback
    )

    return await future
```

### 条件订阅

```python
async def conditional_handler(self, payload):
    # 只处理特定条件的事件
    if payload.source == "bili_danmaku":
        await self.handle_bili_danmaku(payload)
```

## 最佳实践

### 1. 使用事件常量

```python
# ✅ 正确
from src.core.events.names import CoreEvents
await event_bus.emit(CoreEvents.NORMALIZATION_MESSAGE_READY, data)

# ❌ 错误
await event_bus.emit("normalization.message_ready", data)
```

### 2. 使用类型安全的 Payload

```python
# ✅ 正确
from src.core.events.payloads import MessageReadyPayload
await event_bus.emit_typed(
    CoreEvents.NORMALIZATION_MESSAGE_READY,
    MessageReadyPayload(message=msg)
)

# ❌ 错误
await event_bus.emit(
    CoreEvents.NORMALIZATION_MESSAGE_READY,
    {"message": msg}  # 无类型检查
)
```

### 3. 异步处理

```python
# ✅ 正确：异步处理
async def handle_message(self, payload):
    await self.process_async(payload)

# ❌ 错误：阻塞事件循环
def handle_message(self, payload):
    time.sleep(10)  # 阻塞
```

### 4. 错误处理

```python
# ✅ 正确：捕获异常
async def handle_message(self, payload):
    try:
        await self.process(payload)
    except Exception as e:
        self.logger.error(f"处理失败: {e}", exc_info=True)

# ❌ 错误：未处理异常
async def handle_message(self, payload):
    await self.process(payload)  # 可能抛出异常
```

## 架构约束

EventBus 技术上允许任何订阅模式，但架构层面强制约束：

- Input Domain 只发布，不订阅
- Decision Domain 订阅 Input，不订阅 Output
- Output Domain 订阅 Decision，不订阅 Input

详细规则见：[数据流规则](data-flow.md)

## 相关文档

- [数据流规则](data-flow.md) - 事件订阅约束
- [架构验证器](../architectural_validator.md) - 运行时验证
- [EventBus API](../api/event_bus.md) - API 参考

---

*最后更新：2026-02-09*
