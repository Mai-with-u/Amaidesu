"""Warudo 口型渲染器：平台无关口型信号 → Warudo Vowel 通道

元音成分 A/I/U/E/O 写入嘴部 blendshape 状态件的 ``VowelA~O`` 通道
（经监控循环推送到 Warudo）；张嘴量参与开口元音（A/O）的权重，与
Warudo 原生音频口型互斥（启用本渲染器时应在 Warudo 侧关闭原生口型）。
"""

from typing import Any, Callable

from src.modules.avatar.lipsync import LipSyncRenderer, MouthSignal
from src.modules.logging import get_logger

# 开口元音：张嘴量对这些通道加权
_OPEN_VOWELS = ("A", "O")


class WarudoLipSyncRenderer(LipSyncRenderer):
    """Warudo 口型渲染器：``MouthSignal`` → 嘴部状态件 ``VowelA~O`` 通道"""

    def __init__(
        self,
        *,
        logger_name: str = "avatar.lipsync.WarudoRenderer",
        set_mouth_channel: Callable[[str, float], Any],
    ) -> None:
        """Args:
        set_mouth_channel: 嘴部通道写入回调（provider 的 mouth_state.set_first_layer
            单键写法包装——先清零再设目标通道，避免旧元音残留）。
        """
        self.logger = get_logger(logger_name)
        self._set_mouth_channel = set_mouth_channel
        self._last_active: str = ""

    async def on_mouth_signal(self, signal: MouthSignal) -> None:
        """一帧口型信号 → 元音通道写入。"""
        # 取最强元音通道（Warudo 嘴部状态件是单键写法：先清零再设键）
        vowels = signal.vowels
        active = max(vowels, key=lambda k: vowels.get(k, 0.0)) if vowels else "A"
        weight = vowels.get(active, 0.0)

        # 开口元音受张嘴量调制；闭嘴信号（张嘴≈0）直接清通道
        if signal.mouth_open <= 0.01 and weight <= 0.01:
            if self._last_active:
                self._set_mouth_channel("", 0.0)
                self._last_active = ""
            return

        if active in _OPEN_VOWELS:
            weight = weight * max(0.3, signal.mouth_open)
        if weight <= 0.01:
            if self._last_active:
                self._set_mouth_channel("", 0.0)
                self._last_active = ""
            return

        self._set_mouth_channel(f"Vowel{active}", min(1.0, weight))
        self._last_active = f"Vowel{active}"

    async def on_session_end(self) -> None:
        """会话结束：清空元音通道（嘴交回平台待机）。"""
        if self._last_active:
            self._set_mouth_channel("", 0.0)
            self._last_active = ""


__all__ = ["WarudoLipSyncRenderer"]
