"""
VTS Adapter - VTube Studio 平台适配器
"""

from typing import Dict, Any, Optional
import asyncio

from ..base import PlatformAdapter

try:
    import pyvts
except ImportError:
    pyvts = None

from src.core.utils.logger import get_logger


class VTSAdapter(PlatformAdapter):
    """VTube Studio 适配器

    将抽象参数翻译为 VTS 特定参数。
    """

    # 抽象参数 → VTS 参数映射
    PARAM_TRANSLATION = {
        "smile": "MouthSmile",
        "eye_open": "EyeOpenLeft",
        "mouth_open": "MouthOpen",
        "brow_down": None,
    }

    def __init__(self, config: Dict[str, Any]):
        """
        初始化 VTS 适配器

        Args:
            config: 配置字典，需包含：
                - plugin_name: 插件名称
                - developer: 开发者名称
                - authentication_token_path: 认证令牌路径
                - vts_host: VTS 主机地址
                - vts_port: VTS 端口
        """
        super().__init__("vts", config)
        self.logger = get_logger("VTSAdapter")

        if pyvts is None:
            raise ImportError("pyvts is required for VTSAdapter")

        self.vts = None
        self._auth_token = None
        self._connection_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def translate_params(self, abstract_params: Dict[str, float]) -> Dict[str, float]:
        """翻译为 VTS 参数

        Args:
            abstract_params: 抽象参数字典

        Returns:
            VTS 参数字典
        """
        vts_params = {}
        for name, value in abstract_params.items():
            if name in self.PARAM_TRANSLATION:
                vts_param = self.PARAM_TRANSLATION[name]
                if vts_param:
                    vts_params[vts_param] = value
                    # eye_open 同时设置左右眼
                    if name == "eye_open":
                        vts_params["EyeOpenRight"] = value
        return vts_params

    async def connect(self) -> bool:
        """连接到 VTube Studio"""
        try:
            plugin_info = {
                "plugin_name": self.config.get("plugin_name", "Amaidesu_VTS_Adapter"),
                "developer": self.config.get("developer", "Amaidesu"),
                "authentication_token_path": self.config.get("authentication_token_path", "./vts_token.txt"),
            }
            vts_api_info = {
                "host": self.config.get("vts_host", "localhost"),
                "port": self.config.get("vts_port", 8001),
                "name": self.config.get("vts_api_name", "VTubeStudioPublicAPI"),
                "version": self.config.get("vts_api_version", "1.0"),
            }

            self.vts = pyvts.vts(plugin_info=plugin_info, vts_api_info=vts_api_info)
            await self.vts.connect()
            await self.vts.request_authenticate_token()
            authenticated = await self.vts.request_authenticate()

            if authenticated:
                self._is_connected = True
                self.logger.info("VTSAdapter 连接成功")
                return True
            else:
                self.logger.error("VTSAdapter 认证失败")
                return False

        except Exception as e:
            self.logger.error(f"VTSAdapter 连接失败: {e}", exc_info=True)
            return False

    async def disconnect(self) -> bool:
        """断开 VTS 连接"""
        try:
            if self.vts:
                await self.vts.close()
                self._is_connected = False
                self.logger.info("VTSAdapter 已断开")
                return True
        except Exception as e:
            self.logger.error(f"VTSAdapter 断开失败: {e}", exc_info=True)
            return False
        return False

    async def set_parameters(self, params: Dict[str, float]) -> bool:
        """设置表情参数

        Args:
            params: 抽象参数字典（会被自动翻译为 VTS 参数）

        Returns:
            是否设置成功
        """
        if not self._is_connected or not self.vts:
            self.logger.warning("VTSAdapter 未连接")
            return False

        try:
            # 翻译抽象参数为 VTS 参数
            vts_params = self.translate_params(params)

            # 批量设置参数
            for param_name, value in vts_params.items():
                response = await self.vts.request(self.vts.vts_request.requestSetParameterValue(param_name, value))
                if not response or response.get("messageType") != "InjectParameterDataResponse":
                    self.logger.warning(f"设置参数 {param_name} 失败")
                    return False

            return True

        except Exception as e:
            self.logger.error(f"设置参数失败: {e}", exc_info=True)
            return False
