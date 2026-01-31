# Plugin Protocol API

## 概述

Plugin 协议定义了新的插件系统架构，用于替代 BasePlugin 系统。

## 与 BasePlugin 的区别

| 特性 | BasePlugin（旧系统） | Plugin（新系统） |
|------|---------------------|-------------------|
| 继承关系 | 继承 AmaidesuCore | 不继承任何基类，实现 Plugin 协议 |
| 依赖访问 | 通过 self.core 访问核心功能 | 通过 event_bus 和 config 依赖注入 |
| 功能封装 | 所有逻辑在插件类中 | 通过 Provider 接口封装功能 |
| 耦合度 | 高（直接依赖 AmaidesuCore） | 低（通过事件总线通信） |
| 可测试性 | 低（需要 mock AmaidesuCore） | 高（可独立测试 Provider） |

## Plugin 接口

### `__init__(self, config: Dict[str, Any])`
初始化插件。

**参数**:
- config: 插件配置（从 config.toml 中读取）

**示例**:
```python
class MyPlugin:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger("MyPlugin")
        self.enabled = config.get("enabled", True)
```

### `async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> List[Any]`
初始化插件并返回 Provider 列表。

**参数**:
- event_bus: 事件总线实例
- config: 插件配置

**返回**:
- Provider 列表（InputProvider、OutputProvider、DecisionProvider 等）

**示例**:
```python
async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
    self.event_bus = event_bus
    
    # 创建 InputProvider
    input_provider = MyInputProvider(config)
    self._providers.append(input_provider)
    
    return self._providers
```

### `async def cleanup(self)`
清理插件资源。

**示例**:
```python
async def cleanup(self):
    for provider in self._providers:
        try:
            if hasattr(provider, "cleanup"):
                await provider.cleanup()
        except Exception as e:
            self.logger.error(f"清理 Provider 失败: {e}", exc_info=True)
    self._providers.clear()
```

### `def get_info(self) -> Dict[str, Any]`
获取插件信息。

**返回**:
- 包含以下字段的字典：
  - name: 插件名称
  - version: 版本号
  - author: 作者
  - description: 描述
  - category: 分类（input/output/processing/game/hardware/software）
  - api_version: API 版本

**示例**:
```python
def get_info(self) -> Dict[str, Any]:
    return {
        "name": "MyPlugin",
        "version": "1.0.0",
        "author": "Author",
        "description": "Plugin description",
        "category": "input",
        "api_version": "1.0",
    }
```

## Provider 类型

### InputProvider

输入 Provider，从外部数据源采集数据。

**示例**:
```python
from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData

class MyInputProvider(InputProvider):
    async def _collect_data(self) -> AsyncIterator[RawData]:
        while not self._stop_event.is_set():
            # 采集数据
            data = await self._fetch_data()
            if data:
                raw_data = RawData(
                    content={"data": data},
                    source="my_provider",
                    data_type="text",
                )
                yield raw_data
            await asyncio.sleep(1)
```

### OutputProvider

输出 Provider，渲染到目标设备。

**示例**:
```python
from src.core.providers.output_provider import OutputProvider
from .base import RenderParameters

class MyOutputProvider(OutputProvider):
    async def _render_internal(self, parameters: RenderParameters):
        # 渲染到目标设备
        print(f"Rendering: {parameters}")
```

### DecisionProvider

决策 Provider，处理 CanonicalMessage 并生成 MessageBase。

**示例**:
```python
from src.core.providers.decision_provider import DecisionProvider
from src.canonical.canonical_message import CanonicalMessage
from maim_message import MessageBase

class MyDecisionProvider(DecisionProvider):
    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        # 处理 CanonicalMessage
        return MessageBase(message_segment=MessageSegment(...))
```

## 完整示例

```python
from typing import Dict, Any, List
from src.core.plugin import Plugin
from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData
from src.utils.logger import get_logger

class MyPlugin:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger("MyPlugin")
        self._providers: List[InputProvider] = []

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        self.event_bus = event_bus

        provider = MyInputProvider(config)
        self._providers.append(provider)

        return self._providers

    async def cleanup(self):
        for provider in self._providers:
            await provider.cleanup()
        self._providers.clear()

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "MyPlugin",
            "version": "1.0.0",
            "author": "Author",
            "description": "My plugin description",
            "category": "input",
            "api_version": "1.0",
        }

plugin_entrypoint = MyPlugin
```

## 事件通信

### 发布事件

```python
async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
    self.event_bus = event_bus
    
    # 订阅事件
    event_bus.on("some.event", self.handle_event)
    
    # 发布事件
    await event_bus.emit("plugin.ready", {"status": "ready"}, self.__class__.__name__)
```

### 订阅事件

```python
def handle_event(self, event_name: str, event_data: Any, source: str):
    self.logger.info(f"收到事件: {event_name} from {source}")
    # 处理事件
```

---

**相关文档**:
- [EventBus API](./event_bus.md)
- [InputProvider API](./input_provider.md)
- [OutputProvider API](./output_provider.md)
- [DecisionProvider API](./decision_provider.md)
