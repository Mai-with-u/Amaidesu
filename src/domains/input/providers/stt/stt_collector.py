"""
STTCollector - 语音转文字输入Collector

使用讯飞流式 ASR 和 Silero VAD 实现实时语音转文字。
"""

from __future__ import annotations

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

import numpy as np

from src.domains.input.registry import collector
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.types.base.normalized_message import NormalizedMessage

from .config import STTInputProviderConfig

STATUS_FIRST_FRAME = 0
STATUS_CONTINUE_FRAME = 1
STATUS_LAST_FRAME = 2


@collector("stt")
class STTCollector:
    """
    语音转文字输入Collector

    使用 sounddevice 捕获音频，通过 VAD 判断话语起止，
    实时发送到讯飞 ASR，生成识别的文本 NormalizedMessage。

    支持特性:
    - 本地麦克风输入
    - 远程音频流 (RemoteStream)
    - Silero VAD 语音活动检测
    - 讯飞流式 ASR
    - 自定义 torch 缓存目录（避免 Windows 中文用户名问题）
    """

    class ConfigSchema(STTInputProviderConfig):
        """STT Collector 配置 Schema"""

        pass

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
    ):
        """
        初始化 STTCollector

        Args:
            config: 配置字典
            event_bus: 事件总线实例
        """
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger(self.__class__.__name__)

        try:
            self.typed_config = self.ConfigSchema(**config)
        except Exception as e:
            self.logger.error(f"配置验证失败: {e}")
            raise

        self.iflytek_config = self.typed_config.iflytek_asr.model_dump()
        self.vad_config = self.typed_config.vad.model_dump()
        self.audio_config = self.typed_config.audio.model_dump()
        self.message_config = self.typed_config.message_config.model_dump()

        self.sample_rate = self.audio_config.get("sample_rate", 16000)
        if self.sample_rate not in [8000, 16000]:
            self.logger.warning(f"采样率 {self.sample_rate} 不支持，使用 16000")
            self.sample_rate = 16000

        self.channels = self.audio_config.get("channels", 1)
        self.dtype_str = self.audio_config.get("dtype", "int16")
        self.dtype = np.int16 if self.dtype_str == "int16" else np.float32
        self.input_device_name = self.audio_config.get("stt_input_device_name")

        self.vad_enabled = self.vad_config.get("enable", True)
        self.vad_threshold = self.vad_config.get("vad_threshold", 0.5)
        self.min_silence_duration_ms = int(self.vad_config.get("silence_seconds", 1.0) * 1000)

        if self.sample_rate == 16000:
            self.block_size_samples = 512
        elif self.sample_rate == 8000:
            self.block_size_samples = 256
        else:
            self.logger.error(f"不支持的采样率 {self.sample_rate}，使用 16kHz")
            self.sample_rate = 16000
            self.block_size_samples = 512

        self.block_size_ms = int(self.block_size_samples * 1000 / self.sample_rate)

        self.use_remote_stream = self.audio_config.get("use_remote_stream", False)
        self.remote_stream_service = None
        self.remote_audio_received = False

        self._check_dependencies()

        self.vad_model = None
        self.vad_utils = None

        if self.vad_enabled:
            self._load_vad_model()

        self._session = None
        self._active_ws = None
        self._active_receiver_task = None
        self._internal_audio_queue = None
        self._result_queue = None

        self._is_speaking: bool = False
        self._silence_started_time: Optional[float] = None
        self.full_text = ""
        self._last_audio_chunk: Optional[bytes] = None

        self.is_started = False

        self.logger.info(
            f"STTCollector 初始化完成. "
            f"VAD={self.vad_enabled}, Remote={self.use_remote_stream}, "
            f"SampleRate={self.sample_rate}"
        )

    def _check_dependencies(self) -> None:
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

        if not all([self.torch, self.sd, self.aiohttp]):
            raise RuntimeError("STTCollector 缺少核心依赖，无法初始化")

    def _load_vad_model(self) -> None:
        """加载 Silero VAD 模型"""
        try:
            self.logger.info("加载 Silero VAD 模型...")

            import torch

            original_hub_dir = torch.hub.get_dir()
            provider_dir = os.path.dirname(os.path.abspath(__file__))
            safe_cache_dir = os.path.join(provider_dir, ".torch_cache")

            os.makedirs(safe_cache_dir, exist_ok=True)
            torch.hub.set_dir(safe_cache_dir)

            try:
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

    async def start(self) -> None:
        self.is_started = True

    async def stop(self) -> None:
        self.is_started = False

    async def cleanup(self) -> None:
        self.logger.info("清理 STTCollector 资源...")

        if self._session and not self._session.closed:
            await self._session.close()
            self.logger.info("aiohttp session 已关闭")
            self._session = None

        self._active_ws = None
        self._active_receiver_task = None
        self._is_speaking = False
        self._silence_started_time = None

        self.logger.info("STTCollector 清理完成")

    async def collect(self) -> AsyncIterator[NormalizedMessage]:
        """采集语音数据并生成 NormalizedMessage"""
        if not self.vad_enabled or self.vad_model is None:
            self.logger.error("VAD 未启用或模型未加载，无法运行")
            return

        loop = asyncio.get_event_loop()
        stream = None
        self._internal_audio_queue = asyncio.Queue()
        self._result_queue = asyncio.Queue()

        input_device_index = self._find_device_index(self.input_device_name, kind="input")

        def audio_callback(indata, frame_count, time_info, status):
            """sounddevice 音频回调"""
            if status:
                self.logger.warning(f"音频输入状态: {status}")

            try:
                if indata.dtype == np.float32:
                    indata_int16 = (indata * 32767.0).astype(np.int16)
                elif indata.dtype == np.int16:
                    indata_int16 = indata
                else:
                    indata_int16 = indata.astype(np.int16)

                if indata_int16.ndim > 1 and self.channels == 1:
                    audio_bytes = indata_int16[:, 0].tobytes()
                elif indata_int16.ndim == 1 and self.channels == 1:
                    audio_bytes = indata_int16.tobytes()
                else:
                    audio_bytes = indata_int16[:, 0].tobytes() if indata_int16.ndim > 1 else indata_int16.tobytes()

                loop.call_soon_threadsafe(self._internal_audio_queue.put_nowait, audio_bytes)

            except asyncio.QueueFull:
                pass
            except Exception as e:
                self.logger.error(f"音频回调出错: {e}", exc_info=True)

        try:
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

            speech_chunk_count = 0
            timeout_duration = max(0.1, self.min_silence_duration_ms / 1000.0 * 0.8)

            while self.is_started:
                try:
                    audio_chunk_bytes = await asyncio.wait_for(
                        self._internal_audio_queue.get(), timeout=timeout_duration
                    )
                    self._internal_audio_queue.task_done()

                except asyncio.TimeoutError:
                    if self._is_speaking:
                        self._is_speaking = False
                        self._silence_started_time = time.monotonic()

                    if self._silence_started_time is not None and (
                        time.monotonic() - self._silence_started_time > self.min_silence_duration_ms / 1000.0
                    ):
                        if self._active_ws:
                            self.logger.debug(f"静音超过阈值 ({self.min_silence_duration_ms}ms)，结束话语")
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

                if is_speech:
                    if not self._is_speaking:
                        self.logger.debug(f"VAD: 话语开始 (Prob: {speech_prob:.2f})")
                        self._is_speaking = True
                        self._silence_started_time = None
                        speech_chunk_count = 0

                        if not await self._ensure_iflytek_connection():
                            self.logger.error("无法建立讯飞连接")
                            self._is_speaking = False
                            continue

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

                    elif speech_chunk_count > 0 and self._active_ws and not self._active_ws.closed:
                        try:
                            data_frame = self._build_iflytek_frame(STATUS_CONTINUE_FRAME, audio_chunk_bytes)
                            await asyncio.wait_for(self._active_ws.send_bytes(data_frame), timeout=1.0)
                            speech_chunk_count += 1
                            self._last_audio_chunk = audio_chunk_bytes
                        except Exception as e:
                            self.logger.error(f"发送音频帧失败: {e}", exc_info=True)
                            await self._close_iflytek_connection(send_last_frame=False)
                            self._is_speaking = False
                            speech_chunk_count = 0

                else:
                    if self._is_speaking:
                        self.logger.debug("VAD: 话语结束 (静音检测)")
                        self._is_speaking = False
                        self._silence_started_time = now

                        if self._active_ws and not self._active_ws.closed:
                            try:
                                data_frame = self._build_iflytek_frame(STATUS_CONTINUE_FRAME, audio_chunk_bytes)
                                await asyncio.wait_for(self._active_ws.send_bytes(data_frame), timeout=1.0)
                            except Exception as e:
                                self.logger.error(f"发送静音帧失败: {e}", exc_info=True)
                                await self._close_iflytek_connection(send_last_frame=False)
                                self._silence_started_time = None
                                speech_chunk_count = 0

                    elif self._silence_started_time is not None:
                        if now - self._silence_started_time > self.min_silence_duration_ms / 1000.0:
                            if self._active_ws:
                                self.logger.debug(f"静音阈值已达到 ({self.min_silence_duration_ms}ms)，结束话语")
                                if speech_chunk_count > 0:
                                    await self._close_iflytek_connection(send_last_frame=True)
                                else:
                                    await self._close_iflytek_connection(send_last_frame=False)
                                speech_chunk_count = 0
                            self._silence_started_time = None

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

            if not self.use_remote_stream and stream is not None:
                try:
                    stream.stop()
                    stream.close()
                    self.logger.debug("本地麦克风流已停止并关闭")
                except Exception as e:
                    self.logger.error(f"停止麦克流出错: {e}", exc_info=True)

            await self._close_iflytek_connection(send_last_frame=False)

    async def _ensure_iflytek_connection(self) -> bool:
        """确保讯飞 WebSocket 连接存在"""
        if self._active_ws and not self._active_ws.closed:
            if self._active_receiver_task and not self._active_receiver_task.done():
                return True
            else:
                self.logger.warning("WebSocket 存在但接收器任务未运行，重新连接")
                await self._close_iflytek_connection(send_last_frame=False)

        if not self._session or self._session.closed:
            try:
                self._session = self.aiohttp.ClientSession()
                self.logger.info("已创建新的 aiohttp session")
            except Exception as e:
                self.logger.error(f"创建 aiohttp session 失败: {e}", exc_info=True)
                return False

        try:
            auth_url = self._build_iflytek_auth_url()
            self.logger.debug("连接到讯飞 WebSocket...")

            ssl_context = ssl.create_default_context()
            self._active_ws = await self._session.ws_connect(
                auth_url,
                autoping=True,
                heartbeat=30,
                ssl=ssl_context,
            )
            self.logger.info("成功连接到讯飞 WebSocket")

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

    async def _close_iflytek_connection(
        self, send_last_frame: bool = True, last_audio_chunk: Optional[bytes] = None
    ) -> None:
        """关闭讯飞 WebSocket 连接"""
        ws_to_close = self._active_ws
        receiver_task_to_await = self._active_receiver_task

        self._active_ws = None
        self._active_receiver_task = None
        self._last_audio_chunk = None

        if not ws_to_close:
            if receiver_task_to_await and not receiver_task_to_await.done():
                receiver_task_to_await.cancel()
            return

        if ws_to_close.closed:
            if receiver_task_to_await and not receiver_task_to_await.done():
                receiver_task_to_await.cancel()
            return

        self.logger.debug(f"关闭讯飞连接 (发送结束帧: {send_last_frame})...")

        try:
            if send_last_frame:
                try:
                    audio_for_end = last_audio_chunk or self._last_audio_chunk
                    last_frame = self._build_iflytek_frame(STATUS_LAST_FRAME, audio_for_end or b"")
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
            self.logger.debug(f"讯飞连接已关闭 (代码: {ws_to_close.close_code})")

        except Exception as e:
            self.logger.error(f"关闭连接出错: {e}", exc_info=True)
        finally:
            if receiver_task_to_await and not receiver_task_to_await.done():
                receiver_task_to_await.cancel()

    async def _iflytek_receiver(self, ws) -> None:
        """接收讯飞 WebSocket 消息并处理识别结果"""
        self.logger.debug("讯飞接收器任务启动")
        self.full_text = ""
        utterance_failed = False

        try:
            async for msg in ws:
                if not self.is_started:
                    break

                if msg.type == self.aiohttp.WSMsgType.TEXT:
                    try:
                        resp = json.loads(msg.data)
                        self.logger.debug(f"讯飞原始响应: {json.dumps(resp, ensure_ascii=False)[:500]}")

                        if resp.get("code", -1) != 0:
                            msg_content = resp.get("message", "")
                            err_msg = f"讯飞 API 错误: Code={resp.get('code')}, Message={msg_content}"
                            self.logger.error(err_msg)
                            utterance_failed = True
                            break

                        data = resp.get("data", {})
                        result_data = data.get("result", {})
                        status = data.get("status", -1)

                        if "ws" in result_data:
                            try:
                                for w in result_data["ws"]:
                                    for cw in w.get("cw", []):
                                        self.full_text += cw.get("w", "")
                            except Exception as e:
                                self.logger.warning(f"解析识别结果文本失败: {e}")

                        self.logger.debug(
                            f"检查最后一帧: status={status}, STATUS_LAST_FRAME={STATUS_LAST_FRAME}, full_text='{self.full_text}'"
                        )
                        if status == STATUS_LAST_FRAME:
                            full_text = self.full_text.strip()
                            self.logger.debug(f"讯飞识别结果: '{full_text}'")

                            if full_text and not utterance_failed:
                                await self._result_queue.put(
                                    NormalizedMessage(
                                        text=full_text,
                                        source="stt",
                                        data_type="text",
                                        importance=0.5,
                                        user_id=self.message_config.get("user_id") or None,
                                        user_nickname=self.message_config.get("user_nickname") or None,
                                        platform="voice",
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

        host = cfg.get("host", "iat-api.xfyun.com")
        path = cfg.get("path", "/v2/iat")

        if not all([host, path, cfg.get("api_secret"), cfg.get("api_key")]):
            raise ValueError("讯飞配置缺少必要字段")

        url = f"wss://{host}{path}"

        date = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"

        self.logger.debug(f"signature_origin: {repr(signature_origin)}")

        signature_sha = hmac.new(
            cfg["api_secret"].encode("utf-8"), signature_origin.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        signature_sha_base64 = base64.b64encode(signature_sha).decode(encoding="utf-8")

        self.logger.debug(f"signature_sha_base64: {signature_sha_base64}")

        authorization_origin = f'api_key="{cfg["api_key"]}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature_sha_base64}"'

        self.logger.debug(f"authorization_origin: {authorization_origin[:50]}...")

        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode(encoding="utf-8")

        self.logger.debug(f"讯飞认证 - host: {host}, path: {path}, date: {date}")

        params = f"authorization={authorization}&date={date}&host={host}"
        signed_url = url + "?" + params

        self.logger.debug(f"讯飞认证 URL: {signed_url[:150]}...")
        return signed_url

    def _build_iflytek_frame(self, status: int, audio_chunk_bytes: bytes = b"") -> bytes:
        """构建讯飞帧（语音听写流式版 API 格式）"""
        is_first = status == STATUS_FIRST_FRAME

        business = {}
        if is_first:
            business = {
                "language": self.iflytek_config.get("language", "zh_cn"),
                "domain": self.iflytek_config.get("domain", "iat"),
                "accent": self.iflytek_config.get("accent", "mandarin"),
                "vad_eos": self.min_silence_duration_ms,
            }

        format_str = f"audio/L16;rate={self.sample_rate}"
        data = {
            "status": status,
            "format": format_str,
            "encoding": "raw",
            "audio": base64.b64encode(audio_chunk_bytes).decode("utf-8") if audio_chunk_bytes else "",
        }

        if is_first:
            frame = {
                "common": {"app_id": str(self.iflytek_config["appid"])},
                "business": business,
                "data": data,
            }
            self.logger.debug(
                f"发送第一帧: common={frame['common']}, business={frame['business']}, data.status={data['status']}, format={format_str}"
            )
        else:
            frame = {"data": data}

        return json.dumps(frame).encode("utf-8")
