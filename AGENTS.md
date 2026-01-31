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
uv run python main.py --filter StickerPlugin TTSPlugin

# 调试模式并过滤特定模块
uv run python main.py --debug --filter StickerPlugin
```

### 测试
```bash
# 运行所有测试
uv run pytest tests/

# 运行特定测试文件
uv run pytest tests/test_event_system.py

# 详细输出模式
uv run pytest tests/ -v
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
- **类名**：PascalCase（如 `AmaidesuCore`, `BasePlugin`, `MessagePipeline`）
- **函数/方法名**：snake_case（如 `send_to_maicore`, `register_websocket_handler`）
- **变量名**：snake_case（如 `plugin_config`, `event_bus`）
- **私有成员**：前导下划线（如 `_message_handlers`, `_is_connected`）
- **插件类**：以 `Plugin` 结尾（如 `ConsoleInputPlugin`, `TTSPlugin`）
- **管道类**：以 `Pipeline` 结尾（如 `ThrottlePipeline`, `CommandRouterPipeline`）
- **插件入口点**：模块级别的 `plugin_entrypoint` 变量

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

class MyPlugin(BasePlugin):
    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info("插件初始化")
```

### 插件开发规范

#### 新架构（推荐）

新插件应使用 Plugin 协议，通过 event_bus 和 config 进行依赖注入：

1. 不继承任何基类
2. 实现 `__init__(config)`, `setup(event_bus, config)`, `cleanup()` 方法
3. 在 `setup()` 中创建 Provider 并返回 Provider 列表
4. 在 `cleanup()` 中清理资源
5. 定义 `plugin_entrypoint` 作为模块入口点
6. 在 `__init__.py` 中导出插件类

```python
# src/plugins/my_plugin/plugin.py
from typing import Dict, Any, List
from src.core.plugin import Plugin
from src.core.providers.input_provider import InputProvider
from .providers.my_provider import MyProvider

class MyPlugin:
    """
    我的插件（使用新架构）

    不继承 BasePlugin，实现 Plugin 协议
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._providers: List[InputProvider] = []

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表
        """
        # 创建 Provider
        provider = MyProvider(config)
        self._providers.append(provider)

        return self._providers

    async def cleanup(self):
        """清理资源"""
        for provider in self._providers:
            await provider.cleanup()
        self._providers.clear()

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "MyPlugin",
            "version": "1.0.0",
            "author": "Author",
            "description": "My plugin description",
            "category": "input",  # input/output/processing
            "api_version": "1.0",
        }

plugin_entrypoint = MyPlugin
```

#### 旧架构（已废弃）

⚠️ **BasePlugin 已废弃，仅用于向后兼容**

旧插件使用 BasePlugin 基类，通过 self.core 访问核心功能：

1. 继承 `BasePlugin` 类
2. 实现 `__init__()`, `setup()`, `cleanup()` 方法
3. 在 `setup()` 中注册处理器和服务
4. 在 `cleanup()` 中清理资源
5. 定义 `plugin_entrypoint` 作为模块入口点
6. 在 `__init__.py` 中导出插件类

```python
# src/plugins/my_plugin/plugin.py
from src.core.plugin_manager import BasePlugin
from src.core.amaidesu_core import AmaidesuCore
from typing import Dict, Any

class MyPlugin(BasePlugin):
    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        # 初始化逻辑

    async def setup(self):
        # 注册处理器
        await self.core.register_websocket_handler("text", self.handle_message)
        # 注册服务
        self.core.register_service("my_service", self)

    async def cleanup(self):
        # 清理资源
        await super().cleanup()

plugin_entrypoint = MyPlugin
```

**注意**：当前只有 gptsovits_tts 插件使用旧架构，其他插件已迁移到新架构。

### Provider 接口说明

新架构插件通过 Provider 封装具体功能，提供更好的解耦和可测试性。

#### Provider 类型

- **InputProvider**: 输入 Provider，从外部数据源采集数据
  - 示例：BiliDanmakuInputProvider（B站弹幕）、ConsoleInputProvider（控制台输入）
- **OutputProvider**: 输出 Provider，渲染到目标设备
  - 示例：TTSOutputProvider（语音合成）、SubtitleOutputProvider（字幕显示）
- **DecisionProvider**: 决策 Provider，处理 CanonicalMessage
  - 示例：CommandRouterProvider（命令路由）

#### Provider 开发示例

```python
# src/plugins/my_plugin/providers/my_provider.py
from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData
from typing import AsyncIterator, Optional, Dict, Any
from src.utils.logger import get_logger

class MyInputProvider(InputProvider):
    """自定义输入 Provider"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)
        # 初始化逻辑

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """采集数据"""
        while not self._stop_event.is_set():
            # 采集数据
            data = await self._fetch_data()
            if data:
                raw_data = RawData(
                    content={"data": data},
                    source="my_provider",
                    data_type="text",
                )
                yield raw_data

            await asyncio.sleep(1)

    async def _cleanup(self):
        """清理资源"""
        self.logger.info("MyInputProvider 清理完成")
```

### 管道开发规范
1. 继承 `MessagePipeline` 类
2. 实现 `process_message()` 方法
3. 可选实现 `on_connect()` 和 `on_disconnect()` 方法
4. 使用 `self.config` 访问配置
5. 返回 `MessageBase` 继续传递，返回 `None` 丢弃消息

```python
# src/pipelines/my_pipeline/pipeline.py
from src.core.pipeline_manager import MessagePipeline
from maim_message import MessageBase
from typing import Optional, Dict, Any

class MyPipelinePipeline(MessagePipeline):
    priority = 500

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.param = self.config.get("param", "default")

    async def process_message(self, message: MessageBase) -> Optional[MessageBase]:
        # 处理消息
        return message  # 或 return None 丢弃
```

### 配置文件
- 配置文件使用 TOML 格式
- 插件配置：`src/plugins/{plugin_name}/config-template.toml`
- 管道配置：`src/pipelines/{pipeline_name}/config-template.toml`
- 根配置：根目录的 `config-template.toml`
- 首次运行会自动从模板生成 `config.toml`

### 测试规范
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

### 事件系统
- 插件可以通过 Event Bus 发布和订阅事件
- 发布事件：`await self.emit_event("event.name", data)`
- 订阅事件：`self.listen_event("event.name", handler)`
- 取消订阅：`self.stop_listening_event("event.name", handler)`

### 服务注册
- 插件可以将自己注册为服务供其他插件使用
- 注册服务：`self.core.register_service("service_name", self)`
- 获取服务：`service = self.core.get_service("service_name")`
- 服务名称应为描述性的字符串（如 "vts_control", "tts"）

### 禁止事项
- 不要使用 `as any` 或 `@ts-ignore`（Python 中对应的类型忽略）
- 不要使用空的 except 块
- 不要删除失败的测试来"通过"
- 不要在修复 bug 时进行大规模重构
- 不要提交未验证的代码（没有运行测试和 lint）
- 不要在类变量中存储可变对象（如 `dict` 或 `list`）

### 通信模式
项目支持两种互补的通信模式：
1. **服务注册机制（请求-响应）**：稳定的、长期存在的功能（如 TTS、VTS 控制）
2. **事件系统（发布-订阅）**：瞬时通知、广播场景

两者可以共存以保持向后兼容。

### 中文注释和文档
- 项目使用中文作为注释和用户界面语言
- 文档字符串（docstring）和注释应使用清晰、准确的中文
- 变量名和函数名仍使用英文命名
