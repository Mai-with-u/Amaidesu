"""SpeechDispatcher 单元测试：派发扇出 + TTS 队列生命周期降级。

Agent 级端到端行为（事件形状 / 序号 / 降级）由 ``test_streamer_agent_wiring``
等覆盖；本文件直接打 dispatcher，锁定组件级契约：
- dispatch 消费 reply 结构化结果：业务事件 / 字幕 / VTS / 返回三元组
- start：启用但无引擎 → 降级关闭不抛；stop 幂等
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.speech_dispatcher import SpeechDispatcher
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.tools.models import ToolExecutionResult


def _make_dispatcher(
    event_bus=None,
    subtitle_service=None,
    tool_registry=None,
    tts_engine=None,
    speech_config=None,
) -> SpeechDispatcher:
    return SpeechDispatcher(
        event_bus=event_bus,
        subtitle_service=subtitle_service,
        tool_registry=tool_registry,
        tts_engine=tts_engine,
        speech_config=speech_config,
    )


def _registry_with_vts_and_action() -> MagicMock:
    registry = MagicMock()
    registry.invoke = AsyncMock(return_value=ToolExecutionResult(tool_name="x", success=True, structured_content={}))
    return registry


# ---------------------------------------------------------------------------
# dispatch 五路扇出
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_fans_out_to_all_downstreams():
    """speech+emotion 齐备：业务事件 / 字幕 / 表情各路逐一到位，返回三元组。"""
    bus = EventBus()
    captured: list[StreamerSpeechPayload] = []
    captured_event = asyncio.Event()

    async def _capture(event_name, payload, source=None):
        if isinstance(payload, StreamerSpeechPayload):
            captured.append(payload)
            captured_event.set()

    bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)

    subtitle = MagicMock()
    subtitle.show = AsyncMock()
    registry = _registry_with_vts_and_action()

    dispatcher = _make_dispatcher(
        event_bus=bus,
        subtitle_service=subtitle,
        tool_registry=registry,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    # 无 TTS 引擎 → start 降级关闭；VTS/动作扇出不依赖队列启用
    # （VTS 跟随 TTS 门，见下一条用例；本条先验动作工具与业务事件）
    await dispatcher.start()

    result = dispatcher.dispatch(
        {
            "speech": "你好",
            "emotion": {"name": "happy", "intensity": 0.8},
        },
        target_user_id="u1",
        reply_to_message_id="m9",
        round_id="rnd_1",
    )
    await asyncio.wait_for(captured_event.wait(), timeout=2.0)
    await asyncio.sleep(0)  # 让字幕/动作任务得到调度

    speech, emotion, utterance_id = result
    assert speech == "你好"
    assert emotion == "happy"
    assert utterance_id and utterance_id.startswith("utt_")

    # 业务事件：关联键齐全
    assert len(captured) == 1
    payload = captured[0]
    assert payload.text == "你好"
    assert payload.target_user_id == "u1"
    assert payload.reply_to_message_id == "m9"
    assert payload.round_id == "rnd_1"
    assert payload.utterance_id == utterance_id

    # 字幕
    subtitle.show.assert_awaited_once()
    assert subtitle.show.await_args.args[0] == "你好"

    # TTS 未接线（engine=None）→ start 已降级关闭，不产生 vts 表情调用
    assert registry.invoke.await_count == 0


@pytest.mark.asyncio
async def test_dispatch_vts_follows_tts_gate_and_emits_emotion_source():
    """TTS 启用时 emotion 触发 vts_set_expression（MouthSmile=1.0，weight=0.8）。"""
    bus = None  # 业务事件面由上一条覆盖；本条只看 VTS 调用
    registry = _registry_with_vts_and_action()
    engine = MagicMock()
    engine.handle_speech = AsyncMock()

    dispatcher = _make_dispatcher(
        event_bus=bus,
        tool_registry=registry,
        tts_engine=engine,
        speech_config={"enabled": True},
    )
    await dispatcher.start()
    assert dispatcher.tts_enabled is True
    assert dispatcher.utterance_queue is not None

    result = dispatcher.dispatch({"speech": "你好", "emotion": {"name": "happy", "intensity": 0.8}})
    await asyncio.sleep(0.05)  # fire-and-forget 任务调度

    invocations = [c.args[0] for c in registry.invoke.await_args_list]
    vts = [inv for inv in invocations if inv.tool_name == "vts_set_expression"]
    assert len(vts) == 1
    assert vts[0].arguments == {"parameters": {"MouthSmile": 1.0}, "weight": 0.8}
    assert vts[0].source == "streamer_agent.emotion"
    # 返回三元组：emotion 原样带出
    assert result == ("你好", "happy", result[2])


@pytest.mark.asyncio
async def test_dispatch_non_dict_payload_returns_none():
    dispatcher = _make_dispatcher()
    assert dispatcher.dispatch(["speech", "emotion"]) is None


# ---------------------------------------------------------------------------
# TTS 队列生命周期：降级与幂等
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_degrades_when_enabled_but_no_engine():
    """启用但无引擎：start 不抛、TTS 关闭、speech 业务事件照发。"""
    bus = EventBus()
    captured: list[StreamerSpeechPayload] = []
    captured_event = asyncio.Event()

    async def _capture(event_name, payload, source=None):
        captured.append(payload)
        captured_event.set()

    bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)

    dispatcher = _make_dispatcher(
        event_bus=bus,
        tool_registry=_registry_with_vts_and_action(),
        tts_engine=None,
        speech_config={"enabled": True},
    )
    await dispatcher.start()
    assert dispatcher.tts_enabled is False
    assert dispatcher.utterance_queue is None

    result = dispatcher.dispatch({"speech": "仍在说话", "emotion": ""})
    await asyncio.wait_for(captured_event.wait(), timeout=2.0)
    assert result is not None and result[0] == "仍在说话"

    await dispatcher.stop()  # 降级态 stop 安全


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_stops_queue():
    """stop 后队列置空；重复 stop 不报错。"""
    engine = MagicMock()
    engine.handle_speech = AsyncMock()
    dispatcher = _make_dispatcher(
        tts_engine=engine,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await dispatcher.start()
    queue = dispatcher.utterance_queue
    assert queue is not None

    await dispatcher.stop()
    assert dispatcher.utterance_queue is None
    await dispatcher.stop()  # 幂等


@pytest.mark.asyncio
async def test_disabled_config_never_builds_queue():
    """未启用（默认）：start 是 no-op，不构造队列。"""
    dispatcher = _make_dispatcher(tts_engine=MagicMock())
    await dispatcher.start()
    assert dispatcher.tts_enabled is False
    assert dispatcher.utterance_queue is None


# ---------------------------------------------------------------------------
# 异步扇出持有 + 停止汇合（P1-2）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_drains_inflight_fanout_without_pending_warnings():
    """dispatch 后立即 stop：在飞扇出任务被汇合，无 "Task was destroyed but it is pending"。

    覆盖：业务事件 emit / 字幕 show / VTS 表情三路。汇合点为
    ``SpeechDispatcher.stop()``，限 2 秒；超时不抛（本用例不构造超时场景）。
    """
    bus = EventBus()
    captured_event = asyncio.Event()

    async def _capture(event_name, payload, source=None):
        if isinstance(payload, StreamerSpeechPayload):
            captured_event.set()

    bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)

    subtitle = MagicMock()
    subtitle.show = AsyncMock()
    registry = _registry_with_vts_and_action()
    engine = MagicMock()
    engine.handle_speech = AsyncMock()

    dispatcher = _make_dispatcher(
        event_bus=bus,
        subtitle_service=subtitle,
        tool_registry=registry,
        tts_engine=engine,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await dispatcher.start()
    assert dispatcher.tts_enabled is True

    # 各路扇出（speech/emotion/业务事件/字幕）一次性触发，立即 stop 模拟"决策循环立刻回收"
    dispatcher.dispatch(
        {
            "speech": "你好",
            "emotion": {"name": "happy", "intensity": 0.8},
        },
        round_id="rnd_drain",
    )
    await dispatcher.stop()

    # 汇合完成意味着"无悬挂任务"——所有 task 必须 done；callback 的 discard
    # 由 call_soon 排队，sleep(0) 让其执行一次；再断言集合清空。
    assert all(t.done() for t in dispatcher._bg_tasks), (
        f"stop 后仍有未完成的后台任务: {[t for t in dispatcher._bg_tasks if not t.done()]}"
    )
    await asyncio.sleep(0)
    assert dispatcher._bg_tasks == set()


@pytest.mark.asyncio
async def test_dispatch_returns_synchronously_without_awaiting_slow_invoke():
    """决策循环安全：dispatch 在慢 invoke 面前仍同步返回（fire-and-forget 语义保留）。

    注入一个 1.0s 慢 invoke；dispatch 必须立即返回；stop 在 2 秒汇合窗口内完成。
    """
    import time

    sleep_done = asyncio.Event()
    invoke_started = asyncio.Event()

    async def _slow_invoke(inv):
        invoke_started.set()
        await asyncio.sleep(1.0)
        sleep_done.set()
        return ToolExecutionResult(tool_name=inv.tool_name, success=True, structured_content={})

    registry = MagicMock()
    registry.invoke = AsyncMock(side_effect=_slow_invoke)

    engine = MagicMock()
    engine.handle_speech = AsyncMock()

    dispatcher = _make_dispatcher(
        tool_registry=registry,
        tts_engine=engine,
        speech_config={"enabled": True},
    )
    await dispatcher.start()

    t0 = time.monotonic()
    result = dispatcher.dispatch(
        {"speech": "x", "emotion": {"name": "happy", "intensity": 0.8}},
    )
    elapsed = time.monotonic() - t0

    # dispatch 必须几乎瞬时返回（远小于 1s 的 sleep）；扇出慢 invoke 不阻塞决策循环
    assert elapsed < 0.1, f"dispatch 不应阻塞扇出，但耗时 {elapsed:.3f}s"
    assert result is not None and result[0] == "x"

    await asyncio.wait_for(invoke_started.wait(), timeout=1.0)

    t1 = time.monotonic()
    await dispatcher.stop()
    stop_elapsed = time.monotonic() - t1
    # 1.0s 慢 invoke + 余量，stop 必须在 2 秒汇合窗口内完成
    assert stop_elapsed < 2.5, f"stop 应在 2 秒超时内完成，但耗时 {stop_elapsed:.3f}s"

    await asyncio.sleep(0)
    assert dispatcher._bg_tasks == set()


@pytest.mark.asyncio
async def test_stop_is_bounded_when_invoke_hangs_longer_than_timeout():
    """汇合有界：invoke 永远不结束时 stop 不抛，仅 WARN，2 秒内返回。"""
    started = asyncio.Event()

    async def _hanging_invoke(inv):
        started.set()
        # 远超 2 秒 timeout；强制 stop 走 TimeoutError 路径
        await asyncio.sleep(60)
        return ToolExecutionResult(tool_name=inv.tool_name, success=True, structured_content={})

    registry = MagicMock()
    registry.invoke = AsyncMock(side_effect=_hanging_invoke)
    engine = MagicMock()
    engine.handle_speech = AsyncMock()

    dispatcher = _make_dispatcher(
        tool_registry=registry,
        tts_engine=engine,
        speech_config={"enabled": True},
    )
    await dispatcher.start()

    dispatcher.dispatch({"speech": "y", "emotion": {"name": "happy", "intensity": 0.5}})
    await asyncio.wait_for(started.wait(), timeout=1.0)

    import time

    t0 = time.monotonic()
    await dispatcher.stop()
    elapsed = time.monotonic() - t0
    # 略大于 timeout 上限（2s）但在合理误差内——stop 主动放弃，不阻塞
    assert 1.5 < elapsed < 3.0, f"stop 应在 ~2s 返回，实际 {elapsed:.3f}s"
