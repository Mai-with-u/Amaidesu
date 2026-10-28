"""
Amaidesu 视觉基础模块（基础设施 + 对外工具）

定位：
- 视觉模块 = 公用基础设施（任何 Agent 都可调用）
- 屏幕画面 = **快照型数据** → 同步工具（被调才看，不会"错过"）
- 流型数据（弹幕/STT）= 采集器（CollectBus 事件源），不在此

## 工具清单
- ``look_at_screen`` — 同步快照工具，截屏 + 可选文本识别

后端（屏幕采集 / OCR）通过 Protocol 注入；
未注入时**优雅降级**（返回空文本 + 警告 block，不抛异常）。

注册方式：
```python
from src.modules.vision import LookAtScreenProvider

provider = LookAtScreenProvider(screen_capture=my_capture, text_reader=my_reader)
registry.register_provider(provider)
```
"""

from src.modules.vision.capture_types import ScreenCapture, ScreenCaptureResult, TextReader
from src.modules.vision.look_at_screen import LookAtScreenProvider, build_look_at_screen_spec
from src.modules.vision.mss_capture import MonitorInfo, MssScreenCapture
from src.modules.vision.vlm_reader import LlmVisionTextReader

__all__ = [
    "LookAtScreenProvider",
    "ScreenCapture",
    "ScreenCaptureResult",
    "TextReader",
    "LlmVisionTextReader",
    "build_look_at_screen_spec",
    "MonitorInfo",
    "MssScreenCapture",
]
