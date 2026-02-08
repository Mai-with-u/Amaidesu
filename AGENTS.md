# AGENTS.md

为在此代码库中工作的 AI 编码代理提供指南。

**本文档已重构为渐进式披露格式**：核心规则在此文件，详细指南请查看 `docs/` 目录。

## 快速导航

| 我想... | 查看文档 |
|---------|---------|
| 快速上手项目 | [快速开始](docs/getting-started.md) |
| 了解代码规范 | [开发规范](docs/development-guide.md) |
| 理解架构设计 | [3域架构](docs/architecture/overview.md) |
| 开发 Provider | [Provider 开发](docs/development/provider-guide.md) |
| 开发 Pipeline | [管道开发](docs/development/pipeline-guide.md) |
| 管理提示词 | [提示词管理](docs/development/prompt-management.md) |
| 编写测试 | [测试指南](docs/development/testing-guide.md) |

## 重构阶段

当前处于完全重构阶段，在 `refactor` 分支中，不需要保留任何向后兼容的代码，需要彻底重构。

**不必担心会破坏性变更**，因为重构完毕之前，都是没有用户在使用的。

## 核心规范

### 必须遵守

- 移动或者重命名文件的时候注意使用 `git mv` 保留历史记录
- 使用中文和用户沟通以及编写文档、注释
- 需要如实汇报自己的工作进度，不得隐瞒问题不报，不得在未经用户允许的情况下降低任务达成标准
- **提交代码前运行测试**：`uv run pytest tests/` 和 `uv run ruff check .`
- **提交代码前进行格式化**: `uv run ruff format .`

### 禁止事项

| 禁止 | 原因 | 替代方案 |
|------|------|----------|
| ❌ 创建新的 Plugin（插件系统已移除） | 架构已重构为 Provider 系统 | 创建 Provider |
| ❌ 使用服务注册机制（已废弃） | 使用 EventBus | EventBus 事件系统 |
| ❌ 硬编码事件名字符串 | 避免拼写错误 | 使用 `CoreEvents` 常量 |
| ❌ 使用空的 except 块 | 隐藏错误 | 记录日志并处理 |
| ❌ 删除失败的测试来"通过" | 自欺欺人 | 修复代码或测试 |
| ❌ 在修复 bug 时进行大规模重构 | 扩大风险范围 | 只修复 bug |
| ❌ 提交未验证的代码 | 可能破坏构建 | 先运行测试和 lint |
| ❌ 类变量中存储可变对象 | 共享状态问题 | 使用 `__init__` 初始化 |

### 架构约束：3域数据流规则

**严格遵守单向数据流：Input Domain → Decision Domain → Output Domain**

| 禁止模式 | 说明 | 详细规则 |
|---------|------|----------|
| ❌ Output Provider 订阅 Input 事件 | 绕过 Decision Domain，破坏分层 | [数据流规则](docs/architecture/data-flow.md) |
| ❌ Decision Provider 订阅 Output 事件 | 创建循环依赖 | 同上 |
| ❌ Input Provider 订阅 Decision/Output 事件 | Input 应只发布，不订阅下游 | 同上 |

**在提交代码前运行架构测试**：
```bash
uv run pytest tests/architecture/test_event_flow_constraints.py -v
```

## 常用命令

### 包管理器

本项目使用 [uv](https://docs.astral.sh/uv/) 作为 Python 包管理器。

```bash
# 同步依赖
uv sync

# 添加新依赖
uv add package-name

# 移除依赖
uv remove package-name
```

### 运行应用

```bash
# 正常运行
uv run python main.py

# 调试模式
uv run python main.py --debug

# 过滤日志（只显示指定模块）
uv run python main.py --filter TTSProvider SubtitleProvider
```

### 测试

```bash
# 运行所有测试
uv run pytest tests/

# 运行特定测试
uv run pytest tests/layers/input/test_console_provider.py

# 详细输出
uv run pytest tests/ -v

# 排除慢速测试
uv run pytest -m "not slow"
```

### 代码质量

```bash
# 代码检查
uv run ruff check .

# 代码格式化
uv run ruff format .

# 自动修复
uv run ruff check --fix .
```

## 数据类型选用规范

### 统一使用 Pydantic BaseModel

| 类型 | 使用场景 | 示例 |
|------|----------|------|
| **Pydantic BaseModel** | 所有数据模型、配置 Schema、事件 Payload | `class UserConfig(BaseModel)` |
| **dataclass** | 仅用于简单的内部统计/包装类 | `@dataclass class PipelineStats` |
| **Protocol** | 定义接口协议 | `class TextPipeline(Protocol)` |

**详细规范**：[开发规范 - 数据类型选用](docs/development-guide.md#数据类型选用规范)

### 类型注解

```python
# ✅ 正确：总是使用类型注解
async def handle_message(self, message: MessageBase) -> Optional[MessageBase]:
    """处理消息并返回处理结果"""
    pass

def __init__(self, config: Dict[str, Any]):
    self.logger = get_logger(self.__class__.__name__)

# ❌ 错误：缺少类型注解
async def handle_message(self, message):
    pass
```

### 命名约定

| 类型 | 命名风格 | 示例 |
|------|---------|------|
| 类名 | PascalCase | `AmaidesuCore`, `InputProvider`, `TextPipeline` |
| 函数/方法名 | snake_case | `send_to_maicore`, `register_websocket_handler` |
| 变量名 | snake_case | `provider_config`, `event_bus` |
| 私有成员 | 前导下划线 | `_message_handlers`, `_is_connected` |
| Provider 类 | 以 `Provider` 结尾 | `ConsoleInputProvider`, `TTSSProvider` |
| 管道类 | 以 `Pipeline` 结尾 | `RateLimitPipeline`, `SimilarTextFilterPipeline` |

**详细规范**：[开发规范](docs/development-guide.md)

## Provider 开发

项目使用 Provider 系统封装具体功能，由 Manager 统一管理，配置驱动启用。

### Provider 类型

| 类型 | 职责 | 位置 | 示例 |
|------|------|------|------|
| **InputProvider** | 从外部数据源采集数据 | `src/domains/input/providers/` | ConsoleInputProvider, BiliDanmakuInputProvider |
| **DecisionProvider** | 处理 NormalizedMessage 生成 Intent | `src/domains/decision/providers/` | MaiCoreDecisionProvider, LocalLLMDecisionProvider |
| **OutputProvider** | 渲染到目标设备 | `src/domains/output/providers/` | TTSOutputProvider, SubtitleOutputProvider, VTSOutputProvider |

### 添加新 Provider

1. 继承对应的 Provider 基类（InputProvider/DecisionProvider/OutputProvider）
2. 在 Provider 的 `__init__.py` 中注册到 ProviderRegistry
3. 在配置中启用

**详细指南**：[Provider 开发](docs/development/provider-guide.md)

### 配置示例

```toml
# 输入Provider
[providers.input]
enabled_inputs = ["console_input", "bili_danmaku"]

# 决策Provider
[providers.decision]
active_provider = "maicore"

# 输出Provider
[providers.output]
enabled_outputs = ["tts", "subtitle", "vts"]
```

## 管道开发

管道用于在消息处理流程中进行预处理，位于 Input Domain 内部。

### 添加新 Pipeline

1. 继承 `TextPipeline` 类
2. 实现 `process()` 方法
3. 返回 `NormalizedMessage` 继续传递，返回 `None` 丢弃消息

**详细指南**：[管道开发](docs/development/pipeline-guide.md)

## 提示词管理

项目使用 **PromptManager** 统一管理所有 LLM 提示词。

### 基本使用

```python
from src.prompts import get_prompt_manager

# 获取提示词
prompt = get_prompt_manager().get_raw("decision/intent_parser")

# 渲染提示词
prompt = get_prompt_manager().render(
    "output/vts_hotkey",
    text="用户消息",
    hotkey_list_str="smile, wave",
)
```

**详细指南**：[提示词管理](docs/development/prompt-management.md)

## 事件系统

项目使用 **EventBus** 作为唯一的跨域通信机制。

### 基本使用

```python
from src.core.events.names import CoreEvents

# 发布事件
await event_bus.emit(CoreEvents.NORMALIZATION_MESSAGE_READY, normalized_message)

# 订阅事件
await event_bus.subscribe(CoreEvents.NORMALIZATION_MESSAGE_READY, self.handle_message)
```

**详细文档**：
- [事件系统](docs/architecture/event-system.md)
- [数据流规则](docs/architecture/data-flow.md)

### 核心事件

| 事件名 | 发布者 | 订阅者 | 数据类型 |
|--------|--------|--------|---------|
| `normalization.message_ready` | Input Domain | Decision Domain | `NormalizedMessage` |
| `decision.intent_generated` | Decision Domain | Output Domain | `Intent` |
| `expression.parameters_generated` | ExpressionGenerator | OutputProviders | `RenderParameters` |

## 3域架构

| 域 | 职责 | 位置 |
|----|------|------|
| **Input Domain** | 数据采集 + 标准化 + 预处理 | `src/domains/input/` |
| **Decision Domain** | 决策（可替换） | `src/domains/decision/` |
| **Output Domain** | 参数生成 + 渲染 | `src/domains/output/` |

### 数据流

```
外部输入（弹幕、游戏、语音）
  ↓
【Input Domain】外部数据 → NormalizedMessage
  ↓ EventBus: normalization.message_ready
【Decision Domain】NormalizedMessage → Intent
  ↓ EventBus: decision.intent_generated
【Output Domain】Intent → 实际输出
```

**详细文档**：[3域架构](docs/architecture/overview.md)

## 日志使用

```python
from src.utils.logger import get_logger

logger = get_logger("MyClassName")  # 使用类名或模块名
logger.info("信息日志")
logger.debug("调试日志", extra_context={"key": "value"})
logger.error("错误日志", exc_info=True)
```

**日志过滤**：使用 `--filter` 参数时，传入 get_logger 的第一个参数（类名或模块名）

## 测试规范

- 使用 pytest 编写测试
- 测试文件名：`test_*.py`
- 测试函数名：`async def test_*():`
- 异步测试使用 `@pytest.mark.asyncio` 装饰器

**详细指南**：[测试指南](docs/development/testing-guide.md)

## 配置文件

- 配置文件使用 TOML 格式
- Provider 配置：`[providers.input]`, `[providers.decision]`, `[providers.output]`
- 管道配置：`[pipelines.*]`
- 根配置：根目录的 `config-template.toml`
- 首次运行会自动从模板生成 `config.toml`

## 目录结构

```
Amaidesu/
├── main.py              # CLI入口（参数解析、启动应用）
├── AGENTS.md            # 本文件（AI 代理核心规则）
├── CLAUDE.md            # Claude Code 专属规则
├── docs/                # 项目文档（渐进式披露）
│   ├── README.md        # 文档导航
│   ├── getting-started.md
│   ├── development-guide.md
│   ├── architecture/    # 架构文档
│   └── development/     # 开发指南
└── src/
    ├── amaidesu_core.py # 核心协调器（管理组件生命周期）
    ├── core/            # 基础设施（事件、基类、通信、工具）
    ├── services/        # 共享服务（LLM、配置、上下文）
    ├── prompts/         # 提示词管理
    └── domains/         # 业务域（input、decision、output）
```

## 通信模式

项目使用 **EventBus** 作为唯一的跨域通信机制：
- **事件系统（发布-订阅）**：瞬时通知、广播场景
- 支持优先级、错误隔离、统计功能
- 使用 CoreEvents 常量确保类型安全

## 相关文档

### 新手入门
- [快速开始](docs/getting-started.md) - 环境搭建和基本使用

### 架构理解
- [3域架构总览](docs/architecture/overview.md) - 3域架构详解
- [数据流规则](docs/architecture/data-flow.md) - 数据流约束和规则
- [事件系统](docs/architecture/event-system.md) - EventBus 使用指南

### 开发指南
- [开发规范](docs/development-guide.md) - 代码风格和约定
- [Provider 开发](docs/development/provider-guide.md) - Provider 开发详解
- [管道开发](docs/development/pipeline-guide.md) - Pipeline 开发详解
- [提示词管理](docs/development/prompt-management.md) - PromptManager 使用
- [测试指南](docs/development/testing-guide.md) - 测试规范和最佳实践

---

*最后更新：2026-02-09*
