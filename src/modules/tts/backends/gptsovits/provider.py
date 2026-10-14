"""GPTSoVITSProvider - GPT-SoVITS 语音合成引擎

- 引擎：GPT-SoVITS 客户端初始化 + 流式合成 + AudioDeviceManager 播放
- 调用方：``src.agents.streamer.utterance_queue``（编排层确定性调用）
- 自治生命周期：``handle_speech()`` 入口自动 ensure_setup，调用方无需预调
- 错误语义：合成失败主动发 ``tts.utterance.failed`` 并 raise，由队列兜底
- 接受可选 ``utterance_id`` 参数：传入时按 ``tts.utterance.*`` 生命周期发布
  事件；缺省/None 时静默跳过（手动 / 直调场景无 utterance 上下文）
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, AsyncIterator, Dict, Optional, TYPE_CHECKING, cast

import numpy as np

from src.modules.config.core_schemas import GPTSoVITSParams
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from ...common import (
    normalize_tts_text,
    build_stats_dict,
    emit_utterance_failed,
    emit_utterance_finished,
    emit_utterance_started,
)
from .client import GPTSoVITSClient, GPTSoVITSServiceError

if TYPE_CHECKING:
    pass


# 音频流参数
CHANNELS = 1
DTYPE = np.int16
BLOCKSIZE = 1024
SAMPLE_SIZE = DTYPE().itemsize
BUFFER_REQUIRED_BYTES = BLOCKSIZE * CHANNELS * SAMPLE_SIZE


class GPTSoVITSProvider:
    """GPT-SoVITS 语音合成引擎。

    引擎通过 ``handle_speech(text, utterance_id)`` 暴露唯一入口；调用方
    （编排队列）无需关心 setup / cleanup 生命周期——``handle_speech`` 开头
    自动 ensure_setup。
    """

    PROVIDER_NAME = "gptsovits"

    # 参数 Schema 单一事实源在 config 侧（core_schemas），此处仅别名引用
    ConfigSchema = GPTSoVITSParams

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
        audio_sink: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        # 音频分接（AudioSink 协议形状；口型分析等消费者经装配链注入）
        self.audio_sink = audio_sink
        self.logger = get_logger(self.__class__.__name__)

        self.typed_config = self.ConfigSchema.from_dict(config)
        self.host = self.typed_config.host
        self.port = self.typed_config.port
        self.ref_audio_path = self.typed_config.ref_audio_path
        self.prompt_text = self.typed_config.prompt_text
        self.text_language = self.typed_config.text_language
        self.prompt_language = self.typed_config.prompt_language
        self.top_k = self.typed_config.top_k
        self.top_p = self.typed_config.top_p
        self.temperature = self.typed_config.temperature
        self.speed_factor = self.typed_config.speed_factor
        self.streaming_mode = self.typed_config.streaming_mode
        self.media_type = self.typed_config.media_type
        self.text_split_method = self.typed_config.text_split_method
        self.batch_size = self.typed_config.batch_size
        self.batch_threshold = self.typed_config.batch_threshold
        self.repetition_penalty = self.typed_config.repetition_penalty
        self.sample_steps = self.typed_config.sample_steps
        self.super_sampling = self.typed_config.super_sampling
        self.sample_rate = self.typed_config.sample_rate

        self.input_pcm_queue = deque(b"")
        self.audio_data_queue = deque(maxlen=1000)

        self.render_count = 0
        self.error_count = 0
        self.tts_client: Optional[GPTSoVITSClient] = None
        self.audio_manager: Any = None
        self._is_connected = False
        self._has_started = False
        # 启动预热任务（服务端首推理惩罚挪出直播首句）；可变状态在 __init__ 初始化
        self._warmup_task: Optional[asyncio.Task] = None
        # 串行化锁
        self.tts_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    # ===== 生命周期 =====

    async def setup(self) -> None:
        """初始化引擎（幂等）。"""
        if self._has_started:
            return
        self.tts_client = GPTSoVITSClient(self.host, self.port)
        self.tts_client.initialize()

        if self.ref_audio_path and self.prompt_text:
            self.tts_client.set_refer_audio(self.ref_audio_path, self.prompt_text)
        else:
            self.logger.error(
                "GPT-SoVITS api_v2 每请求必填参考音频：请在配置 [tts.gptsovits] 填写 "
                "ref_audio_path（相对 GPT-SoVITS 根目录）与 prompt_text（参考音频转写），否则合成将失败"
            )

        from src.modules.audio import AudioDeviceManager

        manager = AudioDeviceManager(
            sample_rate=self.sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            sink=self.audio_sink,
        )
        self.audio_manager = manager

        self.tts_client.load_preset("default")
        self._has_started = True
        self._is_connected = True
        self._warmup_task = asyncio.create_task(self._warmup())
        self.logger.info("GPTSoVITSProvider 设置完成")

    async def _warmup(self) -> None:
        """启动预热：把服务端冷启动惩罚挪出直播首句。

        两步——/set_refer_audio 触发参考音频预处理缓存；极短文本合成触发
        模型加载后的首次推理（CUDA Graph 编译等，实测约 5 秒）。全程
        fire-and-forget：服务未起/预热失败只记 warning，不影响装配，真实
        首句合成自带兜底（最多退化为承担一次冷启动延迟）。
        """
        try:
            if self.ref_audio_path:
                await asyncio.to_thread(
                    lambda: self.tts_client.set_refer_audio_remote(self.ref_audio_path)  # type: ignore[union-attr]
                )
            await asyncio.to_thread(
                lambda: self.tts_client.tts("预热。")  # type: ignore[union-attr]
            )
            self.logger.info("GPT-SoVITS 预热完成，首句合成已是热态")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.warning(f"GPT-SoVITS 预热未完成（服务未启动或异常，首句合成将承担冷启动延迟）: {e}")

    async def cleanup(self) -> None:
        if not self._has_started:
            return
        self.logger.info("GPTSoVITSProvider 清理中...")
        if self._warmup_task is not None and not self._warmup_task.done():
            self._warmup_task.cancel()
        if self.audio_manager:
            self.audio_manager.stop_stream()
        self.input_pcm_queue.clear()
        self.audio_data_queue.clear()
        self._is_connected = False
        self._has_started = False
        self.logger.info("GPTSoVITSProvider 清理完成")

    # ===== 业务方法 =====

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        """合成并播放语音。

        Args:
            text: 要合成的文本。
            utterance_id: 一次发声实例的唯一 ID；非 None 时按
                ``tts.utterance.*`` 生命周期发布事件。
        """
        if not self._has_started:
            await self.setup()
        if not text or not text.strip():
            self.logger.debug("TTS 文本为空，跳过渲染")
            return

        original_text = text.strip()
        self.logger.debug(f"准备 TTS: '{original_text[:50]}...'")

        # 文本清洗
        final_text = normalize_tts_text(original_text)
        if final_text != original_text:
            self.logger.debug(f"文本清洗: 移除了 {len(original_text) - len(final_text)} 个不支持字符")
        if not final_text.strip():
            self.logger.debug("清洗后文本为空，跳过渲染")
            return

        # 流式引擎合成未完时 duration_ms 传 None；started 在首块 PCM 写声卡前发布。
        # 事件 speech_text 传规范化前原文：字幕是展示面，不随合成清洗改写
        await emit_utterance_started(
            self.event_bus,
            utterance_id=utterance_id,
            speech_text=original_text,
            engine=self.PROVIDER_NAME,
            duration_ms=None,
        )

        try:
            if not (self.ref_audio_path and self.prompt_text):
                raise RuntimeError(
                    "参考音频未配置：api_v2 每请求必填，请在配置 [tts.gptsovits] "
                    "填写 ref_audio_path（相对 GPT-SoVITS 根目录）与 prompt_text"
                )

            async with self.tts_lock:
                # 同步 requests 调用（连接+等待响应头），放线程池执行：GPTSoVITS
                # 推理慢时在事件循环线程内等待会冻结整个循环（WebUI 同循环全卡）
                audio_stream = await asyncio.to_thread(
                    lambda: self.tts_client.tts_stream(  # type: ignore[union-attr]
                        text=final_text,
                        text_lang=self.text_language,
                        prompt_lang=self.prompt_language,
                        top_k=self.top_k,
                        top_p=self.top_p,
                        temperature=self.temperature,
                        speed_factor=self.speed_factor,
                        text_split_method=self.text_split_method,
                        batch_size=self.batch_size,
                        batch_threshold=self.batch_threshold,
                        repetition_penalty=self.repetition_penalty,
                        sample_steps=self.sample_steps,
                        super_sampling=self.super_sampling,
                        media_type=self.media_type,
                        streaming_mode=self.streaming_mode,
                    )
                )

                self.audio_manager.start_stream(utterance_id=utterance_id)

                chunk_index = 0
                async for chunk in self._process_audio_stream(audio_stream):
                    if chunk is not None:
                        self.audio_manager.write_chunk(chunk)
                        chunk_index += 1

                self.audio_manager.stop_stream()

            self.logger.debug(f"TTS 播放完成: '{final_text[:30]}...'")
            self.render_count += 1

            # 流式引擎 finished 时长按整段文本播完估时（精确分块时长需声卡反馈，YAGNI 不做）
            await emit_utterance_finished(
                self.event_bus,
                utterance_id=utterance_id,
                engine=self.PROVIDER_NAME,
                duration_ms=0,
            )

        except asyncio.CancelledError:
            # 队列看门狗超时取消：声卡流必须显式关闭并丢弃未播预缓冲，
            # 否则 PCM 残留会继续播出，与下一条 utterance 的音频重叠
            self.audio_manager.stop_stream(discard_pending=True)
            self.logger.warning(f"TTS 渲染被取消（已截断播放）: utterance_id={utterance_id}")
            raise
        except GPTSoVITSServiceError as e:
            # 服务未启动等环境故障是预期内的：简短告警即可，不打全量 traceback
            self.error_count += 1
            self.logger.warning(f"TTS 渲染失败: {e}（本条放弃，等待服务恢复）")
            await emit_utterance_failed(
                self.event_bus,
                utterance_id=utterance_id,
                speech_text=text,
                engine=self.PROVIDER_NAME,
                error_message=str(e),
            )
            raise
        except Exception as e:
            self.error_count += 1
            self.logger.exception(f"TTS 渲染失败: {e}")
            await emit_utterance_failed(
                self.event_bus,
                utterance_id=utterance_id,
                speech_text=text,
                engine=self.PROVIDER_NAME,
                error_message=f"{type(e).__name__}: {e}",
            )
            raise

    def synthesize_bytes(self, text: str) -> bytes:
        """按当前配置合成整段音频字节（不播放、不发事件）。

        与 ``handle_speech`` 同源的合成路径：同一份文本规范化与参数组装，
        供批量回放/巡检等离线场景复用；仅传输方式为整段返回（服务端合成
        逻辑与流式完全一致）。

        Args:
            text: 待合成文本（内部先做规范化）。

        Returns:
            完整 WAV 字节；规范化后为空文本时返回空字节。
        """
        if not self._has_started or self.tts_client is None:
            raise RuntimeError("引擎未初始化：请先调用 setup()")
        final_text = normalize_tts_text(text)
        if not final_text.strip():
            return b""
        return self.tts_client.tts(
            text=final_text,
            text_lang=self.text_language,
            prompt_lang=self.prompt_language,
            top_k=self.top_k,
            top_p=self.top_p,
            temperature=self.temperature,
            speed_factor=self.speed_factor,
            text_split_method=self.text_split_method,
            batch_size=self.batch_size,
            batch_threshold=self.batch_threshold,
            repetition_penalty=self.repetition_penalty,
            sample_steps=self.sample_steps,
            super_sampling=self.super_sampling,
            media_type=self.media_type,
            streaming_mode=False,
        )

    async def _process_audio_stream(self, audio_stream: Any) -> AsyncIterator[np.ndarray]:
        """处理音频流（同步/异步迭代器）"""
        # 函数体内导入：单元测试 patch wav_decoder.decode_wav_chunk 拦截解码，
        # 顶部导入会使 patch 失效
        from src.modules.tts.wav_decoder import decode_wav_chunk

        try:
            if hasattr(audio_stream, "__aiter__"):
                async for chunk in audio_stream:
                    if not chunk:
                        continue
                    audio_chunk = await decode_wav_chunk(chunk, dtype=DTYPE)
                    if audio_chunk is not None:
                        yield audio_chunk
            else:
                # requests 流式生成器的 next() 会同步阻塞读 socket，逐块放线程池
                # 迭代：服务端推理间隙（chunk 间隔可达数十秒）冻结事件循环的元凶
                loop = asyncio.get_running_loop()
                chunk_iter = iter(audio_stream)
                sentinel = object()
                while True:
                    chunk = await loop.run_in_executor(None, next, chunk_iter, sentinel)
                    if chunk is sentinel:
                        break
                    if not chunk:
                        continue
                    audio_chunk = await decode_wav_chunk(cast(bytes, chunk), dtype=DTYPE)
                    if audio_chunk is not None:
                        yield audio_chunk
        except Exception as e:
            self.logger.exception(f"处理音频流失败: {e}")
            raise

    async def debug_synthesize(self, text: str, overrides: Optional[Dict[str, Any]] = None) -> bytes:
        """调试合成：按配置 + 覆盖参数合成完整音频并返回 wav 字节。

        与 ``handle_speech`` 的差异：不写声卡、不发 utterance 事件，供调
        试面板做参数对比试听与下载。覆盖键与合成参数同名（temperature /
        speed_factor / sample_steps / top_k / top_p），None 值不覆盖。
        自治 ensure_setup 与 ``tts_lock`` 串行化内聚于此（与 handle_speech
        同语义），调用方无需预调 setup——首次直调（未试说过）也能工作。
        """
        if not self._has_started:
            await self.setup()
        clean = normalize_tts_text(text)
        if not clean.strip():
            raise ValueError("文本规范化后为空，无可合成内容")

        params: Dict[str, Any] = {
            "text": clean,
            "text_lang": self.text_language,
            "prompt_lang": self.prompt_language,
            "temperature": self.temperature,
            "speed_factor": self.speed_factor,
            "sample_steps": self.sample_steps,
            "top_k": self.top_k,
            "top_p": self.top_p,
            # 调试合成取完整 wav：固定非流式，忽略外部 streaming_mode
            "streaming_mode": 0,
            "media_type": self.media_type,
        }
        for key, value in (overrides or {}).items():
            if value is not None and key in params:
                params[key] = value
        async with self.tts_lock:
            return await asyncio.to_thread(self.tts_client.tts, **params)  # type: ignore[union-attr]

    def get_stats(self) -> Dict[str, Any]:
        stats = build_stats_dict(
            name=self.__class__.__name__,
            is_connected=self._is_connected,
            render_count=self.render_count,
            error_count=self.error_count,
        )
        # 对接的 GPT-SoVITS API 后端（调试页状态 chip 展示用）
        stats["backend"] = "api_v2"
        # 调试页参数表单的默认值与重置基准：引擎当前生效的合成配置
        stats["defaults"] = {
            "temperature": self.temperature,
            "speed_factor": self.speed_factor,
            "sample_steps": self.sample_steps,
            "top_k": self.top_k,
            "top_p": self.top_p,
        }
        # 参考音频转写：调试页文本框的默认值（试说参考音频原句）
        stats["prompt_text"] = self.prompt_text
        return stats


def create_gptsovits_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    audio_sink: Optional[Any] = None,
) -> GPTSoVITSProvider:
    """工厂：构造 ``GPTSoVITSProvider`` 实例。

    装配阶段由编排队列持有 Provider 实例并直接调用 ``handle_speech``。
    """
    return GPTSoVITSProvider(
        config=config,
        event_bus=event_bus,
        audio_sink=audio_sink,
    )


__all__ = ["GPTSoVITSProvider", "create_gptsovits_provider"]
