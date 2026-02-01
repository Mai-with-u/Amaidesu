"""
MaiCoreDecisionProvider - MaiCore决策提供者

职责:
- 将 CanonicalMessage 转换为决策结果 (MessageBase)
- 协调 WebSocketConnector 和 RouterAdapter
"""

import asyncio
from typing import Dict, Any, Optional, TYPE_CHECKING

from maim_message import Router, RouteConfig, TargetConfig, MessageBase

from src.core.base.decision_provider import DecisionProvider
from src.core.base.websocket_connector import WebSocketConnector
from src.core.base.router_adapter import RouterAdapter
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.layers.canonical.canonical_message import CanonicalMessage


class MaiCoreDecisionProvider(DecisionProvider):
    """
    MaiCore 决策提供者

    通过 WebSocket 与 MaiCore 通信，将 CanonicalMessage 转换为决策结果。

    职责:
    - 决策逻辑 (decide)
    - 协调 WebSocketConnector 和 RouterAdapter

    配置示例:
        ```toml
        [decision.maicore]
        host = "localhost"
        port = 8000
        platform = "amaidesu"
        http_host = "localhost"
        http_port = 8080
        http_callback_path = "/callback"
        ```
    """

    def __init__(self, config: Dict[str, Any]):
        """
        初始化 MaiCoreDecisionProvider

        Args:
            config: 配置字典，包含:
                - host: MaiCore WebSocket 服务器主机
                - port: MaiCore WebSocket 服务器端口
                - platform: 平台标识符
                - http_host: (可选) HTTP 服务器主机
                - http_port: (可选) HTTP 服务器端口
                - http_callback_path: (可选) HTTP 回调路径，默认"/callback"
        """
        self.config = config
        self.logger = get_logger("MaiCoreDecisionProvider")

        # WebSocket 配置
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 8000)
        self.platform = config.get("platform", "amaidesu")
        self.ws_url = f"ws://{self.host}:{self.port}/ws"

        # HTTP 配置
        self.http_host = config.get("http_host")
        self.http_port = config.get("http_port")
        self.http_callback_path = config.get("http_callback_path", "/callback")

        # Router
        self._router: Optional[Router] = None

        # 组件
        self._ws_connector: Optional[WebSocketConnector] = None
        self._router_adapter: Optional[RouterAdapter] = None

        # EventBus 引用（用于事件通知）
        self._event_bus: Optional["EventBus"] = None

    async def setup(self, event_bus: "EventBus", config: Optional[Dict[str, Any]] = None) -> None:
        """
        设置 MaiCoreDecisionProvider

        Args:
            event_bus: EventBus 实例
            config: Provider 配置（忽略，使用 __init__ 传入的 config）
        """
        self._event_bus = event_bus
        self.logger.info("初始化 MaiCoreDecisionProvider...")

        # 配置 Router
        self._setup_router()

        if not self._router:
            self.logger.error("Router 初始化失败")
            raise RuntimeError("Router 初始化失败")

        # 创建 RouterAdapter
        self._router_adapter = RouterAdapter(self._router, event_bus)
        self._router_adapter.register_message_handler(self._handle_maicore_message)

        # 创建 WebSocketConnector
        self._ws_connector = WebSocketConnector(
            ws_url=self.ws_url, router=self._router, event_bus=event_bus, provider_name="maicore"
        )

        self.logger.info("MaiCoreDecisionProvider 初始化完成")

    def _setup_router(self):
        """配置 maim_message Router"""
        route_config = RouteConfig(
            route_config={
                self.platform: TargetConfig(
                    url=self.ws_url,
                    token=None,
                )
            }
        )
        self._router = Router(route_config)
        self.logger.info(f"Router 配置完成，目标 MaiCore: {self.ws_url}")

    async def connect(self):
        """启动 WebSocket 连接"""
        if self._ws_connector:
            await self._ws_connector.connect()

    async def disconnect(self):
        """断开 WebSocket 连接"""
        if self._ws_connector:
            await self._ws_connector.disconnect()

    async def decide(self, canonical_message: "CanonicalMessage") -> MessageBase:
        """
        进行决策（发送消息到 MaiCore）

        Args:
            canonical_message: 标准化消息

        Returns:
            MessageBase: 决策结果

        Raises:
            RuntimeError: 如果未连接
            ConnectionError: 如果发送失败
        """
        if not self._ws_connector or not self._ws_connector.is_connected:
            raise RuntimeError("MaiCore 未连接，无法发送消息")

        # 转换 CanonicalMessage 为 MessageBase
        message = canonical_message.to_message_base()
        if not message:
            self.logger.error("转换为 MessageBase 失败，无法发送消息")
            raise RuntimeError("无法将 CanonicalMessage 转换为 MessageBase")

        # 发送消息
        if not self._router_adapter:
            self.logger.error("RouterAdapter 未初始化，无法发送消息")
            raise RuntimeError("RouterAdapter 未初始化")

        try:
            self.logger.debug(f"准备发送消息到 MaiCore: {message.message_info.message_id}")
            await self._router_adapter.send(message)
            self.logger.info(f"消息 {message.message_info.message_id} 已发送至 MaiCore")
            return message
        except Exception as e:
            self.logger.error(f"发送消息到 MaiCore 时发生错误: {e}", exc_info=True)
            raise ConnectionError(f"发送消息失败: {e}") from e

    def _handle_maicore_message(self, message_data: Dict[str, Any]):
        """
        处理从 MaiCore 接收到的消息

        通过 EventBus 发布消息事件。

        Args:
            message_data: 消息数据（字典格式）
        """
        # 在新任务中处理以避免阻塞
        asyncio.create_task(self._process_maicore_message(message_data))

    async def _process_maicore_message(self, message_data: Dict[str, Any]):
        """
        异步处理从 MaiCore 接收到的消息

        通过 EventBus 发布消息事件。

        Args:
            message_data: 消息数据（字典格式）
        """
        try:
            # 从字典构建 MessageBase 对象
            message = MessageBase.from_dict(message_data)
        except Exception as e:
            self.logger.error(f"从 MaiCore 接收到的消息无法解析为 MessageBase 对象: {e}", exc_info=True)
            self.logger.debug(f"原始消息数据: {message_data}")
            return

        # 通过 EventBus 发布决策响应事件
        if self._event_bus:
            try:
                await self._event_bus.emit("decision.response_generated", {"message": message})
            except Exception as e:
                self.logger.error(f"发布决策响应事件失败: {e}", exc_info=True)

    async def cleanup(self) -> None:
        """
        清理资源

        断开连接并清理所有资源。
        """
        self.logger.info("清理 MaiCoreDecisionProvider...")
        await self.disconnect()
        self.logger.info("MaiCoreDecisionProvider 已清理")

    @property
    def is_connected(self) -> bool:
        """获取连接状态"""
        if self._ws_connector:
            return self._ws_connector.is_connected
        return False

    @property
    def router(self) -> Optional[Router]:
        """获取 Router 实例"""
        return self._router

    def get_info(self) -> Dict[str, Any]:
        """
        获取 Provider 信息

        Returns:
            Provider 信息字典
        """
        return {
            "name": "MaiCoreDecisionProvider",
            "version": "1.0.0",
            "host": self.host,
            "port": self.port,
            "platform": self.platform,
            "http_host": self.http_host,
            "http_port": self.http_port,
            "is_connected": self.is_connected,
        }
