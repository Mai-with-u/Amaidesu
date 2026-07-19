"""OutputHandlerManager 两层事件聚合逻辑测试。

覆盖:
1. 所有 handler emit COMPLETED → FINISHED 触发一次
2. handler 抛异常时 finally 仍 emit COMPLETED → FINISHED 仍能触发
3. handler 漏发 → watchdog 超时兜底触发 FINISHED
4. 未知 intent_id 的迟到 COMPLETED → 静默忽略,不影响其他 intent
5. 无 active handler → DISPATCHED 后立刻发 FINISHED(避免悬挂)
6. 多个 intent 并发 → 各自独立聚合,互不串扰

这些测试只验证 OutputHandlerManager 的聚合协议,不依赖具体 handler 实现,
所有 handler 用 mock 替代。使用 asyncio.Event 而不是 sleep 做同步,
避免 CI 负载下的 flaky 问题。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.events.payloads.output import OutputHandlerCompletedPayload
from src.modules.types.intent import Intent, IntentMetadata

if TYPE_CHECKING:
    from src.stages.output.manager import OutputHandlerManager


# ── 测试异步事件基元 ──────────────────────────────────────────────


class FinishedTracker:
    """封装 FINISHED 事件计数 + asyncio.Event,避免 sleep 同步。"""

    def __init__(self):
        self.count = 0
        self.payloads: List[IntentPayload] = []
        self._event = asyncio.Event()

    def callback(self):
        """返回 on_finished 回调函数,每次触发都 set event。"""
        async def on_finished(event_name, payload, source):
            self.count += 1
            self.payloads.append(payload)
            self._event.set()
        return on_finished

    async def wait(self, timeout: float = 2.0) -> None:
        """等待下一次 FINISHED 触发。"""
        self._event.clear()
        await asyncio.wait_for(self._event.wait(), timeout=timeout)

    async def ensure_not_fired(self, timeout: float = 0.1) -> None:
        """确保在 timeout 秒内没有 FINISHED 触发。"""
        self._event.clear()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(self._event.wait(), timeout=timeout)


# ── 测试辅助函数 ──────────────────────────────────────────────────


def _make_payload(speech: str = "hello") -> IntentPayload:
    """构造测试用 IntentPayload。"""
    intent = Intent(
        speech=speech,
        metadata=IntentMetadata(source_id="test", decision_time_ms=1700000000000),
    )
    return IntentPayload.from_intent(intent, name="test_decider")


def _make_manager_with_handlers(
    bus: EventBus,
    handler_class_names: List[str],
    completion_timeout_ms: int = 30000,
) -> "OutputHandlerManager":
    """构造一个带 N 个 mock handler 的 OutputHandlerManager。

    handler 用动态类模拟,type(h).__name__ 设为给定名字,
    _handler_started[h] = True 让 manager 视其为 active。
    """
    from src.modules.pipeline import PipelineManager
    from src.stages.output.manager import OutputHandlerManager

    manager = OutputHandlerManager(
        event_bus=bus,
        pipeline_manager=MagicMock(spec=PipelineManager),
        config={"completion_timeout_ms": completion_timeout_ms},
    )
    # pipeline_manager.process 是 async,返回 intent 原样(透传)
    manager.pipeline_manager.process = AsyncMock(side_effect=lambda intent: intent)

    for name in handler_class_names:
        cls = type(name, (), {})
        h = cls()
        manager.handlers.append(h)
        manager._handler_names[h] = name
        manager._handler_started[h] = True

    # 手动注册聚合订阅(setup() 会去加载配置,这里跳过)
    bus.on(
        CoreEvents.OUTPUT_HANDLER_COMPLETED,
        manager._on_handler_completed,
        model_class=OutputHandlerCompletedPayload,
    )
    manager._completion_handler_registered = True

    return manager


# ============================================================
# 1. 基本聚合:全部完成 → FINISHED 一次
# ============================================================


@pytest.mark.asyncio
async def test_aggregation_all_complete_fires_finished_once():
    """3 个 handler 都 emit COMPLETED → FINISHED 恰好触发一次。"""
    bus = EventBus()
    manager = _make_manager_with_handlers(bus, ["EdgeTTSHandler", "SubtitleHandler", "StickerHandler"])

    tracker = FinishedTracker()
    bus.on(CoreEvents.OUTPUT_INTENT_FINISHED, tracker.callback(), model_class=IntentPayload)

    payload = _make_payload()
    intent_id = payload.to_intent().metadata.intent_id

    # 模拟 manager 收到一个 decision intent
    await manager._on_decision_intent(CoreEvents.DECISION_INTENT_GENERATED, payload, "test")

    # 让 dispatch 后台任务先跑完: yield 一次控制权
    await asyncio.sleep(0)
    assert tracker.count == 0, "FINISHED 不应在 handler 未完成前触发"

    # 三个 handler 依次 emit COMPLETED
    for name in ["EdgeTTSHandler", "SubtitleHandler", "StickerHandler"]:
        await bus.emit(
            CoreEvents.OUTPUT_HANDLER_COMPLETED,
            OutputHandlerCompletedPayload(handler_name=name, intent_id=intent_id),
            source=name,
        )

    # 等聚合 FINISHED 触发(事件驱动,应在 0.1s 内)
    await tracker.wait(timeout=2.0)
    assert tracker.count == 1, f"应触发 1 次 FINISHED,实际 {tracker.count}"
    assert tracker.payloads[0].name == "test_decider"


# ============================================================
# 2. 异常路径:handler 抛异常仍 emit COMPLETED → FINISHED 正常触发
# ============================================================


@pytest.mark.asyncio
async def test_aggregation_handler_exception_still_completes():
    """handler 即使异常,只要 finally 里 emit COMPLETED,FINISHED 就能触发。"""
    bus = EventBus()
    manager = _make_manager_with_handlers(bus, ["EdgeTTSHandler"])

    tracker = FinishedTracker()
    bus.on(CoreEvents.OUTPUT_INTENT_FINISHED, tracker.callback(), model_class=IntentPayload)

    payload = _make_payload()
    intent_id = payload.to_intent().metadata.intent_id

    await manager._on_decision_intent(CoreEvents.DECISION_INTENT_GENERATED, payload, "test")
    await asyncio.sleep(0)

    # handler 异常路径仍然 emit(success=False)
    await bus.emit(
        CoreEvents.OUTPUT_HANDLER_COMPLETED,
        OutputHandlerCompletedPayload(handler_name="EdgeTTSHandler", intent_id=intent_id, success=False),
        source="EdgeTTSHandler",
    )
    await tracker.wait()
    assert tracker.count == 1, "异常路径的 COMPLETED 也应触发 FINISHED"


# ============================================================
# 3. Watchdog 兜底:handler 漏发 → 超时强制 FINISHED
# ============================================================


@pytest.mark.asyncio
async def test_aggregation_watchdog_timeout_fires_finished():
    """handler 漏发 COMPLETED → watchdog 超时后强制 FINISHED。"""
    bus = EventBus()
    manager = _make_manager_with_handlers(bus, ["SlowHandler"], completion_timeout_ms=200)

    tracker = FinishedTracker()
    bus.on(CoreEvents.OUTPUT_INTENT_FINISHED, tracker.callback(), model_class=IntentPayload)

    payload = _make_payload()
    await manager._on_decision_intent(CoreEvents.DECISION_INTENT_GENERATED, payload, "test")

    # 不发 COMPLETED,等 watchdog 超时触发
    await tracker.wait(timeout=5.0)
    assert tracker.count == 1, "watchdog 超时应强制触发 FINISHED"


# ============================================================
# 4. 未知 intent_id 的迟到 COMPLETED 被忽略
# ============================================================


@pytest.mark.asyncio
async def test_aggregation_unknown_intent_id_ignored():
    """COMPLETED 带未知 intent_id → 静默忽略,不抛错。"""
    bus = EventBus()
    manager = _make_manager_with_handlers(bus, ["EdgeTTSHandler"])

    payload = _make_payload()
    intent_id = payload.to_intent().metadata.intent_id

    await manager._on_decision_intent(CoreEvents.DECISION_INTENT_GENERATED, payload, "test")
    await asyncio.sleep(0)

    # 发一个不属于任何 pending intent 的 COMPLETED
    await bus.emit(
        CoreEvents.OUTPUT_HANDLER_COMPLETED,
        OutputHandlerCompletedPayload(handler_name="Ghost", intent_id="nonexistent_id"),
        source="Ghost",
    )
    await asyncio.sleep(0)

    # pending 应该仍然存在,不被污染
    async with manager._pending_lock:
        assert intent_id in manager._pending_intents
        assert "EdgeTTSHandler" in manager._pending_intents[intent_id]


# ============================================================
# 5. 无 active handler → DISPATCHED 后立刻 FINISHED
# ============================================================


@pytest.mark.asyncio
async def test_aggregation_no_active_handlers_fires_immediately():
    """配置了 0 个 active handler → DISPATCHED 后立刻发 FINISHED(避免悬挂)。"""
    bus = EventBus()
    manager = _make_manager_with_handlers(bus, [])  # 空 handler 列表

    tracker = FinishedTracker()
    bus.on(CoreEvents.OUTPUT_INTENT_FINISHED, tracker.callback(), model_class=IntentPayload)

    payload = _make_payload()
    await manager._on_decision_intent(CoreEvents.DECISION_INTENT_GENERATED, payload, "test")

    await tracker.wait()
    assert tracker.count == 1, "无 active handler 时应立刻发 FINISHED"


# ============================================================
# 6. 多个 intent 并发独立聚合
# ============================================================


@pytest.mark.asyncio
async def test_aggregation_multiple_intents_independent():
    """两个 intent 同时进行 → 各自独立聚合,FINISHED 各发一次。"""
    bus = EventBus()
    manager = _make_manager_with_handlers(bus, ["EdgeTTSHandler", "SubtitleHandler"])

    tracker = FinishedTracker()
    bus.on(CoreEvents.OUTPUT_INTENT_FINISHED, tracker.callback(), model_class=IntentPayload)

    # 两个不同 intent
    payload_a = _make_payload("A")
    payload_b = _make_payload("B")
    intent_id_a = payload_a.to_intent().metadata.intent_id
    intent_id_b = payload_b.to_intent().metadata.intent_id

    await manager._on_decision_intent(CoreEvents.DECISION_INTENT_GENERATED, payload_a, "test")
    await manager._on_decision_intent(CoreEvents.DECISION_INTENT_GENERATED, payload_b, "test")
    await asyncio.sleep(0)

    # 只完成 intent A
    for name in ["EdgeTTSHandler", "SubtitleHandler"]:
        await bus.emit(
            CoreEvents.OUTPUT_HANDLER_COMPLETED,
            OutputHandlerCompletedPayload(handler_name=name, intent_id=intent_id_a),
            source=name,
        )

    await tracker.wait()
    assert tracker.count == 1, "只应完成 intent A"

    # 完成 intent B
    for name in ["EdgeTTSHandler", "SubtitleHandler"]:
        await bus.emit(
            CoreEvents.OUTPUT_HANDLER_COMPLETED,
            OutputHandlerCompletedPayload(handler_name=name, intent_id=intent_id_b),
            source=name,
        )

    await tracker.wait()
    assert tracker.count == 2, "B 也应被完成"
