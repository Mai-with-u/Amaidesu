"""SimulatorService 集成：setup 读配置与启停、模拟标记、取消透传、回放收场与启动顺序、LLM 致命错误收场、付费概率路径

LLM 经 services_by_type 用 duck-type 假服务注入（SimulatorService._find_llm_service 按 generate/setup 属性检测）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.llm.payload import Response, Usage
from src.modules.simulator import SimulatorService
from src.modules.storage.database import SQLiteDatabase
from tests.modules.simulator.helpers import FakeConfigService, FakeLLMService, enabled_config

DANMAKU_FAMILY = (
    CoreEvents.ROOM_MESSAGE_DANMAKU,
    CoreEvents.ROOM_MESSAGE_GIFT,
    CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
    CoreEvents.ROOM_MESSAGE_GUARD,
)


class _FatalLLMService(FakeLLMService):
    """恒返不可恢复错误（HTTP 402）。"""

    async def generate(self, prompt: str, **kwargs: Any) -> Any:
        # 统一假件的计数走 calls 列表（chat_calls 为只读视图），这里手动登记本次调用
        self.calls.append({"method": "generate", "input": prompt})
        error = "全部模型失败 ['deepseek-flash']: FatalError: 请求被服务端拒绝（HTTP 402）"
        return Response(success=False, content="", error=error, usage=Usage(total_tokens=0))


class _FakeSessionManager:
    """记录调用顺序的场次管理器，供启动顺序断言。"""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self.opened_pk: Optional[int] = None

    async def open_session(self, *, title: str, source: str) -> int:
        self.calls.append(f"open:{source}")
        self.opened_pk = 42
        return 42

    async def close_session(self, *, reason: str) -> None:
        self.calls.append("close")

    async def resolve_pk(self) -> int:
        return 42


FIXED = {"cadence_mode": "fixed", "fixed_interval_s": 1.0}  # 节奏字段约束 ge=1.0


def _service(
    store: SQLiteDatabase,
    bus: Optional[EventBus] = None,
    llm: Optional[FakeLLMService] = None,
    session_manager: Any = None,
) -> tuple[SimulatorService, FakeLLMService]:
    llm = llm or FakeLLMService()
    service = SimulatorService(
        event_bus=bus or EventBus(),
        sim_repo=store.sim,
        chat_repo=store.chat,
        services_by_type={type(llm): llm},
        session_manager=session_manager,
    )
    return service, llm


def _record_messages(bus: EventBus, session_manager: Any = None) -> list:
    """订阅弹幕族事件；有场次管理器时同时记录 emit 瞬间场次是否已开启。"""
    received: list = []

    async def capture(event_name: str, payload: Any, source: Optional[str] = None) -> None:
        if isinstance(payload, RoomMessagePayload):
            received.append((payload, session_manager is None or session_manager.opened_pk is not None))

    for event in DANMAKU_FAMILY:
        bus.on(event, capture, model_class=RoomMessagePayload)
    return received


async def _wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)


async def _seed_chat(store: SQLiteDatabase, date_str: str, rows: int) -> None:
    """种入同一本地日期的连续弹幕（相邻 1 秒）。"""
    base_ms = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)
    for i in range(rows):
        await store.chat.insert_live_chat(
            live_session_id=1,
            timestamp_ms=base_ms + i * 1000,
            sender_role="viewer",
            sender_id=f"uid_{i}",
            sender_name=f"观众{i}",
            content=f"第{i}条",
            message_type="danmaku",
            simulated=False,
        )


async def test_setup_starts_only_when_enabled_and_auto_start(store: SQLiteDatabase) -> None:
    assert SimulatorService(event_bus=EventBus()).is_running is False  # 构造不启动

    disabled = SimulatorService(event_bus=EventBus(), services_by_type={})  # enabled=false 零 LLM 依赖
    await disabled.setup(FakeConfigService({"enabled": False}))
    assert disabled.is_running is False

    service, llm = _service(store)
    await service.setup(enabled_config(), auto_start=False)
    assert service.is_running is False and llm.chat_calls == 0


async def test_enabled_emits_danmaku_marked_as_simulated(store: SQLiteDatabase) -> None:
    bus = EventBus()
    received = _record_messages(bus)
    service, _ = _service(store, bus, FakeLLMService(reply="测试弹幕", tokens=5))
    await service.setup(enabled_config(**FIXED, gift_probability=0.0, warmup_duration_s=0.0))  # 关礼物，只走弹幕
    try:
        await _wait_until(lambda: bool(received), 3.0)
        assert service.is_running is True and received
        payload = received[0][0]
        assert payload.simulated is True and payload.message_type == "danmaku" and payload.content
    finally:
        await service.cleanup()  # 必须清理，否则任务残留


async def test_stop_and_cleanup_are_idempotent(store: SQLiteDatabase) -> None:
    idle = SimulatorService(event_bus=EventBus())
    await idle.stop()
    await idle.cleanup()
    await idle.cleanup()
    assert idle.is_running is False

    service, _ = _service(store)
    await service.setup(enabled_config(**FIXED))
    assert service.is_running is True
    await service.stop()
    assert service.is_running is False
    await service.cleanup()
    await service.cleanup()
    assert service._llm_wrapper is None and service._persona_pool is None  # 子系统已清空


async def test_stop_propagates_outer_cancellation(store: SQLiteDatabase) -> None:
    service, _ = _service(store)
    await service.setup(enabled_config(**FIXED))
    current = asyncio.current_task()
    assert current is not None
    current.cancel()  # 模拟 stop() 期间外层被取消
    with pytest.raises(asyncio.CancelledError):
        await service.stop()  # 不能被吞成正常返回


async def test_run_loop_reraises_cancellation(store: SQLiteDatabase) -> None:
    service, _ = _service(store)
    await service.setup(enabled_config(**FIXED))
    assert service._task is not None
    service._task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await service._task  # 主循环捕获取消后必须重新抛出，保持任务取消语义
    await service.cleanup()


async def test_replay_finishes_naturally_opens_session_first_and_is_restartable(store: SQLiteDatabase) -> None:
    bus = EventBus()
    sessions = _FakeSessionManager()
    received = _record_messages(bus, sessions)
    await _seed_chat(store, "2026-09-01", rows=2)
    service, _ = _service(store, bus, session_manager=sessions)
    await service.setup(enabled_config(mode="replay", replay_date="2026-09-01", replay_speed=100.0))  # 近似全速回放
    try:
        assert service.is_running is True  # 默认日期就绪，setup 自动启动回放
        await _wait_until(lambda: not service.is_running)

        assert len(received) == 2 and all(session_open for _, session_open in received)  # 场次先于首条消息开启
        assert "close" in sessions.calls  # 自然收场关闭回放场次
        assert (service.is_running, service._active_mode, service._task) == (False, "off", None)

        await service.start()  # 落场后可立即再启动（重建回放队列）
        assert service.is_running is True
    finally:
        await service.cleanup()


async def test_start_reports_rejection_reason_and_single_message_replay_finishes_instantly(
    store: SQLiteDatabase,
) -> None:
    service, _ = _service(store)
    await service.setup(enabled_config(mode="replay"))
    try:
        no_date = await service.start()
        assert no_date is not None and "日期" in no_date and service.is_running is False
        empty = await service.start(replay_date="1999-01-01")
        assert empty is not None and "无可回放" in empty and service.is_running is False

        await _seed_chat(store, "2026-09-03", rows=1)
        assert await service.start(replay_date="2026-09-03") is None
        assert service.replay_engine is not None and service.replay_engine.total == 1
        await _wait_until(lambda: not service.is_running)
        # 启动即完成后不得挂运行态，否则 API 会误报启动失败
        assert service.is_running is False and service.mode == "off"
    finally:
        await service.cleanup()


async def test_fatal_llm_error_stops_loop_and_records_last_error(store: SQLiteDatabase) -> None:
    service, fatal = _service(store, llm=_FatalLLMService())
    await service.setup(enabled_config(**FIXED, gift_probability=0.0, warmup_duration_s=0.0))
    try:
        await _wait_until(lambda: not service.is_running, 8.0)
        assert service.is_running is False and service.mode == "off"  # 不得无限重试
        assert service.last_error is not None and "FatalError" in service.last_error
        assert fatal.chat_calls == 1  # 不可恢复错误只允许调用一次
    finally:
        await service.cleanup()


@pytest.mark.parametrize(
    ("rolls", "overrides", "message_type", "payload_field"),
    [
        ([0.1, 0.01], {"guard_probability": 1.0}, "guard", "guard"),  # pay_roll 命中上舰概率
        ([0.1, 0.10], {"guard_probability": 0.0, "sc_pay_probability": 1.0}, "super_chat", "sc"),  # 避开上舰命中 SC
        ([0.1, 0.10], {"guard_probability": 0.0, "sc_pay_probability": 0.0}, "gift", None),  # 概率全零回退礼物
    ],
    ids=["guard", "super-chat", "fallback-gift"],
)
async def test_pay_branch_probabilities_come_fromenabled_config(
    monkeypatch: pytest.MonkeyPatch,
    store: SQLiteDatabase,
    rolls: List[float],
    overrides: Dict[str, Any],
    message_type: str,
    payload_field: Optional[str],
) -> None:
    scripted = iter(rolls)  # 依次供给 gift_roll / pay_roll，耗尽后回退 0.99（普通礼物，不干扰断言）
    monkeypatch.setattr("src.modules.simulator.service.random.random", lambda: next(scripted, 0.99))
    bus = EventBus()
    received = _record_messages(bus)
    service, _ = _service(store, bus, FakeLLMService(reply="普通弹幕", tokens=5))
    # gift_probability 拉满上限 0.5，配合脚本 gift_roll 必进付费分支
    await service.setup(enabled_config(**FIXED, warmup_duration_s=0.0, gift_probability=0.5, **overrides))
    try:
        await _wait_until(lambda: bool(received), 3.0)
    finally:
        await service.stop()
        await service.cleanup()

    assert received and received[0][0].message_type == message_type
    if payload_field:
        assert getattr(received[0][0], payload_field) is not None
