# AGENTS.md

为在此代码库中工作的 AI 编码代理提供指南。

## 构建/检查/测试命令

### 运行应用程序
```bash
# 正常运行
python main.py

# 调试模式（显示详细日志）
python main.py --debug

# 过滤日志，只显示指定模块（除了WARNING及以上级别的日志）
python main.py --filter StickerPlugin TTSPlugin

# 调试模式并过滤特定模块
python main.py --debug --filter StickerPlugin
```

### 测试
```bash
# 运行所有测试
python -m pytest tests/

# 运行特定测试文件
python -m pytest tests/test_event_system.py

# 详细输出模式
python -m pytest tests/ -v
```

### 代码质量
```bash
# 代码检查
ruff check .

# 代码格式化
ruff format .

# 自动修复可修复的问题
ruff check --fix .
```

### 模拟服务器
```bash
# 当没有部署MaiCore时，使用模拟服务器测试
python mock_maicore.py
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
