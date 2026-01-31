# OutputProvider API

## 概述

OutputProvider 是输出 Provider 的抽象基类，用于将渲染参数输出到目标设备。

## 核心方法

### `_render_internal(self, parameters: RenderParameters) -> None`
渲染参数到目标设备。

**参数**:
- parameters: 渲染参数对象

**返回**: 无

**RenderParameters 结构**:
```python
@dataclass
class RenderParameters:
    text: str                 # 文本内容
    voice: Optional[str] = None     # 语音设置
    expression: Optional[Dict] = None  # 表情参数
    metadata: Dict[str, Any] = {}  # 元数据
```

**示例**:
```python
from src.core.providers.output_provider import OutputProvider
from .base import RenderParameters
from src.utils.logger import get_logger

class MyOutputProvider(OutputProvider):
    """简单的输出 Provider 示例"""

    async def _render_internal(self, parameters: RenderParameters):
        text = parameters.text
        print(f"渲染: {text}")
        
        # 模拟渲染延迟
        await asyncio.sleep(0.5)
```

### `_cleanup(self)`
清理资源。

**示例**:
```python
async def _cleanup(self):
    # 关闭设备连接
    if hasattr(self, 'device'):
        await self.device.close()
    
    self.logger.info("MyOutputProvider 清理完成")
```

## 属性

### `config: Dict[str, Any]`
Provider 配置。

### `logger: Logger`
Logger 实例。

### `event_bus: Optional[EventBus]`
事件总线实例（可选，用于事件通信）。

### `is_setup: bool`
是否已完成设置。

## 完整示例

### 文本输出 Provider

```python
from src.core.providers.output_provider import OutputProvider
from .base import RenderParameters
from src.utils.logger import get_logger

class TextOutputProvider(OutputProvider):
    """将文本输出到控制台的 Provider"""

    async def _render_internal(self, parameters: RenderParameters):
        text = parameters.text
        print(f"[{self.__class__.__name__}] {text}")

    async def _cleanup(self):
        self.logger.info("TextOutputProvider 清理完成")
```

### 带音频设备的输出 Provider

```python
import sounddevice
import asyncio
from src.core.providers.output_provider import OutputProvider
from .base import RenderParameters
from src.utils.logger import get_logger

class AudioOutputProvider(OutputProvider):
    """将文本转换为语音并播放的 Provider"""

    def __init__(self, config):
        super().__init__(config)
        self.device_index = config.get("device_index", None)

    async def _setup_internal(self):
        """初始化音频设备"""
        # 查找可用设备
        devices = sounddevice.query_devices()
        if self.device_index is None:
            self.device_index = devices[0]['index']
        
        await super()._setup_internal()
        self.logger.info(f"使用音频设备: {devices[self.device_index]['name']}")

    async def _render_internal(self, parameters: RenderParameters):
        text = parameters.text
        # 这里应该调用实际的 TTS 引擎
        # audio_data = await tts_engine.synthesize(text)
        # sounddevice.play(audio_data, device=self.device_index)
        
        # 模拟播放
        self.logger.info(f"播放语音: {text[:50]}...")

    async def _cleanup(self):
        self.logger.info("AudioOutputProvider 清理完成")
```

### 带 GUI 的输出 Provider

```python
import tkinter as tk
import asyncio
from src.core.providers.output_provider import OutputProvider
from .base import RenderParameters
from src.utils.logger import get_logger

class GUIOutputProvider(OutputProvider):
    """将文本显示在 GUI 窗口的 Provider"""

    def __init__(self, config):
        super().__init__(config)
        self.root = None
        self.label = None

    async def _setup_internal(self):
        """初始化 GUI"""
        self.root = tk.Tk()
        self.label = tk.Label(self.root, text="Ready")
        self.label.pack()
        
        await super()._setup_internal()
        self.logger.info("GUI 初始化完成")

    async def _render_internal(self, parameters: RenderParameters):
        """在 GUI 中显示文本"""
        self.label.config(text=parameters.text)

    async def _cleanup(self):
        """清理 GUI"""
        if self.root:
            self.root.destroy()
        
        self.logger.info("GUIOutputProvider 清理完成")
```

## 注意事项

1. **线程安全**: GUI 和音频设备通常需要在主线程操作
2. **资源管理**: 在 `_cleanup()` 中正确释放所有资源
3. **错误处理**: 所有设备操作都需要 try-except
4. **延迟考虑**: 渲染操作可能有延迟，需要异步处理

---

**相关文档**:
- [Plugin Protocol](./plugin_protocol.md)
- [InputProvider API](./input_provider.md)
- [EventBus API](./event_bus.md)
