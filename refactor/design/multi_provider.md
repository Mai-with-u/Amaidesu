# 多Provider并发设计

## 🎯 核心目标

支持输入层(Layer 1)和输出层(Layer 5)的**多Provider并发处理**，提高系统吞吐量和响应速度。

---

## 📊 并发设计概览

### 输入层并发（Layer 1）

```
弹幕InputProvider ──┐
                    ├──→ 都生成RawData
游戏InputProvider ──┤
                    │   通过EventBus发送到Layer 2
语音InputProvider ──┘
```

### 输出层并发（Layer 7）

```
RenderParameters ──┐
                  ├──→ 分别渲染到不同目标
字幕Renderer ─────┤  (字幕窗口、TTS音频、虚拟形象）
                  │
TTSRenderer ───────┤
                  │
VTSRenderer ───────┘
```

---

## 🔑 关键设计决策

### 决策：多Provider并发处理

**决策**: 输入层和输出层支持多个Provider同时运行

**理由**:
- 现实场景需要：弹幕、游戏、语音等不同来源同时输入
- 输出也需要：字幕、TTS、VTS等不同目标同时渲染
- Provider间解耦，互不干扰
- 提高系统吞吐量和响应速度

**示例**:
```python
# 输入层：3个Provider并发
danmaku_provider.start()  # 采集弹幕
game_provider.start()      # 采集游戏状态
voice_provider.start()     # 采集语音

# 输出层：3个Provider并发
subtitle_provider.render(params)   # 显示字幕
tts_provider.render(params)        # 播放语音
vts_provider.render(params)       # 控制虚拟形象
```

---

## 🏗️ 架构设计

### Layer 1: 输入层并发架构

```mermaid
graph TB
    subgraph "Layer 1: 输入感知层（多Provider并发）"
        Danmaku[弹幕InputProvider]
        Game[游戏InputProvider]
        Voice[语音InputProvider]
    end

    subgraph "EventBus"
        EventBus[事件总线]
    end

    subgraph "Layer 2: 输入标准化层"
        Normalization[统一转换为Text]
    end

    Danmaku -->|"perception.raw_data"| EventBus
    Game -->|"perception.raw_data"| EventBus
    Voice -->|"perception.raw_data"| EventBus

    EventBus -->|"normalization.text_ready"| Normalization

    style Danmaku fill:#e1f5ff
    style Game fill:#e1f5ff
    style Voice fill:#e1f5ff
    style EventBus fill:#f5e1ff
    style Normalization fill:#fff4e1
```

### Layer 5: 输出层并发架构

```mermaid
graph TB
    subgraph "Layer 5: 表现生成层"
        Expression[生成RenderParameters]
    end

    subgraph "EventBus"
        EventBus[事件总线]
    end

    subgraph "Layer 5: 渲染呈现层（多Provider并发）"
        Subtitle[字幕Renderer]
        TTS[TTSRenderer]
        VTS[VTSRenderer]
    end

    Expression -->|"expression.parameters_generated"| EventBus
    EventBus -->|"rendering.audio_played"| Subtitle
    EventBus -->|"rendering.audio_played"| TTS
    EventBus -->|"rendering.expression_applied"| VTS

    style Expression fill:#e1ffe1
    style EventBus fill:#f5e1ff
    style Subtitle fill:#e1f5ff
    style TTS fill:#e1f5ff
    style VTS fill:#e1f5ff
```

---

## 🔌 Provider接口

### InputProvider接口

```python
from typing import AsyncIterator, Protocol, Any
from src.core.event_bus import EventBus
from src.utils.logger import get_logger

class RawData:
    """原始数据基类"""
    def __init__(self, content: Any, source: str, metadata: dict = None):
        self.content = content
        self.source = source
        self.metadata = metadata or {}
        self.timestamp = time.time()

class InputProvider(Protocol):
    """输入Provider接口 - 支持多个Provider并发采集"""

    async def start(self) -> AsyncIterator[RawData]:
        """
        启动输入流，返回原始数据

        多个InputProvider可以同时运行，各自采集不同来源的数据

        Yields:
            RawData: 原始数据
        """
        ...

    async def stop(self):
        """停止输入源"""
        ...

    async def cleanup(self):
        """清理资源"""
        ...
```

### OutputProvider接口

```python
from typing import Protocol, Any
from src.core.event_bus import EventBus

class RenderParameters:
    """渲染参数"""
    def __init__(self, expressions: dict, tts_text: str, subtitle_text: str, hotkeys: list = None):
        self.expressions = expressions
        self.tts_text = tts_text
        self.subtitle_text = subtitle_text
        self.hotkeys = hotkeys or []
        self.timestamp = time.time()

class OutputProvider(Protocol):
    """输出Provider接口 - 支持多个Provider并发渲染"""

    async def setup(self, event_bus: EventBus):
        """
        设置Provider（订阅EventBus）

        多个OutputProvider可以同时订阅RenderParameters事件
        """
        ...

    async def render(self, parameters: RenderParameters):
        """
        渲染输出

        多个OutputProvider可以同时处理相同的RenderParameters
        """
        ...

    async def cleanup(self):
        """清理资源"""
        ...
```

---

## 📁 目录结构

### 输入层目录结构（Layer 1）

```
src/perception/                    # Layer 1: 输入感知
├── text/
│   ├── console_input.py           # ConsoleInputProvider
│   └── danmaku/                   # 弹幕输入
│       ├── base_danmaku.py        # 弹幕基类
│       ├── bilibili_danmaku.py    # B站弹幕
│       └── mock_danmaku.py        # 模拟弹幕
├── audio/
│   ├── microphone.py              # 麦克风输入
│   └── stream_audio.py            # 流音频输入
└── input_factory.py               # InputProvider工厂
```

### 输出层目录结构（Layer 6）

```
src/rendering/                    # Layer 7: 渲染呈现
├── virtual_rendering/             # 虚拟渲染
│   ├── base_renderer.py
│   └── implementations/
│       ├── vts_renderer.py        # VTSRenderer
│       └── obs_renderer.py        # OBSRenderer
├── audio_rendering/               # 音频渲染
│   ├── playback_manager.py
│   └── implementations/
│       ├── edge_tts.py           # TTSRenderer (Edge)
│       ├── gptsovits_tts.py      # TTSRenderer (GPT-SoVITS)
│       └── omni_tts.py           # TTSRenderer (Omni)
└── visual_rendering/              # 视觉渲染
    ├── subtitle_renderer.py       # SubtitleRenderer
    └── sticker_renderer.py        # StickerRenderer
```

---

## 🔧 具体实现示例

### 示例1: ConsoleInputProvider

```python
from typing import AsyncIterator
from src.core.input_provider import InputProvider, RawData

class ConsoleInputProvider(InputProvider):
    """控制台输入Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.logger = get_logger("ConsoleInputProvider")
        self._running = False

    async def start(self) -> AsyncIterator[RawData]:
        """启动控制台输入流"""
        self._running = True
        loop = asyncio.get_event_loop()

        while self._running:
            try:
                # 使用aioinput从标准输入读取
                import aioconsole
                text = await aioconsole.ainput("> ")

                if text.lower() in ["quit", "exit", "q"]:
                    break

                # 生成RawData
                yield RawData(
                    content=text,
                    source="console",
                    metadata={"user": "local"}
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"控制台输入异常: {e}", exc_info=True)

    async def stop(self):
        """停止输入源"""
        self._running = False

    async def cleanup(self):
        """清理资源"""
        self.logger.info("ConsoleInputProvider cleanup")
```

### 示例2: BilibiliDanmakuProvider

```python
from typing import AsyncIterator
from src.core.input_provider import InputProvider, RawData

class BilibiliDanmakuProvider(InputProvider):
    """B站弹幕Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.room_id = config.get("room_id")
        self.logger = get_logger("BilibiliDanmakuProvider")
        self._client = None
        self._running = False

    async def start(self) -> AsyncIterator[RawData]:
        """启动弹幕输入流"""
        self._running = True

        # 连接B站直播间
        from blivedm import BLiveClient

        async def on_danmaku(client, danmaku):
            """处理弹幕"""
            yield RawData(
                content=danmaku.text,
                source="bilibili_danmaku",
                metadata={
                    "user_id": danmaku.user_id,
                    "user_name": danmaku.user_name,
                    "room_id": self.room_id
                }
            )

        self._client = BLiveClient(self.room_id)
        self._client.set_handler(on_danmaku)

        await self._client.connect()

        while self._running:
            await asyncio.sleep(1)

    async def stop(self):
        """停止输入源"""
        self._running = False
        if self._client:
            await self._client.disconnect()

    async def cleanup(self):
        """清理资源"""
        self.logger.info("BilibiliDanmakuProvider cleanup")
```

### 示例3: SubtitleRenderer

```python
from src.core.output_provider import OutputProvider, RenderParameters

class SubtitleRenderer(OutputProvider):
    """字幕渲染Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.logger = get_logger("SubtitleRenderer")
        self.window = None
        self.event_bus = None

    async def setup(self, event_bus: EventBus):
        """设置Provider（订阅EventBus）"""
        self.event_bus = event_bus

        # 订阅RenderParameters事件
        event_bus.on("expression.parameters_generated", self.on_parameters)

        # 初始化字幕窗口
        self.window = await self._create_subtitle_window()

        self.logger.info("SubtitleRenderer setup complete")

    async def on_parameters(self, event: dict):
        """处理RenderParameters事件"""
        parameters = event.get("data")
        if not parameters:
            return

        # 渲染字幕
        await self.render(parameters)

    async def render(self, parameters: RenderParameters):
        """渲染字幕"""
        if not parameters.subtitle_text:
            return

        # 显示字幕
        await self.window.show_text(parameters.subtitle_text)

        # 发布事件
        await self.event_bus.emit("rendering.subtitle_shown", {
            "text": parameters.subtitle_text,
            "timestamp": parameters.timestamp
        })

    async def _create_subtitle_window(self):
        """创建字幕窗口"""
        # 使用字幕窗口库创建窗口
        pass

    async def cleanup(self):
        """清理资源"""
        if self.window:
            await self.window.close()
        self.logger.info("SubtitleRenderer cleanup")
```

### 示例4: VTSRenderer

```python
from src.core.output_provider import OutputProvider, RenderParameters

class VTSRenderer(OutputProvider):
    """VTS渲染Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 8001)
        self.logger = get_logger("VTSRenderer")
        self.vts_client = None
        self.event_bus = None

    async def setup(self, event_bus: EventBus):
        """设置Provider（订阅EventBus）"""
        self.event_bus = event_bus

        # 订阅RenderParameters事件
        event_bus.on("expression.parameters_generated", self.on_parameters)

        # 连接VTubeStudio
        from vts_python import VTSClient
        self.vts_client = VTSClient(self.host, self.port)
        await self.vts_client.connect()

        self.logger.info(f"VTSRenderer connected to VTS at {self.host}:{self.port}")

    async def on_parameters(self, event: dict):
        """处理RenderParameters事件"""
        parameters = event.get("data")
        if not parameters:
            return

        # 渲染虚拟形象
        await self.render(parameters)

    async def render(self, parameters: RenderParameters):
        """渲染虚拟形象"""
        # 应用表情参数
        if parameters.expressions:
            for expression_name, value in parameters.expressions.items():
                await self.vts_client.set_parameter(expression_name, value)

        # 执行热键
        if parameters.hotkeys:
            for hotkey in parameters.hotkeys:
                await self.vts_client.trigger_hotkey(hotkey)

        # 发布事件
        await self.event_bus.emit("rendering.expression_applied", {
            "expressions": parameters.expressions,
            "hotkeys": parameters.hotkeys,
            "timestamp": parameters.timestamp
        })

    async def cleanup(self):
        """清理资源"""
        if self.vts_client:
            await self.vts_client.disconnect()
        self.logger.info("VTSRenderer cleanup")
```

---

## 📋 配置示例

### 输入层配置

```toml
[perception]
inputs = ["danmaku", "game", "voice"]

[perception.inputs.danmaku]
type = "bilibili_danmaku"
room_id = "123456"

[perception.inputs.game]
type = "minecraft"
host = "localhost"
port = 25565

[perception.inputs.voice]
type = "microphone"
device_index = 0
```

### 输出层配置

```toml
[rendering]
outputs = ["subtitle", "tts", "vts"]

[rendering.outputs.subtitle]
type = "subtitle"
font_size = 24
window_position = "bottom"

[rendering.outputs.tts]
type = "tts"
provider = "edge"
voice = "zh-CN-XiaoxiaoNeural"

[rendering.outputs.vts]
type = "virtual"
host = "localhost"
port = 8001
```

---

---

## 🔧 Provider错误处理

### 1. 错误隔离原则

**设计原则**：
- Provider失败**不影响其他Provider**
- Provider失败不影响EventBus
- 记录详细错误日志
- 提供手动重启接口

### 2. ProviderManager错误隔离实现

```python
import asyncio
from typing import List

class ProviderManager:
    """Provider管理器"""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.logger = get_logger("ProviderManager")

    async def start_input_providers(self, providers: List[InputProvider]):
        """
        启动所有InputProvider，错误隔离

        使用asyncio.gather确保所有Provider都启动完成，即使某个失败
        """
        tasks = []

        for provider in providers:
            # 为每个Provider创建独立任务，错误隔离
            task = asyncio.create_task(self._run_provider(provider))
            tasks.append(task)

        # 使用gather，即使某个Provider失败也等待所有Provider
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 检查哪些Provider启动失败
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(f"Provider {providers[i].get_info().name} failed to start: {result}")

    async def _run_provider(self, provider: InputProvider):
        """运行单个Provider，捕获异常"""
        try:
            async for data in provider.start():
                await self.event_bus.emit("perception.raw_data.generated", {
                    "data": data,
                    "source": provider.get_info().name
                })
        except Exception as e:
            self.logger.error(f"Provider {provider.get_info().name} failed: {e}", exc_info=True)
            # 不重新抛出，不影响其他Provider
```

### 3. 错误处理策略

```python
@dataclass
class ProviderConfig:
    """Provider配置"""
    enabled: bool = True
    auto_restart: bool = False  # 自动重启失败的Provider（可选）
    restart_interval: int = 5  # 自动重启的间隔（秒）

class ProviderManager:
    def __init__(self, event_bus: EventBus, config: ProviderConfig):
        self.event_bus = event_bus
        self.config = config
        self.logger = get_logger("ProviderManager")
        self._provider_tasks: Dict[str, asyncio.Task] = {}

    async def _run_provider(self, provider: InputProvider):
        """运行单个Provider，支持自动重启"""
        provider_name = provider.get_info().name

        while True:
            try:
                async for data in provider.start():
                    await self.event_bus.emit("perception.raw_data.generated", {
                        "data": data,
                        "source": provider_name
                    })
                # Provider正常结束，退出循环
                break

            except Exception as e:
                self.logger.error(f"Provider {provider_name} failed: {e}", exc_info=True)

                # 检查是否需要自动重启
                if not self.config.auto_restart:
                    self.logger.error(f"Provider {provider_name} stopped (auto_restart=False)")
                    break

                # 等待重启间隔
                self.logger.info(f"Provider {provider_name} will restart in {self.config.restart_interval}s")
                await asyncio.sleep(self.config.restart_interval)
```

---

## 🔄 Provider生命周期管理

### 1. Provider生命周期

**生命周期**：start → running → stop → cleanup

**生命周期钩子**：

```python
from typing import Protocol

class InputProvider(Protocol):
    """输入Provider接口"""

    async def start(self) -> AsyncIterator[RawData]:
        """启动Provider，开始生成数据"""
        ...

    async def stop(self):
        """停止Provider，停止生成数据"""
        ...

    async def cleanup(self):
        """清理Provider资源"""
        ...

    async def on_start(self):
        """启动后钩子（可选）"""
        ...

    async def on_stop(self):
        """停止后钩子（可选）"""
        ...

    async def on_error(self, error: Exception):
        """错误处理钩子（可选）"""
        ...
```

### 2. ProviderInfo接口

```python
from dataclasses import dataclass
from typing import List

@dataclass
class ProviderInfo:
    """Provider信息"""
    name: str
    version: str
    description: str
    supported_data_types: List[str]
    author: str
    dependencies: List[str] = []  # 依赖的其他Provider（可选）
    configuration_schema: dict = {}  # 配置模式（可选）

class InputProvider(Protocol):
    """输入Provider接口"""

    def get_info(self) -> ProviderInfo:
        """获取Provider信息"""
        ...
```

### 3. 生命周期钩子实现示例

```python
class BilibiliDanmakuProvider:
    """B站弹幕Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.room_id = config.get("room_id")
        self.logger = get_logger("BilibiliDanmakuProvider")
        self._client = None
        self._running = False

    def get_info(self) -> ProviderInfo:
        return ProviderInfo(
            name="bilibili_danmaku",
            version="1.0.0",
            description="B站弹幕输入Provider",
            supported_data_types=["danmaku"],
            author="Official",
            dependencies=[],
            configuration_schema={
                "room_id": {"type": "string", "required": true}
            }
        )

    async def start(self) -> AsyncIterator[RawData]:
        """启动弹幕输入"""
        # 调用启动后钩子
        await self.on_start()

        self._running = True

        # 连接B站直播间
        from blivedm import BLiveClient

        async def on_danmaku(client, danmaku):
            yield RawData(
                content=danmaku.text,
                type="danmaku",
                source=self.get_info().name,
                metadata={
                    "user": danmaku.user_name,
                    "room_id": self.room_id
                }
            )

        self._client = BLiveClient(self.room_id)
        self._client.set_handler(on_danmaku)
        await self._client.connect()

        while self._running:
            await asyncio.sleep(1)

    async def stop(self):
        """停止弹幕输入"""
        self._running = False
        if self._client:
            await self._client.disconnect()

        # 调用停止后钩子
        await self.on_stop()

    async def cleanup(self):
        """清理Provider资源"""
        self.logger.info(f"Provider {self.get_info().name} cleanup")

    async def on_start(self):
        """启动后钩子"""
        self.logger.info(f"Provider {self.get_info().name} started")
        # 初始化连接前的工作
        pass

    async def on_stop(self):
        """停止后钩子"""
        self.logger.info(f"Provider {self.get_info().name} stopped")
        # 清理工作
        pass

    async def on_error(self, error: Exception):
        """错误处理钩子"""
        self.logger.error(f"Provider {self.get_info().name} error: {error}")
        # 错误处理逻辑
        pass
```

### 4. Provider配置示例

```toml
[providers]
# 自动重启失败的Provider（可选）
auto_restart = true

# 自动重启的间隔（秒）
restart_interval = 5
```

---

## ✅ 关键优势

### 1. 高并发性能
- ✅ 多个Provider同时运行，提高系统吞吐量
- ✅ 输入层可以同时采集多个数据源
- ✅ 输出层可以同时渲染到多个目标

### 2. 解耦性
- ✅ Provider间互不干扰，独立运行
- ✅ 通过EventBus松耦合通信
- ✅ 易于添加新的Provider

### 3. 可扩展性
- ✅ 新增输入源只需实现InputProvider
- ✅ 新增输出目标只需实现OutputProvider
- ✅ 支持社区开发者贡献Provider

### 4. 容错性
- ✅ 单个Provider失败不影响其他Provider
- ✅ 可以独立重启失败的Provider
- ✅ 系统更加健壮

---

## 🔗 相关文档

- [5层架构设计](./layer_refactoring.md)
- [决策层设计](./decision_layer.md)
- [核心重构设计](./core_refactoring.md)
