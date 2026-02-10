# 开发规范

本文档描述 Amaidesu 项目的代码风格和开发约定。

## 代码风格

### 导入语句组织

按以下顺序组织导入：

1. 标准库导入（如 `asyncio`, `typing`, `os`）
2. 第三方库导入（如 `aiohttp`, `loguru`）
3. 本地项目导入（从 `src` 开始）
4. 使用 `TYPE_CHECKING` 避免循环导入

```python
import asyncio
from typing import TYPE_CHECKING, Any, Dict, Optional

from aiohttp import web
from loguru import logger

from src.core.event_bus import EventBus
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from .some_module import SomeClass
```

### 类型注解

- 总是使用类型注解：函数参数、返回值、类属性
- 使用 `typing` 模块中的类型
- 类型参数使用小写

```python
async def handle_message(self, message: MessageBase) -> Optional[MessageBase]:
    """处理消息并返回处理结果"""
    pass

def __init__(self, config: Dict[str, Any]):
    self.logger = get_logger(self.__class__.__name__)
```

### 命名约定

| 类型 | 规范 | 示例 |
|------|------|------|
| **类名** | PascalCase | `EventBus`, `InputProvider` |
| **函数/方法名** | snake_case | `send_to_maicore`, `register_websocket_handler` |
| **变量名** | snake_case | `provider_config`, `event_bus` |
| **私有成员** | 前导下划线 | `_message_handlers`, `_is_connected` |
| **Provider 类** | 以 `Provider` 结尾 | `ConsoleInputProvider`, `EdgeTTSProvider` |
| **管道类** | 以 `Pipeline` 结尾 | `RateLimitPipeline`, `SimilarTextFilterPipeline` |

### 格式化规范

基于 `pyproject.toml` 配置：

- **行长度**：120 字符
- **字符串引号**：双引号（`"`）
- **缩进**：4 个空格（不使用 Tab）
- **尾随逗号**：保留（用于多行列表/字典）
- **换行符**：自动检测（LF/CRLF）

## 数据类型选用

项目统一使用 **Pydantic BaseModel** 作为主要数据结构类型。

| 类型 | 使用场景 | 示例 |
|------|----------|------|
| **Pydantic BaseModel** | 所有数据模型、配置 Schema、事件 Payload | `class UserConfig(BaseModel)` |
| **dataclass** | 仅用于简单的内部统计/包装类 | `@dataclass class PipelineStats` |
| **Protocol** | 定义接口协议 | `class TextPipeline(Protocol)` |

### Pydantic BaseModel 使用示例

```python
from pydantic import BaseModel, Field, ConfigDict

class UserConfig(BaseModel):
    """用户配置"""

    name: str = Field(description="用户名")
    age: int = Field(default=0, ge=0, le=150)
    enabled: bool = True

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "amaidesu",
                "age": 18,
                "enabled": True
            }
        }
    )
```

### 何时使用 dataclass

仅当满足以下**所有**条件时：

1. 简单的数据容器，无需验证
2. 内部使用，不对外暴露 API
3. 不需要序列化/反序列化

```python
from dataclasses import dataclass

# ✅ 合理使用
@dataclass
class PipelineStats:
    processed_count: int = 0
    error_count: int = 0
```

## Async/Await 模式

- 所有 I/O 操作使用 `async/await`
- 处理器必须是异步函数：`async def handler(...):`
- 使用 `asyncio.create_task()` 创建后台任务
- 使用 `asyncio.Lock()` 保护共享资源
- 优先使用 `asyncio.gather()` 并发执行多个异步操作

```python
async def process_messages(self):
    tasks = []
    for message in messages:
        tasks.append(self.handle_message(message))
    await asyncio.gather(*tasks, return_exceptions=True)
```

## 错误处理

- 使用 try-except 捕获异常
- 在 except 中记录日志时使用 `exc_info=True`
- 不要使用空的 except 块
- 对可预期的错误提供有意义的错误消息
- 使用特定异常类型

```python
try:
    result = await some_async_operation()
except Exception as e:
    self.logger.error(f"操作失败: {e}", exc_info=True)
    raise ConnectionError(f"无法连接到服务: {e}") from e
```

## 日志记录

### 获取 Logger

```python
from src.utils.logger import get_logger

logger = get_logger("MyClassName")  # 使用类名或模块名
```

### 日志级别

- `DEBUG` - 调试信息
- `INFO` - 常规信息
- `WARNING` - 警告
- `ERROR` - 错误

### 日志过滤

使用 `--filter` 参数时，传入 `get_logger` 的第一个参数（类名或模块名）：

```bash
# 只显示 EdgeTTSProvider 和 SubtitleProvider 的日志
uv run python main.py --filter EdgeTTSProvider SubtitleProvider
```

### 日志最佳实践

```python
class MyInputProvider(InputProvider):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info("Provider初始化")

    async def _some_operation(self):
        self.logger.debug("调试信息", extra_context={"key": "value"})
        self.logger.info("常规操作")
        self.logger.warning("警告信息")
        self.logger.error("错误", exc_info=True)  # 记录异常堆栈
```

## 禁止事项

- ❌ 不要创建新的 Plugin（插件系统已移除）
- ❌ 不要使用服务注册机制（已废弃），用 EventBus
- ❌ 不要硬编码事件名字符串，使用 `CoreEvents` 常量
- ❌ 不要使用空的 except 块
- ❌ 不要删除失败的测试来"通过"
- ❌ 不要在修复 bug 时进行大规模重构
- ❌ 不要提交未验证的代码（没有运行测试和 lint）
- ❌ 不要在类变量中存储可变对象（如 `dict` 或 `list`）
- ❌ 不要直接在 `main.py` 中创建 Provider，用 Manager + 配置驱动
- ❌ **不要让 Output Provider 直接订阅 Input 事件**（违反架构分层）
- ❌ **不要让 Decision Provider 订阅 Output 事件**（创建循环依赖）
- ❌ **不要让 Input Provider 订阅 Decision/Output 事件**（Input 应只发布）

## 通信模式

项目使用 **EventBus** 作为唯一的跨域通信机制：

- **事件系统（发布-订阅）**：瞬时通知、广播场景
- 支持优先级、错误隔离、统计功能
- 使用 `CoreEvents` 常量确保类型安全

```python
# 发布事件
from src.core.events.names import CoreEvents
await event_bus.emit(CoreEvents.NORMALIZATION_MESSAGE_READY, normalized_message)

# 订阅事件
await event_bus.subscribe(CoreEvents.NORMALIZATION_MESSAGE_READY, self.handle_message)
```

## 中文注释和文档

- 项目使用中文作为注释和用户界面语言
- 文档字符串（docstring）和注释应使用清晰、准确的中文
- 变量名和函数名仍使用英文命名

## Git 规范

### 提交消息格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Type 类型

- `feat`: 新功能
- `fix`: 修复 Bug
- `refactor`: 重构
- `docs`: 文档
- `test`: 测试
- `chore`: 构建/工具

### 示例

```
feat(decision): 添加本地LLM决策Provider

实现本地LLM决策Provider，支持OpenAI兼容API。

- 添加 LocalLLMDecisionProvider 类
- 实现 decide() 方法
- 添加配置支持

Closes #123
```

## 测试规范

详细测试指南见：[测试规范](development/testing-guide.md)

### 基本原则

- 使用 pytest 编写测试
- 测试文件名：`test_*.py`
- 测试函数名：`async def test_*():`
- 异步测试函数必须使用 `pytest-asyncio`

```python
import pytest

@pytest.mark.asyncio
async def test_something():
    result = await some_async_function()
    assert result is not None
```

## 代码审查

提交前检查：

```bash
# 代码检查
uv run ruff check .

# 自动修复
uv run ruff check --fix .

# 格式化
uv run ruff format .

# 运行测试
uv run pytest tests/

# 运行架构测试
uv run pytest tests/architecture/test_event_flow_constraints.py -v
```

---

*更多开发指南见：[Provider 开发](development/provider-guide.md)*
