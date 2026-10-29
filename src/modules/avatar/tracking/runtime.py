"""面捕合成器运行时：订阅事实事件喂给合成器，按帧率推进，把每一帧发给挂上的平台驱动

时序（表情跟着声音走，不再比声音早一个合成时长）：
- ``streamer.speech``：开着 TTS 时先记下这句话的情绪，不上脸；没开 TTS 时立刻上脸，
  按字数估算这句话说多久；
- ``tts.utterance.started``：这句话真正开始播放，表情从此刻浮现，同时进入"说话中"；
- ``tts.utterance.finished``：播完，表情再停留一会儿后回落；
- ``tts.utterance.failed``：没播出来，记下的情绪作废，已上脸的立刻回落。

口型分析器的信号经一个只记数值的渲染器接进来，帧循环每帧取最新值。没有驱动挂着
时帧循环不跑，零开销。所有路径 fail-soft：驱动写失败、事件处理异常只记日志。
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import OrderedDict
from typing import Any, Callable, List, Optional, Protocol, Set, Tuple, runtime_checkable

from src.modules.avatar.lipsync import LipSyncRenderer, MouthSignal
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms as wall_now_ms

from .channels import Frame
from .config import TrackingConfig
from .synthesizer import TrackingSynthesizer


@runtime_checkable
class TrackingDriver(Protocol):
    """平台驱动契约：把通用通道的一帧写成本平台的输入。

    ``apply`` 要尽快返回（发出去就走，不等平台回复），否则会拖慢帧循环；
    ``ready`` 为假时（未连接、未校准）这一帧跳过它。
    """

    name: str

    def ready(self) -> bool: ...

    async def apply(self, frame: Frame) -> None: ...


class _MouthTap(LipSyncRenderer):
    """接在口型分析器上的"渲染器"：只把嘴张开量交给合成器，不写任何平台参数。"""

    def __init__(self, synthesizer: TrackingSynthesizer, clock: Callable[[], int]) -> None:
        self._synthesizer = synthesizer
        self._clock = clock

    async def on_mouth_signal(self, signal: MouthSignal) -> None:
        self._synthesizer.on_mouth(signal.mouth_open, self._clock())

    async def on_session_end(self) -> None:
        self._synthesizer.on_mouth_end(self._clock())


class TrackingRuntime:
    """面捕合成器运行时（组合根持有，所有平台驱动共用一个）。"""

    # 记下但还没开始播放的发言情绪最多留多少条、留多久（播放失败没有回音的兜底）
    PENDING_LIMIT = 32
    PENDING_TTL_MS = 120_000
    # 收到"开始播放"后先按预计时长 + 余量排一个回落时刻，"播完"事件到了再以它为准；
    # 万一播完事件丢了，表情也不会一直停在脸上
    SAFETY_MARGIN_MS = 2000

    def __init__(
        self,
        config: TrackingConfig,
        *,
        event_bus: Optional[EventBus] = None,
        lipsync_analyzer: Optional[Any] = None,
        tts_enabled: bool = True,
        clock: Callable[[], int] = wall_now_ms,
        rng: Optional[random.Random] = None,
        run_loop: bool = True,
    ) -> None:
        """Args:
        tts_enabled: 是否装配了 TTS（决定表情等"开始播放"还是发言即上脸）。
        clock: 毫秒时刻来源（离线回放与测试注入假时钟）。
        rng: 随机源（同种子可复现同一串眨眼、眼神与漫步）。
        run_loop: False = 不起帧循环，由调用方逐帧调 :meth:`tick_once`（离线回放与测试）。
        """
        self.logger = get_logger("TrackingRuntime")
        self._config = config
        self._run_loop_enabled = run_loop
        self._event_bus = event_bus
        self._lipsync_analyzer = lipsync_analyzer
        self._tts_enabled = tts_enabled
        self._clock = clock
        self.synthesizer = TrackingSynthesizer(config, rng=rng)
        self._mouth_tap = _MouthTap(self.synthesizer, clock)
        self._drivers: List[TrackingDriver] = []
        self._driver_errors: dict[str, int] = {}
        self._pending: "OrderedDict[str, Tuple[str, float, int]]" = OrderedDict()
        self._playing: Set[str] = set()
        self._tool_seq = 0
        self._loop_task: Optional[asyncio.Task] = None
        self._started = False

    # ===== 生命周期 =====

    async def start(self) -> None:
        """订阅事件、接上口型信号（幂等）。帧循环在第一个驱动挂上时才开始跑。"""
        if self._started:
            return
        self._started = True
        bus = self._event_bus
        if bus is not None:
            bus.on(CoreEvents.STREAMER_SPEECH, self._on_speech, model_class=StreamerSpeechPayload)
            bus.on(CoreEvents.TTS_UTTERANCE_STARTED, self._on_started, model_class=UtteranceStartedPayload)
            bus.on(CoreEvents.TTS_UTTERANCE_FINISHED, self._on_finished, model_class=UtteranceFinishedPayload)
            bus.on(CoreEvents.TTS_UTTERANCE_FAILED, self._on_failed, model_class=UtteranceFailedPayload)
        if self._lipsync_analyzer is not None:
            self._lipsync_analyzer.add_renderer(self._mouth_tap)
        self.logger.info(f"面捕合成器已启动（{self._config.tick_hz} 帧/秒，TTS {'开' if self._tts_enabled else '关'}）")
        self._ensure_loop()

    async def stop(self) -> None:
        """退订、摘掉口型信号、停帧循环，并给还挂着的驱动各发一帧静止姿态（幂等）。"""
        if not self._started:
            return
        self._started = False
        bus = self._event_bus
        if bus is not None:
            for name, handler in (
                (CoreEvents.STREAMER_SPEECH, self._on_speech),
                (CoreEvents.TTS_UTTERANCE_STARTED, self._on_started),
                (CoreEvents.TTS_UTTERANCE_FINISHED, self._on_finished),
                (CoreEvents.TTS_UTTERANCE_FAILED, self._on_failed),
            ):
                try:
                    bus.off(name, handler)
                except Exception as exc:  # noqa: BLE001 - 退订失败不阻断关停
                    self.logger.debug(f"{name} 退订失败（已忽略）: {exc}")
        if self._lipsync_analyzer is not None:
            try:
                self._lipsync_analyzer.remove_renderer(self._mouth_tap)
            except Exception as exc:  # noqa: BLE001 - 摘除失败不阻断关停
                self.logger.debug(f"口型信号摘除失败（已忽略）: {exc}")
        await self._stop_loop()
        for driver in list(self._drivers):
            await self._send_rest(driver)
        self._drivers.clear()

    async def attach(self, driver: TrackingDriver) -> None:
        """挂上一个平台驱动（重复挂载幂等）；有驱动时帧循环才跑。"""
        if driver not in self._drivers:
            self._drivers.append(driver)
            self.logger.info(f"面捕合成器已挂上驱动: {driver.name}")
        self._ensure_loop()

    async def detach(self, driver: TrackingDriver) -> None:
        """摘下一个驱动：先发一帧静止姿态再摘（没有驱动了就停帧循环）。"""
        if driver not in self._drivers:
            return
        await self._send_rest(driver)
        self._drivers.remove(driver)
        if not self._drivers:
            await self._stop_loop()

    # ===== 帧循环 =====

    async def tick_once(self) -> Frame:
        """推进一帧并发给所有就绪的驱动（帧循环每拍调一次；测试可直接调）。"""
        frame = self.synthesizer.step(self._clock())
        for driver in list(self._drivers):
            if not driver.ready():
                continue
            try:
                await driver.apply(frame)
            except Exception:  # noqa: BLE001 - 单个驱动失败不影响其他驱动与下一帧
                count = self._driver_errors.get(driver.name, 0) + 1
                self._driver_errors[driver.name] = count
                if count == 1 or count % 300 == 0:
                    self.logger.exception(f"驱动 {driver.name} 写入失败（累计 {count} 次，之后每 300 次记一条）")
        return frame

    async def _run_loop(self) -> None:
        period = 1.0 / self._config.tick_hz
        try:
            while True:
                started = time.perf_counter()
                await self.tick_once()
                await asyncio.sleep(max(0.0, period - (time.perf_counter() - started)))
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - 帧循环意外退出要留痕，不让形象悄悄停住
            self.logger.exception("面捕合成器帧循环异常退出")

    def _ensure_loop(self) -> None:
        """已启动、有驱动、允许自跑时拉起帧循环（不论驱动先挂还是运行时先启动）。"""
        if self._run_loop_enabled and self._started and self._drivers and self._loop_task is None:
            self._loop_task = asyncio.create_task(self._run_loop(), name="TrackingRuntime.loop")

    async def _stop_loop(self) -> None:
        task = self._loop_task
        self._loop_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _send_rest(self, driver: TrackingDriver) -> None:
        if not driver.ready():
            return
        try:
            await driver.apply(self.synthesizer.rest_frame())
        except Exception:  # noqa: BLE001 - 收尾失败只记日志
            self.logger.exception(f"驱动 {driver.name} 发送静止姿态失败")

    # ===== 主动调用（工具、调试台） =====

    def express(self, emotion: str, intensity: float) -> str:
        """主播调工具设表情：立刻上脸，保持配置的时长后回落。返回调度结果。"""
        self._tool_seq += 1
        now = self._clock()
        return self.synthesizer.express(
            emotion,
            intensity,
            now,
            key=f"tool-{self._tool_seq}",
            release_at_ms=now + self._config.expression_tool_hold_ms,
        )

    def set_head_wander(self, enabled: bool) -> None:
        self.synthesizer.set_head_wander(enabled)

    @property
    def head_wander_enabled(self) -> bool:
        return self.synthesizer.head_wander_enabled

    def current_emotion(self) -> str:
        return self.synthesizer.current_emotion(self._clock())

    @property
    def driver_names(self) -> List[str]:
        return [driver.name for driver in self._drivers]

    # ===== 事件 =====

    def _estimate_ms(self, text: str) -> int:
        return max(1500, len(text) * self._config.speech_ms_per_char)

    async def _on_speech(self, event_name: str, payload: StreamerSpeechPayload, source: str) -> None:
        try:
            now = self._clock()
            if not self._tts_enabled:
                # 没有语音：发言即开口，按字数估算说多久
                release_at = now + self._estimate_ms(payload.text) + self._config.expression_linger_ms
                self.synthesizer.express(
                    payload.emotion, payload.emotion_intensity, now, key=payload.utterance_id, release_at_ms=release_at
                )
                return
            # 有语音：先记下，等真正开始播放再上脸
            self._pending[payload.utterance_id] = (payload.emotion, payload.emotion_intensity, now)
            while len(self._pending) > self.PENDING_LIMIT:
                self._pending.popitem(last=False)
            for uid in [uid for uid, (_, _, at) in self._pending.items() if now - at > self.PENDING_TTL_MS]:
                del self._pending[uid]
        except Exception:  # noqa: BLE001 - 装饰性路径边界
            self.logger.exception(f"发言情绪记录异常（已忽略）: utterance_id={getattr(payload, 'utterance_id', '?')}")

    async def _on_started(self, event_name: str, payload: UtteranceStartedPayload, source: str) -> None:
        try:
            now = self._clock()
            self._playing.add(payload.utterance_id)
            self.synthesizer.set_speaking(True)
            entry = self._pending.pop(payload.utterance_id, None)
            if entry is None:
                return
            emotion, intensity, _ = entry
            expected = payload.duration_ms or self._estimate_ms(payload.speech_text)
            release_at = now + expected + self._config.expression_linger_ms + self.SAFETY_MARGIN_MS
            self.synthesizer.express(emotion, intensity, now, key=payload.utterance_id, release_at_ms=release_at)
        except Exception:  # noqa: BLE001 - 装饰性路径边界
            self.logger.exception(f"开播表情处理异常（已忽略）: utterance_id={getattr(payload, 'utterance_id', '?')}")

    async def _on_finished(self, event_name: str, payload: UtteranceFinishedPayload, source: str) -> None:
        self._end_utterance(payload.utterance_id, linger=True)

    async def _on_failed(self, event_name: str, payload: UtteranceFailedPayload, source: str) -> None:
        self._pending.pop(payload.utterance_id, None)
        self._end_utterance(payload.utterance_id, linger=False)

    def _end_utterance(self, utterance_id: str, *, linger: bool) -> None:
        try:
            now = self._clock()
            self._playing.discard(utterance_id)
            self.synthesizer.set_speaking(bool(self._playing))
            delay = self._config.expression_linger_ms if linger else 0
            self.synthesizer.release(utterance_id, now + delay)
        except Exception:  # noqa: BLE001 - 装饰性路径边界
            self.logger.exception(f"发声收尾处理异常（已忽略）: utterance_id={utterance_id}")


__all__ = ["TrackingDriver", "TrackingRuntime"]
