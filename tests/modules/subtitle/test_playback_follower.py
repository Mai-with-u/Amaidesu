"""字幕播放事件跟随器与两个后端适配：started 分段显示、句边界事件与估算定时双路切换、
failed 显示全文、finished 清空；后端只转发 text 并透传 enabled"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtterancePlaybackStartedPayload,
    UtteranceSentenceBoundaryPayload,
    UtteranceStartedPayload,
)
from src.modules.subtitle import DashboardBackend, SubtitleBackend, TkGuiBackend
from src.modules.subtitle.playback_follower import (
    bind_playback_subtitle,
    split_subtitle_segments,
    unbind_playback_subtitle,
)
from src.modules.subtitle.protocol import ReplyQuote
from tests.helpers import FakeClock
from tests.modules.subtitle.helpers import FakeScheduler


class _Logger:
    def debug(self, msg: str) -> None: ...

    def warning(self, msg: str) -> None: ...


def _service() -> AsyncMock:
    return AsyncMock(show=AsyncMock(), clear=AsyncMock())


def _speech(utterance_id: str, user_name: str | None = None, target_text: str | None = None):
    """构造 streamer.speech 载荷；user_name / target_text 齐全才有引用可登记。"""
    return StreamerSpeechPayload(
        utterance_id=utterance_id,
        text="回复内容",
        emotion="happy",
        emotion_intensity=0.5,
        reply_to_user_name=user_name,
        reply_to_text=target_text,
    )


async def _emit(bus: EventBus, event_name: str, payload) -> None:
    await bus.emit(event_name, payload, source="test")
    await bus.wait_until_drained()


async def test_started_and_failed_show_the_sentence_and_finished_clears() -> None:
    bus, svc = EventBus(), _service()
    assert bind_playback_subtitle(bus, svc, _Logger()) is not None

    started = UtteranceStartedPayload(utterance_id="utt_p1", speech_text="这句正在播", engine="edge", duration_ms=100)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_STARTED, started)
    svc.show.assert_awaited_once_with("这句正在播", "utt_p1", None)

    svc.show.reset_mock()
    failed = UtteranceFailedPayload(
        utterance_id="utt_p2", speech_text="失败但字幕要显示", engine="edge", error_message="boom"
    )
    await _emit(bus, CoreEvents.TTS_UTTERANCE_FAILED, failed)  # 合成失败逐句降级：无声音也要给文本
    svc.show.assert_awaited_once_with("失败但字幕要显示", "utt_p2", None)

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
        ("逗号，也切，分句。收尾", ["逗号", "也切", "分句", "收尾"]),  # 逗号同为切分点且不显示
        ("慢一点,别急,慢慢来。", ["慢一点", "别急", "慢慢来"]),  # 半角逗号同切
        ("第一点；第二点。完了", ["第一点", "第二点", "完了"]),  # 分号是停顿，同切
        ("苹果、香蕉和橘子。", ["苹果、香蕉和橘子"]),  # 顿号是并列不是停顿，不切
        ("提示：请注意，看这里。", ["提示：请注意", "看这里"]),  # 冒号是引出不是停顿，不切
        ("他说：“来吧，坐下。”然后大家就坐了。", ["他说：“来吧，坐下。”然后大家就坐了"]),  # 引号内的逗号/句号不切
        (
            "大家好，“欢迎来到”，今天的直播。开始吧",
            ["大家好", "“欢迎来到”", "今天的直播", "开始吧"],
        ),  # 引号整体作一段，引号外的切点照切
        ("。。。", []),  # 纯标点 → 空段全滤
        ("，，，没有内容的标点。好的", ["没有内容的标点", "好的"]),  # 纯标点段跳过，不产生空段
    ],
)
def test_split_subtitle_segments(text: str, expected: list) -> None:
    assert split_subtitle_segments(text) == expected


# ---------------- 分段跟随：假事件总线 + 注入时钟 + 假定时器 ----------------


def _boundary(utterance_id: str, segment_index: int) -> UtteranceSentenceBoundaryPayload:
    return UtteranceSentenceBoundaryPayload(utterance_id=utterance_id, segment_index=segment_index)


def _playback_started(utterance_id: str) -> UtterancePlaybackStartedPayload:
    return UtterancePlaybackStartedPayload(utterance_id=utterance_id)


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


async def test_started_shows_first_segment_and_waits_for_playback() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "第一段。第二段。第三段")
    svc.show.assert_awaited_once_with("第一段", "utt_p1", None)
    # 估算定时不起跑：等真正出声（播放开始事件）再排，字幕不跑在声音前面
    assert sched.pending() == []
    clock.tick(10_000)  # 合成迟迟不回也照旧：字幕停在第一段，不会先行切完
    assert svc.show.await_count == 1
    assert sched.pending() == []


async def test_boundary_event_switches_segment_and_cancels_timer() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "第一段。第二段。第三段")
    await _emit(bus, CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, _playback_started("utt_p1"))
    clock.tick(1000)  # 段上屏已足最短时长：边界正常切
    svc.show.reset_mock()
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 1))
    svc.show.assert_awaited_once_with("第二段", "utt_p1", None)
    # 事件先到：兜底定时被取消并按剩余段重排；3 字 × 250ms = 750ms 但不低于最短段显示时长
    assert sched.pending()[0]["delay_ms"] == 800
    # 文本段耗尽后多余边界事件被忽略（reset_mock 后列表从第二段起计）
    clock.tick(1000)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 2))
    clock.tick(1000)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 3))
    calls = [call.args[0] for call in svc.show.await_args_list]
    assert calls == ["第二段", "第三段"]


async def test_early_boundary_within_min_show_is_ignored() -> None:
    # 缺陷场景：静音检测把逗号停顿也报成边界，出声后 ~600ms 就到——首段被过早
    # 切走；段显示不足最短时长的边界事件直接忽略，段不被提前消耗
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "大家晚上好，今天我们聊聊最近发生的一些事。好的")
    await _emit(bus, CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, _playback_started("utt_p1"))
    clock.tick(600)  # 出声后 600ms：不足最短段显示时长
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 1))
    svc.show.assert_awaited_once_with("大家晚上好", "utt_p1", None)  # 仍停在首段
    clock.tick(400)  # 距上屏已 1000ms：早到的边界放行
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 1))
    calls = [call.args[0] for call in svc.show.await_args_list]
    assert calls == ["大家晚上好", "今天我们聊聊最近发生的一些事"]


async def test_comma_level_segments_flow_with_boundaries_and_timer() -> None:
    # 逗号级切分后段数上升：边界事件照段序逐个切换；边界供完的段由估算定时接管
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "先说结论，再讲原因，最后给出建议，大家听完再提问。好的")
    await _emit(bus, CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, _playback_started("utt_p1"))
    clock.tick(1000)
    for index in range(1, 4):  # 三个边界把前三个逗号段依次切完
        await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", index))
        clock.tick(1000)
    calls = [call.args[0] for call in svc.show.await_args_list]
    assert calls == ["先说结论", "再讲原因", "最后给出建议", "大家听完再提问"]
    assert len(sched.pending()) == 1  # 最后的"好的"段还有估算兜底定时
    sched.fire_next()
    await _pump()
    calls = [call.args[0] for call in svc.show.await_args_list]
    assert calls[-1] == "好的"
    assert sched.pending() == []  # 段耗尽后不再建定时


async def test_boundary_after_segments_exhausted_keeps_last_segment() -> None:
    # 边界数多于段数（静音检测对逗号停顿也报边界）：段耗尽即收会话，多余边界忽略
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "一句话，两段。结尾")
    await _emit(bus, CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, _playback_started("utt_p1"))
    clock.tick(1000)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 1))
    clock.tick(1000)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 2))
    clock.tick(1000)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 3))  # 段已耗尽
    calls = [call.args[0] for call in svc.show.await_args_list]
    assert calls == ["一句话", "两段", "结尾"]
    assert sched.pending() == []


async def _pump() -> None:
    """泵几个循环拍：定时回调 create_task 出的切段任务落地。"""
    for _ in range(3):
        await asyncio.sleep(0)


async def test_fallback_timer_switches_without_boundary_event() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "第一段。第二段")
    await _emit(bus, CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, _playback_started("utt_p1"))
    assert sched.fire_next() is not None  # 检测不工作：估算定时到点照切
    await _pump()
    svc.show.assert_awaited_with("第二段", "utt_p1", None)


async def test_duration_estimates_by_char_proportion() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    # 全文 6 字、总时长 6000ms：首段 2 字 → 2000ms；切到第二段（2 字）后
    # 剩余窗口 4000ms、剩余 4 字 → 再 2000ms
    await _start(bus, "一二。三四。五六", duration_ms=6000)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, _playback_started("utt_p1"))
    assert sched.delays()[0] == 2000
    clock.tick(2000)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 1))
    assert sched.pending()[0]["delay_ms"] == 2000


async def test_event_beats_timer_no_double_switch() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "第一段。第二段。第三段")
    await _emit(bus, CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, _playback_started("utt_p1"))
    clock.tick(1000)  # 段上屏已足最短时长：边界正常切
    await _emit(bus, CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, _boundary("utt_p1", 1))
    # 事件切段后原定时已取消：定时触发不到已被跳过的段
    assert len(sched.pending()) == 1
    sched.fire_next()
    await _pump()
    calls = [call.args[0] for call in svc.show.await_args_list]
    assert calls == ["第一段", "第二段", "第三段"]  # 无重复切段


# ---------------- 播放开始起跑估算时钟 ----------------


async def test_playback_started_arms_estimation_clock_at_audio_start() -> None:
    # 缺陷场景：流式引擎 started 在合成前发布，估算若从 started 起跑会在出声前
    # 跑完；起跑锚定 playback_started（首块写声卡），任何合成延迟下估算都与听感对齐
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "第一段。第二段。第三段")
    clock.tick(3000)  # 合成首块延迟 3s，音频此刻才真正出声
    await _emit(bus, CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, _playback_started("utt_p1"))
    # 估算此刻才起跑："第二段" 3 字 × 250ms = 750ms，抬到最短段显示时长
    assert sched.delays() == [800]
    assert len(sched.pending()) == 1


async def test_playback_started_ignored_for_other_utterance() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "第一段。第二段", utterance_id="utt_a")
    await _emit(bus, CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, _playback_started("utt_other"))
    assert sched.delays() == []  # 非本句的播放开始事件：不起跑估算
    assert sched.pending() == []


async def test_playback_started_without_session_or_after_finish_is_ignored() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "整句无句末标点")  # 单段无分段会话
    await _emit(bus, CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, _playback_started("utt_p1"))
    assert sched.pending() == []
    await _start(bus, "第一段。第二段", utterance_id="utt_b")
    await _finish(bus, utterance_id="utt_b")
    await _emit(bus, CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, _playback_started("utt_b"))
    assert sched.pending() == []  # finished 后迟到的播放开始事件不再建定时


async def test_duration_window_counts_from_playback_start() -> None:
    # 全量引擎（duration_ms 可用）：窗口从出声时刻起算——started 后等待合成的
    # 时间不吞掉播放窗口（从 started 起跑的旧口径下此处只剩 1000ms × 2/4 = 500ms）
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "一二。三四", duration_ms=6000)
    clock.tick(5000)  # started 后 5s 音频才出声
    await _emit(bus, CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, _playback_started("utt_p1"))
    assert sched.pending()[0]["delay_ms"] == 3000  # 完整窗口 6000ms × 首段 2/4 字


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
    svc.show.assert_awaited_once_with("第一段。第二段", "utt_p1", None)  # 全文不分段
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


async def test_single_segment_shows_full_text_without_schedule() -> None:
    clock, sched = FakeClock(), FakeScheduler()
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger(), now_ms_fn=clock.now_ms, scheduler=sched)
    await _start(bus, "整句无句末标点")
    await _emit(bus, CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, _playback_started("utt_p1"))
    svc.show.assert_awaited_once_with("整句无句末标点", "utt_p1", None)
    assert sched.pending() == []  # 单段无分段会话，出声信号也不建定时


# ---------------- 引用弹幕：streamer.speech 登记 → 显示携带 ----------------


async def test_reply_quote_from_speech_flows_into_shows() -> None:
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger())

    await _emit(bus, CoreEvents.STREAMER_SPEECH, _speech("utt_q1", user_name="观众A", target_text="主播你好"))
    await _start(bus, "你好呀观众A。今天玩什么", utterance_id="utt_q1")

    assert svc.show.await_args_list[0].args == ("你好呀观众A", "utt_q1", ReplyQuote(user_name="观众A", text="主播你好"))


async def test_speech_without_reply_target_shows_without_quote() -> None:
    # 主动发言 / 解析不中（宁缺毋假）：无引用可登记，字幕不带引用行
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger())

    await _emit(bus, CoreEvents.STREAMER_SPEECH, _speech("utt_q2"))
    await _start(bus, "大家好", utterance_id="utt_q2")

    assert svc.show.await_args_list[0].args == ("大家好", "utt_q2", None)


async def test_part_id_of_segmented_speech_falls_back_to_base_quote() -> None:
    # 杂谈档分段连播：第 2 段以后的 TTS 事件带基编号加 _pN 后缀，回退基编号查回复目标
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, _Logger())

    await _emit(bus, CoreEvents.STREAMER_SPEECH, _speech("utt_q3", user_name="观众B", target_text="唱首歌吧"))
    await _start(bus, "好呀。那我唱了", utterance_id="utt_q3_p2")

    assert svc.show.await_args_list[0].args == ("好呀", "utt_q3_p2", ReplyQuote(user_name="观众B", text="唱首歌吧"))


async def test_unbind_removes_speech_quote_registration() -> None:
    bus, svc = EventBus(), _service()
    handlers = bind_playback_subtitle(bus, svc, _Logger())
    unbind_playback_subtitle(bus, handlers, _Logger())

    await _emit(bus, CoreEvents.STREAMER_SPEECH, _speech("utt_q4", user_name="观众C", target_text="早上好"))
    await _start(bus, "早上好呀", utterance_id="utt_q4")

    svc.show.assert_not_awaited()  # 退订摘除跟随器全部订阅：引用登记与字幕显示都不再发生


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

    widget.show_subtitle.assert_awaited_once_with("hello", reply_quote=None)
    widget.clear_subtitle.assert_awaited_once_with()
    gui.push_subtitle.assert_called_once_with("hello")
    gui.clear_subtitle.assert_called_once_with()  # 线程安全入队，禁止跨线程直调 Tk（会死锁 GUI 线程）
