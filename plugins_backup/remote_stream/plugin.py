#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Amaidesu Remote Stream Plugin

通过WebSocket协议实现与边缘设备的音视频双向传输：
1. 图像按需获取：当需要图像时才发送请求
2. 音频实时流式传输：支持麦克风音频上传到服务器和TTS音频下发到设备
3. 注册服务接口：供其他插件调用音视频功能

作者: GitHub Copilot
日期: 2025-07-12
"""

import asyncio
import base64
import io
import json
import sys
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

# WebSocket服务器依赖
try:
    import websockets
    from websockets.server import WebSocketServerProtocol
except ImportError:
    print("依赖缺失: 请运行 'pip install websockets' 来启用WebSocket功能", file=sys.stderr)
    websockets = None
    WebSocketServerProtocol = None

# 音频处理依赖
try:
    import numpy as np
except ImportError:
    print("依赖缺失: 请运行 'pip install numpy' 来处理音频数据", file=sys.stderr)
    np = None

# 图像处理依赖
try:
    from PIL import Image
except ImportError:
    print("依赖缺失: 请运行 'pip install Pillow' 来处理图像数据", file=sys.stderr)
    Image = None
from src.core.amaidesu_core import AmaidesuCore
from src.core.plugin_manager import BasePlugin


# 消息类型枚举
class MessageType(str, Enum):
    """WebSocket消息类型"""

    HELLO = "hello"  # 连接建立后的握手消息
    CONFIG = "config"  # 配置消息
    AUDIO_DATA = "audio_data"  # 音频数据
    AUDIO_REQUEST = "audio_req"  # 音频请求
    IMAGE_DATA = "image_data"  # 图像数据
    IMAGE_REQUEST = "image_req"  # 图像请求
    TTS_DATA = "tts_data"  # TTS音频数据
    STATUS = "status"  # 状态更新
    ERROR = "error"  # 错误信息


@dataclass
class StreamMessage:
    """通用WebSocket消息结构"""

    type: str
    data: Dict[str, Any]
    timestamp: float = 0.0
    sequence: int = 0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, json_str: str) -> "StreamMessage":
        """从JSON字符串创建消息对象"""
        data = json.loads(json_str)
        return cls(**data)


@dataclass
class AudioConfig:
    """音频配置"""

    sample_rate: int = 16000  # 采样率
    channels: int = 1  # 通道数
    format: str = "int16"  # 格式（int16, float32等）
    chunk_size: int = 1024  # 块大小


@dataclass
class ImageConfig:
    """图像配置"""

    width: int = 640  # 宽度
    height: int = 480  # 高度
    format: str = "jpeg"  # 格式（jpeg, png等）
    quality: int = 80  # 压缩质量


class RemoteStreamService:
    """远程流服务，供其他插件调用"""

    def __init__(self, plugin):
        """初始化服务接口

        Args:
            plugin: 父插件引用
        """
        self.plugin = plugin
        self.logger = plugin.logger
        self.audio_callbacks: Dict[str, List[Callable]] = {
            "data": [],  # 接收到音频数据时的回调
        }
        self.image_callbacks: Dict[str, List[Callable]] = {
            "data": [],  # 接收到图像数据时的回调
        }

    def register_audio_callback(self, event: str, callback: Callable) -> bool:
        """注册音频事件回调

        Args:
            event: 事件类型 ("data")
            callback: 回调函数

        Returns:
            注册是否成功
        """
        if event not in self.audio_callbacks:
            self.logger.error(f"未知的音频事件类型: {event}")
            return False

        self.audio_callbacks[event].append(callback)
        self.logger.debug(f"已注册音频{event}事件回调: {callback.__name__}")
        return True

    def unregister_audio_callback(self, event: str, callback: Callable) -> bool:
        """注销音频事件回调"""
        if event not in self.audio_callbacks:
            return False

        try:
            self.audio_callbacks[event].remove(callback)
            self.logger.debug(f"已注销音频{event}事件回调: {callback.__name__}")
            return True
        except ValueError:
            return False

    def register_image_callback(self, event: str, callback: Callable) -> bool:
        """注册图像事件回调"""
        if event not in self.image_callbacks:
            self.logger.error(f"未知的图像事件类型: {event}")
            return False

        self.image_callbacks[event].append(callback)
        self.logger.debug(f"已注册图像{event}事件回调: {callback.__name__}")
        return True

    def unregister_image_callback(self, event: str, callback: Callable) -> bool:
        """注销图像事件回调"""
        if event not in self.image_callbacks:
            return False

        try:
            self.image_callbacks[event].remove(callback)
            self.logger.debug(f"已注销图像{event}事件回调: {callback.__name__}")
            return True
        except ValueError:
            return False

    async def request_image(self) -> bool:
        """请求获取一帧图像

        Returns:
            请求是否成功发送
        """
        if not self.plugin.is_connected():
            self.logger.warning("未连接到远程设备，无法请求图像")
            return False

        message = StreamMessage(type=MessageType.IMAGE_REQUEST, data={"timestamp": time.time()})

        return await self.plugin.send_message(message)

    async def send_tts_audio(self, audio_data: bytes, format_info: Optional[Dict[str, Any]] = None) -> bool:
        """发送TTS音频数据到远程设备

        Args:
            audio_data: 音频数据（二进制格式）
            format_info: 音频格式信息

        Returns:
            发送是否成功
        """
        if not self.plugin.is_connected():
            self.logger.warning("未连接到远程设备，无法发送TTS音频")
            return False

        if format_info is None:
            format_info = {
                "sample_rate": self.plugin.audio_config.sample_rate,
                "channels": self.plugin.audio_config.channels,
                "format": self.plugin.audio_config.format,
            }

        # Base64编码音频数据
        encoded_data = base64.b64encode(audio_data).decode("utf-8")

        message = StreamMessage(type=MessageType.TTS_DATA, data={"audio": encoded_data, "format": format_info})

        return await self.plugin.send_message(message)

    def _notify_audio_callbacks(self, data: Dict[str, Any]):
        """通知所有音频回调"""
        for callback in self.audio_callbacks["data"]:
            try:
                callback(data)
            except Exception as e:
                self.logger.error(f"音频回调执行错误: {e}")

    def _notify_image_callbacks(self, data: Dict[str, Any]):
        """通知所有图像回调"""
        for callback in self.image_callbacks["data"]:
            try:
                callback(data)
            except Exception as e:
                self.logger.error(f"图像回调执行错误: {e}")


class RemoteStreamPlugin(BasePlugin):
    """远程流媒体插件 - 实现与边缘设备的音视频双向传输"""

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        """初始化插件

        Args:
            core: Amaidesu核心引用
            plugin_config: 插件配置
        """
        super().__init__(core, plugin_config)

        # 检查必要依赖
        if websockets is None or np is None or Image is None:
            self.logger.error("缺少必要依赖，远程流媒体插件无法正常工作")
            self.enabled = False
            return

        # 配置项
        self.config = self.plugin_config
        self.server_mode = self.config.get("server_mode", True)  # True为服务端模式，False为客户端模式
        self.host = self.config.get("host", "0.0.0.0" if self.server_mode else "127.0.0.1")
        self.port = self.config.get("port", 8765)

        # 音频配置
        audio_cfg = self.config.get("audio", {})
        self.audio_config = AudioConfig(
            sample_rate=audio_cfg.get("sample_rate", 16000),
            channels=audio_cfg.get("channels", 1),
            format=audio_cfg.get("format", "int16"),
            chunk_size=audio_cfg.get("chunk_size", 1024),
        )

        # 图像配置
        image_cfg = self.config.get("image", {})
        self.image_config = ImageConfig(
            width=image_cfg.get("width", 640),
            height=image_cfg.get("height", 480),
            format=image_cfg.get("format", "jpeg"),
            quality=image_cfg.get("quality", 80),
        )

        # WebSocket服务器/客户端相关
        self.server = None
        self.server_task = None
        self.client = None
        self.client_task = None
        self.connections = set()  # 活跃的客户端连接集合
        self.active_connection = None
        self.connect_task = None
        self.reconnect_delay = 5  # 重连延迟（秒）
        self.should_reconnect = True

        # 服务接口
        self.service = RemoteStreamService(self)

        self.logger.info(f"RemoteStreamPlugin 初始化完成，运行模式: {'服务端' if self.server_mode else '客户端'}")

    async def setup(self):
        """插件启动"""
        await super().setup()

        # 注册服务
        self.core.register_service("remote_stream", self.service)
        self.logger.info("已注册 remote_stream 服务")

        # 启动WebSocket服务器或客户端
        if self.server_mode:
            self.server_task = asyncio.create_task(self.run_server())
            self.logger.info(f"WebSocket服务器启动中，监听地址: {self.host}:{self.port}")
        else:
            self.connect_task = asyncio.create_task(self.connect_client())
            self.logger.info(f"WebSocket客户端启动中，连接地址: {self.host}:{self.port}")

    async def cleanup(self):
        """插件清理"""
        self.logger.info("正在清理RemoteStreamPlugin...")

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
                # 检查连接是否仍然打开并关闭
                if self._is_websocket_connected(self.active_connection):
                    await self.active_connection.close()
            except Exception as e:
                self.logger.warning(f"关闭连接时发生错误: {e}")
            finally:
                self.active_connection = None

        await super().cleanup()
        self.logger.info("RemoteStreamPlugin清理完成")

    def _is_websocket_connected(self, ws):
        """检查WebSocket连接是否活跃（兼容新旧版本和不同连接类型）"""
        if ws is None:
            return False

        try:
            # 新版本使用state属性
            if hasattr(ws, "state"):
                from websockets.protocol import State

                return ws.state == State.OPEN
            # 检查closed属性（服务器端连接通常有这个）
            elif hasattr(ws, "closed"):
                return not ws.closed
            # 检查是否有open属性
            elif hasattr(ws, "open"):
                return ws.open
            # 对于客户端连接，检查close_code
            elif hasattr(ws, "close_code"):
                return ws.close_code is None
            else:
                # 最后的回退方案，假设连接是活跃的
                return True
        except (AttributeError, Exception):
            # 如果所有检查都失败，返回False
            return False

    def is_connected(self) -> bool:
        """检查是否已连接到边缘设备"""
        if self.server_mode:
            return len(self.connections) > 0
        else:
            return self._is_websocket_connected(self.active_connection)

    async def send_message(self, message: StreamMessage) -> bool:
        """发送消息到远程设备

        Args:
            message: 要发送的消息

        Returns:
            发送是否成功
        """
        if not self.is_connected():
            self.logger.warning(f"未连接到远程设备，消息未发送: {message.type}")
            return False

        try:
            if self.server_mode:
                # 服务器模式下，发送给所有客户端（通常只有一个）
                for conn in self.connections:
                    await conn.send(message.to_json())
            else:
                # 客户端模式下，发送到服务器
                await self.active_connection.send(message.to_json())

            return True
        except Exception as e:
            self.logger.error(f"发送消息失败: {e}")
            return False

    # ===== 服务端模式方法 =====

    async def run_server(self):
        """运行WebSocket服务器"""
        self.logger.info(f"启动WebSocket服务器: {self.host}:{self.port}")

        try:
            # 尝试检测websockets库版本并使用相应的处理方式
            try:
                # 对于新版本的websockets库 (>= 12.0)
                async def connection_handler(websocket):
                    return await self.handle_connection(websocket)

                self.server = await websockets.serve(connection_handler, self.host, self.port)
            except TypeError:
                # 对于旧版本的websockets库 (< 12.0)
                async def legacy_handler(websocket, path):
                    return await self.handle_connection(websocket)

                self.server = await websockets.serve(legacy_handler, self.host, self.port)

            self.logger.info(f"WebSocket服务器已启动: {self.host}:{self.port}")

            # 保持服务器运行
            try:
                await asyncio.Future()  # 无限等待，直到被取消
            except asyncio.CancelledError:
                self.logger.info("服务器任务被取消")
                raise

        except Exception as e:
            self.logger.error(f"WebSocket服务器启动失败: {e}")

    async def handle_connection(self, websocket):
        """处理客户端连接

        Args:
            websocket: WebSocket连接
        """
        self.logger.info(f"新客户端连接: {websocket.remote_address}")

        # 添加到连接集合
        self.connections.add(websocket)

        # 发送配置信息
        await self.send_config(websocket)

        try:
            # 处理消息
            async for message_str in websocket:
                try:
                    message = StreamMessage.from_json(message_str)
                    await self.process_message(message, websocket)
                except json.JSONDecodeError:
                    self.logger.warning(f"收到无效JSON: {message_str[:100]}...")
                except Exception as e:
                    self.logger.error(f"处理消息时发生错误: {e}")
        except websockets.ConnectionClosed:
            self.logger.info(f"客户端断开连接: {websocket.remote_address}")
        finally:
            # 从连接集合中移除
            self.connections.remove(websocket)

    async def send_config(self, websocket):
        """发送配置信息到客户端"""
        config_message = StreamMessage(
            type=MessageType.CONFIG, data={"audio": asdict(self.audio_config), "image": asdict(self.image_config)}
        )

        await websocket.send(config_message.to_json())
        self.logger.debug("已发送配置信息到客户端")

    # ===== 客户端模式方法 =====

    async def connect_client(self):
        """连接到WebSocket服务器"""
        uri = f"ws://{self.host}:{self.port}"

        while self.should_reconnect:
            try:
                self.logger.info(f"尝试连接到WebSocket服务器: {uri}")

                async with websockets.connect(uri) as websocket:
                    self.active_connection = websocket
                    self.logger.info(f"已连接到WebSocket服务器: {uri}")

                    # 处理消息
                    async for message_str in websocket:
                        try:
                            message = StreamMessage.from_json(message_str)
                            await self.process_message(message, websocket)
                        except json.JSONDecodeError:
                            self.logger.warning(f"收到无效JSON: {message_str[:100]}...")
                        except Exception as e:
                            self.logger.error(f"处理消息时发生错误: {e}")

                    self.active_connection = None

            except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
                self.logger.warning(f"WebSocket连接失败 ({type(e).__name__}): {e}")
                self.active_connection = None

            if self.should_reconnect:
                self.logger.info(f"{self.reconnect_delay}秒后尝试重新连接...")
                await asyncio.sleep(self.reconnect_delay)

    # ===== 消息处理 =====

    async def process_message(self, message: StreamMessage, websocket):
        """处理收到的消息

        Args:
            message: 收到的消息
            websocket: 消息来源的WebSocket连接
        """
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
            audio_data = message.data
            # 解码Base64数据
            if "audio" in audio_data and isinstance(audio_data["audio"], str):
                try:
                    # 解码音频数据
                    binary_data = base64.b64decode(audio_data["audio"])
                    audio_data["binary"] = binary_data

                    # 通知回调
                    self.service._notify_audio_callbacks(audio_data)
                except Exception as e:
                    self.logger.error(f"处理音频数据失败: {e}")

        elif message_type == MessageType.IMAGE_DATA:
            # 处理接收到的图像数据
            image_data = message.data
            # 解码Base64数据
            if "image" in image_data and isinstance(image_data["image"], str):
                try:
                    # 解码图像数据
                    binary_data = base64.b64decode(image_data["image"])
                    image_data["binary"] = binary_data

                    # 如果需要，可以转换为PIL Image对象
                    try:
                        image_data["pil_image"] = Image.open(io.BytesIO(binary_data))
                    except Exception:
                        pass

                    # 通知回调
                    self.service._notify_image_callbacks(image_data)
                except Exception as e:
                    self.logger.error(f"处理图像数据失败: {e}")

        elif message_type == MessageType.IMAGE_REQUEST:
            # 边缘设备请求一帧图像（通常不会发生，因为我们是服务端）
            self.logger.warning("收到意外的图像请求")

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


# 插件入口点
plugin_entrypoint = RemoteStreamPlugin
