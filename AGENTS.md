# AGENTS.md

为在此代码库中工作的 AI 编码代理提供指南。

## 构建/检查/测试命令

### 包管理器

本项目使用 [uv](https://docs.astral.sh/uv/) 作为 Python 包管理器。

```bash
# 安装 uv（如果尚未安装）
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 开发环境设置
```bash
# 同步依赖（自动创建虚拟环境并安装所有依赖）
uv sync

# 安装包含语音识别（STT）的依赖
uv sync --extra stt

# 安装所有可选依赖
uv sync --all-extras

# 添加新依赖
uv add package-name

# 添加开发依赖
uv add --dev package-name

# 移除依赖
uv remove package-name

# 升级特定依赖
uv lock --upgrade-package package-name
```

### 运行应用程序
```bash
# 正常运行
uv run python main.py

# 调试模式（显示详细日志）
uv run python main.py --debug

# 过滤日志，只显示指定模块（除了WARNING及以上级别的日志）
uv run python main.py --filter TTSProvider SubtitleProvider

# 调试模式并过滤特定模块
uv run python main.py --debug --filter VTSProvider
```

### 测试
```bash
# 运行所有测试
uv run pytest tests/

# 运行特定测试文件
uv run pytest tests/layers/input/test_console_provider.py

# 运行特定测试用例
uv run pytest tests/layers/input/test_console_provider.py::test_console_provider_basic

# 详细输出模式
uv run pytest tests/ -v

# 只运行标记的测试
uv run pytest -m "not slow"
```

### 代码质量
```bash
# 代码检查
uv run ruff check .

# 代码格式化
uv run ruff format .

# 自动修复可修复的问题
uv run ruff check --fix .
```

### 模拟服务器
```bash
# 当没有部署MaiCore时，使用模拟服务器测试
uv run python mock_maicore.py
```

## 代码风格指南

### 导入语句组织
1. 标准库导入（如 `asyncio`, `typing`, `os`）
2. 第三方库导入（如 `aiohttp`, `loguru`）
3. 本地项目导入（从 `src` 开始）
4. 使用 `TYPE_CHECKING` 避免循环导入

```python
import asyncio
from typing import TYPE_CHECKING, Any, Dict, Optional

from aiohttp import web
from loguru import logger

from src.core.amaidesu_core import AmaidesuCore
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from .some_module import SomeClass
```

### 类型注解
- 总是使用类型注解：函数参数、返回值、类属性
- 使用 `typing` 模块中的类型：`Dict`, `List`, `Optional`, `Union`, `Any`
- 类型参数使用小写：`Dict[str, Any]`, `Optional[str]`

```python
async def handle_message(self, message: MessageBase) -> Optional[MessageBase]:
    """处理消息并返回处理结果"""
    pass

def __init__(self, config: Dict[str, Any]):
    self.logger = get_logger(self.__class__.__name__)
```

### 命名约定
- **类名**：PascalCase（如 `AmaidesuCore`, `InputProvider`, `MessagePipeline`）
- **函数/方法名**：snake_case（如 `send_to_maicore`, `register_websocket_handler`）
- **变量名**：snake_case（如 `provider_config`, `event_bus`）
- **私有成员**：前导下划线（如 `_message_handlers`, `_is_connected`）
- **Provider类**：以 `Provider` 结尾（如 `ConsoleInputProvider`, `TTSSProvider`）
- **管道类**：以 `Pipeline` 结尾（如 `RateLimitPipeline`, `SimilarTextFilterPipeline`, `MessageLoggerPipeline`）

### 格式化规范（基于 pyproject.toml）
- 行长度：120 字符
- 字符串引号：双引号（`"`）
- 缩进：4 个空格（不使用 Tab）
- 保留尾随逗号（用于多行列表/字典）
- 自动检测换行符（LF/CRLF）

### Async/Await 模式
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

### 错误处理
- 使用 try-except 捕获异常，并在 except 中记录日志时使用 `exc_info=True`
- 不要使用空的 except 块：`except: pass` 是反模式
- 对可预期的错误提供有意义的错误消息
- 使用特定异常类型（如 `ValueError`, `ConnectionError`）

```python
try:
    result = await some_async_operation()
except Exception as e:
    self.logger.error(f"操作失败: {e}", exc_info=True)
    raise ConnectionError(f"无法连接到服务: {e}") from e
```

### 日志记录
- 使用 `get_logger(module_name)` 获取 logger 实例
- 模块名通常使用类名（如 `get_logger("AmaidesuCore")`）
- 日志级别：`DEBUG`, `INFO`, `WARNING`, `ERROR`
- 重要操作始终记录日志（启动、连接、错误、清理）

```python
from src.utils.logger import get_logger

class MyInputProvider(InputProvider):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info("Provider初始化")
```

## Provider 开发规范

### 概述

项目使用 Provider 系统封装具体功能，由 Manager 统一管理，配置驱动启用。

**Provider 类型**：
- **InputProvider**: 输入 Provider，从外部数据源采集数据
- **DecisionProvider**: 决策 Provider，处理 NormalizedMessage 生成 Intent
- **OutputProvider**: 输出 Provider，渲染到目标设备

### InputProvider 开发

输入 Provider 从外部数据源采集数据，继承 `InputProvider` 基类。

```python
# src/domains/input/providers/my_provider/my_input_provider.py
from typing import AsyncIterator, Dict, Any
from src.core.base.input_provider import InputProvider
from src.core.base.raw_data import RawData
from src.core.utils.logger import get_logger

class MyInputProvider(InputProvider):
    """自定义输入 Provider"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)
        # 初始化逻辑

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """采集数据"""
        while self.is_running:
            # 采集数据逻辑
            data = await self._fetch_data()
            if data:
                yield RawData(
                    content={"data": data},
                    source="my_provider",
                    data_type="text",
                )
            await self._sleep_if_running(1.0)

    async def _fetch_data(self):
        """实现具体的数据采集逻辑"""
        # ... 实现细节
        pass
```

### DecisionProvider 开发

决策 Provider 处理 NormalizedMessage 生成 Intent，继承 `DecisionProvider` 基类。

```python
# src/domains/decision/providers/my_provider/my_decision_provider.py
from typing import Dict, Any
from src.core.base.decision_provider import DecisionProvider
from src.core.base.normalized_message import NormalizedMessage
from src.domains.decision.intent import Intent
from src.core.utils.logger import get_logger

class MyDecisionProvider(DecisionProvider):
    """自定义决策 Provider"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)

    async def decide(self, message: NormalizedMessage) -> Intent:
        """决策逻辑"""
        # 实现决策逻辑
        return Intent(
            type="response",
            content="响应内容",
            parameters={},
        )
```

### OutputProvider 开发

输出 Provider 渲染到目标设备，继承 `OutputProvider` 基类。

```python
# src/domains/output/providers/my_provider/my_output_provider.py
from typing import Dict, Any
from src.core.base.output_provider import OutputProvider
from src.domains.output.parameters.render_parameters import RenderParameters
from src.core.utils.logger import get_logger

class MyOutputProvider(OutputProvider):
    """自定义输出 Provider"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)

    async def render(self, parameters: RenderParameters):
        """渲染逻辑"""
        # 实现渲染逻辑
        self.logger.info(f"渲染参数: {parameters}")
```

### Provider 注册

在 Provider 的 `__init__.py` 中注册到 ProviderRegistry：

```python
# src/domains/input/providers/my_provider/__init__.py
from src.domains.output.provider_registry import ProviderRegistry
from .my_input_provider import MyInputProvider

ProviderRegistry.register_input("my_provider", MyInputProvider, source="builtin:my_provider")
```

### 配置启用

在配置文件中启用 Provider：

```toml
# 输入Provider
[providers.input]
enabled_inputs = ["console_input", "my_provider"]

[providers.input.inputs.my_provider]
type = "my_provider"
# Provider特定配置
api_url = "https://api.example.com"

# 决策Provider
[providers.decision]
active_provider = "my_provider"
available_providers = ["maicore", "my_provider"]

[providers.decision.providers.my_provider]
type = "my_provider"
# Provider特定配置

# 输出Provider
[providers.output]
enabled_outputs = ["subtitle", "my_provider"]

[providers.output.outputs.my_provider]
type = "my_provider"
# Provider特定配置
```

## 管道开发规范

管道用于在消息处理流程中进行预处理，位于 Input Domain 内部。

### 管道开发

1. 继承 `TextPipeline` 类
2. 实现 `process()` 方法
3. 返回 `NormalizedMessage` 继续传递，返回 `None` 丢弃消息

```python
# src/domains/input/pipelines/my_pipeline/pipeline.py
from src.domains.input.pipeline_manager import TextPipeline
from src.core.base.normalized_message import NormalizedMessage
from typing import Optional, Dict, Any

class MyPipeline(TextPipeline):
    priority = 500

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.param = self.config.get("param", "default")

    async def process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        # 处理消息
        return message  # 或 return None 丢弃
```

### 管道配置

```toml
# 根 config.toml
[pipelines]
  [pipelines.my_pipeline]
  priority = 500
```

## 配置文件

- 配置文件使用 TOML 格式
- Provider 配置：`[providers.input]`, `[providers.decision]`, `[providers.output]`
- 管道配置：`[pipelines.*]`
- 根配置：根目录的 `config-template.toml`
- 首次运行会自动从模板生成 `config.toml`

## 测试规范

- 使用 pytest 编写测试
- 测试文件名：`test_*.py`
- 测试函数名：`async def test_*():`
- 使用 `assert` 进行断言
- 异步测试函数必须使用 `asyncio.run()` 或 pytest-asyncio

```python
import asyncio
import pytest

async def test_something():
    result = await some_async_function()
    assert result is not None
    assert result.value == "expected"

# 使用 pytest-asyncio
@pytest.mark.asyncio
async def test_with_marker():
    pass
```

## 事件系统

- 组件通过 EventBus 发布和订阅事件
- 优先使用 `CoreEvents` 常量，避免硬编码字符串
- 核心事件使用 Pydantic Model 确保类型安全

```python
# 发布事件
from src.core.events.names import CoreEvents
await event_bus.emit(CoreEvents.NORMALIZATION_MESSAGE_READY, normalized_message)

# 订阅事件
await event_bus.subscribe(CoreEvents.NORMALIZATION_MESSAGE_READY, self.handle_message)
```

## 禁止事项

- ❌ 不要创建新的 Plugin（插件系统已移除）
- ❌ 不要使用服务注册机制（已废弃），用 EventBus
- ❌ 不要硬编码事件名字符串，使用 CoreEvents 常量
- ❌ 不要使用空的 except 块
- ❌ 不要删除失败的测试来"通过"
- ❌ 不要在修复 bug 时进行大规模重构
- ❌ 不要提交未验证的代码（没有运行测试和 lint）
- ❌ 不要在类变量中存储可变对象（如 `dict` 或 `list`）
- ❌ 不要直接在 main.py 中创建 Provider，用 Manager + 配置驱动

## 通信模式

项目使用 **EventBus** 作为唯一的跨域通信机制：
- **事件系统（发布-订阅）**：瞬时通知、广播场景
- 支持优先级、错误隔离、统计功能
- 使用 CoreEvents 常量确保类型安全

## 中文注释和文档

- 项目使用中文作为注释和用户界面语言
- 文档字符串（docstring）和注释应使用清晰、准确的中文
- 变量名和函数名仍使用英文命名

## 目录结构

```
Amaidesu/
├── main.py              # CLI入口（参数解析、启动应用）
└── src/
    ├── amaidesu_core.py # 核心协调器（管理组件生命周期）
    ├── core/            # 基础设施（事件、基类、通信、工具）
    ├── services/        # 共享服务（LLM、配置、上下文）
    └── domains/         # 业务域（input、decision、output）
```

## 3域架构说明

| 域 | 职责 | 位置 |
|----|------|------|
| **Input Domain** | 数据采集 + 标准化 + 预处理 | `src/domains/input/` |
| **Decision Domain** | 决策（可替换） | `src/domains/decision/` |
| **Output Domain** | 参数生成 + 渲染 | `src/domains/output/` |

### 数据流

```
外部输入（弹幕、游戏、语音）
  ↓
【Input Domain】外部数据 → NormalizedMessage
  ├─ InputProvider: 并发采集数据
  ├─ Normalization: 标准化
  └─ Pipelines: 预处理（限流、过滤）
  ↓ EventBus: normalization.message_ready
【Decision Domain】NormalizedMessage → Intent
  ├─ MaiCoreDecisionProvider (默认)
  ├─ LocalLLMDecisionProvider (可选)
  └─ RuleEngineDecisionProvider (可选)
  ↓ EventBus: decision.intent_generated
【Output Domain】Intent → 实际输出
  ├─ Parameters: 参数生成（情绪→表情等）
  └─ OutputProvider: 并发渲染（TTS、字幕、VTS等）
```

详细设计文档见：`refactor/design/overview.md`

## 开发注意事项

### 添加新Provider
1. 在对应域创建Provider目录：`src/domains/{domain}/providers/my_provider/`
2. 继承对应的Provider基类（InputProvider/DecisionProvider/OutputProvider）
3. 在Provider的`__init__.py`中注册到ProviderRegistry
4. 在配置中启用：
   - InputProvider: 添加到 `[providers.input]` 的 `enabled_inputs` 列表
   - OutputProvider: 添加到 `[providers.output]` 的 `enabled_outputs` 列表
   - DecisionProvider: 添加到 `[providers.decision]` 的 `available_providers` 列表

### 事件使用规范
- **使用常量**：优先使用`CoreEvents`常量，避免硬编码字符串
- **核心事件用Pydantic Model**：确保类型安全
- **事件名分层**：使用点分层（如`decision.intent_generated`）

### 日志使用
```python
from src.utils.logger import get_logger

logger = get_logger("MyClassName")  # 使用类名或模块名
logger.info("信息日志")
logger.debug("调试日志", extra_context={"key": "value"})
logger.error("错误日志", exc_info=True)
```

**日志过滤**：使用`--filter`参数时，传入get_logger的第一个参数（类名或模块名）
