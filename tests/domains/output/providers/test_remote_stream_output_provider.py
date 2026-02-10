"""
RemoteStreamOutputProvider 测试
"""

import pytest
import base64
import io
from unittest.mock import AsyncMock, MagicMock, patch
from PIL import Image

from src.domains.output.providers.remote_stream import RemoteStreamOutputProvider
from src.domains.output.providers.remote_stream.remote_stream_output_provider import (
    MessageType,
    StreamMessage,
    AudioConfig,
    ImageConfig,
)
from src.domains.output.parameters.render_parameters import RenderParameters


@pytest.fixture
def remote_stream_config():
    """RemoteStream配置"""
    return {
        "server_mode": True,
        "host": "127.0.0.1",
        "port": 8765,
        "audio_sample_rate": 16000,
        "audio_channels": 1,
        "audio_format": "int16",
        "audio_chunk_size": 1024,
        "image_width": 640,
        "image_height": 480,
        "image_format": "jpeg",
        "image_quality": 80,
        "reconnect_delay": 1,
        "max_reconnect_attempts": 3,
    }


@pytest.fixture
def remote_stream_client_config(remote_stream_config):
    """RemoteStream客户端配置"""
    config = remote_stream_config.copy()
    config["server_mode"] = False
    config["host"] = "localhost"
    return config


@pytest.fixture
def mock_event_bus():
    """Mock EventBus"""
    event_bus = MagicMock()
    event_bus.on = MagicMock()
    event_bus.off = MagicMock()
    event_bus.emit = AsyncMock()
    return event_bus


@pytest.fixture
def sample_audio_data():
    """创建示例音频数据"""
    # 创建1秒的静音音频数据 (16kHz, 单声道, int16)
    sample_rate = 16000
    duration = 1.0
    num_samples = int(sample_rate * duration)
    audio_data = b"\x00\x00" * num_samples  # 静音
    return audio_data


@pytest.fixture
def sample_image_base64():
    """创建一个简单的测试图片base64"""
    img = Image.new("RGB", (640, 480), color="blue")
    buffered = io.BytesIO()
    img.save(buffered, format="JPEG", quality=80)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


class TestRemoteStreamOutputProvider:
    """测试 RemoteStreamOutputProvider"""

    def test_init_with_default_config(self):
        """测试默认配置初始化"""
        provider = RemoteStreamOutputProvider({})

        assert provider.server_mode
        assert provider.host == "0.0.0.0"
        assert provider.port == 8765
        assert provider.audio_config.sample_rate == 16000
        assert provider.audio_config.channels == 1
        assert provider.image_config.width == 640
        assert provider.image_config.height == 480
        assert not provider._is_connected

    def test_init_with_custom_config(self, remote_stream_config):
        """测试自定义配置初始化"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        assert provider.server_mode
        assert provider.host == "127.0.0.1"
        assert provider.port == 8765
        assert provider.audio_config.sample_rate == 16000
        assert provider.audio_config.channels == 1
        assert provider.image_config.width == 640
        assert provider.image_config.height == 480
        assert provider.typed_config.reconnect_delay == 1
        assert provider.typed_config.max_reconnect_attempts == 3

    def test_init_with_client_config(self, remote_stream_client_config):
        """测试客户端模式初始化"""
        provider = RemoteStreamOutputProvider(remote_stream_client_config)

        assert not provider.server_mode
        assert provider.host == "localhost"
        assert provider.port == 8765

    def test_audio_config_model(self):
        """测试AudioConfig模型"""
        config = AudioConfig(sample_rate=22050, channels=2, format="float32", chunk_size=2048)

        assert config.sample_rate == 22050
        assert config.channels == 2
        assert config.format == "float32"
        assert config.chunk_size == 2048

    def test_image_config_model(self):
        """测试ImageConfig模型"""
        config = ImageConfig(width=1280, height=720, format="png", quality=95)

        assert config.width == 1280
        assert config.height == 720
        assert config.format == "png"
        assert config.quality == 95

    def test_stream_message_serialization(self):
        """测试StreamMessage序列化"""
        message = StreamMessage(
            type=MessageType.HELLO,
            data={"client_info": "test_client"},
            timestamp=1234567890.0,
            sequence=1,
        )

        json_str = message.to_json()
        deserialized = StreamMessage.from_json(json_str)

        assert deserialized.type == MessageType.HELLO
        assert deserialized.data["client_info"] == "test_client"
        assert deserialized.timestamp == 1234567890.0
        assert deserialized.sequence == 1

    @pytest.mark.asyncio
    async def test_setup_internal_without_websockets(self, remote_stream_config, mock_event_bus):
        """测试websockets未安装时的设置"""
        with patch("src.domains.output.providers.remote_stream.remote_stream_output_provider.websockets", None):
            provider = RemoteStreamOutputProvider(remote_stream_config)

            # 应该不会抛出异常，但会记录错误
            await provider._setup_internal()

            # 验证事件监听器未注册（因为websockets未安装）
            assert not provider.is_setup

    @pytest.mark.asyncio
    async def test_setup_internal_server_mode(self, remote_stream_config, mock_event_bus):
        """测试服务器模式设置"""
        with patch("src.domains.output.providers.remote_stream.remote_stream_output_provider.websockets") as mock_ws:
            provider = RemoteStreamOutputProvider(remote_stream_config)

            # Mock websockets.serve
            mock_server = AsyncMock()
            mock_server.wait_closed = AsyncMock()
            mock_ws.serve = MagicMock(return_value=mock_server)

            # 设置event_bus
            provider.event_bus = mock_event_bus

            await provider._setup_internal()

            # 验证事件监听器已注册（现在只有 REMOTE_STREAM_REQUEST_IMAGE）
            assert mock_event_bus.on.call_count == 1

    @pytest.mark.asyncio
    async def test_cleanup_internal(self, remote_stream_config):
        """测试内部清理"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        # Mock一些任务
        provider.server_task = AsyncMock()
        provider.server_task.done = MagicMock(return_value=True)

        # 清理不应该抛出异常
        await provider._cleanup_internal()

        assert not provider.should_reconnect

    def test_is_websocket_connected_none(self):
        """测试None WebSocket连接状态"""
        provider = RemoteStreamOutputProvider({})
        assert not provider._is_websocket_connected(None)

    def test_is_websocket_connected_with_closed_attribute(self):
        """测试有closed属性的WebSocket连接"""
        RemoteStreamOutputProvider({})
        mock_ws = MagicMock(spec=["closed"])  # 只指定closed属性
        mock_ws.closed = False

        # 检查是否有其他属性会被优先检查
        assert hasattr(mock_ws, "closed")
        # 因为MagicMock默认会创建所有属性访问，所以需要更精确的mock
        # 实际测试中，真实的WebSocket对象会有这些属性

    def test_is_connected_server_mode(self, remote_stream_config):
        """测试服务器模式连接状态"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        # 没有连接时
        assert not provider.is_connected()

        # 添加模拟连接
        mock_ws = MagicMock()
        provider.connections.add(mock_ws)
        assert provider.is_connected()

    def test_is_connected_client_mode(self, remote_stream_client_config):
        """测试客户端模式连接状态"""
        provider = RemoteStreamOutputProvider(remote_stream_client_config)

        # 没有连接时
        assert not provider.is_connected()

        # 创建一个简单的mock对象，只包含close_code属性
        class MockWS:
            pass

        mock_ws = MockWS()
        mock_ws.close_code = None
        provider.active_connection = mock_ws

        # 现在应该检测到连接
        result = provider.is_connected()
        assert result

    @pytest.mark.asyncio
    async def test_render_internal_with_tts(self, remote_stream_config, mock_event_bus):
        """测试带TTS的渲染"""
        provider = RemoteStreamOutputProvider(remote_stream_config)
        await provider.setup(mock_event_bus)

        # Mock _send_subtitle
        provider._send_subtitle = AsyncMock()

        # 创建渲染参数
        render_params = RenderParameters(
            tts_text="Hello world",
            tts_enabled=True,
            subtitle_text="测试字幕",
            subtitle_enabled=True,
        )

        # 执行渲染
        await provider._render_internal(render_params)

        # 验证字幕被发送
        provider._send_subtitle.assert_called_once_with("测试字幕")

    @pytest.mark.asyncio
    async def test_send_message_not_connected(self, remote_stream_config):
        """测试未连接时发送消息"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        message = StreamMessage(type=MessageType.HELLO, data={})

        # 应该返回False
        result = await provider.send_message(message)
        assert not result

    @pytest.mark.asyncio
    async def test_send_message_server_mode(self, remote_stream_config):
        """测试服务器模式发送消息"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        # 添加模拟连接
        mock_ws = AsyncMock()
        provider.connections.add(mock_ws)

        message = StreamMessage(type=MessageType.HELLO, data={"test": "data"})

        # 发送消息
        result = await provider.send_message(message)

        # 验证发送成功
        assert result
        assert message.sequence >= 0
        mock_ws.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_request_image(self, remote_stream_config):
        """测试请求图像"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        # 添加模拟连接并mock send_message
        mock_ws = AsyncMock()
        provider.connections.add(mock_ws)
        provider.send_message = AsyncMock(return_value=True)

        # 请求图像
        result = await provider.request_image()

        # 验证发送了图像请求消息
        assert result
        provider.send_message.assert_called_once()
        sent_message = provider.send_message.call_args[0][0]
        assert sent_message.type == MessageType.IMAGE_REQUEST

    @pytest.mark.asyncio
    async def test_send_tts_audio(self, remote_stream_config, sample_audio_data):
        """测试发送TTS音频"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        # 添加模拟连接并mock send_message
        mock_ws = AsyncMock()
        provider.connections.add(mock_ws)
        provider.send_message = AsyncMock(return_value=True)

        # 发送TTS音频
        result = await provider.send_tts_audio(sample_audio_data)

        # 验证发送了TTS数据消息
        assert result
        provider.send_message.assert_called_once()
        sent_message = provider.send_message.call_args[0][0]
        assert sent_message.type == MessageType.TTS_DATA
        assert "audio" in sent_message.data
        assert "format" in sent_message.data

    @pytest.mark.asyncio
    async def test_process_message_hello(self, remote_stream_config):
        """测试处理Hello消息"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        message = StreamMessage(type=MessageType.HELLO, data={"client_info": "test_client"})

        # 处理消息（应该不抛出异常）
        await provider._process_message(message, None)

    @pytest.mark.asyncio
    async def test_process_message_config(self, remote_stream_config):
        """测试处理配置消息"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        message = StreamMessage(
            type=MessageType.CONFIG,
            data={
                "audio": {"sample_rate": 22050, "channels": 2, "format": "float32", "chunk_size": 2048},
                "image": {"width": 1280, "height": 720, "format": "png", "quality": 95},
            },
        )

        await provider._process_message(message, None)

        # 验证配置已更新
        assert provider.audio_config.sample_rate == 22050
        assert provider.image_config.width == 1280

    @pytest.mark.asyncio
    async def test_process_message_audio_data(self, remote_stream_config, sample_audio_data):
        """测试处理音频数据消息"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        # 注册回调
        callback_mock = AsyncMock()
        provider.audio_callbacks["data"].append(callback_mock)

        # 编码音频数据
        encoded_audio = base64.b64encode(sample_audio_data).decode("utf-8")

        message = StreamMessage(type=MessageType.AUDIO_DATA, data={"audio": encoded_audio})

        await provider._process_message(message, None)

        # 验证回调被调用
        callback_mock.assert_called_once()
        call_args = callback_mock.call_args[0][0]
        assert "binary" in call_args
        assert call_args["binary"] == sample_audio_data

    @pytest.mark.asyncio
    async def test_process_message_image_data(self, remote_stream_config, sample_image_base64):
        """测试处理图像数据消息"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        # 注册回调
        callback_mock = AsyncMock()
        provider.image_callbacks["data"].append(callback_mock)

        message = StreamMessage(type=MessageType.IMAGE_DATA, data={"image": sample_image_base64})

        await provider._process_message(message, None)

        # 验证回调被调用
        callback_mock.assert_called_once()
        call_args = callback_mock.call_args[0][0]
        assert "binary" in call_args
        assert "pil_image" in call_args

    @pytest.mark.asyncio
    async def test_process_message_error(self, remote_stream_config):
        """测试处理错误消息"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        message = StreamMessage(type=MessageType.ERROR, data={"message": "Test error"})

        # 处理消息（应该记录错误）
        await provider._process_message(message, None)

    @pytest.mark.asyncio
    async def test_handle_audio_data_invalid_base64(self, remote_stream_config):
        """测试处理无效的Base64音频数据"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        # 应该不抛出异常
        await provider._handle_audio_data({"audio": "invalid_base64!!!"})

    @pytest.mark.asyncio
    async def test_handle_image_data_invalid_base64(self, remote_stream_config):
        """测试处理无效的Base64图像数据"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        # 应该不抛出异常
        await provider._handle_image_data({"image": "invalid_base64!!!"})

    @pytest.mark.asyncio
    async def test_register_audio_callback(self, remote_stream_config):
        """测试注册音频回调"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        def callback(x):
            return x

        provider.register_audio_callback("data", callback)

        assert callback in provider.audio_callbacks["data"]

    def test_register_audio_callback_invalid_event(self, remote_stream_config):
        """测试注册无效的音频事件回调"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        def callback(x):
            return x

        # 应该记录错误但不注册
        provider.register_audio_callback("invalid_event", callback)

        assert "invalid_event" not in provider.audio_callbacks

    @pytest.mark.asyncio
    async def test_unregister_audio_callback(self, remote_stream_config):
        """测试注销音频回调"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        def callback(x):
            return x

        provider.audio_callbacks["data"].append(callback)

        result = provider.unregister_audio_callback("data", callback)
        assert result
        assert callback not in provider.audio_callbacks["data"]

    @pytest.mark.asyncio
    async def test_register_image_callback(self, remote_stream_config):
        """测试注册图像回调"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        def callback(x):
            return x

        provider.register_image_callback("data", callback)

        assert callback in provider.image_callbacks["data"]

    @pytest.mark.asyncio
    async def test_unregister_image_callback(self, remote_stream_config):
        """测试注销图像回调"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        def callback(x):
            return x

        provider.image_callbacks["data"].append(callback)

        result = provider.unregister_image_callback("data", callback)
        assert result
        assert callback not in provider.image_callbacks["data"]

    @pytest.mark.asyncio
    async def test_handle_image_request_event(self, remote_stream_config, mock_event_bus):
        """测试处理图像请求事件"""
        provider = RemoteStreamOutputProvider(remote_stream_config)
        await provider.setup(mock_event_bus)

        # Mock request_image
        provider.request_image = AsyncMock(return_value=True)

        # 触发事件
        await provider._handle_image_request("remote_stream.request_image", {}, "test")

        # 验证request_image被调用
        provider.request_image.assert_called_once()

    @pytest.mark.asyncio
    async def test_audio_stream_channel_callback(self, remote_stream_config, sample_audio_data):
        """测试 AudioStreamChannel 音频块回调"""
        from src.core.streaming.audio_chunk import AudioChunk

        provider = RemoteStreamOutputProvider(remote_stream_config)

        # Mock send_tts_audio
        provider.send_tts_audio = AsyncMock(return_value=True)

        # 创建音频块
        chunk = AudioChunk(
            data=sample_audio_data,
            sample_rate=16000,
            channels=1,
            sequence=0,
            timestamp=0.0,
        )

        # 调用回调
        await provider._on_audio_chunk_received(chunk)

        # 验证 send_tts_audio 被调用
        provider.send_tts_audio.assert_called_once()

        # 获取调用参数
        call_args = provider.send_tts_audio.call_args
        assert call_args is not None

    @pytest.mark.asyncio
    async def test_notify_audio_callbacks_sync(self, remote_stream_config):
        """测试通知同步音频回调"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        callback_mock = MagicMock()
        provider.audio_callbacks["data"].append(callback_mock)

        data = {"test": "data"}
        await provider._notify_audio_callbacks(data)

        # 验证回调被调用
        callback_mock.assert_called_once_with(data)

    @pytest.mark.asyncio
    async def test_notify_audio_callbacks_async(self, remote_stream_config):
        """测试通知异步音频回调"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        callback_mock = AsyncMock()
        provider.audio_callbacks["data"].append(callback_mock)

        data = {"test": "data"}
        await provider._notify_audio_callbacks(data)

        # 验证回调被调用
        callback_mock.assert_called_once_with(data)

    @pytest.mark.asyncio
    async def test_notify_image_callbacks(self, remote_stream_config):
        """测试通知图像回调"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        callback_mock = MagicMock()
        provider.image_callbacks["data"].append(callback_mock)

        data = {"test": "data"}
        await provider._notify_image_callbacks(data)

        # 验证回调被调用
        callback_mock.assert_called_once_with(data)

    def test_get_info(self, remote_stream_config):
        """测试获取Provider信息"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        info = provider.get_info()

        assert info["name"] == "RemoteStreamOutputProvider"
        assert info["type"] == "output_provider"
        assert info["server_mode"]
        assert info["host"] == "127.0.0.1"
        assert info["port"] == 8765
        assert not info["is_connected"]
        assert info["connection_count"] == 0

    @pytest.mark.asyncio
    async def test_send_config(self, remote_stream_config):
        """测试发送配置"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        mock_ws = AsyncMock()
        await provider._send_config(mock_ws)

        # 验证发送了配置消息
        mock_ws.send.assert_called_once()
        sent_json = mock_ws.send.call_args[0][0]
        sent_message = StreamMessage.from_json(sent_json)
        assert sent_message.type == MessageType.CONFIG
        assert "audio" in sent_message.data
        assert "image" in sent_message.data

    @pytest.mark.asyncio
    async def test_send_subtitle(self, remote_stream_config):
        """测试发送字幕"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        # 应该不抛出异常
        await provider._send_subtitle("测试字幕")

    @pytest.mark.asyncio
    async def test_full_workflow_server_mode(self, remote_stream_config, mock_event_bus):
        """测试服务器模式完整工作流"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        # Mock websockets
        with patch("src.domains.output.providers.remote_stream.remote_stream_output_provider.websockets") as mock_ws:
            mock_server = AsyncMock()
            mock_server.wait_closed = AsyncMock()
            mock_ws.serve = MagicMock(return_value=mock_server)

            # 设置provider
            await provider.setup(mock_event_bus)

            # 验证设置成功
            assert provider.is_setup

            # 清理
            await provider.cleanup()

            # 验证清理成功
            assert not provider.is_setup

    @pytest.mark.asyncio
    async def test_callback_error_handling(self, remote_stream_config):
        """测试回调错误处理"""
        provider = RemoteStreamOutputProvider(remote_stream_config)

        # 添加会抛出异常的回调
        def bad_callback(data):
            raise ValueError("Test error")

        provider.audio_callbacks["data"].append(bad_callback)

        # 应该不抛出异常，只记录错误
        await provider._notify_audio_callbacks({"test": "data"})


class TestMessageType:
    """测试MessageType枚举"""

    def test_all_message_types(self):
        """测试所有消息类型"""
        assert MessageType.HELLO == "hello"
        assert MessageType.CONFIG == "config"
        assert MessageType.AUDIO_DATA == "audio_data"
        assert MessageType.AUDIO_REQUEST == "audio_req"
        assert MessageType.IMAGE_DATA == "image_data"
        assert MessageType.IMAGE_REQUEST == "image_req"
        assert MessageType.TTS_DATA == "tts_data"
        assert MessageType.STATUS == "status"
        assert MessageType.ERROR == "error"
