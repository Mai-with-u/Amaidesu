"""采集器实例化工厂（配置名 → 具体类）

中央化 main.py 原有的 if/elif 实例化链（Single Source of Truth），
供启动装配与 Dashboard 动态启停复用。

配置名与类名映射（v2 命名）：
- bili_danmaku         → BiliDanmakuCollector（legacy）
- bili_danmaku_official→ BiliDanmakuOfficialCollector
- console_input        → ConsoleInputCollector
- mock_danmaku         → MockCollector
- read_pingmu          → ScreenChangeCollector
- stt                  → STTCollector
"""

from __future__ import annotations

from typing import Any, Optional

from src.modules.collectors.base import BaseCollector

# 已实现的采集器注册名（组件管理页"可用组件"清单来源）
SUPPORTED_COLLECTORS: tuple[str, ...] = (
    "bili_danmaku",
    "bili_danmaku_official",
    "console_input",
    "mock_danmaku",
    "read_pingmu",
    "stt",
)


def instantiate_collector(
    name: str,
    config: Optional[dict[str, Any]] = None,
    event_bus: Any = None,
    llm_manager: Any = None,
) -> Optional[BaseCollector]:
    """按名实例化采集器；未知名字返回 None。

    v2.0.9：新增可选 ``llm_manager`` 参数。仅 ``screen``（read_pingmu）需要
    LLMManager 调用 VLM；其余 collector 沿用事件总线即可。llm_manager 在 main.py
    装配时从步骤 1（llm_service）透传至屏幕采集器，避免 ScreenReader 自带 aiohttp
    绕过统一 profile 管理（D1 收编）。
    """
    if name == "bili_danmaku":
        from src.modules.collectors.bilibili.legacy.bili_danmaku_collector import BiliDanmakuCollector

        return BiliDanmakuCollector(config=config or {}, event_bus=event_bus)
    if name == "bili_danmaku_official":
        from src.modules.collectors.bilibili.official.bili_danmaku_official_collector import (
            BiliDanmakuOfficialCollector,
        )

        return BiliDanmakuOfficialCollector(config=config or {}, event_bus=event_bus)
    if name == "console_input":
        from src.modules.collectors.console.console_input_collector import ConsoleInputCollector

        return ConsoleInputCollector(config=config or {}, event_bus=event_bus)
    if name == "mock_danmaku":
        from src.modules.collectors.mock.mock_collector import MockCollector

        return MockCollector(config=config or {}, event_bus=event_bus)
    if name == "read_pingmu":
        from src.modules.collectors.screen.screen_change_collector import ScreenChangeCollector

        return ScreenChangeCollector(
            config=config or {},
            event_bus=event_bus,
            llm_manager=llm_manager,
        )
    if name == "stt":
        from src.modules.collectors.stt.stt_collector import STTCollector

        return STTCollector(config=config or {}, event_bus=event_bus)
    return None


__all__ = ["SUPPORTED_COLLECTORS", "instantiate_collector"]
