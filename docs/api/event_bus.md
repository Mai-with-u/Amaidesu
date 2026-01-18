# EventBus API

## 概述

EventBus 是一个发布-订阅事件总线，用于插件间通信。

## 方法

### emit(event_name: str, data: Any, source: str = None)
发布事件到 EventBus。

**参数**:
- event_name: 事件名称
- data: 事件数据
- source: 事件源（可选）

**示例**:
```python
await event_bus.emit("test.event", {"count": 1}, "MyPlugin")
```

### on(event_name: str, handler: Callable, priority: int = 100)
订阅事件到 EventBus。

**参数**:
- event_name: 要监听的事件名称
- handler: 事件处理器函数
- priority: 优先级（数字越小越优先）

**示例**:
```python
def handle_event(event_name, data, source):
    print(f"收到事件: {event_name}")

event_bus.on("test.event", handle_event, priority=50)
```

## 事件类型

- `console.input`: 控制台输入
- `tts.audio_ready`: TTS 音频数据就绪
- `canonical.message`: 规范消息准备
- `expression.parameters_generated`: 表达参数已生成
- `understanding.intent_generated`: Intent已生成
- `decision.response_generated`: 决策响应已生成

## 事件生命周期

1. **发布事件**: 使用`emit()`方法
2. **订阅事件**: 使用`on()`方法
3. **取消订阅**: 使用`stop_listening_event()`方法
4. **错误隔离**: 订阅者异常不影响其他订阅者

## 使用示例

### 发布事件

```python
from src.core.event_bus import EventBus

event_bus = EventBus()

# 发布简单事件
await event_bus.emit("user.input", {"text": "Hello"}, "MyPlugin")

# 发布复杂事件
await event_bus.emit("processing.complete", {
    "result": "success",
    "data": {"count": 42}
}, "MyPlugin")
```

### 订阅事件

```python
from src.core.event_bus import EventBus

event_bus = EventBus()

async def handle_user_input(event_name, event_data, source):
    print(f"收到用户输入: {event_data['text']}")

event_bus.on("user.input", handle_user_input, priority=50)
```

### 高级用法

### 优先级控制

```python
# 高优先级处理器（优先执行）
event_bus.on("test.event", high_priority_handler, priority=10)

# 低优先级处理器（后执行）
event_bus.on("test.event", low_priority_handler, priority=100)
```

### 统计信息

```python
# 获取事件统计
stats = event_bus.get_statistics()
print(f"事件总数: {stats['total_events']}")
print(f"订阅者数量: {stats['total_subscribers']}")
```

### 错误处理

EventBus 会隔离订阅者的异常，确保一个订阅者的错误不会影响其他订阅者。

```python
# 即使handler1抛出异常，handler2也会执行
event_bus.on("test.event", handler1)
event_bus.on("test.event", handler2)
await event_bus.emit("test.event", {})
```

---

**相关文档**:
- [Plugin Protocol](./plugin_protocol.md)
- [InputProvider API](./input_provider.md)
- [OutputProvider API](./output_provider.md)
- [DecisionProvider API](./decision_provider.md)
