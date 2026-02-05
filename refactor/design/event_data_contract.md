# 事件数据契约设计

## 核心目标

构建类型安全的事件系统，解决 EventBus 数据传递中的类型不安全问题。

---

## 问题与方案

### 当前问题

```python
# 问题：无类型、无验证
await event_bus.emit(
    "some.event",           # 魔法字符串
    {"data": raw_data},     # 无类型的字典
)
```

### 解决方案

```python
# 方案：Pydantic Model + 事件常量
await event_bus.emit(
    CoreEvents.RAW_DATA_GENERATED,  # 常量
    RawDataPayload(data=raw_data),  # 类型安全
)
```

---

## 核心组件

### 事件名称常量

```python
# src/core/events/names.py
class CoreEvents:
    # Input Domain
    RAW_DATA_GENERATED = "perception.raw_data.generated"
    MESSAGE_READY = "normalization.message_ready"
    
    # Decision Domain
    INTENT_GENERATED = "decision.intent_generated"
    
    # Output Domain
    RENDER_COMPLETED = "rendering.completed"
```

### 事件 Payload（Pydantic Model）

```python
# src/core/events/payloads/input.py
from pydantic import BaseModel

class RawDataPayload(BaseModel):
    data: RawData
    source: str

class MessageReadyPayload(BaseModel):
    message: NormalizedMessage
```

### EventRegistry（注册表）

```python
class EventRegistry:
    """事件注册表，管理事件名→Payload类型的映射"""
    
    @classmethod
    def register(cls, event_name: str, payload_type: Type[BaseModel]):
        cls._registry[event_name] = payload_type
    
    @classmethod
    def validate(cls, event_name: str, data: Any) -> BaseModel:
        payload_type = cls._registry.get(event_name)
        if payload_type:
            return payload_type.model_validate(data)
        return data
```

---

## 使用方式

### 发送事件

```python
from src.core.events.names import CoreEvents
from src.core.events.payloads import RawDataPayload

await event_bus.emit(
    CoreEvents.RAW_DATA_GENERATED,
    RawDataPayload(data=raw_data, source="bili_danmaku")
)
```

### 订阅事件

```python
@event_bus.on(CoreEvents.MESSAGE_READY)
async def handle_message(payload: MessageReadyPayload):
    message = payload.message
    # 有完整的类型提示
```

---

## 核心事件列表

| 事件名 | Payload | 触发时机 |
|--------|---------|----------|
| `perception.raw_data.generated` | RawDataPayload | InputProvider 采集到数据 |
| `normalization.message_ready` | MessageReadyPayload | InputLayer 完成标准化 |
| `decision.intent_generated` | IntentPayload | DecisionProvider 生成意图 |
| `rendering.completed` | RenderCompletedPayload | OutputProvider 完成渲染 |
