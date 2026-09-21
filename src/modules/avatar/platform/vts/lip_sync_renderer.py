"""VTS 口型渲染器：平台无关口型信号 → VTS 参数

分工边界：口型只写"嘴"（``MouthOpen``）；表情（``MouthSmile`` /
``EyeOpen`` 等的说话保持与静止淡出）也归本渲染器维护——它是 VTS 侧
说话期间面部参数的唯一写入者，与情绪工具共享 ``ExpressionController``
（写入经同一把 VTS 请求锁串行化）。

争用协调：情绪渲染（``set_expression``）写整组表情参数、口型渲染写
``MouthOpen`` 与表情基线——两者对表情参数的写入存在先后覆盖关系，
属可接受的装饰性抖动（口型是装饰性路径，任何异常不影响播放与决策）。
"""

from typing import Any, Callable, Coroutine, Dict, Optional

from src.modules.avatar.lipsync import LipSyncRenderer, MouthSignal
from src.modules.logging import get_logger


class VtsLipSyncRenderer(LipSyncRenderer):
    """VTS 口型渲染器：``MouthSignal`` → ``MouthOpen`` 参数 + 表情维护"""

    def __init__(
        self,
        *,
        logger_name: str = "avatar.lipsync.VtsRenderer",
        set_parameter: Callable[..., Coroutine[Any, Any, bool]],
        mouth_open_param: str = "MouthOpen",
        base_expressions: Optional[Dict[str, float]] = None,
    ) -> None:
        """Args:
        set_parameter: VTS 参数写入回调（provider 的表达式控制器代理）。
        mouth_open_param: 张嘴参数名（VTS 标准 ``MouthOpen``）。
        base_expressions: 说话时保持的基础表情（如常驻微笑基线）；
            说话淡出到静止值（EyeOpen 保持睁眼等）由 rest 值表达。
        """
        self.logger = get_logger(logger_name)
        self._set_parameter = set_parameter
        self._mouth_open_param = mouth_open_param

        # 说话时保持的基础表情（过滤 MouthOpen——张嘴归口型信号管）
        self._base_expressions: Dict[str, float] = {
            name: float(value) for name, value in (base_expressions or {}).items() if name != mouth_open_param
        }
        # 各表情参数当前值从 0 起步（平台参数实际未写；首次说话时写入基线，
        # 结束时收回 0 = 交回 idle/情绪渲染的基线管理）
        self._expression_values: Dict[str, float] = {name: 0.0 for name in self._base_expressions}
        self._expression_active = False
        self.current_mouth_open: float = 0.0

    async def on_mouth_signal(self, signal: MouthSignal) -> None:
        """一帧口型信号 → ``MouthOpen`` 写入 + 表情说话保持/淡出。"""
        # 嘴：直接跟随信号的张开量（分析器已平滑，此处直写）
        if abs(signal.mouth_open - self.current_mouth_open) >= 0.005:
            success = await self._set_parameter(self._mouth_open_param, signal.mouth_open, 1)
            self.current_mouth_open = signal.mouth_open
            self.logger.debug(f"VTS MouthOpen={signal.mouth_open:.3f} (volume={signal.volume:.3f}) success={success}")

        # 表情：说话（音量足）时保持基线，静音/结束时淡出到静止值
        await self._maintain_expressions(signal.volume)

    async def on_session_end(self) -> None:
        """会话结束：嘴归零 + 表情回静止值（基线表情的静止值由提供方语义决定）。"""
        if abs(self.current_mouth_open) > 0.0:
            await self._set_parameter(self._mouth_open_param, 0.0, 1)
            self.current_mouth_open = 0.0
        for name in list(self._expression_values):
            self._expression_values[name] = 0.0
        self._expression_active = False

    async def _maintain_expressions(self, volume: float) -> None:
        """说话时维持基础表情（一步到位写入）；静音时不主动写（交回情绪面）。"""
        if not self._base_expressions:
            return
        speaking = volume >= 0.01
        if speaking and not self._expression_active:
            for name, target in self._base_expressions.items():
                if abs(self._expression_values.get(name, 0.0) - target) >= 0.005:
                    await self._set_parameter(name, target, 1)
                self._expression_values[name] = target
            self._expression_active = True
        elif not speaking and self._expression_active:
            # 收回说话基线（0 = 交回 idle/情绪渲染的基线管理）
            for name in list(self._base_expressions):
                await self._set_parameter(name, 0.0, 1)
                self._expression_values[name] = 0.0
            self._expression_active = False


__all__ = ["VtsLipSyncRenderer"]
