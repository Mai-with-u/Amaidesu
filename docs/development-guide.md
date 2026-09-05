# 开发规范

本文档定义了 Amaidesu 项目的代码规范和最佳实践，所有开发者必须遵守。

## 1. 代码风格

### 1.1 语言规范

- **注释和文档**：使用中文编写注释和文档字符串
- **变量命名**：使用 snake_case（如 `provider_config`, `event_bus`）
- **函数命名**：使用 snake_case（如 `send_to_maibot`, `register_websocket_handler`）
- **类命名**：使用 PascalCase（如 `EventBus`, `CollectorManager`）
- **常量命名**：使用 CamelCase（如 `CoreEvents`, `RoomMessagePayload`）

### 1.2 命名约定表

| 类型 | 命名风格 | 示例 |
|------|---------|------|
| 类名 | PascalCase | `EventBus`, `CollectorManager`, `RateLimitInterceptor`, `StreamerAgent`, `EdgeTTSProvider` |
| 函数/方法名 | snake_case | `send_to_maibot`, `register_websocket_handler` |
| 变量名 | snake_case | `provider_config`, `event_bus` |
| 私有成员 | 前导下划线 | `_message_handlers`, `_is_connected` |
| Collector/Agent/工具/拦截器类 | 以类型名结尾 | `ConsoleInputCollector` / `StreamerAgent` / `EdgeTTSProvider` / `RateLimitInterceptor` |
| 常量类 | PascalCase | `CoreEvents` |

### 1.3 注释规范

```python
class MyComponent:
    """
    组件类的中文文档说明

    负责描述这个类的职责和主要功能。
    """

    async def execute(self, payload: "PayloadType") -> None:
        """执行业务逻辑，把 payload 送到目标位置"""
        # 处理具体逻辑
        pass
```

## 2. 类型注解

### 2.1 必须使用类型注解

所有函数和方法必须有完整的类型注解，包括参数类型和返回值类型。

```python
# ✅ 正确：完整的类型注解
async def handle_message(self, message: MessageBase) -> Optional[MessageBase]:
    """处理消息并返回处理结果"""
    pass

def __init__(self, config: Dict[str, Any]):
    self.logger = get_logger(self.__class__.__name__)
    self.config = config

# ❌ 错误：缺少类型注解
async def handle_message(self, message):
    pass

def __init__(self, config):
    self.config = config
```

### 2.2 常用类型导入

```python
from typing import (
    Any,
    Dict,
    List,
    Optional,
    AsyncIterator,
    Callable,
    TypeVar,
    Generic,
)
from pydantic import BaseModel, Field
```

## 3. 数据类型选用规范

### 3.1 选用原则

| 类型 | 使用场景 | 示例 |
|------|----------|------|
| **Pydantic BaseModel** | 所有数据模型、配置 Schema、事件 Payload | `class UserConfig(BaseModel)` |
| **dataclass** | 仅用于简单的内部统计/包装类 | `@dataclass class CollectorStats` |
| **Protocol** | 定义接口协议 | `class CapabilitiesProvider(Protocol)` |
| **Enum** | 定义常量集合 | `class Emotion(str, Enum)` |

### 3.2 Pydantic 使用示例

```python
from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Literal, Optional
from enum import Enum

class Emotion(str, Enum):
    """全局共享情绪词表（12 个值；详见 src/modules/types/emotion_vocab.py）"""
    NEUTRAL = "neutral"
    HAPPY = "happy"
    # ...

# v2 事件 Payload 通用形态——继承 BasePayload + 嵌套 BaseModel + Literal 约束
# 真实字段与处理逻辑以 src/modules/events/payloads/room.py 为准

class RoomMessageUser(BaseModel):
    """直播间消息发送者（嵌套子结构示例）"""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., description="用户唯一 ID")
    name: str = Field(..., description="用户昵称（显示名）")

class RoomMessagePayload(BaseModel):
    """room.message.* 直播间行为流事件 Payload（v2 语义域事件）"""
    model_config = ConfigDict(extra="forbid")

    live_session_id: str = Field(..., description="场次唯一 ID")
    message_type: Literal["danmaku", "gift", "super_chat", "enter"] = Field(
        ...,
        description="消息类型（与存储 live_chat.message_type 枚举一致）",
    )
    user: RoomMessageUser = Field(..., description="发送者信息")
    content: str = Field(default="", description="文本内容（弹幕/SC 文本；其他类型为空）")
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
```

### 3.3 dataclass 使用示例

仅用于简单的内部统计类：

```python
from dataclasses import dataclass

@dataclass
class CollectorStats:
    ""u91c7u96c6u5668u7edfu8ba1u4fe1u606f""
    processed_count: int = 0
    dropped_count: int = 0
    error_count: int = 0

    @property
    def avg_duration_ms(self) -> float:
        """平均处理时间"""
        if self.processed_count == 0:
            return 0.0
        return self.total_duration_ms / self.processed_count
```

## 4. 日志使用

### 4.1 日志模块导入

```python
from src.modules.logging import get_logger

logger = get_logger("MyClassName")  # 使用类名或模块名作为标识
```

### 4.2 日志级别使用

```python
logger.info("信息日志")  # 常规操作信息
logger.debug("调试日志")  # 调试信息
logger.warning("警告日志")  # 警告信息
logger.error("错误日志", exc_info=True)  # 错误信息，包含堆栈
```

### 4.3 日志过滤

使用 `--filter` 参数时，传入 `get_logger` 的第一个参数（类名或模块名）决定是否显示：

```bash
# 只显示 EdgeTTSProvider 和 StreamerAgent 的日志
uv run python main.py --filter EdgeTTSProvider StreamerAgent
```

## 5. 事件使用规范

### 5.1 使用 CoreEvents 常量

禁止硬编码事件名字符串，必须使用 `CoreEvents` 常量：

```python
from src.modules.events.names import CoreEvents

# ✅ 正确：使用语义域常量
await event_bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, danmaku_payload)
event_bus.on(CoreEvents.PLANNER_CHECKPOINT, self._on_checkpoint, model_class=CheckpointPayload)

# 通配订阅：监听所有工具异步结果回传
event_bus.on(CoreEvents.TOOL_RESULT_WILDCARD, self._on_tool_result, model_class=ToolResultPayload)

# ❌ 错误：硬编码字符串
await event_bus.emit("room.message.danmaku", payload)
```

### 5.2 事件命名约定与常用常量

v2 事件名按**语义域**组织（`live.*` / `room.message.*` / `game.*` / `agenda.*` / `planner.checkpoint` / `tool.result.#`），命名空间按"领域 / 主题 / 动作"点分，不掺阶段前缀：

```python
class CoreEvents:
    # 核心系统事件
    CORE_STARTUP = "core.startup"
    CORE_SHUTDOWN = "core.shutdown"
    CORE_ERROR = "core.error"

    # live.* 场次生命周期
    LIVE_STARTED = "live.started"
    LIVE_ENDED = "live.ended"

    # room.message.* 直播间行为流
    ROOM_MESSAGE_DANMAKU = "room.message.danmaku"
    ROOM_MESSAGE_GIFT = "room.message.gift"
    ROOM_MESSAGE_SUPER_CHAT = "room.message.super_chat"
    ROOM_MESSAGE_ENTER = "room.message.enter"

    # game.* 游戏里程碑（低频、只发重大变化）
    GAME_MILESTONE = "game.milestone"
    GAME_ATTENTION_REQUIRED = "game.attention_required"
    GAME_ERROR = "game.error"

    # agenda / planner 编排进度
    AGENDA_UPDATE = "agenda.update"
    PLANNER_CHECKPOINT = "planner.checkpoint"

    # 异步工具结果通配订阅模式（emit 用具体名，如 "tool.result.speak"）
    TOOL_RESULT_WILDCARD = "tool.result.#"
```

> 完整常量表（含订阅者/Payload 形状）见 [事件系统](architecture/event-system.md#核心事件)。

### 5.3 事件 Payload 要求

每个事件都应声明结构化 Pydantic Payload，订阅时通过 `model_class` 自动反序列化：

```python
from pydantic import BaseModel, Field

class ToolResultPayload(BaseModel):
    """tool.result.* 异步工具结果 Payload（v2 fire-and-forget 结果回传通道）"""
    tool_name: str = Field(..., description="工具名（与 emit 时具体事件名后缀一致）")
    status: str = Field(..., description="success / error")
    result: dict = Field(default_factory=dict, description="执行结果数据")
```

实际 Payload 定义见 `src/modules/events/payloads/{room,live,game,agenda,planner,tool_result,...}.py`，通过 `@register_event` 装饰器自动注册到 `EVENT_REGISTRY`。完整索引见 [事件 Payload 模块](architecture/event-system.md#payload-模块索引)。

## 6. 禁止事项

| 禁止 | 原因 | 替代方案 |
|------|------|----------|
| ❌ 创建新的 Plugin | 插件系统已移除 | 创建 Collector / Agent / 工具 |
| ❌ 使用服务注册机制 | 已废弃 | 使用 EventBus |
| ❌ 硬编码事件名字符串 | 避免拼写错误 | 使用 `CoreEvents` 常量 |
| ❌ 使用空的 except 块 | 隐藏错误 | 记录日志并处理 |
| ❌ 删除失败的测试来"通过" | 自欺欺人 | 修复代码或测试 |
| ❌ 在修复 bug 时进行大规模重构 | 扩大风险范围 | 只修复 bug |
| ❌ 提交未验证的代码 | 可能破坏构建 | 先运行测试和 lint |
| ❌ 类变量中存储可变对象 | 共享状态问题 | 使用 `__init__` 初始化 |
| ❌ 在 main.py 中直接硬编码业务组件 | 违反配置驱动原则 | 使用对应 Manager + 配置驱动 |

### 6.1 架构约束：数据流与边界规则

严格遵守单向数据流：**采集器 emit 语义域事件 → Agent 订阅消费 → 工具被调用 → 结果通过 `tool.result.#` 通配事件回传**（结果不回灌采集器，也不经工具推事件回流 Agent）。

| 禁止模式 | 说明 |
|---------|------|
| ❌ 工具订阅 `room.message.*` 等数据事件 | 工具是被动契约；结果通过 `tool.result.<name>` 单向回传，工具不可反向驱动数据 |
| ❌ 框架模块 `src/modules/` 反向 import 业务包 `src/agents/` | 分层规则：业务包可依赖框架，框架不依赖业务包；违反即打破分层 |
| ❌ 业务 Agent 通过"订阅工具推送事件"做发现 | Agent 可只读查询 `CapabilitiesProvider` 元数据做动作选择，不得靠工具推事件回流 |

> 完整三层规则（数据平面 / 分层规则 / 发现平面）见 [AGENTS.md §架构约束](../AGENTS.md) 与 [数据流规则](architecture/data-flow.md)。

## 7. 测试规范

### 7.1 测试文件组织

- 测试文件名：`test_*.py`
- 测试函数名：`async def test_*():` 或 `def test_*():`
- 异步测试使用 `@pytest.mark.asyncio` 装饰器

### 7.2 测试示例

```python
import asyncio

import pytest
from pydantic import BaseModel, Field

# 测试用的 Pydantic Model
class SimpleTestEvent(BaseModel):
    message: str = Field(default="test", description="测试消息")
    id: int = Field(default=0, description="ID")

@pytest.fixture
def event_bus():
    """创建标准 EventBus 实例"""
    return EventBus()

@pytest.mark.asyncio
async def test_event_emission(event_bus: EventBus):
    """测试事件发布"""
    received = []

    async def handler(event_name: str, payload: SimpleTestEvent, source: str):
        received.append(payload)

    event_bus.on("test.event", handler, SimpleTestEvent)
    await event_bus.emit("test.event", SimpleTestEvent(message="hello"), source="test")

    await asyncio.sleep(0.1)  # 等待异步处理

    assert len(received) == 1
    assert received[0].message == "hello"
```

### 7.3 测试目录结构

测试目录与 `src/` 的 v2 布局对应：`modules/`（共享模块）+ `agents/`（业务 Agent）。

```
tests/
├── architecture/           # 架构约束测试（分层依赖 / 事件流约束）
├── agents/                 # 业务 Agent 测试（含 game/text_adv）
├── characterization/       # 特征化测试（占位与历史快照）
├── config/                 # 配置系统测试
├── dashboard/              # Web Dashboard 测试
├── integration/            # 集成测试
├── mocks/                  # 测试用 Mock 对象
└── modules/                # 模块层测试（对应 src/modules/）
    ├── agents/             # Agent 框架 + StreamerAgent 组件
    ├── base/               # NormalizedMessage 等基类
    ├── collectors/         # bilibili / console / mock / screen / stt
    ├── config/             # 配置 Schema / 升级 hook / 漂移写回
    ├── context/            # ContextAssembler 快照组装
    ├── events/             # EventBus / 拦截器 / Payload 注册表
    ├── llm/                # LLMManager 与客户端
    ├── memory/             # MemoryProvider / SimpleMemory
    ├── storage/            # SQLite 存储层
    ├── tools/              # 工具契约（ToolSpec / Registry / ResultBlock）
    └── tts/                # TTS 客户端
```

> 完整测试目录结构、命名规范与编写指南见 [测试指南](development/testing-guide.md#2-测试目录结构)；权威定义随代码演进维护在 [tests/README.md](../tests/README.md)。

## 8. 提交前检查

### 8.1 必须执行的检查

每次提交代码前，必须依次执行以下检查：

```bash
# 1. 运行测试
uv run pytest tests/

# 2. 代码检查
uv run ruff check .

# 3. 代码格式化
uv run ruff format .
```

### 8.2 常用命令速查

```bash
# 运行所有测试
uv run pytest tests/

# 运行特定测试文件
uv run pytest tests/modules/events/test_event_bus.py -v

# 排除慢速测试
uv run pytest -m "not slow"

# 代码检查（不自动修复）
uv run ruff check .

# 代码检查（自动修复）
uv run ruff check --fix .

# 代码格式化
uv run ruff format .
```

### 8.3 提交信息规范

使用清晰的中文提交信息，描述本次修改的内容：

```
feat: 添加新字幕工具
fix: 修复弹幕解析的边界情况
refactor: 重构组件生命周期管理
docs: 更新开发规范文档
```

## 9. 配置规范

### 9.1 配置文件格式

- 使用 TOML 格式
- 业务 Agent 启用：`agents.toml` 的 `[agents].enabled`
- 工具包启用：`tools.toml` 的 `[tools].enabled`
- 感知包内采集器开关：`tools.toml` 的 `[tools.perception.config.enabled]`
- 渲染包内具体工具开关：`tools.toml` 的 `[tools.output.config.enabled]`
- 拦截器配置：`core.toml` 的 `[interceptors.*]`

### 9.2 配置示例

```toml
# agents.toml —— Agent 启用
[agents]
enabled = ["streamer"]      # 可选: streamer / game / custom

# tools.toml —— 工具包启用（采集器挂在 perception 包下）
[tools]
enabled = ["perception", "output"]

# tools.toml —— 感知包具体采集器
[tools.perception.config]
enabled = ["console_input"]

# tools.toml —— 输出包具体工具
[tools.output.config]
enabled = ["subtitle", "vts"]  # TTS 已于 v2.0.12 退役出工具池，迁至 src/modules/tts/ 基础模块——不再在本表列出

# core.toml —— 事件拦截器
[interceptors.rate_limit]
enabled = true
```

> 配置为多文件结构：`config/core.toml` / `model.toml` / `agents.toml` / `tools.toml` / `memory.toml` / `storage.toml` / `background.toml`（七文件配置）。LLM 采用 provider + profile 两层结构（`[[llm_providers]]` + `[llm] provider=`），详见 [快速开始 - 编辑配置文件](getting-started.md#25-编辑配置文件)。

## 10. 组件开发速查

新增组件按三范式（Collector / Agent / 工具）落在对应位置：

### 10.1 组件类型与位置

| 类型 | 职责 | 位置 |
|------|------|------|
| 采集器 Collector | 持续流型数据源，主动 emit 语义域事件 | `src/modules/collectors/<域>/` |
| 业务 Agent | 自主驱动主体（主播 Planner/Replyer、game text_adv 等） | `src/agents/<family>/<name>/` |
| 工具 Tool | 被动能力契约（渲染/感知/内容引擎），经 ToolRegistry 调度 | `src/modules/tools/<包>/` 或 Agent 包内 |

### 10.2 添加新组件

完整骨架代码、生命周期方法（`start/stop/cleanup`）、`CollectorManager` / `AgentManager` / `ToolRegistry` 三类管理器的统一规范，已迁移至 [组件开发指南](development/component-guide.md)。新增组件按该指南三范式开发即可。

## 相关文档

- [组件开发指南](development/component-guide.md) - Collector / Agent / 工具三范式
- [v2 架构总览](architecture/overview.md) - Agent+工具+存储+编排架构设计
- [数据流规则](architecture/data-flow.md) - 数据流约束与边界规则
- [事件系统](architecture/event-system.md) - EventBus 与事件拦截器
- [提示词管理](development/prompt-management.md) - PromptManager 使用
- [测试指南](development/testing-guide.md) - 测试规范和最佳实践

---

*最后更新：2026-08-28（v2.0.8 Sticker 事件链全链删除——`OUTPUT_STICKER_COMMAND = "output.sticker.command"` 从 §5.2 CoreEvents 示例常量代码块移除（v2.0.0 残留的"v2 保留 Sticker→VTS 单向信号（§1.46.1）"引用同步删除，C1 治理收口））*

*上次更新：2026-08-26（v2.0.0 全面落库——切到 Agent+工具+存储+编排架构、语义域事件、七文件配置体系；测试目录与 component-guide 链接同步；生命周期与三范式细节迁移至 component-guide）*
