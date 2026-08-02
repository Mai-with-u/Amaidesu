# 事件系统

Amaidesu 项目采用 **发布-订阅（Pub/Sub）模式** 构建事件驱动架构，通过 EventBus 实现组件间的松耦合通信。

## 目录

- [架构概述](#架构概述)
- [核心组件](#核心组件)
- [核心 API](#核心-api)
- [核心事件常量](#核心事件常量)
- [事件载荷类型](#事件载荷类型)
- [核心特性](#核心特性)
- [使用示例](#使用示例)
- [Mermaid 时序图](#mermaid-时序图)
- [最佳实践](#最佳实践)

---

## 架构概述

事件系统是 3 阶段架构中各组件通信的核心机制：

```mermaid
flowchart LR
    subgraph Input[Input 阶段]
        IP[InputCollector]
    end

    subgraph Decision[Decision 阶段]
        DP[Decider]
    end

    subgraph Output[Output 阶段]
        OP[OutputHandler]
    end

    IP -->|emit: input.message.received| EB[EventBus]
    DP -->|emit: decision.intent.generated| EB
    OPM[OutputHandlerManager] -->|emit: output.intent.dispatched<br/>监控信号| EB

    EB -->|on| DP
    EB -->|on| OPM
    OPM -->|直接调用 handle(intent)| OP[OutputHandler]
```

**数据流规则**：
- **Input 阶段** 发布 `input.message.received` 事件，携带标准化消息
- **Decision 阶段** 订阅并处理消息，发布 `decision.intent.generated` 事件
- **Output 阶段** 由 `OutputHandlerManager` 订阅意图事件，运行 Pipeline，发布 `output.intent.dispatched` 监控信号，并直接并行调用 active handler 的 `handle(intent)`
- Manager 等待全部 Handler 完成后发布 `output.intent.finished`

详细规则见 [数据流规则](data-flow.md)。

---

## 核心组件

| 组件 | 文件位置 | 职责 |
|------|----------|------|
| **EventBus** | `src/modules/events/event_bus.py` | 事件总线核心，提供 emit/on/off 等核心 API |
| **EventRegistry** | `src/modules/events/registry.py` | 事件类型注册表，验证事件合法性 |
| **CoreEvents** | `src/modules/events/names.py` | 核心事件名称常量（避免魔法字符串） |
| **Payloads** | `src/modules/events/payloads/*.py` | 事件载荷类型定义（基于 Pydantic） |

### 模块结构

```
src/modules/events/
├── __init__.py           # 模块导出
├── event_bus.py          # EventBus 核心实现
├── event_history.py      # 事件历史查询服务
├── event_recorder.py     # 事件记录器（监控组件）
├── registry.py           # 事件注册表
├── names.py              # CoreEvents 常量
└── payloads/
    ├── __init__.py       # Payload 统一导出
    ├── base.py           # BasePayload 基类
    ├── connection.py     # 连接状态事件 Payload（共用）
    ├── core.py           # 核心系统事件 Payload
    ├── input.py          # Input 阶段 Payload
    ├── decision.py       # Decision 阶段 Payload
    └── output.py         # Output 阶段 Payload
```

---

## 核心 API

### EventBus 核心方法

```python
from src.modules.events.event_bus import EventBus

# 创建事件总线
event_bus = EventBus(enable_stats=True)
```

#### 发布事件 (emit)

```python
await event_bus.emit(
    event_name: str,              # 事件名称
    data: BaseModel,              # Pydantic Model 实例
    source: str = "unknown",      # 事件源
    error_isolate: bool = True,   # 错误隔离
    wait: bool = False            # 是否等待处理完成
)
```

**参数说明**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `event_name` | `str` | 必填 | 事件名称 |
| `data` | `BaseModel` | 必填 | Pydantic Model 实例 |
| `source` | `str` | `"unknown"` | 事件发布源，通常为 Collector/Decider/Handler 类名 |
| `error_isolate` | `bool` | `True` | 错误隔离策略 |
| `wait` | `bool` | `False` | 是否等待所有监听器执行完成 |

**error_isolate 行为**：
- `True`：单个 handler 异常不会影响其他 handler 执行
- `False`：第一个异常会传播到调用者，中断所有 handler

**wait 行为**：
- `False`：在后台任务中执行，不等待完成
- `True`：等待所有监听器执行完成后再返回

#### 订阅事件 (on)

```python
event_bus.on(
    event_name: str,               # 事件名称
    handler: Callable,              # 处理函数
    model_class: Type[T],          # Payload 类型（必须）
    priority: int = 100            # 优先级（越小越优先）
)
```

**注意**：EventBus 强制要求类型化订阅，所有订阅必须指定 `model_class`。

#### 取消订阅 (off)

```python
event_bus.off(event_name: str, handler: Callable)
```

#### 生命周期管理

```python
# 清理 EventBus
await event_bus.cleanup(timeout: float = 5.0, force: bool = False)
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `timeout` | `float` | `5.0` | 等待活跃 emit 完成的超时时间（秒） |
| `force` | `bool` | `False` | 是否强制清理（即使有活跃任务） |

#### 统计功能

```python
# 获取单个事件统计
stats = event_bus.get_stats(event_name: str)

# 获取所有事件统计
all_stats = event_bus.get_all_stats()

# 重置统计
event_bus.reset_stats(event_name: Optional[str] = None)
```

**EventStats 结构**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `emit_count` | `int` | 发布次数 |
| `listener_count` | `int` | 监听器数量 |
| `error_count` | `int` | 错误次数 |
| `last_emit_time` | `float` | 最后发布时间（Unix 时间戳） |
| `last_error_time` | `float` | 最后错误时间（Unix 时间戳） |
| `total_execution_time_ms` | `float` | 总执行时间（毫秒） |

---

## 核心事件常量

使用 `CoreEvents` 类获取所有事件常量，避免魔法字符串：

```python
from src.modules.events.names import CoreEvents

# ========== Core: 核心系统事件 ==========
CoreEvents.CORE_STARTUP          # core.startup
CoreEvents.CORE_SHUTDOWN         # core.shutdown
CoreEvents.CORE_ERROR            # core.error

# ========== Input 阶段 ==========
CoreEvents.INPUT_MESSAGE_RECEIVED    # input.message.received
CoreEvents.INPUT_CONNECTED           # input.connected
CoreEvents.INPUT_DISCONNECTED        # input.disconnected

# ========== Decision 阶段 ==========
CoreEvents.DECISION_INTENT_GENERATED     # decision.intent.generated
CoreEvents.DECISION_CONNECTED            # decision.connected
CoreEvents.DECISION_DISCONNECTED         # decision.disconnected

# ========== Output 阶段 ==========
CoreEvents.OUTPUT_INTENT_DISPATCHED      # output.intent.dispatched（意图已进入直接调度的监控信号）
CoreEvents.OUTPUT_INTENT_FINISHED        # output.intent.finished（Manager 等待所有 handler 完成后通知）
CoreEvents.OUTPUT_HANDLER_COMPLETED      # output.handler.completed（Manager 内部兼容语义，Handler 不发布）
CoreEvents.OUTPUT_HANDLER_CONNECTED      # DEPRECATED 兼容垫片（不再发射）
CoreEvents.OUTPUT_HANDLER_DISCONNECTED   # DEPRECATED 兼容垫片（不再发射）
CoreEvents.OUTPUT_OBS_COMMAND            # output.obs.command
CoreEvents.OUTPUT_STICKER_COMMAND        # output.sticker.command
```

> **架构演进**：早期版本的事件常量（如 `OBS_SEND_TEXT`/`VTS_SEND_EMOTION`/
> `STT_AUDIO_RECEIVED` 等细粒度事件）已统一收敛为"阶段流转事件 + Payload 内部 command
> 区分"的模式。例如所有 OBS 操作通过 `OUTPUT_OBS_COMMAND` 单一事件分发，
> 具体动作由 `OBSCommandPayload.action` 字段区分。

### 获取所有事件

```python
all_events = CoreEvents.get_all_events()
print(all_events)
# ('core.startup', 'core.shutdown', 'core.error',
#  'input.message.received', 'input.connected', 'input.disconnected',
#  'decision.intent.generated', 'decision.connected', 'decision.disconnected',
#  'output.intent.dispatched', 'output.handler.connected', 'output.handler.disconnected',
#  'output.intent.finished', 'output.handler.completed',
#  'output.obs.command', 'output.sticker.command')
```

---

## 事件载荷类型

所有事件载荷都继承自 `BasePayload`（基于 Pydantic BaseModel），提供统一的字符串表示和日志格式化。

### Payload 继承关系

```mermaid
classDiagram
    BaseModel <|-- BasePayload
    BasePayload <|-- CoreStartupPayload
    BasePayload <|-- CoreShutdownPayload
    BasePayload <|-- CoreErrorPayload
    BasePayload <|-- RawDataPayload
    BasePayload <|-- MessageReadyPayload
    BasePayload <|-- IntentActionPayload
    BasePayload <|-- IntentPayload
    BasePayload <|-- ConnectedPayload
    BasePayload <|-- DisconnectedPayload
    BasePayload <|-- ConnectionEventPayload
    BasePayload <|-- OBSCommandPayload
    BasePayload <|-- OutputIntentDispatchedPayload
    BasePayload <|-- OutputHandlerCompletedPayload
    BasePayload <|-- StickerCommandPayload
```

> **架构演进**：早期版本中散落的 Payload 类（`DecisionRequestPayload`、
> `ProviderConnectedPayload`、`RenderCompletedPayload`、`ErrorPayload` 等）
> 已统一收敛。当前实际存在的 14 个具体 Payload 类（不含 `BasePayload`）如上图所示，全部定义在
> `src/modules/events/payloads/` 下按阶段分包（`core.py` / `input.py` / `decision.py` /
> `output.py` / `connection.py` / `base.py`）。
>
> **注意**：`RawDataPayload`（`input.raw.data`）与 `IntentActionPayload`（`decision.intent.action`）
> 为历史遗留定义——有 Payload 类但**无生产代码发布或订阅**（仅测试与兼容场景使用）。

### 按阶段分类

#### Core 系统事件

| Payload 类 | 事件名 | 用途 |
|-----------|--------|------|
| `CoreStartupPayload` | `core.startup` | 系统启动事件 |
| `CoreShutdownPayload` | `core.shutdown` | 系统关闭事件 |
| `CoreErrorPayload` | `core.error` | 系统错误事件 |

#### Input 阶段

| Payload 类 | 事件名 | 用途 |
|-----------|--------|------|
| `RawDataPayload` | `input.raw.data` | 原始数据事件（⚠️ 仅定义，无生产发布者，保留供测试/兼容） |
| `MessageReadyPayload` | `input.message.received` | 标准化消息就绪（Input → Decision） |

#### Decision 阶段

| Payload 类 | 事件名 | 用途 |
|-----------|--------|------|
| `IntentPayload` | `decision.intent.generated` | 决策意图生成（Decision → Output） |
| `IntentActionPayload` | `decision.intent.action` | 意图中的单个动作（⚠️ 仅定义，无生产发布者，保留供测试/兼容） |
| `ConnectedPayload` | `decision.connected` | Decider 连接 |
| `DisconnectedPayload` | `decision.disconnected` | Decider 断开 |

#### Connection 通用

| Payload 类 | 事件名 | 用途 |
|-----------|--------|------|
| `ConnectionEventPayload` | `connection.event` | 输入/决策组件连接状态（共用） |

#### Output 阶段

| Payload 类 | 事件名 | 用途 |
|-----------|--------|------|
| `OutputIntentDispatchedPayload` | `output.intent.dispatched` | 过滤后意图进入直接调度流程的监控信号，供 Broadcaster、EventRecorder 等组件观察 |
| `OutputHandlerCompletedPayload` | `output.handler.completed` | Manager 内部兼容的单 Handler 完成语义，Handler 无需发布或订阅 |
| `IntentPayload`（复用） | `output.intent.finished` | Manager 等待全部 active handler 任务结束后发布的完成通知 |
| `OBSCommandPayload` | `output.obs.command` | OBS 统一入口（由 payload.action 区分动作） |
| `StickerCommandPayload` | `output.sticker.command` | 贴图命令 |

> **直接调度与完成时序**：`OutputHandlerManager` 发布 `output.intent.dispatched` 监控信号后，为每个 active handler 创建任务并直接调用 `handle(intent)`。该事件不会触发 Handler。
>
> `_run_handler` 使用 `asyncio.wait_for` 应用 `render_timeout_ms`，并隔离单个 Handler 的超时与异常。Manager 使用 `gather` 等待所有任务结束，再发布 `output.intent.finished`。`render_timeout_ms = 0` 表示不设超时。
>
> Handler 只需实现 `handle(intent)`。它不订阅 `output.intent.dispatched`，也不发布 `output.handler.completed`。完成跟踪由 Manager 内部管理。关心"全部输出已结束"的下游组件应订阅 `output.intent.finished`。

> **架构演进**：早期版本中各细粒度事件（`obs.send_text` / `obs.switch_scene` /
> `obs.set_source_visibility` / `render.completed` / `render.failed` /
> `remote_stream.request_image` 等）已统一收敛为"事件 + Payload.command 区分"的模式。
> 一个事件承担一类操作，具体动作由 Payload 内部字段决定。

### BasePayload 特性

所有 Payload 继承 `BasePayload`，提供以下特性：

```python
from src.modules.events.payloads.base import BasePayload

class MyPayload(BasePayload):
    """自定义 Payload"""

    text: str
    user_name: str

    def get_log_format(self):
        """自定义日志格式"""
        return self.text, self.user_name, None
```

| 方法 | 说明 |
|------|------|
| `__str__()` | 返回易读的调试字符串 |
| `get_log_format()` | 返回 (text, user_name, extra) 元组，用于日志优化 |
| `_format_field_value()` | 格式化字段值 |

---

## 核心特性

| 特性 | 说明 |
|------|------|
| **错误隔离** | 单个 handler 异常不影响其他 handler 执行 |
| **优先级控制** | `priority` 参数控制 handler 执行顺序（数字越小越优先） |
| **统计功能** | 跟踪 emit 次数、错误率、执行时间 |
| **类型安全** | 强制要求 `model_class` 参数，自动反序列化 |
| **生命周期管理** | `cleanup()` 方法确保优雅关闭 |
| **数据验证** | 支持事件数据格式验证（基于 EventRegistry） |
| **日志优化** | Payload 自定义 `__str__` 和 `get_log_format()` 方法 |
| **并发安全** | 使用锁保护统计数据，支持并发 emit |

---

## 使用示例

### 基本发布-订阅

```python
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import MessageReadyPayload

# 创建事件总线
event_bus = EventBus(enable_stats=True)

# 订阅事件（类型化）
async def handle_message(event_name: str, data: MessageReadyPayload, source: str):
    print(f"收到消息: {data.message.get('text')}")

event_bus.on(
    CoreEvents.INPUT_MESSAGE_RECEIVED,
    handle_message,
    model_class=MessageReadyPayload,
    priority=50  # 高优先级
)

# 发布事件
await event_bus.emit(
    CoreEvents.INPUT_MESSAGE_RECEIVED,
    MessageReadyPayload(message={"text": "你好", "source": "console"}, source="console"),
    source="ConsoleInputCollector"
)

# 获取统计
stats = event_bus.get_stats(CoreEvents.INPUT_MESSAGE_RECEIVED)
print(f"Emit次数: {stats.emit_count}, 监听器数: {stats.listener_count}")

# 清理
await event_bus.cleanup()
```

### OutputHandler 中实现直接调用入口

```python
from src.modules.types.intent import Intent

class MyOutputHandler:
    async def handle(self, intent: Intent) -> None:
        print(
            f"收到意图: speech={intent.speech!r}, "
            f"action={intent.action.name if intent.action else None!r}"
        )
        await self.render(intent)
```

`OutputHandlerManager` 会自动调用 active handler 的 `handle(intent)`。Handler 的 `init()` 和 `cleanup()` 只负责自身资源生命周期及专用事件通信，不处理 `OUTPUT_INTENT_DISPATCHED`。

### 发布系统错误事件

```python
from src.modules.logging import get_logger

logger = get_logger(__name__)

try:
    # 可能失败的代码
    await do_something()
except Exception as e:
    # core.error 已绑定 CoreErrorPayload（Broadcaster/EventRecorder 会订阅），
    # 但业务代码通常直接 logger.exception() 记录，不主动发布 core.error
    logger.exception("MyHandler 操作失败")
```

### 发布决策意图

```python
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import IntentPayload

# 从 Intent 对象创建 Payload
intent_payload = IntentPayload.from_intent(intent, name="maibot")

await event_bus.emit(
    CoreEvents.DECISION_INTENT_GENERATED,
    intent_payload,
    source="DecisionManager"
)
```

---

## Mermaid 时序图

### 事件发布-订阅流程

```mermaid
sequenceDiagram
    participant P as 阶段参与者
    participant EB as EventBus
    participant H1 as Handler1 (高优先级)
    participant H2 as Handler2 (低优先级)

    Note over P,EB: 发布事件流程

    P->>EB: emit(event_name, payload)
    EB->>EB: 验证 payload 是 BaseModel
    EB->>EB: _validate_event_data() 校验事件注册

    EB->>EB: 按 priority 排序 handlers
    EB->>EB: 并发执行所有 handler

    par 并行执行
        EB->>H1: _call_handler(wrapper, data)
        H1-->>EB: result / exception
    and 并行执行
        EB->>H2: _call_handler(wrapper, data)
        H2-->>EB: result / exception
    end

    alt error_isolate=True
        EB->>EB: 记录异常, 继续执行其他 handler
    else error_isolate=False
        EB->>P: 抛出第一个异常
    end

    EB->>EB: 更新统计信息
    EB->>P: 返回
```

### 3阶段数据流事件

```mermaid
sequenceDiagram
    participant IP as InputCollector
    participant EB as EventBus
    participant DM as DeciderManager
    participant OHM as OutputHandlerManager
    participant OP as OutputHandler

    Note over IP,EB: Input 阶段

    IP->>EB: emit(input.message.received, MessageReadyPayload)
    EB->>DM: 转发事件

    Note over DM,EB: Decision 阶段

    DM->>DM: decide(message) -> Intent
    DM->>EB: emit(decision.intent.generated, IntentPayload)
    EB->>OHM: 转发事件

    Note over OHM,EB: Output 阶段

    OHM->>OHM: OutputPipeline 过滤
    OHM->>EB: emit(output.intent.dispatched) 监控信号
    par 直接并行调用 active handler
        OHM->>OP: handle(intent)
        OP->>OP: render(intent)
        OP-->>OHM: 返回
    end
    OHM->>EB: emit(output.intent.finished)
```

---

## 最佳实践

### 1. 使用 CoreEvents 常量

```python
# 避免魔法字符串
await event_bus.emit("input.message.received", payload)  # 不推荐

# 使用常量
await event_bus.emit(CoreEvents.INPUT_MESSAGE_RECEIVED, payload)  # 推荐
```

### 2. 正确使用类型化订阅

```python
# 强制指定 model_class
event_bus.on(CoreEvents.INPUT_MESSAGE_RECEIVED, handler, model_class=MessageReadyPayload)

# 不指定 model_class 会导致无法自动反序列化
```

### 3. 处理错误隔离

```python
# 需要确保所有 handler 都执行（默认）
await event_bus.emit(event, data, error_isolate=True)

# 需要立即知道错误
await event_bus.emit(event, data, error_isolate=False)
```

### 4. 优雅关闭

```python
# 在应用关闭时调用 cleanup
async def shutdown():
    await event_bus.cleanup(timeout=5.0)
    print("EventBus 已清理")
```

### 5. 日志优化

```python
class MyPayload(BasePayload):
    text: str
    user_name: str

    def get_log_format(self):
        # 返回 (文本, 用户名, 额外信息)
        return self.text, self.user_name, None

    def __str__(self):
        return f'{self.text} ({self.user_name})'
```

### 6. 避免循环依赖

根据 3 阶段架构约束：
- OutputHandler 不应订阅 Input 事件
- Decider 不应订阅 Output 事件
- InputCollector 不应订阅 Decision/Output 的数据事件（`decision.intent.generated` 等）；但 Output 的**元控制信号**（如 `output.intent.finished`，不携带输出结果）可以做为例外

详见 [数据流规则](data-flow.md)。

---

## 相关文档

- [3阶段架构](overview.md)
- [数据流规则](data-flow.md)
- [阶段参与者开发](../development/component-guide.md)

---

*最后更新：2026-07-31（OutputHandlerManager 改为直接调度，DISPATCHED 仅用于监控，完成跟踪收归 Manager）*
