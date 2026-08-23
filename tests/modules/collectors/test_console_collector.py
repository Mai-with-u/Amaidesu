"""
console 采集器注册 + 基本 import 测试（Wave 5）

覆盖：
- ConsoleInputCollector 继承 BaseCollector
- 元数据（name/description）正确
- 旧 InputCollectorManager 兼容接口（start/stop/cleanup/stream/collect）存在
"""

from src.modules.collectors.base import BaseCollector
from src.modules.collectors.console import ConsoleInputCollector


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
