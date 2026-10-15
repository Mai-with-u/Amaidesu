"""
STTCollector —— 语音转文字采集器

- 继承 ``BaseCollector``（流型感知者）
- 技术栈：讯飞 WebSocket 流式 ASR（``XunfeiIatClient``）、Silero VAD、音频采集
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, AsyncIterator, Dict, Optional, Tuple

import numpy as np

from src.modules.collectors.base import BaseCollector
from src.modules.collectors.stt.xunfei_client import (
    STATUS_CONTINUE_FRAME,
    STATUS_FIRST_FRAME,
    XunfeiIatClient,
)
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.logging import get_logger

from .config import STTInputConfig


class STTCollector(BaseCollector):
    """
    语音转文字采集器

    使用 sounddevice 捕获音频，通过 VAD 判断话语起止，
    实时发送到讯飞 ASR，生成识别文本的事件载荷。
    """

    name = "stt"
    description = "语音识别输入（STT）—— 讯飞流式 ASR + Silero VAD"

    class ConfigSchema(STTInputConfig):
        """STT Collector 配置 Schema"""

        pass

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        super().__init__(event_bus=event_bus)
        self.config = config or {}
        self.logger = get_logger(self.__class__.__name__)

        try:
            self.typed_config = self.ConfigSchema.from_dict(self.config)
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

        self._xunfei = XunfeiIatClient(
            config=self.iflytek_config,
            sample_rate=self.sample_rate,
            vad_eos_ms=self.min_silence_duration_ms,
            message_config=self.message_config,
            should_run=lambda: self.is_started,
        )
        self._internal_audio_queue = None

        self._is_speaking: bool = False
        self._silence_started_time: Optional[float] = None

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

            # torch 已在 _check_dependencies 缓存并校验非空，直接复用
            assert self.torch is not None
            torch = self.torch

            original_hub_dir = torch.hub.get_dir()
            # 缓存放用户目录：包源码目录会被清理/重装，运行时下载的模型产物不进源码树
            safe_cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "amaidesu", "torch-hub")
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
            self.logger.exception(f"加载 Silero VAD 模型失败: {e}")
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
            self.logger.exception(f"查找音频设备时出错: {e}")

        return None

    async def start(self) -> None:
        """启动：加载 VAD 模型后开后台任务消费 collect()（collect 内直发 room.message.*）。"""
        if not self.is_started:
            if self.vad_enabled and self.vad_model is None:
                # 模型下载/加载放启动期而非构造期：构造被下载卡住会拖死整个应用装配
                # （首次运行从 GitHub 拉取，网络不佳时可达分钟级）
                await asyncio.to_thread(self._load_vad_model)
            self.is_started = True
            await self._start_collect_task()

    async def stop(self) -> None:
        """停止：取消后台消费任务。"""
        if self.is_started:
            self.is_started = False
            await self._stop_collect_task()

    async def cleanup(self) -> None:
        """清理资源"""
        self.logger.info("清理 STTCollector 资源...")

        await self._xunfei.dispose()

        self._is_speaking = False
        self._silence_started_time = None

        self.logger.info("STTCollector 清理完成")

    # -------------------- 采集循环（按阶段拆分） --------------------

    async def collect(self) -> AsyncIterator[RoomMessagePayload]:
        """采集语音数据并生成事件载荷（直发 room.message.partner_speech）"""
        if not self.vad_enabled or self.vad_model is None:
            self.logger.error("VAD 未启用或模型未加载，无法运行")
            return

        loop = asyncio.get_event_loop()
        stream = None
        self._internal_audio_queue = asyncio.Queue()

        input_device_index = self._find_device_index(self.input_device_name, kind="input")

        try:
            stream = self._open_audio_stream(loop, input_device_index)

            speech_chunk_count = 0
            timeout_duration = max(0.1, self.min_silence_duration_ms / 1000.0 * 0.8)

            while self.is_started:
                try:
                    audio_chunk_bytes = await asyncio.wait_for(
                        self._internal_audio_queue.get(), timeout=timeout_duration
                    )
                    self._internal_audio_queue.task_done()

                except asyncio.TimeoutError:
                    speech_chunk_count = await self._handle_input_silence(speech_chunk_count)
                    continue

                except asyncio.CancelledError:
                    break

                evaluated = self._evaluate_speech(audio_chunk_bytes)
                if evaluated is None:
                    continue
                is_speech, speech_prob = evaluated

                now = time.monotonic()

                if is_speech:
                    if not self._is_speaking:
                        started_count = await self._begin_utterance(audio_chunk_bytes, speech_prob)
                        if started_count is None:
                            continue
                        speech_chunk_count = started_count

                    elif speech_chunk_count > 0 and self._xunfei.is_open:
                        speech_chunk_count = await self._continue_utterance(audio_chunk_bytes, speech_chunk_count)

                else:
                    if self._is_speaking:
                        speech_chunk_count = await self._end_utterance(audio_chunk_bytes, now, speech_chunk_count)

                    elif self._silence_started_time is not None:
                        if now - self._silence_started_time > self.min_silence_duration_ms / 1000.0:
                            if self._xunfei.has_ws:
                                self.logger.debug(f"静音阈值已达到 ({self.min_silence_duration_ms}ms)，结束话语")
                                speech_chunk_count = await self._close_current_utterance(speech_chunk_count)
                            self._silence_started_time = None

                async for payload in self._drain_results():
                    yield payload

        except Exception as e:
            self.logger.exception(f"STT worker 循环出错: {e}")

        finally:
            self.logger.info("STT worker 循环结束，清理资源...")

            if not self.use_remote_stream and stream is not None:
                try:
                    stream.stop()
                    stream.close()
                    self.logger.debug("本地麦克风流已停止并关闭")
                except Exception as e:
                    self.logger.exception(f"停止麦克流出错: {e}")

            await self._xunfei.close(send_last_frame=False)

    def _open_audio_stream(self, loop: asyncio.AbstractEventLoop, input_device_index: Optional[int]) -> Any:
        """打开本地麦克风输入流（远程流模式只记日志，返回 None）。"""
        if self.use_remote_stream:
            self.logger.info(f"使用远程音频流模式 (VAD 阈值: {self.vad_threshold})")
            return None

        self.logger.info("启动本地麦克风输入流...")
        stream = self.sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=self.block_size_samples,
            device=input_device_index,
            channels=self.channels,
            dtype=self.dtype_str,
            callback=self._make_audio_callback(loop),
        )
        stream.start()
        self.logger.info(f"本地音频流已启动 (VAD 阈值: {self.vad_threshold})")
        return stream

    def _make_audio_callback(self, loop: asyncio.AbstractEventLoop) -> Any:
        """构造 sounddevice 音频回调：归一化为 int16 单声道字节后入队。"""

        def audio_callback(indata: "np.ndarray", frame_count: int, time_info: dict, status: Any) -> None:
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
                self.logger.exception(f"音频回调出错: {e}")

        return audio_callback

    async def _handle_input_silence(self, speech_chunk_count: int) -> int:
        """取音频超时（输入静音）时的状态维护：说话中转入静默计时，静默满阈值即收尾话语。"""
        if self._is_speaking:
            self._is_speaking = False
            self._silence_started_time = time.monotonic()

        if self._silence_started_time is not None and (
            time.monotonic() - self._silence_started_time > self.min_silence_duration_ms / 1000.0
        ):
            if self._xunfei.has_ws:
                self.logger.debug(f"静音超过阈值 ({self.min_silence_duration_ms}ms)，结束话语")
                if speech_chunk_count > 0:
                    await self._xunfei.close(send_last_frame=True)
                else:
                    await self._xunfei.close(send_last_frame=False)
                speech_chunk_count = 0
                self._is_speaking = False
            self._silence_started_time = None
        return speech_chunk_count

    def _evaluate_speech(self, audio_chunk_bytes: bytes) -> Optional[Tuple[bool, float]]:
        """VAD 判定单个音频块；处理出错或空块返回 None（调用方跳过该块）。"""
        try:
            audio_np = np.frombuffer(audio_chunk_bytes, dtype=np.int16)
            audio_float32 = audio_np.astype(np.float32) / 32768.0

            if audio_float32.ndim == 0 or audio_float32.size == 0:
                return None

            audio_tensor = self.torch.from_numpy(audio_float32)
            speech_prob = self.vad_model(audio_tensor, self.sample_rate).item()
            return (speech_prob > self.vad_threshold, speech_prob)

        except Exception as e:
            self.logger.exception(f"VAD 处理出错: {e}")
            return None

    async def _begin_utterance(self, audio_chunk_bytes: bytes, speech_prob: float) -> Optional[int]:
        """话语开始：建立讯飞连接并发送首帧。

        Returns:
            成功进入说话态返回已发送帧数（1）；建连或首帧发送失败返回 None
            （调用方跳过本轮后续阶段）。
        """
        self.logger.debug(f"VAD: 话语开始 (Prob: {speech_prob:.2f})")
        self._is_speaking = True
        self._silence_started_time = None
        speech_chunk_count = 0

        if not await self._xunfei.ensure_connection():
            self.logger.error("无法建立讯飞连接")
            self._is_speaking = False
            return None

        if self._xunfei.has_ws:
            try:
                await self._xunfei.send_frame(STATUS_FIRST_FRAME, audio_chunk_bytes, timeout=2.0)
                speech_chunk_count += 1
                self.logger.debug("已发送第一帧")
            except Exception as e:
                self.logger.exception(f"发送第一帧失败: {e}")
                await self._xunfei.close(send_last_frame=False)
                self._is_speaking = False
                speech_chunk_count = 0
                return None

        return speech_chunk_count

    async def _continue_utterance(self, audio_chunk_bytes: bytes, speech_chunk_count: int) -> int:
        """说话中的后续音频块：发送中间帧；失败则收尾连接并复位说话态。"""
        try:
            await self._xunfei.send_frame(STATUS_CONTINUE_FRAME, audio_chunk_bytes, timeout=1.0)
            speech_chunk_count += 1
        except Exception as e:
            self.logger.exception(f"发送音频帧失败: {e}")
            await self._xunfei.close(send_last_frame=False)
            self._is_speaking = False
            speech_chunk_count = 0
        return speech_chunk_count

    async def _end_utterance(self, audio_chunk_bytes: bytes, now: float, speech_chunk_count: int) -> int:
        """VAD 判定话语结束：补发一帧尾部音频后转入静默计时；发送失败则收尾复位。"""
        self.logger.debug("VAD: 话语结束 (静音检测)")
        self._is_speaking = False
        self._silence_started_time = now

        if self._xunfei.is_open:
            try:
                await self._xunfei.send_frame(STATUS_CONTINUE_FRAME, audio_chunk_bytes, timeout=1.0)
            except Exception as e:
                self.logger.exception(f"发送静音帧失败: {e}")
                await self._xunfei.close(send_last_frame=False)
                self._silence_started_time = None
                speech_chunk_count = 0
        return speech_chunk_count

    async def _close_current_utterance(self, speech_chunk_count: int) -> int:
        """静默满阈值收尾话语：按是否已有音频帧决定是否携带结束帧。复位帧计数。"""
        if speech_chunk_count > 0:
            await self._xunfei.close(send_last_frame=True)
        else:
            await self._xunfei.close(send_last_frame=False)
        return 0

    async def _drain_results(self) -> AsyncIterator[RoomMessagePayload]:
        """排空识别结果队列：逐条直发联动对象发言事件并交还给消费方。"""
        try:
            while not self._xunfei.result_queue.empty():
                payload = await asyncio.wait_for(self._xunfei.result_queue.get(), timeout=0.01)
                # 直发联动对象发言事件（场次归属由盖章拦截器统一注入；
                # 落库走 sender_role="partner"，不计观众统计）
                await self.emit_event(CoreEvents.ROOM_MESSAGE_PARTNER_SPEECH, payload)
                yield payload
                self._xunfei.result_queue.task_done()
        except asyncio.TimeoutError:
            pass
