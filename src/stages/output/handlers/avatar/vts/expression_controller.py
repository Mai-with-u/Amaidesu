"""
ExpressionController - VTS 表情/参数控制器

负责 VTS 表情参数读写（微笑、眨眼、参数设置/获取）。
"""

from typing import Any, Callable, Coroutine, Optional

from src.modules.logging import get_logger

PARAM_MOUTH_SMILE = "MouthSmile"
PARAM_MOUTH_OPEN = "MouthOpen"
PARAM_EYE_OPEN_LEFT = "EyeOpenLeft"
PARAM_EYE_OPEN_RIGHT = "EyeOpenRight"


class ExpressionController:
    """VTS 表情/参数控制器"""

    def __init__(
        self,
        *,
        logger_name: str,
        is_connected: Callable[[], bool],
        vts_request: Callable[..., Coroutine[Any, Any, Any]],
    ):
        self.logger = get_logger(logger_name)
        self._is_connected = is_connected
        self._vts_request = vts_request

    async def smile(self, value: float = 1) -> bool:
        if not self._is_connected():
            return False
        try:
            return await self.set_parameter(PARAM_MOUTH_SMILE, value)
        except Exception as e:
            self.logger.error(f"设置微笑参数失败: {e}")
            return False

    async def close_eyes(self) -> bool:
        if not self._is_connected():
            return False
        try:
            await self.set_parameter(PARAM_EYE_OPEN_LEFT, 0.0)
            await self.set_parameter(PARAM_EYE_OPEN_RIGHT, 0.0)
            self.logger.debug("闭眼成功")
            return True
        except Exception as e:
            self.logger.error(f"闭眼失败: {e}")
            return False

    async def open_eyes(self) -> bool:
        if not self._is_connected():
            return False
        try:
            await self.set_parameter(PARAM_EYE_OPEN_LEFT, 1.0)
            await self.set_parameter(PARAM_EYE_OPEN_RIGHT, 1.0)
            self.logger.debug("睁眼成功")
            return True
        except Exception as e:
            self.logger.error(f"睁眼失败: {e}")
            return False

    async def set_parameter(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        if not self._is_connected():
            self.logger.warning(f"VTS未连接，无法设置参数: {parameter_name} = {value}")
            return False
        try:
            response = await self._vts_request(
                self._vts_request.vts_request.requestSetParameterValue(parameter_name, value, weight)
            )
            if response and response.get("messageType") == "InjectParameterDataResponse":
                self.logger.debug(f"VTS参数 {parameter_name} 已设置为: {value}")
                return True
            self.logger.warning(f"设置VTS参数失败: {parameter_name}: {response}")
            return False
        except Exception as e:
            self.logger.error(f"设置VTS参数异常: {parameter_name}: {e}")
            return False

    async def get_parameter(self, parameter_name: str) -> Optional[float]:
        if not self._is_connected():
            return None
        try:
            response = await self._vts_request(self._vts_request.vts_request.requestParameterValue(parameter_name))
            if response and response.get("messageType") == "ParameterValueResponse":
                return response.get("data", {}).get("value", 0.0)
            self.logger.warning(f"获取VTS参数失败: {parameter_name}: {response}")
            return None
        except Exception as e:
            self.logger.error(f"获取VTS参数异常: {parameter_name}: {e}")
            return None
