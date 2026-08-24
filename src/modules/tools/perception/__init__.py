"""
Amaidesu 感知工具包（v2.0.0 / Wave 7）

按架构 §1.5 / §1.5.1 定案：
- 感知包 = 公用工具（任何 Agent 都可调用）
- 屏幕画面 = **快照型数据** → 同步工具（被调才看，不会"错过"）
- 流型数据（弹幕/STT）= 采集器（CollectBus 事件源），不在此

## 工具清单
- ``look_at_screen`` — 同步快照工具，截屏 + 可选文本识别

后端（屏幕采集 / OCR）通过 Protocol 注入；
未注入时**优雅降级**（返回空文本 + 警告 block，不抛异常）。

注册方式：
```python
from src.modules.tools.perception import LookAtScreenProvider

provider = LookAtScreenProvider(screen_capture=my_capture, text_reader=my_reader)
registry.register_provider(provider)
```
"""

from src.modules.tools.perception.look_at_screen import (
    FakeScreenCapture,
    FakeTextReader,
    LookAtScreenProvider,
    ScreenCapture,
    ScreenCaptureResult,
    TextReader,
    build_look_at_screen_spec,
)

__all__ = [
    "LookAtScreenProvider",
    "ScreenCapture",
    "ScreenCaptureResult",
    "TextReader",
    "FakeScreenCapture",
    "FakeTextReader",
    "build_look_at_screen_spec",
]
