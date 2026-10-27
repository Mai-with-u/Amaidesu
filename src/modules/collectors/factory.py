"""采集器实例化工厂（配置名 → 具体类）

配置名 → 具体类的唯一映射在组件目录 ``src.modules.component_catalog``
的 ``COLLECTOR_BINDINGS``；本模块是其消费适配器——名单从目录派生，
实例化按目录条目加载实现类。供启动装配与 Dashboard 动态启停复用。

只管**通用采集器**。Agent 附属采集器（随所属 Agent 开关，例如 Minecraft 的身体事件流与聊天流）
不经本工厂，由 ``src.modules.agents.companions`` 按 Agent 配置构造。
"""

from __future__ import annotations

from typing import Any, Optional

from src.modules.collectors.base import BaseCollector
from src.modules.component_catalog import COLLECTOR_BINDINGS, COLLECTOR_NAMES

# 已实现的采集器注册名（组件目录表的名字派生量；兼容既有消费面的再导出）
SUPPORTED_COLLECTORS: tuple[str, ...] = COLLECTOR_NAMES


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
    binding = next((b for b in COLLECTOR_BINDINGS if b.name == name), None)
    if binding is None:
        raise RuntimeError(
            f"未知的 Collector 名称 '{name}'（合法名单：{list(SUPPORTED_COLLECTORS)}）。"
            f"collectors.toml 加载期已硬错拦截未知名，此处出现即程序缺陷（名单传递链路 bug）。"
        )
    # 加载器内部按需 import 采集器实现（依赖链重，仅在实例化该采集器时付出）
    collector_cls = binding.load_collector()
    return collector_cls(config=config or {}, event_bus=event_bus)


__all__ = ["SUPPORTED_COLLECTORS", "instantiate_collector"]
