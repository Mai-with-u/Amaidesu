# 提示词管理与配置管理

本文档详细介绍 Amaidesu 项目中的提示词管理和配置管理机制。

---

## 提示词管理

### 1. PromptManager 概述

项目使用 **PromptManager** 统一管理所有 LLM 提示词。PromptManager 提供模板加载、变量替换、元数据解析等功能。

**核心特性：**
- 从文件系统加载 `.md` 模板文件
- 解析 YAML Frontmatter 元数据
- 使用 `$variable` 语法进行变量替换
- 支持严格模式和安全模式渲染
- 支持模板 Section 提取

### 2. 快速开始

```python
from src.modules.prompts import get_prompt_manager

# 获取全局单例（推荐）
prompt_mgr = get_prompt_manager()

# 或者手动创建实例
from src.modules.prompts.manager import PromptManager
prompt_mgr = PromptManager()
prompt_mgr.load_all()
```

### 3. 模板目录结构

```
src/modules/prompts/templates/
├── decision/                    # 决策域提示词
│   ├── intent_parser.md        # Intent 解析模板
│   ├── llm.md                  # LLM 对话模板
│   └── llm_structured.md       # 结构化输出模板
├── input/                      # 输入域提示词
│   ├── mainosaba_ocr.md       # OCR 提示词
│   ├── screen_context.md      # 屏幕上下文提示词
│   ├── screen_description.md  # 屏幕描述提示词
│   └── summarize.md           # 摘要提示词
└── output/                     # 输出域提示词
    ├── avatar_expression.md    # 虚拟形象表情模板
    ├── speech.md               # 语音合成模板
    └── vts_hotkey.md           # VTS 热键模板
```

### 4. 模板格式 (YAML Frontmatter)

每个模板文件使用 YAML Frontmatter 定义元数据：

```yaml
---
name: intent_parser
version: "2.0"
description: "Intent 解析系统提示词"
author: Amaidesu
tags: [decision, intent, parser]
variables:
  - text
  - user_name
---

# 模板内容...
请分析以下消息：$text
```

**元数据字段说明：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 模板名称 |
| `version` | string | 否 | 模板版本 |
| `description` | string | 否 | 模板描述 |
| `author` | string | 否 | 作者 |
| `tags` | list[string] | 否 | 标签列表 |
| `variables` | list[string] | 否 | 模板变量列表 |

### 5. 使用方式

#### 5.1 获取原始模板

```python
# 获取原始模板内容（含 Frontmatter）
raw_template = pm.get_raw("decision/intent_parser")
```

#### 5.2 渲染模板（严格模式）

```python
# 渲染模板，缺失变量会抛出 KeyError
prompt = pm.render("decision/intent_parser", text="你好啊", user_name="小明")
```

**严格模式特点：**
- 缺失必需变量会抛出 `KeyError` 异常
- 适用于需要所有变量都必须提供的场景

#### 5.3 安全模式渲染

```python
# 安全模式渲染，缺失变量保留原样
prompt = pm.render_safe("decision/intent_parser", text="你好")
```

**安全模式特点：**
- 缺失变量不会抛出异常，保留为 `$variable` 形式
- 适用于部分变量可选的场景

#### 5.4 提取特定 Section

```python
# 提取并渲染模板中的特定 section
system_msg = pm.extract_section(
    "decision/llm",
    "System Prompt",
    bot_name="麦麦",
    personality="活泼开朗"
)
```

**Section 提取特点：**
- 使用 Markdown `## Section 名称` 格式标记
- 先渲染整个模板，再提取指定 section
- 如果 section 不存在，返回空字符串

#### 5.5 排除特定 Section

```python
# 获取排除指定 section 的内容（如排除 User Message 获取系统提示）
system_prompt = pm.extract_content_without_section(
    "decision/llm",
    "User Message",
    text="你好",
    bot_name="麦麦"
)
```

#### 5.6 列表和元数据

```python
# 列出所有已加载的模板
templates = pm.list_templates()
# ['decision/intent_parser', 'decision/llm', 'output/speech', ...]

# 获取模板元数据
metadata = pm.get_metadata("decision/intent_parser")
# TemplateMetadata(name='intent_parser', version='2.0', ...)
```

### 6. 模板示例

#### 6.1 决策模板示例

```yaml
---
name: local_llm
version: "2.0"
description: "本地 LLM 决策模板"
variables:
  - text
  - bot_name
  - personality
  - user_name
  - max_length
tags: [decision, llm]
---

你是一个 AI VTuber，名字叫 $bot_name。

## 人设特征
性格：$personality

## 用户消息
$user_name 说：$text

## 请生成回复
回复长度控制在 $max_length 字以内。

## 示例
用户: 大家好！
回复: 哈哈，大家好呀！很高兴见到你们~
```

#### 6.2 Intent 解析模板

```yaml
---
name: intent_parser
version: "2.0"
description: "Intent 解析系统提示词"
tags: [decision, intent, parser]
---

你是一个AI VTuber的意图分析助手。

## User Message

请分析以下AI VTuber的回复消息：
$text

## 输出格式
严格按照 JSON 格式输出：
```json
{
  "emotion": "happy",
  "response_text": "回复内容",
  "actions": [{"type": "expression", "params": {"name": "smile"}, "priority": 50}]
}
```
```

### 7. 在 Provider 中使用

```python
from src.modules.prompts import get_prompt_manager

class MyDecisionProvider(DecisionProvider):
    async def _setup_internal(self):
        self._prompt_mgr = get_prompt_manager()

    async def process(self, message: NormalizedMessage) -> Intent:
        # 渲染模板
        prompt = self._prompt_mgr.render(
            "decision/llm",
            text=message.text,
            bot_name="麦麦",
            personality="活泼开朗",
            user_name="大家",
            max_length=50
        )
        # 调用 LLM...
```

---

## 配置管理

### 1. ConfigService 概述

**ConfigService** 是项目的统一配置管理服务，负责：

- 加载和管理主配置文件 (`config.toml`)
- 提供配置合并策略（Schema 默认值 + 主配置覆盖）
- 管理 Provider 和 Pipeline 配置
- 支持配置版本管理和自动更新

### 2. 快速开始

```python
from src.modules.config.service import ConfigService

# 初始化配置服务
config_service = ConfigService(base_dir="/path/to/project")
config, copied, *_ = await config_service.initialize()

# 获取配置节
general_config = config_service.get_section("general")

# 获取 Provider 配置
input_config = config_service.get_provider_config_with_defaults(
    "console_input", "input", ConsoleInputProviderConfig
)
```

### 3. 配置文件结构

#### 3.1 主配置文件

项目使用 TOML 格式的配置文件：

```toml
# 配置文件示例

[meta]
version = "0.2.0"

# ========== 全局 LLM 配置 ==========

[llm]
client = "openai"
model = "gpt-4"
api_key = "your-api-key"
base_url = "https://api.openai.com/v1"
temperature = 0.2
max_tokens = 1024

# ========== 核心配置 ==========

[general]
platform_id = "amaidesu"

[persona]
bot_name = "麦麦"
personality = "活泼开朗"
style_constraints = "口语化，适当使用emoji"
user_name = "大家"
max_response_length = 50
emotion_intensity = 7

[maicore]
host = "127.0.0.1"
port = 8000

# ========== Provider 配置 ==========

[providers.input]
enabled = true
enabled_inputs = ["console_input", "bili_danmaku"]

[providers.output]
enabled = true
enabled_outputs = ["subtitle", "vts", "tts"]

[providers.decision]
enabled = true
active_provider = "maicore"
available_providers = ["maicore", "llm", "maicraft"]

# ========== Pipeline 配置 ==========

[pipelines.rate_limit]
priority = 100
enabled = true
global_rate_limit = 100
user_rate_limit = 10
window_size = 60

[pipelines.similar_filter]
priority = 500
enabled = true
similarity_threshold = 0.85
```

#### 3.2 配置节说明

| 配置节 | 说明 |
|--------|------|
| `[meta]` | 元数据，如配置版本 |
| `[llm]` | 标准 LLM 配置 |
| `[llm_fast]` | 快速 LLM 配置（低延迟任务） |
| `[vlm]` | 视觉语言模型配置 |
| `[llm_local]` | 本地 LLM 配置（Ollama 等） |
| `[general]` | 通用配置 |
| `[persona]` | VTuber 人设配置 |
| `[maicore]` | MaiCore 连接配置 |
| `[context]` | 上下文管理器配置 |
| `[logging]` | 日志配置 |
| `[providers.input]` | 输入 Provider 启用列表 |
| `[providers.output]` | 输出 Provider 启用列表 |
| `[providers.decision]` | 决策 Provider 配置 |
| `[pipelines.*]` | 各 Pipeline 配置 |

### 4. Provider 配置

#### 4.1 启用 Provider

在对应域的配置节中添加 Provider 名称到启用列表：

```toml
# 启用输入 Provider
[providers.input]
enabled_inputs = ["console_input", "bili_danmaku"]

# 启用输出 Provider
[providers.output]
enabled_outputs = ["subtitle", "vts", "tts"]

# 配置决策 Provider
[providers.decision]
active_provider = "maicore"
```

#### 4.2 Provider 特定配置

每个 Provider 可以有自己的配置节：

```toml
# 输入 Provider 配置
[providers.input.console_input]
# ConsoleInputProvider 特定配置

[providers.input.bili_danmaku_official]
id_code = "your_id_code"
app_id = "your_app_id"
access_key = "your_access_key"

# 输出 Provider 配置
[providers.output.tts]
voice = "zh-CN-YunxiNeural"
rate = "+0%"

[providers.output.subtitle]
font_size = 32
window_width = 1000
window_height = 720

# 决策 Provider 配置
[providers.decision.maicore]
host = "127.0.0.1"
port = 8000
```

### 5. 二级配置合并

ConfigService 支持**二级配置合并**，优先级如下：

```
Schema 默认值（优先级低） → 主配置覆盖（优先级高）
```

#### 5.1 获取合并后的配置

```python
from src.modules.config.schemas.input_providers import ConsoleInputProviderConfig

# 获取带默认值合并的配置
config = config_service.get_provider_config_with_defaults(
    "console_input",      # Provider 名称
    "input",               # Provider 类型
    ConsoleInputProviderConfig  # Schema 类（可选）
)
```

#### 5.2 Schema 配置类

每个 Provider 可以定义 Pydantic Schema 配置类：

```python
# src/domains/input/providers/console/config.py
from pydantic import Field
from src.modules.config.schemas.base import BaseProviderConfig


class ConsoleInputProviderConfig(BaseProviderConfig):
    """ConsoleInputProvider 配置"""

    type: str = "console_input"

    # 命令行提示符
    prompt: str = Field(default="> ", description="命令行提示符")

    # 是否启用历史记录
    history_enabled: bool = Field(default=True, description="是否启用历史记录")

    # 最大历史记录数
    max_history: int = Field(default=100, description="最大历史记录数")
```

#### 5.3 合并规则

```python
# 深度合并示例
base = {"a": 1, "b": {"x": 10, "y": 20}}
override = {"b": {"y": 200}, "c": 3}
result = deep_merge_configs(base, override)
# result = {"a": 1, "b": {"x": 10, "y": 200}, "c": 3}
```

**合并规则说明：**
- 基本类型（str, int, float, bool）：override 直接覆盖
- 字典类型：递归合并
- 列表类型：override 完全替换（不合并）
- None：跳过

### 6. 配置 API

#### 6.1 获取配置节

```python
# 获取顶层配置节
general = config_service.get_section("general")

# 获取嵌套配置节（使用点分路径）
input_config = config_service.get_section("providers.input")
maicore_config = config_service.get_section("providers.decision.maicore")
```

#### 6.2 获取配置项

```python
# 从根配置获取
platform_id = config_service.get("platform_id", section="general")

# 带默认值
api_key = config_service.get("api_key", default="default_key")
```

#### 6.3 检查 Provider 启用状态

```python
# 检查 Provider 是否启用
if config_service.is_provider_enabled("bili_danmaku", "input"):
    # ...

if config_service.is_provider_enabled("tts", "output"):
    # ...
```

#### 6.4 检查 Pipeline 启用状态

```python
# Pipeline 启用的条件：定义了 priority 键
if config_service.is_pipeline_enabled("rate_limit"):
    # ...
```

#### 6.5 获取所有配置

```python
# 获取所有 Input Provider 配置
all_inputs = config_service.get_all_provider_configs("input")

# 获取所有 Output Provider 配置
all_outputs = config_service.get_all_provider_configs("output")

# 获取所有 Pipeline 配置
all_pipelines = config_service.get_all_pipeline_configs()
```

### 7. 配置模板生成

ConfigService 支持从 Schema 类自动生成配置模板：

```python
from src.modules.config.schemas.input_providers import ConsoleInputProviderConfig

# 生成 TOML 配置文件
ConsoleInputProviderConfig.generate_toml(
    output_path="config_example.toml",
    provider_name="providers.input.console_input",
    include_comments=True
)
```

生成的配置文件：

```toml
# ConsoleInputProvider 配置

[providers.input.console_input]
# 命令行提示符
prompt = "> "
# 是否启用历史记录
history_enabled = true
# 最大历史记录数
max_history = 100
```

### 8. 模板配置文件

项目提供 `config-template.toml` 模板文件，首次运行时会自动复制为 `config.toml`：

```bash
# 首次运行
uv run python main.py
# 会自动从 config-template.toml 生成 config.toml
```

---

## 相关文档

- [Provider 开发指南](provider-guide.md) - 如何开发自定义 Provider
- [管道开发指南](pipeline-guide.md) - 如何开发自定义 Pipeline
- [开发规范](../development-guide.md) - 代码风格和约定
- [3域架构](../architecture/overview.md) - 架构设计总览
- [事件系统](../architecture/event-system.md) - EventBus 使用指南
