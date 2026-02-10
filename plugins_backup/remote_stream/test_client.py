#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Remote Stream 客户端测试脚本
模拟边缘设备，用于测试 remote_stream 插件的功能

功能：
1. 连接到 remote_stream 服务器
2. 模拟发送音频数据（可以是麦克风录音或测试音频）
3. 模拟发送图像数据（可以是摄像头或测试图像）
4. 接收并播放 TTS 音频数据

使用方法：
python test_client.py --host localhost --port 8765 --mode audio,image
"""

import argparse
import asyncio
import base64
import json
import sys
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict

# WebSocket客户端依赖
try:
    import websockets
    from websockets.client import WebSocketClientProtocol
except ImportError:
    print("依赖缺失: 请运行 'pip install websockets' 来启用WebSocket功能", file=sys.stderr)
    websockets = None
    WebSocketClientProtocol = None

# 音频处理依赖
try:
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    print("依赖缺失: 请运行 'pip install numpy sounddevice soundfile' 来处理音频", file=sys.stderr)
    np = None
    sd = None
    sf = None

# 图像处理依赖
try:
    import cv2
    from PIL import Image
except ImportError:
    print("依赖缺失: 请运行 'pip install Pillow opencv-python' 来处理图像", file=sys.stderr)
    Image = None
    cv2 = None


# 消息类型枚举
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


class RemoteStreamTestClient:
    """远程流测试客户端"""

    def __init__(self, host: str = "localhost", port: int = 8765):
        """初始化测试客户端

        Args:
            host: 服务器地址
            port: 服务器端口
        """
        self.host = host
        self.port = port
        self.uri = f"ws://{host}:{port}"
        self.websocket = None
        self.running = False

        # 音频配置
        self.audio_config = {"sample_rate": 16000, "channels": 1, "format": "int16", "chunk_size": 512}

        # 图像配置
        self.image_config = {"width": 1280, "height": 720, "format": "jpeg", "quality": 85}

        # 模式控制
        self.send_audio = False
        self.send_image = False
        self.camera_index = 0

        # TTS音频流处理
        self.tts_pcm_buffer = bytearray()  # PCM数据缓冲区
        self.audio_stream = None  # 音频输出流
        self.is_playing_tts = False  # 是否正在播放TTS
        self.wav_header_processed = False  # 是否已处理WAV头
        self.tts_sample_rate = 32000  # TTS默认采样率
        self.tts_channels = 1  # TTS默认通道数
        self.tts_format = np.int16  # TTS默认格式
        self.buffer_size = 1024  # 播放缓冲区大小

        # 状态追踪属性将在需要时动态创建
        self._audio_shape_reported = False

    async def connect(self):
        """连接到服务器"""
        try:
            print(f"连接到 {self.uri}...")
            self.websocket = await websockets.connect(self.uri)
            print("连接成功！")

            # 发送Hello消息
            hello_msg = StreamMessage(
                type=MessageType.HELLO, data={"client_info": "RemoteStreamTestClient", "version": "1.0"}
            )
            await self.send_message(hello_msg)

            return True
        except Exception as e:
            print(f"连接失败: {e}")
            return False

    async def send_message(self, message: StreamMessage):
        """发送消息到服务器"""
        if self.websocket:
            await self.websocket.send(message.to_json())

    async def handle_message(self, message_str: str):
        """处理来自服务器的消息"""
        try:
            message = StreamMessage.from_json(message_str)

            if message.type == MessageType.CONFIG:
                print(f"收到配置: {message.data}")

            elif message.type == MessageType.TTS_DATA:
                print("收到TTS音频数据")
                await self.handle_tts_audio(message.data)

            elif message.type == MessageType.IMAGE_REQUEST:
                print("收到图像请求")
                await self.send_image()

            elif message.type == MessageType.STATUS:
                print(f"收到状态更新: {message.data}")

            elif message.type == MessageType.ERROR:
                print(f"收到错误信息: {message.data}")

        except Exception as e:
            print(f"处理消息时出错: {e}")

    async def handle_tts_audio(self, data: Dict[str, Any]):
        """处理TTS音频数据"""
        if "audio" in data and sd is not None:
            try:
                # 解码音频数据
                audio_bytes = base64.b64decode(data["audio"])
                format_info = data.get("format", {})

                # 获取格式参数（仅用于非WAV格式）
                channels = format_info.get("channels", 1)

                # 判断是否是新的音频段
                is_new_audio = format_info.get("start_flag", False)

                # 检查是否是WAV格式并处理头部
                if (
                    not self.wav_header_processed
                    and len(audio_bytes) > 44
                    and audio_bytes.startswith(b"RIFF")
                    and b"WAVE" in audio_bytes[0:12]
                ):
                    print("检测到WAV头部数据")
                    # 这也表明是新的音频段
                    is_new_audio = True

                # 如果是新的音频段，重置状态
                if is_new_audio:
                    print("处理新的TTS音频段")
                    self.reset_audio_state()
                    # 解析WAV头
                    wav_info = self.extract_wav_header_info(audio_bytes)
                    if wav_info:
                        self.tts_sample_rate, wav_channels, bits_per_sample, data_offset, data_size = wav_info
                        print(f"WAV信息: {self.tts_sample_rate}Hz, {wav_channels}通道, {bits_per_sample}位")

                        # 覆盖格式参数
                        if wav_channels > 0:
                            channels = wav_channels

                        # 提取PCM数据部分并添加到缓冲区
                        pcm_data = audio_bytes[data_offset : data_offset + data_size]

                        # 确保PCM数据格式与声道数匹配
                        # 对于16位整数音频，每个样本2字节
                        bytes_per_sample = bits_per_sample // 8
                        if bytes_per_sample == 0:
                            bytes_per_sample = 2  # 默认为16位

                        # 确保数据长度是声道数和样本大小的整数倍
                        sample_count = len(pcm_data) // (bytes_per_sample * channels)
                        valid_size = sample_count * bytes_per_sample * channels

                        if valid_size != len(pcm_data):
                            print(f"警告: PCM数据长度不是完整帧的整数倍，截断为{valid_size}字节")
                            pcm_data = pcm_data[:valid_size]

                        # 添加到缓冲区
                        self.tts_pcm_buffer.extend(pcm_data)
                        self.wav_header_processed = True
                    else:
                        print("WAV头部解析失败，按原始格式处理")
                        self.tts_pcm_buffer.extend(audio_bytes)
                else:
                    # 非首个块或非WAV格式，直接添加到缓冲区
                    self.tts_pcm_buffer.extend(audio_bytes)

                # 如果还没有创建音频流或采样率发生变化，或者是新的音频段，则创建新的音频流
                if self.audio_stream is None or not self.is_playing_tts or is_new_audio:
                    if self.audio_stream is not None:
                        try:
                            self.audio_stream.stop()
                            self.audio_stream.close()
                        except Exception as e:
                            print(f"关闭旧音频流时出错: {e}")

                    try:
                        # 确保声道数至少为1
                        if channels <= 0:
                            print(f"警告：无效的声道数 {channels}，使用默认值1")
                            channels = 1

                        print(f"尝试创建音频流: {self.tts_sample_rate}Hz, {channels}通道, 块大小: {self.buffer_size}")
                        self.audio_stream = self.start_tts_stream(
                            sample_rate=self.tts_sample_rate,
                            channels=channels,
                            dtype=self.tts_format,
                            blocksize=self.buffer_size,
                        )

                        if self.audio_stream:
                            self.audio_stream.start()
                            self.is_playing_tts = True
                            print(f"成功启动TTS音频流: {self.tts_sample_rate}Hz, {channels}通道")
                        else:
                            print("音频流创建失败！")
                    except Exception as e:
                        print(f"创建和启动音频流时出错: {e}")
                        self.is_playing_tts = False

                # 强制开始播放（如果缓冲区有足够数据）
                if not self.is_playing_tts and len(self.tts_pcm_buffer) > self.buffer_size * 4:
                    try:
                        if self.audio_stream:
                            self.audio_stream.start()
                            self.is_playing_tts = True
                            print(f"重新开始播放: 缓冲区大小 {len(self.tts_pcm_buffer)} 字节")
                    except Exception as e:
                        print(f"重新开始播放失败: {e}")

                print(f"添加到TTS缓冲区: {len(audio_bytes)} 字节, 当前缓冲区大小: {len(self.tts_pcm_buffer)} 字节")

            except Exception as e:
                print(f"处理TTS音频失败: {e}")

    async def send_audio_loop(self):
        """音频发送循环"""
        if not sd:
            print("sounddevice 不可用，跳过音频发送")
            return

        print("开始音频发送循环...")

        # 音频缓冲区
        audio_buffer = []

        def audio_callback(indata, frames, timeinfo, status):
            """音频回调函数"""
            if status:
                print(f"音频输入状态: {status}")

            # 转换为int16格式
            if indata.dtype == np.float32:
                audio_int16 = (indata * 32767.0).astype(np.int16)
            else:
                audio_int16 = indata.astype(np.int16)

            # 确保单声道
            if audio_int16.ndim > 1:
                audio_int16 = audio_int16[:, 0]

            audio_buffer.append(audio_int16.tobytes())

        # 启动音频流
        try:
            stream = sd.InputStream(
                samplerate=self.audio_config["sample_rate"],
                channels=self.audio_config["channels"],
                dtype="float32",
                blocksize=self.audio_config["chunk_size"],
                callback=audio_callback,
            )

            stream.start()
            print(f"音频流已启动: {self.audio_config['sample_rate']}Hz, {self.audio_config['channels']}ch")

            while self.running and self.send_audio:
                if audio_buffer:
                    # 发送缓冲的音频数据
                    audio_bytes = audio_buffer.pop(0)

                    # 编码为Base64
                    encoded_audio = base64.b64encode(audio_bytes).decode("utf-8")

                    # 发送音频消息
                    audio_msg = StreamMessage(
                        type=MessageType.AUDIO_DATA, data={"audio": encoded_audio, "format": self.audio_config}
                    )

                    await self.send_message(audio_msg)
                    # print(f"发送音频数据: {len(audio_bytes)} 字节")

                await asyncio.sleep(0.01)  # 10ms间隔

            stream.stop()
            stream.close()
            print("音频流已停止")

        except Exception as e:
            print(f"音频发送出错: {e}")

    async def send_image_loop(self):
        """图像发送循环"""
        if not cv2:
            print("OpenCV 不可用，跳过图像发送")
            return

        print("开始图像发送循环...")

        # 尝试打开摄像头
        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            print(f"无法打开摄像头 {self.camera_index}")
            return

        try:
            while self.running and self.send_image:
                ret, frame = cap.read()
                if not ret:
                    print("无法获取摄像头帧")
                    break

                # 调整图像大小
                frame = cv2.resize(frame, (self.image_config["width"], self.image_config["height"]))

                # 编码为JPEG
                _, buffer = cv2.imencode(
                    f".{self.image_config['format']}", frame, [cv2.IMWRITE_JPEG_QUALITY, self.image_config["quality"]]
                )

                # 编码为Base64
                encoded_image = base64.b64encode(buffer).decode("utf-8")

                # 发送图像消息
                image_msg = StreamMessage(
                    type=MessageType.IMAGE_DATA, data={"image": encoded_image, "format": self.image_config}
                )

                await self.send_message(image_msg)
                print(f"发送图像数据: {len(buffer)} 字节")

                await asyncio.sleep(0.5)  # 500ms间隔

        except Exception as e:
            print(f"图像发送出错: {e}")
        finally:
            cap.release()
            print("摄像头已释放")

    async def send_image(self):
        """发送单帧图像（响应请求）"""
        if not cv2:
            return

        # 打开摄像头并获取一帧
        cap = cv2.VideoCapture(self.camera_index)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret:
                # 调整图像大小
                frame = cv2.resize(frame, (self.image_config["width"], self.image_config["height"]))

                # 编码为JPEG
                _, buffer = cv2.imencode(
                    f".{self.image_config['format']}", frame, [cv2.IMWRITE_JPEG_QUALITY, self.image_config["quality"]]
                )

                # 编码为Base64
                encoded_image = base64.b64encode(buffer).decode("utf-8")

                # 发送图像消息
                image_msg = StreamMessage(
                    type=MessageType.IMAGE_DATA, data={"image": encoded_image, "format": self.image_config}
                )

                await self.send_message(image_msg)
                print(f"响应图像请求: {len(buffer)} 字节")

            cap.release()

    async def run(self, modes: list):
        """运行测试客户端"""
        self.send_audio = "audio" in modes
        self.send_image = "image" in modes

        if not await self.connect():
            return

        self.running = True

        # 重置音频状态
        self.tts_pcm_buffer.clear()
        self.is_playing_tts = False
        self.reset_audio_state()

        # 启动任务
        tasks = []

        # 消息接收任务
        async def message_receiver():
            try:
                async for message in self.websocket:
                    await self.handle_message(message)
            except websockets.exceptions.ConnectionClosed:
                print("服务器断开连接")
            except Exception as e:
                print(f"接收消息时出错: {e}")

        tasks.append(asyncio.create_task(message_receiver()))

        # 音频发送任务
        if self.send_audio:
            tasks.append(asyncio.create_task(self.send_audio_loop()))

        # 图像发送任务
        if self.send_image:
            tasks.append(asyncio.create_task(self.send_image_loop()))

        try:
            print("测试客户端正在运行... 按 Ctrl+C 停止")
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            print("\n收到停止信号...")
        finally:
            self.running = False

            # 取消所有任务
            for task in tasks:
                if not task.done():
                    task.cancel()

            # 关闭连接
            if self.websocket:
                await self.websocket.close()

            # 关闭音频流
            if self.audio_stream:
                try:
                    self.audio_stream.stop()
                    self.audio_stream.close()
                    self.audio_stream = None
                    print("音频流已关闭")
                except Exception as e:
                    print(f"关闭音频流时出错: {e}")

            print("测试客户端已停止")

    def start_tts_stream(self, sample_rate=32000, channels=1, dtype=np.int16, blocksize=1024):
        """创建并启动TTS音频播放流

        Args:
            sample_rate: 采样率
            channels: 声道数
            dtype: 数据类型
            blocksize: 每次处理的帧数

        Returns:
            创建的音频流对象
        """
        if sd is None:
            print("sounddevice 不可用，无法创建音频流")
            return None

        def audio_callback(outdata, frames, timeinfo, status):
            """音频回调函数，从缓冲区读取数据并输出"""
            nonlocal dtype, channels

            try:
                if status:
                    print(f"音频输出状态: {status}")

                # 检查输出缓冲区的实际形状 - 仅在首次回调时输出
                actual_channels = 1 if outdata.ndim == 1 else outdata.shape[1]
                if not hasattr(self, "_audio_shape_reported"):
                    self._audio_shape_reported = True
                    print(f"输出缓冲区形状: {outdata.shape}, 预期声道数: {channels}, 实际声道数: {actual_channels}")

                if actual_channels != channels:
                    if not hasattr(self, "_channels_mismatch_reported"):
                        self._channels_mismatch_reported = True
                        print(f"警告: 输出缓冲区的声道数({actual_channels})与配置的声道数({channels})不匹配")
                    # 调整声道数以匹配实际情况
                    channels = actual_channels

                bytes_per_sample = np.dtype(dtype).itemsize
                bytes_per_frame = bytes_per_sample * channels
                bytes_needed = frames * bytes_per_frame

                # PCM缓冲区检查和状态更新
                buffer_size = len(self.tts_pcm_buffer)

                # 判断是否有足够数据
                if buffer_size >= bytes_needed:
                    # 从缓冲区读取一块数据
                    chunk = bytes(self.tts_pcm_buffer[:bytes_needed])

                    # 移除已使用的数据
                    del self.tts_pcm_buffer[:bytes_needed]

                    # 转换为NumPy数组并复制到输出缓冲区
                    audio_data = np.frombuffer(chunk, dtype=dtype)

                    # 如果缓冲区用尽，更新状态标志，以便下次回调时检测结束
                    if len(self.tts_pcm_buffer) < bytes_needed and not hasattr(self, "_buffer_depleting"):
                        self._buffer_depleting = True
                        print(f"PCM缓冲区即将用尽: 剩余{len(self.tts_pcm_buffer)}字节")

                    # 处理通道形状
                    if channels > 1:
                        # 确保数据能正确地重新成形
                        if len(audio_data) == frames * channels:
                            try:
                                audio_data = audio_data.reshape(-1, channels)
                            except ValueError as e:
                                print(f"重新成形音频数据失败: {e}")
                                # 如果重新成形失败，复制数据到所有声道
                                print("尝试通过复制单声道数据到所有声道来解决问题")
                                audio_data_multi = np.zeros((frames, channels), dtype=dtype)
                                for ch in range(channels):
                                    audio_data_multi[: min(frames, len(audio_data)), ch] = audio_data[
                                        : min(frames, len(audio_data))
                                    ]
                                audio_data = audio_data_multi
                        else:
                            # 数据大小不匹配，调整到适当大小
                            print(f"警告: 音频数据大小不匹配 ({len(audio_data)} != {frames * channels})")
                            if len(audio_data) > frames * channels:
                                # 截断多余数据
                                try:
                                    audio_data = audio_data[: frames * channels].reshape(-1, channels)
                                except ValueError as e:
                                    print(f"截断后重新成形失败: {e}")
                                    # 复制单声道数据到所有声道
                                    audio_data_multi = np.zeros((frames, channels), dtype=dtype)
                                    for ch in range(channels):
                                        audio_data_multi[: min(frames, len(audio_data)), ch] = audio_data[
                                            : min(frames, len(audio_data))
                                        ]
                                    audio_data = audio_data_multi
                            else:
                                # 数据不足，填充零
                                print("数据不足，填充零")
                                try:
                                    temp = np.zeros(frames * channels, dtype=dtype)
                                    temp[: len(audio_data)] = audio_data
                                    audio_data = temp.reshape(-1, channels)
                                except ValueError as e:
                                    print(f"填充零后重新成形失败: {e}")
                                    # 创建新的多声道数组
                                    audio_data_multi = np.zeros((frames, channels), dtype=dtype)
                                    for ch in range(channels):
                                        audio_data_multi[: min(frames, len(audio_data)), ch] = audio_data[
                                            : min(frames, len(audio_data))
                                        ]
                                    audio_data = audio_data_multi

                    # 复制到输出缓冲区，确保不会越界
                    out_frames = min(len(audio_data) if channels == 1 else len(audio_data) // channels, frames)

                    # 处理形状不匹配问题
                    if channels == 1:
                        # 单声道情况
                        if outdata.ndim == 2:
                            # 如果输出是二维的，需要调整输入以匹配
                            outdata[:out_frames, 0] = audio_data[:out_frames]
                        else:
                            # 如果输出是一维的，直接赋值
                            outdata[:out_frames] = audio_data[:out_frames]
                    else:
                        # 多声道情况
                        if audio_data.ndim == 1:
                            # 如果输入是一维的，需要复制到所有声道
                            for ch in range(channels):
                                outdata[:out_frames, ch] = audio_data[:out_frames]
                        else:
                            # 正常的多声道数据
                            outdata[:out_frames, :] = audio_data[:out_frames, :]

                    # 如果数据不足一帧，填充零
                    if out_frames < frames:
                        outdata[out_frames:] = 0
                else:
                    # 缓冲区数据不足，填充零
                    outdata.fill(0)

                    # 检查缓冲区状态
                    current_buffer_size = len(self.tts_pcm_buffer)

                    # 检查是否有一些数据但不足一帧
                    if current_buffer_size > 0:
                        # 仅在数据量变化超过阈值或首次记录时输出消息
                        if (
                            not hasattr(self, "_last_buffer_size")
                            or abs(self._last_buffer_size - current_buffer_size) > bytes_needed * 0.5
                        ):
                            self._last_buffer_size = current_buffer_size
                            # 不频繁输出此类日志 - 使用导入的time模块，而非回调参数
                            current_time = time.time()
                            if not hasattr(self, "_buffer_low_logged") or current_time - self._buffer_low_logged > 2.0:
                                print(f"缓冲区数据不足一帧: {current_buffer_size}/{bytes_needed}字节")
                                self._buffer_low_logged = current_time
                    # 检查是否已经播放完毕且缓冲区为空
                    elif self.is_playing_tts:
                        # 避免重复输出播放完成日志
                        if not hasattr(self, "_playback_complete_logged"):
                            print("TTS音频播放完成")
                            self._playback_complete_logged = True
                            # 通过异步方式安全地更新播放状态
                            asyncio.run_coroutine_threadsafe(self._update_play_status(False), asyncio.get_event_loop())
                    # 否则静默等待
            except Exception as e:
                print(f"音频回调出错: {e}")
                outdata.fill(0)  # 确保输出有效，即使发生错误

        # 创建输出流
        try:
            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype=dtype,
                blocksize=blocksize,
                callback=audio_callback,
            )
            print(f"已创建TTS音频流: {sample_rate}Hz, {channels}通道")
            return stream
        except Exception as e:
            print(f"创建TTS音频流失败: {e}")
            return None

    def extract_wav_header_info(self, wav_data):
        """从WAV头部提取音频参数信息

        Args:
            wav_data: WAV格式的二进制数据

        Returns:
            (sample_rate, channels, bits_per_sample, data_offset, data_size) 元组，如果无效则返回None
        """
        try:
            # WAV文件格式: RIFF(4) + 文件大小(4) + WAVE(4) + fmt(4) + 块大小(4) + ...
            if len(wav_data) < 44 or not wav_data.startswith(b"RIFF") or b"WAVE" not in wav_data[0:12]:
                print(f"WAV头部格式无效: 长度={len(wav_data)}, 头部={wav_data[:20]}")
                return None

            print(f"WAV头部: {wav_data[:12]}")

            # 查找fmt块
            fmt_pos = wav_data.find(b"fmt ")
            if fmt_pos < 0:
                print("未找到fmt块")
                return None

            print(f"fmt块位置: {fmt_pos}")

            # 从fmt块解析参数
            # fmt块后4字节是块大小，然后是格式类型(2字节)，声道数(2字节)，采样率(4字节)...
            fmt_size = int.from_bytes(wav_data[fmt_pos + 4 : fmt_pos + 8], byteorder="little")
            print(f"fmt块大小: {fmt_size}")

            format_type = int.from_bytes(wav_data[fmt_pos + 8 : fmt_pos + 10], byteorder="little")
            print(f"音频格式类型: {format_type} (1=PCM)")

            channels = int.from_bytes(wav_data[fmt_pos + 10 : fmt_pos + 12], byteorder="little")
            sample_rate = int.from_bytes(wav_data[fmt_pos + 12 : fmt_pos + 16], byteorder="little")

            # 字节率 = 采样率 × 每个样本的字节数 × 通道数
            byte_rate = int.from_bytes(wav_data[fmt_pos + 16 : fmt_pos + 20], byteorder="little")
            print(f"字节率: {byte_rate} 字节/秒")

            # 块对齐 = 每个样本的字节数 × 通道数
            block_align = int.from_bytes(wav_data[fmt_pos + 20 : fmt_pos + 22], byteorder="little")
            print(f"块对齐: {block_align} 字节")

            bits_per_sample = int.from_bytes(wav_data[fmt_pos + 22 : fmt_pos + 24], byteorder="little")

            # 查找data块
            data_pos = wav_data.find(b"data")
            if data_pos < 0:
                print("未找到data块，尝试查找其他可能的标记位置")

                # 尝试查找其他可能的标记位置
                offset = 36  # 标准WAV头部之后的位置
                while offset < min(len(wav_data), 100):
                    if offset + 4 <= len(wav_data):
                        chunk_id = wav_data[offset : offset + 4]
                        print(f"在位置 {offset} 找到块: {chunk_id}")
                        if chunk_id == b"data":
                            data_pos = offset
                            break
                        if offset + 8 <= len(wav_data):
                            chunk_size = int.from_bytes(wav_data[offset + 4 : offset + 8], byteorder="little")
                            offset += 8 + chunk_size
                        else:
                            offset += 1
                    else:
                        break

                if data_pos < 0:
                    print("无法找到data块，假设数据紧随fmt块")
                    # 假设数据紧随fmt块之后
                    data_pos = fmt_pos + 8 + fmt_size
                    if data_pos + 8 <= len(wav_data):
                        print(f"假设data块位置: {data_pos}, 数据: {wav_data[data_pos : data_pos + 8]}")
                    else:
                        return None

            print(f"data块位置: {data_pos}")

            # data块后4字节是数据大小
            if data_pos + 8 <= len(wav_data):
                data_size = int.from_bytes(wav_data[data_pos + 4 : data_pos + 8], byteorder="little")
                data_offset = data_pos + 8  # 数据开始位置
            else:
                print("data块不完整，假设剩余所有数据都是音频数据")
                data_offset = data_pos + 4
                data_size = len(wav_data) - data_offset

            print(f"完整WAV信息: 采样率={sample_rate}Hz, 通道数={channels}, 位深={bits_per_sample}位")
            print(f"数据区: 偏移={data_offset}, 大小={data_size}字节")

            # 验证数据块大小
            if data_offset + data_size > len(wav_data):
                print(f"警告: 数据块大小声明为 {data_size} 字节，但实际可用数据仅 {len(wav_data) - data_offset} 字节")
                data_size = len(wav_data) - data_offset

            return (sample_rate, channels, bits_per_sample, data_offset, data_size)
        except Exception as e:
            print(f"解析WAV头失败: {e}")
            import traceback

            traceback.print_exc()
            return None

    async def _update_play_status(self, is_playing):
        """安全地更新播放状态（从回调线程调用）

        Args:
            is_playing: 是否正在播放
        """
        # 避免重复更新相同状态
        if self.is_playing_tts == is_playing:
            return

        self.is_playing_tts = is_playing
        print(f"音频播放状态更新为: {'播放中' if is_playing else '已停止'}")

        if not is_playing:
            # 如果停止播放，可以考虑关闭流
            if self.audio_stream and self.tts_pcm_buffer is not None and len(self.tts_pcm_buffer) == 0:
                # 完全播放完毕，且没有新的数据
                try:
                    # 延迟一段时间后再停止，以防止有新的数据进来
                    await asyncio.sleep(1.0)
                    if len(self.tts_pcm_buffer) == 0 and not self.is_playing_tts:
                        print("播放完成，关闭音频流")
                        self.audio_stream.stop()
                        # 重置标志位，方便下次播放
                        self.reset_audio_state()
                except Exception as e:
                    print(f"停止音频流时出错: {e}")

    def reset_audio_state(self):
        """重置音频相关状态"""
        self.wav_header_processed = False

        # 重置所有标志位
        for attr in [
            "_audio_shape_reported",
            "_channels_mismatch_reported",
            "_last_buffer_size",
            "_playback_complete_logged",
            "_buffer_depleting",
        ]:
            if hasattr(self, attr):
                delattr(self, attr)

        print("音频状态已重置")


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="Remote Stream 测试客户端")
    parser.add_argument("--host", default="localhost", help="服务器地址")
    parser.add_argument("--port", type=int, default=8765, help="服务器端口")
    parser.add_argument("--mode", default="audio,image", help="测试模式: audio,image,tts")
    parser.add_argument("--camera", type=int, default=0, help="摄像头索引")

    args = parser.parse_args()

    # 解析模式
    modes = [mode.strip() for mode in args.mode.split(",")]

    # 创建并运行客户端
    client = RemoteStreamTestClient(args.host, args.port)
    client.camera_index = args.camera

    await client.run(modes)


if __name__ == "__main__":
    if websockets is None:
        print("WebSocket 库不可用，无法运行测试客户端")
        sys.exit(1)

    asyncio.run(main())
