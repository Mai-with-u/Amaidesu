# 提示词管理与配置管理

本文档详细介绍 Amaidesu 项目中的提示词管理和配置管理机制。

---

## 提示词管理

### PromptManager 概述

项目使用 **PromptManager** 统一管理所有 LLM 提示词。PromptManager 提供模板加载、变量替换、元数据解析等功能。

**核心特性：**
- 从多个扫描根加载 `.md` 模板文件（组件内聚 `prompts/` 目录 + 中央目录）
- 解析 YAML Frontmatter 元数据，以 `name` 字段作为声明式注册键
- 使用 `$variable` 语法进行**严格渲染**（缺变量抛错，render 是唯一渲染路径）
- 加载期校验：frontmatter `variables` 声明与正文占位符核对（不一致告警）；frontmatter 解析失败与单文件加载失败 fail-fast
- 键重复注册 fail-fast（抛 ValueError，防止静默覆盖）

### 快速开始

```python
from src.modules.prompts import get_prompt_manager

# 获取全局单例（推荐，启用 src/**/prompts/ 约定扫描）
pm = get_prompt_manager()

# 或者手动创建实例（不启用 src 约定扫描，用于测试隔离）
from src.modules.prompts.manager import PromptManager
pm = PromptManager()
pm.load_all()
```

### 模板目录结构（内聚式）

提示词文件**内聚在消费组件的 `prompts/` 目录下**，扫描范围为 `src/**/prompts/`：
组件把提示词放在自己包内的 `prompts/` 子目录即可被自动发现（全局单例
`get_prompt_manager` 默认开启约定扫描）。模板键来自 frontmatter 的 `name`
字段，与文件位置解耦。

当前全部模板清单：

| 模板键 | 位置 | 用途 |
|--------|------|------|
| `amaidesu_planner_react` | `src/agents/streamer/prompts/` | Planner ReAct 循环系统提示词（行为准则，工具面经 LLM 请求注入，不在模板内） |
| `amaidesu_replyer` | `src/agents/streamer/prompts/` | Replyer 回复生成模板（人设注入 + reply 工具调用契约） |
| `summary_system` | `src/agents/streamer/prompts/` | 后台维护者话题摘要的系统提示词 |
| `amaidesu_minecraft_agent` | `src/agents/minecraft/prompts/` | MinecraftAgent 系统提示词（事件驱动 ReAct AI 玩家） |
| `screen_vlm_system` | `src/modules/collectors/screen/prompts/` | 屏幕感知 VLM 的 system message |
| `screen_vlm_prompt` | `src/modules/collectors/screen/prompts/` | 屏幕感知 VLM 的用户 prompt |
| `viewer_message` | `src/modules/simulator/prompts/` | 模拟观众发言生成（常驻人设） |
| `sc_message` | `src/modules/simulator/prompts/` | SuperChat 付费留言生成 |
| `passerby_message` | `src/modules/simulator/prompts/` | 路人观众随机弹幕生成（无固定人设） |
| `warmup_message` | `src/modules/simulator/prompts/` | 暖场期弹幕生成（主播尚未开口） |
| `persona_generation` | `src/modules/simulator/prompts/` | 常驻观众人设批量生成 |

跨组件共享的提示词可放中央目录 `src/modules/prompts/templates/`（按相对路径
作为兜底键）；该目录不存在时加载跳过，不报错。优先推荐内聚到消费方。

### 模板格式 (YAML Frontmatter)

每个模板文件使用 YAML Frontmatter 定义元数据：

```yaml
---
name: amaidesu_replyer
description: "Amaidesu 直播回复生成模板"
author: Amaidesu
tags: [decision, live, vtuber, replyer, persona]
variables:
  - bot_name
  - personality
  - style_constraints
---

模板正文...
你叫 $bot_name，是一位正在 B 站进行实时直播的 AI VTuber。
```

**元数据字段说明：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | **模板注册键**（全局唯一，消费方 render 时使用；重复声明会在加载时抛 ValueError） |
| `description` | string | 否 | 模板描述 |
| `author` | string | 否 | 作者 |
| `tags` | list[string] | 否 | 标签列表 |
| `variables` | list[string] | 否 | 模板变量声明列表（与正文占位符核对，见下文加载期校验） |

> 未声明 `name` 的模板以相对扫描根的路径为兜底键（如某扫描根下
> `decision/llm.md` → 键 `decision/llm`），但**推荐始终显式声明 name**。

### strict-only 渲染契约

`render` 是**唯一渲染路径**，全系统不存在安全模式/容错渲染。

```python
# 渲染模板，缺失变量抛出 KeyError
prompt = pm.render("amaidesu_replyer", bot_name="麦麦", personality="活泼开朗", style_constraints="...")
```

契约语义：

- **缺变量抛错且带定位信息**：渲染时缺少必需变量抛 `KeyError`，错误信息含
  模板名与完整缺失变量清单（不只是第一个缺失项）。
- **加载期声明核对**：`load_all` 完成后核对每个模板的 frontmatter
  `variables` 声明与正文 `$占位符`——不一致记录 `logger.warning`（列出未声明
  与声明未使用的差异），不阻断启动。声明漂移不影响严格渲染的正确性，但会
  误导维护者对模板变量面的判断。
- **解析/加载失败 fail-fast**：frontmatter 解析失败、单文件加载异常、模板键
  冲突均在 `load_all` 阶段抛出——坏模板让启动失败，而不是带着残缺提示词运行。
- **中央目录不存在 → 跳过**：`src/modules/prompts/templates/` 不存在时记
  debug 日志跳过，属既有语义（该目录为可选的共享提示词位置）。

变量替换基于 `string.Template`：`$variable` / `${variable}` 为占位符，`$$`
转义为字面 `$`。加载期占位符提取复用同一模式，保证核对与渲染行为严格一致。

### 在组件中使用

```python
from src.modules.prompts import get_prompt_manager

class MyComponent:
    def setup(self):
        self._prompt_mgr = get_prompt_manager()

    def build_prompt(self) -> str:
        # 渲染模板（键 = 模板 frontmatter 的 name）
        return self._prompt_mgr.render(
            "amaidesu_replyer",
            bot_name="麦麦",
            personality="活泼开朗",
            style_constraints="口语化、简短",
        )
```

组件对渲染失败的处理按自身语义决定：核心提示词渲染失败通常意味着配置或
模板损坏，应记录日志并走自身的失败路径（如 Planner 本轮静默），而不是
带着空提示词继续。

---

## 配置管理

### ConfigService 概述

**ConfigService** 是项目的统一配置管理服务，负责：

- 加载 `config/` 目录下的六文件配置（`agents` / `collectors` / `tools` / `model` / `storage` / `infra`）
- 首次运行从 Pydantic Schema 自动生成缺失的配置文件
- 提供配置合并策略（Schema 默认值 + 配置覆盖）
- 支持配置文件热重载（file watcher）

### 快速开始

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

### 配置文件结构

配置为**六文件**结构（`config/` 目录），按领域拆分，每文件自带 `[meta].version`：

| 文件 | 内容 |
|------|------|
| `agents.toml` | 业务 Agent（`[agents].enabled` + streamer/minecraft/text_adv 子树） |
| `collectors.toml` | 采集器（顶层 `enabled` 名单 + 各采集器子段） |
| `tools.toml` | 工具域（`[tools]` 提供者开关 / `disabled_tools` / `[tools.tasks]` 异步任务基建） |
| `model.toml` | 三层模型结构（`[[llm_providers]]` / `[[llm_models]]` / `[llm_profiles]` 六用途 profile） |
| `storage.toml` | 顶层扁平存储（`[sqlite]` / `[memory]`） |
| `infra.toml` | 基础设施（`[tts]` / `[subtitle]` / `[dashboard]` / `[logging]` / `[interceptors.*]` / `[simulator]`） |

### 组件启用配置

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

### 配置合并

ConfigService 支持**配置合并**，优先级如下：

```
Schema 默认值（优先级低） → 配置文件覆盖（优先级高）
```

#### 获取合并后的配置

```python
# 获取带默认值合并的组件配置（phase 为兼容参数：input=采集器 / output=渲染工具）
config = config_service.get_config_with_defaults(
    "console_input",      # 组件名称
    phase="input"
)
```

> Schema 权威定义在 `src/modules/config/{core,model,agents,tools,memory,storage,background}_schemas.py`（Pydantic），组件嵌套配置类随所属域定义，由 `multi_file_loader` 的漂移写回闭环自动补齐用户文件缺失字段。

### 配置 API

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

# 拦截器配置（infra.toml [interceptors.*]）
pipe_cfg = config_service.get_interceptor_config("rate_limit")
if config_service.is_interceptor_enabled("rate_limit"):
    # ...
```

### 配置文件生成与重载

- **首次运行**：`ConfigService.initialize()` 经加载管线自动生成 `config/` 六文件并按 Schema 校验
- **重载**：`FileWatcher` 监听六文件变更（管线自写经自写压标跳过）；`reload_config` 按段策略分流——`infra` 为 hot 段即时回调生效，其余五文件提示待重启；重载失败保留旧配置继续运行
- **版本升级**：`upgrade.py` 升级钩子注册表按 `[meta].version` 区间推进（缺失版本硬错）

```bash
# 首次运行自动生成 config/ 六文件
uv run python main.py
# → 生成 agents.toml, collectors.toml, tools.toml, model.toml, storage.toml, infra.toml
```

---

## 相关文档

- [组件开发指南](component-guide.md) - 如何开发自定义采集器/工具/Agent
- [主播上下文构成](streamer-context.md) - 主播 Agent 决策窗的消息形态、参考段与输入预算
- [事件拦截器](../architecture/event-system.md#事件拦截器interceptor) - 如何开发自定义拦截器
- [开发规范](../development-guide.md) - 代码风格和约定
- [架构总览](../architecture/overview.md) - v2.0.0 架构设计总览
- [事件系统](../architecture/event-system.md) - EventBus 使用指南

---
