"""BaseCollector v2 主动推事件模板测试（后台消费任务 + 自产自发）

覆盖：
1. ``_start_collect_task`` 后台消费 ``collect()`` 生成器
2. 采集器在 collect() 内自行 emit 事件（自产自发，基类零转换零兜底）
3. 不 emit 的采集器不产生任何事件（基类无兜底转发）
4. ``_stop_collect_task`` 取消任务
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from src.modules.collectors.base import BaseCollector
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser


class _SelfEmitCollector(BaseCollector):
    """在 collect() 内自行 emit 的测试采集器（自产自发）。"""

    name = "self_emit_test"

    def __init__(self, bus):
        self.messages: list[str] = []
        self.started = False
        self.stopped = False
        self.is_started = False
        self._bus = bus
        super().__init__(event_bus=bus)

    async def collect(self) -> AsyncIterator[RoomMessagePayload]:
        self.started = True
        while self.is_started:
            await asyncio.sleep(0.01)
            if not self.messages:
                break
            text = self.messages.pop(0)
            payload = RoomMessagePayload(
                message_type="danmaku",
                user=RoomMessageUser(id="u1", name="测试"),
                content=text,
            )
            await self.emit_event(CoreEvents.ROOM_MESSAGE_DANMAKU, payload)
            yield payload
        self.stopped = True


class _SilentCollector(BaseCollector):
    """不 emit 的测试采集器（基类不做任何兜底转发）。"""

    name = "silent_test"

    def __init__(self, bus):
        self.messages: list[str] = []
        self.is_started = False
        super().__init__(event_bus=bus)

    async def collect(self) -> AsyncIterator[RoomMessagePayload]:
        while self.is_started:
            await asyncio.sleep(0.01)
            if not self.messages:
                break
            yield RoomMessagePayload(
                message_type="danmaku",
                user=RoomMessageUser(id="u1", name="测试"),
                content=self.messages.pop(0),
            )


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def emit(self, event_name: str, payload, **kwargs):
        self.events.append((event_name, payload))


def test_self_emit_collector_emits_and_yields() -> None:
    """自产自发采集器：collect() 内 emit 的任务经后台消费到达总线。"""
    bus = _FakeBus()
    collector = _SelfEmitCollector(bus)
    collector.messages.append("第一条")

    async def run():
        collector.is_started = True
        await collector._start_collect_task()
        for _ in range(30):
            if collector.stopped:
                break
            await asyncio.sleep(0.01)
        await collector._stop_collect_task()

    asyncio.run(run())

    assert collector.started is True
    assert bus.events, "自产自发应发出事件"
    event_name, payload = bus.events[0]
    assert event_name == "room.message.danmaku"
    assert payload.content == "第一条"  # type: ignore[attr-defined]


def test_silent_collector_gets_no_fallback_emit() -> None:
    """不 emit 的采集器：基类无兜底转发，总线零事件。"""
    bus = _FakeBus()
    collector = _SilentCollector(bus)
    collector.messages.append("屏幕内容")

    async def run():
        collector.is_started = True
        await collector._start_collect_task()
        for _ in range(30):
            if collector._collect_task is not None and collector._collect_task.done():
                break
            await asyncio.sleep(0.01)
        await collector._stop_collect_task()

    asyncio.run(run())

    assert bus.events == [], "基类不应兜底转发（采集器自产自发）"


def test_manager_dynamic_register_start_stop() -> None:
    """CollectorManager 动态注册→启动→停止→注销全链（真实运行态）。"""
    from src.modules.collectors.manager import CollectorManager

    async def run():
        cm = CollectorManager()
        ok = await cm.enable_collector("console_input", {})
        assert ok is True
        assert "console_input" in cm.list_running()
        inst = cm.get_collector_by_name("console_input")
        assert inst is not None
        assert getattr(inst, "is_started", False) is True
        await cm.disable_collector("console_input")
        assert "console_input" not in cm.list_collectors()

    asyncio.run(run())
