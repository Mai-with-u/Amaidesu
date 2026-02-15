# 核心模块变化

本文档详细对比重构前后核心模块的变化，解释新架构如何改进提示词管理、上下文管理和日志系统。

---

## 一、Prompts 模块（提示词管理）

### 重构前：无专门的提示词模块

重构前没有专门的提示词管理模块，Prompts 是**硬编码**在 LLMClient 代码中，直接传递字符串：

```python
# amaidesu-dev/src/openai_client/llm_request.py
async def chat_completion(
    self,
    prompt: str,
    system_message: Optional[str] = None,
    ...
):
    messages = []
    if system_message:
        messages.append({"role": "system", "content": system_message})
    messages.append({"role": "user", "content": prompt})
```

**问题**：
- 没有模板概念，prompts 直接以字符串形式存在代码中
- 无法版本化管理
- 修改 prompts 需要修改代码
- 多个地方使用相似的提示词，无法复用

### 重构后：PromptManager + 模板文件

```python
# 目录结构
src/modules/prompts/
├── __init__.py           # 导出 get_prompt_manager()
├── manager.py            # 核心 PromptManager 类
└── templates/            # 提示词模板目录
    ├── decision/
    │   └── intent.md
    └── output/
        └── vts_hotkey.md
```

**模板格式** (YAML frontmatter + Markdown):

```markdown
---
name: intent_parser
description: 意图解析提示词
version: 1.0.0
variables:
  - user_name
  - message
---

## System Message
你是一个助手...

## User Message
用户 $user_name 说: $message
```

**使用方式**：

```python
from src.modules.prompts import get_prompt_manager

# 获取全局单例
prompt_mgr = get_prompt_manager()

# 渲染模板（严格模式）- 缺失变量会抛异常
result = prompt_mgr.render("decision/intent", user_name="Alice", message="你好")

# 安全模式渲染 - 缺失变量保留原样
result = prompt_mgr.render_safe("decision/intent", user_name="Alice")

# 获取元数据
metadata = prompt_mgr.get_metadata("decision/intent")

# 提取特定 section（不含 section 标记）
system_prompt = prompt_mgr.extract_content_without_section("decision/intent", "User Message")
```

### 改进对比

| 特性 | 重构前 | 重构后 |
|------|--------|--------|
| 存储方式 | 硬编码字符串 | .md 模板文件 |
| 变量替换 | 无 | string.Template |
| 元数据 | 无 | YAML frontmatter |
| 严格模式 | - | render() 缺失变量抛异常 |
| 安全模式 | - | render_safe() 缺失变量保留原样 |
| Section 提取 | - | 支持提取/排除特定 section |
| 版本管理 | 无 | version 字段支持 |

### 迁移指南

```python
# 重构前：直接传递字符串
system_message = "你是一个助手..."
user_message = f"用户 {name} 说: {message}"

# 重构后：使用模板
from src.modules.prompts import get_prompt_manager
prompt_mgr = get_prompt_manager()
system_prompt = prompt_mgr.render("decision/intent", user_name=name, message=message)
```

---

## 二、Context 模块（上下文管理）

### 重构前：ContextManager

**文件位置**：`amaidesu-dev/src/core/context_manager.py`

重构前的 ContextManager 主要是为 LLM 提示词提供**附加上下文信息**（如 VTS 动作信息、情绪状态等），各个插件可以注册自己的上下文：

```python
from src.utils.logger import get_logger

class ContextManager:
    """管理和聚合可附加到提示的上下文信息"""

    def __init__(self, config: Dict[str, Any]):
        self.logger = get_logger("ContextManager")
        self._context_providers: Dict[str, ContextProviderData] = {}

    def register_context_provider(
        self,
        provider_name: str,
        context_info: Any,
        priority: Optional[int] = None,
        tags: Optional[List[str]] = None,
        enabled: bool = True,
    ) -> bool:
        """注册上下文提供者"""

    async def get_formatted_context(
        self,
        tags: Optional[List[str]] = None,
        max_length: Optional[int] = None
    ) -> str:
        """获取格式化的上下文字符串"""
```

**用途**：聚合外部上下文信息，为 LLM 提供附加信息。

### 重构后：ContextService

**文件位置**：`src/modules/context/`

```
context/
├── __init__.py           # 导出 ContextService, MessageRole
├── config.py             # ContextServiceConfig 配置
├── models.py             # 数据模型（ConversationMessage）
├── service.py            # 核心 ContextService 类
└── storage/
    ├── __init__.py
    └── memory.py         # MemoryStorage 实现
```

重构后的 ContextService 专注于**对话历史管理**：

```python
from src.modules.context import ContextService, MessageRole

# 创建服务
context_service = ContextService()
await context_service.initialize()

# 添加消息
await context_service.add_message(
    session_id="console_input",
    role=MessageRole.USER,
    content="你好",
)

# 添加助手回复
await context_service.add_message(
    session_id="console_input",
    role=MessageRole.ASSISTANT,
    content="有什么可以帮助你的？",
)

# 获取历史
history = await context_service.get_history("console_input", limit=10)

# 构建 LLM 上下文（OpenAI messages 格式）
context = await context_service.build_context("console_input")
# 返回: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]

# 清理
await context_service.cleanup()
```

**支持的数据模型**：

```python
from src.modules.context import MessageRole

class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
```

### 改进对比

| 特性 | 重构前 (ContextManager) | 重构后 (ContextService) |
|------|------------------------|------------------------|
| 职责 | 聚合外部上下文信息 | 管理对话历史 |
| 数据模型 | ContextProviderData (TypedDict) | ConversationMessage (Pydantic) |
| 存储 | 无（实时计算） | MemoryStorage 支持持久化 |
| 会话管理 | 无 | 多会话隔离 |
| 输出格式 | 拼接字符串 | OpenAI messages 格式 |
| 生命周期 | 无 | initialize/cleanup |
| 配置驱动 | 配置分散 | ContextServiceConfig 统一配置 |

### 迁移指南

```python
# 重构前：注册上下文提供者
from src.core.context_manager import ContextManager
ctx = ContextManager(config)
ctx.register_context_provider("vts", {"action": "smile", "emotion": "happy"})
context_str = await ctx.get_formatted_context()

# 重构后：管理对话历史
from src.modules.context import ContextService, MessageRole
ctx = ContextService()
await ctx.initialize()
await ctx.add_message(session_id="default", role=MessageRole.USER, content="你好")
history = await ctx.get_history(session_id="default")
context = await ctx.build_context(session_id="default")
```

---

## 三、Logging 模块（日志系统）

### 重构前：utils/logger

**文件位置**：`amaidesu-dev/src/utils/logger.py`

重构前的日志系统在**模块导入时就添加 handler**：

```python
import sys
from loguru import logger

# 模块加载时直接配置！
logger.remove()

logger.add(
    sys.stderr,
    level="INFO",
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | ...",
)

def get_logger(module_name: str):
    return logger.bind(module=module_name)
```

**问题**：
- 在模块导入时就添加 handler，可能导致重复
- 无文件日志功能
- 无配置驱动
- 无法延迟初始化
- 无法按模块过滤

### 重构后：modules/logging

**文件位置**：`src/modules/logging/`

重构后的日志系统支持**配置驱动**和**延迟初始化**：

```python
# main.py 中调用 - 应用启动时配置
from src.modules.logging import configure_from_config, get_logger

# 配置日志系统
configure_from_config({
    "enabled": True,
    "format": "jsonl",              # jsonl 或 text
    "directory": "logs",           # 日志文件目录
    "level": "INFO",               # 文件日志级别
    "rotation": "10 MB",           # 单文件大小限制
    "retention": "7 days",         # 保留时间
    "compression": "zip",          # 压缩格式
    "console_level": "INFO",       # 控制台日志级别
    "filter": ["MyClassName"],     # 只显示指定模块日志（用于 --filter 参数）
})

# 使用
logger = get_logger("MyClassName")
logger.info("消息内容")
logger.debug("调试信息")
logger.error("错误信息", exc_info=True)
```

**延迟初始化**：

```python
def get_logger(module_name: str):
    # 若尚未配置，自动创建默认 stderr handler
    _ensure_default_handler()
    return loguru_logger.bind(module=module_name)
```

### 日志过滤使用

```bash
# 运行应用时过滤特定模块日志
uv run python main.py --filter EdgeTTSProvider SubtitleProvider
```

```python
# 内部实现
configure_from_config({
    "filter": ["EdgeTTSProvider", "SubtitleProvider"],
    ...
})

# 只显示 filter 中包含的模块日志
```

### 改进对比

| 特性 | 重构前 | 重构后 |
|------|--------|--------|
| 初始化时机 | 模块导入时 | 应用启动时（延迟） |
| 配置方式 | 硬编码 | configure_from_config() |
| 文件日志 | 无 | JSONL/text 格式 |
| 日志轮转 | 无 | rotation/retention/compression |
| 模块过滤 | 无 | filter 参数支持 |
| 控制台级别 | 固定 INFO | 可配置 console_level |
| 重复 handler | 可能重复 | 自动清理 |

### 迁移指南

```python
# 重构前
from src.utils.logger import get_logger
logger = get_logger("MyClassName")
logger.info("消息")

# 重构后
from src.modules.logging import configure_from_config, get_logger

# main.py 中配置一次
configure_from_config({"level": "INFO", "directory": "logs"})

# 使用（与之前相同）
logger = get_logger("MyClassName")
logger.info("消息")
```

---

## 四、模块迁移速查

| 模块 | 重构前 | 重构后 |
|------|--------|--------|
| **Prompts** | 硬编码字符串 | `src.modules.prompts.PromptManager` |
| **Context** | `src.core.context_manager.ContextManager` | `src.modules.context.ContextService` |
| **Logging** | `src.utils.logger` | `src.modules.logging` |

### API 快速对照

```python
# === Prompts ===
# 重构前: 无
# 重构后:
from src.modules.prompts import get_prompt_manager
prompt = get_prompt_manager().render("template_name", var="value")

# === Context ===
# 重构前:
from src.core.context_manager import ContextManager
ctx = ContextManager(config)
await ctx.get_formatted_context()

# 重构后:
from src.modules.context import ContextService, MessageRole
ctx = ContextService()
await ctx.initialize()
history = await ctx.get_history(session_id)
context = await ctx.build_context(session_id)

# === Logging ===
# 重构前:
from src.utils.logger import get_logger

# 重构后:
from src.modules.logging import configure_from_config, get_logger

# main.py 中配置一次
configure_from_config(config)
logger = get_logger("ModuleName")
```

---

*最后更新：2026-02-15*
