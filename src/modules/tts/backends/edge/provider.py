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
from typing import Any, Dict, Optional, TYPE_CHECKING, Tuple

import numpy as np

from src.modules.config.core_schemas import EdgeTTSParams
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from ...common import build_stats_dict, run_full_engine_speech

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

    # 参数 Schema 单一事实源在 config 侧（core_schemas），此处仅别名引用
    ConfigSchema = EdgeTTSParams

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
        """合成并播放语音（发声四步前奏走公共入口，引擎只剩合成差异）。

        Args:
            text: 要合成的文本。
            utterance_id: 一次发声实例的唯一 ID；非 None 时按
                ``tts.utterance.*`` 生命周期发布事件。
        """
        await run_full_engine_speech(self, text=text, utterance_id=utterance_id, synthesize=self.synthesize)

    async def synthesize(self, text: str) -> Tuple[np.ndarray, int]:
        """调用 Edge TTS 合成（失败直接抛出）。

        异常不在此处理：由公共骨架统一计错误数、发 ``tts.utterance.failed``
        并上抛给编排队列兜底——引擎侧吞错会让失败以假音频形态静默过关。
        """
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
        finally:
            if tmp_filename:
                try:
                    os.remove(tmp_filename)
                except Exception as e:
                    self.logger.debug(f"临时音频文件清理失败: {tmp_filename}: {e}")

    def _setup_audio_device(self) -> None:
        """初始化音频设备管理器（统一走系统默认输出设备）"""
        # 函数内 import：src.modules.audio 顶层即拉起 numpy（重型数值依赖），仅在需要音频设备管理时加载
        from src.modules.audio import AudioDeviceManager

        manager = AudioDeviceManager(sample_rate=48000, channels=1, dtype=np.float32, sink=self.audio_sink)
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
    audio_sink: Optional[Any] = None,
) -> EdgeTTSProvider:
    """工厂：构造 ``EdgeTTSProvider`` 实例。

    装配阶段由编排队列持有 Provider 实例并直接调用 ``handle_speech``。
    """
    return EdgeTTSProvider(
        config=config,
        event_bus=event_bus,
        audio_sink=audio_sink,
    )


__all__ = ["EdgeTTSProvider", "create_edge_tts_provider"]
