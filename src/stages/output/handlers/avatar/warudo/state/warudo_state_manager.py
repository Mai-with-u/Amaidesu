"""Warudo 状态管理系统

包含所有状态类和状态管理器，用于管理 Warudo 模型的面部表情状态。
"""

import asyncio
import logging
from typing import Dict, Optional

# ==================== 常量定义 ====================

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

ALL_EYEBROW_STATE = {
    "eyebrow_happy_weak": 0.0,
    "eyebrow_happy_strong": 0.0,
    "eyebrow_angry_weak": 0.0,
    "eyebrow_angry_strong": 0.0,
    "eyebrow_sad_weak": 0.0,
    "eyebrow_sad_strong": 0.0,
}

ALL_EYE_STATE = {
    "eye_happy_strong": 0.0,
    "eye_close": 0.0,
}

ALL_PUPIL_STATE = {
    "eye_shift_left": 0.0,
    "eye_shift_right": 0.0,
    "eye_shift_up": 0.0,
    "eye_shift_down": 0.0,
}

ALL_MOUTH_STATE = {
    "mouth_happy_strong": 0.0,
    "mouth_angry_weak": 0.0,
    "mouth_sad_weak": 0.0,
    "mouth_smlie_2": 0.0,
    "mouth_smlie_3": 0.0,
    "mouth_smlie_teeth": 0.0,
    "VowelA": 0.0,
    "VowelI": 0.0,
    "VowelU": 0.0,
    "VowelE": 0.0,
    "VowelO": 0.0,
}


# ==================== 状态类 ====================


class SightState:
    """视线状态管理"""

    def __init__(self):
        self.first_layer = {
            "camera": 0.0,
            "danmu": 0.0,
            "phone": 0.0,
        }
        self.changed = True

    async def send_state(self, send_action_callback):
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

    def set_state(self, key: str, intensity: float):
        """设置视线状态

        Args:
            key: 视线类型 (camera/danmu/phone)
            intensity: 强度 (0.0-1.0)
        """
        for k in self.first_layer:
            self.first_layer[k] = 0.0
        self.first_layer[key] = intensity
        self.changed = True


class EyebrowState:
    """眉毛状态管理"""

    def __init__(self):
        self.first_layer = {
            "eyebrow_happy_weak": 0.0,
            "eyebrow_happy_strong": 0.0,
            "eyebrow_angry_weak": 0.0,
            "eyebrow_angry_strong": 0.0,
            "eyebrow_sad_weak": 0.0,
            "eyebrow_sad_strong": 0.0,
        }
        self.changed = True

    async def send_state(self, send_action_callback):
        """发送眉毛状态

        Args:
            send_action_callback: 发送动作的回调函数
        """
        state = self.get_eyebrow_state()
        for k, v in state.items():
            await send_action_callback(k, v)

    def get_eyebrow_state(self) -> Dict[str, float]:
        """获取眉毛状态"""
        result = ALL_EYEBROW_STATE.copy()
        if any(v > 0 for v in self.first_layer.values()):
            for k in result:
                if k in self.first_layer:
                    result[k] = self.first_layer[k]
        return result

    def set_first_layer(self, key: str, weight: float = 1.0):
        """设置第一层状态

        Args:
            key: 眉毛状态类型
            weight: 权重 (0.0-1.0)
        """
        for k in self.first_layer:
            self.first_layer[k] = 0.0
        self.first_layer[key] = weight
        self.changed = True


class EyeState:
    """眼睛状态管理"""

    def __init__(self):
        self.first_layer = {
            "eye_happy_strong": 0.0,
            "eye_close": 0.0,
        }
        self.is_blinking = False
        self.changed = True

    def can_blink(self) -> bool:
        """检查是否可以眨眼

        Returns:
            bool: 如果当前没有其他眼部动作，返回 True
        """
        # 只要 eye_happy_strong 或 eye_close 有值（非0），就不能眨眼
        if self.first_layer.get("eye_happy_strong", 0.0) > 0 or self.first_layer.get("eye_close", 0.0) > 0:
            return False
        return True

    async def send_state(self, send_action_callback):
        """发送眼睛状态

        Args:
            send_action_callback: 发送动作的回调函数
        """
        state = self.get_eye_state()
        for k, v in state.items():
            await send_action_callback(k, v)

    def set_blinking(self, is_blinking: bool):
        """设置眨眼状态

        Args:
            is_blinking: 是否正在眨眼
        """
        self.is_blinking = is_blinking
        self.changed = True

    def set_first_layer(self, key: str, weight: float = 1.0):
        """设置第一层状态

        Args:
            key: 眼睛状态类型
            weight: 权重 (0.0-1.0)
        """
        if not key:
            for k in self.first_layer:
                self.first_layer[k] = 0.0
            self.changed = True
            return

        for k in self.first_layer:
            self.first_layer[k] = 0.0
        self.first_layer[key] = weight
        self.changed = True

    def get_eye_state(self) -> Dict[str, float]:
        """获取眼睛状态"""
        result = ALL_EYE_STATE.copy()
        if self.is_blinking:
            result["eye_close"] = 1.0
            return result

        if any(v > 0 for v in self.first_layer.values()):
            for k in result:
                if k in self.first_layer:
                    result[k] = self.first_layer[k]
        return result


class PupilState:
    """瞳孔状态管理"""

    def __init__(self):
        self.first_layer = {
            "eye_shift_left": 0.0,
            "eye_shift_right": 0.0,
            "eye_shift_up": 0.0,
            "eye_shift_down": 0.0,
        }
        self.changed = True

    async def send_state(self, send_action_callback):
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

    def set_state(self, direction: str, intensity: float):
        """设置瞳孔状态

        Args:
            direction: 方向 (left/right/up/down)
            intensity: 强度 (0.0-1.0)
        """
        self.first_layer[direction] = intensity
        self.changed = True


class MouthState:
    """嘴巴状态管理，获取状态时，如果有口型，会优先只返回口型状态"""

    def __init__(self):
        self.first_layer = {
            "mouth_happy_strong": 0.0,
            "mouth_angry_weak": 0.0,
            "mouth_sad_weak": 0.0,
            "mouth_smlie_2": 0.0,
            "mouth_smlie_3": 0.0,
            "mouth_smlie_teeth": 0.0,
        }
        self.second_layer = {
            "VowelA": 0.0,
            "VowelI": 0.0,
            "VowelU": 0.0,
            "VowelE": 0.0,
            "VowelO": 0.0,
        }
        self.changed = True

    async def send_state(self, send_action_callback):
        """发送嘴巴状态

        Args:
            send_action_callback: 发送动作的回调函数
        """
        state = self.get_mouth_state()
        for k, v in state.items():
            await send_action_callback(k, v)

    def set_first_layer(self, key: str, weight: float = 1.0):
        """设置第一层状态

        Args:
            key: 嘴巴状态类型
            weight: 权重 (0.0-1.0)
        """
        for k in self.first_layer:
            self.first_layer[k] = 0.0
        self.first_layer[key] = weight
        self.changed = True

    def get_mouth_state(self) -> Dict[str, float]:
        """获取嘴巴状态"""
        result = ALL_MOUTH_STATE.copy()
        if any(v > 0 for v in self.second_layer.values()):
            # 优先返回口型状态
            for k in result:
                if k in self.second_layer:
                    result[k] = self.second_layer[k]
        else:
            # 返回表情状态
            for k in result:
                if k in self.first_layer:
                    result[k] = self.first_layer[k]
                elif k in self.second_layer:
                    result[k] = self.second_layer[k]
        return result

    def set_vowel_state(self, lip_sync_states: Dict[str, float]):
        """设置口型状态

        Args:
            lip_sync_states: 字典，格式为 {"VowelA": 0.8, "VowelI": 0.3, ...}
        """
        for vowel_key, intensity in lip_sync_states.items():
            if vowel_key in self.second_layer:
                # 限制强度值范围
                intensity = max(0.0, min(1.0, float(intensity)))
                self.second_layer[vowel_key] = intensity
                self.changed = True


# ==================== 状态管理器 ====================


class WarudoStateManager:
    """Warudo 模型状态管理器

    管理所有面部状态，并定期检查并发送变化到 Warudo。
    """

    def __init__(self, logger: logging.Logger, send_action_callback):
        """初始化状态管理器

        Args:
            logger: 日志记录器
            send_action_callback: 发送动作的回调函数，签名为 async def send_action(name: str, value: Any)
        """
        self.logger = logger
        self.send_action_callback = send_action_callback

        # 初始化所有状态
        self.eye_state = EyeState()
        self.pupil_state = PupilState()
        self.mouth_state = MouthState()
        self.eyebrow_state = EyebrowState()
        self.sight_state = SightState()

        # 监控任务
        self._monitoring_task: Optional[asyncio.Task] = None
        self._is_monitoring = False

        # 需要使用 debug 日志级别的动作列表
        self.debug_actions = {
            "eye_shift_left",
            "eye_shift_right",
            "eye_close",
        }

        self.logger.info("Warudo 状态管理器已初始化")

    def start_monitoring(self):
        """启动状态监控循环

        启动一个后台任务，每 0.1 秒检查一次所有状态，如果状态有变化则发送到 Warudo。
        """
        if self._is_monitoring:
            self.logger.warning("状态监控已在运行中")
            return

        self._is_monitoring = True
        self._monitoring_task = asyncio.create_task(self._check_and_send_state_loop())
        self.logger.info("状态监控已启动")

    def stop_monitoring(self):
        """停止状态监控循环"""
        if not self._is_monitoring:
            return

        self._is_monitoring = False

        if self._monitoring_task:
            self._monitoring_task.cancel()
            self._monitoring_task = None

        self.logger.info("状态监控已停止")

    async def _check_and_send_state_loop(self):
        """状态检查和发送循环"""
        try:
            while self._is_monitoring:
                await asyncio.sleep(0.1)

                # 检查并发送所有状态
                await self._send_changed_states()

        except asyncio.CancelledError:
            self.logger.debug("状态监控循环被取消")
        except Exception as e:
            self.logger.error(f"状态监控循环出错: {e}", exc_info=True)

    async def _send_changed_states(self):
        """发送所有变化的状态"""
        # 嘴巴状态
        if self.mouth_state.changed:
            await self.mouth_state.send_state(self.send_action_callback)
            self.logger.debug("发送嘴巴状态")
            self.mouth_state.changed = False

        # 眼睛状态
        if self.eye_state.changed:
            await self.eye_state.send_state(self.send_action_callback)
            self.logger.debug("发送眼睛状态")
            self.eye_state.changed = False

        # 瞳孔状态
        if self.pupil_state.changed:
            await self.pupil_state.send_state(self.send_action_callback)
            self.logger.debug("发送瞳孔状态")
            self.pupil_state.changed = False

        # 眉毛状态
        if self.eyebrow_state.changed:
            await self.eyebrow_state.send_state(self.send_action_callback)
            self.logger.debug("发送眉毛状态")
            self.eyebrow_state.changed = False

        # 视线状态
        if self.sight_state.changed:
            await self.sight_state.send_state(self.send_action_callback)
            self.logger.debug("发送视线状态")
            self.sight_state.changed = False
