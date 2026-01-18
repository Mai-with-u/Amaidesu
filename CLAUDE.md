# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 核心架构

**5层核心数据流架构** - 项目按AI VTuber数据处理流程组织，每层有明确的输入输出：

```
外部输入（弹幕、游戏、语音）
  ↓
【Layer 1-2: Input】RawData → NormalizedMessage
  ├─ InputProvider: 并发采集 RawData (console_input, bili_danmaku, minecraft等)
  ├─ TextPipeline: 限流、过滤、相似文本检测（可选）
  └─ InputLayer: 标准化为 NormalizedMessage
  ↓ normalization.message_ready (EventBus事件)
【Layer 3: Decision】NormalizedMessage → Intent
  ├─ MaiCoreDecisionProvider (默认，WebSocket+LLM意图解析)
  ├─ LocalLLMDecisionProvider (可选，直接LLM)
  └─ RuleEngineDecisionProvider (可选，规则引擎)
  ↓ decision.intent_generated (EventBus事件)
【Layer 4-5: Parameters+Rendering】Intent → RenderParameters → 输出
  ├─ ExpressionGenerator: Intent → RenderParameters
  └─ OutputProvider: 并发渲染（TTS、字幕、VTS等）
```

**核心原则**：
- **插件系统已移除** - Provider由Manager统一管理，配置驱动启用
- **EventBus通信** - 唯一的跨层通信机制（事件名用点分层，如`normalization.message_ready`）
- **Provider = 原子能力** - 所有功能模块都是Provider，按数据流层级组织
- **类型安全** - 事件数据使用Pydantic Model定义契约（见`src/core/events/`）

## 常用开发命令

### 包管理器
项目使用 [uv](https://docs.astral.sh/uv/) 作为包管理器（比pip快10-100倍）。

```bash
# 同步依赖（自动创建虚拟环境）
uv sync

# 安装语音识别依赖
uv sync --extra stt

# 安装所有可选依赖
uv sync --all-extras

# 添加新依赖
uv add package-name

# 移除依赖
uv remove package-name
```

### 运行应用
```bash
# 正常运行
uv run python main.py

# 调试模式（显示详细日志）
uv run python main.py --debug

# 过滤日志，只显示指定模块（WARNING及以上级别总是显示）
uv run python main.py --filter SubtitleProvider TTSProvider

# 调试模式并过滤特定模块
uv run python main.py --debug --filter BiliDanmakuProvider
```

### 测试
```bash
# 运行所有测试
uv run pytest tests/

# 运行特定测试文件
uv run pytest tests/layers/input/test_console_provider.py

# 运行特定测试用例
uv run pytest tests/layers/input/test_console_provider.py::test_console_provider_basic

# 详细输出模式
uv run pytest tests/ -v

# 只运行标记的测试
uv run pytest -m "not slow"
```

### 代码质量
```bash
# 代码检查
uv run ruff check .

# 代码格式化
uv run ruff format .

# 自动修复可修复的问题
uv run ruff check --fix .
```

### 模拟MaiCore测试
当不方便部署MaiCore时，可以使用模拟服务器：
```bash
uv run python mock_maicore.py
```

## 关键组件说明

### 核心模块（`src/core/`）
- **AmaidesuCore** (`amaidesu_core.py`): 中央枢纽，管理EventBus、LLMService、PipelineManager、Managers
- **EventBus** (`event_bus.py`): 增强的事件总线（优先级、错误隔离、统计功能）
- **ProviderRegistry** (`layers/rendering/provider_registry.py`): 统一管理所有Provider的注册和创建
- **LLMService** (`llm_service.py`): 管理LLM客户端（llm/llm_fast/vlm）
- **FlowCoordinator** (`flow_coordinator.py`): 协调Layer 3 → Layer 4-5的数据流

### Provider接口（`src/core/base/`）
**Provider是核心抽象**，封装具体功能：

| Provider类型 | 职责 | 示例实现 |
|-------------|------|---------|
| **InputProvider** | 从外部数据源采集RawData | ConsoleInputProvider, BiliDanmakuProvider, MinecraftProvider |
| **DecisionProvider** | 处理NormalizedMessage并决策 | MaiCoreDecisionProvider, LocalLLMDecisionProvider, RuleEngineDecisionProvider |
| **OutputProvider** | 渲染到目标设备 | TTSProvider, SubtitleProvider, VTSProvider |

### Manager（管理者）
- **InputProviderManager** (`src/layers/input/input_provider_manager.py`): 管理输入Provider的生命周期，支持并发启动、优雅停止、错误隔离
- **DecisionManager** (`src/layers/decision/decision_manager.py`): 管理决策Provider，支持运行时切换
- **OutputProviderManager** (`src/core/output_provider_manager.py`): 管理输出Provider的生命周期

### 事件系统（`src/core/events/`）
- **EventRegistry** (`registry.py`): 事件类型注册表，支持核心事件（只读）和插件事件（开放）
- **CoreEvents** (`names.py`): 核心事件名称常量（如`CoreEvents.NORMALIZATION_MESSAGE_READY`）
- **Payloads** (`payloads/`): 事件数据契约（Pydantic Model）

**事件命名规范**：使用点分层，如`perception.raw_data.generated`、`normalization.message_ready`、`decision.intent_generated`

## 配置文件

### 配置层次
```
config.toml（根配置）
├── [llm]/[llm_fast]/[vlm] - LLM配置
├── [general] - 平台标识
├── [maicore] - MaiCore连接配置
├── [providers.input] - 输入Provider配置
├── [providers.decision] - 决策Provider配置
├── [providers.output] - 渲染Provider配置
└── [pipelines] - 管道配置
```

### Provider配置格式
```toml
# 输入Provider配置
[providers.input]
enabled = true
enabled_inputs = ["console_input", "bili_danmaku"]

[providers.input.inputs.console_input]
type = "console_input"
enabled = true

# 决策Provider配置
[providers.decision]
enabled = true
active_provider = "maicore"
available_providers = ["maicore", "local_llm", "rule_engine"]

[providers.decision.providers.maicore]
type = "maicore"
enabled = true

# 输出Provider配置
[providers.output]
enabled = true
enabled_outputs = ["subtitle", "vts", "tts"]

[providers.output.outputs.subtitle]
type = "subtitle"
enabled = true
```

**首次运行**：程序会自动从`config-template.toml`生成`config.toml`，然后退出。请编辑配置文件填入必要信息后重新运行。

## 开发注意事项

### 添加新Provider
1. 在对应层创建Provider文件：`src/layers/{layer}/providers/my_provider/my_provider.py`
2. 继承对应的Provider基类（InputProvider/DecisionProvider/OutputProvider）
3. 在Provider的`__init__.py`中注册到ProviderRegistry：
   ```python
   from src.layers.rendering.provider_registry import ProviderRegistry
   from .my_provider import MyProvider

   ProviderRegistry.register_output("my_provider", MyProvider, source="builtin:my_provider")
   ```
4. 在配置中启用：
   - InputProvider: 添加到 `[providers.input]` 的 `enabled_inputs` 列表
   - OutputProvider: 添加到 `[providers.output]` 的 `enabled_outputs` 列表
   - DecisionProvider: 添加到 `[providers.decision]` 的 `available_providers` 列表

### 事件使用规范
- **使用常量**：优先使用`CoreEvents`常量，避免硬编码字符串
- **核心事件用Pydantic Model**：确保类型安全
- **事件名分层**：使用点分层（如`decision.intent_generated`）

### 日志使用
```python
from src.utils.logger import get_logger

logger = get_logger("MyClassName")  # 使用类名或模块名
logger.info("信息日志")
logger.debug("调试日志", extra_context={"key": "value"})
logger.error("错误日志", exc_info=True)
```

**日志过滤**：使用`--filter`参数时，传入get_logger的第一个参数（类名或模块名）

### 重构完成状态
项目已完成**核心架构重构**（2026-02-02），详见`refactor/ARCHITECTURE_ISSUES_REPORT.md`：

**已完成**（P0-P1）：
- ✅ 插件系统完全移除，Provider由Manager统一管理
- ✅ Provider自动注册机制（22个Provider全部注册）
- ✅ InputProviderManager接入主流程
- ✅ 配置格式统一为`[providers.*]`，旧配置已标记为deprecated
- ✅ LLMService依赖注入重构
- ✅ E2E测试用例完整（tests/e2e/）

**已完成**（P2）：
- ✅ 事件命名统一（使用CoreEvents常量）
- ✅ 配置迁移检测工具

**文档状态**：
- ✅ 准确：`refactor/design/overview.md`（5层架构设计文档）
- ✅ 当前文档：已更新为5层架构和Provider系统

### 不推荐的做法
- ❌ 不要创建新的Plugin（插件系统已移除）
- ❌ 不要使用服务注册机制（register_service/get_service），用EventBus
- ❌ 不要硬编码事件名字符串，使用CoreEvents常量
- ❌ 不要直接在main.py中创建Provider，用Manager + 配置驱动

## 架构设计文档

详细的架构设计文档位于`refactor/design/`：
- [架构总览](refactor/design/overview.md) - 5层架构概述
- [5层架构设计](refactor/design/layer_refactoring.md) - 核心数据流详细说明
- [决策层设计](refactor/design/decision_layer.md) - 可替换的决策Provider系统
- [多Provider并发设计](refactor/design/multi_provider.md) - 并发处理架构
- [LLM服务设计](refactor/design/llm_service.md) - LLM调用基础设施

## 数据流关键事件

| 事件名 | 触发时机 | 数据格式 |
|--------|---------|---------|
| `perception.raw_data.generated` | InputProvider采集到数据 | `{"data": RawData, "source": str}` |
| `normalization.message_ready` | InputLayer完成标准化 | NormalizedMessage |
| `decision.intent_generated` | DecisionProvider完成决策 | Intent |
| `expression.parameters_generated` | ExpressionGenerator生成参数 | RenderParameters |

## 测试策略

**单元测试**：测试单个Provider或Manager
- 位置：`tests/layers/{layer}/test_*.py`
- 使用Mock隔离外部依赖

**集成测试**：测试多Provider协作
- 位置：`tests/layers/input/test_multi_provider_integration.py`
- 测试数据流完整性

**测试覆盖目标**：核心数据流（Layer 1-5）应有E2E测试（当前缺失，见`docs/VTUBER_FLOW_E2E_GAP_ANALYSIS.md`）

## 项目特定约定

- **中文注释**：代码注释和用户界面使用中文
- **配置优先**：所有可配置项都应通过配置文件控制，不硬编码
- **异步优先**：所有IO操作都应使用async/await
- **错误隔离**：单个Provider失败不应影响其他Provider
- **日志分级**：DEBUG用于开发，INFO用于正常运行，WARNING用于可恢复问题，ERROR用于严重问题

## Git工作流

- **主分支**：`main`
- **重构分支**：`refactor`（当前工作分支）
- **提交规范**：使用Conventional Commits格式（feat/fix/docs/refactor等）
