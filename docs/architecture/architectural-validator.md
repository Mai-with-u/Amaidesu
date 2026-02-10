# 架构约束运行时验证器

## 概述

`ArchitecturalValidator` 是一个运行时验证器，用于强制执行3域架构的事件订阅约束，防止违反分层原则。

## 功能

验证器通过包装 `EventBus.on()` 和 `EventBus.on_typed()` 方法，在运行时验证订阅关系是否符合架构约束：

- **Input Domain**: 只发布事件，不订阅任何下游事件
- **Decision Domain**: 可以订阅 Input 事件，但不能订阅 Output 事件
- **Output Domain**: 可以订阅 Decision 事件，但不能订阅 Input 事件
- **Core Orchestrator** (FlowCoordinator): 可以协调跨域事件

## 使用方式

### 1. 通过命令行参数启用（推荐）

在启动应用时添加 `--arch-validate` 参数：

```bash
uv run python main.py --arch-validate
```

### 2. 在代码中手动启用

```python
from src.modules.events.architectural_validator import ArchitecturalValidator

# 创建 EventBus
event_bus = EventBus()

# 启用架构验证
validator = ArchitecturalValidator(event_bus, enabled=True)
```

## 配置选项

### 启用/禁用验证器

```python
# 创建时启用
validator = ArchitecturalValidator(event_bus, enabled=True)

# 创建后禁用
validator.disable()

# 重新启用
validator.enable()
```

### 严格模式

```python
# 严格模式：未配置的订阅者也会被阻止
validator = ArchitecturalValidator(event_bus, enabled=True, strict=True)

# 非严格模式（默认）：未配置的订阅者允许所有事件（向后兼容）
validator = ArchitecturalValidator(event_bus, enabled=True, strict=False)
```

## 允许的订阅关系

验证器在 `ALLOWED_SUBSCRIPTIONS` 中定义了每个类允许订阅的事件：

```python
ALLOWED_SUBSCRIPTIONS = {
    # Input Domain (只发布，不订阅)
    "InputProvider": None,
    "InputProviderManager": None,

    # Decision Domain (可订阅 Input，不可订阅 Output)
    "DecisionManager": ["normalization.message_ready", ...],
    "DecisionProvider": ["normalization.message_ready"],

    # Output Domain (可订阅 Decision，不可订阅 Input)
    "OutputProvider": ["expression.*", "output.*", ...],

    # Core Orchestrator (可协调跨域)
    "FlowCoordinator": ["decision.intent_generated", ...],
}
```

## 错误示例

当尝试违反架构约束时，会抛出 `ArchitecturalViolationError`：

```python
# ❌ 错误：InputProvider 不应该订阅任何事件
class MyInputProvider(InputProvider):
    async def initialize(self):
        # 这会抛出 ArchitecturalViolationError
        await self.event_bus.on(
            CoreEvents.DECISION_INTENT_GENERATED,
            self.handler
        )

# ✅ 正确：DecisionProvider 可以订阅 Input 事件
class MyDecisionProvider(DecisionProvider):
    async def initialize(self):
        await self.event_bus.on(
            CoreEvents.NORMALIZATION_MESSAGE_READY,
            self.handler
        )

# ❌ 错误：OutputProvider 不应该订阅 Input 事件
class MyOutputProvider(OutputProvider):
    async def initialize(self):
        # 这会抛出 ArchitecturalViolationError
        await self.event_bus.on(
            CoreEvents.NORMALIZATION_MESSAGE_READY,
            self.handler
        )
```

## 测试

运行架构验证器测试：

```bash
uv run pytest tests/core/events/test_architectural_validator.py -v
```

## 注意事项

1. **仅验证实例方法订阅**: 验证器通过检查调用栈来识别订阅者，只对类的实例方法有效（即通过 `self.event_bus.on()` 调用）。

2. **基类继承**: 如果一个类没有在 `ALLOWED_SUBSCRIPTIONS` 中配置，验证器会尝试通过继承关系查找配置。

3. **通配符支持**: 事件模式支持通配符，例如 `"expression.*"` 匹配所有以 `expression.` 开头的事件。

4. **开发工具**: 这是一个开发辅助工具，在 `--arch-validate` 模式下启用，用于在开发阶段尽早发现架构违规。
