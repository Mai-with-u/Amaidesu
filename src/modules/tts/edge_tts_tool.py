"""EdgeTTSProvider - Edge TTS 语音合成引擎

- 引擎：Edge TTS 临时文件合成 + soundfile 读取 + AudioDeviceManager 播放
- 调用方：``src.agents.streamer.utterance_queue``（编排层确定性调用）
- 自治生命周期：``handle_speech()`` 入口自动 ensure_setup，调用方无需预调
- 错误语义：合成失败主动发 ``tts.utterance.failed`` 并 raise，由队列兜底
- 接受可选 ``utterance_id`` 参数：传入时按 ``tts.utterance.*`` 生命周期发布
  事件；缺省/None 时静默跳过（手动 / 直调场景无 utterance 上下文）
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import TYPE_CHECKING, Any, Dict, Optional

import numpy as np
from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from .common import (
    build_stats_dict,
    compute_duration_ms,
    emit_utterance_failed,
    emit_utterance_finished,
    emit_utterance_started,
)

if TYPE_CHECKING:
    pass


DEPENDENCIES_OK = True
try:
    import soundfile as sf
except ImportError:
    DEPENDENCIES_OK = False

try:
    import edge_tts
except ImportError:
    pass  # edge_tts 可选，由配置决定使用哪个引擎


class EdgeTTSProvider:
    """Edge TTS 语音合成引擎。

    引擎通过 ``handle_speech(text, utterance_id)`` 暴露唯一入口；调用方
    （编排队列）无需关心 setup / cleanup 生命周期——``handle_speech`` 开头
    自动 ensure_setup。
    """

    PROVIDER_NAME = "edge_tts"

    class ConfigSchema(BaseConfig):
        """EdgeTTS 配置"""

        type: str = "edge_tts"
        voice: str = Field(default="zh-CN-XiaoxiaoNeural", description="Edge TTS 语音")

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
    ):
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger(self.__class__.__name__)

        self.typed_config = self.ConfigSchema.from_dict(config)
        self.voice = self.typed_config.voice

        # 音频设备管理器（在 setup 中初始化）
        self.audio_manager: Any = None
        self._is_connected = False
        self._has_started = False
        # 串行化锁：同一时间只合成一句话
        self.tts_lock = asyncio.Lock()
        self.render_count = 0
        self.error_count = 0

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    # ===== 生命周期 =====

    async def setup(self) -> None:
        """初始化引擎（幂等）。

        调用方在装配阶段和 ``handle_speech`` 自治入口都可能触发；重复调用
        早退，不重复初始化。
        """
        if self._has_started:
            return
        if not DEPENDENCIES_OK:
            raise RuntimeError("缺少必要的依赖: soundfile")
        if "edge_tts" not in globals():
            raise RuntimeError("Edge TTS 引擎未安装")

        self._setup_audio_device()
        self._has_started = True
        self._is_connected = True
        self.logger.info("EdgeTTSProvider 启动完成")

    async def cleanup(self) -> None:
        if not self._has_started:
            return
        self.logger.info("EdgeTTSProvider 停止中...")
        if self.audio_manager:
            self.audio_manager.stop_audio()
        self._is_connected = False
        self._has_started = False
        self.logger.info("EdgeTTSProvider 停止完成")

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
        if not text:
            self.logger.debug("TTS 文本为空，跳过渲染")
            return
        async with self.tts_lock:
            await self._synthesize(text, utterance_id=utterance_id)

    async def _synthesize(self, text: str, utterance_id: Optional[str] = None) -> None:
        """执行 TTS 合成 + 播放（对应父类 handle() 模板）"""
        self.logger.debug(f"开始 TTS 渲染: '{text[:30]}...'")
        try:
            audio_array, samplerate = await self._edge_tts_synthesize(text)
            duration_ms = compute_duration_ms(len(audio_array), samplerate)
            self.logger.debug(f"音频时长: {duration_ms}毫秒")

            await emit_utterance_started(
                self.event_bus,
                utterance_id=utterance_id,
                speech_text=text,
                engine=self.PROVIDER_NAME,
                duration_ms=duration_ms,
            )

            self.logger.debug("开始播放音频...")
            await self.audio_manager.play_audio(audio_array, samplerate=samplerate)

            await emit_utterance_finished(
                self.event_bus,
                utterance_id=utterance_id,
                engine=self.PROVIDER_NAME,
                duration_ms=duration_ms,
            )

            self.render_count += 1
        except asyncio.CancelledError:
            # 协程取消不会停止已提交给声卡的播放（sounddevice 线程独立于
            # 事件循环），超时取消时必须显式截断，否则与下一条语音重叠
            if self.audio_manager:
                self.audio_manager.stop_audio()
            self.logger.warning("TTS 渲染被取消（已截断播放）: utterance_id=: {}", utterance_id)
            raise
        except Exception as e:
            self.error_count += 1
            await emit_utterance_failed(
                self.event_bus,
                utterance_id=utterance_id,
                engine=self.PROVIDER_NAME,
                error_message=f"{type(e).__name__}: {e}",
            )
            self.logger.opt(exception=True).error("TTS 渲染失败: : {}", e)
            raise

    async def _edge_tts_synthesize(self, text: str):
        """调用 Edge TTS 合成"""
        if "edge_tts" not in globals():
            raise RuntimeError("Edge TTS 未安装")
        tmp_filename = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
                tmp_filename = tmp_file.name
            communicate = edge_tts.Communicate(text, self.voice)
            await asyncio.to_thread(communicate.save_sync, tmp_filename)
            self.logger.debug(f"Edge TTS 合成完成: {tmp_filename}")
            audio_array, samplerate = await asyncio.to_thread(sf.read, tmp_filename, dtype="float32")
            return audio_array, samplerate
        except Exception as e:
            self.logger.opt(exception=True).error("Edge TTS 合成失败: : {}", e)
            return np.zeros(44100, dtype=np.float32), 16000
        finally:
            if tmp_filename:
                try:
                    os.remove(tmp_filename)
                except Exception:
                    pass

    def _setup_audio_device(self) -> None:
        """初始化音频设备管理器（统一走系统默认输出设备）"""
        from src.modules.audio import AudioDeviceManager

        manager = AudioDeviceManager(sample_rate=48000, channels=1, dtype=np.float32)
        self.audio_manager = manager

    def get_stats(self) -> Dict[str, Any]:
        return build_stats_dict(
            name=self.__class__.__name__,
            is_connected=self._is_connected,
            render_count=self.render_count,
            error_count=self.error_count,
        )


def create_edge_tts_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
) -> EdgeTTSProvider:
    """工厂：构造 ``EdgeTTSProvider`` 实例。

    装配阶段由编排队列持有 Provider 实例并直接调用 ``handle_speech``。
    """
    return EdgeTTSProvider(
        config=config,
        event_bus=event_bus,
    )


__all__ = ["EdgeTTSProvider", "create_edge_tts_provider"]
