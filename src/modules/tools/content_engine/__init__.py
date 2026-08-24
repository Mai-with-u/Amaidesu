"""
Amaidesu 内容引擎工具包（v2.0.0 / Wave 7）

按架构 §1.5.1 + §1.52 定案：
- **内容引擎 = 通用游戏控制器**（start/stop/status/send_input）
- 是控制面（control plane），不是游戏实现
- 游戏 Agent 通过它驱动具体游戏进程（MC / 文字冒险 / ...）

## 内容
- ``ContentEngine`` Protocol — 引擎接口（start/stop/send_input/status/get_state）
- ``ContentInput`` / ``ContentInputResult`` — 输入/响应数据类
- ``ContentEngineProvider`` ToolProvider — 把引擎封装成工具注册到 ToolRegistry
- ``StubContentEngine`` — 缺省 stub（无游戏进程时使用，记录所有 send_input 调用）
- ``FakeContentEngine`` — 测试用 fake（可注入预设响应）

注册示例：
```python
from src.modules.tools.content_engine import ContentEngineProvider

provider = ContentEngineProvider(engine=my_real_engine)
registry.register_provider(provider)
```
"""

from src.modules.tools.content_engine.provider import (
    ContentEngine,
    ContentEngineProvider,
    ContentEngineStatus,
    ContentInput,
    ContentInputResult,
    FakeContentEngine,
    StubContentEngine,
    build_content_engine_specs,
)

__all__ = [
    "ContentEngine",
    "ContentEngineStatus",
    "ContentInput",
    "ContentInputResult",
    "ContentEngineProvider",
    "StubContentEngine",
    "FakeContentEngine",
    "build_content_engine_specs",
]
