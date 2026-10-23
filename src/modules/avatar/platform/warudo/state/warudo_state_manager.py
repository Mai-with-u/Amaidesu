"""Warudo 状态管理系统

包含所有状态类和状态管理器，用于管理 Warudo 模型的面部表情状态。

表情键的全集由表情档案决定（见 ``expression_profile``），本模块不认任何
具体模型的形状名；眨眼、移眼、口型元音是固定协议键。
"""

import asyncio
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional

from src.modules.avatar.platform.warudo.expression_profile import BLINK_KEY, VOWEL_KEYS
from src.modules.logging import ModuleLogger

# ==================== 常量定义 ====================

# 状态检查循环间隔（秒）：越高 CPU 占用越低，但状态同步延迟越大
_STATE_CHECK_INTERVAL_S = 0.1

ALL_SIGHT_STATE = {
    "camera": 0.0,
    "danmu": 0.0,
    "phone": 0.0,
}

name_transfer_dict = {
    "camera": "漫游",
    "danmu": "danmu",
    "phone": "phone",
}

ALL_PUPIL_STATE = {
    "eye_shift_left": 0.0,
    "eye_shift_right": 0.0,
    "eye_shift_up": 0.0,
    "eye_shift_down": 0.0,
}


# ==================== 状态类 ====================


class SightState:
    """视线状态管理"""

    def __init__(self) -> None:
        self.first_layer = {
            "camera": 0.0,
            "danmu": 0.0,
            "phone": 0.0,
        }
        self.changed = True

    async def send_state(self, send_action_callback: Callable[[str, Any], Awaitable[None]]) -> None:
        """发送视线状态

        Args:
            send_action_callback: 发送动作的回调函数
        """
        state = self.get_sight_state()
        for k, v in state.items():
            if v > 0:
                await send_action_callback("sight", name_transfer_dict[k])

    def get_sight_state(self) -> Dict[str, float]:
        """获取视线状态"""
        result = ALL_SIGHT_STATE.copy()
        if any(v > 0 for v in self.first_layer.values()):
            for k in result:
                if k in self.first_layer:
                    result[k] = self.first_layer[k]
        return result

    def set_state(self, key: str, intensity: float) -> None:
        """设置视线状态

        Args:
            key: 视线类型 (camera/danmu/phone)
            intensity: 强度 (0.0-1.0)
        """
        for k in self.first_layer:
            self.first_layer[k] = 0.0
        self.first_layer[key] = intensity
        self.changed = True


class ExpressionState:
    """情绪表情状态：当前情绪写入的表情键与权重。

    换情绪时整表替换并整表下发（不在新情绪里的键发 0），旧表情不残留；
    说话口型生效期间，嘴部表情键暂时按 0 下发，停嘴后恢复原权重。
    """

    def __init__(self, keys: Iterable[str] = (), mouth_keys: Iterable[str] = ()) -> None:
        # 眨眼键由眼部状态单独合并下发，这里排除，避免两路同时写同一个键
        self.weights: Dict[str, float] = {key: 0.0 for key in keys if key != BLINK_KEY}
        self.mouth_keys = frozenset(mouth_keys)
        self.speaking = False
        self.changed = True

    def set_weights(self, weights: Dict[str, float]) -> None:
        """整表替换当前表情：列出的键取新权重，其余键清零。"""
        for key in self.weights:
            self.weights[key] = 0.0
        for key, weight in weights.items():
            if key != BLINK_KEY:
                self.weights[key] = max(0.0, min(1.0, float(weight)))
        self.changed = True

    def set_speaking(self, speaking: bool) -> None:
        """口型开始/结束：只有档案配置了嘴部表情键时才需要重发。"""
        if speaking == self.speaking:
            return
        self.speaking = speaking
        if self.mouth_keys:
            self.changed = True

    def get_state(self) -> Dict[str, float]:
        """待下发的表情权重（说话时嘴部表情键按 0 下发）"""
        if not self.speaking:
            return dict(self.weights)
        return {key: (0.0 if key in self.mouth_keys else weight) for key, weight in self.weights.items()}

    async def send_state(self, send_action_callback: Callable[[str, Any], Awaitable[None]]) -> None:
        """整表下发表情键（含零值，保证旧值被清掉）"""
        for key, weight in self.get_state().items():
            await send_action_callback(key, weight)


class EyeState:
    """眼部状态：眨眼与情绪对眨眼键的写入合并成一个下发值。

    情绪写了眨眼键（如困倦半闭眼）或档案指定的暂停眨眼键（如笑眼）生效时，
    自动眨眼暂停，避免眨眼把情绪眼形覆盖掉。
    """

    def __init__(self) -> None:
        self.expression_weight = 0.0
        self.blink_blocked = False
        self.is_blinking = False
        self.changed = True

    def can_blink(self) -> bool:
        """检查是否可以眨眼

        Returns:
            bool: 情绪没有占用眼部时返回 True
        """
        return not self.blink_blocked and self.expression_weight <= 0

    def set_expression(self, weight: float, blink_blocked: bool) -> None:
        """情绪写入眨眼键的权重，以及当前情绪是否暂停眨眼"""
        self.expression_weight = max(0.0, min(1.0, float(weight)))
        self.blink_blocked = blink_blocked
        self.changed = True

    def set_blinking(self, is_blinking: bool) -> None:
        """设置眨眼状态

        Args:
            is_blinking: 是否正在眨眼
        """
        self.is_blinking = is_blinking
        self.changed = True

    def get_eye_state(self) -> Dict[str, float]:
        """获取眼睛状态：眨眼中闭满，否则取情绪写入的权重"""
        return {BLINK_KEY: 1.0 if self.is_blinking else self.expression_weight}

    async def send_state(self, send_action_callback: Callable[[str, Any], Awaitable[None]]) -> None:
        """发送眼睛状态

        Args:
            send_action_callback: 发送动作的回调函数
        """
        for k, v in self.get_eye_state().items():
            await send_action_callback(k, v)


class PupilState:
    """瞳孔状态管理"""

    def __init__(self) -> None:
        self.first_layer = {
            "eye_shift_left": 0.0,
            "eye_shift_right": 0.0,
            "eye_shift_up": 0.0,
            "eye_shift_down": 0.0,
        }
        self.changed = True

    async def send_state(self, send_action_callback: Callable[[str, Any], Awaitable[None]]) -> None:
        """发送瞳孔状态

        Args:
            send_action_callback: 发送动作的回调函数
        """
        state = self.get_pupil_state()
        for k, v in state.items():
            await send_action_callback(k, v)

    def get_pupil_state(self) -> Dict[str, float]:
        """获取瞳孔状态"""
        result = ALL_PUPIL_STATE.copy()
        if any(v > 0 for v in self.first_layer.values()):
            for k in result:
                if k in self.first_layer:
                    result[k] = self.first_layer[k]
        return result

    def set_state(self, direction: str, intensity: float) -> None:
        """设置瞳孔状态

        Args:
            direction: 方向 (left/right/up/down)
            intensity: 强度 (0.0-1.0)
        """
        self.first_layer[direction] = intensity
        self.changed = True


class VowelState:
    """口型元音状态：同一时刻只保留一个元音键（口型渲染器取最强元音）"""

    def __init__(self) -> None:
        self.weights: Dict[str, float] = {key: 0.0 for key in VOWEL_KEYS}
        self.changed = True

    def set_vowel(self, key: str, weight: float) -> None:
        """写入一个元音并清零其余；空键 = 全部清零（停嘴）"""
        for existing in self.weights:
            self.weights[existing] = 0.0
        if key in self.weights:
            self.weights[key] = max(0.0, min(1.0, float(weight)))
        self.changed = True

    @property
    def active(self) -> bool:
        """是否有元音正在张嘴"""
        return any(v > 0 for v in self.weights.values())

    async def send_state(self, send_action_callback: Callable[[str, Any], Awaitable[None]]) -> None:
        """整表下发元音键（含零值）"""
        for key, weight in self.weights.items():
            await send_action_callback(key, weight)


# ==================== 状态管理器 ====================


class WarudoStateManager:
    """Warudo 模型状态管理器

    管理所有面部状态，并定期检查并发送变化到 Warudo。
    """

    def __init__(
        self,
        logger: ModuleLogger,
        send_action_callback: Callable[[str, Any], Awaitable[None]],
        expression_keys: Iterable[str] = (),
        mouth_keys: Iterable[str] = (),
    ) -> None:
        """初始化状态管理器

        Args:
            logger: 日志记录器
            send_action_callback: 发送动作的回调函数，签名为 async def send_action(name: str, value: Any)
            expression_keys: 表情档案涉及的全部表情键（换情绪时整表重发）
            mouth_keys: 说话口型期间暂时归零的嘴部表情键
        """
        self.logger = logger
        self.send_action_callback = send_action_callback

        # 初始化所有状态
        self.expression_state = ExpressionState(expression_keys, mouth_keys)
        self.eye_state = EyeState()
        self.pupil_state = PupilState()
        self.vowel_state = VowelState()
        self.sight_state = SightState()

        # 监控任务
        self._monitoring_task: Optional[asyncio.Task] = None
        self._is_monitoring = False

        self.logger.info("Warudo 状态管理器已初始化")

    def start_monitoring(self) -> None:
        """启动状态监控循环

        启动一个后台任务，每 0.1 秒检查一次所有状态，如果状态有变化则发送到 Warudo。
        """
        if self._is_monitoring:
            self.logger.warning("状态监控已在运行中")
            return

        self._is_monitoring = True
        self._monitoring_task = asyncio.create_task(self._check_and_send_state_loop())
        self.logger.info("状态监控已启动")

    def stop_monitoring(self) -> None:
        """停止状态监控循环"""
        if not self._is_monitoring:
            return

        self._is_monitoring = False

        if self._monitoring_task:
            self._monitoring_task.cancel()
            self._monitoring_task = None

        self.logger.info("状态监控已停止")

    async def _check_and_send_state_loop(self) -> None:
        """状态检查和发送循环"""
        try:
            while self._is_monitoring:
                await asyncio.sleep(_STATE_CHECK_INTERVAL_S)

                # 检查并发送所有状态
                await self._send_changed_states()

        except asyncio.CancelledError:
            self.logger.debug("状态监控循环被取消")
        except Exception as e:
            self.logger.exception(f"状态监控循环出错: {e}")

    async def _send_changed_states(self) -> None:
        """发送所有变化的状态：口型 → 表情 → 眼 → 瞳孔 → 视线"""
        for label, state in (
            ("口型", self.vowel_state),
            ("表情", self.expression_state),
            ("眼睛", self.eye_state),
            ("瞳孔", self.pupil_state),
            ("视线", self.sight_state),
        ):
            if state.changed:
                await state.send_state(self.send_action_callback)
                self.logger.debug(f"发送{label}状态")
                state.changed = False
