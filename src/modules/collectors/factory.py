"""采集器实例化工厂（配置名 → 具体类）

配置名 → 具体类的唯一映射，供启动装配与 Dashboard 动态启停复用。

配置名与类名映射：
- bili_danmaku         → BiliDanmakuCollector（legacy）
- bili_danmaku_official→ BiliDanmakuOfficialCollector
- console_input        → ConsoleInputCollector
- maicraft_attention   → MaicraftAttentionCollector
- stt                  → STTCollector
"""

from __future__ import annotations

from typing import Any, Optional

from src.modules.collectors.base import BaseCollector
from src.modules.logging import get_logger

_logger = get_logger(__name__)

# 已实现的采集器注册名
SUPPORTED_COLLECTORS: tuple[str, ...] = (
    "bili_danmaku",
    "bili_danmaku_official",
    "console_input",
    "maicraft_attention",
    "stt",
)


def instantiate_collector(
    name: str,
    config: Optional[dict[str, Any]] = None,
    event_bus: Any = None,
    llm_manager: Any = None,
) -> Optional[BaseCollector]:
    """按名实例化采集器；未知名字记录 warning 并返回 None。"""
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
    if name == "maicraft_attention":
        # 代码归属 Minecraft Agent 包（游戏相关外部世界适配器内聚），装配仍走本工厂
        from src.agents.minecraft.attention_collector import MaicraftAttentionCollector

        return MaicraftAttentionCollector(config=config or {}, event_bus=event_bus)
    if name == "stt":
        from src.modules.collectors.stt.stt_collector import STTCollector

        return STTCollector(config=config or {}, event_bus=event_bus)
    # 未知采集器名：跳过 + warning，不抛异常（允许配置残留段平滑过渡）
    _logger.warning(
        f"未知的 Collector 名称 '{name}'：未在 SUPPORTED_COLLECTORS 中找到对应实现，已跳过注册。"
        f"如该采集器已退役，可从 config/collectors.toml 的 enabled 列表移除。"
    )
    return None


__all__ = ["SUPPORTED_COLLECTORS", "instantiate_collector"]
