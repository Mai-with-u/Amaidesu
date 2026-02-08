"""
OBS Control OutputProvider - OBS控制Provider

职责:
- 通过WebSocket连接到OBS
- 接收文本更新事件并发送到OBS
"""

from typing import Dict, Any, Optional, TYPE_CHECKING
from pydantic import Field

if TYPE_CHECKING:
    from src.core.event_bus import EventBus

from src.core.base.output_provider import OutputProvider
from src.core.utils.logger import get_logger
from src.services.config.schemas.schemas.base import BaseProviderConfig

try:
    import obsws_python as obs
except ImportError:
    obs = None


class ObsControlOutputProvider(OutputProvider):
    """OBS控制Provider"""

    class ConfigSchema(BaseProviderConfig):
        """OBS控制输出Provider配置"""

        type: str = "obs_control"

        # OBS配置
        host: str = Field(default="localhost", description="OBS WebSocket主机地址")
        port: int = Field(default=4455, ge=1, le=65535, description="OBS WebSocket端口")
        password: Optional[str] = Field(default=None, description="OBS WebSocket密码")
        text_source_name: str = Field(default="text", description="文本源名称")

    def __init__(self, config: Dict[str, Any]):
        """
        初始化OBS Control Provider

        Args:
            config: 配置字典，包含:
                - host: OBS WebSocket主机地址 (默认: localhost)
                - port: OBS WebSocket端口 (默认: 4455)
                - password: OBS WebSocket密码 (默认: "")
                - text_source_name: 文本源名称 (默认: "text")
        """
        super().__init__(config)
        self.logger = get_logger("ObsControlOutputProvider")

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        self.typed_config = self.ConfigSchema(**config)

        # OBS配置
        self.host = self.typed_config.host
        self.port = self.typed_config.port
        self.password = self.typed_config.password
        self.text_source_name = self.typed_config.text_source_name

        self.obs_connection = None
        self.event_bus = None

    async def setup(self, event_bus: "EventBus"):
        """
        设置Provider

        Args:
            event_bus: 事件总线实例
        """
        self.event_bus = event_bus

        if obs is None:
            self.logger.error("obsws-python库未安装")
            return

        # 注册事件监听
        self.event_bus.on("obs.send_text", self._handle_send_text)

        # 连接OBS
        await self._connect_obs()

        self.logger.info("ObsControlOutputProvider 已设置")

    async def cleanup(self):
        """清理资源"""
        # 断开OBS连接
        if self.obs_connection:
            self.obs_connection.disconnect()

        # 取消事件监听
        if self.event_bus:
            self.event_bus.off("obs.send_text", self._handle_send_text)

        self.logger.info("ObsControlOutputProvider 已清理")

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
