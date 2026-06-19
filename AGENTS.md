# AGENTS.md

为在此代码库中工作的 AI 编码代理提供指南。

**本文档已重构为渐进式披露格式**：核心规则在此文件，详细指南请查看 `docs/` 目录。

## 快速导航

| 我想... | 查看文档 |
|---------|---------|
| 快速上手项目 | [快速开始](docs/getting-started.md) |
| 了解代码规范 | [开发规范](docs/development-guide.md) |
| 理解架构设计 | [3阶段架构](docs/architecture/overview.md) |
| 理解事件系统 | [事件系统](docs/architecture/event-system.md) |
| 开发 Collector/Decider/Handler | [阶段参与者开发](docs/development/provider-guide.md) |
| 开发 Pipeline | [管道开发](docs/development/pipeline-guide.md) |
| 管理提示词 | [提示词管理](docs/development/prompt-management.md) |
| 编写测试 | [测试指南](docs/development/testing-guide.md) |

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
| ❌ 创建新的 Plugin（插件系统已移除） | 架构已重构为阶段参与者系统 | 创建 Collector/Decider/Handler |
| ❌ 使用服务注册机制（已废弃） | 使用 EventBus | EventBus 事件系统 |
| ❌ 硬编码事件名字符串 | 避免拼写错误 | 使用 `CoreEvents` 常量 |
| ❌ 使用空的 except 块 | 隐藏错误 | 记录日志并处理 |
| ❌ 删除失败的测试来"通过" | 自欺欺人 | 修复代码或测试 |
| ❌ 在修复 bug 时进行大规模重构 | 扩大风险范围 | 只修复 bug |
| ❌ 提交未验证的代码 | 可能破坏构建 | 先运行测试和 lint |
| ❌ 类变量中存储可变对象 | 共享状态问题 | 使用 `__init__` 初始化 |

### 架构约束：3阶段数据流规则

**严格遵守单向数据流：Input 阶段 → Decision 阶段 → Output 阶段**

| 禁止模式 | 说明 | 详细规则 |
|---------|------|----------|
| ❌ OutputHandler 订阅 Input 事件 | 绕过 Decision 阶段，破坏分层 | [数据流规则](docs/architecture/data-flow.md) |
| ❌ Decider 订阅 Output 事件 | 创建循环依赖 | 同上 |
| ❌ InputCollector 订阅 Decision/Output 事件 | Input 应只发布，不订阅下游 | 同上 |

## AudioStreamChannel 音频流系统

AudioStreamChannel 是专门的音频数据传输通道，与 EventBus 分离，用于高效传输大量音频数据。

### 与 EventBus 的关系

- **EventBus**: 用于元数据事件（开始/结束/状态通知）
- **AudioStreamChannel**: 用于音频数据流（chunk 数据传输）

### 发布者（TTS Handler）

所有 TTS Handler 在 `__init__()` 中通过构造器注入获取 AudioStreamChannel：

```python
def __init__(
    self,
    config: Dict[str, Any],
    event_bus: EventBus,
    audio_stream_channel: Optional[AudioStreamChannel] = None,
):
    self.audio_stream_channel = audio_stream_channel
```

在音频生成时：
1. 调用 `notify_start(AudioMetadata(text=text, sample_rate=...))` 通知开始
2. 循环调用 `publish(AudioChunk(data=chunk_bytes, ...))` 发布音频块
3. 调用 `notify_end(AudioMetadata(...))` 通知结束

### 订阅者（Avatar Handler, Remote Stream Handler）

订阅者在连接/设置阶段注册回调函数：

```python
await audio_channel.subscribe(
    name="handler_name",
    on_audio_start=self._on_audio_start,
    on_audio_chunk=self._on_audio_chunk,
    on_audio_end=self._on_audio_end,
    config=SubscriberConfig(
        queue_size=100,
        backpressure_strategy=BackpressureStrategy.DROP_NEWEST,
    ),
)
```

回调方法负责：
- 接收 AudioChunk
- 重采样到目标采样率（使用 `resample_audio()`）
- 处理音频数据（口型同步、网络传输等）

### 背压策略

- **BLOCK**: 队列满时阻塞等待
- **DROP_NEWEST**: 丢弃新数据（默认，不阻塞 TTS）
- **DROP_OLDEST**: 替换最旧的数据
- **FAIL_FAST**: 队列满时抛出异常

### 依赖注入链路

```python
# main.py
audio_stream_channel = AudioStreamChannel("tts")
await output_handler_manager.setup(..., audio_stream_channel=audio_stream_channel)

# OutputHandlerManager 内部
await self.setup_all_handlers(event_bus, audio_stream_channel=audio_stream_channel)

# Handler
self.audio_stream_channel = audio_stream_channel  # 构造器注入
```

### 共享类型

以下类型被多个阶段共享，因此放在 `src/modules/types/` 中避免循环依赖：

| 类型 | 用途 | 定义位置 |
|------|------|---------|
| `EmotionType` | 情感类型枚举 | `src/modules/types/intent.py` |
| `ActionType` | 动作类型枚举 | `src/modules/types/intent.py` |
| `IntentAction` | 意图动作模型 | `src/modules/types/intent.py` |

**为什么这些类型在 Modules 层？**
- 被 Input/Decision/Output 多个阶段使用
- 如果放在任何一个阶段中，会导致其他阶段依赖它
- 放在 Modules 层可以避免循环依赖

**Decision 阶段特定的类型**：
以下类型位于 `src/modules/types/intent.py` 中（共享类型）：
- `Intent`: 完整的决策意图类（Decision 阶段的核心输出）
- `SourceContext`: 输入源上下文

**如何添加新的共享类型？**
1. 评估类型是否真的需要跨多个阶段使用
2. 如果是，添加到 `src/modules/types/` 中的合适文件
3. 更新相关阶段的导入语句
4. 运行架构测试验证

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
uv run python main.py --filter EdgeTTSHandler SubtitleHandler
```

### Web Dashboard

项目内置 Web 管理界面，有两种访问方式：

| 端口 | 模式 | 说明 |
|------|------|------|
| **60214** | 生产模式 | 后端直接服务打包后的前端（`dist/`），适合最终部署 |
| **5173** | 开发模式 | Vite 开发服务器，支持热更新，适合开发调试 |

```bash
# 方式一：生产模式（后端自动启动）
# 访问 http://127.0.0.1:60214
uv run python main.py

# 方式二：开发模式（需要两个终端）
# 终端1：启动后端
uv run python main.py

# 终端2：启动前端开发服务器（支持热更新）
cd dashboard
npm install   # 首次需要
npm run dev   # 访问 http://localhost:5173
```

**开发模式说明：**
- Vite 开发服务器会自动代理 `/api` 和 `/ws` 请求到后端（60214）
- 修改 Vue 文件后，浏览器会自动热更新（无需刷新）
- 这是前端开发时推荐的方式

**配置**（`config.toml`）：

```toml
[dashboard]
enabled = true
host = "127.0.0.1"
port = 60214
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
| **Protocol** | 定义接口协议 | `class InputPipeline(Protocol)` |

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
| 类名 | PascalCase | `EventBus`, `InputCollector`, `InputPipeline` |
| 函数/方法名 | snake_case | `send_to_maicore`, `register_websocket_handler` |
| 变量名 | snake_case | `handler_config`, `event_bus` |
| 私有成员 | 前导下划线 | `_message_handlers`, `_is_connected` |
| Collector 类 | 以 `Collector` 结尾 | `ConsoleInputCollector`, `BiliDanmakuCollector` |
| Decider 类 | 以 `Decider` 结尾 | `MaiBotDecider`, `LLMDecider` |
| Handler 类 | 以 `Handler` 结尾 | `EdgeTTSHandler`, `SubtitleHandler` |
| 管道类 | 以 `Pipeline` 结尾 | `RateLimitPipeline`, `SimilarTextFilterPipeline` |

**详细规范**：[开发规范](docs/development-guide.md)

## 阶段参与者开发

项目使用阶段参与者系统封装具体功能，由 Manager 统一管理，配置驱动启用。

### 阶段参与者类型

| 类型 | 职责 | 位置 | 示例 |
|------|------|------|------|
| **InputCollector** | 从外部数据源采集数据 | `src/domains/input/collectors/` | ConsoleInputCollector, BiliDanmakuCollector, STTCollector, BiliDanmakuOfficialCollector |
| **Decider** | 处理 NormalizedMessage 生成 Intent | `src/domains/decision/deciders/` | MaiBotDecider, LLMDecider, MaicraftDecider |
| **OutputHandler** | 渲染到目标设备 | `src/domains/output/handlers/` | EdgeTTSHandler, GPTSoVITSHandler, VTSHandler, WarudoHandler, VRChatHandler, SubtitleHandler, StickerHandler, ObsControlHandler, RemoteStreamHandler, DebugConsoleHandler |

### 阶段参与者生命周期方法

| 参与者类型 | 启动方法 | 停止方法 | 说明 |
|--------------|---------|---------|------|
| InputCollector | `start()` | `stop()` | 返回 AsyncIterator，用于数据流生成 |
| Decider | `setup()` | `cleanup()` | 注册到 EventBus，处理消息 |
| OutputHandler | `setup()` | `cleanup()` | 注册到 EventBus，渲染参数 |

**注意**: InputCollector 使用 `start()`/`stop()` 是因为它需要返回异步生成器（AsyncIterator），
而 Decider/OutputHandler 使用 `setup()`/`cleanup()` 是因为它们是事件订阅者。

InputCollector 也提供了 `setup()` 方法作为接口一致性，但它是空实现，实际启动数据流必须使用 `start()`。

### 阶段参与者生命周期快速参考

| 类型 | 启动 | 停止 | 内部初始化 | 内部清理 |
|------|-----|------|----------|----------|
| InputCollector | `start()` | `stop()` + `cleanup()` | `_setup_internal()` | `_cleanup_internal()` |
| Decider | `setup()` | `cleanup()` | `_setup_internal()` | `_cleanup_internal()` |
| OutputHandler | `setup()` | `cleanup()` | `_setup_internal()` | `_cleanup_internal()` |

### 添加新 Handler

1. 继承对应的 Handler 基类（InputCollector/Decider/OutputHandler）
2. 使用 `@collector`/`@decider`/`@handler` 装饰器注册
3. 在配置中启用

**详细指南**：[阶段参与者开发](docs/development/provider-guide.md)

### 配置示例

```toml
# 输入Collector
[collectors]
enabled = ["console_input", "bili_danmaku"]

# 决策Decider
[deciders]
active = "maibot"

# 输出Handler
[handlers]
enabled = ["tts", "subtitle", "vts"]
```

## 管道开发

管道用于在消息处理流程中进行预处理，位于 Input 阶段内部。

### 添加新 Pipeline

1. 继承 `InputPipeline` 类
2. 实现 `process()` 方法
3. 返回 `NormalizedMessage` 继续传递，返回 `None` 丢弃消息

**详细指南**：[管道开发](docs/development/pipeline-guide.md)

## 提示词管理

项目使用 **PromptManager** 统一管理所有 LLM 提示词。

### 基本使用

```python
from src.modules.prompts import get_prompt_manager

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

项目使用 **EventBus** 作为唯一的跨阶段通信机制。

### 基本使用

```python
from src.modules.events.names import CoreEvents

# 发布事件
await event_bus.emit(CoreEvents.INPUT_MESSAGE_READY, normalized_message)

# 订阅事件
await event_bus.subscribe(CoreEvents.INPUT_MESSAGE_READY, self.handle_message)
```

**详细文档**：
- [事件系统](docs/architecture/event-system.md)
- [数据流规则](docs/architecture/data-flow.md)
- [事件命名规范](docs/architecture/event-naming-convention.md)

### 核心事件

| 事件名 | 发布者 | 订阅者 | 数据类型 |
|--------|--------|--------|---------|
| `input.message.ready` | Input 阶段 | Decision 阶段 | `NormalizedMessage` |
| `decision.intent.generated` | Decision 阶段 | Output 阶段 | `Intent` |
| `output.intent.ready` | OutputHandlerManager | OutputHandlers | `Intent` |

## ContextService 上下文管理

ContextService 提供对话历史管理和多会话支持。

### 用途

- 管理对话历史（内存存储）
- 支持多会话隔离（通过 session_id）
- 为 Decider 提供上下文

### 使用示例

见 `docs/development/context-service.md`（待创建）

## 3阶段架构

| 阶段 | 职责 | 位置 |
|----|------|------|
| **Input 阶段** | 数据采集 + 标准化 + 预处理 | `src/domains/input/` |
| **Decision 阶段** | 决策（可替换） | `src/domains/decision/` |
| **Output 阶段** | 参数生成 + 渲染 | `src/domains/output/` |

### 数据流

```
外部输入（弹幕、游戏、语音）
  ↓
【Input 阶段】外部数据 → NormalizedMessage
  ↓ EventBus: input.message.ready
【Decision 阶段】NormalizedMessage → Intent
  ↓ EventBus: decision.intent.generated
【Output 阶段】Intent → 实际输出
```

**详细文档**：[3阶段架构](docs/architecture/overview.md)

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
- ✓ `src/modules/base/raw_data.py`: 定义 RawData 基础类型
- ✓ `src/modules/types/intent.py`: 共享的枚举类型
- ✗ `src/modules/base/base.py`: 重导出 `RenderParameters`（违规）

## 日志使用

```python
from src.modules.logging import get_logger

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
- Collector 配置：`[collectors]`
- Decider 配置：`[deciders]`
- Handler 配置：`[handlers]`
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
    └── domains/         # 业务阶段（input、decision、output）
```

## 通信模式

项目使用 **EventBus** 作为唯一的跨阶段通信机制：
- **事件系统（发布-订阅）**：瞬时通知、广播场景
- 支持优先级、错误隔离、统计功能
- 使用 CoreEvents 常量确保类型安全

## 相关文档

### 新手入门
- [快速开始](docs/getting-started.md) - 环境搭建和基本使用

### 架构理解
- [3阶段架构总览](docs/architecture/overview.md) - 3阶段架构详解
- [数据流规则](docs/architecture/data-flow.md) - 数据流约束和规则
- [事件系统](docs/architecture/event-system.md) - EventBus 使用指南

### 开发指南
- [开发规范](docs/development-guide.md) - 代码风格和约定
- [阶段参与者开发](docs/development/provider-guide.md) - Collector/Decider/Handler 开发详解
- [管道开发](docs/development/pipeline-guide.md) - Pipeline 开发详解
- [提示词管理](docs/development/prompt-management.md) - PromptManager 使用
- [测试指南](docs/development/testing-guide.md) - 测试规范和最佳实践

---

*最后更新：2026-06-19*
