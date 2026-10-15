"""TTS 共享函数（common.py）：时长换算、统计字典、utterance 事件发布、播报文本规范化"""

from __future__ import annotations

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.tts.common import (
    build_stats_dict,
    compute_duration_ms,
    emit_utterance_failed,
    emit_utterance_finished,
    emit_utterance_started,
    normalize_tts_text,
)


@pytest.mark.parametrize(
    ("samples", "rate", "expected"),
    [
        (48000, 48000, 1000),
        (96000, 32000, 3000),
        (1000, 48000, 20),  # 不足 1 毫秒的尾数被整数除法截断
        (0, 48000, 0),
        (48000, 0, 0),  # 采样率非正时防御性返回 0
        (48000, -1, 0),
    ],
)
def test_compute_duration_ms(samples: int, rate: int, expected: int) -> None:
    assert compute_duration_ms(samples, rate) == expected


def test_build_stats_dict_required_extra_and_bool_coercion() -> None:
    required = {"name": "TestProvider", "is_connected": True, "render_count": 3, "error_count": 1}
    assert build_stats_dict(**required) == required
    assert build_stats_dict(**required, extra={"buffer_size": 42})["buffer_size"] == 42
    coerced = build_stats_dict(name="X", is_connected=None, render_count=0, error_count=0)  # type: ignore[arg-type]
    assert coerced["is_connected"] is False  # 避免 None 渗入


_STARTED = (emit_utterance_started, CoreEvents.TTS_UTTERANCE_STARTED, UtteranceStartedPayload)
_FINISHED = (emit_utterance_finished, CoreEvents.TTS_UTTERANCE_FINISHED, UtteranceFinishedPayload)
_FAILED = (emit_utterance_failed, CoreEvents.TTS_UTTERANCE_FAILED, UtteranceFailedPayload)


@pytest.mark.parametrize(
    ("kind", "kwargs"),
    [
        (_STARTED, {"speech_text": "你好", "engine": "edge", "duration_ms": 2000}),
        (_STARTED, {"speech_text": "正在合成…", "engine": "gptsovits", "duration_ms": None}),  # 流式引擎合成未完
        (_FINISHED, {"engine": "voicebox", "duration_ms": 3500}),
        (_FAILED, {"speech_text": "这句没播出来", "engine": "gptsovits", "error_message": "WebSocket closed"}),
    ],
    ids=["started", "started-streaming", "finished", "failed"],
)
async def test_emit_utterance_event_carries_fields_and_is_silent_without_bus_or_id(
    kind: tuple, kwargs: dict, event_bus: EventBus
) -> None:
    emit, event_name, model = kind
    captured: list[tuple[str, object]] = []

    async def capture(name: str, data: object, source: str) -> None:
        captured.append((name, data))

    event_bus.on(event_name, capture, model_class=model)

    await emit(event_bus, utterance_id="utt_1", **kwargs)
    await emit(event_bus, utterance_id=None, **kwargs)  # 手动/直调场景无 utterance 上下文：静默跳过
    await emit(None, utterance_id="utt_x", **kwargs)  # type: ignore[arg-type]  # 无总线：静默不抛
    await event_bus.cleanup()  # 等后台派发完成

    ((name, payload),) = captured
    assert name == event_name and isinstance(payload, model) and payload.utterance_id == "utt_1"
    assert payload.timestamp_ms > 0
    assert all(getattr(payload, key) == value for key, value in kwargs.items())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("还扛得住😱 现在一边砍树", "还扛得住， 现在一边砍树"),  # emoji 映射为逗号，保留停顿边界，不让前后文粘连
        ("我是麦麦～今天带你们玩生存！", "我是麦麦，今天带你们玩生存！"),  # 波浪号无发音价值，服务端 cut5 也不切
        ("吓死我了😱😱——完蛋", "吓死我了，完蛋"),  # 连续符号合并为单个逗号
        ("50%↑【材料】——搞定", "50，材料，搞定"),  # 装饰/数学符号映射为停顿
        ("🎉🎉", "，"),  # 纯符号退化为单个逗号，调用方负责空检查后跳过
        (
            "大家好，我是麦麦。要三连？嗯…：好；来、试试!",
            "大家好，我是麦麦。要三连？嗯…：好；来、试试!",
        ),  # cut5 认识的切分标点保留
        (
            "don't stop, state-of-the-art costs 3.14",
            "don't stop, state-of-the-art costs 3.14",
        ),  # 英文构词符与小数点保留
    ],
)
def test_normalize_tts_text(text: str, expected: str) -> None:
    assert normalize_tts_text(text) == expected
