# 事件系统模块

提供类型安全的发布-订阅事件总线。

## 概述

`src/modules/events/` 模块提供事件系统功能：
- EventBus 事件总线
- CoreEvents 事件名称常量
- 事件 Payload 类型定义

## 目录结构

```
src/modules/events/
├── event_bus.py        # EventBus 核心实现
├── names.py           # CoreEvents 事件常量
├── registry.py        # 事件注册工具
├── payloads/          # 事件 Payload
│   ├── input.py       # 输入相关 Payload
│   ├── decision.py    # 决策相关 Payload
│   ├── output.py      # 输出相关 Payload
│   └── system.py      # 系统事件 Payload
└── __init__.py       # 模块导出
```

## 核心 API

### EventBus

```python
from src.modules.events import EventBus, CoreEvents

# 创建事件总线
event_bus = EventBus()

# 订阅事件
await event_bus.subscribe(
    CoreEvents.DATA_MESSAGE,
    self.handle_message,
    priority=0
)

# 发布事件
await event_bus.emit(
    CoreEvents.DATA_MESSAGE,
    message
)
```

### CoreEvents

```python
from src.modules.events.names import CoreEvents

# 核心事件
CoreEvents.DATA_MESSAGE       # Input Domain 消息
CoreEvents.DECISION_INTENT    # Decision Domain 意图
CoreEvents.OUTPUT_INTENT     # Output Domain 意图

# 系统事件
CoreEvents.CORE_STARTUP       # 启动完成
CoreEvents.CORE_SHUTDOWN     # 关闭
CoreEvents.CORE_ERROR        # 错误
```

### 事件 Payload

```python
from src.modules.events.payloads import (
    # 输入
    RawDataPayload,
    MessageReadyPayload,
    # 决策
    IntentPayload,
    IntentActionPayload,
    # 输出
    ParametersGeneratedPayload,
    RenderCompletedPayload,
    # 系统
    StartupPayload,
    ShutdownPayload,
    ErrorPayload,
)
```

## 事件列表

### 数据流事件

| 事件名 | 发布者 | 订阅者 | 数据类型 |
|--------|--------|--------|---------|
| `data.message` | Input Domain | Decision Domain | NormalizedMessage |
| `decision.intent` | Decision Domain | Output Domain | Intent |
| `output.params` | ExpressionGenerator | OutputProviders | RenderParameters (已废弃，请使用 `output.intent`) |

> **废弃说明**: `output.params` 事件已废弃，建议使用 `decision.intent` 事件。

### 系统事件

| 事件名 | 触发时机 | 数据类型 |
|--------|---------|---------|
| `core.startup` | 启动完成 | StartupPayload |
| `core.shutdown` | 关闭 | ShutdownPayload |
| `core.error` | 错误发生 | ErrorPayload |

## 详细文档

更详细的 EventBus API 使用指南请查看 [EventBus API](../api/event_bus.md) 和 [事件系统架构](../architecture/event-system.md)。

---

*最后更新：2026-02-14*
