# 开发规范

本文档定义了 Amaidesu 项目的代码规范和最佳实践，所有开发者必须遵守。

## 1. 代码风格

### 1.1 语言规范

- **注释和文档**：使用中文编写注释和文档字符串
- **变量命名**：使用 snake_case（如 `provider_config`, `event_bus`）
- **函数命名**：使用 snake_case（如 `send_to_maicore`, `register_websocket_handler`）
- **类命名**：使用 PascalCase（如 `EventBus`, `InputProvider`）
- **常量命名**：使用 CamelCase（如 `CoreEvents`, `EmotionType`）

### 1.2 命名约定表

| 类型 | 命名风格 | 示例 |
|------|---------|------|
| 类名 | PascalCase | `EventBus`, `InputProvider`, `InputPipeline` |
| 函数/方法名 | snake_case | `send_to_maicore`, `register_websocket_handler` |
| 变量名 | snake_case | `provider_config`, `event_bus` |
| 私有成员 | 前导下划线 | `_message_handlers`, `_is_connected` |
| Provider 类 | 以 `Provider` 结尾 | `ConsoleInputProvider`, `TTSSProvider` |
| 管道类 | 以 `Pipeline` 结尾 | `RateLimitPipeline`, `SimilarTextFilterPipeline` |
| 常量类 | PascalCase | `CoreEvents`, `EmotionType` |

### 1.3 注释规范

```python
class MyProvider(OutputProvider):
    """
    Provider 类的中文文档说明

    负责描述这个类的职责和主要功能。
    """

    async def execute(self, intent: Intent) -> None:
        """执行意图渲染，将 Intent 输出到目标设备"""
        # 处理具体逻辑
        pass
```

## 2. 类型注解

### 2.1 必须使用类型注解

所有函数和方法必须有完整的类型注解，包括参数类型和返回值类型。

```python
# ✅ 正确：完整的类型注解
async def handle_message(self, message: MessageBase) -> Optional[MessageBase]:
    """处理消息并返回处理结果"""
    pass

def __init__(self, config: Dict[str, Any]):
    self.logger = get_logger(self.__class__.__name__)
    self.config = config

# ❌ 错误：缺少类型注解
async def handle_message(self, message):
    pass

def __init__(self, config):
    self.config = config
```

### 2.2 常用类型导入

```python
from typing import (
    Any,
    Dict,
    List,
    Optional,
    AsyncIterator,
    Callable,
    TypeVar,
    Generic,
)
from pydantic import BaseModel, Field
```

## 3. 数据类型选用规范

### 3.1 选用原则

| 类型 | 使用场景 | 示例 |
|------|----------|------|
| **Pydantic BaseModel** | 所有数据模型、配置 Schema、事件 Payload | `class UserConfig(BaseModel)` |
| **dataclass** | 仅用于简单的内部统计/包装类 | `@dataclass class PipelineStats` |
| **Protocol** | 定义接口协议 | `class InputPipeline(Protocol)` |
| **Enum** | 定义常量集合 | `class EmotionType(str, Enum)` |

### 3.2 Pydantic 使用示例

```python
from pydantic import BaseModel, Field, ConfigDict
from typing import Dict, Any, Optional
from enum import Enum

class EmotionType(str, Enum):
    """情感类型枚举"""
    NEUTRAL = "neutral"
    HAPPY = "happy"

class Intent(BaseModel):
    """决策意图"""
    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(..., description="唯一标识符")
    response_text: str = Field(..., description="回复文本")
    emotion: EmotionType = Field(default=EmotionType.NEUTRAL, description="情感类型")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
```

### 3.3 dataclass 使用示例

仅用于简单的内部统计类：

```python
from dataclasses import dataclass

@dataclass
class PipelineStats:
    """Pipeline 统计信息"""
    processed_count: int = 0
    dropped_count: int = 0
    error_count: int = 0

    @property
    def avg_duration_ms(self) -> float:
        """平均处理时间"""
        if self.processed_count == 0:
            return 0.0
        return self.total_duration_ms / self.processed_count
```

## 4. 日志使用

### 4.1 日志模块导入

```python
from src.modules.logging import get_logger

logger = get_logger("MyClassName")  # 使用类名或模块名作为标识
```

### 4.2 日志级别使用

```python
logger.info("信息日志")  # 常规操作信息
logger.debug("调试日志", extra_context={"key": "value"})  # 调试信息
logger.warning("警告日志")  # 警告信息
logger.error("错误日志", exc_info=True)  # 错误信息，包含堆栈
```

### 4.3 日志过滤

使用 `--filter` 参数时，传入 `get_logger` 的第一个参数（类名或模块名）决定是否显示：

```bash
# 只显示 SubtitleProvider 和 EdgeTTSProvider 的日志
uv run python main.py --filter SubtitleProvider EdgeTTSProvider
```

## 5. 事件使用规范

### 5.1 使用 CoreEvents 常量

禁止硬编码事件名字符串，必须使用 `CoreEvents` 常量：

```python
from src.modules.events.names import CoreEvents

# ✅ 正确：使用常量
await event_bus.emit(CoreEvents.DATA_MESSAGE, normalized_message)
event_bus.on(CoreEvents.OUTPUT_INTENT, self._on_intent, model_class=IntentPayload)

# ❌ 错误：硬编码字符串
await event_bus.emit("data.message", normalized_message)
```

### 5.2 CoreEvents 常用常量

```python
class CoreEvents:
    # 核心事件
    DATA_MESSAGE = "data.message"
    DECISION_INTENT = "decision.intent"
    OUTPUT_INTENT = "output.intent"

    # 系统事件
    CORE_STARTUP = "core.startup"
    CORE_SHUTDOWN = "core.shutdown"
    CORE_ERROR = "core.error"
```

### 5.3 事件 Payload 要求

核心事件使用 Pydantic Model 作为 Payload：

```python
from pydantic import BaseModel, Field

class IntentPayload(BaseModel):
    """Intent 事件载荷"""
    intent: Intent = Field(..., description="意图对象")
```

## 6. 禁止事项

| 禁止 | 原因 | 替代方案 |
|------|------|----------|
| ❌ 创建新的 Plugin | 插件系统已移除 | 创建 Provider |
| ❌ 使用服务注册机制 | 已废弃 | 使用 EventBus |
| ❌ 硬编码事件名字符串 | 避免拼写错误 | 使用 `CoreEvents` 常量 |
| ❌ 使用空的 except 块 | 隐藏错误 | 记录日志并处理 |
| ❌ 删除失败的测试来"通过" | 自欺欺人 | 修复代码或测试 |
| ❌ 在修复 bug 时进行大规模重构 | 扩大风险范围 | 只修复 bug |
| ❌ 提交未验证的代码 | 可能破坏构建 | 先运行测试和 lint |
| ❌ 类变量中存储可变对象 | 共享状态问题 | 使用 `__init__` 初始化 |
| ❌ 在 main.py 中直接创建 Provider | 违反配置驱动原则 | 使用 Manager + 配置驱动 |

### 6.1 架构约束：3域数据流规则

严格遵守单向数据流：**Input Domain → Decision Domain → Output Domain**

| 禁止模式 | 说明 |
|---------|------|
| ❌ Output Provider 订阅 Input 事件 | 绕过 Decision Domain，破坏分层 |
| ❌ Decision Provider 订阅 Output 事件 | 创建循环依赖 |
| ❌ Input Provider 订阅 Decision/Output 事件 | Input 应只发布，不订阅下游 |

## 7. 测试规范

### 7.1 测试文件组织

- 测试文件名：`test_*.py`
- 测试函数名：`async def test_*():` 或 `def test_*():`
- 异步测试使用 `@pytest.mark.asyncio` 装饰器

### 7.2 测试示例

```python
import pytest
from pydantic import BaseModel, Field

# 测试用的 Pydantic Model
class SimpleTestEvent(BaseModel):
    message: str = Field(default="test", description="测试消息")
    id: int = Field(default=0, description="ID")

@pytest.fixture
def event_bus():
    """创建标准 EventBus 实例"""
    return EventBus()

@pytest.mark.asyncio
async def test_event_emission(event_bus: EventBus):
    """测试事件发布"""
    received = []

    async def handler(event_name: str, payload: SimpleTestEvent, source: str):
        received.append(payload)

    event_bus.on("test.event", handler, SimpleTestEvent)
    await event_bus.emit("test.event", SimpleTestEvent(message="hello"), source="test")

    await pytest.asyncio.sleep(0.1)  # 等待异步处理

    assert len(received) == 1
    assert received[0].message == "hello"
```

### 7.3 测试目录结构

```
tests/
├── core/              # 核心模块测试
│   ├── events/        # 事件系统测试
│   ├── types/         # 类型测试
│   └── config/        # 配置测试
├── domains/           # Domain 测试
│   ├── input/         # 输入域测试
│   ├── decision/      # 决策域测试
│   └── output/        # 输出域测试
├── services/          # 服务测试
└── mocks/             # 测试用 Mock 对象
```

## 8. 提交前检查

### 8.1 必须执行的检查

每次提交代码前，必须依次执行以下检查：

```bash
# 1. 运行测试
uv run pytest tests/

# 2. 代码检查
uv run ruff check .

# 3. 代码格式化
uv run ruff format .
```

### 8.2 常用命令速查

```bash
# 运行所有测试
uv run pytest tests/

# 运行特定测试文件
uv run pytest tests/core/test_event_bus.py -v

# 排除慢速测试
uv run pytest -m "not slow"

# 代码检查（不自动修复）
uv run ruff check .

# 代码检查（自动修复）
uv run ruff check --fix .

# 代码格式化
uv run ruff format .
```

### 8.3 提交信息规范

使用清晰的中文提交信息，描述本次修改的内容：

```
feat: 添加新的字幕 Provider
fix: 修复弹幕解析的边界情况
refactor: 重构 Provider 生命周期管理
docs: 更新开发规范文档
```

## 9. 配置规范

### 9.1 配置文件格式

- 使用 TOML 格式
- Provider 配置：`[providers.input]`, `[providers.decision]`, `[providers.output]`
- 管道配置：`[pipelines.*]`

### 9.2 配置示例

```toml
# 输入 Provider
[providers.input]
enabled_inputs = ["console_input", "bili_danmaku"]

# 决策 Provider
[providers.decision]
active_provider = "maicore"

# 输出 Provider
[providers.output]
enabled_outputs = ["subtitle", "vts", "tts"]
```

## 10. Provider 开发规范

### 10.1 Provider 类型

| 类型 | 职责 | 位置 |
|------|------|------|
| InputProvider | 从外部数据源采集数据 | `src/domains/input/providers/` |
| DecisionProvider | 处理 NormalizedMessage 生成 Intent | `src/domains/decision/providers/` |
| OutputProvider | 渲染到目标设备 | `src/domains/output/providers/` |

### 10.2 Provider 生命周期

| Provider 类型 | 启动方法 | 停止方法 |
|--------------|---------|---------|
| InputProvider | `start()` | `stop()` |
| DecisionProvider | `setup()` | `cleanup()` |
| OutputProvider | `setup()` | `cleanup()` |

### 10.3 添加新 Provider 步骤

1. 继承对应的 Provider 基类
2. 在 Provider 的 `__init__.py` 中注册到 ProviderRegistry
3. 在配置文件中启用

详见：[Provider 开发指南](development/provider-guide.md)

## 相关文档

- [Provider 开发](development/provider-guide.md) - Provider 开发详解
- [管道开发](development/pipeline-guide.md) - Pipeline 开发详解
- [提示词管理](development/prompt-management.md) - PromptManager 使用
- [测试指南](development/testing-guide.md) - 测试规范和最佳实践
- [3域架构](architecture/overview.md) - 架构设计详解
- [事件系统](architecture/event-system.md) - EventBus 使用指南
- [数据流规则](architecture/data-flow.md) - 数据流约束和规则
