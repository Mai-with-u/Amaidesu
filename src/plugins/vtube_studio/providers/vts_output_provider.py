"""
VTube Studio Output Provider

控制VTube Studio虚拟形象，包括表情、口型同步等。
"""

import asyncio
from typing import Optional, Dict, Any

try:
    import pyvts
except ImportError:
    pyvts = None

from src.core.providers.output_provider import OutputProvider
from src.core.providers.base import RenderParameters
from src.utils.logger import get_logger


class VTSOutputProvider(OutputProvider):
    """
    VTube Studio Output Provider

    负责与VTube Studio的连接和渲染控制。
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)

        # 依赖检查
        if pyvts is None:
            self.logger.error("pyvts library not found. Please install it (`pip install pyvts`).")
            raise ImportError("pyvts is required for VTSOutputProvider")

        # VTS连接
        self.vts = None
        self._is_connected_and_authenticated = False
        self._auth_token = None
        self._connection_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        # 配置
        self.plugin_name = self.config.get("plugin_name", "Amaidesu_VTS_Connector")
        self.developer = self.config.get("developer", "Amaidesu User")
        self.token_path = self.config.get("authentication_token_path", "./vts_token.txt")
        self.vts_host = self.config.get("vts_host")
        self.vts_port = self.config.get("vts_port")

        # 口型同步配置
        lip_sync_config = self.config.get("lip_sync", {})
        self.lip_sync_enabled = lip_sync_config.get("enabled", True)
        self.volume_threshold = lip_sync_config.get("volume_threshold", 0.01)
        self.sample_rate = lip_sync_config.get("sample_rate", 32000)

        # LLM配置
        self.llm_matching_enabled = self.config.get("llm_matching_enabled", True)
        self.llm_api_key = self.config.get("llm_api_key", "")
        self.llm_base_url = self.config.get("llm_base_url", "https://api.siliconflow.cn/v1")
        self.llm_model = self.config.get("llm_model", "deepseek-chat")
        self.llm_temperature = self.config.get("llm_temperature", 0.1)
        self.llm_max_tokens = self.config.get("llm_max_tokens", 100)

        # OpenAI客户端
        self.openai_client = None
        try:
            import openai

            if self.llm_matching_enabled and self.llm_api_key:
                self.openai_client = openai.OpenAI(api_key=self.llm_api_key, base_url=self.llm_base_url)
        except ImportError:
            self.logger.warning("openai library not found. LLM matching disabled.")
            self.llm_matching_enabled = False

        # 热键列表
        self.hotkey_list = []

    async def _setup_internal(self):
        """设置VTS连接"""
        self.logger.info("设置 VTSOutputProvider...")

        # 创建pyvts实例
        plugin_info = {
            "plugin_name": self.plugin_name,
            "developer": self.developer,
            "authentication_token_path": self.token_path,
        }
        vts_api_info = {
            "host": self.config.get("vts_host", "localhost"),
            "port": self.config.get("vts_port", 8001),
            "name": self.config.get("vts_api_name", "VTubeStudioPublicAPI"),
            "version": self.config.get("vts_api_version", "1.0"),
        }

        self.vts = pyvts.vts(plugin_info=plugin_info, vts_api_info=vts_api_info)
        self.logger.info("pyvts instance created")

        # 启动连接任务
        self._connection_task = asyncio.create_task(self._connect_and_auth(), name="VTS_ConnectAuth")

        # 等待连接成功
        await asyncio.sleep(2)

    async def _connect_and_auth(self):
        """连接和认证VTS"""
        try:
            self.logger.info("Connecting to VTube Studio...")
            await self.vts.connect()
            self.logger.info("Connected to VTube Studio WebSocket")

            # 请求认证token
            self.logger.info("Requesting authentication token...")
            await self.vts.request_authenticate_token()

            # 认证
            self.logger.info("Authenticating...")
            authenticated = await self.vts.request_authenticate()

            if authenticated:
                self.logger.info("Successfully authenticated with VTube Studio")
                self._is_connected_and_authenticated = True

                # 获取热键列表
                await self._load_hotkeys()

                # 测试表情
                await self.smile(0)
                await self.close_eyes()
            else:
                self.logger.error("Authentication failed")
                self._is_connected_and_authenticated = False

        except Exception as e:
            self.logger.error(f"Error during VTS connection/authentication: {e}", exc_info=True)
            self._is_connected_and_authenticated = False

    async def _load_hotkeys(self):
        """获取热键列表"""
        try:
            response = await self.vts.request(self.vts.vts_request.requestHotKeyList())
            if response and response.get("data") and "availableHotkeys" in response["data"]:
                self.hotkey_list = response["data"]["availableHotkeys"]
                self.logger.info(f"Loaded {len(self.hotkey_list)} hotkeys")
        except Exception as e:
            self.logger.error(f"Error loading hotkeys: {e}", exc_info=True)

    async def _render_internal(self, parameters: RenderParameters):
        """
        渲染参数

        根据render_type处理不同的渲染请求：
        - "expression": 设置表情
        - "parameter": 设置参数值
        - "hotkey": 触发热键
        - "lip_sync": 口型同步
        """
        if not self._is_connected_and_authenticated:
            self.logger.warning("VTS not connected, skipping render")
            return

        try:
            render_type = parameters.render_type
            content = parameters.content
            metadata = parameters.metadata

            if render_type == "expression":
                # 表情控制
                expression_type = metadata.get("expression_type")
                if expression_type == "smile":
                    await self.smile(content)
                elif expression_type == "close_eyes":
                    await self.close_eyes()
                elif expression_type == "open_eyes":
                    await self.open_eyes()

            elif render_type == "parameter":
                # 参数控制
                param_name = metadata.get("param_name")
                value = float(content)
                weight = metadata.get("weight", 1.0)
                await self.set_parameter_value(param_name, value, weight)

            elif render_type == "hotkey":
                # 触发热键
                await self.trigger_hotkey(content)

            elif render_type == "lip_sync":
                # 口型同步（音频数据）
                pass  # 口型同步由服务接口处理

        except Exception as e:
            self.logger.error(f"Error rendering VTS parameters: {e}", exc_info=True)

    # --- VTS控制方法 ---

    async def trigger_hotkey(self, hotkey_id: str) -> bool:
        """触发热键"""
        if not self._is_connected_and_authenticated or not self.vts:
            return False

        try:
            request_msg = self.vts.vts_request.requestTriggerHotKey(hotkeyID=hotkey_id)
            response = await self.vts.request(request_msg)
            return response and response.get("messageType") == "HotkeyTriggerResponse"
        except Exception as e:
            self.logger.error(f"Error triggering hotkey: {e}", exc_info=True)
            return False

    async def set_parameter_value(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        """设置参数值"""
        if not self._is_connected_and_authenticated or not self.vts:
            return False

        try:
            response = await self.vts.request(
                self.vts.vts_request.requestSetParameterValue(parameter_name, value, weight)
            )
            return response and response.get("messageType") == "InjectParameterDataResponse"
        except Exception as e:
            self.logger.error(f"Error setting parameter: {e}", exc_info=True)
            return False

    async def close_eyes(self) -> bool:
        """闭眼"""
        await self.set_parameter_value("EyeOpenLeft", 0)
        await self.set_parameter_value("EyeOpenRight", 0)
        return True

    async def open_eyes(self) -> bool:
        """睁眼"""
        await self.set_parameter_value("EyeOpenLeft", 1)
        await self.set_parameter_value("EyeOpenRight", 1)
        return True

    async def smile(self, value: float = 1) -> bool:
        """微笑"""
        return await self.set_parameter_value("MouthSmile", value)

    async def _cleanup_internal(self):
        """清理VTS连接"""
        self.logger.info("Cleaning up VTSOutputProvider...")

        # 停止连接任务
        if self._connection_task and not self._connection_task.done():
            self._connection_task.cancel()
            try:
                await asyncio.wait_for(self._connection_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        # 关闭VTS连接
        if self.vts:
            try:
                await self.vts.close()
                self.logger.info("VTS connection closed")
            except Exception as e:
                self.logger.error(f"Error closing VTS connection: {e}", exc_info=True)

        self._is_connected_and_authenticated = False
        self.hotkey_list.clear()

    def get_info(self) -> Dict[str, Any]:
        """获取Provider信息"""
        info = super().get_info()
        info.update(
            {
                "is_connected": self._is_connected_and_authenticated,
                "hotkey_count": len(self.hotkey_list),
            }
        )
        return info
