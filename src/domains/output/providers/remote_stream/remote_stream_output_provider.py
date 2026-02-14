"""
RemoteStream Output Provider - 远程流媒体输出Provider

职责:
- 通过WebSocket实现与边缘设备的音视频双向传输
- 处理图像请求和TTS发送请求
- 提供服务器和客户端两种模式
"""

import asyncio
import base64
import io
import json
import time
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import RemoteStreamRequestImagePayload
from src.modules.logging import get_logger
from src.modules.types.base.output_provider import OutputProvider
from src.modules.types.intent import Intent

if TYPE_CHECKING:
    from src.modules.streaming.audio_chunk import AudioChunk, AudioMetadata

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
    from websockets.server import WebSocketServerProtocol
except ImportError:
    websockets = None
    WebSocketServerProtocol = None
    ConnectionClosed = Exception

try:
    import numpy as np
except ImportError:
    np = None

try:
    from PIL import Image
except ImportError:
    Image = None


class MessageType(str, Enum):
    """WebSocket消息类型"""

    HELLO = "hello"
    CONFIG = "config"
    AUDIO_DATA = "audio_data"
    AUDIO_REQUEST = "audio_req"
    IMAGE_DATA = "image_data"
    IMAGE_REQUEST = "image_req"
    TTS_DATA = "tts_data"
    STATUS = "status"
    ERROR = "error"


class StreamMessage(BaseModel):
    """通用WebSocket消息结构"""

    type: str
    data: Dict[str, Any]
    timestamp: float = 0.0
    sequence: int = 0

    def to_json(self) -> str:
        """转换为JSON字符串"""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, json_str: str) -> "StreamMessage":
        """从JSON字符串创建消息对象"""
        return cls.model_validate_json(json_str)


class AudioConfig(BaseModel):
    """音频配置"""

    sample_rate: int = 16000
    channels: int = 1
    format: str = "int16"
    chunk_size: int = 1024


class ImageConfig(BaseModel):
    """图像配置"""

    width: int = 640
    height: int = 480
    format: str = "jpeg"
    quality: int = 80


class RemoteStreamOutputProvider(OutputProvider):
    """远程流媒体输出Provider"""

    class ConfigSchema(BaseProviderConfig):
        """远程流输出Provider配置"""

        type: str = "remote_stream"

        # WebSocket配置
        server_mode: bool = Field(default=True, description="服务器模式")
        host: str = Field(default="0.0.0.0", description="主机地址")
        port: int = Field(default=8765, ge=1, le=65535, description="端口")

        # 音频/图像配置
        audio_sample_rate: int = Field(default=16000, description="音频采样率")
        audio_channels: int = Field(default=1, description="音频通道数")
        audio_format: str = Field(default="int16", description="音频格式")
        audio_chunk_size: int = Field(default=1024, description="音频块大小")

        image_width: int = Field(default=640, description="图像宽度")
        image_height: int = Field(default=480, description="图像高度")
        image_format: str = Field(default="jpeg", description="图像格式")
        image_quality: int = Field(default=80, description="图像压缩质量")

        # 重连配置
        reconnect_delay: int = Field(default=5, description="重连延迟(秒)")
        max_reconnect_attempts: int = Field(default=-1, description="最大重连次数(-1表示无限)")

    def __init__(self, config: Dict[str, Any]):
        """
        初始化RemoteStream输出Provider

        Args:
            config: 配置字典，包含:
                - server_mode: 服务器模式 (默认: True)
                - host: 主机地址 (默认: "0.0.0.0" 或 "127.0.0.1")
                - port: 端口 (默认: 8765)
                - audio_sample_rate, audio_channels, etc.: 音频配置
                - image_width, image_height, etc.: 图像配置
        """
        super().__init__(config)
        self.logger = get_logger("RemoteStreamOutputProvider")

        # 设置状态
        self.is_setup = False

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        self.typed_config = self.ConfigSchema(**config)

        # WebSocket配置
        self.server_mode = self.typed_config.server_mode
        self.host = self.typed_config.host
        self.port = self.typed_config.port

        # 音频配置
        self.audio_config = AudioConfig(
            sample_rate=self.typed_config.audio_sample_rate,
            channels=self.typed_config.audio_channels,
            format=self.typed_config.audio_format,
            chunk_size=self.typed_config.audio_chunk_size,
        )

        # 图像配置
        self.image_config = ImageConfig(
            width=self.typed_config.image_width,
            height=self.typed_config.image_height,
            format=self.typed_config.image_format,
            quality=self.typed_config.image_quality,
        )

        # WebSocket服务器/客户端相关
        self.server = None
        self.server_task: Optional[asyncio.Task] = None
        self.client = None
        self.client_task: Optional[asyncio.Task] = None
        self.connections = set()  # 活跃的客户端连接集合
        self.active_connection: Optional[WebSocketServerProtocol] = None
        self.connect_task: Optional[asyncio.Task] = None
        self._is_connected = False
        self._sequence_counter = 0

        # 重连控制
        self.should_reconnect = True
        self.reconnect_count = 0

        # 回调注册
        self.audio_callbacks: Dict[str, List[Callable]] = {"data": []}
        self.image_callbacks: Dict[str, List[Callable]] = {"data": []}

        # AudioStreamChannel 订阅
        self._remote_subscription_id: Optional[str] = None

    async def init(self):
        """初始化 Provider"""
        # 检查依赖
        if websockets is None:
            self.logger.error("websockets库未安装，请使用 'uv add websockets' 安装")
            return

        # 注册事件监听
        if self.event_bus:
            self.event_bus.on(
                CoreEvents.REMOTE_STREAM_REQUEST_IMAGE,
                self._handle_image_request,
                RemoteStreamRequestImagePayload,
            )

        # 注册 AudioStreamChannel 订阅
        audio_channel = self.audio_stream_channel
        if audio_channel:
            from src.modules.streaming.backpressure import BackpressureStrategy, SubscriberConfig

            self._remote_subscription_id = await audio_channel.subscribe(
                name="remote_stream",
                on_audio_chunk=self._on_audio_chunk_received,
                on_audio_start=self._on_audio_start,
                on_audio_end=self._on_audio_end,
                config=SubscriberConfig(queue_size=200, backpressure_strategy=BackpressureStrategy.DROP_NEWEST),
            )
            self.logger.info("RemoteStream 已订阅 AudioStreamChannel")

        self.is_setup = True

        # 启动WebSocket服务器或客户端
        if self.server_mode:
            self.server_task = asyncio.create_task(self._run_server())
            self.logger.info(f"WebSocket服务器启动中，监听地址: {self.host}:{self.port}")
        else:
            self.connect_task = asyncio.create_task(self._connect_client())
            self.logger.info(f"WebSocket客户端启动中，连接地址: {self.host}:{self.port}")

        self.logger.info("RemoteStreamOutputProvider 已设置")

    async def execute(self, intent: Intent):
        """
        执行意图

        Args:
            intent: 决策意图
        """
        # 如果有回复文本，发送到远程设备
        if intent.response_text:
            # TTS 音频通过 AudioStreamChannel 传输，这里只发送字幕
            self.logger.debug(f"准备发送字幕数据: {intent.response_text[:50]}...")
            await self._send_subtitle(intent.response_text)

    async def cleanup(self):
        """清理资源"""
        self.logger.info("正在清理RemoteStreamOutputProvider...")

        # 取消 AudioStreamChannel 订阅
        audio_channel = self.audio_stream_channel
        if audio_channel and self._remote_subscription_id:
            try:
                await audio_channel.unsubscribe(self._remote_subscription_id)
                self.logger.info("RemoteStream 已取消订阅 AudioStreamChannel")
            except Exception as e:
                self.logger.error(f"取消 AudioStreamChannel 订阅失败: {e}")
            finally:
                self._remote_subscription_id = None

        # 取消自动重连
        self.should_reconnect = False

        # 取消并等待服务器/客户端任务
        if self.server_task and not self.server_task.done():
            self.server_task.cancel()
            try:
                await self.server_task
            except asyncio.CancelledError:
                pass

        if self.connect_task and not self.connect_task.done():
            self.connect_task.cancel()
            try:
                await self.connect_task
            except asyncio.CancelledError:
                pass

        # 关闭WebSocket连接
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        # 安全地关闭活跃连接
        if self.active_connection:
            try:
                if self._is_websocket_connected(self.active_connection):
                    await self.active_connection.close()
            except Exception as e:
                self.logger.warning(f"关闭连接时发生错误: {e}")
            finally:
                self.active_connection = None

        # 取消事件监听
        if self.event_bus:
            self.event_bus.off(
                CoreEvents.REMOTE_STREAM_REQUEST_IMAGE,
                self._handle_image_request,
                RemoteStreamRequestImagePayload,
            )

        self._is_connected = False
        self.is_setup = False
        self.logger.info("RemoteStreamOutputProvider 已清理")

    # ===== 连接管理 =====

    def _is_websocket_connected(self, ws) -> bool:
        """检查WebSocket连接是否活跃"""
        if ws is None:
            return False

        try:
            # 新版本使用state属性
            if hasattr(ws, "state"):
                from websockets.protocol import State

                return ws.state == State.OPEN
            # 检查closed属性
            elif hasattr(ws, "closed"):
                return not ws.closed
            # 检查是否有open属性
            elif hasattr(ws, "open"):
                return ws.open
            # 对于客户端连接，检查close_code
            elif hasattr(ws, "close_code"):
                return ws.close_code is None
            else:
                return True
        except (AttributeError, Exception):
            return False

    def is_connected(self) -> bool:
        """检查是否已连接到边缘设备"""
        if self.server_mode:
            return len(self.connections) > 0
        else:
            return self._is_websocket_connected(self.active_connection)

    async def _run_server(self):
        """运行WebSocket服务器"""
        self.logger.info(f"启动WebSocket服务器: {self.host}:{self.port}")

        try:
            # 兼容不同版本的websockets库
            try:
                # 新版本 (>= 12.0)
                async def connection_handler(websocket):
                    return await self._handle_connection(websocket)

                self.server = await websockets.serve(connection_handler, self.host, self.port)
            except TypeError:
                # 旧版本 (< 12.0)
                async def legacy_handler(websocket, path):
                    return await self._handle_connection(websocket)

                self.server = await websockets.serve(legacy_handler, self.host, self.port)

            self.logger.info(f"WebSocket服务器已启动: {self.host}:{self.port}")

            # 保持服务器运行
            try:
                await asyncio.Future()  # 无限等待，直到被取消
            except asyncio.CancelledError:
                self.logger.info("服务器任务被取消")
                raise

        except Exception as e:
            self.logger.error(f"WebSocket服务器启动失败: {e}", exc_info=True)

    async def _handle_connection(self, websocket):
        """处理客户端连接"""
        self.logger.info(f"新客户端连接: {websocket.remote_address}")

        # 添加到连接集合
        self.connections.add(websocket)
        self._is_connected = True

        # 发送配置信息
        await self._send_config(websocket)

        try:
            # 处理消息
            async for message_str in websocket:
                try:
                    message = StreamMessage.from_json(message_str)
                    await self._process_message(message, websocket)
                except json.JSONDecodeError:
                    self.logger.warning(f"收到无效JSON: {message_str[:100]}...")
                except Exception as e:
                    self.logger.error(f"处理消息时发生错误: {e}", exc_info=True)
        except ConnectionClosed:
            self.logger.info(f"客户端断开连接: {websocket.remote_address}")
        finally:
            # 从连接集合中移除
            self.connections.discard(websocket)
            if not self.connections:
                self._is_connected = False

    async def _connect_client(self):
        """连接到WebSocket服务器"""
        uri = f"ws://{self.host}:{self.port}"

        max_attempts = self.typed_config.max_reconnect_attempts
        reconnect_delay = self.typed_config.reconnect_delay

        while self.should_reconnect:
            # 检查重连次数
            if max_attempts > 0 and self.reconnect_count >= max_attempts:
                self.logger.error(f"达到最大重连次数({max_attempts})，停止重连")
                break

            try:
                self.logger.info(f"尝试连接到WebSocket服务器: {uri}")

                async with websockets.connect(uri) as websocket:
                    self.active_connection = websocket
                    self._is_connected = True
                    self.reconnect_count = 0  # 重置重连计数

                    self.logger.info(f"已连接到WebSocket服务器: {uri}")

                    # 发送Hello消息
                    hello_message = StreamMessage(
                        type=MessageType.HELLO, data={"client_info": "RemoteStreamOutputProvider"}
                    )
                    await websocket.send(hello_message.to_json())

                    # 处理消息
                    async for message_str in websocket:
                        try:
                            message = StreamMessage.from_json(message_str)
                            await self._process_message(message, websocket)
                        except json.JSONDecodeError:
                            self.logger.warning(f"收到无效JSON: {message_str[:100]}...")
                        except Exception as e:
                            self.logger.error(f"处理消息时发生错误: {e}", exc_info=True)

                    self.active_connection = None
                    self._is_connected = False

            except (ConnectionClosed, ConnectionRefusedError, OSError) as e:
                self.logger.warning(f"WebSocket连接失败 ({type(e).__name__}): {e}")
                self.active_connection = None
                self._is_connected = False
                self.reconnect_count += 1

            if self.should_reconnect:
                self.logger.info(f"{reconnect_delay}秒后尝试重新连接...")
                await asyncio.sleep(reconnect_delay)

    async def _send_config(self, websocket):
        """发送配置信息到客户端"""
        config_message = StreamMessage(
            type=MessageType.CONFIG,
            data={"audio": self.audio_config.model_dump(), "image": self.image_config.model_dump()},
        )

        await websocket.send(config_message.to_json())
        self.logger.debug("已发送配置信息到客户端")

    # ===== 消息处理 =====

    async def _process_message(self, message: StreamMessage, websocket):
        """处理收到的消息"""
        message_type = message.type

        if message_type == MessageType.HELLO:
            self.logger.info(f"收到Hello消息: {message.data.get('client_info', '未知客户端')}")

        elif message_type == MessageType.CONFIG:
            # 更新配置
            if "audio" in message.data:
                self.audio_config = AudioConfig(**message.data["audio"])
            if "image" in message.data:
                self.image_config = ImageConfig(**message.data["image"])
            self.logger.debug("已更新配置")

        elif message_type == MessageType.AUDIO_DATA:
            # 处理接收到的音频数据
            await self._handle_audio_data(message.data)

        elif message_type == MessageType.IMAGE_DATA:
            # 处理接收到的图像数据
            await self._handle_image_data(message.data)

        elif message_type == MessageType.IMAGE_REQUEST:
            # 边缘设备请求一帧图像
            self.logger.debug("收到图像请求")
            # 触发图像请求事件
            if self.event_bus:
                payload = RemoteStreamRequestImagePayload(timestamp=message.data.get("timestamp", time.time()))
                await self.event_bus.emit(CoreEvents.REMOTE_STREAM_REQUEST_IMAGE, payload)

        elif message_type == MessageType.TTS_DATA:
            # 服务器向边缘设备发送的TTS数据（在客户端模式下会收到）
            self.logger.debug("收到TTS音频数据")

        elif message_type == MessageType.STATUS:
            # 状态更新
            self.logger.info(f"收到状态更新: {message.data}")

        elif message_type == MessageType.ERROR:
            # 错误信息
            self.logger.error(f"远程设备报告错误: {message.data.get('message', '未知错误')}")

        else:
            self.logger.warning(f"收到未知类型消息: {message_type}")

    async def _handle_audio_data(self, audio_data: Dict[str, Any]):
        """处理音频数据"""
        try:
            # 解码Base64数据
            if "audio" in audio_data and isinstance(audio_data["audio"], str):
                binary_data = base64.b64decode(audio_data["audio"])
                audio_data["binary"] = binary_data

                # 通知回调
                await self._notify_audio_callbacks(audio_data)
        except Exception as e:
            self.logger.error(f"处理音频数据失败: {e}", exc_info=True)

    async def _handle_image_data(self, image_data: Dict[str, Any]):
        """处理图像数据"""
        try:
            # 解码Base64数据
            if "image" in image_data and isinstance(image_data["image"], str):
                binary_data = base64.b64decode(image_data["image"])
                image_data["binary"] = binary_data

                # 如果需要，可以转换为PIL Image对象
                if Image is not None:
                    try:
                        image_data["pil_image"] = Image.open(io.BytesIO(binary_data))
                    except Exception:
                        pass

                # 通知回调
                await self._notify_image_callbacks(image_data)
        except Exception as e:
            self.logger.error(f"处理图像数据失败: {e}", exc_info=True)

    async def _notify_audio_callbacks(self, data: Dict[str, Any]):
        """通知所有音频回调"""
        for callback in self.audio_callbacks["data"]:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
            except Exception as e:
                self.logger.error(f"音频回调执行错误: {e}", exc_info=True)

    async def _notify_image_callbacks(self, data: Dict[str, Any]):
        """通知所有图像回调"""
        for callback in self.image_callbacks["data"]:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
            except Exception as e:
                self.logger.error(f"图像回调执行错误: {e}", exc_info=True)

    # ===== 消息发送 =====

    async def send_message(self, message: StreamMessage) -> bool:
        """发送消息到远程设备"""
        if not self.is_connected():
            self.logger.warning("未连接到远程设备，消息未发送")
            return False

        try:
            # 添加序列号和时间戳
            message.sequence = self._sequence_counter
            self._sequence_counter += 1
            if message.timestamp == 0.0:
                message.timestamp = time.time()

            if self.server_mode:
                # 服务器模式下，发送给所有客户端
                for conn in self.connections:
                    await conn.send(message.to_json())
            else:
                # 客户端模式下，发送到服务器
                await self.active_connection.send(message.to_json())

            return True
        except Exception as e:
            self.logger.error(f"发送消息失败: {e}", exc_info=True)
            return False

    async def request_image(self) -> bool:
        """请求获取一帧图像"""
        message = StreamMessage(type=MessageType.IMAGE_REQUEST, data={"timestamp": time.time()})
        return await self.send_message(message)

    async def send_tts_audio(self, audio_data: bytes, format_info: Optional[Dict[str, Any]] = None) -> bool:
        """发送TTS音频数据到远程设备"""
        if format_info is None:
            format_info = {
                "sample_rate": self.audio_config.sample_rate,
                "channels": self.audio_config.channels,
                "format": self.audio_config.format,
            }

        # Base64编码音频数据
        encoded_data = base64.b64encode(audio_data).decode("utf-8")

        message = StreamMessage(type=MessageType.TTS_DATA, data={"audio": encoded_data, "format": format_info})

        return await self.send_message(message)

    async def _send_subtitle(self, text: str):
        """发送字幕到远程设备"""
        # 可以扩展为发送字幕消息
        self.logger.debug(f"发送字幕: {text}")

    # ===== 回调管理 =====

    def register_audio_callback(self, event: str, callback: Callable):
        """注册音频回调"""
        if event in self.audio_callbacks:
            self.audio_callbacks[event].append(callback)
            self.logger.debug(f"已注册音频{event}事件回调: {callback.__name__}")
        else:
            self.logger.error(f"未知的音频事件类型: {event}")

    def unregister_audio_callback(self, event: str, callback: Callable) -> bool:
        """注销音频回调"""
        if event not in self.audio_callbacks:
            return False

        try:
            self.audio_callbacks[event].remove(callback)
            self.logger.debug(f"已注销音频{event}事件回调: {callback.__name__}")
            return True
        except ValueError:
            return False

    def register_image_callback(self, event: str, callback: Callable):
        """注册图像回调"""
        if event in self.image_callbacks:
            self.image_callbacks[event].append(callback)
            self.logger.debug(f"已注册图像{event}事件回调: {callback.__name__}")
        else:
            self.logger.error(f"未知的图像事件类型: {event}")

    def unregister_image_callback(self, event: str, callback: Callable) -> bool:
        """注销图像回调"""
        if event not in self.image_callbacks:
            return False

        try:
            self.image_callbacks[event].remove(callback)
            self.logger.debug(f"已注销图像{event}事件回调: {callback.__name__}")
            return True
        except ValueError:
            return False

    # ===== 事件处理 =====

    async def _handle_image_request(self, payload: RemoteStreamRequestImagePayload):
        """处理图像请求事件"""
        self.logger.debug(f"收到图像请求: {payload}")
        # 转发为实际的图像请求消息
        await self.request_image()

    # 注意: _handle_tts_request 已移除，TTS 音频现在通过 AudioStreamChannel 传输

    # ===== AudioStreamChannel 回调 =====

    async def _on_audio_start(self, metadata: "AudioMetadata"):
        """AudioStreamChannel: 音频流开始回调（可选实现）"""
        self.logger.debug(f"收到音频流开始通知: {metadata.text[:30] if metadata.text else '(empty)'}...")
        # 可选：实现开始处理逻辑（如通知远程设备准备接收）

    async def _on_audio_chunk_received(self, chunk: "AudioChunk"):
        """AudioStreamChannel: 音频块回调"""
        try:
            # RemoteStream 期望 16000 Hz
            from src.modules.streaming.audio_utils import resample_audio

            audio_data = resample_audio(chunk.data, chunk.sample_rate, 16000)
            await self.send_tts_audio(audio_data)
        except Exception as e:
            self.logger.error(f"处理音频块失败: {e}")

    async def _on_audio_end(self, metadata: "AudioMetadata"):
        """AudioStreamChannel: 音频流结束回调（可选实现）"""
        self.logger.debug("收到音频流结束通知")
        # 可选：实现结束处理逻辑（如通知远程设备音频传输完成）

    # ===== 信息获取 =====

    def get_info(self) -> Dict[str, Any]:
        """
        获取Provider信息

        Returns:
            Provider信息字典
        """
        return {
            "name": self.__class__.__name__,
            "is_setup": self.is_setup,
            "type": "output_provider",
            "server_mode": self.server_mode,
            "host": self.host,
            "port": self.port,
            "is_connected": self._is_connected,
            "connection_count": len(self.connections) if self.server_mode else (1 if self._is_connected else 0),
        }
