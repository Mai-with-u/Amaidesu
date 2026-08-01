"""
ExpressionController - VTS 表情/参数控制器

负责 VTS 表情参数读写（微笑、眨眼、参数设置/获取）。
"""

from typing import Any, Callable, Coroutine, Dict, List, Optional

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

    async def set_parameter(
        self, parameter_name: str, value: float, weight: float = 1, *, silent: bool = False
    ) -> bool:
        if not self._is_connected():
            if not silent:
                self.logger.warning(f"VTS未连接，无法设置参数: {parameter_name} = {value}")
            return False
        try:
            response = await self._vts_request(
                self._vts_request.vts_request.requestSetParameterValue(parameter_name, value, weight)
            )
            if response and response.get("messageType") == "InjectParameterDataResponse":
                if not silent:
                    self.logger.debug(f"VTS参数 {parameter_name} 已设置为: {value}")
                return True
            if not silent:
                self.logger.warning(f"设置VTS参数失败: {parameter_name}: {response}")
            return False
        except Exception as e:
            if not silent:
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

    async def list_tracking_parameters(self) -> List[str]:
        """获取 VTS 当前可用参数名列表（含自定义与跟踪参数）。

        用于 idle 动画等场景自动回退到可用参数名。
        注意：VTS 对该请求的响应 messageType 为 InputParameterListResponse，
        参数分布在 data.defaultParameters / data.customParameters 两个列表中。
        """
        if not self._is_connected():
            return []
        try:
            response = await self._vts_request(self._vts_request.vts_request.requestTrackingParameterList())
            if response and response.get("messageType") in (
                "InputParameterListResponse",
                "TrackingParameterListResponse",
            ):
                data = response.get("data", {})
                # 新版响应：defaultParameters + customParameters；旧版：model_parameters
                params = (
                    data.get("defaultParameters", [])
                    + data.get("customParameters", [])
                    + data.get("model_parameters", [])
                )
                return [str(p.get("name")) for p in params if p.get("name")]
            self.logger.warning(f"获取VTS参数列表失败: {response}")
            return []
        except Exception as e:
            self.logger.error(f"获取VTS参数列表异常: {e}")
            return []

    async def set_multi_parameter(
        self,
        parameter_values: Dict[str, float],
        weight: float = 1,
        *,
        silent: bool = False,
    ) -> bool:
        """一次性设置多个 VTS 参数值，减少 API 往返。

        Args:
            parameter_values: 参数名 -> 目标值。
            weight: 与 VTS 自身跟踪/其他输入的混合权重；默认 1 表示完全覆盖。
            silent: 失败时是否静默（避免刷屏）。

        Returns:
            是否全部设置成功。
        """
        if not parameter_values:
            return True
        if not self._is_connected():
            if not silent:
                self.logger.warning("VTS未连接，无法批量设置参数")
            return False
        names = list(parameter_values.keys())
        values = list(parameter_values.values())
        try:
            response = await self._vts_request(
                self._vts_request.vts_request.requestSetMultiParameterValue(names, values, weight)
            )
            if response and response.get("messageType") == "InjectParameterDataResponse":
                self.logger.debug(f"VTS 多参数已设置: {names}")
                return True
            if not silent:
                self.logger.warning(f"批量设置VTS参数失败: {names}: {response}")
            return False
        except Exception as e:
            if not silent:
                self.logger.error(f"批量设置VTS参数异常: {names}: {e}")
            return False
