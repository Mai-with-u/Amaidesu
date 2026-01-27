# OBS Control Plugin - OBS控制插件（新架构）

import asyncio
from typing import Dict, Any, Optional

from src.core.plugin import Plugin
from src.core.event_bus import EventBus
from src.utils.logger import get_logger

try:
    import obsws_python as obs
except ImportError:
    obs = None


class ObsControlPlugin(Plugin):
    """OBS控制插件（新架构）"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.event_bus = None
        self.logger = get_logger("ObsControlPlugin")

        # OBS配置
        obs_config = config.get("obs", {})
        self.host = obs_config.get("host", "localhost")
        self.port = obs_config.get("port", 4455)
        self.password = obs_config.get("password", "")
        self.text_source_name = obs_config.get("text_source_name", "text")

        self.obs_connection = None
        self.enabled = True

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> list:
        """初始化插件"""
        self.event_bus = event_bus
        self.config = config

        if obs is None:
            self.logger.error("obsws-python库未安装")
            return []

        # 注册事件监听
        self.event_bus.on("obs.send_text", self._handle_send_text)

        # 连接OBS
        await self._connect_obs()

        self.logger.info("ObsControlPlugin 初始化完成")
        return []

    async def cleanup(self):
        """清理资源"""
        if self.obs_connection:
            self.obs_connection.disconnect()

        if self.event_bus:
            self.event_bus.off("obs.send_text", self._handle_send_text)

        self.logger.info("ObsControlPlugin 清理完成")

    async def _connect_obs(self):
        """连接OBS WebSocket"""
        if not obs:
            return

        try:
            self.obs_connection = obs.ReqClient(
                host=self.host, port=self.port, password=self.password if self.password else None
            )
            self.logger.info(f"已连接到OBS: {self.host}:{self.port}")
        except Exception as e:
            self.logger.error(f"连接OBS失败: {e}")

    async def _handle_send_text(self, event_name: str, data: Any, source: str):
        """处理发送文本事件"""
        if not self.obs_connection:
            self.logger.warning("OBS未连接")
            return

        try:
            text = data.get("text", "")
            if text:
                self.obs_connection.set_input_settings(self.text_source_name, {"text": text}, True)
                self.logger.debug(f"已发送文本到OBS: {text}")
        except Exception as e:
            self.logger.error(f"发送文本到OBS失败: {e}")

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "ObsControl",
            "version": "2.0.0",
            "author": "Amaidesu Team",
            "description": "OBS控制插件",
            "category": "software",
            "api_version": "2.0",
        }


plugin_entrypoint = ObsControlPlugin
