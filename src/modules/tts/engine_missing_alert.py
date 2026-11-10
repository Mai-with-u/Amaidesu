"""TTS 引擎没构造出来时的运营告警

配置里开启了语音、引擎却没构造出来 —— 这场直播会全程只有字幕没有声音，
而字幕照常出字，运营者从直播画面上看不出来。WebUI 里 TTS 条目显示"未启用"
的灰色档说的是"配置确实关闭"，与此不是一回事：这里是配置说开、实际没开，
属于配置与实际不一致，需要显性化。

这类矛盾态发成 ``core.error``，进运营告警通道（WebUI 通知与告警收件箱）。
按该通道的约定，同来源同类告警 5 分钟窗内只发第一条（节流判定用注入时钟）。
"""

from __future__ import annotations

from typing import Callable, Optional, TYPE_CHECKING

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.core import CoreErrorPayload
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms

from .protocol import TTSProvider

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus


# 同类运行故障告警的最小重发间隔（毫秒）：5 分钟窗口内只发布第一条 core.error
ERROR_ALERT_THROTTLE_MS = 5 * 60 * 1000


class TtsEngineMissingAlert:
    """TTS 引擎缺席（配置说开、引擎没构造出来）的告警发布器。

    只报装配期矛盾：配置启用、引擎没构造成功（构造抛异常、或构造出的对象
    不满足引擎契约，都会让引擎缺席）。有意关闭（配置 enabled=false）不是
    矛盾态，不报；引擎构造成功也不报。

    引擎构造成功、运行中才崩（如语音服务中途断连）属于运行期故障，由
    ``tts.utterance.failed`` 事件与队列熔断告警负责，不归这里。
    """

    def __init__(self, event_bus: Optional["EventBus"] = None, clock: Optional[Callable[[], int]] = None) -> None:
        self._logger = get_logger("TTSEngineMissingAlert")
        self._event_bus = event_bus
        self._clock = clock or now_ms
        # 上一次发布缺席告警的时刻（毫秒）；0 = 从未发布过
        self._last_alert_ms: int = 0

    async def report_missing_engine(
        self,
        *,
        config_enabled: bool,
        engine: Optional[TTSProvider],
        provider: str,
    ) -> None:
        """配置启用而引擎没构造出来时发一条 core.error；其余情况不发。

        Args:
            config_enabled: 配置里的 TTS 开关是否为开启。
            engine: 构造出来的 TTS 引擎；``None`` 表示引擎缺席。
            provider: 配置选中的引擎名（写进载荷，便于定位是哪一路没起来）。
        """
        if not config_enabled or engine is not None:
            return

        now = self._clock()
        if now - self._last_alert_ms < ERROR_ALERT_THROTTLE_MS:
            self._logger.debug("TTS 引擎缺席告警处于节流窗口内，跳过发布")
            return
        self._last_alert_ms = now

        message = "配置开启了语音（TTS），但引擎没有构造成功，本场直播不会出声；请检查配置或重启"
        self._logger.error(f"发布 TTS 引擎缺席告警: {message}")
        if self._event_bus is None:
            return
        try:
            await self._event_bus.emit(
                CoreEvents.CORE_ERROR,
                CoreErrorPayload(message=message, data={"source": "tts", "provider": provider}),
                source="tts_assembly",
            )
        except Exception as exc:  # noqa: BLE001 - 告警是旁路，不阻断启动
            self._logger.warning(f"发布 TTS 引擎缺席告警失败（已忽略）: err={exc}")


__all__ = ["ERROR_ALERT_THROTTLE_MS", "TtsEngineMissingAlert"]
