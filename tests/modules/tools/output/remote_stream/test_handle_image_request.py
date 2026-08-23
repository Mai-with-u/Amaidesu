"""RemoteStream 消息协议测试（Wave 4 迁移）

原 RemoteStreamHandler 是带 websocket 脚手架的渲染器；Wave 4 拆分后只保留
消息协议与分发器：

- ``MessageType`` / ``StreamMessage`` / ``AudioConfig`` / ``ImageConfig``
- ``RemoteStreamTypes.dispatch_message()`` 消息分发

本文件验证：
- 消息序列化往返（to_json / from_json）
- ``build_*`` 工厂方法生成正确 payload
- ``dispatch_message`` 按 type 分发到对应回调
"""
import base64
import json
import time
from unittest.mock import MagicMock

from src.modules.tools.output.remote_stream import (
    AudioConfig,
    ImageConfig,
    MessageType,
    RemoteStreamTypes,
    StreamMessage,
)


class TestStreamMessageRoundtrip:
    def test_to_json_from_json_roundtrip(self):
        original = StreamMessage(
            type=MessageType.CONFIG,
            data={"audio": {"sample_rate": 16000}, "image": {"width": 640}},
            timestamp=1234567890.0,
            sequence=42,
        )
        restored = StreamMessage.from_json(original.to_json())
        assert restored.type == MessageType.CONFIG
        assert restored.data["audio"]["sample_rate"] == 16000
        assert restored.sequence == 42
        assert abs(restored.timestamp - 1234567890.0) < 0.001


class TestRemoteStreamTypesBuilders:
    def test_build_config_message(self):
        msg = RemoteStreamTypes.build_config_message(
            AudioConfig(sample_rate=16000), ImageConfig(width=640)
        )
        assert msg.type == MessageType.CONFIG
        assert msg.data["audio"]["sample_rate"] == 16000
        assert msg.data["image"]["width"] == 640

    def test_build_hello_message(self):
        msg = RemoteStreamTypes.build_hello_message("test_client")
        assert msg.type == MessageType.HELLO
        assert msg.data["client_info"] == "test_client"

    def test_build_image_request(self):
        before = time.time()
        msg = RemoteStreamTypes.build_image_request()
        after = time.time()
        assert msg.type == MessageType.IMAGE_REQUEST
        assert before <= msg.data["timestamp"] <= after

    def test_build_tts_data(self):
        audio_bytes = b"\x01\x02\x03\x04test_audio_data"
        msg = RemoteStreamTypes.build_tts_data(audio_bytes, {"format": "int16"})
        assert msg.type == MessageType.TTS_DATA
        decoded = base64.b64decode(msg.data["audio"])
        assert decoded == audio_bytes
        assert msg.data["format"]["format"] == "int16"


class TestDispatchMessage:
    def test_dispatch_to_hello(self):
        on_hello = MagicMock()
        RemoteStreamTypes.dispatch_message(
            StreamMessage(type=MessageType.HELLO, data={"client_info": "x"}),
            on_hello=on_hello,
        )
        on_hello.assert_called_once_with({"client_info": "x"})

    def test_dispatch_to_image_request(self):
        on_image_request = MagicMock()
        RemoteStreamTypes.dispatch_message(
            StreamMessage(type=MessageType.IMAGE_REQUEST, data={"timestamp": 1.0}),
            on_image_request=on_image_request,
        )
        on_image_request.assert_called_once()

    def test_dispatch_to_tts_data(self):
        """TTS_DATA 消息直接传给回调（旧实现不自动解码 base64）"""
        on_tts_data = MagicMock()
        RemoteStreamTypes.dispatch_message(
            StreamMessage(
                type=MessageType.TTS_DATA,
                data={"audio": base64.b64encode(b"abc").decode("utf-8"), "format": {"format": "int16"}},
            ),
            on_tts_data=on_tts_data,
        )
        on_tts_data.assert_called_once()

    def test_dispatch_unknown_message_ignored(self):
        """未匹配 type 的消息被静默忽略，不抛异常"""
        # 没有注册任何回调
        RemoteStreamTypes.dispatch_message(
            StreamMessage(type="unknown_type", data={})
        )
        # 不抛异常即通过

    def test_dispatch_to_audio_data_decodes_base64(self):
        on_audio_data = MagicMock()
        audio_bytes = b"\x00\x01\x02\x03"
        RemoteStreamTypes.dispatch_message(
            StreamMessage(
                type=MessageType.AUDIO_DATA,
                data={"audio": base64.b64encode(audio_bytes).decode("utf-8")},
            ),
            on_audio_data=on_audio_data,
        )
        args = on_audio_data.call_args[0][0]
        assert args["binary"] == audio_bytes

    def test_json_serialization_roundtrip(self):
        """完整的消息 JSON 序列化往返"""
        msg = StreamMessage(
            type=MessageType.STATUS,
            data={"state": "online", "uptime": 100},
        )
        decoded = json.loads(msg.to_json())
        assert decoded["type"] == "status"
        assert decoded["data"]["state"] == "online"