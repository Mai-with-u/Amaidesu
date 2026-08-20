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
pm = get_prompt_manager()

# 或者手动创建实例
from src.modules.prompts.manager import PromptManager
pm = PromptManager()
pm.load_all()
```

### 3. 模板目录结构

```
src/modules/prompts/templates/
├── decision/                    # 决策阶段提示词
│   ├── amaidesu_planner.md      # Amaidesu Planner 模板（两阶段，零人设注入）
│   ├── amaidesu_replyer.md      # Amaidesu Replyer 模板
│   ├── amaidesu_timing_gate.md  # 时机门控模板
│   ├── llm.md                   # LLM 对话模板
│   └── llm_structured.md        # LLM 结构化输出模板
├── input/                      # 输入阶段提示词
│   ├── text_adv_game_ocr.md   # OCR 提示词
│   ├── screen_context.md      # 屏幕上下文提示词
│   ├── screen_description.md  # 屏幕描述提示词
│   └── summarize.md           # 摘要提示词
├── output/                     # 输出阶段提示词
│   ├── avatar_expression.md    # 虚拟形象表情模板
│   ├── speech.md               # 语音合成模板
│   └── vts_hotkey.md           # VTS 热键模板
└── simulator/                  # 模拟直播间提示词
    ├── passerby_message.md     # 临时路人消息
    ├── sc_message.md           # Super Chat 消息
    ├── viewer_message.md       # 观众消息
    └── warmup_message.md       # 暖场期消息
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

class MyDecider:
    async def setup(self):
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

- 加载 `config/` 目录下的多文件配置（`core.toml` / `model.toml` / `input.toml` / `decision.toml` / `output.toml`）
- 首次运行从 Pydantic Schema 自动生成缺失的配置文件
- 提供配置合并策略（Schema 默认值 + 配置覆盖）
- 支持配置文件热重载（file watcher）

### 2. 快速开始

```python
from src.modules.config.service import ConfigService

# 初始化配置服务（首次运行自动生成 config/ 目录，同步方法）
config_service = ConfigService(base_dir="/path/to/project")
main_config, was_created = config_service.initialize()

# 获取配置节
general_config = config_service.get_section("general")

# 获取 Collector 配置（合并 Schema 默认值）
input_config = config_service.get_config_with_defaults(
    "console_input", phase="input"
)
```

> 配置文件的完整结构与 LLM provider/profile 两层模型见 [快速开始 - 编辑配置文件](../getting-started.md#25-编辑配置文件)。

### 3. 配置文件结构

配置为**多文件**结构（`config/` 目录），按关注点拆分：

| 文件 | 内容 |
|------|------|
| `core.toml` | 核心配置（`[general]` / `[persona]` / `[logging]` / `[dashboard]` 等） |
| `model.toml` | LLM provider 池（`[[llm_providers]]`）+ 各 profile（`[llm]` / `[llm_fast]` / `[vlm]` / `[llm_local]`） |
| `input.toml` | 输入阶段（`[collectors]` 启用列表 + 各 Collector 配置节） |
| `decision.toml` | 决策阶段（`[deciders]` 启用列表 + 各 Decider 配置节） |
| `output.toml` | 输出阶段（`[handlers]` 启用列表 + 各 Handler 配置节） |

### 4. 阶段参与者配置

在对应配置文件的启用列表中添加参与者名称：

```toml
# config/input.toml
[collectors]
enabled = ["console_input", "bili_danmaku"]

# config/output.toml
[handlers]
enabled = ["subtitle", "vts", "edge_tts"]

# config/decision.toml
[deciders]
enabled = ["amaidesu"]
```

每个阶段参与者可以有独立的配置节（位于同一配置文件）：

```toml
# config/input.toml
[collectors.bili_danmaku_official]
id_code = "your_id_code"
app_id = "your_app_id"
access_key = "your_access_key"

# config/output.toml
[handlers.subtitle]
font_size = 32
window_width = 1000
window_height = 720

# config/decision.toml
[deciders.maibot]
host = "127.0.0.1"
port = 8000
```

### 5. 配置合并

ConfigService 支持**配置合并**，优先级如下：

```
Schema 默认值（优先级低） → 配置文件覆盖（优先级高）
```

#### 5.1 获取合并后的配置

```python
# 获取带默认值合并的配置（phase 指定阶段）
config = config_service.get_config_with_defaults(
    "console_input",      # 参与者名称
    phase="input"         # 阶段：input / decision / output
)
```

#### 5.2 Schema 配置类

每个阶段参与者在自身模块内定义 `ConfigSchema` 嵌套类：

```python
# src/stages/input/collectors/console_input/console_input_collector.py
from pydantic import Field
from src.modules.config.schemas.base import BaseConfig


class ConsoleInputCollector:
    """ConsoleInputCollector 配置"""

    class ConfigSchema(BaseConfig):
        type: str = "console_input"

        # 命令行提示符
        prompt: str = Field(default="> ", description="命令行提示符")

        # 是否启用历史记录
        history_enabled: bool = Field(default=True, description="是否启用历史记录")

        # 最大历史记录数
        max_history: int = Field(default=100, description="最大历史记录数")
```

> `src/modules/config/schemas/input_schemas.py` 通过 `_try_import_schema()` 延迟导入各 Collector 的 `ConfigSchema` 并重导出为 `XXXConfigSchema` 别名（如 `InputCollectorsConfig` 聚合容器），避免循环 import。开发者只需关注参与者内的 `ConfigSchema` 定义，聚合由配置模块自动完成。

### 6. 配置 API

```python
# 获取顶层配置节
general = config_service.get_section("general")

# 获取配置项
platform_id = config_service.get("platform_id", section="general")

# 获取带默认值的参与者配置
cfg = config_service.get_config_with_defaults("bili_danmaku", phase="input")

# 检查参与者是否启用（phase 指定阶段）
if config_service.is_config_enabled("bili_danmaku", phase="input"):
    # ...

# 获取某阶段全部配置
all_input = config_service.get_all_configs(phase="input")

# Pipeline 配置
pipe_cfg = config_service.get_pipeline_config("rate_limit", phase="input")
if config_service.is_pipeline_enabled("rate_limit", phase="input"):
    # ...
```

### 7. 配置文件生成与热重载

- **首次运行**：`ConfigService.initialize()` 通过 `ConfigSchemaGenerator` 从 Schema 自动生成 `config/` 目录及全部配置文件
- **热重载**：`FileWatcher` 监听配置文件变更，通过 `register_reload_callback(callback)` 注册回调感知变化
- **迁移**：`migration.py` / `upgrade_hooks.py` 处理配置版本升级

```bash
# 首次运行自动生成 config/ 目录
uv run python main.py
# → 生成 core.toml, model.toml, input.toml, decision.toml, output.toml
```

---

## 相关文档

- [阶段参与者开发指南](component-guide.md) - 如何开发自定义阶段参与者
- [管道开发指南](pipeline-guide.md) - 如何开发自定义 Pipeline
- [开发规范](../development-guide.md) - 代码风格和约定
- [3阶段架构](../architecture/overview.md) - 架构设计总览
- [事件系统](../architecture/event-system.md) - EventBus 使用指南
