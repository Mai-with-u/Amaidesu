"""
text_adv 内容引擎（包内私有）

定位：text_adv Agent 的内部件——引擎接口与实现约定在此包内，
不注册进工具注册表、不对其他 Agent 暴露；Agent 与其工具直接调用。

## 内容
- ``ContentEngine`` Protocol — 引擎接口（start/stop/send_input/status/get_state）
- ``ContentInput`` / ``ContentInputResult`` / ``ContentEngineStatus`` — 数据类
- ``StubContentEngine`` — 缺省 stub（无游戏进程时使用，记录所有 send_input 调用）
- ``FakeContentEngine`` — 测试用 fake（可注入预设响应）
"""

from src.agents.text_adv.content_engine.provider import (
    ContentEngine,
    ContentEngineStatus,
    ContentInput,
    ContentInputResult,
    FakeContentEngine,
    StubContentEngine,
)

__all__ = [
    "ContentEngine",
    "ContentEngineStatus",
    "ContentInput",
    "ContentInputResult",
    "StubContentEngine",
    "FakeContentEngine",
]
