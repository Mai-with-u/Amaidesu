# 插件系统设计

## 🎯 核心目标

构建友好的插件系统，让社区开发者能够轻松为Amaidesu添加新能力。

---

## 📊 核心概念

### Provider 与 Plugin 的职责边界

**核心原则**：

```
Provider = 原子能力（单一职责、可复用、统一管理）
Plugin = 能力组合（整合 Provider、提供业务场景、不创建 Provider）
```

**对比**：

| 概念         | 定义             | 职责               | 创建 Provider | 示例                   |
| ------------ | ---------------- | ------------------ | ------------- | ---------------------- |
| **Provider** | 标准化的原子能力 | 单一能力，可替换   | -             | TTSProvider, VTSProvider |
| **Plugin**   | 聚合多个Provider | 业务场景，一键开关 | ❌ 不创建      | LiveStreamPlugin       |

### 三类参与者

| 参与者 | 职责 | Provider 来源 | 管理方式 |
|--------|------|--------------|----------|
| **内置 Provider** | 核心原子能力 | 放在层目录下 | Manager 直接管理 |
| **官方 Plugin** | 场景整合 | 声明依赖，不创建 | 配置驱动 |
| **第三方插件** | 扩展能力 | 通过 Registry 注册 | 统一注册机制 |

### 为什么 Plugin 不应该创建 Provider？

如果 Plugin 创建并管理自己的 Provider，会导致：

1. **管理分散**：每个 Plugin 各自管理 Provider，没有统一入口
2. **依赖混乱**：Plugin 之间可能绕过 EventBus，直接服务注册
3. **回到旧架构**：重蹈重构前的覆辙（24个插件，18个服务注册）

**正确做法**：

- 内置 Provider 放在层目录（`src/rendering/providers/`），由 Manager 统一管理
- Plugin 只声明需要哪些 Provider，不创建
- 第三方插件如需新 Provider，通过 ProviderRegistry 注册

### 推荐架构

```
src/
├── perception/providers/          # ✅ 内置 InputProvider
│   ├── console_input_provider.py
│   └── bili_danmaku_provider.py
│
├── decision/providers/            # ✅ 内置 DecisionProvider
│   └── maicore_decision_provider.py
│
├── rendering/                     # Layer 6-7 渲染层
│   ├── output_provider_manager.py # Manager 直接管理 Provider
│   ├── provider_registry.py       # ✅ Provider 注册表
│   └── providers/                 # ✅ 内置 OutputProvider
│       ├── tts_provider.py
│       ├── subtitle_provider.py
│       └── vts_provider.py
│
├── plugins/                       # 官方 Plugin（整合，不创建）
│   ├── live_stream/plugin.py      # 声明: bili_danmaku + tts + vts
│   └── game_companion/plugin.py   # 声明: minecraft + tts
│
plugins/                           # 第三方插件
├── custom_stt/
│   └── providers/whisper_provider.py  # 通过 Registry 注册
```

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
from typing import List, Dict, Protocol, Optional

class Plugin(Protocol):
    """插件协议 - 整合已有 Provider，不创建新 Provider"""

    def get_required_providers(self) -> Dict[str, List[str]]:
        """
        声明需要的 Provider（不创建）

        Returns:
            dict: 分类的 Provider 名称列表
            - input: 输入 Provider 列表
            - output: 输出 Provider 列表
            - decision: 决策 Provider 列表（可选）
        """
        return {
            "input": [],
            "output": []
        }

    async def setup(self, event_bus: EventBus, config: dict) -> List[Any]:
        """
        初始化插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            空列表（官方 Plugin 不创建 Provider）
            或第三方插件通过 Registry 注册后返回空列表

        注意：
            - 官方 Plugin 不应创建 Provider，只声明依赖
            - 第三方插件如需新 Provider，应通过 ProviderRegistry 注册
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

### ProviderRegistry（Provider 注册表）

```python
from typing import Dict, Type

class ProviderRegistry:
    """
    Provider 注册表 - 统一管理所有 Provider

    内置 Provider 在模块加载时自动注册
    第三方插件可以通过此接口注册自定义 Provider
    """
    _input_providers: Dict[str, Type[InputProvider]] = {}
    _output_providers: Dict[str, Type[OutputProvider]] = {}
    _decision_providers: Dict[str, Type[DecisionProvider]] = {}

    @classmethod
    def register_input(cls, name: str, provider_class: Type[InputProvider]):
        """注册输入 Provider"""
        cls._input_providers[name] = provider_class

    @classmethod
    def register_output(cls, name: str, provider_class: Type[OutputProvider]):
        """注册输出 Provider"""
        cls._output_providers[name] = provider_class

    @classmethod
    def register_decision(cls, name: str, provider_class: Type[DecisionProvider]):
        """注册决策 Provider"""
        cls._decision_providers[name] = provider_class

    @classmethod
    def create_output(cls, name: str, config: dict) -> OutputProvider:
        """创建输出 Provider 实例"""
        if name not in cls._output_providers:
            raise ValueError(f"Unknown output provider: {name}")
        return cls._output_providers[name](config)

    # ... 其他 create 方法
```

---

## 🏗️ 内置 Provider vs 官方 Plugin vs 社区插件

| 维量           | 内置 Provider            | 官方 Plugin              | 社区插件                  |
| -------------- | ------------------------ | ------------------------ | ------------------------- |
| **目录**       | `src/{layer}/providers/` | `src/plugins/`           | `plugins/`（根目录）      |
| **职责**       | 原子能力                 | 场景整合                 | 扩展能力                  |
| **创建 Provider** | ✅ 是 Provider 本身    | ❌ 只声明依赖             | ✅ 可通过 Registry 注册    |
| **管理方式**   | Manager 直接管理         | 配置驱动                 | Registry 统一注册         |
| **维护者**     | 官方核心团队             | 官方团队                 | 社区/用户                 |
| **启用**       | 配置驱动                 | 默认启用                 | 自动识别，默认启用        |
| **配置**       | `[providers.xxx]`        | `[plugins.xxx]`          | `[plugins.xxx]`           |
| **版本控制**   | 纳入 Git 仓库            | 纳入 Git 仓库            | `.gitignore` 排除         |

### 关键区别

```
内置 Provider（原子能力）
├── 放在层目录：src/perception/providers/, src/rendering/providers/
├── 由 Manager 直接管理（统一生命周期）
└── 配置文件决定启用哪些

官方 Plugin（场景整合）
├── 放在 src/plugins/
├── 声明需要哪些 Provider（不创建）
├── 处理业务逻辑（如礼物触发表情）
└── 通过 EventBus 通信

社区插件（扩展能力）
├── 放在 plugins/（根目录）
├── 可以通过 ProviderRegistry 注册新 Provider
├── 也可以只做业务逻辑整合
└── 遵循统一的注册机制
```

---

## 🔧 具体实现示例

### 示例1：官方 Plugin（整合已有 Provider）

```python
# src/plugins/live_stream/plugin.py
"""直播场景 Plugin - 整合已有 Provider，不创建新 Provider"""
from typing import List, Dict, Any
from src.core.event_bus import EventBus

class LiveStreamPlugin:
    """
    直播场景 Plugin

    整合 B 站弹幕输入 + TTS + VTS + 字幕输出
    不创建 Provider，只声明依赖和处理业务逻辑
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.event_bus = None

    def get_required_providers(self) -> Dict[str, List[str]]:
        """
        声明需要的 Provider（不创建）

        这些 Provider 在 src/perception/providers/ 和 src/rendering/providers/ 中
        由 Manager 统一管理
        """
        return {
            "input": ["bili_danmaku"],
            "output": ["tts", "vts", "subtitle"]
        }

    async def setup(self, event_bus: EventBus, config: dict) -> List[Any]:
        """
        设置 Plugin

        注意：不创建 Provider，只注册业务逻辑
        """
        self.event_bus = event_bus

        # 订阅业务事件（可选）
        event_bus.subscribe("danmaku.gift_received", self.on_gift)
        event_bus.subscribe("danmaku.super_chat", self.on_super_chat)

        return []  # ✅ 不返回 Provider

    async def on_gift(self, event_name: str, data: dict, source: str):
        """处理礼物事件"""
        # 业务逻辑：礼物触发特殊表情
        await self.event_bus.emit("expression.trigger", {
            "expression": "happy",
            "intensity": 0.8
        })

    async def on_super_chat(self, event_name: str, data: dict, source: str):
        """处理 SC 事件"""
        # 业务逻辑：SC 优先播报
        pass

    async def cleanup(self):
        """清理资源"""
        pass

    def get_info(self) -> dict:
        return {
            "name": "LiveStream",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "直播场景整合 Plugin",
            "category": "scene",
            "api_version": "1.0"
        }

plugin_entrypoint = LiveStreamPlugin
```

### 示例2：内置 Provider（放在层目录下）

```python
# src/perception/providers/bili_danmaku_provider.py
"""B 站弹幕输入 Provider - 内置，放在层目录下"""
from typing import AsyncIterator
from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData
from src.utils.logger import get_logger

class BiliDanmakuProvider(InputProvider):
    """
    B 站弹幕输入 Provider

    内置 Provider，放在 src/perception/providers/ 下
    由 InputProviderManager 统一管理
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.room_id = config.get("room_id")
        self.logger = get_logger("BiliDanmakuProvider")
        self._client = None

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """采集弹幕数据"""
        # 连接 B 站直播间
        self._client = await self._connect(self.room_id)

        while self.is_running:
            danmaku = await self._client.wait_for_danmaku()
            yield RawData(
                content={"text": danmaku.text, "user": danmaku.user},
                source="bili_danmaku",
                data_type="text",
                metadata={"room_id": self.room_id}
            )

    async def _cleanup(self):
        """清理连接"""
        if self._client:
            await self._client.close()

# 模块加载时自动注册到 Registry
from src.rendering.provider_registry import ProviderRegistry
ProviderRegistry.register_input("bili_danmaku", BiliDanmakuProvider)
```

### 示例3：第三方插件（注册自定义 Provider）

```python
# plugins/custom_stt/plugin.py
"""第三方 STT 插件 - 可以注册自定义 Provider"""
from typing import List, Dict, Any
from src.core.event_bus import EventBus
from src.rendering.provider_registry import ProviderRegistry
from .providers.whisper_provider import WhisperSTTProvider

class CustomSTTPlugin:
    """
    自定义 STT 插件

    第三方插件可以通过 ProviderRegistry 注册新的 Provider
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def get_required_providers(self) -> Dict[str, List[str]]:
        """声明依赖（包括自己注册的）"""
        return {
            "input": ["whisper_stt"],  # 自己注册的 Provider
            "output": []
        }

    async def setup(self, event_bus: EventBus, config: dict) -> List[Any]:
        """
        设置插件

        第三方插件通过 Registry 注册自定义 Provider
        """
        # ✅ 注册自定义 Provider 到全局 Registry
        ProviderRegistry.register_input("whisper_stt", WhisperSTTProvider)

        return []  # 不直接返回 Provider 实例

    async def cleanup(self):
        pass

    def get_info(self) -> dict:
        return {
            "name": "CustomSTT",
            "version": "1.0.0",
            "author": "Community",
            "description": "基于 Whisper 的语音识别插件",
            "category": "input",
            "api_version": "1.0"
        }

plugin_entrypoint = CustomSTTPlugin

# plugins/custom_stt/providers/whisper_provider.py
"""自定义 Whisper STT Provider"""
from src.core.providers.input_provider import InputProvider

class WhisperSTTProvider(InputProvider):
    """Whisper 语音识别 Provider"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.model_size = config.get("model_size", "base")

    async def _collect_data(self):
        # 语音识别逻辑
        ...
```

---

## 📦 插件安装

### 自动识别

**官方插件**：`src/plugins/`（官方，自动启用）
**社区插件**：`plugins/`（根目录，自动扫描）

### 安装示例

```bash
# 方式1：从GitHub克隆
git clone https://github.com/xxx/minecraft-plugin.git plugins/minecraft

# 方式2：下载后复制
cp -r ~/downloads/mygame-plugin plugins/mygame

# 方式3：直接创建目录
mkdir plugins/my-custom-plugin
# 然后创建插件文件...

# 运行程序（自动识别）
python main.py
# 日志会显示：✅ 插件加载成功: minecraft, mygame
```

### 插件目录结构要求

```
plugins/
├── minecraft/                # 社区插件1
│   ├── __init__.py         # 必须包含
│   │   └── minecraftPlugin
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
[plugins]
# 启用的插件列表
enabled = [
    "console_input",
    "llm_text_processor",
    "keyword_action",

    # 注释掉的插件将被禁用
    # "minecraft",
    # "mygame",
]

[plugins.minecraft]
enabled = true  # 单独配置优先级更高
api_url = "https://minecraft-api.example.com"
events_enabled = true

[plugins.mygame]
enabled = false  # 单独禁用
api_url = "https://mygame-api.example.com"
```

**配置说明**：
- **推荐使用**：`[plugins]enabled = [...]` 列表格式
- **兼容旧格式**：`[plugins.xxx]enabled = true/false` 单独配置
- **优先级规则**：单独配置 > 列表配置（如果两者都存在）
- **迁移工具**：提供工具自动转换旧配置到新格式

### 配置覆盖（可选）

```toml
# 默认：所有插件自动启用，使用默认配置

# 可选：自定义插件配置
[plugins.minecraft]
enabled = true  # 显式启用（默认就是true）
api_url = "https://minecraft-api.example.com"  # 自定义配置

[plugins.mygame]
enabled = false  # 禁用某个插件
```

---

## 🔄 插件迁移到扩展

### 官方插件迁移

| 原插件           | 迁移到                   | 插件类型 |
| ---------------- | ------------------------ | -------- |
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

## 📁 目录结构

### 完整目录结构

```
src/
├── perception/                     # Layer 1-2 感知层
│   ├── input_layer.py
│   └── providers/                  # ✅ 内置 InputProvider
│       ├── __init__.py             # 自动注册到 Registry
│       ├── console_input_provider.py
│       ├── bili_danmaku_provider.py
│       └── minecraft_event_provider.py
│
├── decision/                       # Layer 4: 决策层
│   ├── decision_manager.py
│   └── providers/                  # ✅ 内置 DecisionProvider
│       ├── __init__.py
│       └── maicore_decision_provider.py
│
├── rendering/                      # Layer 5-6 渲染层
│   ├── output_provider_manager.py
│   ├── provider_registry.py        # ✅ Provider 注册表
│   └── providers/                  # ✅ 内置 OutputProvider
│       ├── __init__.py             # 自动注册到 Registry
│       ├── tts_provider.py
│       ├── subtitle_provider.py
│       ├── vts_provider.py
│       └── minecraft_command_provider.py
│
├── plugins/                        # 官方 Plugin（场景整合）
│   ├── live_stream/                # 直播场景
│   │   ├── __init__.py
│   │   └── plugin.py               # 声明: bili_danmaku + tts + vts
│   ├── game_companion/             # 游戏陪伴场景
│   │   ├── __init__.py
│   │   └── plugin.py               # 声明: minecraft + tts
│   └── console_debug/              # 控制台调试
│       ├── __init__.py
│       └── plugin.py               # 声明: console_input + subtitle
│
plugins/                            # 社区插件（根目录）
├── custom_stt/                     # 社区插件：自定义 STT
│   ├── __init__.py
│   ├── plugin.py                   # 注册 WhisperSTTProvider
│   └── providers/
│       └── whisper_provider.py
└── my_game/                        # 社区插件：自定义游戏
    ├── __init__.py
    ├── plugin.py                   # 注册自定义 Provider
    └── providers/
```

### 关键说明

1. **内置 Provider** 放在对应层的 `providers/` 目录下
2. **官方 Plugin** 放在 `src/plugins/` 下，只做场景整合
3. **社区插件** 放在 `plugins/`（根目录），可以注册新 Provider

---

## 🔄 Plugin迁移指南

### 1. 迁移策略

**总体原则**：
- 完全重构，不提供兼容层
- 所有24个插件需要按新规范重写
- 提供详细的迁移指南和示例代码

### 2. 迁移步骤

#### 步骤1：分析现有Plugin

```python
# 旧Plugin（BasePlugin）
class BilibiliDanmakuPlugin(BasePlugin):
    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        self.room_id = plugin_config.get("room_id")
        self.danmaku_client = None

    async def setup(self):
        # 初始化弹幕客户端
        self.danmaku_client = BilibiliDanmakuClient(self.room_id)
        self.danmaku_client.on_danmaku(self._on_danmaku)

        # 注册WebSocket处理器
        await self.core.register_websocket_handler("text", self.handle_message)

        # 注册服务
        self.core.register_service("danmaku_input", self)

    async def handle_message(self, message: MessageBase):
        # 处理从MaiCore返回的消息
        pass

    async def cleanup(self):
        # 清理弹幕客户端
        if self.danmaku_client:
            await self.danmaku_client.close()

    async def _on_danmaku(self, danmaku: Danmaku):
        # 接收弹幕
        text = danmaku.text
        # 发送到MaiCore
        await self.core.send_to_maicore(MessageBase(text))
```

#### 步骤2：识别Plugin的功能

分析旧Plugin的功能，拆分为Provider：

| 旧Plugin功能 | 新Provider                   | 类型          |
| ------------ | ---------------------------- | ------------- |
| 接收弹幕     | BilibiliDanmakuInputProvider | InputProvider |
| 处理弹幕     | DanmakuProcessor             | Plugin        |

#### 步骤3：实现Provider

```python
@dataclass
class ProviderInfo:
    name: str
    version: str
    description: str
    supported_data_types: List[str]
    author: str

class BilibiliDanmakuInputProvider:
    """B站弹幕输入Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.room_id = config.get("room_id")
        self.danmaku_client = None

    def get_info(self) -> ProviderInfo:
        return ProviderInfo(
            name="bilibili_danmaku",
            version="1.0.0",
            description="B站弹幕输入Provider",
            supported_data_types=["danmaku"],
            author="Official"
        )

    async def start(self) -> AsyncIterator[RawData]:
        """启动弹幕输入"""
        self.danmaku_client = BilibiliDanmakuClient(self.room_id)
        self.danmaku_client.on_danmaku(self._on_danmaku)
        await self.danmaku_client.connect()

        while True:
            # 等待弹幕
            danmaku = await self.danmaku_client.wait_for_danmaku()
            yield RawData(
                content=danmaku.text,
                type="danmaku",
                source=self.get_info().name,
                metadata={
                    "user": danmaku.user,
                    "room_id": self.room_id
                }
            )

    async def stop(self):
        """停止弹幕输入"""
        if self.danmaku_client:
            await self.danmaku_client.close()

    async def cleanup(self):
        """清理资源"""
        await self.stop()

    async def _on_danmaku(self, danmaku: Danmaku):
        # 内部使用，不暴露
        pass
```

#### 步骤4：实现Plugin

```python
class BilibiliDanmakuPlugin(Plugin):
    """B站弹幕Plugin"""

    async def setup(self, event_bus: EventBus, config: dict) -> List[Provider]:
        """初始化Plugin，返回Provider列表"""
        self.event_bus = event_bus
        self.config = config

        # 1. 创建Provider
        danmaku_provider = BilibiliDanmakuInputProvider(config)

        # 2. 订阅EventBus（如果需要处理Decision层的响应）
        event_bus.on("decision.response.generated", self._on_response)

        # 3. 返回Provider列表
        return [danmaku_provider]

    async def cleanup(self):
        """清理资源"""
        pass

    async def _on_response(self, event: dict):
        """处理Decision层的响应"""
        # 如果需要处理弹幕相关的响应
        pass

    def get_info(self) -> dict:
        return {
            "name": "BilibiliDanmaku",
            "version": "1.0.0",
            "author": "Official",
            "description": "B站弹幕输入插件",
            "category": "input"
        }
```

#### 步骤5：测试验证

```python
# 测试Provider
async def test_bilibili_danmaku_input_provider():
    provider = BilibiliDanmakuInputProvider({"room_id": "123456"})

    # 启动Provider
    data_count = 0
    async for data in provider.start():
        assert isinstance(data, RawData)
        assert data.type == "danmaku"
        data_count += 1
        if data_count >= 10:
            await provider.stop()
            break

# 测试Plugin
async def test_bilibili_danmaku_plugin():
    event_bus = EventBus()
    config = {"room_id": "123456"}

    plugin = BilibiliDanmakuPlugin()
    providers = await plugin.setup(event_bus, config)

    assert len(providers) == 1
    assert isinstance(providers[0], BilibiliDanmakuInputProvider)

    await plugin.cleanup()
```

### 3. Plugin迁移检查清单

#### 分析阶段
- [ ] 列出旧Plugin的所有功能
- [ ] 识别哪些功能是输入，哪些是输出，哪些是处理
- [ ] 识别哪些功能可以拆分为Provider

#### 设计阶段
- [ ] 设计Provider接口
- [ ] 设计Plugin结构
- [ ] 设计EventBus事件订阅
- [ ] 设计配置文件格式
- [ ] 设计错误处理机制

#### 实现阶段
- [ ] 实现Provider
  - [ ] 实现start/stop/cleanup
  - [ ] 实现get_info()
  - [ ] 实现生命周期钩子（可选）
- [ ] 实现Plugin
  - [ ] 实现setup()
  - [ ] 实现cleanup()
  - [ ] 订阅EventBus（如果需要）
  - [ ] 实现get_info()

#### 测试阶段
- [ ] 单元测试
  - [ ] 测试Provider的功能
  - [ ] 测试Plugin的功能
  - [ ] 测试Provider的错误处理
  - [ ] 测试Plugin的生命周期
- [ ] 集成测试
  - [ ] 测试Provider集成
  - [ ] 测试Plugin集成
  - [ ] 测试EventBus集成
  - [ ] 测试端到端流程
- [ ] 手动测试
  - [ ] 功能验证
  - [ ] 性能验证
  - [ ] 边界条件测试
  - [ ] 用户场景测试

#### 文档阶段
- [ ] 创建config-template.toml
- [ ] 更新README.md
- [ ] 提供使用示例
- [ ] 说明迁移注意事项

### 4. Plugin迁移优先级

| 优先级 | Plugin类型 | Plugin名称              | 复杂度 | 预计工作量 |
| ------ | ---------- | ----------------------- | ------ | ---------- |
| P1     | 输入型     | ConsoleInput            | 简单   | 1天        |
| P1     | 输入型     | MockDanmaku             | 简单   | 1天        |
| P1     | 输出型     | Subtitle                | 简单   | 2天        |
| P2     | 输入型     | BilibiliDanmaku         | 中等   | 3天        |
| P2     | 输出型     | TTS                     | 中等   | 3天        |
| P2     | 输出型     | VTubeStudio             | 中等   | 3天        |
| P3     | 输入型     | Microphone              | 复杂   | 3天        |
| P3     | 输入型     | MinecraftPlugin         | 复杂   | 5天        |
| P3     | 输出型     | Warudo                  | 复杂   | 5天        |
| P3     | 处理型     | EmotionJudge            | 中等   | 3天        |
| P4     | 输入型     | BilibiliDanmakuOfficial | 复杂   | 5天        |
| P4     | 输入型     | VRChat                  | 复杂   | 5天        |
| P4     | 输出型     | OBS                     | 复杂   | 4天        |
| P4     | 处理型     | LLMProcessor            | 复杂   | 5天        |
| P4     | 处理型     | STT                     | 复杂   | 5天        |

**总计**：24个插件，预计36-40天

### 5. Plugin迁移验证流程

```
1. 单元测试
   ├─ Provider功能测试
   ├─ Plugin功能测试
   ├─ 错误处理测试
   └─ 生命周期测试

2. 集成测试
   ├─ Provider集成测试
   ├─ Plugin集成测试
   ├─ EventBus集成测试
   └─ 端到端测试

3. 手动测试
   ├─ 功能验证
   ├─ 性能验证
   ├─ 边界条件测试
   └─ 用户场景测试
```

### 6. 迁移配置示例

```toml
# 旧Plugin配置
[plugins.bilibili_danmaku]
enabled = true
room_id = "123456"

# 新Plugin配置
[plugins.bilibili_danmaku]
enabled = true
# Plugin配置保持不变
room_id = "123456"
```

### 7. 迁移注意事项

1. **不要使用BasePlugin**：新Plugin使用Plugin接口，不继承BasePlugin
2. **不要调用self.core**：新Plugin通过event_bus和config进行依赖注入
3. **拆分为Provider**：将旧Plugin的功能拆分为一个或多个Provider
4. **返回Provider列表**：Plugin的setup()方法必须返回Provider列表
5. **生命周期管理**：Provider实现start/stop/cleanup，Plugin实现setup/cleanup

### 8. 相关文档

- [多Provider并发设计](./multi_provider.md) - Provider接口和实现
- [DataCache设计](./data_cache.md) - 元数据和原始数据管理
- [ AmaidesuCore重构设计](./core_refactoring.md) - 核心模块重构

---

## ✅ 关键优势

### 1. 统一管理，不会回到旧架构
- ✅ 内置 Provider 由 Manager 统一管理
- ✅ 所有 Provider 通过 Registry 注册
- ✅ 强制使用 EventBus，禁止服务注册
- ✅ 不会重蹈"24个插件，18个服务注册"的覆辙

### 2. 职责清晰
- ✅ Provider = 原子能力（单一职责）
- ✅ Plugin = 场景整合（业务逻辑）
- ✅ 不混淆"能力"和"场景"

### 3. 一键开关
- ✅ 通过 `enabled` 控制 Provider/Plugin 的开关
- ✅ 无需修改代码，只需修改配置

### 4. 统一配置
- ✅ Provider 配置：`[providers.xxx]`
- ✅ Plugin 配置：`[plugins.xxx]`
- ✅ 配置层次清晰

### 5. 社区友好
- ✅ 社区可以通过 Registry 注册新 Provider
- ✅ 也可以只做业务整合（不注册 Provider）
- ✅ 遵循统一规范，降低学习成本

### 6. 自动识别
- ✅ `plugins/` 目录自动扫描
- ✅ 无需手动配置，开箱即用

### 7. 可测试性
- ✅ Provider 独立，可单独测试
- ✅ Plugin 只依赖 EventBus，易于 mock
- ✅ 没有隐式依赖

---

## 🔗 相关文档

- [7层架构设计](./layer_refactoring.md)
- [多Provider并发设计](./multi_provider.md)
- [决策层设计](./decision_layer.md)
