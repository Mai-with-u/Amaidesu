"""
MaiCoreDecisionProvider - MaiCore决策提供者

职责:
- 将 NormalizedMessage 转换为 Intent
- 通过 WebSocket 与 MaiCore 通信
- 使用 IntentParser 解析 MaiCore 响应

事件说明:
- "decision.response_generated": 保留字符串形式的事件名，用于向后兼容
  该事件不在 CoreEvents 中定义，因为它是 MaiCore 特定的历史事件
"""

import asyncio
from typing import Dict, Any, Optional, TYPE_CHECKING

from maim_message import Router, RouteConfig, TargetConfig, MessageBase

from src.core.base.decision_provider import DecisionProvider
from src.core.connectors.websocket_connector import WebSocketConnector
from src.core.connectors.router_adapter import RouterAdapter
from src.domains.decision.intent import Intent
from src.core.utils.logger import get_logger

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.core.base.normalized_message import NormalizedMessage


class MaiCoreDecisionProvider(DecisionProvider):
    """
    MaiCore 决策提供者

    通过 WebSocket 与 MaiCore 通信，将 NormalizedMessage 转换为 Intent。

    职责:
    - 决策逻辑 (decide)
    - 协调 WebSocketConnector 和 RouterAdapter
    - 使用 IntentParser 解析 MaiCore 响应为 Intent

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

    异步处理流程:
        1. decide() 被调用，创建 asyncio.Future
        2. 发送消息到 MaiCore
        3. MaiCore 响应到达时，通过 IntentParser 解析为 Intent
        4. 设置 Future 结果，decide() 返回 Intent
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
        self._intent_parser = None  # IntentParser实例

        # EventBus 引用（用于事件通知）
        self._event_bus: Optional["EventBus"] = None

        # 异步响应处理（message_id -> Future）
        self._pending_futures: Dict[str, asyncio.Future] = {}
        self._futures_lock = asyncio.Lock()

    async def setup(
        self,
        event_bus: "EventBus",
        config: Optional[Dict[str, Any]] = None,
        dependencies: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        设置 MaiCoreDecisionProvider

        Args:
            event_bus: EventBus 实例
            config: Provider 配置（忽略，使用 __init__ 传入的 config）
            dependencies: 依赖注入字典，可能包含 llm_service
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

        # 初始化 IntentParser（需要LLMService）
        llm_service = dependencies.get("llm_service") if dependencies else None
        if llm_service:
            from src.domains.decision.intent_parser import IntentParser

            self._intent_parser = IntentParser(llm_service)
            await self._intent_parser.setup()
            self.logger.info("IntentParser初始化成功")
        else:
            self.logger.warning("LLMService未找到，IntentParser功能将不可用")

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

    async def decide(self, normalized_message: "NormalizedMessage") -> Intent:
        """
        进行决策（发送消息到 MaiCore）

        Args:
            normalized_message: 标准化消息

        Returns:
            Intent: 决策意图

        Raises:
            RuntimeError: 如果未连接
            ConnectionError: 如果发送失败
        """
        if not self._ws_connector or not self._ws_connector.is_connected:
            raise RuntimeError("MaiCore 未连接，无法发送消息")

        # 转换 NormalizedMessage 为 MessageBase
        message = normalized_message.to_message_base()
        if not message:
            self.logger.error("转换为 MessageBase 失败，无法发送消息")
            raise RuntimeError("无法将 NormalizedMessage 转换为 MessageBase")

        # 发送消息
        if not self._router_adapter:
            self.logger.error("RouterAdapter 未初始化，无法发送消息")
            raise RuntimeError("RouterAdapter 未初始化")

        # 创建 Future 用于等待响应
        future: asyncio.Future[Intent] = asyncio.Future()
        message_id = message.message_info.message_id

        # 注册 Future
        async with self._futures_lock:
            self._pending_futures[message_id] = future

        try:
            self.logger.debug(f"准备发送消息到 MaiCore: {message_id}")
            await self._router_adapter.send(message)
            self.logger.info(f"消息 {message_id} 已发送至 MaiCore，等待 Intent 解析...")

            # 等待响应（超时30秒）
            intent = await asyncio.wait_for(future, timeout=30.0)
            self.logger.info(f"消息 {message_id} 的 Intent 解析完成")
            return intent

        except asyncio.TimeoutError:
            self.logger.error(f"等待 MaiCore 响应超时: {message_id}")
            # 清理 Future
            async with self._futures_lock:
                self._pending_futures.pop(message_id, None)
            # 返回默认 Intent
            return self._create_fallback_intent(normalized_message.text, "timeout")
        except Exception as e:
            self.logger.error(f"发送消息到 MaiCore 时发生错误: {e}", exc_info=True)
            # 清理 Future
            async with self._futures_lock:
                self._pending_futures.pop(message_id, None)
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

        1. 解析为 MessageBase
        2. 使用 IntentParser 解析为 Intent
        3. 设置 Future 结果

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

        # 获取消息ID
        message_id = message.message_info.message_id

        # 查找对应的 Future
        async with self._futures_lock:
            future = self._pending_futures.pop(message_id, None)

        if not future:
            self.logger.warning(f"收到未知消息的响应: {message_id}")
            # 仍然发布事件（向后兼容）
            if self._event_bus:
                try:
                    from src.core.events.names import CoreEvents
                    await self._event_bus.emit(
                        CoreEvents.DECISION_RESPONSE_GENERATED,
                        {"message": message}
                    )
                except Exception as e:
                    self.logger.error(f"发布决策响应事件失败: {e}", exc_info=True)
            return

        try:
            # 使用 IntentParser 解析 MessageBase → Intent
            if self._intent_parser:
                intent = await self._intent_parser.parse(message)
                self.logger.debug(f"Intent解析成功: {intent}")
            else:
                # 降级：创建简单的 Intent
                text = str(message)
                intent = self._create_fallback_intent(text, "no_parser")
                self.logger.warning("IntentParser未初始化，使用降级方案")

            # 设置 Future 结果
            if not future.done():
                future.set_result(intent)
                self.logger.debug(f"消息 {message_id} 的 Intent 已设置")

        except Exception as e:
            self.logger.error(f"解析 Intent 失败: {e}", exc_info=True)
            # 设置异常
            if not future.done():
                future.set_exception(e)

    def _create_fallback_intent(self, text: str, reason: str) -> Intent:
        """
        创建降级 Intent

        Args:
            text: 消息文本
            reason: 降级原因

        Returns:
            默认 Intent
        """
        from src.domains.decision.intent import EmotionType, ActionType, IntentAction

        return Intent(
            original_text=text,
            response_text=text,
            emotion=EmotionType.NEUTRAL,
            actions=[IntentAction(type=ActionType.BLINK, params={}, priority=30)],
            metadata={"fallback_reason": reason, "parser": "fallback"},
        )

    async def cleanup(self) -> None:
        """
        清理资源

        断开连接并清理所有资源。
        """
        self.logger.info("清理 MaiCoreDecisionProvider...")

        # 清理 IntentParser
        if self._intent_parser:
            await self._intent_parser.cleanup()

        # 取消所有待处理的 Future
        async with self._futures_lock:
            for future in self._pending_futures.values():
                if not future.done():
                    future.cancel()
            self._pending_futures.clear()

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
