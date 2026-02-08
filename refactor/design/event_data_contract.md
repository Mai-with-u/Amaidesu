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

## 架构约束：事件订阅规则

### 硬性约束

虽然 EventBus 技术上允许任何订阅模式，但**架构层面强制约束**事件订阅关系：

| 订阅者 | 可以订阅的事件 | 禁止订阅的事件 |
|--------|---------------|---------------|
| **Input Domain** | 无（仅发布） | Decision/Output 事件 |
| **Decision Domain** | `NORMALIZATION_MESSAGE_READY` | `RENDER_*` 事件 |
| **Output Domain** | `DECISION_INTENT_GENERATED` | **`NORMALIZATION_*` 事件** |

### 禁止的订阅模式

```python
# ❌ 禁止：Output Provider 订阅 Input 事件
class MyOutputProvider(OutputProvider):
    async def initialize(self):
        # 禁止！绕过了 Decision Domain
        await self.event_bus.subscribe(
            CoreEvents.NORMALIZATION_MESSAGE_READY,
            self.handler
        )

# ❌ 禁止：Decision Provider 订阅 Output 事件
class MyDecisionProvider(DecisionProvider):
    async def initialize(self):
        # 禁止！创建循环依赖
        await self.event_bus.subscribe(
            CoreEvents.RENDER_COMPLETED,
            self.handler
        )
```

### 正确的事件流

```
Input Domain          Decision Domain       Output Domain
     │                       │                      │
     ├─ RAW_DATA_GENERATED  │                      │
     │          └───────────┘                      │
     │                       │                      │
     ├─ MESSAGE_READY ───────┼───────────┐         │
     │                       │           │         │
     │                       ├─ INTENT_GENERATED ──┼───┐
     │                       │           │         │   │
     │                       │           │         └─ RENDER_COMPLETED
     │                       │           │             (仅用于监听/日志)
     ▼                       ▼           ▼
```

### 验证

架构测试（`tests/architecture/test_event_flow_constraints.py`）会自动验证：
- Output Domain 不订阅 Input Domain 事件
- Decision Domain 不订阅 Output Domain 事件
- 事件流严格遵守单向原则

---

## 核心事件列表

| 事件名 | Payload | 触发时机 |
|--------|---------|----------|
| `perception.raw_data.generated` | RawDataPayload | InputProvider 采集到数据 |
| `normalization.message_ready` | MessageReadyPayload | InputDomain 完成标准化 |
| `decision.intent_generated` | IntentPayload | DecisionProvider 生成意图 |
| `rendering.completed` | RenderCompletedPayload | OutputProvider 完成渲染 |
