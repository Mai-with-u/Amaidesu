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
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import MessageReadyPayload, IntentPayload

# 发布事件（必须使用 Pydantic BaseModel）
await event_bus.emit(
    CoreEvents.DATA_MESSAGE,
    MessageReadyPayload(message=normalized_message),
    source="MyInputProvider"
)

# 所有事件都需要 source 参数
await event_bus.emit(
    CoreEvents.DECISION_INTENT,
    IntentPayload(intent=intent),
    source="MyDecisionProvider"
)
```

### 订阅事件

```python
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import MessageReadyPayload, IntentPayload

# 订阅事件（on() 是同步方法）
event_bus.on(
    CoreEvents.DATA_MESSAGE,
    self.handle_message
)

# 订阅类型化事件（自动反序列化）
event_bus.on_typed(
    CoreEvents.DECISION_INTENT,
    self.handle_intent,
    IntentPayload
)

# 新的事件处理器签名
async def handle_message(self, event_name: str, payload: MessageReadyPayload, source: str):
    message = payload.message
    # 处理消息
```

### 取消订阅

```python
# 取消订阅
event_bus.off(
    CoreEvents.DATA_MESSAGE,
    self.handle_message
)
```

## 核心事件

### 事件列表

| 事件名 | 发布者 | 订阅者 | 数据类型 |
|--------|--------|--------|---------|
| `data.raw` | InputProvider | InputCoordinator | `RawDataPayload` |
| `data.message` | InputCoordinator | DecisionCoordinator | `MessageReadyPayload` |
| `decision.intent` | DecisionCoordinator | OutputProviderManager | `IntentPayload` |
| `output.params` | OutputProviderManager | TTS/Subtitle Providers | `ParametersGeneratedPayload` |
| `render.completed` | OutputProvider | - | `RenderCompletedPayload` |

### 事件常量

使用 `CoreEvents` 常量避免硬编码字符串：

```python
from src.modules.events.names import CoreEvents

# ✅ 正确：使用常量
await event_bus.emit(CoreEvents.DATA_MESSAGE, data)

# ❌ 错误：硬编码字符串
await event_bus.emit("data.message", data)
```

## 类型安全

### 事件 Payload

使用 Pydantic Model 确保类型安全：

```python
from pydantic import BaseModel
from src.modules.events.payloads import MessageReadyPayload

class MessageReadyPayload(BaseModel):
    """消息就绪事件 Payload"""
    message: NormalizedMessage
    source: str
    timestamp: float = Field(default_factory=time.time)

# 订阅时指定类型（on_typed 自动反序列化）
event_bus.on_typed(
    CoreEvents.DATA_MESSAGE,
    self.handle_message,
    MessageReadyPayload
)

async def handle_message(self, event_name: str, payload: MessageReadyPayload, source: str):
    # payload 类型已知，IDE 有自动补全
    message = payload.message
```

## 优先级

### 设置优先级

```python
# 高优先级订阅（先处理）
event_bus.on(
    CoreEvents.DATA_MESSAGE,
    self.handle_critical,
    priority=100
)

# 普通优先级
event_bus.on(
    CoreEvents.DATA_MESSAGE,
    self.handle_normal,
    priority=50  # 默认值
)

# 低优先级订阅（后处理）
event_bus.on(
    CoreEvents.DATA_MESSAGE,
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

event_bus.on("eventbus.error", on_error)
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

### 一次性订阅

```python
# 只处理一次
async def handle_once():
    future = asyncio.Future()

    async def callback(event_name: str, payload: dict, source: str):
        future.set_result(payload)

    event_bus.on(
        CoreEvents.DATA_MESSAGE,
        callback
    )

    return await future
```

### 条件订阅

```python
async def conditional_handler(self, event_name: str, payload: dict, source: str):
    # 只处理特定条件的事件
    if source == "bili_danmaku":
        await self.handle_bili_danmaku(payload)
```

## 最佳实践

### 1. 使用事件常量

```python
# ✅ 正确
from src.modules.events.names import CoreEvents
await event_bus.emit(CoreEvents.DATA_MESSAGE, data)

# ❌ 错误
await event_bus.emit("data.message", data)
```

### 2. 使用类型安全的 Payload

```python
# ✅ 正确
from src.modules.events.payloads import MessageReadyPayload
await event_bus.emit(
    CoreEvents.DATA_MESSAGE,
    MessageReadyPayload(message=msg),
    source="MyProvider"
)

# ❌ 错误
await event_bus.emit(
    CoreEvents.DATA_MESSAGE,
    {"message": msg},  # 无类型检查，会抛出 TypeError
    source="MyProvider"
)
```

### 3. 异步处理

```python
# ✅ 正确：异步处理
async def handle_message(self, event_name: str, payload: dict, source: str):
    await self.process_async(payload)

# ❌ 错误：阻塞事件循环
def handle_message(self, event_name: str, payload: dict, source: str):
    time.sleep(10)  # 阻塞
```

### 4. 错误处理

```python
# ✅ 正确：捕获异常
async def handle_message(self, event_name: str, payload: dict, source: str):
    try:
        await self.process(payload)
    except Exception as e:
        self.logger.error(f"处理失败: {e}", exc_info=True)

# ❌ 错误：未处理异常
async def handle_message(self, event_name: str, payload: dict, source: str):
    await self.process(payload)  # 可能抛出异常
```

## 架构约束

EventBus 技术上允许任何订阅模式，但架构层面强制约束：

- Input Domain 只发布，不订阅
- Decision Domain 订阅 Input，不订阅 Output
- Output Domain 订阅 Decision，不订阅 Input

详细规则见：[数据流规则](data-flow.md)

## AudioStreamChannel 与 EventBus 的协作

AudioStreamChannel 是专门的音频数据传输通道，与 EventBus 配合使用：

### 职责分离

- **EventBus**: 元数据事件（开始/结束/状态通知）
- **AudioStreamChannel**: 音频数据流（chunk 数据传输）

### 为什么需要分离？

音频数据量大，通过 EventBus 传输会：
- 序列化开销大（EventBus 支持持久化，需要序列化）
- 不适合高频大块数据传输
- 缺乏背压机制

AudioStreamChannel 提供：
- 低开销进程内队列传输（传递 Python 对象引用）
- 背压策略（BLOCK/DROP_NEWEST/DROP_OLDEST/FAIL_FAST）
- 音频专用功能（重采样、采样率转换）

### 使用示例

```python
# EventBus: 发布 TTS 触发事件
await event_bus.emit(
    CoreEvents.OUTPUT_PARAMS,
    ExpressionParametersPayload(parameters=expr_params),
    source="ExpressionGenerator"
)

# AudioStreamChannel: TTS Provider 发布音频数据
await audio_channel.notify_start(AudioMetadata(text=text, sample_rate=48000))
for chunk in audio_chunks:
    await audio_channel.publish(AudioChunk(data=chunk, sample_rate=48000))
await audio_channel.notify_end(AudioMetadata(text=text, sample_rate=48000))

# VTS Provider: 订阅音频数据用于口型同步
await audio_channel.subscribe(
    name="vts_lip_sync",
    on_audio_chunk=self._on_lip_sync_chunk,
    config=SubscriberConfig(
        queue_size=100,
        backpressure_strategy=BackpressureStrategy.DROP_NEWEST
    )
)
```

详细文档见：[AGENTS.md - AudioStreamChannel](../../AGENTS.md#audiostreamchannel-音频流系统)

## 相关文档

- [数据流规则](data-flow.md) - 事件订阅约束
- [AGENTS.md - AudioStreamChannel](../../AGENTS.md#audiostreamchannel-音频流系统) - 音频流系统

---

*最后更新：2026-02-10*
