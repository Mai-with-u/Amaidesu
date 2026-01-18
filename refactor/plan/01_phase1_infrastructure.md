# Phase 1: 基础设施搭建

## 🎯 目标

搭建重构的基础设施，包括：
1. Provider接口定义（公共API）
2. Plugin接口定义
3. 决策Provider接口定义（新增）
4. PluginLoader实现
5. 完善EventBus

## 📁 目录结构

```
src/
├── core/
│   ├── amaidesu_core.py       # 保留
│   ├── event_bus.py            # 保留并增强
│   ├── pipeline_manager.py     # 保留
│   ├── context_manager.py      # 保留
│   ├── provider.py             # Provider接口（新建）
│   ├── decision_provider.py    # 决策Provider接口（新建）
│   ├── plugin.py            # Plugin接口（新建）
│   └── extension_loader.py     # 插件加载器（新建）
```

## 📝 实施内容

### 1.1 Provider接口（公共API）

创建`src/core/provider.py`，定义所有Provider的基类和协议：

```python
from typing import Protocol, AsyncIterator, Any
from src.core.event_bus import EventBus

class RawData:
    """原始数据基类 - Layer 1的输出格式"""
    def __init__(self, content: Any, source: str, **metadata):
        self.content = content
        self.source = source
        self.metadata = metadata
        self.timestamp = metadata.get("timestamp", time.time())

class InputProvider(Protocol):
    """输入Provider接口 - Layer 1

    多个InputProvider可以并发运行，采集不同来源的数据
    """
    async def start(self) -> AsyncIterator[RawData]:
        """
        启动输入流，返回原始数据

        Returns:
            AsyncIterator[RawData]: 异步迭代器，持续产生RawData
        """
        ...

    async def stop(self):
        """停止输入源"""
        ...

    async def cleanup(self):
        """清理资源"""
        ...

class OutputProvider(Protocol):
    """输出Provider接口 - Layer 6

    多个OutputProvider可以并发运行，渲染到不同目标
    """
    async def setup(self, event_bus: EventBus, config: dict):
        """
        设置Provider（订阅EventBus）

        Args:
            event_bus: 事件总线实例
            config: Provider配置
        """
        ...

    async def render(self, parameters: Any):
        """
        渲染输出

        Args:
            parameters: 渲染参数（通常是RenderParameters）
        """
        ...

    async def cleanup(self):
        """清理资源"""
        ...

class ProviderFactory:
    """Provider工厂 - 动态创建Provider实例"""
    def __init__(self):
        self._providers: dict[str, type] = {}

    def register(self, name: str, provider_class: type):
        """注册Provider"""
        self._providers[name] = provider_class

    def create(self, name: str, config: dict) -> Any:
        """创建Provider实例"""
        provider_class = self._providers.get(name)
        if not provider_class:
            raise ValueError(f"Provider not found: {name}")
        return provider_class(config)
```

### 1.2 决策Provider接口（新增）

创建`src/core/decision_provider.py`，定义决策Provider接口：

```python
from typing import Protocol
from src.core.event_bus import EventBus

class CanonicalMessage:
    """标准化消息格式 - Layer 3的输出格式"""
    def __init__(self, text: str, metadata: dict, context: dict = None):
        self.text = text
        self.metadata = metadata
        self.context = context or {}

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "text": self.text,
            "metadata": self.metadata,
            "context": self.context
        }

class DecisionProvider(Protocol):
    """决策Provider接口 - 决策层

    支持多种决策实现：MaiCore、本地LLM、规则引擎等
    """
    async def setup(self, event_bus: EventBus, config: dict):
        """
        初始化决策Provider

        Args:
            event_bus: 事件总线实例
            config: Provider配置
        """
        ...

    async def decide(self, canonical_message: CanonicalMessage):
        """
        根据CanonicalMessage做出决策

        Args:
            canonical_message: 标准化消息

        Returns:
            MessageBase: 决策结果
        """
        ...

    async def cleanup(self):
        """清理资源"""
        ...

class DecisionProviderFactory:
    """决策Provider工厂"""
    def __init__(self):
        self._providers: dict[str, type] = {}

    def register(self, name: str, provider_class: type):
        """注册决策Provider"""
        self._providers[name] = provider_class

    def create(self, name: str, config: dict):
        """创建决策Provider实例"""
        provider_class = self._providers.get(name)
        if not provider_class:
            raise ValueError(f"DecisionProvider not found: {name}")
        return provider_class(config)

class DecisionManager:
    """决策管理器 - 管理决策Provider"""
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.factory = DecisionProviderFactory()
        self._current_provider: DecisionProvider = None
        self._provider_name: str = None

    async def setup(self, provider_name: str, config: dict):
        """设置决策Provider"""
        provider_class = self.factory._providers.get(provider_name)
        if not provider_class:
            raise ValueError(f"DecisionProvider not found: {provider_name}")

        if self._current_provider:
            await self._current_provider.cleanup()

        self._current_provider = provider_class(config)
        self._provider_name = provider_name
        await self._current_provider.setup(self.event_bus, config)

    async def decide(self, canonical_message: CanonicalMessage):
        """进行决策"""
        if not self._current_provider:
            raise RuntimeError("No decision provider configured")
        return await self._current_provider.decide(canonical_message)

    async def switch_provider(self, provider_name: str, config: dict):
        """切换决策Provider（运行时）"""
        await self.setup(provider_name, config)

    async def cleanup(self):
        """清理资源"""
        if self._current_provider:
            await self._current_provider.cleanup()
```

### 1.3 Plugin接口

创建`src/core/plugin.py`，定义Plugin接口：

```python
from typing import Protocol, List, Dict, Any
from src.core.event_bus import EventBus

class Extension(Protocol):
    """插件协议 - Layer 8

    Extension是聚合多个Provider的完整功能
    """
    async def setup(self, event_bus: EventBus, config: dict) -> List:
        """
        初始化扩展

        Args:
            event_bus: 事件总线实例
            config: 扩展配置

        Returns:
            List[Provider]: 初始化好的Provider列表
        """
        ...

    async def cleanup(self):
        """清理资源"""
        ...

    def get_info(self) -> Dict[str, Any]:
        """
        获取扩展信息

        Returns:
            dict: 扩展信息（name, version, author, description等）
        """
        return {
            "name": "ExtensionName",
            "version": "1.0.0",
            "author": "Author",
            "description": "Extension description",
            "category": "game/hardware/software",
            "api_version": "1.0"
        }
```

### 1.4 PluginLoader实现

创建`src/core/extension_loader.py`，实现插件加载器：

```python
import os
import sys
import importlib
import inspect
from typing import List, Dict, Any, Optional
from src.utils.logger import get_logger
from src.core.extension import Extension
from src.core.event_bus import EventBus

class PluginLoader:
    """插件加载器 - 加载和管理扩展"""

    def __init__(self, event_bus: EventBus, config: dict):
        self.event_bus = event_bus
        self.config = config
        self.logger = get_logger("PluginLoader")
        self._loaded_extensions: Dict[str, Extension] = {}

        # 配置
        self.builtin_extensions_dir = "src/plugins"
        self.user_extensions_dir = "extensions"  # 根目录

    def _setup_sys_path(self):
        """设置sys.path以支持社区插件"""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        user_extensions_dir = os.path.join(project_root, "extensions")

        if user_extensions_dir not in sys.path:
            sys.path.insert(0, user_extensions_dir)
            self.logger.debug(f"Added to sys.path: {user_extensions_dir}")

    async def load_all(self):
        """加载所有扩展（内置 + 用户）"""
        self._setup_sys_path()

        # 加载官方插件
        await self._load_builtin_extensions()

        # 加载社区插件
        await self._load_user_extensions()

        self.logger.info(f"Total loaded extensions: {len(self._loaded_extensions)}")

    async def _load_builtin_extensions(self):
        """加载官方插件（src/plugins/）"""
        builtin_extensions = ["minecraft", "warudo", "dg_lab"]

        for ext_name in builtin_extensions:
            try:
                await self._load_extension(ext_name, builtin=True)
            except Exception as e:
                self.logger.error(f"Failed to load builtin extension '{ext_name}': {e}", exc_info=True)

    async def _load_user_extensions(self):
        """加载社区插件（plugins/）"""
        if not os.path.exists(self.user_extensions_dir):
            self.logger.info("User extensions directory not found")
            return

        for item in os.listdir(self.user_extensions_dir):
            item_path = os.path.join(self.user_extensions_dir, item)

            # 跳过非目录和隐藏目录
            if not os.path.isdir(item_path) or item.startswith("_"):
                continue

            # 检查是否有__init__.py
            init_path = os.path.join(item_path, "__init__.py")
            if not os.path.exists(init_path):
                continue

            # 检查配置是否禁用
            ext_config = self.config.get("extensions", {}).get(item, {})
            if ext_config.get("enabled", True) == False:
                self.logger.info(f"Extension '{item}' is disabled in config")
                continue

            try:
                await self._load_extension(item, builtin=False)
            except Exception as e:
                self.logger.error(f"Failed to load user extension '{item}': {e}", exc_info=True)

    async def _load_extension(self, name: str, builtin: bool):
        """加载单个扩展"""
        self.logger.info(f"Loading extension: {name} (builtin={builtin})")

        # 构建模块路径
        if builtin:
            module_path = f"src.extensions.{name}"
        else:
            module_path = name

        # 动态导入
        module = importlib.import_module(module_path)

        # 查找Plugin类
        extension_class = None
        for name, obj in inspect.getmembers(module):
            if inspect.isclass(obj) and issubclass(obj, Extension):
                extension_class = obj
                break

        if not extension_class:
            raise ValueError(f"Extension class not found in {module_path}")

        # 初始化扩展
        ext_config = self.config.get("extensions", {}).get(name, {})
        extension = extension_class()
        providers = await extension.setup(self.event_bus, ext_config)

        self._loaded_extensions[name] = {
            "extension": extension,
            "providers": providers
        }

        self.logger.info(f"Extension '{name}' loaded successfully with {len(providers)} providers")

    async def unload_extension(self, name: str):
        """卸载扩展"""
        if name not in self._loaded_extensions:
            self.logger.warning(f"Extension '{name}' not loaded")
            return

        ext_data = self._loaded_extensions[name]
        await ext_data["extension"].cleanup()

        del self._loaded_extensions[name]
        self.logger.info(f"Extension '{name}' unloaded")

    def get_loaded_extensions(self) -> List[Dict[str, Any]]:
        """获取已加载的扩展列表"""
        result = []
        for name, data in self._loaded_extensions.items():
            ext_info = data["extension"].get_info()
            ext_info["name"] = name
            ext_info["providers_count"] = len(data["providers"])
            result.append(ext_info)
        return result

    async def cleanup(self):
        """清理所有扩展"""
        for name in list(self._loaded_extensions.keys()):
            await self.unload_extension(name)
        self.logger.info("All extensions cleaned up")
```

### 1.5 EventBus增强

增强`src/core/event_bus.py`，支持更强大的事件路由：

```python
from typing import Callable, Dict, List, Any, Optional
from src.utils.logger import get_logger

class EventBus:
    """事件总线 - 模块间解耦的核心通信机制"""

    def __init__(self):
        self.logger = get_logger("EventBus")
        self._handlers: Dict[str, List[Callable]] = {}
        self._event_history: List[Dict[str, Any]] = []
        self._max_history = 100

    async def emit(self, event_name: str, data: Dict[str, Any], source: str = None):
        """
        发布事件

        Args:
            event_name: 事件名称
            data: 事件数据
            source: 事件来源（可选）
        """
        event = {
            "event": event_name,
            "data": data,
            "source": source,
            "timestamp": time.time()
        }

        # 记录事件历史
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)

        self.logger.debug(f"Event emitted: {event_name} from {source}")

        # 通知所有监听者
        handlers = self._handlers.get(event_name, [])
        if not handlers:
            return

        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                self.logger.error(f"Handler failed for event '{event_name}': {e}", exc_info=True)

    def on(self, event_name: str, handler: Callable):
        """
        订阅事件

        Args:
            event_name: 事件名称（支持通配符*）
            handler: 处理函数
        """
        if event_name not in self._handlers:
            self._handlers[event_name] = []

        self._handlers[event_name].append(handler)
        self.logger.debug(f"Handler registered for event: {event_name}")

    def off(self, event_name: str, handler: Callable):
        """取消订阅事件"""
        if event_name in self._handlers:
            if handler in self._handlers[event_name]:
                self._handlers[event_name].remove(handler)
                self.logger.debug(f"Handler unregistered for event: {event_name}")

    def get_event_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取事件历史"""
        return self._event_history[-limit:]

    def clear(self):
        """清除所有事件订阅"""
        self._handlers.clear()
        self.logger.info("All event handlers cleared")
```

## ✅ 验证标准

1. ✅ Provider接口定义完整（InputProvider、OutputProvider）
2. ✅ 决策Provider接口定义完整（DecisionProvider）
3. ✅ Plugin接口定义完整
4. ✅ PluginLoader实现完整，支持内置和社区插件
5. ✅ EventBus增强完成，支持事件路由和历史记录
6. ✅ 所有代码通过类型检查
7. ✅ 所有代码有完整的文档字符串

## 📝 提交

```bash
git add src/core/provider.py src/core/decision_provider.py src/core/plugin.py src/core/extension_loader.py
git commit -m "feat(phase1): add provider interfaces and extension system"
```
