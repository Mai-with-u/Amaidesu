"""采集器框架：基类后台消费 collect()（自产自发、无兜底转发）、CollectorManager 注册与健康、控制台采集器、工厂未知名硬错"""

from __future__ import annotations

import asyncio
import sys
from typing import AsyncIterator, Callable

import pytest

from src.modules.collectors import BaseCollector, CollectorManager
from src.modules.collectors.base import CollectorState
from src.modules.collectors.console import ConsoleInputCollector
from src.modules.collectors.console import console_input_collector as console_mod
from src.modules.collectors.factory import SUPPORTED_COLLECTORS, instantiate_collector
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def emit(self, event_name: str, payload: object, **kwargs: object) -> None:
        self.events.append((event_name, payload))


async def _until(condition: Callable[[], bool], timeout: float = 1.0) -> None:
    """轮询等待后台任务推进到条件成立；超时即由后续断言暴露。"""
    for _ in range(int(timeout / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)


class _ScriptedCollector(BaseCollector):
    """collect() 里逐条吐出脚本消息；emit=True 时自产自发（采集器自己 emit），否则只 yield（基类不兜底转发）。"""

    name = "scripted_test"

    def __init__(self, bus: _FakeBus, messages: list[str], *, emit: bool) -> None:
        super().__init__(event_bus=bus)
        self.messages = list(messages)
        self.emit = emit

    async def collect(self) -> AsyncIterator[RoomMessagePayload]:
        while self.messages:
            payload = RoomMessagePayload(
                message_type="danmaku", user=RoomMessageUser(id="u1", name="测试"), content=self.messages.pop(0)
            )
            if self.emit:
                await self.emit_event(CoreEvents.ROOM_MESSAGE_DANMAKU, payload)
            yield payload


@pytest.mark.parametrize(("emit", "expected_events"), [(True, 1), (False, 0)])
async def test_collect_task_drives_self_emitting_collectors_only(emit: bool, expected_events: int) -> None:
    bus = _FakeBus()
    collector = _ScriptedCollector(bus, ["第一条"], emit=emit)

    await collector._start_collect_task()
    await _until(lambda: collector._collect_task.done())
    await collector._stop_collect_task()

    assert len(bus.events) == expected_events  # 基类零转换零兜底：不 emit 的采集器总线零事件
    if emit:
        assert bus.events[0][0] == "room.message.danmaku" and bus.events[0][1].content == "第一条"


class _SampleCollector(BaseCollector):
    name = "sample_collector"
    description = "Sample Collector for testing"

    def __init__(self) -> None:
        super().__init__()
        self.started = self.stopped = 0

    async def _on_start(self) -> None:
        self.started += 1

    async def _on_stop(self) -> None:
        self.stopped += 1


@pytest.fixture
def manager() -> CollectorManager:
    return CollectorManager()


async def test_manager_registers_without_duplicates_and_batches_start_stop(manager: CollectorManager) -> None:
    sample = _SampleCollector()
    assert manager.register(sample) is True and manager.register(_SampleCollector()) is False  # 同名跳过
    assert manager.get("sample_collector") is sample
    assert "sample_collector" in manager and "absent" not in manager and len(manager) == 1
    assert manager.list_collectors() == ["sample_collector"] and manager.list_running() == []

    await manager.start_all()
    assert (sample.state, sample.started) == (CollectorState.RUNNING, 1)
    assert manager.is_all_healthy() is True and manager.get_unhealthy() == []
    assert manager.unregister("sample_collector") is False  # 运行中不可注销

    await manager.stop_all()
    assert (sample.state, sample.stopped) == (CollectorState.STOPPED, 1)
    assert manager.unregister("sample_collector") is True


def test_errored_collector_counts_as_unhealthy(manager: CollectorManager) -> None:
    broken = _SampleCollector()
    broken.name = "err"
    broken._state = CollectorState.ERRORED
    manager.register(broken)

    assert manager.is_all_healthy() is False and "err" in manager.get_unhealthy()


def _room_payload(message_type: str, user_id: str = "u1") -> RoomMessagePayload:
    return RoomMessagePayload(message_type=message_type, user=RoomMessageUser(id=user_id, name="测试"), content="内容")


async def test_publish_room_message_fans_out_by_message_type() -> None:
    """深入口按 message_type 选 CoreEvents 语义事件；返回值标记是否真的发布。"""
    cases = {
        "danmaku": CoreEvents.ROOM_MESSAGE_DANMAKU,
        "gift": CoreEvents.ROOM_MESSAGE_GIFT,
        "super_chat": CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
        "guard": CoreEvents.ROOM_MESSAGE_GUARD,
        "enter": CoreEvents.ROOM_MESSAGE_ENTER,
    }
    for message_type, event_name in cases.items():
        bus = _FakeBus()
        collector = _ScriptedCollector(bus, [], emit=False)
        assert await collector.publish_room_message(_room_payload(message_type)) is True
        assert bus.events == [(event_name, bus.events[0][1])]


async def test_publish_room_message_drops_unknown_type_and_missing_identity() -> None:
    """未知 message_type 记 warning 不抛异常；身份键缺失宁缺毋滥丢弃——两者都不发布。"""
    bus = _FakeBus()
    collector = _ScriptedCollector(bus, [], emit=False)

    # message_type 是 Literal、正常构造造不出未知值：绕过校验模拟字段落错/上游漂移
    unknown = RoomMessagePayload.model_construct(
        message_type="mystery_type", user=RoomMessageUser(id="u1", name="测试"), content="内容"
    )
    assert await collector.publish_room_message(unknown) is False
    assert await collector.publish_room_message(_room_payload("danmaku", user_id="")) is False
    assert bus.events == []


async def test_manager_enable_and_disable_a_collector_at_runtime(manager, monkeypatch: pytest.MonkeyPatch) -> None:
    # console_input 仅作链路载体：屏蔽交互终端检查（pytest 下 stdin 非 TTY 会自禁）
    monkeypatch.setattr(console_mod, "_stdin_interactive", lambda: True)
    monkeypatch.setattr(sys.stdin, "readline", lambda: "", raising=False)

    assert await manager.enable_collector("console_input", {}) is True
    assert "console_input" in manager.list_running()
    assert manager.get_collector_by_name("console_input") is not None

    await manager.disable_collector("console_input")
    assert "console_input" not in manager.list_collectors()


def test_console_collector_metadata_and_defaults() -> None:
    config = ConsoleInputCollector.ConfigSchema()
    assert issubclass(ConsoleInputCollector, BaseCollector) and ConsoleInputCollector.name == "console_input"
    assert "控制台" in ConsoleInputCollector.description
    assert all(hasattr(ConsoleInputCollector, method) for method in ("start", "stop", "cleanup", "collect"))
    assert (config.user_id, config.user_nickname) == ("console_user", "控制台")


async def test_console_stdin_line_becomes_a_danmaku_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(console_mod, "_stdin_interactive", lambda: True)
    lines = iter(["你好测试", ""])
    monkeypatch.setattr(sys.stdin, "readline", lambda: next(lines), raising=False)
    bus = _FakeBus()
    collector = ConsoleInputCollector(config={}, event_bus=bus)

    await collector.start()
    await _until(lambda: bus.events)
    await collector.stop()

    event_name, payload = bus.events[0]
    assert (event_name, payload.content, payload.user.name) == ("room.message.danmaku", "你好测试", "控制台")


@pytest.mark.parametrize("interactive", [False, True])
async def test_console_loop_stays_off_without_a_terminal_and_exits_on_eof(
    monkeypatch: pytest.MonkeyPatch, interactive: bool
) -> None:
    """stdin 非交互（重定向/后台）时不启动输入循环，防宿主命令文本被当弹幕；交互但 EOF 时循环退出并复位，不忙转。"""
    monkeypatch.setattr(console_mod, "_stdin_interactive", lambda: interactive)
    monkeypatch.setattr(sys.stdin, "readline", lambda: "", raising=False)
    bus = _FakeBus()
    collector = ConsoleInputCollector(config={}, event_bus=bus)

    await collector.start()
    await _until(lambda: not collector.is_started, timeout=0.5)

    assert collector.is_started is False and bus.events == []


@pytest.mark.parametrize("name", ["nonexistent_collector_xyz", "screen", ""])
def test_instantiating_an_unknown_collector_name_is_a_hard_error(name: str) -> None:
    """加载期已硬错拦截，走到这里就是程序缺陷；退役的 screen 与空串同属未知名。"""
    with pytest.raises(RuntimeError) as exc_info:
        instantiate_collector(name)

    message = str(exc_info.value)
    assert name in message and "console_input" in message and "程序缺陷" in message  # 点名 + 合法名单


def test_registered_names_exclude_retired_ones_and_known_names_instantiate_quietly(loguru_capture) -> None:
    assert "screen" not in SUPPORTED_COLLECTORS
    assert {
        "console_input",
        "bili_danmaku",
        "bili_danmaku_official",
        "bili_danmaku_laplace",
        "stt",
    } <= set(SUPPORTED_COLLECTORS)

    assert instantiate_collector("console_input").name == "console_input"
    assert not any(record["level"] == "WARNING" for record in loguru_capture.records)
