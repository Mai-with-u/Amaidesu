# 提示词管理与配置管理

本文档详细介绍 Amaidesu 项目中的提示词管理和配置管理机制。

---

## 提示词管理

### 1. PromptManager 概述

项目使用 **PromptManager** 统一管理所有 LLM 提示词。PromptManager 提供模板加载、变量替换、元数据解析等功能。

**核心特性：**
- 从多个扫描根加载 `.md` 模板文件（组件内聚 `prompts/` 目录 + 中央目录）
- 解析 YAML Frontmatter 元数据，以 `name` 字段作为声明式注册键
- 使用 `$variable` 语法进行变量替换
- 支持严格模式和安全模式渲染
- 支持模板 Section 提取
- 键重复注册 fail-fast（抛 ValueError，防止静默覆盖）

### 2. 快速开始

```python
from src.modules.prompts import get_prompt_manager

# 获取全局单例（推荐）
pm = get_prompt_manager()

# 或者手动创建实例（不启用 src 约定扫描，用于测试隔离）
from src.modules.prompts.manager import PromptManager
pm = PromptManager()
pm.load_all()
```

### 3. 模板目录结构（内聚式）

提示词文件**内聚在消费组件的 `prompts/` 目录下**，由 PromptManager 按
`src/**/prompts/` 约定自动发现；模板键来自 frontmatter 的 `name` 字段，
与文件位置解耦。

```
src/
├── agents/streamer/prompts/          # 主播 Agent 提示词
│   ├── amaidesu_planner.md           # Planner 模板（两阶段，零人设注入）
│   ├── amaidesu_replyer.md           # Replyer 模板
│   └── agenda_expand.md              # 大纲环节动态扩展模板
├── modules/tools/output/vts/prompts/
│   └── vts_hotkey.md                 # VTS 热键模板
└── modules/simulator/prompts/        # 模拟直播间提示词
    ├── passerby_message.md           # 临时路人消息
    ├── persona_generation.md         # 常驻观众人设批量生成
    ├── sc_message.md                 # Super Chat 消息
    ├── viewer_message.md             # 观众消息
    └── warmup_message.md             # 暖场期消息
```

跨组件共享的提示词可放中央目录 `src/modules/prompts/templates/`（按相对路径
作为兜底键），但优先推荐内聚到消费方。

### 4. 模板格式 (YAML Frontmatter)

每个模板文件使用 YAML Frontmatter 定义元数据：

```yaml
---
name: amaidesu_replyer
version: "1.1"
description: "Amaidesu 直播回复生成模板 - 基于 Planner 的 DecisionPlan 生成实际回复 JSON {text, emotion, action, action_parameters}（含人设注入）"
author: Amaidesu
tags: [decision, live, vtuber, replyer, persona]
variables:
  - bot_name
  - personality
  - style_constraints
  - plan
---

# 模板内容...
你叫 $bot_name，是一位正在 B 站进行实时直播的 AI VTuber。
```

**元数据字段说明：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | **模板注册键**（全局唯一，消费方 render 时使用；重复声明会在加载时抛 ValueError） |
| `version` | string | 否 | 模板版本 |
| `description` | string | 否 | 模板描述 |
| `author` | string | 否 | 作者 |
| `tags` | list[string] | 否 | 标签列表 |
| `variables` | list[string] | 否 | 模板变量列表 |

> 未声明 `name` 的模板以相对扫描根的路径为兜底键（如中央目录下
> `decision/llm.md` → 键 `decision/llm`），但**推荐始终显式声明 name**。

### 5. 使用方式

#### 5.1 获取原始模板

```python
# 获取原始模板内容（含 Frontmatter）
raw_template = pm.get_raw("amaidesu_replyer")
```

#### 5.2 渲染模板（严格模式）

```python
# 渲染模板，缺失变量会抛出 KeyError
prompt = pm.render("amaidesu_replyer", bot_name="麦麦", personality="活泼开朗", style_constraints="...", plan={})
```

**严格模式特点：**
- 缺失必需变量会抛出 `KeyError` 异常
- 适用于需要所有变量都必须提供的场景

#### 5.3 安全模式渲染

```python
# 安全模式渲染，缺失变量保留原样
prompt = pm.render_safe("amaidesu_replyer", bot_name="麦麦")
```

**安全模式特点：**
- 缺失变量不会抛出异常，保留为 `$variable` 形式
- 适用于部分变量可选的场景

#### 5.4 提取特定 Section

```python
# 提取并渲染模板中的特定 section
system_msg = pm.extract_section(
    "amaidesu_replyer",`n    "System Prompt",
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
    "amaidesu_replyer",`n    "User Message",
    text="你好",
    bot_name="麦麦"
)
```

#### 5.6 列表和元数据

```python
# 列出所有已加载的模板
templates = pm.list_templates()
# ['agenda_expand', 'amaidesu_planner', 'amaidesu_replyer', 'vts_hotkey', ...]

# 获取模板元数据
metadata = pm.get_metadata("amaidesu_replyer")
# TemplateMetadata(name='amaidesu_replyer', version='1.1', ...)
```

### 6. 模板示例

#### 6.1 回复生成模板示例（节选自 amaidesu_replyer.md）

```yaml
---
name: amaidesu_replyer
version: "1.1"
description: "Amaidesu 直播回复生成模板"
variables:
  - bot_name
  - personality
  - style_constraints
  - plan
tags: [decision, live, vtuber, replyer, persona]
---

# ① 人设注入层

你叫 $bot_name，是一位正在 B 站进行实时直播的 AI VTuber。

## 人设特征
性格：$personality

## 风格约束
$style_constraints

## 决策计划
$plan
```

### 7. 在组件中使用

```python
from src.modules.prompts import get_prompt_manager

class MyComponent:
    def setup(self):
        self._prompt_mgr = get_prompt_manager()

    def build_prompt(self) -> str:
        # 渲染模板（键 = 模板 frontmatter 的 name）
        prompt = self._prompt_mgr.render_safe(
            "amaidesu_replyer",
            bot_name="麦麦",
            personality="活泼开朗",
            style_constraints="口语化、简短",
            plan="{}",
        )
        return prompt
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

配置为**七文件**结构（`config/` 目录，v2.0.0），按关注点拆分：

| 文件 | 内容 |
|------|------|
| `core.toml` | 核心配置（`[general]` / `[persona]` / `[logging]` / `[dashboard]` / `[interceptors.*]` 等） |
| `model.toml` | LLM provider 池（`[[llm_providers]]`）+ 各 profile（`[llm]` / `[llm_fast]` / `[vlm]` / `[llm_local]` / `[llm_summary]` / `[llm_agenda]`） |
| `agents.toml` | Agent 启用与配置（`[agents].enabled` + streamer/game 子段） |
| `tools.toml` | 工具包启用与配置（`[tools].enabled` + 采集器挂 `[tools.perception.config]`、渲染挂 `[tools.output.config]`） |
| `memory.toml` | 记忆系统（`[memory]` backend=simple\|amemorix） |
| `storage.toml` | 存储层（`[storage]` sqlite） |
| `background.toml` | 后台任务（`[background]` compressor 等） |

### 4. 组件启用配置

在对应配置文件的启用列表中添加组件名称：

```toml
# config/agents.toml —— Agent 启用
[agents]
enabled = ["streamer"]        # 可选: streamer / game / custom

# config/tools.toml —— 工具包启用
[tools]
enabled = ["perception", "output"]
```

每个组件的独立配置节位于对应工具包/Agent 段内（Schema 权威定义见 `src/modules/config/*_schemas.py`，Pydantic Schema 驱动生成/校验/迁移）。

### 5. 配置合并

ConfigService 支持**配置合并**，优先级如下：

```
Schema 默认值（优先级低） → 配置文件覆盖（优先级高）
```

#### 5.1 获取合并后的配置

```python
# 获取带默认值合并的组件配置（phase 为兼容参数：input=采集器 / output=渲染工具）
config = config_service.get_config_with_defaults(
    "console_input",      # 组件名称
    phase="input"
)
```

> Schema 权威定义在 `src/modules/config/{core,model,agents,tools,memory,storage,background}_schemas.py`（Pydantic），组件嵌套配置类随所属域定义，由 `multi_file_loader` 的漂移写回闭环自动补齐用户文件缺失字段。

### 6. 配置 API

```python
# 获取顶层配置节
general = config_service.get_section("general")

# 获取配置项
platform_id = config_service.get("platform_id", section="general")

# 获取带默认值的组件配置
cfg = config_service.get_config_with_defaults("console_input", phase="input")

# 检查组件是否启用
if config_service.is_config_enabled("console_input", phase="input"):
    # ...

# 拦截器配置（core.toml [interceptors.*]）
pipe_cfg = config_service.get_interceptor_config("rate_limit")
if config_service.is_interceptor_enabled("rate_limit"):
    # ...
```

### 7. 配置文件生成与热重载

- **首次运行**：`ConfigService.initialize()` 从 Pydantic Schema 自动生成 `config/` 目录及全部配置文件
- **热重载**：`FileWatcher` 监听配置文件变更，通过 `register_reload_callback(callback)` 注册回调感知变化
- **迁移**：`migration.py` / `upgrade_hooks.py` 处理配置版本升级

```bash
# 首次运行自动生成 config/ 目录
uv run python main.py
# → 生成 core.toml, model.toml, agents.toml, tools.toml, memory.toml, storage.toml, background.toml
```

---

## 相关文档

- [组件开发指南](component-guide.md) - 如何开发自定义采集器/工具/Agent
- [事件拦截器](../architecture/event-system.md#事件拦截器interceptor) - 如何开发自定义拦截器
- [开发规范](../development-guide.md) - 代码风格和约定
- [架构总览](../architecture/overview.md) - v2.0.0 架构设计总览
- [事件系统](../architecture/event-system.md) - EventBus 使用指南

---

*最后更新：2026-08-26（v2.0.0 对齐：配置章节切七文件体系、配置 API 示例更新为现行 ConfigService 方法；提示词内聚化重构：模板迁入消费组件 prompts/ 目录，键改用 frontmatter name 声明式键 + src/**/prompts 约定自动发现 + 重复键 fail-fast）*
