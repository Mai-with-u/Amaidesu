"""
console 采集器注册 + 基本 import 测试（Wave 5）

覆盖：
- ConsoleInputCollector 继承 BaseCollector
- 元数据（name/description）正确
- 旧 InputCollectorManager 兼容接口（start/stop/cleanup/stream/collect）存在
- v2 主动推事件：start() 开后台循环 → emit room.message.*
"""

import asyncio

from src.modules.collectors.base import BaseCollector
from src.modules.collectors.console import ConsoleInputCollector


class _FakeEventBus:
    """捕获 emit 的桩（替代真实 EventBus）。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def emit(self, event_name: str, payload, **kwargs):  # noqa: ANN001
        self.events.append((event_name, payload))


def test_console_inherits_base_collector() -> None:
    """ConsoleInputCollector 继承 BaseCollector（v2 框架）"""
    assert issubclass(ConsoleInputCollector, BaseCollector)


def test_console_metadata() -> None:
    """元数据正确"""
    assert ConsoleInputCollector.name == "console_input"
    assert "控制台" in ConsoleInputCollector.description


def test_console_has_old_compat_interface() -> None:
    """旧 InputCollectorManager 兼容接口存在"""
    methods = ("start", "stop", "cleanup", "stream", "collect")
    for m in methods:
        assert hasattr(ConsoleInputCollector, m), f"缺少兼容方法 {m}"


def test_console_config_defaults() -> None:
    """配置默认值"""
    cfg = ConsoleInputCollector.ConfigSchema()
    assert cfg.user_id == "console_user"
    assert cfg.user_nickname == "控制台"


def test_console_start_emits_danmaku_on_input() -> None:
    """start() 开后台循环：stdin 输入 → emit room.message.danmaku（v2 主动推）。"""
    import sys

    bus = _FakeEventBus()
    collector = ConsoleInputCollector(config={}, event_bus=bus)  # type: ignore[arg-type]

    sys.stdin_readline_orig = sys.stdin.readline
    input_lines = iter(["你好测试", ""])
    sys.stdin.readline = lambda: next(input_lines)  # type: ignore[assignment]

    try:
        async def run():
            await collector.start()
            for _ in range(20):
                if bus.events:
                    break
                await asyncio.sleep(0.01)
            await collector.stop()
            return bus.events

        events = asyncio.run(run())
    finally:
        sys.stdin.readline = sys.stdin_readline_orig  # type: ignore[assignment]

    assert len(events) >= 1
    event_name, payload = events[0]
    assert event_name == "room.message.danmaku"
    assert payload.content == "你好测试"  # type: ignore[attr-defined]
    assert payload.user.name == "控制台"  # type: ignore[attr-defined]
