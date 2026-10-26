"""
stt 采集器注册 + 基本 import 测试

覆盖：
- STTCollector 继承 BaseCollector
- 元数据正确
- 配置 Schema 嵌套结构（iflytek_asr/vad/audio/message_config）正确加载
- 识别结果经基类 publish_room_message 统一扇出为 partner_speech 事件
"""

from __future__ import annotations

import asyncio
from typing import List

from src.modules.collectors.base import BaseCollector
from src.modules.collectors.stt import STTCollector
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser


def test_stt_inherits_base_collector() -> None:
    """STTCollector 继承 BaseCollector"""
    assert issubclass(STTCollector, BaseCollector)


def test_stt_metadata() -> None:
    """元数据正确"""
    assert STTCollector.name == "stt"
    assert "STT" in STTCollector.description or "语音" in STTCollector.description


def test_stt_config_schema_loads_nested() -> None:
    """配置 Schema 嵌套结构正确加载（来自 iflytek_asr/vad/audio/message_config）"""
    cfg = STTCollector.ConfigSchema.from_dict(
        {
            "iflytek_asr": {
                "appid": "test_appid",
                "api_key": "test_key",
                "api_secret": "test_secret",
            },
            "vad": {
                "enable": True,
                "vad_threshold": 0.5,
                "silence_seconds": 1.0,
            },
            "audio": {
                "sample_rate": 16000,
                "channels": 1,
                "dtype": "int16",
            },
            "message_config": {
                "user_id": "stt_user",
                "user_nickname": "语音",
            },
        }
    )
    assert cfg.iflytek_asr.appid == "test_appid"
    assert cfg.vad.enable is True
    assert cfg.audio.sample_rate == 16000
    assert cfg.message_config.user_nickname == "语音"


class _FakeBus:
    def __init__(self) -> None:
        self.events: List[tuple] = []

    async def emit(self, event_name: str, payload: object, **kwargs: object) -> None:
        self.events.append((event_name, payload))


class _FakeXunfei:
    """只提供 _drain_results 消费的 result_queue，替代完整讯飞客户端。"""

    def __init__(self, payloads: List[RoomMessagePayload]) -> None:
        self.result_queue: asyncio.Queue = asyncio.Queue()
        for payload in payloads:
            self.result_queue.put_nowait(payload)


def _bare_collector(bus: _FakeBus, xunfei: _FakeXunfei) -> STTCollector:
    """绕过重依赖构造（torch/sounddevice/讯飞连接）拿实例，只为驱动 _drain_results。"""
    collector = STTCollector.__new__(STTCollector)
    collector.name = "stt"
    collector._event_bus = bus
    collector._xunfei = xunfei
    return collector


async def test_stt_results_fan_out_via_publish_room_message() -> None:
    """识别结果逐条经基类统一扇出口发布为 partner_speech 语义事件，并原样交还消费方。"""
    bus = _FakeBus()
    payload = RoomMessagePayload(
        message_type="partner_speech",
        user=RoomMessageUser(id="partner", name="联动对象"),
        content="识别文本",
    )
    collector = _bare_collector(bus, _FakeXunfei([payload]))

    drained = [item async for item in collector._drain_results()]

    assert drained == [payload]
    assert bus.events == [(CoreEvents.ROOM_MESSAGE_PARTNER_SPEECH, payload)]
