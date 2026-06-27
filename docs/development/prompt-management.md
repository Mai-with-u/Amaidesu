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
├── decision/                    # 决策阶段提示词
│   └── llm.md                  # LLM 对话模板
├── input/                      # 输入阶段提示词
│   ├── mainosaba_ocr.md       # OCR 提示词
│   ├── screen_context.md      # 屏幕上下文提示词
│   ├── screen_description.md  # 屏幕描述提示词
│   └── summarize.md           # 摘要提示词
└── output/                     # 输出阶段提示词
    ├── avatar_expression.md    # 虚拟形象表情模板
    ├── speech.md               # 语音合成模板
    └── vts_hotkey.md           # VTS 热键模板
```

### 4. 模板格式 (YAML Frontmatter)

每个模板文件使用 YAML Frontmatter 定义元数据：

```yaml
---
name: local_llm
version: "2.0"
description: "本地 LLM 决策模板"
author: Amaidesu
tags: [decision, llm]
variables:
  - text
  - bot_name
  - personality
  - user_name
---

# 模板内容...
你是一个 AI VTuber，名字叫 $bot_name。
$user_name 说：$text
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
raw_template = pm.get_raw("decision/llm")
```

#### 5.2 渲染模板（严格模式）

```python
# 渲染模板，缺失变量会抛出 KeyError
prompt = pm.render("decision/llm", text="你好啊", bot_name="麦麦", personality="活泼开朗", user_name="小明")
```

**严格模式特点：**
- 缺失必需变量会抛出 `KeyError` 异常
- 适用于需要所有变量都必须提供的场景

#### 5.3 安全模式渲染

```python
# 安全模式渲染，缺失变量保留原样
prompt = pm.render_safe("decision/llm", text="你好")
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
# ['decision/llm', 'output/speech', 'output/vts_hotkey', ...]

# 获取模板元数据
metadata = pm.get_metadata("decision/llm")
# TemplateMetadata(name='local_llm', version='2.0', ...)
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

### 7. 在阶段参与者中使用

```python
from src.modules.prompts import get_prompt_manager

class MyDecider(Decider):
    async def _setup_internal(self):
        self._prompt_mgr = get_prompt_manager()

    async def decide(self, message: NormalizedMessage) -> None:
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
- 管理阶段参与者和 Pipeline 配置
- 支持配置版本管理和自动更新

### 2. 快速开始

```python
from src.modules.config.service import ConfigService

# 初始化配置服务
config_service = ConfigService(base_dir="/path/to/project")
config, copied, *_ = await config_service.initialize()

# 获取配置节
general_config = config_service.get_section("general")

# 获取 Collector 配置
input_config = config_service.get_collector_config_with_defaults(
    "console_input", ConsoleInputCollectorConfig
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

[maibot]
host = "127.0.0.1"
port = 8000

# ========== 阶段参与者配置 ==========

[collectors]
enabled = true
enabled = ["console_input", "bili_danmaku"]

[handlers]
enabled = true
enabled = ["subtitle", "vts", "tts"]

[deciders]
enabled = true
active = "maibot"
available = ["maibot", "llm", "command"]

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
| `[maibot]` | MaiBot 连接配置 |
| `[context]` | 上下文管理器配置 |
| `[logging]` | 日志配置 |
| `[collectors]` | 输入 Collector 启用列表 |
| `[handlers]` | 输出 Handler 启用列表 |
| `[deciders]` | 决策 Decider 配置 |
| `[pipelines.*]` | 各 Pipeline 配置 |

### 4. 阶段参与者配置

#### 4.1 启用阶段参与者

在对应阶段的配置节中添加参与者名称到启用列表：

```toml
# 启用输入 Collector
[collectors]
enabled = ["console_input", "bili_danmaku"]

# 启用输出 Handler
[handlers]
enabled = ["subtitle", "vts", "tts"]

# 配置决策 Decider
[deciders]
active = "maibot"
```

#### 4.2 阶段参与者特定配置

每个阶段参与者可以有自己的配置节：

```toml
# 输入 Collector 配置
[collectors.console_input]
# ConsoleInputCollector 特定配置

[collectors.bili_danmaku_official]
id_code = "your_id_code"
app_id = "your_app_id"
access_key = "your_access_key"

# 输出 Handler 配置
[handlers.tts]
voice = "zh-CN-YunxiNeural"
rate = "+0%"

[handlers.subtitle]
font_size = 32
window_width = 1000
window_height = 720

# 决策 Decider 配置
[deciders.maibot]
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
from src.modules.config.schemas.input_collectors import ConsoleInputCollectorConfig

# 获取带默认值合并的配置
config = config_service.get_collector_config_with_defaults(
    "console_input",      # Collector 名称
    ConsoleInputCollectorConfig  # Schema 类（可选）
)
```

#### 5.2 Schema 配置类

每个阶段参与者可以定义 Pydantic Schema 配置类：

```python
# src/stages/input/collectors/console_input/config.py
from pydantic import Field
from src.modules.config.schemas.base import BaseConfig


class ConsoleInputCollectorConfig(BaseConfig):
    """ConsoleInputCollector 配置"""

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
maibot_config = config_service.get_section("providers.decision.maibot")
```

#### 6.2 获取配置项

```python
# 从根配置获取
platform_id = config_service.get("platform_id", section="general")

# 带默认值
api_key = config_service.get("api_key", default="default_key")
```

#### 6.3 检查阶段参与者启用状态

```python
# 检查 Collector 是否启用
if config_service.is_collector_enabled("bili_danmaku"):
    # ...

# 检查 Handler 是否启用
if config_service.is_handler_enabled("tts"):
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
# 获取所有 Collector 配置
all_collectors = config_service.get_all_collector_configs()

# 获取所有 Handler 配置
all_handlers = config_service.get_all_handler_configs()

# 获取所有 Decider 配置
all_deciders = config_service.get_all_decider_configs()

# 获取所有 Pipeline 配置
all_pipelines = config_service.get_all_pipeline_configs()
```

### 7. 配置模板生成

ConfigService 支持从 Schema 类自动生成配置模板：

```python
from src.modules.config.schemas.input_collectors import ConsoleInputCollectorConfig

# 生成 TOML 配置文件
ConsoleInputCollectorConfig.generate_toml(
    output_path="config_example.toml",
    section="collectors.console_input",
    include_comments=True
)
```

生成的配置文件：

```toml
# ConsoleInputCollector 配置

[collectors.console_input]
# 命令行提示符
prompt = "> "
# 是否启用历史记录
history_enabled = true
# 最大历史记录数
max_history = 100
```

### 8. 模板配置文件

项目使用 `config/` 目录下的多文件配置结构，首次运行时从 Pydantic Schema 自动生成：

```bash
# 首次运行
uv run python main.py
# 会自动在 config/ 目录下生成 core.toml, model.toml, input.toml, decision.toml, output.toml
```

---

## 相关文档

- [阶段参与者开发指南](provider-guide.md) - 如何开发自定义阶段参与者
- [管道开发指南](pipeline-guide.md) - 如何开发自定义 Pipeline
- [开发规范](../development-guide.md) - 代码风格和约定
- [3阶段架构](../architecture/overview.md) - 架构设计总览
- [事件系统](../architecture/event-system.md) - EventBus 使用指南
