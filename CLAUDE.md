# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 核心架构

**3域数据流架构** - 项目按AI VTuber数据处理流程组织为3个业务域：

```
外部输入（弹幕、游戏、语音）
  ↓
【Input Domain】外部数据 → NormalizedMessage
  ├─ InputProvider: 并发采集各类数据
  ├─ Normalization: 标准化为 NormalizedMessage
  └─ Pipelines: 预处理（限流、过滤）
  ↓ EventBus: normalization.message_ready
【Decision Domain】NormalizedMessage → Intent
  ├─ MaiCoreDecisionProvider (默认)
  ├─ LocalLLMDecisionProvider (可选)
  └─ RuleEngineDecisionProvider (可选)
  ↓ EventBus: decision.intent_generated
【Output Domain】Intent → 实际输出
  ├─ Parameters: 参数生成（情绪→表情等）
  └─ OutputProvider: 并发渲染（TTS、字幕、VTS等）
```

**目录结构**：
```
Amaidesu/
├── main.py              # CLI入口
└── src/
    ├── amaidesu_core.py # 核心协调器
    ├── core/            # 基础设施（事件、基类、通信、工具）
    ├── services/        # 共享服务（LLM、配置、上下文）
    └── domains/         # 业务域（input、decision、output）
```

**核心原则**：
- **插件系统已移除** - Provider由Manager统一管理，配置驱动启用
- **EventBus通信** - 唯一的跨域通信机制
- **Provider = 原子能力** - 所有功能模块都是Provider
- **类型安全** - 事件数据使用Pydantic Model定义契约

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

### 基础设施（`src/core/`）
- **events/** - 事件子系统（EventBus、事件名、载荷定义）
- **base/** - Provider基类和数据类型（NormalizedMessage、RawData等）
- **connectors/** - 通信组件（WebSocketConnector、RouterAdapter）
- **utils/** - 无状态工具函数（logger、config工具）

### 共享服务（`src/services/`）
- **llm/** - LLM子系统（LLMService + 各后端实现）
- **config/** - 配置子系统（ConfigService + Schema定义）
- **context/** - 上下文子系统（ContextManager）

### 业务域（`src/domains/`）

| Provider类型 | 位置 | 职责 |
|-------------|------|------|
| **InputProvider** | `domains/input/providers/` | 采集外部数据 |
| **DecisionProvider** | `domains/decision/providers/` | 生成Intent |
| **OutputProvider** | `domains/output/providers/` | 渲染输出 |

### Manager（管理者）
- **InputProviderManager** (`domains/input/`): 管理输入Provider，支持并发启动
- **DecisionManager** (`domains/decision/`): 管理决策Provider，支持运行时切换
- **OutputProviderManager** (`domains/output/`): 管理输出Provider，支持并发渲染

### 事件系统（`src/core/events/`）
- **event_bus.py**: 增强的事件总线（优先级、错误隔离）
- **names.py**: 核心事件名称常量（CoreEvents）
- **payloads.py**: 事件数据契约（Pydantic Model）

**事件命名规范**：使用点分层，如`normalization.message_ready`、`decision.intent_generated`

## 配置文件

### 配置层次
```
config.toml（主配置，从config-template.toml生成）
├── [llm]/[llm_fast]/[vlm] - 全局LLM配置
├── [general] - 平台标识
├── [maicore] - MaiCore连接配置
├── [providers.input] - 输入Provider配置（启用列表）
├── [providers.decision] - 决策Provider配置（启用列表）
├── [providers.output] - 输出Provider配置（启用列表）
├── [providers.*.overrides] - 常用参数覆盖（可选）
└── [pipelines] - 管道配置

各Provider配置（可选，从Schema自动生成）：
└── src/domains/*/providers/*/config.toml（本地配置）
```

### Schema-as-Template配置系统

项目采用**Schema-as-Template**配置系统，通过Pydantic Schema类定义Provider配置。

**核心理念**：配置的"事实来源"是代码中的Pydantic Schema类，而非配置文件。

#### 三级配置合并体系

配置加载时的三级合并顺序（后者覆盖前者）：

```
1. Schema默认值（最低优先级）
   定义位置：src/core/config/schemas/*.py
   示例：room_id: int = Field(default=0)
   ↓ 被2覆盖

2. 主配置覆盖 (config.toml中的[providers.*.overrides])
   定义位置：config.toml
   示例：[providers.input.overrides] bili_danmaku.room_id = "123456"
   ↓ 被3覆盖

3. Provider本地配置 (src/layers/*/providers/{name}/config.toml)
   定义位置：Provider目录/config.toml
   优先级最高，用于本地开发配置
   ↓
最终生效配置
```

#### Schema定义示例

每个Provider都有对应的Pydantic Schema类：

```python
# src/core/config/schemas/input_providers.py
from pydantic import BaseModel, Field

class BiliDanmakuProviderConfig(BaseProviderConfig):
    """Bilibili弹幕输入Provider配置"""

    type: Literal["bili_danmaku"] = "bili_danmaku"
    room_id: int = Field(..., description="直播间ID", gt=0)
    poll_interval: int = Field(default=3, description="轮询间隔（秒）", ge=1)
    message_config: dict = Field(default_factory=dict, description="消息配置")
```

**关键特性**：
- `Field(...)` 表示必填项
- `Field(default=...)` 定义默认值
- `Field(description=...)` 提供配置说明（用于生成注释）
- `Field(ge=1, gt=0)` 等提供验证规则

#### Provider配置格式

**主配置 (config.toml)**:
```toml
# 输入Provider配置
[providers.input]
enabled = true
enabled_inputs = ["console_input", "bili_danmaku"]

# 决策Provider配置
[providers.decision]
enabled = true
active_provider = "maicore"
available_providers = ["maicore", "local_llm", "rule_engine"]

# 输出Provider配置
[providers.output]
enabled = true
enabled_outputs = ["subtitle", "vts", "tts"]

# ExpressionGenerator配置
[providers.output.expression_generator]
default_tts_enabled = true
default_subtitle_enabled = true

# 常用参数覆盖（可选）
[providers.input.overrides]
bili_danmaku.room_id = "123456"

[providers.output.overrides]
subtitle.font_size = 32
vts.vts_host = "192.168.1.100"
```

**Provider本地配置 (可选)**:

Provider本地配置文件（`src/layers/*/providers/{name}/config.toml`）可以从Schema自动生成。如果不存在，程序会在首次运行时自动生成。

```toml
# 示例：src/layers/input/providers/bili_danmaku/config.toml
# 从 BiliDanmakuProviderConfig Schema 自动生成

[bili_danmaku]
# 直播间ID
room_id = 0
# 轮询间隔（秒）
poll_interval = 3
# 消息配置
message_config = {}
```

#### 配置自动生成

当Provider本地配置不存在时，系统会：

1. 从`PROVIDER_SCHEMA_REGISTRY`查找对应的Schema类
2. 从Schema生成`config.toml`文件（作为模板）
3. 返回空字典，让主配置覆盖优先生效

手动生成配置文件：
```python
from src.core.config import ensure_provider_config
from src.core.config.schemas import BiliDanmakuProviderConfig

config_path = ensure_provider_config(
    provider_name="bili_danmaku",
    provider_layer="input",
    schema_class=BiliDanmakuProviderConfig,
    force_regenerate=True  # 强制重新生成
)
```

### 配置验证

程序启动时会自动进行配置验证：

1. **Schema验证** - 使用Pydantic模型验证配置格式和类型
2. **Provider验证** - 检查启用的Provider是否注册
3. **依赖验证** - 检查必需的参数和配置项
4. **连接测试** - 测试外部服务连接（如LLM、MaiCore）

**验证失败示例**：
```python
from pydantic import ValidationError

try:
    config = config_service.get_provider_config_with_defaults(
        "bili_danmaku",
        "input",
        schema_class=BiliDanmakuProviderConfig
    )
except ValidationError as e:
    print(f"配置验证失败: {e}")
    # Error: room_id必须大于0 (got 0)
```

**首次运行**：程序会自动从`config-template.toml`生成`config.toml`，然后退出。请编辑配置文件填入必要信息后重新运行。

详细配置说明见：
- [CONFIG_FINAL_DESIGN.md](docs/CONFIG_FINAL_DESIGN.md) - Schema-as-Template配置系统设计
- [CONFIG_UPGRADE_GUIDE.md](docs/CONFIG_UPGRADE_GUIDE.md) - 配置系统升级指南
- [CONFIG_GENERATOR.md](docs/CONFIG_GENERATOR.md) - 配置生成器使用指南

## 开发注意事项

### 添加新Provider
1. 在对应域创建Provider目录：`src/domains/{domain}/providers/my_provider/`
2. 继承对应的Provider基类（InputProvider/DecisionProvider/OutputProvider）
3. 创建Provider的Schema定义：在`src/services/config/schemas/{domain}_providers.py`中定义
   ```python
   from pydantic import BaseModel, Field

   class MyProviderConfig(BaseProviderConfig):
       """MyProvider配置"""
       type: Literal["my_provider"] = "my_provider"
       api_key: str = Field(default="your-api-key", description="API密钥")
       timeout: int = Field(default=30, description="超时时间（秒）", ge=1)
   ```
4. 注册Schema到`PROVIDER_SCHEMA_REGISTRY`
5. 在Provider的`__init__.py`中注册到ProviderRegistry：
   ```python
   from src.domains.output.provider_registry import ProviderRegistry
   from .my_provider import MyProvider

   ProviderRegistry.register_output("my_provider", MyProvider, source="builtin:my_provider")
   ```
6. 在主配置中启用：
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
项目已完成**核心架构重构**，详见`refactor/ARCHITECTURE_ISSUES_REPORT.md`：

**已完成**：
- ✅ 插件系统完全移除，Provider由Manager统一管理
- ✅ Provider自动注册机制
- ✅ 配置格式统一为`[providers.*]`
- ✅ 事件命名统一（使用CoreEvents常量）
- ✅ 3域架构设计（Input/Decision/Output）
- ✅ 目录结构重组（core/services/domains）

**文档状态**：
- ✅ 当前：`refactor/design/overview.md`（3域架构设计文档）

### 不推荐的做法
- ❌ 不要创建新的Plugin（插件系统已移除）
- ❌ 不要使用服务注册机制（register_service/get_service），用EventBus
- ❌ 不要硬编码事件名字符串，使用CoreEvents常量
- ❌ 不要直接在main.py中创建Provider，用Manager + 配置驱动

## 架构设计文档

详细的架构设计文档位于`refactor/design/`：
- [架构总览](refactor/design/overview.md) - 3域架构概述 + 目录结构设计
- [决策层设计](refactor/design/decision_layer.md) - Decision Domain 详细设计
- [多Provider并发设计](refactor/design/multi_provider.md) - 并发处理架构
- [Pipeline设计](refactor/design/pipeline_refactoring.md) - TextPipeline 设计
- [LLM服务设计](refactor/design/llm_service.md) - LLM 服务设计
- [配置系统](refactor/design/config_system.md) - Schema-as-Template 配置系统

## 数据流关键事件

| 事件名 | 触发时机 | 数据格式 |
|--------|---------|---------|
| `perception.raw_data.generated` | InputProvider采集到数据 | `{"data": RawData, "source": str}` |
| `normalization.message_ready` | InputLayer完成标准化 | NormalizedMessage |
| `decision.intent_generated` | DecisionProvider完成决策 | Intent |
| `expression.parameters_generated` | ExpressionGenerator生成参数 | RenderParameters |

## 测试策略

**单元测试**：测试单个Provider或Manager
- 位置：`tests/domains/{domain}/test_*.py`
- 使用Mock隔离外部依赖

**集成测试**：测试多Provider协作
- 测试数据流完整性

**测试覆盖目标**：核心数据流（Input → Decision → Output）应有E2E测试

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
