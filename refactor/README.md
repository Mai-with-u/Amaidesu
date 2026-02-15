# Amaidesu 重构文档

本目录包含 Amaidesu 项目的重构设计文档，旨在帮助了解旧架构的开发者快速理解新架构的变化和优势。

## 目录

- [为何重构](#为何重构)
- [原本的架构](#原本的架构)
- [新的架构](#新的架构)
- [核心变化](#核心变化)
- [详细设计文档](#详细设计文档)

## 为何重构

旧架构在实际开发和维护过程中暴露了以下问题：

### 1. 过度插件化

旧架构将几乎所有功能都作为插件实现，包括核心功能（如 WebUI 后端、LLM 客户端）。这导致了以下问题：

- **概念混淆**：如果某个"插件"对所有用户都是必需的，那它就不符合"可拔插"的定义，应该称为模块
- **权限过高**：插件被赋予了过高的权限，核心功能与可选插件混在一起
- **复杂度爆炸**：24 个插件之间存在复杂的依赖关系，难以理解和维护

### 2. 服务注册机制的复杂性

旧架构使用 `register_service()` / `get_service()` 的服务注册机制：

```python
# 旧架构：插件通过服务注册发现依赖
self.core.register_service("text_cleanup", service_instance)
service = self.core.get_service("text_cleanup")
```

这种方式导致：
- **隐式依赖**：依赖关系在运行时才能发现，而非编译时
- **依赖链复杂**：18 个插件使用服务注册，形成复杂的依赖网络
- **难以追踪**：服务调用链难以追踪，调试困难

### 3. 插件间依赖问题

旧架构中插件之间存在相互依赖：

- **稳定性差**：功能互相依赖一直在变，插件排列组合难以保证稳定
- **难以解耦**：插件之间的互相依赖不可避免，最终变成"石山"代码
- **需要额外工具**：需要开发依赖解决器、依赖下载器等工具

### 4. 消息流不清晰

旧架构的消息流动路径：

```
插件 -> AmaidesuCore -> MaiCore -> AmaidesuCore -> 插件
```

这种设计：
- 所有消息都通过 MaiCore 中转，即使不需要 AI 处理的消息
- 插件需要注册 `websocket_handler` 来接收消息，分发逻辑分散
- 难以追踪消息的处理流程

### 5. 配置管理分散

旧架构的配置系统：
- 全局配置和插件级配置混在一起
- 配置合并逻辑复杂
- 难以进行配置验证

## 原本的架构

### 目录结构

```
Amaidesu-dev/
├── main.py                      # 应用入口
├── config-template.toml          # 配置模板
├── src/
│   ├── core/
│   │   ├── amaidesu_core.py     # 核心协调器
│   │   ├── plugin_manager.py    # 插件管理器
│   │   ├── event_bus.py        # 事件总线
│   │   ├── pipeline_manager.py  # 管道管理器
│   │   └── context_manager.py   # 上下文管理器
│   ├── plugins/                 # 24 个插件
│   │   ├── bili_danmaku/
│   │   ├── bili_danmaku_official/
│   │   ├── console_input/
│   │   ├── tts/
│   │   ├── maicraft/
│   │   ├── vts/
│   │   └── ...
│   └── pipelines/               # 消息处理管道
│       ├── command_processor/
│       ├── command_router/
│       ├── similar_message_filter/
│       └── throttle/
└── tests/
```

### 核心组件

| 组件 | 职责 |
|------|------|
| **AmaidesuCore** | WebSocket 通信、消息分发、服务注册 |
| **PluginManager** | 插件加载、生命周期管理 |
| **EventBus** | 事件发布-订阅（可选） |
| **PipelineManager** | 入站/出站消息管道处理 |

### 插件系统

旧架构使用 `plugin_entrypoint` 入口点加载插件：

```python
# plugin.py
class MyPlugin(BasePlugin):
    def __init__(self, core, plugin_config):
        self.core = core
        self.plugin_config = plugin_config

    async def setup(self):
        self.core.register_websocket_handler("*", self.handler)

    async def handler(self, message: MessageBase):
        await self.core.send_to_maicore(message)

plugin_entrypoint = MyPlugin
```

### 数据流

```
外部输入（B站弹幕/控制台/语音）
    ↓
【插件】发送 MessageBase → AmaidesuCore.send_to_maicore()
    ↓
【PipelineManager】出站管道处理
    ↓
【WebSocket】发送到 MaiCore
    ↓
【MaiCore】AI 决策
    ↓
【WebSocket】接收响应 → AmaidesuCore._handle_maicore_message()
    ↓
【PipelineManager】入站管道处理
    ↓
【插件】分发给处理器 → TTS/VTS/动作执行
```

## 新的架构

### 目录结构

```
Amaidesu/
├── main.py                      # CLI 入口
├── config-template.toml         # 配置模板
├── src/
│   ├── domains/                 # 业务域（3域架构）
│   │   ├── input/               # 输入域
│   │   │   ├── provider_manager.py
│   │   │   ├── pipelines/       # 输入管道
│   │   │   └── providers/       # 输入 Provider
│   │   ├── decision/            # 决策域
│   │   │   ├── provider_manager.py
│   │   │   └── providers/       # 决策 Provider
│   │   └── output/              # 输出域
│   │       ├── provider_manager.py
│   │       ├── pipelines/       # 输出管道
│   │       └── providers/       # 输出 Provider
│   └── modules/                 # 核心模块（共享基础设施）
│       ├── config/             # 配置管理
│       ├── context/            # 上下文服务
│       ├── di/                 # 依赖注入
│       ├── events/             # 事件系统
│       ├── llm/                # LLM 服务
│       ├── logging/            # 日志系统
│       ├── prompts/            # 提示词管理
│       ├── registry/          # Provider 注册表
│       ├── streaming/          # 音频流通道
│       └── types/              # 共享类型
└── docs/                       # 项目文档
```

### 核心组件

| 组件 | 职责 |
|------|------|
| **InputProviderManager** | 管理输入 Provider 生命周期 |
| **DecisionProviderManager** | 管理决策 Provider（单一活跃） |
| **OutputProviderManager** | 管理输出 Provider 生命周期 |
| **EventBus** | 唯一的跨域通信机制 |
| **AudioStreamChannel** | 音频数据流传输通道 |

### Provider 系统

新架构使用 Provider 替代插件：

```python
# Provider 示例
class ConsoleInputProvider(InputProvider):
    async def start(self) -> AsyncIterator[NormalizedMessage]:
        while True:
            text = await self._read_input()
            yield NormalizedMessage(
                source=self.name,
                content=text,
                metadata={},
            )
```

### 数据流

```
外部输入（弹幕、游戏、语音）
        ↓
【Input Domain】InputProvider → NormalizedMessage → Pipeline 过滤
        ↓ EventBus: data.message
【Decision Domain】DecisionProvider → Intent
        ↓ EventBus: decision.intent
【Output Domain】OutputProviderManager → OutputPipeline → OutputProviders
```

## 核心变化

### 1. 从插件到 Provider

| 方面 | 旧架构（插件） | 新架构（Provider） |
|------|---------------|-------------------|
| **定位** | 所有功能都是插件 | 核心功能是 Provider，可选功能是扩展 |
| **加载** | 动态导入 `plugin_entrypoint` | 注册表 + 类型安全 |
| **生命周期** | `setup()` / `cleanup()` | `init()` / `start()` / `stop()` / `cleanup()`（所有 Provider 类型统一） |
| **依赖** | 服务注册 | 依赖注入 |

### 2. 从服务注册到依赖注入

| 方面 | 旧架构 | 新架构 |
|------|--------|--------|
| **机制** | `register_service()` / `get_service()` | `ProviderContext` 依赖注入 |
| **依赖发现** | 运行时 | 初始化时 |
| **类型安全** | 无 | 完整类型注解 |

**注入链路**：
```
main.py 创建 ProviderContext
    ↓
ProviderManager 接收 context
    ↓
创建 Provider 时注入: provider_class(config=config, context=context)
    ↓
Provider 通过 self.context.xxx 访问依赖
```

### 3. 从集中式到 3 域架构

| 方面 | 旧架构 | 新架构 |
|------|--------|--------|
| **组织** | 扁平的插件列表 | 按职责分域 |
| **通信** | 通过 AmaidesuCore 中转 | EventBus 直接通信 |
| **数据流** | 插件 → Core → MaiCore → Core → 插件 | Input → Decision → Output |

### 4. 从可选到强制的 EventBus

| 方面 | 旧架构 | 新架构 |
|------|--------|--------|
| **EventBus** | 可选功能 | 唯一跨域通信机制 |
| **事件常量** | 字符串硬编码 | `CoreEvents` 枚举 |
| **类型安全** | 无 | 事件类型与数据类型绑定 |

## 详细设计文档

| 文档 | 说明 |
|------|------|
| [architecture_comparison.md](design/architecture_comparison.md) | 架构对比详解 |
| [data_flow.md](design/data_flow.md) | 数据流变化 |
| [event_system.md](design/event_system.md) | 事件系统变化 |
| [dependency_injection.md](design/dependency_injection.md) | 依赖注入变化 |
| [config_system.md](design/config_system.md) | 配置系统变化 |
| [core_modules.md](design/core_modules.md) | 核心模块变化（Prompts/Context/Logging） |

## 迁移指南

如果你有基于旧架构开发的插件，请参考以下迁移路径：

1. **Input 插件** → 继承 `InputProvider`，实现 `start()` 方法
2. **Output 插件** → 继承 `OutputProvider`，订阅 `decision.intent` 事件
3. **Service 插件** → 转换为 `modules/` 中的共享模块或作为独立的 Provider
4. **Decision 插件** → 继承 `DecisionProvider`，订阅 `data.message` 事件

---

*最后更新：2026-02-15*
