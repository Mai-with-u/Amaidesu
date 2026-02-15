# 架构对比详解

本文档详细对比重构前后的架构设计，帮助开发者理解架构变化的动机和收益。

## 架构总览对比

### 旧架构：插件化中心辐射模型

```
                    ┌─────────────────┐
                    │   AmaidesuCore  │
                    │  (中心协调器)    │
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
         ▼                   ▼                   ▼
   ┌───────────┐      ┌───────────┐      ┌───────────┐
   │  Plugin 1 │      │  Plugin 2 │      │  Plugin N │
   │  (Input)  │      │ (Output)  │      │ (Service) │
   └───────────┘      └───────────┘      └───────────┘
         │                   │                   │
         └───────────────────┴───────────────────┘
                             │
                    WebSocket to MaiCore
```

**特点**：
- 所有插件通过 `AmaidesuCore` 中转通信
- 插件通过 `register_service()` 共享服务
- 消息必须经过 MaiCore 处理

### 新架构：3域分层架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        External Input                           │
│              (弹幕、语音、控制台、游戏)                           │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     INPUT DOMAIN                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │InputProvider │  │InputProvider │  │InputProvider │          │
│  │  (弹幕)       │  │  (语音)       │  │  (控制台)    │          │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
│         │                 │                 │                   │
│         └─────────────────┼─────────────────┘                   │
│                           ▼                                     │
│                  NormalizedMessage                              │
│                           │                                     │
│                  Pipeline 过滤                                   │
└───────────────────────────┬─────────────────────────────────────┘
                            │ EventBus: data.message
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    DECISION DOMAIN                              │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                  DecisionProvider                         │  │
│  │    (MaiCore / LLM / Maicraft / Replay)                   │  │
│  └────────────────────────┬─────────────────────────────────┘  │
│                           │                                     │
│                           ▼                                     │
│                        Intent                                   │
└───────────────────────────┬─────────────────────────────────────┘
                            │ EventBus: decision.intent
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     OUTPUT DOMAIN                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │OutputProvider│  │OutputProvider│  │OutputProvider│          │
│  │   (TTS)      │  │   (字幕)      │  │   (虚拟形象) │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│                                                                 │
│                  OutputPipeline 过滤                             │
└─────────────────────────────────────────────────────────────────┘
```

**特点**：
- 严格的单向数据流
- EventBus 作为唯一的跨域通信机制
- 每个 Domain 内部职责清晰

## 目录结构对比

### 旧架构

```
Amaidesu-dev/
├── main.py
├── src/
│   ├── core/                    # 核心模块
│   │   ├── amaidesu_core.py    # 中心协调器（600+ 行）
│   │   ├── plugin_manager.py   # 插件管理器（450+ 行）
│   │   ├── event_bus.py        # 事件总线（可选）
│   │   ├── pipeline_manager.py # 管道管理器
│   │   ├── context_manager.py  # 上下文管理
│   │   ├── llm_client_manager.py
│   │   └── avatar/
│   │       └── avatar_manager.py
│   ├── plugins/                 # 24 个插件
│   │   ├── bili_danmaku/
│   │   ├── bili_danmaku_official/
│   │   ├── bili_danmaku_official_maicraft/
│   │   ├── console_input/
│   │   ├── dg_lab_service/
│   │   ├── emotion_judge/
│   │   ├── gptsovits_tts/
│   │   ├── keyword_action/
│   │   ├── maicraft/
│   │   ├── stt/
│   │   ├── subtitle/
│   │   ├── tts/
│   │   ├── vts/
│   │   └── ... (共 24 个)
│   ├── pipelines/               # 消息处理管道
│   │   ├── command_processor/
│   │   ├── command_router/
│   │   ├── similar_message_filter/
│   │   └── throttle/
│   ├── openai_client/           # LLM 客户端
│   ├── config/
│   └── utils/
└── tests/
```

**问题**：
- `plugins/` 目录下混合了输入、输出、服务类插件
- `core/` 目录承担了过多职责
- 缺乏清晰的模块边界

### 新架构

```
Amaidesu/
├── main.py                      # CLI 入口（简洁）
├── src/
│   ├── domains/                 # 业务域（核心变化）
│   │   ├── input/              # 输入域
│   │   │   ├── __init__.py
│   │   │   ├── base.py         # InputProvider 基类
│   │   │   ├── provider_manager.py
│   │   │   ├── pipelines/      # 输入管道
│   │   │   │   ├── rate_limit/
│   │   │   │   └── similar_filter/
│   │   │   └── providers/      # 输入 Provider
│   │   │       ├── console_input/
│   │   │       ├── bili_danmaku/
│   │   │       ├── bili_danmaku_official/
│   │   │       ├── stt/
│   │   │       └── ...
│   │   ├── decision/           # 决策域
│   │   │   ├── __init__.py
│   │   │   ├── base.py         # DecisionProvider 基类
│   │   │   ├── provider_manager.py
│   │   │   └── providers/      # 决策 Provider
│   │   │       ├── maicore/
│   │   │       ├── llm/
│   │   │       ├── maicraft/
│   │   │       └── replay/
│   │   └── output/             # 输出域
│   │       ├── __init__.py
│   │       ├── base.py         # OutputProvider 基类
│   │       ├── provider_manager.py
│   │       ├── pipelines/      # 输出管道
│   │       │   └── profanity_filter/
│   │       └── providers/      # 输出 Provider
│   │           ├── audio/      # TTS 类
│   │           │   ├── edge_tts/
│   │           │   ├── gptsovits/
│   │           │   └── omni_tts/
│   │           ├── avatar/     # 虚拟形象类
│   │           │   ├── vts/
│   │           │   ├── warudo/
│   │           │   └── vrchat/
│   │           ├── subtitle/
│   │           ├── sticker/
│   │           └── ...
│   └── modules/                 # 共享基础设施
│       ├── config/             # 配置管理
│       ├── context/            # 上下文服务
│       ├── di/                 # 依赖注入
│       ├── events/             # 事件系统
│       ├── llm/                # LLM 服务
│       ├── logging/            # 日志系统
│       ├── prompts/            # 提示词管理
│       ├── registry/           # Provider 注册表
│       ├── streaming/          # 音频流通道
│       └── types/              # 共享类型
└── docs/
    ├── architecture/           # 架构文档
    └── development/            # 开发指南
```

**改进**：
- 按职责分域，每个域有清晰的边界
- `modules/` 提供共享基础设施
- 简化目录结构，移除不必要的 `services/` 层

## 核心组件对比

### AmaidesuCore vs Domain Managers

| 方面 | 旧架构：AmaidesuCore | 新架构：Domain Managers |
|------|---------------------|------------------------|
| **职责** | WebSocket、消息分发、服务注册、HTTP | 仅协调生命周期 |
| **代码行数** | 600+ 行 | 每个 Manager 约 100-200 行 |
| **耦合度** | 高（所有功能集中） | 低（职责分离） |
| **可测试性** | 难（需要模拟所有依赖） | 易（可单独测试每个 Manager） |

### Plugin vs Provider

| 方面 | 旧架构：Plugin | 新架构：Provider |
|------|---------------|------------------|
| **基类** | `BasePlugin` | `InputProvider` / `DecisionProvider` / `OutputProvider` |
| **生命周期** | `setup()` / `cleanup()` | `init()` / `start()` / `stop()` / `cleanup()`（所有 Provider 类型统一） |
| **依赖获取** | `self.core.get_service()` | `self.context.xxx` |
| **类型安全** | 弱 | 强（泛型支持） |
| **消息类型** | `MessageBase` | `NormalizedMessage` / `Intent` |

### 服务注册 vs 依赖注入

```python
# 旧架构：服务注册
class MyPlugin(BasePlugin):
    async def setup(self):
        # 运行时发现依赖，可能失败
        context = self.core.get_service("prompt_context")
        if context is None:
            raise RuntimeError("服务未注册")

# 新架构：依赖注入（完整注入链路）

# 1. main.py 创建 ProviderContext
from src.modules.di import ProviderContext

context = ProviderContext(
    event_bus=event_bus,
    llm_service=llm_manager,
    context_service=context_service,
    audio_stream_channel=audio_stream_channel,
)

# 2. ProviderManager 创建 Provider 时注入 context
class InputProviderManager:
    async def load_provider(self, provider_class, config):
        provider = provider_class(config=config, context=self._context)
        return provider

# 3. Provider 通过 self.context 访问依赖
class MyProvider(InputProvider):
    def __init__(self, config: dict, context: ProviderContext):
        self.config = config
        self.context = context  # 构造时注入

    async def init(self):
        if self.context.event_bus:
            await self.context.event_bus.subscribe(...)
```

### 核心模块变化

重构后新增了多个核心模块，详细变化见 [core_modules.md](core_modules.md)：

| 模块 | 旧架构 | 新架构 |
|------|--------|--------|
| **Prompts** | 硬编码字符串 | `PromptManager` + 模板文件 |
| **Context** | `ContextManager`（聚合外部上下文） | `ContextService`（对话历史） |
| **Logging** | `utils/logger`（导入时配置） | `modules/logging`（配置驱动） |

## 数据流对比

### 旧架构数据流

```
外部输入
    ↓
【插件 A】创建 MessageBase
    ↓
【AmaidesuCore.send_to_maicore()】出站管道处理
    ↓
【WebSocket】发送到 MaiCore
    ↓
【MaiCore】AI 处理
    ↓
【WebSocket】接收响应
    ↓
【AmaidesuCore._handle_maicore_message()】入站管道处理
    ↓
【分发】根据 message_segment.type 分发给插件
    ↓
【插件 B/C/D】处理消息
```

**问题**：
- 所有消息都必须经过 MaiCore
- 分发逻辑在 `AmaidesuCore` 中，难以扩展
- 插件需要主动注册处理器

### 新架构数据流

```
外部输入
    ↓
【InputProvider】创建 NormalizedMessage
    ↓
【InputPipeline】过滤处理
    ↓
【EventBus.emit(CoreEvents.DATA_MESSAGE)】发布事件
    ↓
【DecisionProvider】订阅并处理
    ↓
【DecisionProvider】创建 Intent
    ↓
【EventBus.emit(CoreEvents.DECISION_INTENT)】发布事件
    ↓
【OutputPipeline】过滤处理
    ↓
【OutputProvider】订阅并渲染
```

**改进**：
- 单向数据流，易于追踪
- EventBus 解耦组件
- 本地决策 Provider 可以不依赖 MaiCore

## 扩展性对比

### 旧架构：添加新插件

1. 创建 `src/plugins/my_plugin/plugin.py`
2. 定义 `plugin_entrypoint`
3. 在 `config.toml` 中启用
4. 实现生命周期方法（`init()` / `start()` / `stop()` / `cleanup()`）注册处理器
5. 通过 `get_service()` 获取依赖

**问题**：
- 没有类型约束
- 依赖关系不明确
- 与其他插件的关系难以管理

### 新架构：添加新 Provider

1. 选择合适的 Domain（input/decision/output）
2. 继承对应的 Provider 基类
3. 在 `__init__.py` 中注册到 Registry
4. 在 `config.toml` 中启用

**改进**：
- 类型安全
- 依赖通过 `ProviderContext` 注入
- 职责边界清晰

## 测试对比

### 旧架构测试难点

```python
# 需要 mock 整个 AmaidesuCore
def test_my_plugin():
    mock_core = Mock()
    mock_core.event_bus = Mock()
    mock_core.get_service = Mock(return_value=mock_service)

    plugin = MyPlugin(mock_core, {})
    # 难以隔离测试
```

### 新架构测试优势

```python
# 可以轻松注入 ProviderContext
from src.modules.di import ProviderContext

def test_my_provider():
    mock_context = ProviderContext(
        event_bus=MockEventBus(),
        context_service=MockContextService(),
    )

    provider = MyProvider(config={}, context=mock_context)
    # 完全隔离测试
```

## 迁移建议

### 输入类插件迁移

```python
# 旧代码
class BiliDanmakuPlugin(BasePlugin):
    async def setup(self):
        self.core.register_websocket_handler("*", self.handler)

    async def handler(self, message):
        # 处理弹幕
        await self.core.send_to_maicore(message)

# 新代码
class BiliDanmakuProvider(InputProvider):
    async def start(self) -> AsyncIterator[NormalizedMessage]:
        async for danmaku in self._fetch_danmaku():
            yield NormalizedMessage(
                source=self.name,
                content=danmaku.text,
                metadata={"user": danmaku.user},
            )
```

### 输出类插件迁移

```python
# 旧代码
class TTSPlugin(BasePlugin):
    async def setup(self):
        self.core.register_websocket_handler("text", self.handler)

    async def handler(self, message: MessageBase):
        text = message.message_segment.data
        await self._speak(text)

# 新代码
class TTSProvider(OutputProvider):
    async def init(self):
        await self.event_bus.subscribe(
            CoreEvents.DECISION_INTENT,
            self._handle_intent
        )

    async def _handle_intent(self, intent: Intent):
        if intent.text:
            await self._speak(intent.text)
```

---

*最后更新：2026-02-15*
