# Phase 4: 输出层实现 (Layer 5-6)

## 🎯 目标

实现：
1. **Layer 5: 表现生成层**：生成渲染参数
2. **Layer 6: 渲染呈现层**：多Provider并发渲染（字幕、TTS、VTS）

## 📁 目录结构

```
src/
├── expression/                          # Layer 5: 表现生成
│   ├── __init__.py
│   ├── expression_generator.py
│   ├── tts_module.py
│   └── action_mapper.py
│
└── rendering/                           # Layer 6: 渲染呈现
    ├── __init__.py
    ├── base_renderer.py
    ├── subtitle_renderer.py
    ├── audio_renderer.py
    ├── virtual_renderer.py
    └── renderer_factory.py
```

## 📝 实施内容

### 4.1 Layer 5: 表现生成层

#### Intent → RenderParameters

`src/expression/expression_generator.py`:
```python
from typing import Protocol
from src.understanding.response_parser import Intent

class RenderParameters:
    """渲染参数 - Layer 5的输出格式"""

    def __init__(
        self,
        expressions: dict = None,
        tts_text: str = "",
        subtitle_text: str = "",
        hotkeys: list = None,
        metadata: dict = None
    ):
        self.expressions = expressions or {}
        self.tts_text = tts_text
        self.subtitle_text = subtitle_text
        self.hotkeys = hotkeys or []
        self.metadata = metadata or {}

class ExpressionGenerator(Protocol):
    """表现生成器协议 - Layer 5"""

    async def generate(self, intent: Intent) -> RenderParameters:
        """从Intent生成RenderParameters"""
        ...
```

`src/expression/tts_module.py`:
```python
from src.expression.expression_generator import ExpressionGenerator, RenderParameters
from src.understanding.response_parser import Intent
from src.utils.logger import get_logger

class TTSModule:
    """TTS模块 - Layer 5"""

    def __init__(self, config: dict):
        self.config = config
        self.provider = config.get("default_provider", "edge")
        self.logger = get_logger("TTSModule")

    async def generate(self, intent: Intent) -> RenderParameters:
        """生成TTS参数"""
        self.logger.info(f"Generating TTS: {intent.response_text[:50]}...")

        return RenderParameters(
            tts_text=intent.response_text,
            subtitle_text=intent.response_text,
            metadata={"emotion": intent.emotion}
        )
```

### 4.2 Layer 6: 渲染呈现层

#### 多Provider并发渲染

`src/rendering/base_renderer.py`:
```python
from typing import Protocol
from src.expression.expression_generator import RenderParameters

class OutputRenderer(Protocol):
    """输出Renderer协议 - Layer 6

    多个Renderer可以并发运行，渲染到不同目标
    """
    async def setup(self, event_bus, config: dict):
        """设置Renderer（订阅EventBus）"""
        ...

    async def render(self, parameters: RenderParameters):
        """渲染输出"""
        ...

    async def cleanup(self):
        """清理资源"""
        ...
```

`src/rendering/subtitle_renderer.py`:
```python
from src.rendering.base_renderer import OutputRenderer
from src.expression.expression_generator import RenderParameters
from src.utils.logger import get_logger

class SubtitleRenderer:
    """字幕Renderer - Layer 6"""

    def __init__(self, config: dict):
        self.config = config
        self.font_size = config.get("font_size", 24)
        self.logger = get_logger("SubtitleRenderer")

    async def setup(self, event_bus, config: dict):
        """订阅RenderParameters事件"""
        self.event_bus = event_bus
        self.event_bus.on("expression.parameters_generated", self._on_parameters)
        self.logger.info("SubtitleRenderer subscribed to expression.parameters_generated")

    async def _on_parameters(self, event: dict):
        """处理RenderParameters事件"""
        params = event.get("data", {}).get("parameters")
        if params:
            await self.render(params)

    async def render(self, parameters: RenderParameters):
        """渲染字幕"""
        if parameters.subtitle_text:
            self.logger.info(f"Rendering subtitle: {parameters.subtitle_text[:50]}...")
            # 实际渲染逻辑

    async def cleanup(self):
        """清理资源"""
        if hasattr(self, 'event_bus'):
            self.event_bus.off("expression.parameters_generated", self._on_parameters)
```

`src/rendering/audio_renderer.py`:
```python
from src.rendering.base_renderer import OutputRenderer
from src.expression.expression_generator import RenderParameters
from src.utils.logger import get_logger

class AudioRenderer:
    """音频Renderer - Layer 6"""

    def __init__(self, config: dict):
        self.config = config
        self.provider = config.get("provider", "edge")
        self.voice = config.get("voice", "zh-CN-XiaoxiaoNeural")
        self.logger = get_logger("AudioRenderer")

    async def setup(self, event_bus, config: dict):
        """订阅RenderParameters事件"""
        self.event_bus = event_bus
        self.event_bus.on("expression.parameters_generated", self._on_parameters)
        self.logger.info("AudioRenderer subscribed to expression.parameters_generated")

    async def _on_parameters(self, event: dict):
        """处理RenderParameters事件"""
        params = event.get("data", {}).get("parameters")
        if params:
            await self.render(params)

    async def render(self, parameters: RenderParameters):
        """渲染音频（TTS）"""
        if parameters.tts_text:
            self.logger.info(f"Rendering TTS: {parameters.tts_text[:50]}...")
            # 调用TTS API生成音频
            # 播放音频

    async def cleanup(self):
        """清理资源"""
        if hasattr(self, 'event_bus'):
            self.event_bus.off("expression.parameters_generated", self._on_parameters)
```

`src/rendering/virtual_renderer.py`:
```python
from src.rendering.base_renderer import OutputRenderer
from src.expression.expression_generator import RenderParameters
from src.utils.logger import get_logger

class VirtualRenderer:
    """虚拟Renderer - Layer 6"""

    def __init__(self, config: dict):
        self.config = config
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 8001)
        self.logger = get_logger("VirtualRenderer")

    async def setup(self, event_bus, config: dict):
        """订阅RenderParameters事件"""
        self.event_bus = event_bus
        self.event_bus.on("expression.parameters_generated", self._on_parameters)
        self.logger.info("VirtualRenderer subscribed to expression.parameters_generated")

    async def _on_parameters(self, event: dict):
        """处理RenderParameters事件"""
        params = event.get("data", {}).get("parameters")
        if params:
            await self.render(params)

    async def render(self, parameters: RenderParameters):
        """渲染虚拟形象（VTS控制）"""
        if parameters.expressions:
            self.logger.info(f"Rendering virtual: {parameters.expressions}")
            # 调用VTS API控制虚拟形象

    async def cleanup(self):
        """清理资源"""
        if hasattr(self, 'event_bus'):
            self.event_bus.off("expression.parameters_generated", self._on_parameters)
```

## ✅ 验证标准

1. ✅ Intent可以正确转换为RenderParameters
2. ✅ SubtitleRenderer可以订阅并渲染字幕
3. ✅ AudioRenderer可以订阅并渲染音频（TTS）
4. ✅ VirtualRenderer可以订阅并控制虚拟形象
5. ✅ 多个Renderer可以并发运行

## 📝 提交

```bash
git add src/expression/ src/rendering/

git commit -m "feat(phase4): implement Layer 5-6 output rendering"
```
