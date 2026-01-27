# Plugin迁移指南

本文档指导开发者如何将现有的`BasePlugin`迁移到新的`Plugin`接口。

## 目录

- [概述](#概述)
- [新Plugin接口说明](#新plugin接口说明)
- [Provider接口说明](#provider接口说明)
- [从BasePlugin迁移步骤](#从baseplugin迁移步骤)
- [完整示例](#完整示例)
- [常见问题](#常见问题)

---

## 概述

### 为什么迁移？

新的Plugin系统具有以下优势：

1. **解耦设计**：Plugin不再继承`AmaidesuCore`，通过依赖注入获取所需组件
2. **更清晰的职责**：Plugin专注于Provider聚合，Provider专注于具体功能
3. **更好的可测试性**：更容易进行单元测试，无需mock整个AmaidesuCore
4. **更好的复用性**：Provider可以在不同Plugin间复用
5. **标准化接口**：统一的Plugin和Provider接口，降低学习成本

### 系统对比

| 特性 | BasePlugin（旧系统） | Plugin（新系统） |
|------|-------------------|------------------|
| 继承关系 | 继承`AmaidesuCore` | 不继承基类（Protocol） |
| 依赖方式 | `self.core.xxx` | `event_bus` + `config`依赖注入 |
| 职责范围 | 直接实现功能 | 聚合Provider，管理Provider生命周期 |
| 通信方式 | EventBus + 直接方法调用 | 仅使用EventBus |
| 测试难度 | 较难（需要mock AmaidesuCore） | 较易（只需mock EventBus） |
| 插件位置 | `src/plugins/xxx/` | `src/plugins/xxx/`（兼容） |

---

## 新Plugin接口说明

### Plugin接口

Plugin是一个**协议（Protocol）**，不是基类。任何实现了`Plugin`协议的类都可以作为Plugin使用。

```python
from typing import Protocol, List, Dict, Any

class Plugin(Protocol):
    """Plugin协议 - 聚合多个Provider"""

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> List[Any]:
        """
        初始化插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置（从config.toml中读取）

        Returns:
            Provider列表: 插件管理的所有Provider实例
        """
        ...

    async def cleanup(self):
        """清理资源"""
        ...

    def get_info(self) -> Dict[str, Any]:
        """
        获取插件信息

        Returns:
            dict: 插件信息
        """
        ...
```

### Plugin生命周期

```
1. 实例化(__init__)
   └─ 接收config参数
2. 设置(setup())
   ├─ 创建Provider实例
   ├─ 调用provider.setup()
   └─ 返回Provider列表
3. 运行期间
   └─ Provider通过EventBus通信
4. 清理(cleanup())
   └─ 调用所有provider.cleanup()
```

---

## Provider接口说明

Provider是**具体功能实现单元**，分为以下几种类型：

### 1. InputProvider（输入Provider）

```python
from typing import AsyncIterator

class InputProvider(ABC):
    """输入Provider抽象基类"""

    def __init__(self, config: dict):
        self.config = config
        self.is_running = False

    async def start(self) -> AsyncIterator[RawData]:
        """
        启动Provider并返回数据流

        Yields:
            RawData: 原始数据
        """
        self.is_running = True
        try:
            async for data in self._collect_data():
                yield data
        finally:
            self.is_running = False

    async def stop(self):
        """停止Provider"""
        self.is_running = False
        await self._cleanup()

    @abstractmethod
    async def _collect_data(self) -> AsyncIterator[RawData]:
        """采集数据（子类必须实现）"""
        pass
```

**适用场景**：
- 从外部系统采集数据（弹幕、控制台输入、语音等）
- 任何需要持续产生RawData的场合

**示例**：
```python
class BilibiliDanmakuInputProvider(InputProvider):
    """B站弹幕输入Provider"""

    async def _collect_data(self) -> AsyncIterator[RawData]:
        # 连接到B站弹幕API
        async for danmaku in self._connect_danmaku():
            yield RawData(
                content=danmaku.text,
                source="bilibili",
                data_type="text",
                metadata={"user": danmaku.user}
            )
```

### 2. OutputProvider（输出Provider）

```python
class OutputProvider(ABC):
    """输出Provider抽象基类"""

    def __init__(self, config: dict):
        self.config = config

    async def render(self, params: RenderParameters):
        """
        渲染输出

        Args:
            params: 渲染参数
        """
        await self._render_internal(params)

    @abstractmethod
    async def _render_internal(self, params: RenderParameters):
        """内部渲染逻辑（子类必须实现）"""
        pass
```

**适用场景**：
- 将CanonicalMessage渲染到外部系统（VTS、字幕、TTS等）
- 任何需要接收RenderParameters的场合

**示例**：
```python
class VTSOutputProvider(OutputProvider):
    """VTubeStudio输出Provider"""

    async def _render_internal(self, params: RenderParameters):
        if params.render_type == "expression":
            await self._send_expression(params.content)
        elif params.render_type == "text":
            await self._send_text(params.content)
```

### 3. DecisionProvider（决策Provider）

```python
class DecisionProvider(ABC):
    """决策Provider抽象基类"""

    def __init__(self, config: dict, event_bus: Optional = None):
        self.config = config
        self.event_bus = event_bus
        self.is_setup = False

    async def setup(self, event_bus, config: dict):
        """设置Provider"""
        self.event_bus = event_bus
        self.config = config
        await self._setup_internal()
        self.is_setup = True

    @abstractmethod
    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        """
        决策

        Args:
            canonical_message: 标准消息

        Returns:
            MessageBase: 决策结果
        """
        pass
```

**适用场景**：
- 将CanonicalMessage转换为决策结果（MaiCore、本地LLM、规则引擎等）
- 任何需要处理CanonicalMessage的场合

**示例**：
```python
class MaiCoreDecisionProvider(DecisionProvider):
    """MaiCore决策Provider"""

    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        # 发送到MaiCore并获取响应
        response = await self.maicore.send_message(canonical_message.text)
        return response
```

---

## 从BasePlugin迁移步骤

### 步骤1：分析现有Plugin

首先，分析你的BasePlugin类，确定它的职责：

```python
# 旧BasePlugin示例
class MyPlugin(BasePlugin):
    async def setup(self):
        # 注册消息处理器
        await self.core.register_websocket_handler("text", self.handle_message)
        # 注册为服务
        self.core.register_service("my_service", self)

    async def handle_message(self, message: MessageBase):
        # 处理消息
        response = await self._process(message)
        await self.core.send_to_maicore(response)
```

**问题清单**：
1. Plugin是做什么的？（输入、输出、决策、处理？）
2. 它监听了哪些事件？
3. 它发布了哪些事件？
4. 它提供了哪些服务？
5. 它依赖哪些外部服务？

### 步骤2：确定Provider类型

根据Plugin的职责，确定需要哪些Provider：

| Plugin职责 | Provider类型 | 示例 |
|-----------|------------|------|
| 从外部采集数据 | InputProvider | BilibiliDanmakuInputProvider |
| 渲染输出到外部系统 | OutputProvider | VTSOutputProvider |
| 处理CanonicalMessage | DecisionProvider | MaiCoreDecisionProvider |
| 数据转换/处理 | ProcessingProvider（自定义） | TextCleanupProvider |

### 步骤3：创建Provider类

将Plugin的功能拆分到Provider中：

```python
# 旧代码在Plugin中
async def handle_message(self, message: MessageBase):
    response = await self._process(message)
    await self.core.send_to_maicore(response)

# 新代码：创建DecisionProvider
class MyDecisionProvider(DecisionProvider):
    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        # 处理逻辑
        response = await self._process(canonical_message)
        return response
```

### 步骤4：重写Plugin类

创建新的Plugin类，使用Provider：

```python
# 旧的BasePlugin
class MyPlugin(BasePlugin):
    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        # 初始化逻辑

    async def setup(self):
        # 设置逻辑
        pass

    async def cleanup(self):
        # 清理逻辑
        pass


# 新的Plugin
class MyPlugin:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._providers = []

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> List[Any]:
        """初始化插件"""
        # 创建Provider
        decision_provider = MyDecisionProvider(config.get("decision", {}))
        await decision_provider.setup(event_bus, config)
        self._providers.append(decision_provider)

        return self._providers

    async def cleanup(self):
        """清理资源"""
        for provider in self._providers:
            await provider.cleanup()
        self._providers.clear()

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "MyPlugin",
            "version": "1.0.0",
            "author": "Your Name",
            "description": "My plugin description",
            "category": "processing",
            "api_version": "1.0",
        }
```

### 步骤5：更新配置

旧配置格式：
```toml
[plugins.my_plugin]
enable_my_plugin = true
api_url = "https://example.com/api"
```

新配置格式：
```toml
[plugins.my_plugin]
enabled = true
priority = 100

[plugins.my_plugin.decision]
api_url = "https://example.com/api"
timeout = 30
```

### 步骤6：迁移事件处理

**旧方式**：直接注册处理器
```python
async def setup(self):
    await self.core.register_websocket_handler("text", self.handle_message)
```

**新方式**：通过EventBus
```python
async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> List[Any]:
    # 创建Provider并监听事件
    provider = MyInputProvider(config.get("input", {}))

    async def on_raw_data(event_name, data, source):
        # 处理原始数据
        await self._process_raw_data(data)

    event_bus.on("perception.raw_data.generated", on_raw_data)

    return [provider]
```

### 步骤7：迁移服务注册

**旧方式**：注册为服务
```python
async def setup(self):
    self.core.register_service("my_service", self)
```

**新方式**：通过EventBus通信
```python
# 在Provider中发布事件
await event_bus.emit("custom.event", data, "MyInputProvider")

# 在其他Provider中订阅事件
event_bus.on("custom.event", self.handle_custom_event)
```

### 步骤8：测试

使用新的测试工具进行测试：

```python
import pytest
from tests.test_plugin_utils import PluginTestBase, MockEventBus

class TestMyPlugin(PluginTestBase):
    @pytest.mark.asyncio
    async def test_plugin_setup(self, mock_event_bus):
        plugin = MyPlugin({"enabled": True})
        providers = await plugin.setup(mock_event_bus, plugin.config)

        assert len(providers) > 0
        await plugin.cleanup()
```

---

## 完整示例

### 旧的BasePlugin实现

```python
# src/plugins/my_plugin/plugin.py
from src.core.plugin_manager import BasePlugin
from src.core.amaidesu_core import AmaidesuCore
from typing import Dict, Any
from maim_message import MessageBase

class MyPlugin(BasePlugin):
    """我的插件（旧系统）"""

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        self.api_url = self.plugin_config.get("api_url")
        self.timeout = self.plugin_config.get("timeout", 30)

    async def setup(self):
        # 注册消息处理器
        await self.core.register_websocket_handler("text", self.handle_message)

        # 注册为服务
        self.core.register_service("my_service", self)

        # 监听事件
        self.listen_event("custom.event", self.handle_custom_event)

        self.logger.info("MyPlugin setup完成")

    async def handle_message(self, message: MessageBase):
        """处理消息"""
        text = message.message_segment.data

        # 处理逻辑
        response = await self._process_text(text)

        # 发送回MaiCore
        await self.core.send_to_maicore(response)

    async def handle_custom_event(self, event_name, data, source):
        """处理自定义事件"""
        self.logger.info(f"收到事件: {event_name}, 数据: {data}")

    async def _process_text(self, text: str) -> MessageBase:
        """处理文本"""
        # 实际处理逻辑
        return MessageBase.create(text=text)

    async def cleanup(self):
        # 取消事件监听
        self.stop_listening_event("custom.event", self.handle_custom_event)

        await super().cleanup()
```

### 新的Plugin实现

```python
# src/plugins/my_plugin/plugin.py
from src.core.plugin import Plugin
from src.core.providers.decision_provider import DecisionProvider
from src.core.event_bus import EventBus
from typing import Dict, Any, List, Optional

if False:  # TYPE_CHECKING
    from src.canonical.canonical_message import CanonicalMessage


# 1. 创建DecisionProvider
class MyDecisionProvider(DecisionProvider):
    """我的决策Provider"""

    async def _setup_internal(self):
        """内部设置"""
        self.api_url = self.config.get("api_url")
        self.timeout = self.config.get("timeout", 30)
        self.logger.info(f"MyDecisionProvider初始化: api_url={self.api_url}")

    async def decide(self, canonical_message: "CanonicalMessage") -> MessageBase:
        """决策"""
        text = canonical_message.text

        # 处理逻辑
        response = await self._process_text(text)

        return response

    async def _process_text(self, text: str) -> MessageBase:
        """处理文本"""
        # 实际处理逻辑
        return MessageBase.create(text=text)


# 2. 创建Plugin
class MyPlugin:
    """我的插件（新系统）"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._providers: List[Any] = []

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> List[Any]:
        """
        初始化插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表
        """
        # 创建DecisionProvider
        decision_provider = MyDecisionProvider(config.get("decision", {}))
        await decision_provider.setup(event_bus, config)
        self._providers.append(decision_provider)

        # 监听自定义事件（替代旧系统的事件处理）
        async def on_custom_event(event_name, data, source):
            # 处理自定义事件
            print(f"收到事件: {event_name}, 数据: {data}")

        event_bus.on("custom.event", on_custom_event)

        # 发布设置完成事件
        await event_bus.emit("plugin.setup.completed", {"plugin": "MyPlugin"}, "MyPlugin")

        return self._providers

    async def cleanup(self):
        """清理资源"""
        for provider in self._providers:
            await provider.cleanup()
        self._providers.clear()

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "MyPlugin",
            "version": "2.0.0",
            "author": "Your Name",
            "description": "我的插件（新系统实现）",
            "category": "processing",
            "api_version": "1.0",
        }
```

### 配置对比

**旧配置**：
```toml
[plugins.my_plugin]
enable_my_plugin = true
api_url = "https://example.com/api"
timeout = 30
```

**新配置**：
```toml
[plugins.my_plugin]
enabled = true
priority = 100

[plugins.my_plugin.decision]
api_url = "https://example.com/api"
timeout = 30
```

---

## 常见问题

### Q1: 如何在Provider中访问AmaidesuCore？

**不推荐直接访问AmaidesuCore**。新架构通过EventBus进行通信，而不是直接方法调用。

如果需要与其他组件交互，应该：
1. 发布事件，让其他组件监听
2. 订阅事件，响应其他组件的请求

```python
# 旧方式（不推荐）
result = await self.core.some_method()

# 新方式（推荐）
await event_bus.emit("my.request", data, "MyProvider")
```

### Q2: 如何注册服务供其他Plugin使用？

新架构不使用服务注册，而是通过EventBus通信：

```python
# 发布者Provider
async def _publish_service_result(self, data):
    await self.event_bus.emit("service.result", data, "MyProvider")

# 订阅者Provider
event_bus.on("service.result", self.handle_service_result)
```

### Q3: 如何处理异步初始化？

在Provider的`_setup_internal`中进行异步初始化：

```python
class MyProvider(InputProvider):
    async def _setup_internal(self):
        # 异步连接到外部服务
        self.connection = await self._connect_async()
```

### Q4: 如何处理错误？

使用try-except捕获异常，并通过EventBus发布错误事件：

```python
try:
    result = await self._process(data)
    await event_bus.emit("process.success", result, "MyProvider")
except Exception as e:
    logger.error(f"处理失败: {e}", exc_info=True)
    await event_bus.emit("process.error", {"error": str(e)}, "MyProvider")
```

### Q5: 如何迁移复杂的Plugin？

对于复杂的Plugin，建议：
1. 先创建一个最小化的新Plugin（只包含一个Provider）
2. 逐步将功能从旧Plugin迁移到新Provider
3. 使用测试确保迁移过程中功能正常
4. 最后删除旧Plugin

### Q6: 是否需要立即迁移？

不需要。新架构向后兼容，旧Plugin可以继续使用。建议：
- 新开发的Plugin使用新架构
- 需要重大修改的旧Plugin可以顺便迁移
- 稳定运行的旧Plugin可以暂时保留

---

## 附录

### 快速参考

| 功能 | 旧系统（BasePlugin） | 新系统（Plugin） |
|------|---------------------|------------------|
| 访问核心 | `self.core.xxx` | `event_bus.emit/on` |
| 注册处理器 | `self.core.register_websocket_handler` | Provider监听EventBus事件 |
| 发布事件 | `self.emit_event` | `event_bus.emit` |
| 监听事件 | `self.listen_event` | `event_bus.on` |
| 注册服务 | `self.core.register_service` | EventBus通信 |
| 日志记录 | `self.logger` | `get_logger(__class__.__name__)` |

### 相关文档

- [Plugin系统设计文档](../refactor/design/plugin_system.md)
- [Provider接口文档](../refactor/design/provider_interfaces.md)
- [测试工具文档](../tests/test_plugin_utils.py)
- [AGENTS.md开发指南](../AGENTS.md)
