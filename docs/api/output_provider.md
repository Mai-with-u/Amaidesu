# OutputProvider API

## 概述

`OutputProvider` 是输出域（Output Domain）的 Provider 抽象基类，负责将 `ExpressionParameters` 渲染到目标设备（如 TTS 播放、字幕显示、虚拟形象控制等）。

**位置**: `src/modules/types/base/output_provider.py`

## 核心概念

### 数据流

```
Decision Domain (Intent)
    ↓ EventBus: decision.intent
ExpressionGenerator
    ↓ EventBus: output.params
OutputProvider (订阅事件并渲染)
    ↓
目标设备 (TTS/字幕/VTS/OBS等)
```

### 事件驱动架构

OutputProvider 通过订阅 `output.params` 事件来接收渲染参数，而不是直接调用 `render()` 方法。

**推荐模式**:
```python
async def _setup_internal(self):
    if self.event_bus:
        self.event_bus.on(
            CoreEvents.OUTPUT_PARAMS,
            self._on_parameters_ready,
            priority=50
        )
```

## 生命周期方法

### `__init__(config: dict)`

初始化 Provider。

**参数**:
- `config`: Provider 配置字典（来自 `[providers.output.xxx]` 配置节）

**示例**:
```python
from src/modules/types/base/output_provider import OutputProvider
from src/modules/logging import get_logger

class MyOutputProvider(OutputProvider):
    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("MyOutputProvider")

        # 从配置读取参数
        self.device_id = config.get("device_id", "default")
```

### `setup(event_bus, dependencies: Optional[Dict[str, Any]] = None)`

设置 Provider，注册到 EventBus。

**参数**:
- `event_bus`: EventBus 实例
- `dependencies`: 可选的依赖注入字典

**调用时机**: 由 `ProviderManager` 在启动时自动调用

**不要直接调用此方法**，由框架管理。

### `_setup_internal()`

内部设置逻辑，子类可选重写。

**用途**:
- 初始化设备连接
- 订阅 EventBus 事件
- 加载资源

**示例**:
```python
from src/modules/events/names import CoreEvents

async def _setup_internal(self):
    """初始化音频设备并订阅事件"""
    # 初始化设备
    self.device = self._connect_device()
    if not self.device:
        raise RuntimeError("设备连接失败")

    # 订阅事件
    if self.event_bus:
        self.event_bus.on(
            CoreEvents.OUTPUT_PARAMS,
            self._on_parameters_ready,
            priority=50
        )

    self.logger.info("MyOutputProvider 设置完成")
```

### `render(parameters: ExpressionParameters)`

渲染参数到目标设备。

**参数**:
- `parameters`: `ExpressionParameters` 对象

**调用方式**:
- 通常在事件处理器中调用: `await self.render(event_data)`
- 也可以直接调用（不推荐）

**示例**:
```python
async def _on_parameters_ready(
    self,
    event_name: str,
    event_data: ExpressionParameters,
    source: str
):
    """处理参数生成事件"""
    # 检查是否启用当前功能
    if not event_data.tts_enabled or not event_data.tts_text:
        return

    try:
        await self.render(event_data)
    except Exception as e:
        self.logger.error(f"渲染失败: {e}", exc_info=True)
```

### `_render_internal(parameters: ExpressionParameters)`

内部渲染逻辑，**子类必须实现**。

**参数**:
- `parameters`: `ExpressionParameters` 对象

**示例**:
```python
from src/domains/output/parameters/render_parameters import ExpressionParameters

async def _render_internal(self, parameters: ExpressionParameters):
    """渲染 TTS 输出"""
    if not parameters.tts_enabled or not parameters.tts_text:
        self.logger.debug("TTS 未启用或文本为空，跳过")
        return

    text = parameters.tts_text
    self.logger.info(f"开始 TTS 渲染: '{text[:30]}...'")

    # 执行实际的 TTS 合成和播放
    await self._synthesize_and_play(text)
```

### `cleanup()`

清理资源，由 `ProviderManager` 在关闭时自动调用。

**不要直接调用此方法**。

### `_cleanup_internal()`

内部清理逻辑，子类可选重写。

**用途**:
- 关闭设备连接
- 取消事件订阅
- 释放资源

**示例**:
```python
from src/modules/events/names import CoreEvents

async def _cleanup_internal(self):
    """清理资源"""
    self.logger.info("正在清理 MyOutputProvider...")

    # 取消事件订阅
    if self.event_bus:
        try:
            self.event_bus.off(
                CoreEvents.OUTPUT_PARAMS,
                self._on_parameters_ready
            )
        except Exception as e:
            self.logger.warning(f"取消事件订阅失败: {e}")

    # 关闭设备连接
    if self.device:
        await self.device.close()
        self.device = None

    self.logger.info("MyOutputProvider 清理完成")
```

## ExpressionParameters 结构

`ExpressionParameters` 是从 ExpressionGenerator 传递到 OutputProvider 的完整参数对象。

**导入**:
```python
from src/domains/output/parameters/render_parameters import ExpressionParameters
# 或者使用别名（向后兼容）
from src.domains.output.parameters.render_parameters import RenderParameters
# RenderParameters = ExpressionParameters
```

**字段说明**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `tts_text` | `str` | `""` | TTS 文本内容 |
| `tts_enabled` | `bool` | `True` | 是否启用 TTS |
| `subtitle_text` | `str` | `""` | 字幕文本内容 |
| `subtitle_enabled` | `bool` | `True` | 是否启用字幕 |
| `expressions` | `Dict[str, float]` | `{}` | 表情参数字典 (如 `{"smile": 0.8}`) |
| `expressions_enabled` | `bool` | `True` | 是否启用表情 |
| `hotkeys` | `List[str]` | `[]` | 热键列表 (如 `["smile", "wave"]`) |
| `hotkeys_enabled` | `bool` | `True` | 是否启用热键 |
| `actions` | `List[Dict[str, Any]]` | `[]` | 动作列表 (如 `[{"type": "hotkey", "key": "smile"}]`) |
| `actions_enabled` | `bool` | `True` | 是否启用动作 |
| `metadata` | `Dict[str, Any]` | `{}` | 扩展元数据 |
| `priority` | `int` | `100` | 优先级（数字越小越优先） |
| `timestamp` | `float` | `time.time()` | 时间戳 |

**使用示例**:
```python
from src/domains/output/parameters/render_parameters import ExpressionParameters

async def _render_internal(self, parameters: ExpressionParameters):
    # TTS 相关
    if parameters.tts_enabled and parameters.tts_text:
        await self._play_tts(parameters.tts_text)

    # 字幕相关
    if parameters.subtitle_enabled and parameters.subtitle_text:
        await self._show_subtitle(parameters.subtitle_text)

    # 表情相关
    if parameters.expressions_enabled and parameters.expressions:
        await self._set_expressions(parameters.expressions)

    # 热键相关
    if parameters.hotkeys_enabled and parameters.hotkeys:
        await self._trigger_hotkeys(parameters.hotkeys)

    # 动作相关
    if parameters.actions_enabled and parameters.actions:
        await self._execute_actions(parameters.actions)

    # 元数据
    source = parameters.metadata.get("source", "unknown")
    priority = parameters.priority
```

## 配置验证（推荐）

使用 Pydantic `BaseProviderConfig` 进行配置验证，提供类型安全和 IDE 自动补全。

**示例**:
```python
from pydantic import Field
from src/modules/services/config/schemas/schemas/base import BaseProviderConfig

class MyOutputProvider(OutputProvider):

    class ConfigSchema(BaseProviderConfig):
        """MyOutputProvider 配置 Schema"""

        type: str = "my_output"

        # 设备配置
        device_id: str = Field(default="default", description="设备 ID")
        device_port: int = Field(default=8080, ge=1, le=65535, description="设备端口")

        # 行为配置
        timeout_seconds: int = Field(default=30, ge=1, le=300, description="超时时间")
        retry_count: int = Field(default=3, ge=0, le=10, description="重试次数")
        enabled: bool = Field(default=True, description="是否启用")

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("MyOutputProvider")

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        self.typed_config = self.ConfigSchema(**config)

        # 访问配置
        self.device_id = self.typed_config.device_id
        self.device_port = self.typed_config.device_port
        self.timeout_seconds = self.typed_config.timeout_seconds
        self.retry_count = self.typed_config.retry_count
```

## 注册 Provider

### 方式一：显式注册（推荐）

实现 `get_registration_info()` 类方法：

```python
from typing import Dict, Any

class MyOutputProvider(OutputProvider):

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """获取 Provider 注册信息"""
        return {
            "layer": "output",
            "name": "my_output",  # 唯一标识符
            "class": cls,
            "source": "builtin:my_output"
        }
```

在 `src/domains/output/providers/__init__.py` 中导入模块：
```python
from . import my_output  # noqa: F401
```

### 方式二：手动注册

在 `src/domains/output/providers/__init__.py` 中手动注册：
```python
from src/modules/providers/provider_registry import ProviderRegistry
from .my_output import MyOutputProvider

ProviderRegistry.register_output(
    name="my_output",
    provider_class=MyOutputProvider,
    source="builtin:my_output"
)
```

## 完整示例

### 示例 1：简单的 TTS Provider

```python
"""
简单的 TTS Provider 示例
"""

import asyncio
from typing import Optional, Dict, Any
from pydantic import Field

from src.modules.types.base.output_provider import OutputProvider
from src/domains/output/parameters/render_parameters import ExpressionParameters
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger
from src.modules.services.config.schemas.schemas.base import BaseProviderConfig


class SimpleTTSProvider(OutputProvider):
    """简单的 TTS Provider 示例"""

    class ConfigSchema(BaseProviderConfig):
        """TTS Provider 配置"""
        type: str = "simple_tts"
        voice: str = Field(default="zh-CN-XiaoxiaoNeural", description="语音")
        rate: str = Field(default="+0%", description="语速")

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("SimpleTTSProvider")

        # 验证配置
        self.typed_config = self.ConfigSchema(**config)
        self.voice = self.typed_config.voice
        self.rate = self.typed_config.rate

        # 音频锁（防止同时播放多个 TTS）
        self.tts_lock = asyncio.Lock()

    async def _setup_internal(self):
        """初始化 TTS 引擎"""
        # 模拟初始化
        self.logger.info(f"TTS 引擎初始化完成，语音: {self.voice}")

        # 订阅事件
        if self.event_bus:
            self.event_bus.on(
                CoreEvents.OUTPUT_PARAMS,
                self._on_parameters_ready,
                priority=50
            )
            self.logger.info("已订阅 output.params 事件")

    async def _render_internal(self, parameters: ExpressionParameters):
        """渲染 TTS 输出"""
        if not parameters.tts_enabled or not parameters.tts_text:
            self.logger.debug("TTS 未启用或文本为空，跳过")
            return

        text = parameters.tts_text
        self.logger.info(f"开始 TTS 渲染: '{text[:30]}...'")

        async with self.tts_lock:
            # 模拟 TTS 合成和播放
            await self._synthesize_and_play(text)

    async def _synthesize_and_play(self, text: str):
        """合成并播放语音（模拟）"""
        # 这里应该调用实际的 TTS 引擎
        self.logger.info(f"播放语音: {text}")

        # 模拟播放延迟
        await asyncio.sleep(1.0)

        self.logger.info("语音播放完成")

    async def _on_parameters_ready(
        self,
        event_name: str,
        event_data: ExpressionParameters,
        source: str
    ):
        """处理参数生成事件"""
        # 检查是否启用 TTS
        if not event_data.tts_enabled or not event_data.tts_text:
            return

        try:
            await self._render_internal(event_data)
        except Exception as e:
            self.logger.error(f"TTS 渲染失败: {e}", exc_info=True)

    async def _cleanup_internal(self):
        """清理资源"""
        self.logger.info("正在清理 SimpleTTSProvider...")

        # 取消事件订阅
        if self.event_bus:
            try:
                self.event_bus.off(
                    CoreEvents.OUTPUT_PARAMS,
                    self._on_parameters_ready
                )
            except Exception as e:
                self.logger.warning(f"取消事件订阅失败: {e}")

        self.logger.info("SimpleTTSProvider 清理完成")

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """获取 Provider 注册信息"""
        return {
            "layer": "output",
            "name": "simple_tts",
            "class": cls,
            "source": "builtin:simple_tts"
        }
```

### 示例 2：字幕 Provider（GUI 应用）

```python
"""
字幕 Provider 示例（CustomTkinter GUI）
"""

import asyncio
import queue
import threading
from typing import Optional, Dict, Any
from pydantic import Field

from src.modules.types.base.output_provider import OutputProvider
from src/domains/output/parameters/render_parameters import ExpressionParameters
from src/modules/events/names import CoreEvents
from src.modules.logging import get_logger
from src/modules/services/config/schemas/schemas/base import BaseProviderConfig


class SubtitleOutputProvider(OutputProvider):
    """字幕输出 Provider 示例"""

    class ConfigSchema(BaseProviderConfig):
        """字幕 Provider 配置"""
        type: str = "subtitle"

        # GUI 配置
        window_width: int = Field(default=800, ge=100, le=3840, description="窗口宽度")
        window_height: int = Field(default=100, ge=50, le=2160, description="窗口高度")
        font_size: int = Field(default=28, ge=10, le=100, description="字体大小")
        text_color: str = Field(default="white", description="文字颜色")

        # 行为配置
        fade_delay_seconds: int = Field(default=5, ge=0, le=300, description="淡出延迟")
        auto_hide: bool = Field(default=True, description="是否自动隐藏")

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("SubtitleOutputProvider")

        # 验证配置
        self.typed_config = self.ConfigSchema(**config)

        # GUI 配置
        self.window_width = self.typed_config.window_width
        self.window_height = self.typed_config.window_height
        self.font_size = self.typed_config.font_size
        self.text_color = self.typed_config.text_color
        self.fade_delay_seconds = self.typed_config.fade_delay_seconds
        self.auto_hide = self.typed_config.auto_hide

        # 线程和队列
        self.text_queue = queue.Queue()
        self.gui_thread: Optional[threading.Thread] = None
        self.root = None
        self.is_running = True

    async def _setup_internal(self):
        """初始化 GUI"""
        # 订阅事件
        if self.event_bus:
            self.event_bus.on(
                CoreEvents.OUTPUT_PARAMS,
                self._on_parameters_ready,
                priority=50
            )
            self.logger.info("已订阅 output.params 事件")

        # 启动 GUI 线程（GUI 必须在主线程）
        self.gui_thread = threading.Thread(target=self._run_gui, daemon=True)
        self.gui_thread.start()

        self.logger.info("字幕 Provider 初始化完成")

    async def _render_internal(self, parameters: ExpressionParameters):
        """渲染字幕"""
        if not parameters.subtitle_enabled:
            return

        # 使用 subtitle_text 或回退到 tts_text
        text = parameters.subtitle_text or parameters.tts_text or ""
        if not text:
            return

        self.logger.debug(f"收到字幕渲染请求: {text[:30]}...")

        # 将文本放入队列（GUI 线程会处理）
        try:
            self.text_queue.put(text)
        except Exception as e:
            self.logger.error(f"放入字幕队列时出错: {e}", exc_info=True)

    async def _on_parameters_ready(
        self,
        event_name: str,
        event_data: ExpressionParameters,
        source: str
    ):
        """处理参数生成事件"""
        try:
            await self._render_internal(event_data)
        except Exception as e:
            self.logger.error(f"字幕渲染失败: {e}", exc_info=True)

    def _run_gui(self):
        """运行 GUI 线程"""
        try:
            import tkinter as tk

            self.root = tk.Tk()
            self.root.title("字幕")
            self.root.geometry(f"{self.window_width}x{self.window_height}")

            # 创建文本标签
            label = tk.Label(
                self.root,
                text="",
                font=("Microsoft YaHei UI", self.font_size),
                fg=self.text_color,
                bg="black"
            )
            label.pack(expand=True, fill="both")

            # 定时检查队列
            def check_queue():
                if not self.is_running:
                    return

                try:
                    while not self.text_queue.empty():
                        text = self.text_queue.get_nowait()
                        label.config(text=text)
                        self.logger.debug(f"已更新字幕: {text[:30]}...")
                except queue.Empty:
                    pass

                if self.is_running:
                    self.root.after(100, check_queue)

            check_queue()
            self.root.mainloop()

        except Exception as e:
            self.logger.error(f"运行 GUI 时出错: {e}", exc_info=True)
        finally:
            self.is_running = False

    async def _cleanup_internal(self):
        """清理资源"""
        self.logger.info("正在清理 SubtitleOutputProvider...")
        self.is_running = False

        # 等待 GUI 线程结束
        if self.gui_thread and self.gui_thread.is_alive():
            self.gui_thread.join(timeout=3.0)

        self.logger.info("SubtitleOutputProvider 清理完成")

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """获取 Provider 注册信息"""
        return {
            "layer": "output",
            "name": "subtitle",
            "class": cls,
            "source": "builtin:subtitle"
        }
```

### 示例 3：虚拟形象 Provider

```python
"""
虚拟形象 Provider 示例（VTS/VRChat）
"""

from typing import Optional, Dict, Any
from pydantic import Field

from src.modules.types.base.output_provider import OutputProvider
from src/domains/output/parameters/render_parameters import ExpressionParameters
from src/modules/events/names import CoreEvents
from src.modules.logging import get_logger
from src/modules/services/config/schemas/schemas/base import BaseProviderConfig


class AvatarOutputProvider(OutputProvider):
    """虚拟形象输出 Provider 示例"""

    class ConfigSchema(BaseProviderConfig):
        """虚拟形象 Provider 配置"""
        type: str = "avatar"

        # 平台配置
        platform: str = Field(
            default="vts",
            pattern=r"^(vts|vrchat|live2d)$",
            description="虚拟形象平台"
        )
        host: str = Field(default="localhost", description="主机地址")
        port: int = Field(default=8001, ge=1, le=65535, description="端口")

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("AvatarOutputProvider")

        # 验证配置
        self.typed_config = self.ConfigSchema(**config)

        self.platform = self.typed_config.platform
        self.host = self.typed_config.host
        self.port = self.typed_config.port

        # 连接状态
        self.client = None
        self.is_connected = False

    async def _setup_internal(self):
        """初始化连接"""
        # 模拟连接
        self.logger.info(f"连接到 {self.platform} @ {self.host}:{self.port}")
        # self.client = await self._connect()
        self.is_connected = True

        # 订阅事件
        if self.event_bus:
            self.event_bus.on(
                CoreEvents.OUTPUT_PARAMS,
                self._on_parameters_ready,
                priority=50
            )
            self.logger.info("已订阅 output.params 事件")

    async def _render_internal(self, parameters: ExpressionParameters):
        """渲染表情参数"""
        if not self.is_connected:
            self.logger.warning("未连接到虚拟形象平台，跳过渲染")
            return

        # 表情参数
        if parameters.expressions_enabled and parameters.expressions:
            await self._set_expressions(parameters.expressions)

        # 热键
        if parameters.hotkeys_enabled and parameters.hotkeys:
            await self._trigger_hotkeys(parameters.hotkeys)

    async def _set_expressions(self, expressions: Dict[str, float]):
        """设置表情参数"""
        self.logger.debug(f"设置表情参数: {expressions}")
        # await self.client.set_parameters(expressions)

    async def _trigger_hotkeys(self, hotkeys: list):
        """触发热键"""
        self.logger.debug(f"触发热键: {hotkeys}")
        # for hotkey in hotkeys:
        #     await self.client.trigger_hotkey(hotkey)

    async def _on_parameters_ready(
        self,
        event_name: str,
        event_data: ExpressionParameters,
        source: str
    ):
        """处理参数生成事件"""
        try:
            await self._render_internal(event_data)
        except Exception as e:
            self.logger.error(f"虚拟形象渲染失败: {e}", exc_info=True)

    async def _cleanup_internal(self):
        """清理连接"""
        self.logger.info("正在清理 AvatarOutputProvider...")

        # 取消事件订阅
        if self.event_bus:
            try:
                self.event_bus.off(
                    CoreEvents.OUTPUT_PARAMS,
                    self._on_parameters_ready
                )
            except Exception as e:
                self.logger.warning(f"取消事件订阅失败: {e}")

        # 关闭连接
        if self.client:
            # await self.client.close()
            self.client = None
        self.is_connected = False

        self.logger.info("AvatarOutputProvider 清理完成")

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """获取 Provider 注册信息"""
        return {
            "layer": "output",
            "name": "avatar",
            "class": cls,
            "source": "builtin:avatar"
        }
```

## 事件订阅模式

### 推荐模式：订阅 `output.params`

这是最常见的方式，Provider 会在参数生成后自动渲染。

```python
async def _setup_internal(self):
    if self.event_bus:
        self.event_bus.on(
            CoreEvents.OUTPUT_PARAMS,
            self._on_parameters_ready,
            priority=50
        )

async def _on_parameters_ready(
    self,
    event_name: str,
    event_data: ExpressionParameters,
    source: str
):
    """处理参数生成事件"""
    # 检查是否启用当前功能
    if not event_data.tts_enabled:
        return

    try:
        await self._render_internal(event_data)
    except Exception as e:
        self.logger.error(f"渲染失败: {e}", exc_info=True)
```

### 替代模式：订阅特定渲染事件

某些 Provider 可能订阅更具体的事件（如 `RENDER_SUBTITLE`）：

```python
async def _setup_internal(self):
    if self.event_bus:
        self.event_bus.on(
            CoreEvents.RENDER_SUBTITLE,
            self._handle_render_request,
            priority=50
        )

async def _handle_render_request(
    self,
    event_name: str,
    data: ExpressionParameters,
    source: str
):
    """处理字幕渲染请求"""
    await self.render(data)
```

## 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `config` | `dict` | Provider 配置字典（原始配置） |
| `event_bus` | `EventBus | None` | EventBus 实例 |
| `is_setup` | `bool` | 是否已完成设置 |
| `_dependencies` | `Dict[str, Any]` | 依赖注入字典 |

## 方法

| 方法 | 类型 | 说明 |
|------|------|------|
| `setup(event_bus, dependencies)` | 异步 | 设置 Provider（框架调用） |
| `_setup_internal()` | 异步 | 内部设置逻辑（子类可选重写） |
| `render(parameters)` | 异步 | 渲染参数 |
| `_render_internal(parameters)` | 异步 | 内部渲染逻辑（子类必须实现） |
| `cleanup()` | 异步 | 清理资源（框架调用） |
| `_cleanup_internal()` | 异步 | 内部清理逻辑（子类可选重写） |
| `get_info()` | 同步 | 获取 Provider 信息 |
| `get_registration_info()` | 类方法 | 获取注册信息（子类必须实现） |

## 最佳实践

### 1. 配置验证

使用 `ConfigSchema` 进行配置验证，提供类型安全和 IDE 自动补全：

```python
class ConfigSchema(BaseProviderConfig):
    type: str = "my_provider"
    timeout: int = Field(default=30, ge=1, le=300, description="超时时间")

def __init__(self, config: dict):
    super().__init__(config)
    self.typed_config = self.ConfigSchema(**config)
    self.timeout = self.typed_config.timeout
```

### 2. 事件驱动

订阅 `output.params` 事件，而不是直接调用 `render()`：

```python
async def _setup_internal(self):
    if self.event_bus:
        self.event_bus.on(
            CoreEvents.OUTPUT_PARAMS,
            self._on_parameters_ready,
            priority=50
        )
```

### 3. 错误处理

在事件处理器中捕获异常，避免影响其他 Provider：

```python
async def _on_parameters_ready(self, event_name: str, event_data: ExpressionParameters, source: str):
    try:
        await self._render_internal(event_data)
    except Exception as e:
        self.logger.error(f"渲染失败: {e}", exc_info=True)
```

### 4. 资源清理

在 `_cleanup_internal()` 中正确释放所有资源：

```python
async def _cleanup_internal(self):
    # 取消事件订阅
    if self.event_bus:
        self.event_bus.off(CoreEvents.OUTPUT_PARAMS, self._on_parameters_ready)

    # 关闭连接
    if self.client:
        await self.client.close()

    # 释放资源
    self.client = None
```

### 5. 条件渲染

检查对应的 `enabled` 字段，避免不必要的渲染：

```python
async def _render_internal(self, parameters: ExpressionParameters):
    # 检查是否启用
    if not parameters.tts_enabled or not parameters.tts_text:
        self.logger.debug("TTS 未启用或文本为空，跳过")
        return

    # 执行渲染
    await self._play_tts(parameters.tts_text)
```

### 6. 使用锁

对于有状态的 Provider（如 TTS），使用锁避免并发问题：

```python
import asyncio

class TTSProvider(OutputProvider):
    def __init__(self, config: dict):
        super().__init__(config)
        self.tts_lock = asyncio.Lock()

    async def _render_internal(self, parameters: ExpressionParameters):
        async with self.tts_lock:
            await self._synthesize_and_play(parameters.tts_text)
```

### 7. GUI 线程

GUI 应用（如字幕）需要单独的线程，注意线程安全：

```python
import queue
import threading

class SubtitleOutputProvider(OutputProvider):
    def __init__(self, config: dict):
        super().__init__(config)
        self.text_queue = queue.Queue()
        self.gui_thread = None

    async def _setup_internal(self):
        # 启动 GUI 线程
        self.gui_thread = threading.Thread(target=self._run_gui, daemon=True)
        self.gui_thread.start()

    async def _render_internal(self, parameters: ExpressionParameters):
        # 将文本放入队列（线程安全）
        self.text_queue.put(parameters.subtitle_text)
```

## 注意事项

1. **事件订阅**: 必须在 `_setup_internal()` 中订阅事件，在 `_cleanup_internal()` 中取消订阅
2. **资源管理**: 在 `_cleanup_internal()` 中释放所有资源，避免内存泄漏
3. **错误处理**: 在事件处理器中捕获异常，避免影响其他 Provider
4. **线程安全**: GUI 应用需要单独的线程，使用队列进行线程间通信
5. **配置验证**: 使用 `ConfigSchema` 进行配置验证，提供类型安全
6. **条件渲染**: 检查对应的 `enabled` 字段，避免不必要的渲染
7. **并发控制**: 使用锁避免并发问题（如 TTS 同时播放多个语音）

## 相关文档

- [Provider 开发指南](../development/provider.md)
- [3域架构](../architecture/overview.md)
- [事件系统](../architecture/event-system.md)
- [数据流规则](../architecture/data-flow.md)
- [InputProvider API](./input_provider.md)
- [DecisionProvider API](./decision_provider.md)
