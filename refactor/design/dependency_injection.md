# 依赖注入变化

本文档详细对比重构前后的依赖管理方式，解释新架构如何通过 `ProviderContext` 实现类型安全的依赖注入。

## 依赖管理总览

### 旧架构：服务注册模式

```
┌─────────────────────────────────────────────────────────────────┐
│                        AmaidesuCore                             │
│                                                                │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                     服务注册表                              │ │
│  │                  self._services: Dict                      │ │
│  │                                                            │ │
│  │   "prompt_context" → ContextManager                       │ │
│  │   "text_cleanup"   → TextCleanupService                   │ │
│  │   "vts_client"     → VTSClient                            │ │
│  └────────────────────────────────────────────────────────────┘ │
│                           ▲                                     │
│                           │ get_service()                       │
│                           │                                     │
│          ┌────────────────┴────────────────┐                   │
│          │                                 │                    │
│    ┌───────────┐                     ┌───────────┐             │
│    │  Plugin A │                     │  Plugin B │             │
│    └───────────┘                     └───────────┘             │
│                                                                │
└─────────────────────────────────────────────────────────────────┘
```

**特点**：
- 插件通过 `core.get_service()` 获取依赖
- 依赖关系在运行时发现
- 无类型安全

### 新架构：ProviderContext 依赖注入

```
┌─────────────────────────────────────────────────────────────────┐
│                         ProviderContext                         │
│                                                                 │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │              @dataclass(frozen=True)                       │ │
│  │                                                            │ │
│  │   event_bus: EventBus                                     │ │
│  │   config_service: ConfigService                           │ │
│  │   audio_stream_channel: AudioStreamChannel                │ │
│  │   llm_service: LLMManager                                 │ │
│  │   context_service: ContextService                         │ │
│  │   prompt_service: PromptManager                           │ │
│  └────────────────────────────────────────────────────────────┘ │
│                           │                                     │
│                           │ 构造时注入                          │
│                           ▼                                     │
│          ┌────────────────┴────────────────┐                   │
│          │                                 │                    │
│    ┌───────────┐                     ┌───────────┐             │
│    │ Provider A│                     │ Provider B│             │
│    │self.context.event_bus            │self.context.llm_service│
│    └───────────┘                     └───────────┘             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**特点**：
- 依赖在 Provider 构造时通过 `ProviderContext` 注入
- 依赖关系在初始化时确定
- 完整的类型安全（dataclass + 类型注解）
- 不可变（frozen=True）确保线程安全

## 服务注册 vs ProviderContext

### 旧架构：服务注册

```python
# amaidesu_core.py
class AmaidesuCore:
    def __init__(self, ...):
        self._services: Dict[str, Any] = {}

    def register_service(self, name: str, service_instance: Any):
        """注册服务"""
        self._services[name] = service_instance

    def get_service(self, name: str) -> Optional[Any]:
        """获取服务"""
        return self._services.get(name)

# main.py
core = AmaidesuCore(...)
core.register_service("prompt_context", context_manager)
core.register_service("text_cleanup", text_cleanup_service)

# plugin.py
class MyPlugin(BasePlugin):
    async def setup(self):
        # 运行时获取依赖
        self.context = self.core.get_service("prompt_context")
        if self.context is None:
            raise RuntimeError("服务未注册")
```

**问题**：
- 依赖在运行时发现，可能失败
- 无类型安全，返回 `Optional[Any]`
- 服务注册顺序敏感
- 难以追踪依赖关系

### 新架构：直接参数注入 + ProviderContext

新架构采用两层注入方式：
1. **ProviderManager 层**：通过构造函数参数直接注入
2. **Provider 层**：通过 `ProviderContext` 统一注入

## ProviderManager 层依赖注入

ProviderManager 作为 Domain 的协调器，通过**构造函数参数**直接接收所需的依赖。这是与 Provider 层的主要区别：Manager 不使用 ProviderContext，而是直接使用参数。

```python
# main.py - ProviderManager 层使用直接参数注入

# 输入域 Manager
input_provider_manager = InputProviderManager(
    event_bus=event_bus,
    pipeline_manager=input_pipeline_manager,
)

# 决策域 Manager
decision_provider_manager = DecisionProviderManager(
    event_bus, llm_service, config_service, context_service, prompt_manager
)

# 输出域 Manager
output_provider_manager = OutputProviderManager(
    event_bus, prompt_manager=prompt_manager
)
await output_provider_manager.setup(
    output_config,
    config_service=config_service,
    root_config=config,
    audio_stream_channel=audio_stream_channel,
)
```

**Manager 层注入特点**：
- 直接通过构造函数参数传递依赖
- 每个参数类型明确，IDE 支持好
- 不同 Manager 接收不同的依赖组合

**Manager 与 Provider 层的区别**：

| 层级 | 注入方式 | 原因 |
|------|----------|------|
| **Manager 层** | 构造函数参数 | Manager 数量固定，依赖因域而异 |
| **Provider 层** | ProviderContext | Provider 数量可扩展，需要统一的依赖接口 |

## Provider 层依赖注入

Provider 层通过 `ProviderContext` 统一接收依赖。Manager 在创建 Provider 时，负责构建 ProviderContext 并传递给 Provider。

```python
# modules/di/context.py - ProviderContext 定义
@dataclass(frozen=True)
class ProviderContext:
    """所有 Provider 的统一依赖上下文（不可变）"""
    event_bus: Optional["EventBus"] = None
    config_service: Optional["ConfigService"] = None
    audio_stream_channel: Optional["AudioStreamChannel"] = None
    llm_service: Optional["LLMManager"] = None
    context_service: Optional["ContextService"] = None
    prompt_service: Optional["PromptManager"] = None

# modules/types/base/input_provider.py
class InputProvider(ABC):
    def __init__(self, config: dict, context: ProviderContext = None):
        self.config = config
        self.context = context or ProviderContext()

    @property
    def event_bus(self):
        """获取事件总线"""
        return self.context.event_bus
```

**改进**：
- ProviderManager 层使用直接参数注入，类型明确
- Provider 层使用 ProviderContext 统一访问
- 依赖关系在构造时确定
- 易于测试

## 依赖获取方式对比

### 旧架构：通过 Core 获取

```python
class MyPlugin(BasePlugin):
    def __init__(self, core, plugin_config):
        self.core = core
        self.plugin_config = plugin_config

    async def setup(self):
        # 方式 1：直接访问 core 属性
        if self.core.event_bus:
            self.event_bus = self.core.event_bus

        # 方式 2：通过 get_service
        self.context = self.core.get_service("prompt_context")

        # 方式 3：通过 get_llm_client
        self.llm = self.core.get_llm_client("llm")
```

**问题**：
- 多种获取方式混乱
- 可选依赖需要检查 None
- 紧耦合到 AmaidesuCore

### 新架构：统一通过 ProviderContext 获取

```python
class MyProvider(OutputProvider):
    def __init__(self, config: dict, context: ProviderContext):
        self.config = config
        self.context = context

    async def init(self):
        # 统一通过 context 访问
        if self.context.event_bus:
            await self.context.event_bus.subscribe(...)

        if self.context.llm_service:
            client = self.context.llm_service.get_client("llm")
```

**改进**：
- 统一的获取方式
- 依赖在构造时确定
- 通过 dataclass 属性访问，IDE 支持好

## 可用依赖对比

### 旧架构可用依赖

| 依赖 | 获取方式 | 类型 |
|------|----------|------|
| EventBus | `self.core.event_bus` | `Optional[EventBus]` |
| ContextManager | `self.core.get_service("prompt_context")` | `Optional[ContextManager]` |
| AvatarManager | `self.core.avatar` | `Optional[AvatarControlManager]` |
| LLMClient | `self.core.get_llm_client()` | `LLMClient` |
| 任意服务 | `self.core.get_service(name)` | `Optional[Any]` |

### 新架构可用依赖（ProviderContext）

| 属性名 | 类型 | 说明 |
|--------|------|------|
| `event_bus` | `Optional[EventBus]` | 事件总线 |
| `config_service` | `Optional[ConfigService]` | 配置服务 |
| `audio_stream_channel` | `Optional[AudioStreamChannel]` | 音频流通道 |
| `audio_device_service` | `Optional[AudioDeviceManager]` | 音频设备管理 |
| `llm_service` | `Optional[LLMManager]` | LLM 服务 |
| `token_usage_service` | `Optional[TokenUsageManager]` | Token 用量统计 |
| `prompt_service` | `Optional[PromptManager]` | 提示词管理 |
| `context_service` | `Optional[ContextService]` | 上下文服务 |

## 类型安全对比

### 旧架构：无类型安全

```python
# 返回 Any，无法在编译时检查
service = self.core.get_service("prompt_context")
# service 可能是任何类型，也可能不存在

# 需要手动类型检查
if service is None:
    raise RuntimeError("服务未注册")
context: ContextManager = service  # 类型断言，可能错误
```

### 新架构：完整类型安全

```python
# ProviderContext 是 dataclass，属性类型明确
class MyProvider(InputProvider):
    async def init(self):
        # IDE 自动补全，编译时类型检查
        if self.context.event_bus:
            await self.context.event_bus.emit(...)

        if self.context.llm_service:
            client = self.context.llm_service.get_client("llm")
```

## 测试对比

### 旧架构测试

```python
# 需要 mock 整个 AmaidesuCore
def test_my_plugin():
    mock_core = Mock(spec=AmaidesuCore)
    mock_core.event_bus = Mock(spec=EventBus)
    mock_core.get_service = Mock(return_value=MockContextManager())

    plugin = MyPlugin(mock_core, {})
    # 难以控制依赖行为
```

### 新架构测试

```python
# 轻松创建 mock ProviderContext
def test_my_provider():
    mock_context = ProviderContext(
        event_bus=MockEventBus(),
        context_service=MockContextService(),
    )

    provider = MyProvider(config={}, context=mock_context)
    # 完全控制依赖行为
```

## 依赖链对比

### 旧架构：复杂的依赖链

```
Plugin A
    ↓ get_service("text_cleanup")
TextCleanupService (另一个 Plugin)
    ↓ core.get_service("prompt_context")
ContextManager
    ↓ core.get_llm_client()
LLMClient
```

**问题**：
- 依赖链深
- 难以追踪
- 循环依赖风险

### 新架构：扁平的 ProviderContext

```
Provider A
    ↓ self.context.llm_service
LLMManager (直接注入)
```

**改进**：
- 依赖扁平
- 易于追踪
- 无循环依赖

## 配置与依赖分离

### 旧架构：配置与依赖混合

```python
class MyPlugin(BasePlugin):
    def __init__(self, core, plugin_config):
        self.core = core
        # 配置中可能包含服务引用
        self.service_name = plugin_config.get("service_name")
```

### 新架构：配置与依赖分离

```python
class MyProvider(InputProvider):
    def __init__(self, config: dict, context: ProviderContext):
        self.config = config      # 配置（来自 TOML）
        self.context = context    # 依赖（来自 ProviderContext）
```

**改进**：
- 关注点分离
- 配置可序列化
- 依赖不可序列化

## ProviderContext 便捷属性

Provider 基类提供了便捷属性访问：

```python
class InputProvider(ABC):
    def __init__(self, config: dict, context: ProviderContext = None):
        self.config = config
        self.context = context or ProviderContext()

    @property
    def event_bus(self):
        """获取事件总线"""
        return self.context.event_bus
```

这样 Provider 可以直接使用 `self.event_bus` 而不需要 `self.context.event_bus`。

## 迁移建议

### 服务注册迁移

```python
# 旧代码
# main.py
core.register_service("text_cleanup", text_cleanup_service)

# plugin.py
self.text_cleanup = self.core.get_service("text_cleanup")

# 新代码
# 如果是通用服务，添加到 ProviderContext
# 1. 在 modules/di/context.py 中添加字段
# 2. 在 main.py 中创建 ProviderContext 时注入

# context.py
@dataclass(frozen=True)
class ProviderContext:
    # ... 现有字段 ...
    text_cleanup_service: Optional["TextCleanupService"] = None

# main.py
context = ProviderContext(
    event_bus=event_bus,
    text_cleanup_service=text_cleanup_service,
)

# provider.py
if self.context.text_cleanup_service:
    self.context.text_cleanup_service.cleanup(text)
```

### Core 属性迁移

```python
# 旧代码
self.event_bus = self.core.event_bus
self.llm = self.core.get_llm_client()

# 新代码
# 直接使用 context 属性
await self.context.event_bus.emit(...)
client = self.context.llm_service.get_client("llm")

# 或使用基类提供的便捷属性
await self.event_bus.emit(...)
```

### 添加新依赖

如果需要添加新的共享依赖：

1. 在 `ProviderContext` 中添加字段：

```python
# modules/di/context.py
@dataclass(frozen=True)
class ProviderContext:
    # ... 现有字段 ...
    my_new_service: Optional["MyNewService"] = None
```

2. 在 `main.py` 中创建 ProviderContext 时注入：

```python
context = ProviderContext(
    event_bus=event_bus,
    my_new_service=my_new_service,
)
```

3. 在 Provider 中使用：

```python
class MyProvider(InputProvider):
    async def init(self):
        if self.context.my_new_service:
            self.context.my_new_service.do_something()
```

---

*最后更新：2026-02-15*
