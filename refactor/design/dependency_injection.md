# 依赖注入变化

本文档详细对比重构前后的依赖管理方式，解释新架构如何通过**构造函数注入**实现类型安全的依赖管理。

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

### 新架构：构造函数注入

```
┌─────────────────────────────────────────────────────────────────┐
│                    依赖注入容器                                  │
│                                                                 │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │              构造函数参数直接传递                            │ │
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
│    │Collector A│                     │Handler B  │             │
│    │self._event_bus                  │self._llm_service│
│    └───────────┘                     └───────────┘             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**特点**：
- 依赖在组件构造时通过构造函数参数注入
- 依赖关系在初始化时确定
- 完整的类型安全（类型注解）
- 组件直接接收所需依赖，无需通过 Context 对象

## 服务注册 vs 构造函数注入

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

### 新架构：构造函数注入

新架构采用构造函数注入：
- **Manager 层**：通过构造函数参数直接注入
- **阶段参与者层**：通过构造函数参数直接注入

## Manager 层依赖注入

Manager 作为阶段的协调器，通过**构造函数参数**直接接收所需的依赖。

```python
# main.py - Manager 层使用直接参数注入

# 输入阶段 Manager
input_collector_manager = InputCollectorManager(
    event_bus=event_bus,
    pipeline_manager=input_pipeline_manager,
)

# 决策阶段 Manager
decider_manager = DeciderManager(
    event_bus, llm_service, config_service, context_service, prompt_manager
)

# 输出阶段 Manager
output_handler_manager = OutputHandlerManager(
    event_bus, prompt_manager=prompt_manager
)
await output_handler_manager.setup(
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

## 阶段参与者层依赖注入

阶段参与者通过**构造函数参数**直接接收依赖。Manager 在创建阶段参与者时，直接传递所需依赖。

```python
# InputCollector 基类
class InputCollector(ABC):
    def __init__(
        self,
        config: dict,
        event_bus: Optional["EventBus"] = None,
        pipeline_manager: Optional["PipelineManager"] = None,
    ):
        self.config = config
        self._event_bus = event_bus
        self._pipeline_manager = pipeline_manager

    @property
    def event_bus(self):
        """获取事件总线"""
        return self._event_bus
```

**改进**：
- Manager 层使用直接参数注入，类型明确
- 阶段参与者层使用构造函数参数直接接收
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

### 新架构：构造函数参数直接获取

```python
class MyHandler(OutputHandler):
    def __init__(self, config: dict, event_bus: EventBus, llm_service: LLMManager = None):
        self.config = config
        self._event_bus = event_bus
        self._llm_service = llm_service

    async def init(self):
        # 直接使用注入的依赖
        if self._event_bus:
            await self._event_bus.subscribe(...)

        if self._llm_service:
            client = self._llm_service.get_client("llm")
```

**改进**：
- 依赖通过构造函数直接获取
- 依赖在构造时确定
- IDE 完全支持类型推断和自动补全

## 可用依赖对比

### 旧架构可用依赖

| 依赖 | 获取方式 | 类型 |
|------|----------|------|
| EventBus | `self.core.event_bus` | `Optional[EventBus]` |
| ContextManager | `self.core.get_service("prompt_context")` | `Optional[ContextManager]` |
| AvatarManager | `self.core.avatar` | `Optional[AvatarControlManager]` |
| LLMClient | `self.core.get_llm_client()` | `LLMClient` |
| 任意服务 | `self.core.get_service(name)` | `Optional[Any]` |

### 新架构可用依赖（构造函数注入）

| 属性名 | 类型 | 说明 |
|--------|------|------|
| `event_bus` | `EventBus` | 事件总线 |
| `config_service` | `ConfigService` | 配置服务 |
| `audio_stream_channel` | `AudioStreamChannel` | 音频流通道 |
| `audio_device_service` | `AudioDeviceManager` | 音频设备管理 |
| `llm_service` | `LLMManager` | LLM 服务 |
| `token_usage_service` | `TokenUsageManager` | Token 用量统计 |
| `prompt_service` | `PromptManager` | 提示词管理 |
| `context_service` | `ContextService` | 上下文服务 |

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
# 构造函数参数类型明确
class MyCollector(InputCollector):
    def __init__(self, config: dict, event_bus: EventBus, llm_service: LLMManager = None):
        self.config = config
        self._event_bus = event_bus
        self._llm_service = llm_service

    async def init(self):
        # IDE 自动补全，编译时类型检查
        if self._event_bus:
            await self._event_bus.emit(...)

        if self._llm_service:
            client = self._llm_service.get_client("llm")
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
# 直接传递 mock 依赖
def test_my_collector():
    mock_event_bus = MockEventBus()
    mock_context_service = MockContextService()

    collector = MyCollector(
        config={},
        event_bus=mock_event_bus,
        context_service=mock_context_service,
    )
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

### 新架构：扁平的依赖注入

```
Collector A
    ↓ 直接注入
LLMManager
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
class MyCollector(InputCollector):
    def __init__(self, config: dict, event_bus: EventBus, context_service: ContextService = None):
        self.config = config      # 配置（来自 TOML）
        self._event_bus = event_bus  # 依赖（直接注入）
        self._context_service = context_service
```

**改进**：
- 关注点分离
- 配置可序列化
- 依赖不可序列化

## 构造函数注入便捷属性

阶段参与者基类提供了便捷属性访问：

```python
class InputCollector(ABC):
    def __init__(self, config: dict, event_bus: EventBus = None):
        self.config = config
        self._event_bus = event_bus

    @property
    def event_bus(self):
        """获取事件总线"""
        return self._event_bus
```

这样 Collector 可以直接使用 `self.event_bus`。

## 迁移建议

### 服务注册迁移

```python
# 旧代码
# main.py
core.register_service("text_cleanup", text_cleanup_service)

# plugin.py
self.text_cleanup = self.core.get_service("text_cleanup")

# 新代码
# 1. 在 main.py 中创建依赖
text_cleanup_service = TextCleanupService()

# 2. 在创建阶段参与者时注入
collector = MyCollector(
    config=config,
    event_bus=event_bus,
    text_cleanup_service=text_cleanup_service,
)

# collector.py
if self._text_cleanup_service:
    self._text_cleanup_service.cleanup(text)
```

### Core 属性迁移

```python
# 旧代码
self.event_bus = self.core.event_bus
self.llm = self.core.get_llm_client()

# 新代码
# 直接使用构造函数参数
await self._event_bus.emit(...)
client = self._llm_service.get_client("llm")

# 或使用基类提供的便捷属性
await self.event_bus.emit(...)
```

### 添加新依赖

如果需要添加新的共享依赖：

1. 在阶段参与者构造函数中添加参数：

```python
class MyCollector(InputCollector):
    def __init__(self, config: dict, event_bus: EventBus, my_new_service: MyNewService = None):
        super().__init__(config, event_bus)
        self._my_new_service = my_new_service
```

2. 在 main.py 中创建阶段参与者时注入：

```python
collector = MyCollector(
    config=config,
    event_bus=event_bus,
    my_new_service=my_new_service,
)
```

3. 在阶段参与者中使用：

```python
class MyCollector(InputCollector):
    async def init(self):
        if self._my_new_service:
            self._my_new_service.do_something()
```

---

*最后更新：2026-02-15*
