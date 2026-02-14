"""
STTInputProvider - 语音转文字 Provider

使用讯飞流式 ASR 和 Silero VAD 实现实时语音转文字。
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import ssl
import time
from datetime import datetime
from typing import Any, AsyncIterator, Dict, Optional
from urllib.parse import urlencode

import numpy as np

from src.modules.logging import get_logger
from src.modules.types.base.input_provider import InputProvider
from src.modules.types.base.normalized_message import NormalizedMessage

from .config import STTInputProviderConfig

# 讯飞帧状态
STATUS_FIRST_FRAME = 0
STATUS_CONTINUE_FRAME = 1
STATUS_LAST_FRAME = 2


class STTInputProvider(InputProvider):
    """
    语音转文字输入 Provider

    使用 sounddevice 捕获音频，通过 VAD 判断话语起止，
    实时发送到讯飞 ASR，生成识别的文本 RawData。

    支持特性:
    - 本地麦克风输入
    - 远程音频流 (RemoteStream)
    - Silero VAD 语音活动检测
    - 讯飞流式 ASR
    - 自定义 torch 缓存目录（避免 Windows 中文用户名问题）
    """

    class ConfigSchema(STTInputProviderConfig):
        """STT Provider 配置 Schema"""

        pass

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """获取 Provider 注册信息"""
        return {"layer": "input", "name": "stt", "class": cls, "source": "builtin:stt"}

    def __init__(self, config: dict):
        """
        初始化 STTInputProvider

        Args:
            config: Provider 配置
        """
        super().__init__(config)
        self.logger = get_logger("STTInputProvider")

        # 使用 ConfigSchema 验证配置
        try:
            self.typed_config = self.ConfigSchema(**config)
        except Exception as e:
            self.logger.error(f"配置验证失败: {e}")
            raise

        # 解析嵌套配置
        self.iflytek_config = self.typed_config.iflytek_asr.model_dump()
        self.vad_config = self.typed_config.vad.model_dump()
        self.audio_config = self.typed_config.audio.model_dump()
        self.message_config = self.typed_config.message_config.model_dump()

        # 音频参数
        self.sample_rate = self.audio_config.get("sample_rate", 16000)
        if self.sample_rate not in [8000, 16000]:
            self.logger.warning(f"采样率 {self.sample_rate} 不支持，使用 16000")
            self.sample_rate = 16000

        self.channels = self.audio_config.get("channels", 1)
        self.dtype_str = self.audio_config.get("dtype", "int16")
        self.dtype = np.int16 if self.dtype_str == "int16" else np.float32
        self.input_device_name = self.audio_config.get("stt_input_device_name")

        # VAD 参数
        self.vad_enabled = self.vad_config.get("enable", True)
        self.vad_threshold = self.vad_config.get("vad_threshold", 0.5)
        self.min_silence_duration_ms = int(self.vad_config.get("silence_seconds", 1.0) * 1000)

        # 计算块大小（必须匹配 Silero VAD 要求）
        if self.sample_rate == 16000:
            self.block_size_samples = 512
        elif self.sample_rate == 8000:
            self.block_size_samples = 256
        else:
            self.logger.error(f"不支持的采样率 {self.sample_rate}，使用 16kHz")
            self.sample_rate = 16000
            self.block_size_samples = 512

        self.block_size_ms = int(self.block_size_samples * 1000 / self.sample_rate)

        # 远程流支持
        self.use_remote_stream = self.audio_config.get("use_remote_stream", False)
        self.remote_stream_service = None
        self.remote_audio_received = False

        # 依赖检查
        self._check_dependencies()

        # VAD 模型
        self.vad_model = None
        self.vad_utils = None

        if self.vad_enabled:
            self._load_vad_model()

        # 会话和连接
        self._session = None
        self._active_ws = None
        self._active_receiver_task = None
        self._internal_audio_queue = None
        self._result_queue = None

        # 状态
        self._is_speaking: bool = False
        self._silence_started_time: Optional[float] = None
        self.full_text = ""

        self.logger.info(
            f"STTInputProvider 初始化完成. "
            f"VAD={self.vad_enabled}, Remote={self.use_remote_stream}, "
            f"SampleRate={self.sample_rate}"
        )

    def _check_dependencies(self):
        """检查必要的依赖"""
        try:
            import torch

            self.torch = torch
        except ImportError:
            self.logger.error("缺少依赖: torch。请运行 'pip install torch'")
            self.torch = None

        try:
            import sounddevice as sd

            self.sd = sd
        except ImportError:
            self.logger.error("缺少依赖: sounddevice。请运行 'pip install sounddevice'")
            self.sd = None

        try:
            import aiohttp

            self.aiohttp = aiohttp
        except ImportError:
            self.logger.error("缺少依赖: aiohttp。请运行 'pip install aiohttp'")
            self.aiohttp = None

        # 检查核心依赖
        if not all([self.torch, self.sd, self.aiohttp]):
            raise RuntimeError("STTInputProvider 缺少核心依赖，无法初始化")

    def _load_vad_model(self):
        """加载 Silero VAD 模型"""
        try:
            self.logger.info("加载 Silero VAD 模型...")

            # 解决中文用户名路径编码问题：设置自定义缓存目录
            import torch

            original_hub_dir = torch.hub.get_dir()
            provider_dir = os.path.dirname(os.path.abspath(__file__))
            safe_cache_dir = os.path.join(provider_dir, ".torch_cache")

            # 确保缓存目录存在
            os.makedirs(safe_cache_dir, exist_ok=True)

            # 临时设置 torch.hub 缓存目录到安全路径
            torch.hub.set_dir(safe_cache_dir)

            try:
                # 加载 VAD 模型
                self.vad_model, self.vad_utils = torch.hub.load(
                    repo_or_dir="snakers4/silero-vad",
                    model="silero_vad",
                    source="github",
                    force_reload=False,
                    onnx=False,
                    trust_repo=True,
                    skip_validation=True,
                )
                self.logger.info("Silero VAD 模型加载成功")
            finally:
                # 恢复原始缓存目录设置
                torch.hub.set_dir(original_hub_dir)

        except Exception as e:
            self.logger.error(f"加载 Silero VAD 模型失败: {e}", exc_info=True)
            self.vad_enabled = False

    def _find_device_index(self, device_name: Optional[str], kind: str = "input") -> Optional[int]:
        """根据设备名称查找设备索引"""
        if self.sd is None:
            return None

        try:
            devices = self.sd.query_devices()
            if device_name:
                for i, device in enumerate(devices):
                    if device_name.lower() in device["name"].lower() and device[f"max_{kind}_channels"] > 0:
                        self.logger.info(f"找到 {kind} 设备 '{device['name']}' (索引: {i})")
                        return i
                self.logger.warning(f"未找到名称包含 '{device_name}' 的 {kind} 设备，使用默认设备")

            # 使用默认设备
            default_device_indices = self.sd.default.device
            default_index = default_device_indices[0] if kind == "input" else default_device_indices[1]

            if default_index == -1:
                self.logger.warning(f"未找到默认 {kind} 设备")
                return None

            default_device_info = self.sd.query_devices(default_index)
            if default_device_info[f"max_{kind}_channels"] > 0:
                self.logger.info(f"使用默认 {kind} 设备索引: {default_index} ({default_device_info['name']})")
                return default_index

        except Exception as e:
            self.logger.error(f"查找音频设备时出错: {e}", exc_info=True)

        return None

    async def generate(self) -> AsyncIterator[NormalizedMessage]:
        """
        采集语音数据并生成 RawData

        主要流程:
        1. 从本地麦克风或远程流获取音频
        2. 使用 VAD 检测语音活动
        3. 发送到讯飞 ASR 进行识别
        4. 返回识别结果的 RawData

        Yields:
            RawData: 包含识别文本的原始数据
        """
        if not self.vad_enabled or self.vad_model is None:
            self.logger.error("VAD 未启用或模型未加载，无法运行")
            return

        loop = asyncio.get_event_loop()
        stream = None
        self._internal_audio_queue = asyncio.Queue()
        self._result_queue = asyncio.Queue()

        # 查找音频设备
        input_device_index = self._find_device_index(self.input_device_name, kind="input")

        # 音频回调函数
        def audio_callback(indata, frame_count, time_info, status):
            """sounddevice 音频回调"""
            if status:
                self.logger.warning(f"音频输入状态: {status}")

            try:
                # 转换为 int16 字节
                if indata.dtype == np.float32:
                    indata_int16 = (indata * 32767.0).astype(np.int16)
                elif indata.dtype == np.int16:
                    indata_int16 = indata
                else:
                    indata_int16 = indata.astype(np.int16)

                # 确保单声道
                if indata_int16.ndim > 1 and self.channels == 1:
                    audio_bytes = indata_int16[:, 0].tobytes()
                elif indata_int16.ndim == 1 and self.channels == 1:
                    audio_bytes = indata_int16.tobytes()
                else:
                    audio_bytes = indata_int16[:, 0].tobytes() if indata_int16.ndim > 1 else indata_int16.tobytes()

                # 放入队列
                loop.call_soon_threadsafe(self._internal_audio_queue.put_nowait, audio_bytes)

            except asyncio.QueueFull:
                pass  # 队列满，丢弃数据
            except Exception as e:
                self.logger.error(f"音频回调出错: {e}", exc_info=True)

        try:
            # 启动音频流（本地麦克风）
            if not self.use_remote_stream:
                self.logger.info("启动本地麦克风输入流...")
                stream = self.sd.InputStream(
                    samplerate=self.sample_rate,
                    blocksize=self.block_size_samples,
                    device=input_device_index,
                    channels=self.channels,
                    dtype=self.dtype_str,
                    callback=audio_callback,
                )
                stream.start()
                self.logger.info(f"本地音频流已启动 (VAD 阈值: {self.vad_threshold})")
            else:
                self.logger.info(f"使用远程音频流模式 (VAD 阈值: {self.vad_threshold})")

            # 启动结果读取任务
            result_task = asyncio.create_task(self._read_results())

            # 处理循环
            speech_chunk_count = 0
            timeout_duration = max(0.1, self.min_silence_duration_ms / 1000.0 * 0.8)

            while self.is_running:
                try:
                    # 等待音频块
                    audio_chunk_bytes = await asyncio.wait_for(
                        self._internal_audio_queue.get(), timeout=timeout_duration
                    )
                    self._internal_audio_queue.task_done()

                except asyncio.TimeoutError:
                    # 超时处理
                    if self._is_speaking:
                        self._is_speaking = False
                        self._silence_started_time = time.monotonic()

                    # 检查静音持续时间
                    if self._silence_started_time is not None and (
                        time.monotonic() - self._silence_started_time > self.min_silence_duration_ms / 1000.0
                    ):
                        if self._active_ws:
                            self.logger.info(f"静音超过阈值 ({self.min_silence_duration_ms}ms)，结束话语")
                            if speech_chunk_count > 0:
                                await self._close_iflytek_connection(send_last_frame=True)
                            else:
                                await self._close_iflytek_connection(send_last_frame=False)
                            speech_chunk_count = 0
                            self._is_speaking = False
                        self._silence_started_time = None
                    continue

                except asyncio.CancelledError:
                    break

                # VAD 检查
                try:
                    audio_np = np.frombuffer(audio_chunk_bytes, dtype=np.int16)
                    audio_float32 = audio_np.astype(np.float32) / 32768.0

                    if audio_float32.ndim == 0 or audio_float32.size == 0:
                        continue

                    audio_tensor = self.torch.from_numpy(audio_float32)
                    speech_prob = self.vad_model(audio_tensor, self.sample_rate).item()
                    is_speech = speech_prob > self.vad_threshold

                except Exception as e:
                    self.logger.error(f"VAD 处理出错: {e}", exc_info=True)
                    continue

                now = time.monotonic()

                # 状态机
                if is_speech:
                    # 检测到语音
                    if not self._is_speaking:
                        # 话语开始
                        self.logger.info(f"VAD: 话语开始 (Prob: {speech_prob:.2f})")
                        self._is_speaking = True
                        self._silence_started_time = None
                        speech_chunk_count = 0

                        # 确保连接存在
                        if not await self._ensure_iflytek_connection():
                            self.logger.error("无法建立讯飞连接")
                            self._is_speaking = False
                            continue

                        # 发送第一帧
                        if self._active_ws:
                            try:
                                first_frame = self._build_iflytek_frame(STATUS_FIRST_FRAME, audio_chunk_bytes)
                                await asyncio.wait_for(self._active_ws.send_bytes(first_frame), timeout=2.0)
                                speech_chunk_count += 1
                                self.logger.debug("已发送第一帧")
                            except Exception as e:
                                self.logger.error(f"发送第一帧失败: {e}", exc_info=True)
                                await self._close_iflytek_connection(send_last_frame=False)
                                self._is_speaking = False
                                speech_chunk_count = 0
                                continue

                    # 继续发送语音帧
                    elif speech_chunk_count > 0 and self._active_ws and not self._active_ws.closed:
                        try:
                            data_frame = self._build_iflytek_frame(STATUS_CONTINUE_FRAME, audio_chunk_bytes)
                            await asyncio.wait_for(self._active_ws.send_bytes(data_frame), timeout=1.0)
                            speech_chunk_count += 1
                        except Exception as e:
                            self.logger.error(f"发送音频帧失败: {e}", exc_info=True)
                            await self._close_iflytek_connection(send_last_frame=False)
                            self._is_speaking = False
                            speech_chunk_count = 0

                else:
                    # 检测到静音
                    if self._is_speaking:
                        # 语音结束
                        self.logger.debug("VAD: 话语结束 (静音检测)")
                        self._is_speaking = False
                        self._silence_started_time = now

                        # 发送静音帧
                        if self._active_ws and not self._active_ws.closed:
                            try:
                                data_frame = self._build_iflytek_frame(STATUS_CONTINUE_FRAME, audio_chunk_bytes)
                                await asyncio.wait_for(self._active_ws.send_bytes(data_frame), timeout=1.0)
                            except Exception as e:
                                self.logger.error(f"发送静音帧失败: {e}", exc_info=True)
                                await self._close_iflytek_connection(send_last_frame=False)
                                self._silence_started_time = None
                                speech_chunk_count = 0

                    # 静音持续时间检查
                    elif self._silence_started_time is not None:
                        if now - self._silence_started_time > self.min_silence_duration_ms / 1000.0:
                            if self._active_ws:
                                self.logger.info(f"静音阈值已达到 ({self.min_silence_duration_ms}ms)，结束话语")
                                if speech_chunk_count > 0:
                                    await self._close_iflytek_connection(send_last_frame=True)
                                else:
                                    await self._close_iflytek_connection(send_last_frame=False)
                                speech_chunk_count = 0
                            self._silence_started_time = None

                # 读取并 yield 结果
                try:
                    while not self._result_queue.empty():
                        result = await asyncio.wait_for(self._result_queue.get(), timeout=0.01)
                        yield result
                        self._result_queue.task_done()
                except asyncio.TimeoutError:
                    pass

        except Exception as e:
            self.logger.error(f"STT worker 循环出错: {e}", exc_info=True)

        finally:
            self.logger.info("STT worker 循环结束，清理资源...")
            result_task.cancel()
            try:
                await result_task
            except asyncio.CancelledError:
                pass

            if not self.use_remote_stream and stream is not None:
                try:
                    stream.stop()
                    stream.close()
                    self.logger.debug("本地麦克风流已停止并关闭")
                except Exception as e:
                    self.logger.error(f"停止麦克流出错: {e}", exc_info=True)

            await self._close_iflytek_connection(send_last_frame=False)

    async def _read_results(self):
        """从结果队列读取并返回 NormalizedMessage"""
        try:
            while self.is_running:
                try:
                    result = await asyncio.wait_for(self._result_queue.get(), timeout=0.1)
                    yield result
                    self._result_queue.task_done()
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
        except asyncio.CancelledError:
            pass

    async def _ensure_iflytek_connection(self) -> bool:
        """确保讯飞 WebSocket 连接存在"""
        # 检查现有连接
        if self._active_ws and not self._active_ws.closed:
            if self._active_receiver_task and not self._active_receiver_task.done():
                return True
            else:
                self.logger.warning("WebSocket 存在但接收器任务未运行，重新连接")
                await self._close_iflytek_connection(send_last_frame=False)

        # 确保 session 存在
        if not self._session or self._session.closed:
            try:
                self._session = self.aiohttp.ClientSession()
                self.logger.info("已创建新的 aiohttp session")
            except Exception as e:
                self.logger.error(f"创建 aiohttp session 失败: {e}", exc_info=True)
                return False

        # 建立新连接
        try:
            auth_url = self._build_iflytek_auth_url()
            self.logger.info("连接到讯飞 WebSocket...")

            ssl_context = ssl.create_default_context()
            self._active_ws = await self._session.ws_connect(
                auth_url,
                autoping=True,
                heartbeat=30,
                ssl=ssl_context,
            )
            self.logger.info("成功连接到讯飞 WebSocket")

            # 启动接收器任务
            if self._active_receiver_task and not self._active_receiver_task.done():
                self._active_receiver_task.cancel()
                try:
                    await asyncio.wait_for(self._active_receiver_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

            self._active_receiver_task = asyncio.create_task(self._iflytek_receiver(self._active_ws))
            self.logger.info("已启动讯飞接收器任务")
            return True

        except Exception as e:
            self.logger.error(f"建立讯飞连接失败: {e}", exc_info=True)
            if self._active_ws and not self._active_ws.closed:
                await self._active_ws.close()
            self._active_ws = None
            if self._active_receiver_task and not self._active_receiver_task.done():
                self._active_receiver_task.cancel()
            self._active_receiver_task = None
            return False

    async def _close_iflytek_connection(self, send_last_frame: bool = True):
        """关闭讯飞 WebSocket 连接"""
        ws_to_close = self._active_ws
        receiver_task_to_await = self._active_receiver_task

        self._active_ws = None
        self._active_receiver_task = None

        if not ws_to_close:
            if receiver_task_to_await and not receiver_task_to_await.done():
                receiver_task_to_await.cancel()
            return

        if ws_to_close.closed:
            if receiver_task_to_await and not receiver_task_to_await.done():
                receiver_task_to_await.cancel()
            return

        self.logger.info(f"关闭讯飞连接 (发送结束帧: {send_last_frame})...")

        try:
            if send_last_frame:
                try:
                    last_frame = self._build_iflytek_frame(STATUS_LAST_FRAME)
                    await asyncio.wait_for(ws_to_close.send_bytes(last_frame), timeout=2.0)
                    self.logger.debug("已发送结束帧")
                except Exception as e:
                    self.logger.warning(f"发送结束帧失败: {e}")

            if receiver_task_to_await and not receiver_task_to_await.done():
                try:
                    await asyncio.wait_for(receiver_task_to_await, timeout=1.0)
                except asyncio.TimeoutError:
                    receiver_task_to_await.cancel()
                except Exception as e:
                    self.logger.error(f"等待接收器任务出错: {e}", exc_info=True)

            await ws_to_close.close()
            self.logger.info(f"讯飞连接已关闭 (代码: {ws_to_close.close_code})")

        except Exception as e:
            self.logger.error(f"关闭连接出错: {e}", exc_info=True)
        finally:
            if receiver_task_to_await and not receiver_task_to_await.done():
                receiver_task_to_await.cancel()

    async def _iflytek_receiver(self, ws):
        """
        接收讯飞 WebSocket 消息并处理识别结果

        Args:
            ws: WebSocket 连接
        """
        self.logger.debug("讯飞接收器任务启动")
        self.full_text = ""
        utterance_failed = False

        try:
            async for msg in ws:
                if not self.is_running:
                    break

                if msg.type == self.aiohttp.WSMsgType.TEXT:
                    try:
                        resp = json.loads(msg.data)

                        if resp.get("code", -1) != 0:
                            err_msg = f"讯飞 API 错误: Code={resp.get('code')}, Message={resp.get('message')}"
                            self.logger.error(err_msg)
                            utterance_failed = True
                            break

                        data = resp.get("data", {})
                        status = data.get("status", -1)
                        result = data.get("result", {})

                        # 提取文本
                        if "ws" in result:
                            for w in result["ws"]:
                                for cw in w.get("cw", []):
                                    self.full_text += cw.get("w", "")

                        # 最后一帧
                        if status == STATUS_LAST_FRAME:
                            full_text = self.full_text.strip()
                            self.logger.info(f"讯飞识别结果: '{full_text}'")

                            if full_text and not utterance_failed:
                                # 将结果放入队列
                                await self._result_queue.put(
                                    NormalizedMessage(
                                        text=full_text,
                                        source="stt",
                                        data_type="text",
                                        importance=0.5,
                                        raw={
                                            "user": self.message_config.get("user_nickname", "语音"),
                                            "user_id": self.message_config.get("user_id", "stt_user"),
                                        },
                                    )
                                )
                                self.full_text = ""
                                break

                    except json.JSONDecodeError:
                        self.logger.error(f"无法解码讯飞 JSON: {msg.data}")
                        utterance_failed = True
                        break
                    except Exception as e:
                        self.logger.error(f"处理讯飞消息出错: {e}", exc_info=True)
                        utterance_failed = True
                        break

                elif msg.type == self.aiohttp.WSMsgType.ERROR:
                    err = ws.exception() or RuntimeError("WebSocket 错误")
                    self.logger.error(f"讯飞 WebSocket 错误: {err}")
                    utterance_failed = True
                    break

                elif msg.type == self.aiohttp.WSMsgType.CLOSED:
                    self.logger.warning(f"讯飞连接已关闭: Code={ws.close_code}")
                    break

        except asyncio.CancelledError:
            self.logger.info("讯飞接收器任务被取消")
        except Exception as e:
            self.logger.error(f"讯飞接收器任务异常: {e}", exc_info=True)
        finally:
            self.logger.debug("讯飞接收器任务结束")

    def _build_iflytek_auth_url(self) -> str:
        """构建讯飞认证 URL"""
        cfg = self.iflytek_config

        if not all([cfg.get(k) for k in ["host", "path", "api_secret", "api_key"]]):
            raise ValueError("讯飞配置缺少必要字段")

        host = cfg["host"]
        path = cfg["path"]
        url = f"wss://{host}{path}"

        # 使用 UTC 时间
        date = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
        signature_sha = hmac.new(
            cfg["api_secret"].encode("utf-8"), signature_origin.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        signature_sha_base64 = base64.b64encode(signature_sha).decode(encoding="utf-8")
        authorization_origin = f'api_key="{cfg["api_key"]}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature_sha_base64}"'
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode(encoding="utf-8")

        # URL 编码参数
        v = {"authorization": authorization, "date": date, "host": host}
        signed_url = url + "?" + urlencode(v)

        return signed_url

    def _build_iflytek_common_params(self) -> dict:
        """构建讯飞公共参数"""
        return {"app_id": str(self.iflytek_config["appid"])}

    def _build_iflytek_business_params(self) -> dict:
        """构建讯飞业务参数"""
        return {
            "language": self.iflytek_config.get("language", "zh_cn"),
            "domain": self.iflytek_config.get("domain", "iat"),
            "accent": self.iflytek_config.get("accent", "mandarin"),
            "ptt": self.iflytek_config.get("ptt", 1),
            "rlang": self.iflytek_config.get("rlang", "zh-cn"),
            "vinfo": self.iflytek_config.get("vinfo", 1),
            "dwa": self.iflytek_config.get("dwa", "wpgs"),
            "vad_eos": self.min_silence_duration_ms,
            "nunum": 0,
        }

    def _build_iflytek_data_params(self, status: int, audio_chunk_bytes: bytes = b"") -> dict:
        """构建讯飞数据参数"""
        return {
            "status": status,
            "format": f"audio/L16;rate={self.sample_rate}",
            "encoding": "raw",
            "audio": base64.b64encode(audio_chunk_bytes).decode("utf-8"),
        }

    def _build_iflytek_frame(self, status: int, audio_chunk_bytes: bytes = b"") -> bytes:
        """构建讯飞帧"""
        frame = {
            "common": self._build_iflytek_common_params(),
            "business": self._build_iflytek_business_params(),
            "data": self._build_iflytek_data_params(status, audio_chunk_bytes),
        }
        return json.dumps(frame).encode("utf-8")

    async def _cleanup_internal(self):
        """清理资源"""
        self.logger.info("清理 STTInputProvider 资源...")

        if self._session and not self._session.closed:
            await self._session.close()
            self.logger.info("aiohttp session 已关闭")
            self._session = None

        self._active_ws = None
        self._active_receiver_task = None
        self._is_speaking = False
        self._silence_started_time = None

        self.logger.info("STTInputProvider 清理完成")
