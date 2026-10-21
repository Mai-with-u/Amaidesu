"""采集器实例化工厂（配置名 → 具体类）

配置名 → 具体类的唯一映射，供启动装配与 Dashboard 动态启停复用。

配置名与类名映射：
- bili_danmaku         → BiliDanmakuCollector（legacy）
- bili_danmaku_official→ BiliDanmakuOfficialCollector
- bili_danmaku_laplace → BiliDanmakuLaplaceCollector
- console_input        → ConsoleInputCollector
- live_frame           → LiveFrameCollector
- stt                  → STTCollector

只管**通用采集器**。Agent 附属采集器（随所属 Agent 开关，例如 Minecraft 的身体事件流与聊天流）
不经本工厂，由 ``src.modules.agents.companions`` 按 Agent 配置构造。
"""

from __future__ import annotations

from typing import Any, Optional

from src.modules.collectors.base import BaseCollector

# 已实现的采集器注册名
SUPPORTED_COLLECTORS: tuple[str, ...] = (
    "bili_danmaku",
    "bili_danmaku_official",
    "bili_danmaku_laplace",
    "console_input",
    "live_frame",
    "stt",
)


def instantiate_collector(
    name: str,
    config: Optional[dict[str, Any]] = None,
    event_bus: Any = None,
    llm_manager: Any = None,
) -> BaseCollector:
    """按名实例化采集器。

    未知名属程序缺陷而非合法输入：collectors.toml 的未知名在加载期
    已被硬错拦截，能到达此处的名字必然在 SUPPORTED_COLLECTORS 内
    （Dashboard 动态启停的 API 入口另行校验用户输入）。
    """
    if name == "bili_danmaku":
        # 函数内 import：具体采集器实现携带各自依赖（网络栈/音频栈等），仅在实例化该采集器时加载
        from src.modules.collectors.bilibili.legacy.bili_danmaku_collector import BiliDanmakuCollector

        return BiliDanmakuCollector(config=config or {}, event_bus=event_bus)
    if name == "bili_danmaku_official":
        # 函数内 import：同上，仅在实例化该采集器时加载
        from src.modules.collectors.bilibili.official.bili_danmaku_official_collector import (
            BiliDanmakuOfficialCollector,
        )

        return BiliDanmakuOfficialCollector(config=config or {}, event_bus=event_bus)
    if name == "bili_danmaku_laplace":
        # 函数内 import：同上，仅在实例化该采集器时加载
        from src.modules.collectors.bilibili.laplace import BiliDanmakuLaplaceCollector

        return BiliDanmakuLaplaceCollector(config=config or {}, event_bus=event_bus)
    if name == "console_input":
        # 函数内 import：同上，仅在实例化该采集器时加载
        from src.modules.collectors.console.console_input_collector import ConsoleInputCollector

        return ConsoleInputCollector(config=config or {}, event_bus=event_bus)
    if name == "live_frame":
        # 函数内 import：同上，仅在实例化该采集器时加载
        from src.modules.collectors.frame.live_frame_collector import LiveFrameCollector

        return LiveFrameCollector(config=config or {}, event_bus=event_bus)
    if name == "stt":
        # 函数内 import：同上，仅在实例化该采集器时加载
        from src.modules.collectors.stt.stt_collector import STTCollector

        return STTCollector(config=config or {}, event_bus=event_bus)
    raise RuntimeError(
        f"未知的 Collector 名称 '{name}'（合法名单：{list(SUPPORTED_COLLECTORS)}）。"
        f"collectors.toml 加载期已硬错拦截未知名，此处出现即程序缺陷（名单传递链路 bug）。"
    )


__all__ = ["SUPPORTED_COLLECTORS", "instantiate_collector"]
