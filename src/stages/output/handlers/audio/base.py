"""
AudioHandlerBase - 语音合成 Handler 抽象基类

设计目标：
  对标 AvatarHandlerBase，抽取三个 TTS Handler（EdgeTTS/GPTSoVITS/OmniTTS）的
  公共样板代码到基类，让子类只关注引擎特有的合成逻辑。

基类提供：
  1. 构造器（config / event_bus / audio_stream_channel）
  2. 事件订阅/取消（OUTPUT_INTENT_DISPATCHED）
  3. AudioDeviceManager 初始化辅助
  4. AudioStreamChannel 通知辅助（notify_start / publish / notify_end）
  5. handle() 模板方法（提取 speech → 加锁 → 通知 → 合成 → 通知）
  6. **per-handler 完成事件自动 emit**（OUTPUT_HANDLER_COMPLETED，放在 finally 里保证异常也发）

子类必须实现：
  - _synthesize(text): 核心合成逻辑
  - init(): 引擎初始化 + 调用 _setup_audio_device() + _subscribe_output_events()
  - cleanup(): 引擎清理 + _unsubscribe_output_events()

覆盖 handle() 时（如 GPTSoVITS）需自行保证在末尾 emit OUTPUT_HANDLER_COMPLETED。
"""

from abc import ABC, abstractmethod
import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

import numpy as np

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import IntentPayload, OutputHandlerCompletedPayload
from src.modules.logging import get_logger
from src.modules.streaming.audio_stream_channel import AudioStreamChannel
from src.modules.tts import AudioDeviceManager

if TYPE_CHECKING:
    from src.modules.types import Intent

from .utils.device_finder import find_device_index


class AudioHandlerBase(ABC):
    """语音合成 Handler 抽象基类。

    子类职责：
    - 定义自己的 ConfigSchema（嵌套在子类中）
    - 在 __init__ 中解析配置到实例属性
    - 在 init() 中初始化 TTS 引擎 + 调用 _setup_audio_device()
    - 实现 _synthesize(text)：合成并 publish/play
    - 在 cleanup() 中释放引擎资源
    """

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
        audio_stream_channel: AudioStreamChannel,
    ):
        self.config = config
        self.event_bus = event_bus
        self.audio_stream_channel = audio_stream_channel
        self.logger = get_logger(self.__class__.__name__)

        # 音频设备管理器（子类在 init() 中调用 _setup_audio_device() 初始化）
        self.audio_manager: Optional[AudioDeviceManager] = None

        # 串行化锁：同一时间只合成一句话
        self.tts_lock = asyncio.Lock()

        # 事件订阅状态标志（确保幂等）
        self._dispatch_subscribed = False

    # ── 模板方法 ────────────────────────────────────────────────

    async def handle(self, intent: "Intent"):
        """模板方法：提取 speech → 加锁 → 通知 → 合成 → 通知。

        子类可以完全覆盖此方法（如 GPTSoVITS 需要 inline 文本清洗），
        但覆盖时建议仍用 _notify_audio_start/_notify_audio_end 保证通知完整性。

        无论成功或异常,都会在 finally 中 emit OUTPUT_HANDLER_COMPLETED,
        以便 OutputHandlerManager 可以聚合各 handler 的完成事件。
        """
        text = intent.speech
        if not text:
            self.logger.debug("TTS 文本为空，跳过渲染")
            await self._emit_completed(intent, success=True)
            return

        success = True
        async with self.tts_lock:
            await self._notify_audio_start(text)
            try:
                await self._synthesize(text)
            except Exception as e:
                success = False
                self.logger.error(f"TTS 合成失败: {e}", exc_info=True)
                raise
            finally:
                await self._notify_audio_end(text)
                await self._emit_completed(intent, success=success)

    async def _synthesize(self, text: str):  # noqa: B027 — 故意非抽象；GPTSoVITS 覆盖 handle() 无需此方法
        """合成钩子：子类可覆盖。

        当子类使用父类 handle() 模板方法时被调用。
        子类如完全覆盖 handle()（如 GPTSoVITSHandler），则无需实现此方法。

        覆盖时方法内应：
        - 调用 TTS 引擎获取音频数据
        - 调用 self._publish_chunk() 发布音频块（供口型同步使用）
        - 调用 self.audio_manager.play_audio() / write_chunk() 播放
        - 自行处理引擎特定的清理（如删除临时文件）
        """
        pass

    # ── 生命周期（子类必须实现） ────────────────────────────────

    @abstractmethod
    async def init(self):
        """初始化子类 TTS 引擎。

        通用步骤（子类 init() 中按顺序调用）：
        1. 检查依赖
        2. 初始化 TTS 客户端/引擎
        3. self._setup_audio_device(...)
        4. self._subscribe_output_events()
        """
        ...

    @abstractmethod
    async def cleanup(self):
        """清理子类引擎资源。

        通用步骤（子类 cleanup() 中按顺序调用）：
        1. 停止音频播放 / 停止流
        2. 清空缓冲区
        3. self._unsubscribe_output_events()
        """
        ...

    # ── 音频设备辅助 ────────────────────────────────────────────

    def _setup_audio_device(
        self,
        sample_rate: int,
        channels: int = 1,
        dtype=np.float32,
        device_name: Optional[str] = None,
    ) -> AudioDeviceManager:
        """初始化并配置 AudioDeviceManager。

        子类在 init() 中调用此方法替代各自手写的设备初始化代码。
        """
        manager = AudioDeviceManager(sample_rate=sample_rate, channels=channels, dtype=dtype)
        if device_name:
            index = find_device_index(
                device_name=device_name,
                logger=self.logger,
                sample_rate=sample_rate,
                channels=channels,
                dtype=dtype,
            )
            if index is not None:
                manager.set_output_device(device_index=index)
        self.audio_manager = manager
        return manager

    # ── 事件订阅辅助 ────────────────────────────────────────────

    async def _subscribe_output_events(self):
        """订阅 OUTPUT_INTENT_DISPATCHED（幂等）。"""
        if self.event_bus and not self._dispatch_subscribed:
            self.event_bus.on(
                CoreEvents.OUTPUT_INTENT_DISPATCHED,
                self._on_intent_dispatched,
                model_class=IntentPayload,
            )
            self._dispatch_subscribed = True
            self.logger.debug(f"{self.__class__.__name__} 已订阅 {CoreEvents.OUTPUT_INTENT_DISPATCHED}")

    async def _unsubscribe_output_events(self):
        """取消订阅 OUTPUT_INTENT_DISPATCHED（幂等）。"""
        if self.event_bus and self._dispatch_subscribed:
            self.event_bus.off(CoreEvents.OUTPUT_INTENT_DISPATCHED, self._on_intent_dispatched)
            self._dispatch_subscribed = False

    async def _on_intent_dispatched(self, event_name: str, payload: IntentPayload, source: str):
        intent = payload.to_intent()
        await self.handle(intent)

    # ── AudioStreamChannel 通知辅助 ─────────────────────────────

    async def _notify_audio_start(self, text: str):
        """通知 AudioStreamChannel：音频开始。"""
        if self.audio_stream_channel:
            from src.modules.streaming.audio_chunk import AudioMetadata

            await self.audio_stream_channel.notify_start(AudioMetadata(text=text, sample_rate=0, channels=0))

    async def _notify_audio_end(self, text: str):
        """通知 AudioStreamChannel：音频结束。"""
        if self.audio_stream_channel:
            from src.modules.streaming.audio_chunk import AudioMetadata

            await self.audio_stream_channel.notify_end(AudioMetadata(text=text, sample_rate=0, channels=0))

    async def _publish_chunk(
        self,
        data: bytes,
        sample_rate: int,
        channels: int,
        sequence: int,
    ):
        """发布一个音频块到 AudioStreamChannel。"""
        if self.audio_stream_channel:
            from src.modules.streaming.audio_chunk import AudioChunk

            await self.audio_stream_channel.publish(
                AudioChunk(
                    data=data,
                    sample_rate=sample_rate,
                    channels=channels,
                    sequence=sequence,
                    timestamp=time.time(),
                )
            )

    # ── 完成事件辅助(供 handle() 调用) ──────────────────────────

    async def _emit_completed(self, intent: "Intent", success: bool = True) -> None:
        """emit 一个 OUTPUT_HANDLER_COMPLETED 事件给聚合者。

        handler_name 用 `self.__class__.__name__`（与 Manager 端的 `type(h).__name__`
        一致），intent_id 从 `intent.metadata.intent_id` 取。无 event_bus 时静默跳过。
        老 Intent 结构缺 metadata 时降级到 "unknown" 让 watchdog 兜底。
        """
        if self.event_bus is None:
            return
        try:
            intent_id = intent.metadata.intent_id
        except AttributeError:
            # 老 Intent 结构没 metadata/intent_id,降级到占位符避免聚合死锁
            # (这种情况 handler 应被 manager 视为 unknown,不会完成聚合)
            intent_id = "unknown"
        await self.event_bus.emit(
            CoreEvents.OUTPUT_HANDLER_COMPLETED,
            OutputHandlerCompletedPayload(
                handler_name=self.__class__.__name__,
                intent_id=intent_id,
                success=success,
            ),
            source=self.__class__.__name__,
        )
