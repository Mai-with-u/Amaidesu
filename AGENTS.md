# AGENTS.md

为在此代码库中工作的 AI 编码代理提供指南。

**本文档为 AI 代理核心规则**：只内联**硬约束**（必须/禁止/架构红线）与**高频 API 速查**；详细操作手册走渐进式披露——一律以相对链接指向 `docs/` 权威文档，不在本文复制。通用编程/工具链常识（Python 语法、git 基础、uv 用法等）不收录。

## 快速导航

| 我想... | 查看文档 |
|---------|---------|
| 快速上手项目 | [快速开始](docs/getting-started.md) |
| 了解代码规范 | [开发规范](docs/development-guide.md) |
| 理解架构设计 | [v2.0.0 架构叙事](docs/architecture/v2-architecture.md) |
| 速查组件/目录/时序 | [架构总览](docs/architecture/overview.md) |
| 理解事件系统 | [事件系统](docs/architecture/event-system.md) |
| 开发采集器/工具/Agent | [组件开发指南](docs/development/component-guide.md) |
| 开发事件拦截器 | [事件系统](docs/architecture/event-system.md#事件拦截器interceptor) |
| 管理提示词 | [提示词管理](docs/development/prompt-management.md) |
| 编写测试 | [测试指南](docs/development/testing-guide.md) |

## 硬约束

### 必须遵守

- 移动或者重命名文件的时候注意使用 `git mv` 保留历史记录
- 使用中文和用户沟通以及编写文档、注释
- 需要如实汇报自己的工作进度，不得隐瞒问题不报，不得在未经用户允许的情况下降低任务达成标准
- **提交代码前运行测试**：`uv run pytest tests/` 和 `uv run ruff check .`；**提交前格式化**：`uv run ruff format .`
- **git 提交必须获得用户显式授权**：任何 `git commit` / `git push` 前必须确认用户明确要求（含"提交/commit/push"等词）。计划文件（`.omo/plans/*.md`）中的 Commit 策略**仅覆盖该计划范围内的任务**；计划之外的工作（bug 修复、追加功能、临时改动）即使复用同一委托模板，也**不得**继承提交授权——委托子代理时若任务超出计划范围，**禁止**在 prompt 中写入 commit 指令，改为"完成后展示结果，由用户决定是否提交"。
- **git 提交体规范（Conventional Commits）**：格式 `type(scope): subject`（type ∈ feat/fix/docs/refactor/perf/test/chore；scope 用影响域，如 decision/dashboard/config/core/prompts）。subject 用**中文**简洁描述（≤50 字符）；body 用**中文**说明"为什么"（空行分隔，可留空）。Windows PowerShell 下提交 message 必须用 `git commit -F <file>`（UTF-8 文件）或双引号包裹（防 `$` 变量展开、防中文乱码）；提交后 `git log -1` 复核无乱码、无截断。**禁止**：英文 subject、乱码字符、特殊符号被 shell 吞掉、body 缺失"为什么"。

### 禁止事项

| 禁止 | 原因 | 替代方案 |
|------|------|----------|
| ❌ 创建新的 Plugin（插件系统已移除） | 架构已重构为 Agent+工具系统 | 创建 Collector / BaseAgent 子类 / ToolProvider |
| ❌ 使用服务注册机制（已废弃） | 使用 EventBus | EventBus 事件系统 |
| ❌ 硬编码事件名字符串 | 避免拼写错误 | 使用 `CoreEvents` 常量 |
| ❌ 使用空的 except 块 | 隐藏错误 | 记录日志并处理 |
| ❌ 删除失败的测试来"通过" | 自欺欺人 | 修复代码或测试 |
| ❌ 在修复 bug 时进行大规模重构 | 扩大风险范围 | 只修复 bug |
| ❌ 提交未验证的代码 | 可能破坏构建 | 先运行测试和 lint |
| ❌ 类变量中存储可变对象 | 共享状态问题 | 使用 `__init__` 初始化 |
| ❌ 把"需要用户回复才能继续"的内容加入 todo list | todo tracker 会被系统持续催促,而 agent 又无法推进,陷入死循环 | 用户驱动的阻塞项(等用户决定/确认)不要写成 todo;若必须跟踪,在回复中作为"备注"或"待你确认"显式说明,不进 todo 系统 |

### AI 痕迹防范（防 AI slop）

**原则**：注释/ docstring 只回答"这段代码**是什么**、**为什么这样设计**"。**代码注释禁止引用文档**（`§N.NN`、`Wave N`、`vN.N.N`、`ADR-XXX`、章节号、版本号）——因为文档会频繁变化，未及时维护就会过时，误导读者。行为/架构原因用**自足的自然语言**表述，不锚定到可能漂移的文档位置。

| 禁止 | 替代方案 |
|------|----------|
| 章节引用（`§1.50`、`§1.46.1`、`§1.7 / §1.49`） | 直接陈述架构含义（如"敏感词净化归 Replyer 表达引擎"） |
| 版本变更记录（`Wave N`、`vN.N.N 修复/新增`、`重构后已删除`） | 删除；git log 是变更历史的事实源 |
| `ADR-XXX` 编号引用 | 删除编号，保留其技术实质（如"订阅 room.message.# 落业务表"） |
| 步骤编号注释（`# --- 1. ... ---`、`2b/3c/6d`） | 纯客观标题（`# --- LLM 服务 ---`）；函数过长优先拆函数 |
| docstring 中的"变更历史"段 | 只保留功能/架构描述 |

**import 纪律**：import 一律放文件顶部（`from __future__` 之后、第一个代码语句之前），按 isort 排序。函数体内 import 仅允许以下 5 种情形，且**必须**有自足注释说明原因：

1. `TYPE_CHECKING` 块（类型检查专用）
2. 循环 import 规避（注释写明：模块间互引）
3. 可选重型依赖延迟加载（`pyvts`/`torch`/`sounddevice` 等，注释写明）
4. Pydantic `model_rebuild()` forward-ref（注释写明）
5. **测试可 mock 性**（如 `llm/manager.py`：函数体内 import 使 `unittest.mock.patch` 能拦截，顶部 import 会使 patch 失效）

**硬性禁止**：同一模块顶部和函数体内重复 import；同一模块在多个函数体内重复 import；改函数时先检查顶部是否已有同模块 import——有则复用。

### 架构红线（v2 最高约束）

**架构一句话**：Amaidesu 2.0.0 = **Agent（自主主体）+ 工具（能力契约）+ 存储（状态/记忆）+ 编排（Agenda 节目单）**。完整推导见 [v2.0.0 架构叙事](docs/architecture/v2-architecture.md)。

**主体性判据（★ 最高约束）**：Agent 与工具的唯一判别是"谁驱动谁"——
- **Agent**：自我驱动，没人调也在跑，有循环/目标（主播 Planner、游戏代理 AI 玩家）
- **工具**：被动驱动，被调才干活，无循环（Replyer 表达引擎、屏幕捕捉、VLM；TTS 自 v2.0.12 §8 修正起已是基础模块，不再是工具）
- **直播内容是编排配置 + Planner 上下文/行为模式的变化，不是代码模块**

| 禁止模式 | 说明 | 详细规则 |
|---------|------|----------|
| ❌ 把 Agent 内脏注册为工具 | Planner/Replyer 是主播 Agent 的内部器官（用户拍板），注册为工具即插件换皮 | [组件开发指南](docs/development/component-guide.md) |
| ❌ 内容特有逻辑写进框架层 | 防插件换皮红线：游戏/内容逻辑内聚 `src/agents/<family>/<name>/` 包内，加内容=加包+配置，框架零改动 | 同上 |
| ❌ 采集器订阅下游事件 | 采集器只发布数据事件，不订阅 Agent/工具的结果事件 | [数据流与边界规则](docs/architecture/data-flow.md) |
| ❌ 感知快照做成采集器 | 快照型能力（被调才看，如 `look_at_screen`）是工具；持续流型才是采集器 | 同上 |

数据流三层面约束（数据平面/分层规则/发现平面）完整表述见 [数据流与边界规则](docs/architecture/data-flow.md)。发现平面口诀："能挥手吗"可问（发现），"刚才挥手成功了吗"不可问（结果回灌）。

### 文档维护规则

**单一事实源**（修改以下事实时只改权威处，其他文件只引用链接，禁止复制）：

| 事实 | 唯一权威处 |
|------|-----------|
| 事件表（含发布者/订阅者/数据类型） | `docs/architecture/event-system.md` |
| 数据流图 / 组件清单 / 目录结构 | `docs/architecture/overview.md` |
| 生命周期表 / 三范式开发指南 | `docs/development/component-guide.md` |
| 架构决策记录（ADR） | `docs/architecture/adr/` |
| 数据流规则约束 | `docs/architecture/data-flow.md` |

- 修改文档后更新文件末尾"最后更新"日期（`YYYY-MM-DD` + 变更摘要）
- ADR 编号按创建时间递增，含元数据（状态 / 日期 / 实现提交 40 位 hash）
- 根目录不放图片/视频；图片放 `docs/images/`，视频放 `docs/videos/`
- 详细规范见 [文档维护规范](docs/development/documentation-guide.md)

### 配置 Schema 变更规则（★ 高事故区，改配置必读）

配置系统是"Schema 即真相"（Pydantic Schema 驱动生成/验证/迁移）。**任何对配置结构的修改都必须升版本号**，否则用户现有配置文件不会被自动升级，迁移机制沦为摆设（历史教训：`CONFIG_VERSION` 长期停在 0.4.0 只有漂移警告；大纲功能配置缺失静默失效）。

**版本号机制**：
- 唯一权威定义：`src/modules/config/multi_file_loader.py` 的 `CONFIG_VERSION`
- `src/modules/config/core_schemas.py` 的 `MetaConfig.version` 默认值必须与 `CONFIG_VERSION` **同步修改**（改一必改二）
- 用户文件版本位于 `config/core.toml` 的 `[meta].version`，升级时自动写回

**必须升 CONFIG_VERSION（patch 级，如 2.0.4 → 2.0.5）**：新增/删除/重命名字段（含各工具包/Agent 嵌套配置类）、字段类型或约束变化、字段默认值语义变化、配置段移动/拆分/合并。仅改注释/description 或纯内部重构（不改 TOML 结构）不需要升版本。

**需要数据变换时（字段重命名/段拆分/类型转换/默认值调整）还必须注册 `ConfigUpgradeHook`**：
- 注册到 `src/modules/config/upgrade_hooks.py` 的 `CONFIG_UPGRADE_HOOKS`；hook 必须**原地修改 dict、幂等**、返回变更字段路径列表
- 每个 hook 必须配单元测试（旧结构输入 → 断言新结构输出）
- 纯新增字段（无需数据变换）可不注册 hook，由写回机制自动补默认值

**配置段跨文件移动**必须注册 `CrossFileMigration`：
- 注册到 `src/modules/config/multi_file_loader.py` 的 `CROSS_FILE_MIGRATIONS`（source_file/source_key → target_file/target_key）
- 执行时机：`load_config_dir` 中目标段缺失时合并进目标文件，源文件备份到 `config/old/` 后移除；必须配迁移测试

**存储层表结构变更另有硬规则**：改 `src/modules/storage/schema.py` 的任何表必须升 `SCHEMA_VERSION` 并保证 `schema_migrations` 记录推进（幂等）。

**变更时需同步检查**：

| 变更内容 | 需同步修改 |
|---------|-----------|
| 任何 Schema 变更 | `CONFIG_VERSION` + `MetaConfig.version` |
| 需要数据变换 | `upgrade_hooks.py` 注册 hook + 迁移测试 |
| 配置段跨文件移动 | `multi_file_loader.py` 注册 CrossFileMigration + 迁移测试 |
| 涉及旧配置段（迁移/死配置） | `migration.py` 的 `_SECTION_MAP` / `_DEAD_SECTIONS` |
| 组件嵌套配置（采集器/Agent/工具包） | 对应 `tests/config/test_*_schema.py` 更新 |

**升版本 ≠ 迁移生效（★ 禁止"升了版本但没验证迁移"的提交）**：
- 真正让用户文件升级的是 `load_config_dir` 的**漂移写回闭环**——只覆盖 `multi_file_loader.py` 中接入 `_load_and_validate_schema` + `_write_back_schema_file` 的文件。**当前全部 7 个文件（core/model/agents/tools/memory/storage/background）均已接入**，未来新增配置文件必须同样接入，否则该文件的 Schema 变更不会写回用户文件（字段缺失只在内存兜底，功能静默失效）。
- 每次 Schema 变更后必须实际验证迁移生效：用 `uv run python -c "from src.modules.config.multi_file_loader import load_config_dir; load_config_dir(__import__('pathlib').Path('config'))"` 检查日志出现"已自动升级: 补齐 N 项"且对应字段落盘；或跑 `tests/config/test_config_auto_upgrade.py`。
- 提交前 `uv run pytest tests/config/ -q` 必须通过；注册了 hook 必须有对应迁移测试。禁止"只改 Schema 不升版本/不注册 hook"的提交。

## 高频 API 速查

### 事件系统

```python
from src.modules.events.names import CoreEvents

# 发布事件
await event_bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, payload)

# 订阅事件（model_class 必填，自动反序列化）
event_bus.on(CoreEvents.ROOM_MESSAGE_DANMAKU, self.handle_message, model_class=RoomMessagePayload)

# 通配订阅（收全部工具结果回传）
event_bus.on("tool.result.#", self.on_tool_result, model_class=ToolResultPayload)
```

- 事件按语义域组织：`core.*` / `live.*` / `room.message.*` / `game.*` / `agenda.*` + `planner.*` / `tool.result.<name>`；通配订阅 `*` 单层、`#` 多层尾缀（MQTT 风格）
- 事件 Payload 用 `@register_event("事件名")` 装饰器注册（幂等；启动时 `register_core_events()` 触发 import），不硬编码事件名字符串
- 完整事件表 / 通配排序语义 / Payload 规范见 [事件系统](docs/architecture/event-system.md)

**时间字段约定（★ 硬规则）**：统一毫秒（ms）。时刻字段用 `int` Unix epoch 毫秒，时长/超时字段用毫秒，命名 `<name>_ms`（如 `timestamp_ms`、`render_timeout_ms`）。禁止 `timestamp_s` / `duration_seconds`；历史 `timestamp` 字段用 Pydantic `alias` 兼容。

### 组件开发（三范式）

| 类型 | 职责 | 基类/协议 | 位置 |
|------|------|----------|------|
| **采集器 Collector** | 持续流型数据源，主动推事件（`room.message.*` 等） | `BaseCollector.collect()` 返回 AsyncIterator | `src/modules/collectors/<域>/` |
| **业务 Agent** | 自我驱动主体（主播/游戏代理），决策循环内聚 | `BaseAgent`（协议六面 + `list_tools()` 抽象） | `src/agents/<family>/<name>/` |
| **工具 Tool** | 被动能力契约（渲染/感知/内容引擎），被调才干活 | `ToolProvider` Protocol + `ToolSpec` | `src/modules/tools/<包>/` 或 Agent 包内 |

添加组件三步：
1. 采集器：继承 `BaseCollector`，实现 `collect()`；配置写 `tools.toml` 的 `[tools.perception.config]`
2. 工具：实现 `ToolProvider`（或 Agent 内提供 `list_tools()`），经 `tool_registry.register_provider(...)` 注册（生产模式为 ToolProvider 类；`@tool` 装饰器双模式——无 `registry=` 时入模块级 pending 表，由装配根 `bind_pending_tools(registry)` flush；带 `registry=` 时立即注册，仅测试/本地用）
3. Agent：继承 `BaseAgent`，放 `src/agents/<family>/<name>/` 自包含包；在 `agents.toml` 的 `[agents].enabled` 启用、`src/modules/agents/factory.py` 登记

> **单一事实源**：三范式完整指南（含最小骨架代码、装配路径、生命周期表）见 [组件开发指南](docs/development/component-guide.md)。

### 事件拦截器开发

1. 继承 `EventInterceptor`（`src/modules/events/interceptors/base.py`）
2. 实现 `name` 属性（唯一标识）与 `intercept()` 方法
3. 在 `main.py` 的 `register_event_interceptors()` 中实例化并 `event_bus.add_interceptor()`
4. 配置放 `core.toml` 的 `[interceptors.<name>]`
5. `intercept()` 返回 dict 放行（可原地修改 payload）、返回 `None` 丢弃事件

**详细指南**：[事件系统 - 事件拦截器](docs/architecture/event-system.md#事件拦截器interceptor)

### LLM / 提示词 / 日志

```python
from src.modules.llm import LLMManager
from src.modules.prompts import get_prompt_manager
from src.modules.logging import get_logger

llm_manager = LLMManager()
await llm_manager.setup(model_config)
response = await llm_manager.chat("你好")              # 完整对话（profile: llm）
short_reply = await llm_manager.chat_fast("翻译成英文")  # 快速对话（profile: llm_fast）

prompt = get_prompt_manager().get_raw("amaidesu_replyer")  # 原始提示词
prompt = get_prompt_manager().render("vts_hotkey", text="用户消息")  # 渲染变量

logger = get_logger("MyClassName")  # 类名/模块名；--filter 用同名参数过滤
logger.info("信息日志"); logger.error("错误日志", exc_info=True)
```

- LLM 为 **provider + profile** 两层结构（`[[llm_providers]]` 连接池 + `[llm]/[llm_fast]/[vlm]/[llm_local]/[llm_summary]/[llm_agenda]` profile 引用覆盖），配置见 `config/model.toml`
- 添加 LLM provider：新建 `src/modules/llm/clients/your_client.py` 继承 `BaseLLMClient` 实现 `chat()/stream_chat()`，末尾 `register_client("your_type", YourClient)`，配置加 `[[llm_providers]]`；未知 provider/client_type 在 `setup()` 时 fail-fast
- 提示词：模板键为声明式键（frontmatter `name`），文件内聚在消费组件 `prompts/` 目录，`src/**/prompts/` 约定自动发现，详见 [提示词管理](docs/development/prompt-management.md)

### 依赖注入 / 配置读取

- **服务对象**（LLMManager、PromptManager、EventBus 等）→ 构造器注入（DI）；禁止把服务塞进 Context 容器传递。详见 [依赖注入指南](docs/development/dependency-injection.md)
- 配置：`config/` 目录 **7 文件**（core/model/agents/tools/memory/storage/background，首次运行从 Schema 自动生成；CONFIG_VERSION 2.0.4）；Agent 启用 `[agents].enabled` / 工具包启用 `[tools].enabled` / 拦截器配置 `[interceptors.*]`

## 多工作树并行开发

使用 `git worktree` 为并行任务（多代理协作、实验性改动）提供互相隔离的检出环境：所有工作树共享同一 `.git`，各自独立 index 与工作目录。

- 术语：**主工作区**=检出主分支（`v2.0.0`）的原仓库；**任务工作树**=`git worktree add` 创建的链接工作树；**停泊分支**=`scratch/idle-*` 占位分支
- 任务工作树**物理路径属于机器本地信息**，登记于 `AGENTS.local.md`（不入库）；放置于仓库外同级目录，禁止嵌套在仓库内；同一分支同时只允许一个工作树

**开工固定动作**：
```bash
# ① 侦察：主线位置与他人未提交变更
git status --short && git log --oneline -1 v2.0.0
# ② 换班：从主线最新位置长出新任务分支（无需检出 v2.0.0 本身）
git switch -c task/<名称> v2.0.0
# ③ 环境引导 + 基线对齐（先分清"环境的失败"与"代码的失败"）
uv sync && uv run pytest tests/ -q
```
- **交集预检（硬性前置）**：列出新任务目标文件集，与主工作区未提交变更求交集；非零重叠时改为协调串行，不得开工
- 被 `.gitignore` 排除但测试所需文件（如 `tests/modules/prompts/golden_datasets/*.jsonl`、mock 采集器 `data/` 素材）需从主工作区手动复制或经 post-checkout 钩子补齐

**收口固定动作**：
1. 提交前照常测试 + lint（铁律不变）；提交仍须用户显式授权
2. **主线已前移**：先 `git rebase v2.0.0`；冲突解决后必须做**语义双验**（双方意图的特征标记在合并结果中均可检索到），并重新全量测试
3. **并入主线**：优先快进；主工作区被占用无法 checkout 时，确认 `git merge-base --is-ancestor v2.0.0 <任务分支>` 后用 `git update-ref refs/heads/v2.0.0 <任务分支>` 推进指针，**立即**回主工作区对本任务路径执行 `git checkout HEAD -- <路径>` 同步陈旧副本（索引幽灵条目用 `git rm --cached` 清理），全程不得触碰他人未提交文件
4. 清理：`git worktree remove <路径>`、删除已并入分支

| 禁止 | 替代方案 |
|------|---------|
| ❌ 未做交集预检即开工 | 预检零重叠才并行，否则串行 |
| ❌ 在仓库目录内部嵌套创建工作树 | 放置于仓库外同级目录 |
| ❌ `update-ref` 推进指针后不同步主工作区 | 紧跟路径级 `checkout HEAD -- <路径>` 同步 |
| ❌ 删除失败的工作树来"通过"验证 | 先定位环境差异（缺失的忽略文件 / 过期基线） |

## 常用命令

```bash
uv sync            # 同步依赖（uv 是包管理器）
uv run python main.py                    # 正常运行
uv run python main.py --debug            # 调试模式
uv run python main.py --filter StreamerAgent ConsoleInputCollector   # 过滤日志
uv run python main.py --dry              # 仅验证组合根装配后立即退出

uv run pytest tests/                     # 运行所有测试
uv run pytest tests/path/to/test.py      # 运行特定测试
uv run ruff check .                      # 代码检查
uv run ruff format .                     # 代码格式化
```

Web Dashboard 两种模式（生产 60214 / 开发 60315）说明见 [快速开始 - Web Dashboard](docs/getting-started.md#44-web-dashboard)。

## 测试规范

- pytest；测试文件 `test_*.py`，测试函数 `async def test_*():`；异步测试用 `@pytest.mark.asyncio`
- 详细指南：[测试指南](docs/development/testing-guide.md)

## 其他约定

- **命名约定**：类名 PascalCase（`EventBus`、`CollectorManager`、`StreamerAgent`）；函数/变量 snake_case；私有成员前导下划线；Collector/Agent/工具/拦截器类以类型名结尾（`ConsoleInputCollector` / `StreamerAgent` / `EdgeTTSProvider` / `RateLimitInterceptor`）。详细规范见 [开发规范](docs/development-guide.md)
- **数据类型**：数据模型/配置 Schema/事件 Payload 用 Pydantic BaseModel；简单内部统计用 dataclass；接口协议用 Protocol
- ContextService 提供会话历史/多会话隔离，ContextAssembler（`src/modules/context/`）为 Planner/Replyer 组装快照上下文（纯函数）
- 核心布局：`src/modules/`（框架模块）+ `src/agents/`（业务 Agent：streamer/game）+ `config/`（七文件配置）+ `docs/`（文档），详见 [架构总览](docs/architecture/overview.md#目录结构)
- 日志过滤：`--filter` 参数传入 `get_logger` 的第一个参数（类名或模块名）

## 相关文档

### 新手入门
- [快速开始](docs/getting-started.md) - 环境搭建和基本使用

### 架构理解
- [v2.0.0 架构叙事](docs/architecture/v2-architecture.md) - 重构缘由与设计推导（先读这篇）
- [架构总览](docs/architecture/overview.md) - v2.0.0 组件清单与目录结构
- [数据流规则](docs/architecture/data-flow.md) - 数据流约束和规则
- [事件系统](docs/architecture/event-system.md) - EventBus 使用指南
- [事件命名规范](docs/architecture/event-naming-convention.md) - 事件命名规则

### 开发指南
- [开发规范](docs/development-guide.md) - 代码风格和约定
- [组件开发指南](docs/development/component-guide.md) - 采集器/工具/Agent 三范式开发详解
- [事件拦截器](docs/architecture/event-system.md#事件拦截器interceptor) - 事件拦截器开发详解
- [提示词管理](docs/development/prompt-management.md) - PromptManager 使用
- [测试指南](docs/development/testing-guide.md) - 测试规范和最佳实践

---

*最后更新：2026-09-05（v2.0.12 §8 概念修正：TTS 提升为基础设施（基础模块）。架构红线节"主体性判据"工具例子清单中 TTS 加注"自 v2.0.12 §8 修正起已是基础模块，不再是工具"——其余三例仍为工具；高频 API 速查节"事件系统" + "命名约定" + "依赖注入"未改；事件 Payload / 配置 Schema / 三范式 / 拦截器节未改；同日术语统一：'退役出工具池'改为'提升为基础设施'（避免误导为降级））*
