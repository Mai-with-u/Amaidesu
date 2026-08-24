"""LiveStreamSimulator 占位 stub（Wave 6）

Wave 6 简化：``SimulatorService`` 不再需要完整 LLM 驱动的 LiveStreamSimulator 实现。
真实模拟（人设池/节奏生成/礼物生成）的复杂逻辑由 ``collectors/mock/`` 取代，
``SimulatorService`` 退化为一个简单的包装器（仍暴露 ``is_running`` /
``simulator`` 字段以保持 Dashboard API 向后兼容）。

注：完整功能（人设池 / LLM 驱动生成 / 礼物模拟）由 ``collectors/mock/``
``MockCollector`` 提供；本 stub 仅保留 ``start/collect/stop/cleanup/async for``
最小接口以兼容 ``SimulatorService`` 的 ``async for message in simulator.collect()`` 调用。
"""

from __future__ import annotations

from typing import Any, AsyncIterator


__all__ = ["LiveStreamSimulator"]


class LiveStreamSimulator:
    """最小 stub 实现（Wave 6）。

    原 LiveStreamSimulator 由 LLM 驱动生成模拟弹幕；Wave 6 该职责由
    ``collectors/mock/MockCollector`` 接管。本类保留 start/collect/stop 接口
    以兼容 ``SimulatorService`` 调用，但实际不产生任何消息（empty async iterator）。
    """

    def __init__(self, config: Any, **services: Any) -> None:
        # Wave 6：兼容 DI 注入额外服务（event_bus / llm 等）——stub 全部丢弃
        self._config = config
        self._services = services
        self._is_started = False

    async def setup(self) -> None:
        return None

    async def start(self) -> None:
        self._is_started = True

    async def stop(self) -> None:
        self._is_started = False

    async def cleanup(self) -> None:
        self._is_started = False

    async def collect(self) -> AsyncIterator[Any]:
        """返回空的 async iterator（Wave 6 stub）。

        真实模拟消息由 ``collectors/mock/MockCollector`` 经 ``room.message.*``
        事件发出；本 stub 仅保留接口契约。
        """
        if False:
            yield None
        return
        yield  # pragma: no cover
