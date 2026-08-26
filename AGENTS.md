# AGENTS.md

为在此代码库中工作的 AI 编码代理提供指南。

**本文档为 AI 代理核心规则**：硬规则与高频 API 内联，操作手册渐进式披露——详细指南请查看 `docs/` 目录。

## 快速导航

| 我想... | 查看文档 |
|---------|---------|
| 快速上手项目 | [快速开始](docs/getting-started.md) |
| 了解代码规范 | [开发规范](docs/development-guide.md) |
| 理解架构设计 | [3阶段架构](docs/architecture/overview.md) |
| 理解事件系统 | [事件系统](docs/architecture/event-system.md) |
| 开发 Collector/Decider/Handler | [阶段参与者开发](docs/development/component-guide.md) |
| 开发事件拦截器 | [事件系统](docs/architecture/event-system.md#事件拦截器interceptor) |
| 管理提示词 | [提示词管理](docs/development/prompt-management.md) |
| 编写测试 | [测试指南](docs/development/testing-guide.md) |

## 核心规范

### 必须遵守

- 移动或者重命名文件的时候注意使用 `git mv` 保留历史记录
- 使用中文和用户沟通以及编写文档、注释
- 需要如实汇报自己的工作进度，不得隐瞒问题不报，不得在未经用户允许的情况下降低任务达成标准
- **提交代码前运行测试**：`uv run pytest tests/` 和 `uv run ruff check .`
- **提交代码前进行格式化**: `uv run ruff format .`
- **git 提交必须获得用户显式授权**：任何 `git commit` / `git push` 前必须确认用户明确要求（含"提交/commit/push"等词）。计划文件（`.omo/plans/*.md`）中的 Commit 策略**仅覆盖该计划范围内的任务**；计划之外的工作（bug 修复、追加功能、临时改动）即使复用同一委托模板，也**不得**继承提交授权——委托子代理时若任务超出计划范围，**禁止**在 prompt 中写入 commit 指令，改为"完成后展示结果，由用户决定是否提交"。
- **git 提交体规范（Conventional Commits）**：格式 `type(scope): subject`（type ∈ feat/fix/docs/refactor/perf/test/chore；scope 用影响域，如 decision/dashboard/config/core/prompts）。subject 用**中文**简洁描述（≤50 字符）；body 用**中文**说明"为什么"（空行分隔，可留空）。Windows PowerShell 下提交 message 必须用 `git commit -F <file>`（UTF-8 文件）或双引号包裹（防 `$` 变量展开、防中文乱码）；提交后 `git log -1` 复核无乱码、无截断。**禁止**：英文 subject、乱码字符、特殊符号被 shell 吞掉、body 缺失"为什么"。

### 禁止事项

| 禁止 | 原因 | 替代方案 |
|------|------|----------|
| ❌ 创建新的 Plugin（插件系统已移除） | 架构已重构为阶段参与者系统 | 创建 Collector/Decider/Handler |
| ❌ 使用服务注册机制（已废弃） | 使用 EventBus | EventBus 事件系统 |
| ❌ 硬编码事件名字符串 | 避免拼写错误 | 使用 `CoreEvents` 常量 |
| ❌ 使用空的 except 块 | 隐藏错误 | 记录日志并处理 |
| ❌ 删除失败的测试来"通过" | 自欺欺人 | 修复代码或测试 |
| ❌ 在修复 bug 时进行大规模重构 | 扩大风险范围 | 只修复 bug |
| ❌ 提交未验证的代码 | 可能破坏构建 | 先运行测试和 lint |
| ❌ 类变量中存储可变对象 | 共享状态问题 | 使用 `__init__` 初始化 |
| ❌ 把"需要用户回复才能继续"的内容加入 todo list | todo tracker 会被系统持续催促,而 agent 又无法推进,陷入死循环 | 用户驱动的阻塞项(等用户决定/确认)不要写成 todo;若必须跟踪,在回复中作为"备注"或"待你确认"显式说明,不进 todo 系统 |

### 架构约束：3阶段数据流规则

**严格遵守单向数据流：Input 阶段 → Decision 阶段 → Output 阶段**

约束分三个层面（精确表述见[数据流规则](docs/architecture/data-flow.md)）：
- **① 数据平面（硬规则）**：运行时消息/结果严格单向，禁止下游结果回灌（防环）。
- **② 分层规则**：阶段间不直接 import 对方实现，共享契约放 `src/modules/`（无 import 环）。
- **③ 发现平面（允许）**：Decision 可**只读、拉取式**获取 Output 的能力元数据做动作选择，但必须经 `src/modules/` 的 Protocol（如 `CapabilitiesProvider`）+ 组合根注入，**不得** import Output 实现、**不得**靠 Output 推送事件。区分口诀："能挥手吗"可问（发现），"刚才挥手成功了吗"不可问（结果回灌）。

| 禁止模式 | 说明 | 详细规则 |
|---------|------|----------|
| ❌ OutputHandler 订阅 Input 事件 | 绕过 Decision 阶段，破坏分层 | [数据流规则](docs/architecture/data-flow.md) |
| ❌ Decider 订阅 Output 事件（运行时结果） | 创建循环依赖；但只读能力元数据可经 Protocol 拉取 | 同上 |
| ❌ InputCollector 订阅 Decision/Output 的数据事件 | Input 应只发布数据，不订阅下游结果数据；元控制信号（如 `output.intent.finished`）除外 | 同上 |

### 文档维护规则

**单一事实源**（修改以下事实时只改权威处，其他文件只引用链接，不得复制）：

| 事实 | 唯一权威处 |
|------|-----------|
| 事件表（含发布者/订阅者/数据类型） | `docs/architecture/event-system.md` |
| 数据流图 / 组件清单 / 目录结构 | `docs/architecture/overview.md` |
| 生命周期表 | `docs/development-guide.md` §10.2 |
| 架构决策记录（ADR） | `docs/architecture/adr/` |
| 数据流规则约束 | `docs/architecture/data-flow.md` |

- **禁止在多个文档复制同一事实**；需要引用时用相对链接指向权威处
- 修改文档后更新文件末尾"最后更新"日期（`YYYY-MM-DD` + 变更摘要）
- ADR 编号按创建时间递增，含元数据（状态 / 日期 / 实现提交 40 位 hash）
- 根目录不放图片/视频；图片放 `docs/images/`，视频放 `docs/videos/`
- 详细规范见 [文档维护规范](docs/development/documentation-guide.md)

### 配置 Schema 变更规则

配置系统是"Schema 即真相"（Pydantic Schema 驱动生成/验证/迁移）。**任何对配置结构的修改都必须升配置版本号**，否则用户现有配置文件不会被自动升级，迁移机制沦为摆设（历史教训：`CONFIG_VERSION` 长期停在 0.4.0，每次启动只有漂移警告、从不自动升级）。

**版本号机制**：
- 唯一权威定义：`src/modules/config/multi_file_loader.py` 的 `CONFIG_VERSION`
- `src/modules/config/core_schemas.py` 的 `MetaConfig.version` 默认值必须与 `CONFIG_VERSION` **同步修改**（改一必改二，防止新生成文件版本与检测目标不一致）
- 用户文件中的版本号位于 `config/core.toml` 的 `[meta].version`，由系统在升级时自动写回（写回后下次启动不再重复提示）

**必须升 CONFIG_VERSION（patch 级，如 0.4.0 → 0.4.1）的变更**：
- 新增/删除/重命名字段（含 Collector/Decider/Handler 的 `ConfigSchema` 嵌套类）
- 字段类型或约束变化（如 `str → list`、新增 Literal 选项、`gt/ge` 等约束调整）
- 字段默认值语义变化（影响已有用户行为的默认值调整）
- 配置段移动/拆分/合并

**除升版本外还必须注册 `ConfigUpgradeHook`（minor 级，如 0.4.x → 0.5.0）的变更**——需要数据变换时：
- 字段重命名、配置段拆分、类型转换、默认值调整等需改写旧数据的变更
- 注册到 `src/modules/config/upgrade_hooks.py` 的 `CONFIG_UPGRADE_HOOKS`；hook 必须：**原地修改 dict、幂等**（重复执行结果一致）、返回变更字段路径列表
- 每个 hook 必须配单元测试（旧结构输入 → 断言新结构输出）
- 纯新增字段（无需数据变换）可不注册 hook，由写回机制自动补默认值

**配置段跨文件移动**（如 `simulator.toml` 合并进 `core.toml`）必须注册 `CrossFileMigration`：
- 注册到 `src/modules/config/multi_file_loader.py` 的 `CROSS_FILE_MIGRATIONS`（source_file/source_key → target_file/target_key）
- 执行时机：`load_config_dir` 中目标段缺失时合并进目标文件，源文件备份到 `config/old/` 后移除
- 必须配迁移测试（旧文件结构 → 断言目标段合并 + 源文件移除）

**不需要升版本**：仅修改注释/description、纯内部实现重构（不改变 TOML 结构）。

**变更时需同步检查的联动位置**：

| 变更内容 | 需同步修改 |
|---------|-----------|
| 任何 Schema 变更 | `CONFIG_VERSION` + `MetaConfig.version` |
| 需要数据变换 | `upgrade_hooks.py` 注册 hook + 迁移测试 |
| 配置段跨文件移动 | `multi_file_loader.py` 注册 CrossFileMigration + 迁移测试 |
| 涉及旧配置段（迁移/死配置） | `migration.py` 的 `_SECTION_MAP` / `_DEAD_SECTIONS` |
| 涉及 Dashboard 显示字段 | `schema_registry.py`（启动 coverage gate 会校验） |
| 组件 Schema（Collector/Decider/Handler） | 对应 `tests/config/test_*_schema.py` 更新 |

**验证要求**：提交前 `uv run pytest tests/config/ -q` 必须通过；注册了 hook 必须有对应迁移测试。禁止"只改 Schema 不升版本/不注册 hook"的提交。

**升版本 ≠ 迁移生效（历史教训：2026-08-16 大纲功能静默失效）**：
- 升 `CONFIG_VERSION` 只是"声明变更"，真正让用户文件升级的是 `load_config_dir` 的**漂移写回闭环**——该闭环只覆盖 `multi_file_loader.py` 中接入 `_load_and_validate_schema` + `_write_back_schema_file` 的文件。**当前全部 5 个文件（core/model/decision/input/output）均已接入**，未来新增配置文件必须同样接入，否则该文件的 Schema 变更永远不会写回用户文件（字段缺失只在内存兜底，功能静默失效）。
- **每次 Schema 变更后必须实际验证迁移生效**：用 `uv run python -c "from src.modules.config.multi_file_loader import load_config_dir; load_config_dir(__import__('pathlib').Path('config'))"` 检查日志出现"已自动升级: 补齐 N 项"且对应字段落盘；或跑 `tests/config/test_config_auto_upgrade.py`（已覆盖全部文件的漂移补齐 + 幂等断言）。
- **禁止"升了版本但没验证用户文件真的被升级"的提交**——版本号变化本身不保证迁移生效。

## 常用命令

### 包管理器

本项目使用 [uv](https://docs.astral.sh/uv/) 作为 Python 包管理器。

```bash
uv sync          # 同步依赖
uv add pkg       # 添加依赖
uv remove pkg    # 移除依赖
```

### 运行应用

```bash
uv run python main.py                    # 正常运行
uv run python main.py --debug            # 调试模式
uv run python main.py --filter EdgeTTSHandler SubtitleHandler   # 过滤日志
```

### Web Dashboard

项目内置 Web 管理界面，有**两种运行模式**（生产 60214 / 开发 60315），完整说明见 [快速开始 - Web Dashboard](docs/getting-started.md#44-web-dashboard)。

### 测试

```bash
uv run pytest tests/                     # 运行所有测试
uv run pytest tests/path/to/test.py      # 运行特定测试
uv run pytest -v                         # 详细输出
uv run pytest -m "not slow"              # 排除慢速测试
```

### 代码质量

```bash
uv run ruff check .      # 代码检查
uv run ruff format .     # 代码格式化
uv run ruff check --fix .  # 自动修复
```

## 数据类型选用规范

| 类型 | 使用场景 | 示例 |
|------|----------|------|
| **Pydantic BaseModel** | 所有数据模型、配置 Schema、事件 Payload | `class UserConfig(BaseModel)` |
| **dataclass** | 仅用于简单的内部统计/包装类 | `@dataclass class CollectorStats` |
| **Protocol** | 定义接口协议 | `class CapabilitiesProvider(Protocol)` |

**详细规范**：[开发规范 - 数据类型选用](docs/development-guide.md#3-数据类型选用规范)

### 命名约定

| 类型 | 命名风格 | 示例 |
|------|---------|------|
| 类名 | PascalCase | `EventBus`, `InputCollector`, `RateLimitInterceptor` |
| 函数/方法/变量名 | snake_case | `send_to_maibot`, `handler_config` |
| 私有成员 | 前导下划线 | `_message_handlers`, `_is_connected` |
| Collector/Agent/工具/拦截器类 | 以类型名结尾 | `ConsoleInputCollector`, `StreamerAgent`, `EdgeTTSHandler`, `RateLimitInterceptor` |

**详细规范**：[开发规范](docs/development-guide.md)

## 阶段参与者开发

项目使用阶段参与者系统封装具体功能，由 Manager 统一管理，配置驱动启用。

### 阶段参与者类型

| 类型 | 职责 | 位置 |
|------|------|------|
| **InputCollector** | 从外部数据源采集数据 | `src/stages/input/collectors/` |
| **Decider** | 处理 NormalizedMessage 生成 Intent | `src/stages/decision/deciders/` |
| **OutputHandler** | 渲染到目标设备 | `src/stages/output/handlers/` |

### 生命周期方法

| 参与者类型 | 启动 | 停止 | 业务入口 | 说明 |
|------------|------|------|----------|------|
| InputCollector | `start()` | `stop()` + `cleanup()` | `collect()` | 返回 AsyncIterator，持续产出消息 |
| Decider | `setup()` | `cleanup()` | `decide()` | 订阅 `input.message.received`，处理消息 |
| OutputHandler | `init()` | `cleanup()` | `handle(intent)` | Manager 初始化资源并直接调用，Handler 不订阅阶段调度事件 |

**注意**: InputCollector 使用 `start()`/`stop()` 是因为它需要返回异步生成器（AsyncIterator），
而 Decider 使用 `setup()`/`cleanup()` 是因为它是事件订阅者。

OutputHandler 的 `init()` 与 `cleanup()` 只管理自身资源及专用事件通信（如 `OUTPUT_STICKER_COMMAND`），不处理阶段调度事件。Manager 直接调用 active Handler 的 `handle(intent)`。

> **单一事实源**：生命周期表的权威定义在 [开发规范 §10.2](docs/development-guide.md#102-阶段参与者生命周期)。若两侧不一致，以 development-guide.md 为准并同步本表。

### 添加新 Handler

1. 继承对应的 Handler 基类（InputCollector/Decider/OutputHandler）
2. 使用 `@collector`/`@decider`/`@handler` 装饰器注册
3. 在配置中启用

**详细指南**：[阶段参与者开发](docs/development/component-guide.md)

### 配置示例

```toml
# 输入Collector
[collectors]
enabled = ["console_input", "bili_danmaku"]

# 决策Decider
[deciders]
enabled = ["amaidesu"]

# 输出Handler
[handlers]
enabled = ["edge_tts", "subtitle", "vts"]
```

## 事件拦截器开发

事件拦截器是挂在 EventBus 分发层的全局单点（§1.46.1）：emit 后、订阅者收到前，所有事件过同一道拦截器链。旧 Pipeline 系统已移除。

### 添加新拦截器

1. 继承 `EventInterceptor`（`src/modules/events/interceptors/base.py`）
2. 实现 `name` 属性（唯一标识）与 `intercept()` 方法
3. 在 `main.py` 的 `register_event_interceptors()` 中实例化并 `event_bus.add_interceptor()`
4. 配置放 `core.toml` 的 `[interceptors.<name>]`
5. `intercept()` 返回 dict 放行（可原地修改 payload）、返回 `None` 丢弃事件

**详细指南**：[事件系统 - 事件拦截器](docs/architecture/event-system.md#事件拦截器interceptor)

## 依赖注入约定

- **服务对象**（LLMManager、PromptManager、EventBus 等）→ 构造器注入（DI）
- **数据对象**（请求 ID、会话状态、值对象）→ 可用 Context Object 或直接参数
- **禁止**把服务塞进 Context 容器传递
- 详见 [依赖注入指南](docs/development/dependency-injection.md)

## LLM 模块

LLM 调用统一通过 `LLMManager`。配置采用 **provider + profile** 两层结构（`[[llm_providers]]` 定义可复用 API 连接，`[llm]`/`[llm_fast]`/`[vlm]`/`[llm_local]` 等 profile 通过 `provider` 字段引用并覆盖 model/temperature）。配置示例见 `config/model.toml`。

```python
from src.modules.llm import LLMManager

llm_manager = LLMManager()
await llm_manager.setup(model_config)

response = await llm_manager.chat("你好")              # 完整对话
short_reply = await llm_manager.chat_fast("翻译成英文")  # 快速对话
```

**添加新的 LLM Provider**：新建 `src/modules/llm/clients/your_client.py` 继承 `BaseLLMClient`，实现 `chat()/stream_chat()`，末尾调用 `register_client("your_type", YourClient)`，并在 `config/model.toml` 添加 `[[llm_providers]]`。未知的 `provider`/`client_type` 会在 `setup()` 时 fail-fast 报错。

## 提示词管理

项目使用 **PromptManager** 统一管理所有 LLM 提示词。模板键为**声明式键**（frontmatter `name` 字段），提示词文件内聚在消费组件的 `prompts/` 目录下，由 `src/**/prompts/` 约定自动发现。

```python
from src.modules.prompts import get_prompt_manager

prompt = get_prompt_manager().get_raw("amaidesu_replyer")      # 获取原始提示词
prompt = get_prompt_manager().render("vts_hotkey", text="用户消息", hotkey_list_str="smile, wave")
```

**详细指南**：[提示词管理](docs/development/prompt-management.md)

## 事件系统

项目使用 **EventBus** 作为唯一的跨阶段通信机制。

### 基本使用

```python
from src.modules.events.names import CoreEvents

# 发布事件
await event_bus.emit(CoreEvents.INPUT_MESSAGE_RECEIVED, normalized_message)

# 订阅事件
event_bus.on(CoreEvents.INPUT_MESSAGE_RECEIVED, self.handle_message, model_class=MessageReadyPayload)
```

**详细文档**：
- [事件系统](docs/architecture/event-system.md)
- [数据流规则](docs/architecture/data-flow.md)
- [事件命名规范](docs/architecture/event-naming-convention.md)

### 核心事件

事件按阶段流转使用统一的动词链：`received → generated → dispatched → finished`。`completed` 保留为 Manager 内部完成语义，Handler 不必再发布。**完整事件表（含发布者/订阅者/数据类型）见 [事件系统](docs/architecture/event-system.md#事件载荷类型)**。

| 事件名 | 方向 |
|--------|------|
| `input.message.received` | Input → Decision |
| `decision.intent.generated` | Decision → OutputHandlerManager |
| `output.intent.dispatched` | 监控信号（Broadcaster/EventRecorder 等观察，不触发 Handler） |
| `output.intent.finished` | Manager 聚合全部 handler 完成后发布 |

> **单一事实源**：事件表的权威定义在 event-system.md。若两侧不一致，以 event-system.md 为准并同步本表。

#### Manager 直接调度与完成跟踪

`OutputHandlerManager` 是 Output 阶段唯一的调度点：订阅 `decision.intent.generated` → 发布 `output.intent.dispatched` 监控信号 → 为每个 active Handler 创建任务并直接调用 `handle(intent)`（`asyncio.wait_for` 落实 `render_timeout_ms`，隔离超时与异常）→ `gather` 等待全部完成后发布 `output.intent.finished`。Handler 不订阅 `output.intent.dispatched`，也不发布 `output.handler.completed`。

### 事件注册

事件 Payload 通过 `@register_event` 装饰器注册到模块级 `EVENT_REGISTRY` 字典，由 Payload 模块导入时自动触发。

```python
from pydantic import BaseModel
from src.modules.events.registry import register_event
from src.modules.events.payloads.base import BasePayload

@register_event("input.message.received")
class MessageReadyPayload(BasePayload):
    message: dict
    source: str
    timestamp_ms: int
    ...
```

要点：
- **装饰器幂等**：同一类重复注册不会出错，重复注册为不同类型会抛 `ValueError`
- **自动反向引用**：被装饰类获得 `cls._registered_event_name` 属性，便于日志/调试
- **Payload 模块导入**：应用启动时调用 `register_core_events()` 触发各 Payload 子模块 import，使装饰器生效
- **注册表查询**：`get_registered_event(name)` / `list_registered_events()`；`EventRegistry` 仅提供查询 API（`get`/`is_registered`/`list_all_events`）

### 时间字段约定

项目统一使用**毫秒（ms）**作为时间单位。时刻字段用 `int` Unix epoch 毫秒，时长/超时字段用毫秒，命名统一 `<name>_ms`（如 `timestamp_ms`、`render_timeout_ms`）。

```python
from src.modules.time_utils import now_ms, elapsed_ms, format_duration_ms, ms_to_datetime

ts = now_ms()                        # 当前时刻（int 毫秒）
elapsed = elapsed_ms(start_ms=ts)    # 经过时长
format_duration_ms(1234)             # "1.2s"
```

**注意事项**：
- 禁止使用秒为单位的字段（如 `timestamp_s`、`duration_seconds`），如需人类阅读用 `ms_to_datetime()` 转换
- 历史代码中的 `timestamp` 字段通过 Pydantic `alias` 兼容（`alias="timestamp"`，实际字段为 `timestamp_ms`）

## ContextService 上下文管理

ContextService 提供对话历史管理和多会话支持：管理对话历史（内存存储）、支持多会话隔离（session_id）、为 Decider 提供上下文。

> **文档状态**：`docs/development/context-service.md` 尚未编写，使用示例待补充。基本用法见 `src/modules/context/` 下的实现与测试。

## 3阶段架构

| 阶段 | 职责 | 位置 |
|----|------|------|
| **Input 阶段** | 数据采集 + 标准化 + 预处理 | `src/stages/input/` |
| **Decision 阶段** | 决策（可替换） | `src/stages/decision/` |
| **Output 阶段** | 参数生成 + 渲染 | `src/stages/output/` |

**数据流**：外部输入 → NormalizedMessage → Intent → Manager 直接并行渲染（详见 [3阶段架构](docs/architecture/overview.md)）。

### Core 层职责边界

**Core 层的职责**：
- 定义基础接口（Collector/Decider/Handler 基类、事件系统）
- 提供共享工具（日志、配置管理）
- 存放跨阶段共享的类型（避免循环依赖）
- 组合根（main.py）协调组件生命周期

**Core 层不应该**：
- 从阶段层导入类型并重导出
- 依赖任何阶段的具体实现
- 包含业务逻辑

**示例**：
- ✓ `src/modules/types/base/normalized_message.py`: 定义 NormalizedMessage 基础类型
- ✓ `src/modules/types/intent.py`: 共享的 Intent 类型

## 日志使用

```python
from src.modules.logging import get_logger

logger = get_logger("MyClassName")  # 使用类名或模块名（绑定 module 字段）
logger.info("信息日志")
logger.debug("调试日志")
logger.error("错误日志", exc_info=True)
```

**日志过滤**：使用 `--filter` 参数时，传入 get_logger 的第一个参数（类名或模块名）

## 测试规范

- 使用 pytest 编写测试，测试文件 `test_*.py`，测试函数 `async def test_*():`
- 异步测试使用 `@pytest.mark.asyncio` 装饰器

**详细指南**：[测试指南](docs/development/testing-guide.md)

## 配置文件

- 配置文件使用 TOML 格式，目录 `config/`（多文件结构，首次运行从 Schema 自动生成）
- Collector 配置 `[collectors]` / Decider 配置 `[deciders]` / Handler 配置 `[handlers]` / 拦截器配置 `[interceptors.*]`

## 目录结构

完整目录结构见 [3阶段架构总览 - 目录结构](docs/architecture/overview.md#目录结构)。核心布局：`src/modules/`（共享模块）+ `src/stages/`（业务阶段：input/decision/output）+ `config/`（配置）+ `docs/`（文档）。

## 通信模式

项目使用 **EventBus** 作为唯一的跨阶段通信机制（发布-订阅），支持优先级、错误隔离、统计功能，使用 CoreEvents 常量确保类型安全。

## 相关文档

### 新手入门
- [快速开始](docs/getting-started.md) - 环境搭建和基本使用

### 架构理解
- [3阶段架构总览](docs/architecture/overview.md) - 3阶段架构详解
- [数据流规则](docs/architecture/data-flow.md) - 数据流约束和规则
- [事件系统](docs/architecture/event-system.md) - EventBus 使用指南
- [事件命名规范](docs/architecture/event-naming-convention.md) - 事件命名规则

### 开发指南
- [开发规范](docs/development-guide.md) - 代码风格和约定
- [阶段参与者开发](docs/development/component-guide.md) - Collector/Decider/Handler 开发详解
- [事件拦截器](docs/architecture/event-system.md#事件拦截器interceptor) - 事件拦截器开发详解
- [提示词管理](docs/development/prompt-management.md) - PromptManager 使用
- [测试指南](docs/development/testing-guide.md) - 测试规范和最佳实践

---

*最后更新：2026-08-25（§1.46.1 收官：移除旧 Pipeline 系统与 `src/modules/pipeline/`，"管道开发"章节改写为"事件拦截器开发"；配置 `[pipelines]` 正名 `[interceptors]`（CONFIG_VERSION 2.0.4）；历史教训——重构"新建替代物"后必须同步"清除旧物+文档"，否则虚构叙事误导后续开发者）*
