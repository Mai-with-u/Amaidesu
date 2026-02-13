# 数据流规则

本文档详细说明 3 域架构的数据流约束和规则。

## 核心原则

**严格遵守单向数据流：Input Domain → Decision Domain → Output Domain**

虽然 EventBus 技术上允许任何订阅模式，但架构层面强制约束事件订阅关系。

## 数据流向图

```
┌─────────────────────────────────┐
│ Input Domain                   │
│ (InputProvider 直接构造)        │
│                                │
│ InputProvider.start()           │
│    ↓                           │
│ NormalizedMessage               │
└────────────┬────────────────────┘
             │ data.message
             ▼
┌─────────────────────────────────┐
│ Decision Domain                │
│ (订阅 Input)                    │
└────────────┬────────────────────┘
             │ decision.intent
             ▼
┌─────────────────────────────────┐
│ Output Domain                  │
│ (订阅 Decision)                 │
└─────────────────────────────────┘
```

### Input Domain 内部流程

```
┌─────────────────────────────────┐
│ InputProvider                  │
│ (数据采集 + 自我标准化)         │
└────────────┬────────────────────┘
             │ AsyncIterator[NormalizedMessage]
             ▼
┌─────────────────────────────────┐
│ InputProviderManager           │
│ (错误隔离 + Pipeline过滤)      │
└────────────┬────────────────────┘
             │ data.message
             ▼
         EventBus
```

## 禁止的订阅模式

### 模式1：Output Provider 直接订阅 Input 事件

```python
# ❌ 错误示例
class MyOutputProvider(OutputProvider):
    async def setup(self):
        # 禁止！绕过了 Decision Domain
        self.event_bus.on(
            CoreEvents.DATA_MESSAGE,  # Input 事件
            self.handler
        )
```

**问题**：绕过 Decision Domain，破坏分层架构

### 模式2：Decision Provider 订阅 Output 事件

```python
# ❌ 错误示例
class MyDecisionProvider(DecisionProvider):
    async def setup(self):
        # 禁止！创建循环依赖
        self.event_bus.on(
            CoreEvents.RENDER_COMPLETED,  # Output 事件
            self.handler
        )
```

**问题**：创建循环依赖

### 模式3：Input Provider 订阅下游事件

```python
# ❌ 错误示例
class MyInputProvider(InputProvider):
    async def setup(self):
        # 禁止！Input 应只发布，不订阅下游
        self.event_bus.on(
            CoreEvents.DECISION_INTENT,
            self.handler
        )
```

**问题**：Input Domain 应只发布事件，不订阅下游事件

## 允许的订阅关系

| 订阅者 | 可以订阅的事件 | 禁止订阅的事件 |
|--------|---------------|---------------|
| **Input Domain** | 无（仅发布） | Decision/Output 事件 |
| **Decision Domain** | `DATA_MESSAGE` | `RENDER_*` 事件 |
| **Output Domain** | `DECISION_INTENT` | **`NORMALIZATION_*` 事件** |

## 正确的事件使用

### Input Domain（只发布）

```python
# ✅ 正确：InputProvider 直接构造 NormalizedMessage
class ConsoleInputProvider(InputProvider):
    async def start(self) -> AsyncIterator[NormalizedMessage]:
        """直接返回 NormalizedMessage 流"""
        while self.is_running:
            # 采集数据
            text = await self._fetch_data()

            # 直接构造 NormalizedMessage（无需中间数据结构）
            content = TextContent(
                text=text,
                user="用户昵称",
                user_id="user_id",
            )
            yield NormalizedMessage(
                text=content.text,
                content=content,
                source="console",
                data_type=content.type,
                importance=content.get_importance(),
            )
```

**关键点**：
- InputProvider 的 `start()` 方法直接返回 `AsyncIterator[NormalizedMessage]`
- 不再构造 RawData
- InputCoordinator 和 Normalizer 已删除

### Decision Domain（订阅 Input，发布 Decision）

```python
# ✅ 正确：Decision 订阅 Input 事件
class DecisionCoordinator:
    async def setup(self):
        self.event_bus.on(
            CoreEvents.DATA_MESSAGE,  # Input 事件
            self.handle_message
        )

    async def handle_message(self, event_name: str, payload: MessageReadyPayload, source: str):
        intent = await self.decision_provider.decide(payload.message)
        await self.event_bus.emit(
            CoreEvents.DECISION_INTENT,
            IntentPayload(intent=intent),
            source="DecisionCoordinator"
        )
```

### Output Domain（订阅 Decision，渲染）

```python
# ✅ 正确：Output 订阅 Decision 事件
class OutputProviderManager:
    async def setup(self):
        self.event_bus.on(
            CoreEvents.DECISION_INTENT,  # Decision 事件
            self.handle_intent
        )
```

## 架构测试

项目包含架构测试自动验证：

```bash
uv run pytest tests/architecture/test_event_flow_constraints.py -v
```

测试验证：
- Output Domain 不订阅 Input Domain 事件
- Decision Domain 不订阅 Output Domain 事件
- Input Domain 不订阅下游事件
- 事件流向严格遵守单向原则

## 数据转换

### 外部数据 → NormalizedMessage

```python
# Input Provider 负责自我标准化（重构后）
# 直接从外部数据源构造 NormalizedMessage，无需中间数据结构

# 示例：控制台输入
content = TextContent(
    text="用户消息",
    user="用户昵称",
    user_id="user_id",
)
normalized_message = NormalizedMessage(
    text=content.text,
    content=content,
    source="console",
    data_type=content.type,
    importance=content.get_importance(),
)

# 示例：弹幕输入
content = TextContent(
    text="弹幕内容",
    user="观众昵称",
    user_id="user_id",
)
normalized_message = NormalizedMessage(
    text=content.text,
    content=content,
    source="bili_danmaku",
    data_type=content.type,
    importance=content.get_importance(),
)
```

**重构变化**：
- **删除**：`RawData` 中间数据结构
- **删除**：`InputCoordinator` 协调器
- **删除**：`Normalizer` 标准化系统
- **新增**：Provider 自我标准化（直接构造 `NormalizedMessage`）
- **新增**：`InputProviderManager` 层的 Pipeline 过滤

### NormalizedMessage → Intent

```python
# Decision Domain 负责决策
normalized_message = NormalizedMessage(
    text="用户消息",
    user=User(nickname="观众"),
    source="bili_danmaku"
)

# 决策为
intent = Intent(
    type="response",
    content="回复内容",
    emotion="happy",
    parameters={"tts_enabled": True}
)
```

### Intent → RenderParameters

```python
# Output Domain 负责参数生成
intent = Intent(
    type="response",
    content="回复内容",
    emotion="happy",
    parameters={"tts_enabled": True}
)

# 生成为
render_params = RenderParameters(
    text="回复内容",
    tts_text="回复内容",
    emotion=Emotion(type="happy", intensity=0.8),
    vts_hotkey="smile"
)
```

## 常见问题

### Q: 为什么不让 Output 直接订阅 Input？

A: 这样会绕过 Decision Domain，破坏分层架构：
- 无法统一决策逻辑
- 难以切换决策引擎
- 违反单一职责原则

### Q: 如何在多个 Output 之间共享数据？

A: 通过 Decision Domain 的 Intent：
- Intent 包含所有必要信息
- Output 从 Intent 提取需要的数据
- 避免直接的跨域通信

### Q: 如何实现复杂的跨域协作？

A: 通过 FlowCoordinator（核心协调器）：
- FlowCoordinator 可以协调跨域事件
- 但不直接修改数据流
- 保持单向原则

## Output Domain 内部的音频流

在 Output Domain 内部，音频数据通过 AudioStreamChannel 传输：

```
TTS Provider (EdgeTTS/GPTSoVITS/OmniTTS)
    │
    ├─ EventBus: output.params (触发 TTS)
    │
    └─ AudioStreamChannel: AudioChunk 数据流
            │
            ├─> VTSProvider (口型同步)
            └─> RemoteStreamOutputProvider (网络传输)
```

### 关键点

- **跨域通信**：仍然通过 EventBus（Input → Decision → Output）
- **域内音频流**：通过 AudioStreamChannel（TTS → Avatar/RemoteStream）
- **不违反分层**：AudioStreamChannel 只在 Output Domain 内部使用

详细文档：[AGENTS.md - AudioStreamChannel](../../AGENTS.md#audiostreamchannel-音频流系统)

## 相关文档

- [3域架构总览](overview.md)
- [事件系统](event-system.md)
- [AGENTS.md - AudioStreamChannel](../../AGENTS.md#audiostreamchannel-音频流系统)

---

*最后更新：2026-02-13*
