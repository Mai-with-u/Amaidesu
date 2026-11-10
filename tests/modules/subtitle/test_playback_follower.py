"""字幕播放事件跟随器与两个后端适配：started 分段显示、句边界事件与估算定时双路切换、
failed 显示全文、finished 清空；后端只转发 text 并透传 enabled"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceSentenceBoundaryPayload,
    UtteranceStartedPayload,
)
from src.modules.subtitle import DashboardBackend, SubtitleBackend, TkGuiBackend
from src.modules.subtitle.playback_follower import bind_playback_subtitle, split_subtitle_segments
from tests.helpers import FakeClock
from tests.modules.subtitle.helpers import FakeScheduler


class _Logger:
    def debug(self, msg: str) -> None: ...

    def warning(self, msg: str) -> None: ...


def _service() -> AsyncMock:
    return AsyncMock(show=AsyncMock(), clear=AsyncMock())


async def _emit(bus: EventBus, event_name: str, payload) -> None:
    await bus.emit(event_name, payload, source="test")
    await bus.wait_until_drained()


async def test_started_and_failed_show_the_sentence_and_finished_clears() -> None:
    bus, svc = EventBus(), _service()
    assert bind_playback_subtitle(bus, svc, _Logger()) is not None

    started = UtteranceStartedPayload(utterance_id="utt_p1", speech_text="这句正在播", engine="edge", duration_ms=100)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_STARTED, started)
    svc.show.assert_awaited_once_with("这句正在播", "utt_p1")

    svc.show.reset_mock()
    failed = UtteranceFailedPayload(
        utterance_id="utt_p2", speech_text="失败但字幕要显示", engine="edge", error_message="boom"
    )
    await _emit(bus, CoreEvents.TTS_UTTERANCE_FAILED, failed)  # 合成失败逐句降级：无声音也要给文本
    svc.show.assert_awaited_once_with("失败但字幕要显示", "utt_p2")

    finished = UtteranceFinishedPayload(utterance_id="utt_p3", engine="edge", duration_ms=100)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_FINISHED, finished)
    svc.clear.assert_awaited_once()


async def test_subtitle_service_error_does_not_break_the_bus() -> None:
    bus, svc = EventBus(), _service()
    svc.show = AsyncMock(side_effect=RuntimeError("boom"))
    bind_playback_subtitle(bus, svc, _Logger())
    seen: list[str] = []

    async def follower(event_name, payload, source=None) -> None:
        seen.append(payload.utterance_id)

    bus.on(CoreEvents.TTS_UTTERANCE_STARTED, follower, model_class=UtteranceStartedPayload)
    await _emit(
        bus,
        CoreEvents.TTS_UTTERANCE_STARTED,
        UtteranceStartedPayload(utterance_id="utt_p4", speech_text="x", engine="edge", duration_ms=1),
    )
    assert seen == ["utt_p4"]  # 装饰性路径的异常被隔离


def test_binding_needs_both_bus_and_service() -> None:
    assert bind_playback_subtitle(None, _service(), _Logger()) is None
    assert bind_playback_subtitle(EventBus(), None, _Logger()) is None


# ---------------- 分段切分 ----------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("你好。今天走！真的吗？", ["你好", "今天走", "真的吗"]),  # 句末标点不显示
        ("等等……好吧。", ["等等", "好吧"]),  # 叠用省略号是边界
        ("天啊！！！真的？！", ["天啊", "真的"]),  # 叠用叹号/问叹连用
        ("没有句末标点的一句话", ["没有句末标点的一句话"]),  # 单段 = 原行为
        ("逗号，不切，句子。收尾", ["逗号，不切，句子", "收尾"]),  # 逗号不是句末标点
        ("。。。", []),  # 纯标点 → 空段全滤
    ],
)
def test_split_subtitle_segments(text: str, expected: list) -> None:
    assert split_subtitle_segments(text) == expected


# ---------------- 分段跟随：假事件总线 + 注入时钟 + 假定时器 ----------------


def _boundary(utterance_id: str, segment_index: int) -> UtteranceSentenceBoundaryPayload:
    return UtteranceSentenceBoundaryPayload(utterance_id=utterance_id, segment_index=segment_index)


async def _start(bus: EventBus, text: str, utterance_id: str = "utt_p1", duration_ms: int | None = None) -> None:
    await _emit(
        bus,
        CoreEvents.TTS_UTTERANCE_STARTED,
        UtteranceStartedPayload(utterance_id=utterance_id, speech_text=text, engine="edge", duration_ms=duration_ms),
    )


async def _finish(bus: EventBus, utterance_id: str = "utt_p1") -> None:
    await _emit(
        bus,
        CoreEvents.TTS_UTTERANCE_FINISHED,
        UtteranceFinishedPayload(utterance_id=utterance_id, engine="edge", duration_ms=100),
    )


async def test_started_shows_first_segment_and_arms_fallback_timer() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "第一段。第二段。第三段")
    svc.show.assert_awaited_once_with("第一段", "utt_p1")
    # 无 duration_ms（流式）：按每字 250ms 估算；"第二段"3 字 → 750ms
    assert sched.delays() == [750]


async def test_boundary_event_switches_segment_and_cancels_timer() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "第一段。第二段。第三段")
    svc.show.reset_mock()
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 1))
    svc.show.assert_awaited_once_with("第二段", "utt_p1")
    # 事件先到：兜底定时被取消并按剩余段重排
    assert sched.pending()[0]["delay_ms"] == 750  # "第三段" 3 字 × 250ms
    # 文本段耗尽后多余边界事件被忽略（reset_mock 后列表从第二段起计）
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 2))
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 3))
    calls = [call.args[0] for call in svc.show.await_args_list]
    assert calls == ["第二段", "第三段"]


async def _pump() -> None:
    """泵几个循环拍：定时回调 create_task 出的切段任务落地。"""
    for _ in range(3):
        await asyncio.sleep(0)


async def test_fallback_timer_switches_without_boundary_event() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "第一段。第二段")
    assert sched.fire_next() is not None  # 检测不工作：估算定时到点照切
    await _pump()
    svc.show.assert_awaited_with("第二段", "utt_p1")


async def test_duration_estimates_by_char_proportion() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    # 全文 6 字、总时长 6000ms：首段 2 字 → 2000ms；切到第二段（2 字）后
    # 剩余窗口 4000ms、剩余 4 字 → 再 2000ms
    await _start(bus, "一二。三四。五六", duration_ms=6000)
    assert sched.delays()[0] == 2000
    clock.tick(2000)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 1))
    assert sched.pending()[0]["delay_ms"] == 2000


async def test_event_beats_timer_no_double_switch() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "第一段。第二段。第三段")
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 1))
    # 事件切段后原定时已取消：定时触发不到已被跳过的段
    assert len(sched.pending()) == 1
    sched.fire_next()
    await _pump()
    calls = [call.args[0] for call in svc.show.await_args_list]
    assert calls == ["第一段", "第二段", "第三段"]  # 无重复切段


async def test_finished_clears_and_late_boundary_is_ignored() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "第一段。第二段")
    await _finish(bus)
    svc.clear.assert_awaited_once()
    assert sched.pending() == []  # finished 取消兜底定时
    svc.show.reset_mock()
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 1))
    svc.show.assert_not_awaited()  # utterance 结束后迟到事件丢弃


async def test_failed_shows_full_text_without_segmentation() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "第一段。第二段")
    svc.show.reset_mock()
    failed = UtteranceFailedPayload(
        utterance_id="utt_p1", speech_text="第一段。第二段", engine="edge", error_message="boom"
    )
    await _emit(bus, CoreEvents.TTS_UTTERANCE_FAILED, failed)
    svc.show.assert_awaited_once_with("第一段。第二段", "utt_p1")  # 全文不分段
    assert sched.pending() == []  # 失败即取消分段会话


async def test_new_started_replaces_previous_session() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "旧句一。旧句二", utterance_id="utt_a")
    await _start(bus, "新句一。新句二", utterance_id="utt_b")
    # 旧 utterance 的迟到边界事件不再切新会话的段
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_a", 1))
    calls = [call.args[0] for call in svc.show.await_args_list]
    assert calls == ["旧句一", "新句一"]


async def test_single_segment_started_keeps_legacy_behavior() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "整句无句末标点")
    svc.show.assert_awaited_once_with("整句无句末标点", "utt_p1")
    assert sched.pending() == []  # 单段无兜底定时


@pytest.mark.parametrize("enabled", [True, False])
async def test_backends_forward_text_only_and_expose_enabled(enabled: bool) -> None:
    widget = MagicMock(show_subtitle=AsyncMock(), clear_subtitle=AsyncMock())
    widget.subtitle_config.enabled = enabled
    dashboard = DashboardBackend(widget)
    gui = MagicMock(enabled=enabled)
    tk = TkGuiBackend(gui)

    for backend in (dashboard, tk):
        assert isinstance(backend, SubtitleBackend) and backend.enabled is enabled
        await backend.show("hello", utterance_id="u-42")  # 展示位暂未消费 utterance_id，只转发 text
        await backend.clear()

    widget.show_subtitle.assert_awaited_once_with("hello")
    widget.clear_subtitle.assert_awaited_once_with()
    gui.push_subtitle.assert_called_once_with("hello")
    gui.clear_subtitle.assert_called_once_with()  # 线程安全入队，禁止跨线程直调 Tk（会死锁 GUI 线程）
