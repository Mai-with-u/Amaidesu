# 数据流变化

本文档详细对比重构前后的数据流设计，解释新架构如何通过单向数据流简化消息处理。

## 数据流总览

### 旧架构：双向辐射模型

```
                    ┌─────────────────────────────┐
                    │        MaiCore (外部)        │
                    └──────────────┬──────────────┘
                                   │ WebSocket
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│                         AmaidesuCore                             │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                      PipelineManager                        │  │
│  │          (入站管道 / 出站管道)                               │  │
│  └────────────────────────────────────────────────────────────┘  │
│                              ▲                                   │
│                              │ 分发                              │
│          ┌───────────────────┼───────────────────┐              │
│          │                   │                   │              │
│          ▼                   ▼                   ▼              │
│    ┌──────────┐        ┌──────────┐        ┌──────────┐        │
│    │ Plugin A │        │ Plugin B │        │ Plugin C │        │
│    │ (Input)  │        │ (Output) │        │ (Service)│        │
│    └──────────┘        └──────────┘        └──────────┘        │
│          │                   ▲                   │              │
│          │                   │                   │              │
│          └───────────────────┴───────────────────┘              │
│                    服务注册 / 事件总线                           │
└──────────────────────────────────────────────────────────────────┘
```

**特点**：
- 所有消息都必须经过 MaiCore
- 插件通过 `AmaidesuCore` 中转
- 双向通信（入站/出站）

### 新架构：单向分层模型

```
┌──────────────────────────────────────────────────────────────────┐
│                      External Sources                             │
│          (B站弹幕 / 语音 / 控制台 / 游戏)                          │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                       INPUT DOMAIN                               │
│                                                                  │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│   │ Provider 1  │  │ Provider 2  │  │ Provider 3  │            │
│   │  (弹幕)     │  │  (语音)     │  │ (控制台)    │            │
│   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘            │
│          │                │                │                    │
│          └────────────────┼────────────────┘                    │
│                           ▼                                     │
│                   NormalizedMessage                             │
│                           │                                     │
│                    InputPipeline                                │
│                           │                                     │
│                           ▼                                     │
│              EventBus.emit(DATA_MESSAGE)                        │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            │ EventBus: data.message
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                     DECISION DOMAIN                              │
│                                                                  │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │                  DecisionProvider                        │   │
│   │      (MaiCore / LLM / Maicraft / Replay)                │   │
│   │                                                         │   │
│   │   EventBus.subscribe(DATA_MESSAGE)                      │   │
│   │           │                                             │   │
│   │           ▼                                             │   │
│   │      处理 NormalizedMessage                             │   │
│   │           │                                             │   │
│   │           ▼                                             │   │
│   │       生成 Intent                                       │   │
│   │           │                                             │   │
│   │           ▼                                             │   │
│   │   EventBus.emit(DECISION_INTENT)                        │   │
│   └─────────────────────────────────────────────────────────┘   │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            │ EventBus: decision.intent
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                       OUTPUT DOMAIN                              │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              OutputProviderManager                         │  │
│  │                                                            │  │
│  │   EventBus.subscribe(DECISION_INTENT)                      │  │
│  │           │                                                │  │
│  │           ▼                                                │  │
│  │      OutputPipeline 过滤                                    │  │
│  │           │                                                │  │
│  │           ▼                                                │  │
│  │   EventBus.emit(OUTPUT_INTENT)                             │  │
│  └────────────────────────┬───────────────────────────────────┘  │
│                           │                                     │
│                           │ EventBus: output.intent             │
│                           │                                     │
│           ┌───────────────┼───────────────┐                     │
│           │               │               │                     │
│           ▼               ▼               ▼                     │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│   │ Provider 1  │  │ Provider 2  │  │ Provider 3  │            │
│   │   (TTS)     │  │   (字幕)    │  │ (虚拟形象)  │            │
│   └─────────────┘  └─────────────┘  └─────────────┘            │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**特点**：
- 严格的单向数据流
- EventBus 作为唯一通信机制
- OutputProviderManager 负责过滤和分发
- 使用 `OUTPUT_INTENT` 事件将过滤后的 Intent 分发给 OutputProvider

## 消息类型对比

### 旧架构消息类型

```python
# 使用 maim_message 库的 MessageBase
from maim_message import MessageBase, UserInfo, BaseMessageInfo, Seg

message = MessageBase(
    message_info=BaseMessageInfo(
        platform="amaidesu_default",
        message_id="xxx",
        user_info=UserInfo(
            user_id="123",
            user_nickname="用户",
        ),
    ),
    message_segment=Seg(
        type="text",
        data="这是一条消息",
    ),
)
```

**问题**：
- 外部库依赖
- 结构复杂，包含大量不必要字段
- 类型定义不明确

### 新架构消息类型

```python
# 输入层标准化消息
@dataclass
class NormalizedMessage:
    """标准化消息，所有输入 Provider 产生的统一格式"""
    source: str           # 来源 Provider 名称
    content: str          # 消息内容
    metadata: Dict[str, Any]  # 附加元数据
    timestamp: float      # 时间戳

# 决策层意图
class Intent(BaseModel):
    """决策意图，DecisionProvider 的输出"""
    text: Optional[str] = None        # 要朗读/显示的文本
    emotions: List[EmotionType] = []  # 情感标签
    actions: List[ActionType] = []    # 动作指令
    source: SourceContext             # 来源上下文
```

**改进**：
- 类型定义明确
- 结构简洁
- 使用 Pydantic 提供验证

## 数据流详细对比

### 场景 1：用户发送弹幕

#### 旧架构

```
1. BiliDanmakuPlugin 轮询获取弹幕
2. 创建 MessageBase 对象
3. 调用 AmaidesuCore.send_to_maicore()
4. PipelineManager.process_outbound_message()
5. WebSocket 发送到 MaiCore
6. MaiCore 处理并返回响应
7. AmaidesuCore._handle_maicore_message() 接收
8. PipelineManager.process_inbound_message()
9. 根据 message_segment.type 分发给注册的处理器
10. TTSPlugin.handler() 处理文本
11. VTSPlugin.handler() 处理动作
```

**问题**：
- 11 步处理，路径长
- 必须经过 MaiCore（即使只是简单回复）
- 分发逻辑在 AmaidesuCore 中

#### 新架构

```
1. BiliDanmakuProvider.start() 生成 NormalizedMessage
2. InputPipeline 过滤（频率限制、相似过滤）
3. EventBus.emit(DATA_MESSAGE)
4. DecisionProvider 接收并处理
5. 生成 Intent
6. EventBus.emit(DECISION_INTENT)
7. OutputPipeline 过滤（敏感词过滤）
8. TTSProvider 接收并朗读
9. VTSProvider 接收并执行动作
```

**改进**：
- 9 步处理，更直接
- 本地 DecisionProvider 可不依赖 MaiCore
- 每个 Provider 独立订阅事件

### 场景 2：语音输入

#### 旧架构

```
STTPlugin 轮询麦克风
    ↓
创建 MessageBase
    ↓
AmaidesuCore.send_to_maicore()
    ↓
WebSocket → MaiCore
    ↓
WebSocket ← MaiCore
    ↓
分发到插件
```

#### 新架构

```
STTProvider.start() 生成 NormalizedMessage
    ↓
InputPipeline 过滤
    ↓
EventBus.emit(DATA_MESSAGE)
    ↓
DecisionProvider 处理
    ↓
EventBus.emit(DECISION_INTENT)
    ↓
OutputProvider 渲染
```

## 管道系统对比

### 旧架构：入站/出站管道

```python
# pipeline_manager.py
class PipelineManager:
    async def process_inbound_message(self, message):
        """处理从 MaiCore 接收的消息"""
        for pipeline in self._inbound_pipelines:
            message = await pipeline.process_message(message)
            if message is None:
                return None
        return message

    async def process_outbound_message(self, message):
        """处理发送到 MaiCore 的消息"""
        for pipeline in self._outbound_pipelines:
            message = await pipeline.process_message(message)
            if message is None:
                return None
        return message
```

**问题**：
- 管道逻辑集中在 AmaidesuCore
- 入站/出站概念与外部通信耦合
- 难以针对特定 Domain 定制管道

### 新架构：Domain 专属管道

```python
# Input Domain 管道
class InputPipeline:
    async def process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        """处理输入消息"""
        pass

# Output Domain 管道
class OutputPipeline:
    async def process(self, intent: Intent) -> Optional[Intent]:
        """处理输出意图"""
        pass
```

**改进**：
- 管道与 Domain 绑定
- 类型安全
- 易于扩展和测试

## 音频流对比

### 旧架构：无专门音频通道

```python
# TTS 插件直接调用播放
class TTSPlugin(BasePlugin):
    async def _speak(self, text):
        audio = await self._generate_audio(text)
        await self._play_audio(audio)  # 直接播放
```

**问题**：
- 音频数据与控制消息混在一起
- 难以实现多订阅者（如同时口型同步和录音）

### 新架构：AudioStreamChannel

```python
# 专门的音频流通道
class AudioStreamChannel:
    async def publish(self, chunk: AudioChunk):
        """发布音频块"""
        pass

    async def subscribe(self, name: str, callbacks: AudioCallbacks):
        """订阅音频流"""
        pass

# TTS Provider 发布音频
class TTSProvider(OutputProvider):
    async def _speak(self, text):
        self.audio_channel.notify_start(metadata)
        async for chunk in self._generate_audio(text):
            await self.audio_channel.publish(chunk)
        self.audio_channel.notify_end(metadata)

# VTS Provider 订阅音频进行口型同步
class VTSProvider(OutputProvider):
    async def _setup_internal(self):
        await self.audio_channel.subscribe(
            name="vts_lip_sync",
            on_audio_chunk=self._on_audio_chunk,
        )
```

**改进**：
- 音频数据与控制分离
- 支持多订阅者
- 背压控制

## 数据流规则

新架构强制执行以下规则：

### 1. 单向数据流

```
Input Domain → Decision Domain → Output Domain
```

- 禁止 Output Provider 订阅 Input 事件
- 禁止 Decision Provider 订阅 Output 事件
- Input Provider 应只发布，不订阅下游

### 2. 事件类型约束

| 事件 | 发布者 | 订阅者 | 数据类型 |
|------|--------|--------|---------|
| `data.message` | InputProvider | DecisionProvider | `NormalizedMessage` |
| `decision.intent` | DecisionProvider | OutputProviderManager | `Intent` |
| `output.intent` | OutputProviderManager | OutputProvider | `Intent` |

### 3. Domain 边界

- 每个 Domain 只能访问自己的 Manager
- 跨 Domain 通信必须通过 EventBus
- 共享状态通过依赖注入

## 迁移建议

### 消息类型迁移

```python
# 旧代码
message = MessageBase(
    message_info=BaseMessageInfo(...),
    message_segment=Seg(type="text", data=text),
)

# 新代码
message = NormalizedMessage(
    source="bili_danmaku",
    content=text,
    metadata={"user_id": user_id},
)
```

### 处理器迁移

```python
# 旧代码
class MyPlugin(BasePlugin):
    async def setup(self):
        self.core.register_websocket_handler("text", self.handler)

    async def handler(self, message: MessageBase):
        text = message.message_segment.data
        await self.process(text)

# 新代码
class MyProvider(OutputProvider):
    async def _setup_internal(self):
        await self.event_bus.subscribe(
            CoreEvents.DECISION_INTENT,
            self._handle_intent
        )

    async def _handle_intent(self, intent: Intent):
        if intent.text:
            await self.process(intent.text)
```

---

*最后更新：2026-02-15*
