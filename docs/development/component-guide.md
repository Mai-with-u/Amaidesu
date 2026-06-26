# 阶段参与者开发指南

本指南介绍如何在 Amaidesu 项目中开发自定义阶段参与者。阶段参与者（InputCollector / Decider / OutputHandler）通过装饰器注册到对应 Manager，由 Manager 统一管理生命周期。

## 1. 架构概览

Amaidesu 采用 3 阶段架构，阶段参与者分为三种类型：

| 类型 | 职责 | 位置 | 注册装饰器 | Manager |
|------|------|------|------------|---------|
| **InputCollector** | 从外部数据源采集数据 | `src/stages/input/collectors/` | `@collector("xxx")` | `InputCollectorManager` |
| **Decider** | 处理 `NormalizedMessage` 生成 `Intent` | `src/stages/decision/deciders/` | `@decider("xxx")` | `DeciderManager` |
| **OutputHandler** | 将 `Intent` 渲染到目标设备 | `src/stages/output/handlers/` | `@handler("xxx")` | `OutputHandlerManager` |

### 数据流

```
外部输入 → InputCollector.start() / collect()
                          ↓
              NormalizedMessage (event: input.message.received)
                          ↓
                    Decider.decide()
                          ↓
                  Intent (event: decision.intent.generated)
                          ↓
              OutputHandlerManager.dispatch()
                          ↓
              Intent (event: output.intent.dispatched)
                          ↓
                  OutputHandler.handle()
                          ↓
                    实际渲染输出
```

## 2. 装饰器注册

所有阶段参与者使用装饰器注册到 Manager，无需手动注册到 dict。

### 2.1 InputCollector 注册

```python
from src.stages.input.registry import collector

@collector("my_input")
class MyInputCollector:
    def __init__(self, config: dict, event_bus: EventBus):
        self.config = config
        self.event_bus = event_bus
        # ...
```

注册后，`InputCollectorManager` 通过 `_COLLECTORS["my_input"]` 自动发现。

### 2.2 Decider 注册

```python
from src.stages.decision.registry import decider

@decider("my_decider")
class MyDecider:
    def __init__(self, config: dict, event_bus: EventBus):
        # ...
```

### 2.3 OutputHandler 注册

```python
from src.stages.output.registry import handler

@handler("my_handler")
class MyOutputHandler:
    def __init__(self, config: dict, event_bus: EventBus):
        # ...
```

## 3. 生命周期

阶段参与者遵循统一的生命周期：每个参与者定义 **3-4 个方法**，由 Manager 在不同阶段调用。

### 3.1 InputCollector 生命周期

| 方法 | 调用时机 | 职责 |
|------|----------|------|
| `__init__(config, event_bus)` | 注册时 | 存储配置和依赖 |
| `async start()` | Manager 启动时 | 启动采集（建连、订阅） |
| `async collect() → AsyncIterator[NormalizedMessage]` | Manager 启动后 | 持续产出消息 |
| `async stop()` | Manager 停止时 | 停止采集 |
| `async cleanup()` (可选) | Manager 清理时 | 释放资源 |

实际示例参见 `src/stages/input/collectors/console_input/console_input_collector.py`。

### 3.2 Decider 生命周期

| 方法 | 调用时机 | 职责 |
|------|----------|------|
| `__init__(config, event_bus)` | 注册时 | 存储配置和依赖 |
| `async setup()` | Manager 启动时 | 初始化资源、建连 |
| `async decide(normalized_message)` | 每个消息 | 生成 Intent（同步处理或异步发布） |
| `async cleanup()` | Manager 停止时 | 释放资源 |

实际示例参见 `src/stages/decision/deciders/llm/llm_decider.py`。

### 3.3 OutputHandler 生命周期

| 方法 | 调用时机 | 职责 |
|------|----------|------|
| `__init__(config, event_bus)` | 注册时 | 存储配置和依赖 |
| `async init()` | Manager 启动时 | 初始化资源、订阅事件 |
| `async handle(intent)` | 每个派发的 Intent | 实际渲染 |
| `async cleanup()` | Manager 停止时 | 释放资源、取消订阅 |

实际示例参见 `src/stages/output/handlers/subtitle/subtitle_handler.py`。

## 4. Handler 事件订阅模式

Handler 通过订阅 `OUTPUT_INTENT_DISPATCHED` 事件接收 Intent，**不**通过 Manager 注入的回调执行。

### 4.1 推荐订阅模式（幂等）

```python
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import IntentPayload

class MyHandler:
    def __init__(self, config, event_bus):
        self.event_bus = event_bus
        self._dispatch_subscribed = False

    async def init(self):
        if self.event_bus and not self._dispatch_subscribed:
            self.event_bus.on(
                CoreEvents.OUTPUT_INTENT_DISPATCHED,
                self._handle_intent_dispatched,
                model_class=IntentPayload,
            )
            self._dispatch_subscribed = True

    async def _handle_intent_dispatched(self, event_name, payload, source):
        intent = payload.to_intent()
        await self.handle(intent)

    async def cleanup(self):
        if self.event_bus and self._dispatch_subscribed:
            self.event_bus.off(
                CoreEvents.OUTPUT_INTENT_DISPATCHED,
                self._handle_intent_dispatched,
            )
            self._dispatch_subscribed = False

    async def handle(self, intent):
        # 实际渲染逻辑
        ...
```

模板见 `src/stages/output/handlers/subtitle/subtitle_handler.py:329-346`。

### 4.2 跨 Handler 通信

通过专用事件实现 Handler 间解耦通信，避免直接查找其他 Handler。典型模式：

- **事件名常量**：在 `src/modules/events/names.py` 添加（如 `OUTPUT_STICKER_COMMAND`）
- **Payload**：在 `src/modules/events/payloads/output.py` 定义（带 `@register_event`）
- **发布者**：emit 事件
- **订阅者**：订阅事件并通过 `target_handler` 字段过滤

实例：`StickerHandler` 通过 `OUTPUT_STICKER_COMMAND` 通知 `VTSHandler` 显示贴纸。

## 5. 配置系统

所有阶段参与者使用 Pydantic BaseConfig 子类定义配置（自动验证 + JSON Schema 派生）。

```python
from pydantic import Field
from src.modules.config.schemas.base import BaseConfig

@handler("my_handler")
class MyHandler:
    class ConfigSchema(BaseConfig):
        type: str = "my_handler"
        api_key: str = Field(default="", description="API 密钥")
        timeout_seconds: float = Field(default=10.0, ge=0.0, le=60.0)

    def __init__(self, config: dict, event_bus: EventBus):
        self.typed_config = self.ConfigSchema.from_dict(config)
        # 使用 self.typed_config.api_key 而非 self.config["api_key"]
```

## 6. 时间与单位约定

- **时刻字段**（如事件时间戳）统一为 `int` Unix 毫秒（13 位整数）
- **时长字段**（如超时、节流间隔）统一为 `int` 毫秒
- **使用 `now_ms()`** 获取当前时刻，**不使用** `time.time() * 1000`

```python
from src.modules.time_utils import now_ms, elapsed_ms

ts = now_ms()           # int 毫秒
duration = elapsed_ms(ts)  # int 毫秒
```

## 7. 错误处理

- **`except CancelledError` 是合理用法**，保留
- **可选依赖 `except ImportError: pass`** 合理保留（pyvts、edge_tts 等）
- **业务异常 `except Exception`** 必须有日志或兜底（如 `sys.stderr.write`）

```python
# 可选依赖 (acceptable pattern)
try:
    import pyvts  # noqa: F401
except ImportError:
    self.logger.warning("pyvts库不可用，VTSHandler将被禁用")

# 业务错误 (必须记录)
try:
    await self._send_to_remote(data)
except Exception as e:
    self.logger.error(f"发送失败: {e}", exc_info=True)
```

## 8. 测试与 Mock

- **集成测试**：使用真实组件
- **单元测试**：使用 `MockOutputHandler` 等 Mock 类（位于 `tests/mocks/`）
- **断言**：使用 `assert` 语句，不用 `print("OK: ...")`
- **配置**：测试用 `MockOutputHandler` 不应在生产 config 启用

```python
from src.stages.output.handlers.mock import MockOutputHandler
from src.modules.events.payloads import IntentPayload

handler = MockOutputHandler({}, mock_event_bus)
await handler.init()
await handler.handle(test_intent)
assert len(handler.get_received_intents()) == 1
```

## 9. 已下线组件（了解历史）

以下组件曾在早期版本存在，已在 P1 重构中删除，**新代码不应使用**：

| 组件 | 替代方案 |
|------|----------|
| `CapabilityRegistry` | Pydantic `model_json_schema()` 自动派生 |
| `OutputCapabilityRegistry` | 同上 |
| `intent_structuring_pipeline` | 删除（不需要替代） |
| `LLMClient` ABC | `OpenAIClient` 唯一实现 |
| `Storage` ABC | `MemoryStorage` 唯一实现 |
| `TTSManager` 单例 + `get_tts_manager()` | 直接 `GPTSoVITSClient(host, port)` |
| `AbstractActionFactory` | MaicraftDecider 内置实现 |
| `CapabilityPipelineContext.capability_registry` | 删除 |
| `InputPipeline` / `OutputPipeline` Protocol 重复定义 | 保留 ABC 版本（`InputPipelineBase` / `OutputPipelineBase`） |
| `src/modules/di/` 空模块 | 已删除 |

## 10. 最佳实践

1. **优先组合而非继承**（VTS Handler 已拆分为 VTSHandler + LipSyncProcessor + HotkeyMatcher + ExpressionController）
2. **避免类级可变字典**（AGENTS.md 禁止）；实例级 dict 放在 `__init__`
3. **事件命名遵循动词链**：`input.message.received` → `decision.intent.generated` → `output.intent.dispatched`
4. **避免 magic number**（P3-8 TODO）；超时、节流间隔应放 config
5. **资源生命周期**（如 Client、Connection）在 `start/init` 创建，`stop/cleanup` 释放，避免泄漏
6. **依赖通过构造函数注入**，便于测试

## 11. 相关文档

- [3 阶段架构总览](../architecture/overview.md)
- [数据流规则](../architecture/data-flow.md)
- [事件系统](../architecture/event-system.md)
- [管道开发](pipeline-guide.md)
- [测试指南](testing-guide.md)
