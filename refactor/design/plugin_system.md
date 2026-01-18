# 插件系统设计

## 🎯 核心目标

构建友好的插件系统，让社区开发者能够轻松为Amaidesu添加新能力。

---

## 📊 核心概念

### Plugin（插件）

**定义**：聚合多个Provider的完整功能，是社区开发的入口。

**对比**：

| 概念         | 定义             | 职责               | 示例                   |
| ------------ | ---------------- | ------------------ | ---------------------- |
| **Provider**  | 标准化的原子能力 | 单一能力，可替换   | MinecraftEventProvider |
| **Plugin**    | 聚合多个Provider | 完整功能，一键开关 | MinecraftPlugin     |

**关系**：
- 一个Plugin = 多个Provider的聚合
- Plugin的`setup()`方法返回Provider列表
- 插件加载器自动注册所有Provider

---

## 🔌 公共API接口

### Provider接口

#### InputProvider接口

```python
from typing import Protocol, AsyncIterator, Any

class RawData:
    """原始数据基类"""
    def __init__(self, content: Any, source: str, metadata: dict = None):
        self.content = content
        self.source = source
        self.metadata = metadata or {}
        self.timestamp = time.time()

class InputProvider(Protocol):
    """输入Provider接口 - 社区可继承"""

    async def start(self) -> AsyncIterator[RawData]:
        """启动输入流"""
        ...

    async def stop(self):
        """停止输入源"""
        ...

    async def cleanup(self):
        """清理资源"""
        ...
```

#### OutputProvider接口

```python
from typing import Protocol, Any

class RenderParameters:
    """渲染参数"""
    def __init__(self, expressions: dict, tts_text: str, subtitle_text: str, hotkeys: list = None):
        self.expressions = expressions
        self.tts_text = tts_text
        self.subtitle_text = subtitle_text
        self.hotkeys = hotkeys or []
        self.timestamp = time.time()

class OutputProvider(Protocol):
    """输出Provider接口 - 社区可继承"""

    async def setup(self, event_bus: EventBus):
        """设置Provider（订阅EventBus）"""
        ...

    async def render(self, parameters: RenderParameters):
        """渲染输出"""
        ...

    async def cleanup(self):
        """清理资源"""
        ...
```

### Plugin接口

```python
from typing import List, Protocol

class Plugin(Protocol):
    """插件协议 - 聚合多个Provider"""

    async def setup(self, event_bus: EventBus, config: dict) -> List[Provider]:
        """
        初始化插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            初始化好的Provider列表
        """
        ...

    async def cleanup(self):
        """清理资源"""
        ...

    def get_info(self) -> dict:
        """
        获取插件信息

        Returns:
            dict: 插件信息（name, version, description等）
        """
        return {
            "name": "PluginName",
            "version": "1.0.0",
            "author": "Author",
            "description": "Plugin description",
            "category": "game/hardware/software",
            "api_version": "1.0"
        }
```

---

## 🏗️ 官方插件 vs 社区插件

| 维量         | 官方插件           | 社区插件                       |
| ------------ | ------------------ | ------------------------------ |
| **目录**     | `src/plugins/`     | `plugins/`（根目录）        |
| **维护者**   | 官方团队           | 社区/用户                      |
| **启用**     | 默认启用           | ✅ **自动识别，默认启用**       |
| **配置**     | `[plugins.xxx]` | `[plugins.xxx]`（可选覆盖） |
| **Provider** | 可以定义新Provider | 可以定义新Provider             |
| **来源**     | 代码仓库           | 插件市场/手动安装              |
| **版本控制** | 纳入Git仓库        | `.gitignore`排除               |

---

## 🔧 具体实现示例

### 示例：Minecraft插件

```python
# src/plugins/minecraft/__init__.py
"""Minecraft插件"""
from typing import List
from src.core.plugin import Plugin
from src.core.event_bus import EventBus
from src.core.input_provider import InputProvider
from src.core.output_provider import OutputProvider
from src.providers.event_provider import MinecraftEventProvider
from src.providers.command_provider import MinecraftCommandProvider

class MinecraftPlugin(Plugin):
    """Minecraft插件 - 聚合Minecraft的所有能力"""

    async def setup(self, event_bus: EventBus, config: dict) -> List[Provider]:
        """
        初始化Minecraft插件

        Returns:
            Provider列表
        """
        # ✅ 一处配置
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 25565)

        # ✅ 一处初始化
        providers = []

        # 输入Provider
        if config.get("events_enabled", True):
            event_provider = MinecraftEventProvider({
                "host": self.host,
                "port": self.port
            })
            await event_provider.setup(event_bus)
            providers.append(event_provider)

        # 输出Provider
        if config.get("commands_enabled", True):
            command_provider = MinecraftCommandProvider({
                "host": self.host,
                "port": self.port
            })
            await command_provider.setup(event_bus)
            providers.append(command_provider)

        self.providers = providers
        return providers

    async def cleanup(self):
        """清理资源"""
        await asyncio.gather(*[p.cleanup() for p in self.providers])

    def get_info(self) -> dict:
        """获取插件信息"""
        return {
            "name": "Minecraft",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "Minecraft游戏集成插件",
            "category": "game",
            "api_version": "1.0"
        }

# 内部Provider（对开发者透明）
# src/plugins/minecraft/providers/event_provider.py
from typing import AsyncIterator
from src.core.input_provider import InputProvider, RawData
from src.utils.logger import get_logger

class MinecraftEventProvider(InputProvider):
    """Minecraft事件输入Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 25565)
        self.logger = get_logger("MinecraftEventProvider")
        self._client = None
        self._running = False

    async def start(self) -> AsyncIterator[RawData]:
        """启动游戏事件输入流"""
        self._running = False

        # 连接Minecraft服务器
        # ... 连接逻辑

        while self._running:
            # 监听游戏事件
            event = await self._wait_for_event()

            yield RawData(
                content=event,
                source="minecraft",
                metadata={"host": self.host, "port": self.port}
            )

    async def stop(self):
        """停止输入源"""
        self._running = False

    async def cleanup(self):
        """清理资源"""
        self.logger.info("MinecraftEventProvider cleanup")

# src/plugins/minecraft/providers/command_provider.py
from src.core.output_provider import OutputProvider, RenderParameters
from src.utils.logger import get_logger

class MinecraftCommandProvider(OutputProvider):
    """Minecraft命令输出Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 25565)
        self.logger = get_logger("MinecraftCommandProvider")
        self._client = None

    async def setup(self, event_bus: EventBus):
        """设置Provider（订阅EventBus）"""
        self.event_bus = event_bus

        # 订阅RenderParameters事件
        event_bus.on("expression.parameters_generated", self.on_parameters)

        # 连接Minecraft服务器
        # ... 连接逻辑

    async def on_parameters(self, event: dict):
        """处理RenderParameters事件"""
        parameters = event.get("data")
        if not parameters:
            return

        # 渲染命令
        await self.render(parameters)

    async def render(self, parameters: RenderParameters):
        """渲染Minecraft命令"""
        if parameters.minecraft_commands:
            await self._send_commands(parameters.minecraft_commands)

    async def _send_commands(self, commands: list):
        """发送Minecraft命令"""
        # ... 发送逻辑

    async def cleanup(self):
        """清理资源"""
        self.logger.info("MinecraftCommandProvider cleanup")
```

---

## 📦 插件安装

### 自动识别

**官方插件**：`src/plugins/`（官方，自动启用）
**社区插件**：`plugins/`（根目录，自动扫描）

### 安装示例

```bash
# 方式1：从GitHub克隆
git clone https://github.com/xxx/genshin-plugin.git plugins/genshin

# 方式2：下载后复制
cp -r ~/downloads/mygame-plugin plugins/mygame

# 方式3：直接创建目录
mkdir plugins/my-custom-plugin
# 然后创建插件文件...

# 运行程序（自动识别）
python main.py
# 日志会显示：✅ 插件加载成功: genshin, mygame
```

### 插件目录结构要求

```
plugins/
├── genshin/                # 社区插件1
│   ├── __init__.py         # 必须包含
│   │   └── GenshinPlugin
│   └── providers/
└── mygame/                 # 社区插件2
    ├── __init__.py         # 必须包含
    │   └── MyGamePlugin
    └── providers/
```

---

## 📋 配置示例

### 官方插件配置

```toml
# 官方插件（官方）
[plugins.minecraft]
enabled = true
host = "localhost"
port = 25565
events_enabled = true
commands_enabled = true

[plugins.warudo]
enabled = true
host = "localhost"
port = 50051

[plugins.dg_lab]
enabled = true
api_url = "http://localhost:8080/api"
```

### 社区插件配置

```toml
# 社区插件（社区）
[plugins.genshin]
enabled = false  # 需要手动启用
api_url = "https://genshin-api.example.com"
events_enabled = true

[plugins.mygame]
enabled = false
api_url = "https://mygame-api.example.com"
```

### 配置覆盖（可选）

```toml
# 默认：所有插件自动启用，使用默认配置

# 可选：自定义插件配置
[plugins.genshin]
enabled = true  # 显式启用（默认就是true）
api_url = "https://genshin-api.example.com"  # 自定义配置

[plugins.mygame]
enabled = false  # 禁用某个插件
```

---

## 🔄 插件迁移到扩展

### 官方插件迁移

| 原插件           | 迁移到                  | 插件类型 |
| ---------------- | ----------------------- | -------- |
| `mainosaba`      | `src/plugins/mainosaba/` | 官方插件 |
| `minecraft`      | `src/plugins/minecraft/` | 官方插件 |
| `warudo`         | `src/plugins/warudo/`    | 官方插件 |
| `dg_lab_service` | `src/plugins/dg_lab/`    | 官方插件 |

### 迁移步骤

```bash
# 1. 使用git mv迁移（必须！）
git mv src/plugins/minecraft src/plugins/minecraft
git commit -m "refactor: migrate minecraft plugin to plugin"

# 2. 改造插件为插件
# 将单一插件拆分为多个Provider
# 创建Plugin类聚合Provider

# 3. 更新配置
# [plugins.minecraft] → [plugins.minecraft]
```

### 迁移代码示例

**原插件** (`src/plugins/minecraft/plugin.py`):
```python
# 原插件：单一插件同时处理输入和输出
class MinecraftPlugin(BasePlugin):
    async def setup(self):
        # 注册处理器
        await self.core.register_websocket_handler("text", self.handle_message)

        # 注册服务
        self.core.register_service("minecraft", self)

    async def handle_message(self, message):
        # 处理消息，执行命令
        await self._send_command(message)
```

**迁移后插件** (`src/plugins/minecraft/__init__.py`):
```python
# 插件：拆分为多个Provider
class MinecraftPlugin(Plugin):
    async def setup(self, event_bus, config):
        providers = []

        # 输入Provider
        if config.get("events_enabled", True):
            event_provider = MinecraftEventProvider(config)
            await event_provider.setup(event_bus)
            providers.append(event_provider)

        # 输出Provider
        if config.get("commands_enabled", True):
            command_provider = MinecraftCommandProvider(config)
            await command_provider.setup(event_bus)
            providers.append(command_provider)

        return providers
```

---

## 📁 插件目录结构

### 官方插件结构

```
src/plugins/
├── minecraft/                      # 官方插件（官方）
│   ├── __init__.py                 # Plugin类
│   └── providers/                  # Provider实现
│       ├── event_provider.py       # 输入Provider
│       └── command_provider.py    # 输出Provider
├── warudo/                         # 官方插件
│   ├── __init__.py
│   └── providers/
└── dg_lab/                         # 官方插件
    ├── __init__.py
    └── providers/
```

### 社区插件结构

```
plugins/                            # 社区插件（根目录）
├── genshin/                        # 社区插件1
│   ├── __init__.py                 # 必须包含
│   └── providers/                  # Provider实现
└── mygame/                         # 社区插件2
    ├── __init__.py                 # 必须包含
    └── providers/
```

---

## ✅ 关键优势

### 1. 一键开关
- ✅ 通过`enabled`控制插件的整体开关
- ✅ 无需修改代码，只需修改配置

### 2. 统一配置
- ✅ 插件的配置集中管理
- ✅ 一处配置，多处生效

### 3. 社区友好
- ✅ 开发者只需实现Plugin
- ✅ 自动拆分为Provider
- ✅ 降低开发门槛

### 4. 自动识别
- ✅ 放在`plugins/`目录自动加载
- ✅ 无需手动配置，开箱即用

### 5. 聚合能力
- ✅ 一个插件包含多个Provider
- ✅ 统一初始化和清理
- ✅ 统一配置管理

---

## 🔗 相关文档

- [6层架构设计](./layer_refactoring.md)
- [多Provider并发设计](./multi_provider.md)
- [决策层设计](./decision_layer.md)
