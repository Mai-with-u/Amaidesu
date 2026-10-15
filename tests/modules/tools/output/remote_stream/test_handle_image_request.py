"""RemoteStream 消息协议：``StreamMessage`` 序列化往返、``build_*`` 工厂的 payload、``dispatch_message`` 按 type 分发。"""

import base64
import json
import time
from unittest.mock import MagicMock

import pytest

from src.modules.tools.output.remote_stream import (
    AudioConfig,
    ImageConfig,
    MessageType,
    RemoteStreamTypes,
    StreamMessage,
)


def test_json_round_trip_keeps_type_data_sequence_and_timestamp() -> None:
    original = StreamMessage(
        type=MessageType.CONFIG,
        data={"audio": {"sample_rate": 16000}, "image": {"width": 640}},
        timestamp=1234567890.0,
        sequence=42,
    )

    restored = StreamMessage.from_json(original.to_json())

    assert (restored.type, restored.sequence) == (MessageType.CONFIG, 42)
    assert restored.data["audio"]["sample_rate"] == 16000
    assert abs(restored.timestamp - 1234567890.0) < 0.001
    decoded = json.loads(StreamMessage(type=MessageType.STATUS, data={"state": "online"}).to_json())
    assert (decoded["type"], decoded["data"]["state"]) == ("status", "online")


def test_builders_produce_the_expected_payloads() -> None:
    config = RemoteStreamTypes.build_config_message(AudioConfig(sample_rate=16000), ImageConfig(width=640))
    assert config.type == MessageType.CONFIG
    assert (config.data["audio"]["sample_rate"], config.data["image"]["width"]) == (16000, 640)

    hello = RemoteStreamTypes.build_hello_message("test_client")
    assert (hello.type, hello.data["client_info"]) == (MessageType.HELLO, "test_client")

    before = time.time()
    request = RemoteStreamTypes.build_image_request()
    assert request.type == MessageType.IMAGE_REQUEST and before <= request.data["timestamp"] <= time.time()

    audio_bytes = b"\x01\x02\x03\x04test_audio_data"
    tts = RemoteStreamTypes.build_tts_data(audio_bytes, {"format": "int16"})
    assert tts.type == MessageType.TTS_DATA and tts.data["format"]["format"] == "int16"
    assert base64.b64decode(tts.data["audio"]) == audio_bytes


@pytest.mark.parametrize(
    ("message_type", "data", "callback", "expected"),
    [
        (MessageType.HELLO, {"client_info": "x"}, "on_hello", {"client_info": "x"}),
        (MessageType.IMAGE_REQUEST, {"timestamp": 1.0}, "on_image_request", {"timestamp": 1.0}),
        # TTS_DATA 原样交给回调（不自动解码 base64），AUDIO_DATA 则解码出 binary
        (MessageType.TTS_DATA, {"audio": "YWJj", "format": {"format": "int16"}}, "on_tts_data", None),
        (MessageType.AUDIO_DATA, {"audio": base64.b64encode(b"\x00\x01\x02\x03").decode()}, "on_audio_data", None),
    ],
)
def test_dispatch_routes_each_type_to_its_callback(message_type, data, callback, expected) -> None:
    handler = MagicMock()

    RemoteStreamTypes.dispatch_message(StreamMessage(type=message_type, data=data), **{callback: handler})

    handler.assert_called_once()
    received = handler.call_args[0][0]
    if expected is not None:
        assert received == expected
    elif message_type == MessageType.AUDIO_DATA:
        assert received["binary"] == b"\x00\x01\x02\x03"
    else:
        assert received["audio"] == "YWJj"


def test_dispatch_ignores_unknown_types_silently() -> None:
    RemoteStreamTypes.dispatch_message(StreamMessage(type="unknown_type", data={}))
