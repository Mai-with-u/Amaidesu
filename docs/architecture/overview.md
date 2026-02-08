# 3域架构总览

Amaidesu 采用 3 域架构，实现清晰的数据流和职责分离。

## 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         Amaidesu Core                           │
│                    (核心协调器 - 生命周期管理)                    │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  Input Domain │   │Decision Domain│   │ Output Domain │
│  输入域        │   │   决策域       │   │   输出域       │
└───────────────┘   └───────────────┘   └───────────────┘
        │                     │                     │
        │                     │                     │
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ InputProvider │   │DecisionProvider│  │OutputProvider │
│ (数据采集)     │   │  (决策引擎)     │   │  (渲染输出)    │
└───────────────┘   └───────────────┘   └───────────────┘
```

## 3域职责

### Input Domain（输入域）

**位置**：`src/domains/input/`

**职责**：
- 数据采集：从外部数据源获取原始数据
- 标准化：将不同来源的数据统一为 `NormalizedMessage`
- 预处理：通过管道进行限流、过滤等处理

**核心组件**：
- `InputProviderManager` - 管理 InputProvider 生命周期
- `InputProvider` - 数据采集接口
- `TextPipeline` - 消息预处理管道

**数据输出**：`NormalizedMessage`

### Decision Domain（决策域）

**位置**：`src/domains/decision/`

**职责**：
- 决策：将 `NormalizedMessage` 转换为 `Intent`
- 可替换：支持多种决策引擎（MaiCore、本地LLM、规则引擎）

**核心组件**：
- `DecisionManager` - 管理决策 Provider
- `DecisionProvider` - 决策接口

**可用的 DecisionProvider**：
- `MaiCoreDecisionProvider` - 默认，使用 MaiCore WebSocket
- `LocalLLMDecisionProvider` - 使用本地 LLM API
- `RuleEngineDecisionProvider` - 规则引擎

**数据输出**：`Intent`

### Output Domain（输出域）

**位置**：`src/domains/output/`

**职责**：
- 参数生成：将 `Intent` 转换为 `RenderParameters`
- 并发渲染：多个 OutputProvider 同时渲染

**核心组件**：
- `OutputProviderManager` - 管理 OutputProvider
- `ExpressionGenerator` - 参数生成器
- `OutputProvider` - 渲染接口

**可用的 OutputProvider**：
- `TTSOutputProvider` - 语音合成
- `SubtitleOutputProvider` - 字幕显示
- `VTSOutputProvider` - VTube Studio 控制
- `StickerOutputProvider` - 贴纸显示

## 数据流

```
外部输入（弹幕、游戏、语音）
    ↓
【Input Domain】外部数据 → NormalizedMessage
  ├─ InputProvider: 并发采集数据
  ├─ Normalization: 标准化
  └─ Pipelines: 预处理（限流、过滤）
  ↓ EventBus: normalization.message_ready
【Decision Domain】NormalizedMessage → Intent
  ├─ MaiCoreDecisionProvider (默认)
  ├─ LocalLLMDecisionProvider (可选)
  └─ RuleEngineDecisionProvider (可选)
  ↓ EventBus: decision.intent_generated
【Output Domain】Intent → 实际输出
  ├─ ExpressionGenerator: 参数生成（情绪→表情等）
  └─ OutputProvider: 并发渲染（TTS、字幕、VTS等）
```

## 核心原则

### 单向数据流

**严格遵守单向数据流：Input Domain → Decision Domain → Output Domain**

虽然 EventBus 技术上允许任何订阅模式，但架构层面强制约束事件订阅关系。

### 禁止的订阅模式

| 模式 | 说明 |
|------|------|
| ❌ Output Provider 直接订阅 Input 事件 | 绕过 Decision Domain，破坏分层 |
| ❌ Decision Provider 订阅 Output 事件 | 创建循环依赖 |
| ❌ Input Provider 订阅 Decision/Output 事件 | Input 应只发布，不订阅下游 |

### 配置驱动

所有 Provider 通过配置文件启用和配置：

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

## EventBus 事件系统

### 核心事件

| 事件名 | 发布者 | 订阅者 | 数据 |
|--------|--------|--------|------|
| `normalization.message_ready` | Input Domain | Decision Domain | `NormalizedMessage` |
| `decision.intent_generated` | Decision Domain | Output Domain | `Intent` |
| `expression.parameters_generated` | ExpressionGenerator | OutputProviders | `RenderParameters` |

### 事件使用

```python
# 发布事件
from src.core.events.names import CoreEvents
await event_bus.emit(CoreEvents.NORMALIZATION_MESSAGE_READY, message)

# 订阅事件
await event_bus.subscribe(CoreEvents.NORMALIZATION_MESSAGE_READY, self.handle_message)
```

详细事件系统见：[事件系统](event-system.md)

## 可替换性

### Decision Provider 可替换

通过配置文件切换决策引擎：

```toml
# 使用 MaiCore
[providers.decision]
active_provider = "maicore"

# 使用本地 LLM
[providers.decision]
active_provider = "local_llm"

# 使用规则引擎
[providers.decision]
active_provider = "rule_engine"
```

### Output Provider 可扩展

添加新的输出 Provider：

1. 继承 `OutputProvider` 基类
2. 在 `__init__.py` 中注册到 `ProviderRegistry`
3. 在配置中启用

## 架构测试

项目包含架构测试自动验证事件流向：

```bash
uv run pytest tests/architecture/test_event_flow_constraints.py -v
```

## 设计文档

详细设计见：`refactor/design/overview.md`

## 相关文档

- [数据流规则](data-flow.md) - 3域数据流约束
- [事件系统](event-system.md) - EventBus 使用指南
- [Provider 开发](../development/provider-guide.md) - Provider 开发指南

---

*最后更新：2026-02-09*
