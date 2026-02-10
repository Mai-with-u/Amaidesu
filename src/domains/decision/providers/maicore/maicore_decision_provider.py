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
from typing import TYPE_CHECKING, Any, Dict, Literal, Optional

from maim_message import MessageBase, RouteConfig, Router, TargetConfig
from pydantic import Field

from src.domains.decision.intent import Intent
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.types.base.decision_provider import DecisionProvider

from .router_adapter import RouterAdapter

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus
    from src.modules.types.base.normalized_message import NormalizedMessage


class MaiCoreDecisionProvider(DecisionProvider):
    """
    MaiCore 决策提供者

    通过 WebSocket 与 MaiCore 通信，将 NormalizedMessage 转换为 Intent。

    职责:
    - 决策逻辑 (decide)
    - 直接使用 Router 管理 WebSocket 连接
    - 使用 RouterAdapter 发送消息到 MaiCore
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

    class ConfigSchema(BaseProviderConfig):
        """MaiCore决策Provider配置Schema

        通过WebSocket与MaiCore通信进行决策。
        """

        type: Literal["maicore"] = "maicore"
        host: str = Field(default="localhost", description="MaiCore WebSocket服务器主机地址")
        port: int = Field(default=8000, description="MaiCore WebSocket服务器端口", ge=1, le=65535)
        platform: str = Field(default="amaidesu", description="平台标识符")
        http_host: Optional[str] = Field(default=None, description="HTTP服务器主机（可选）")
        http_port: Optional[int] = Field(default=None, description="HTTP服务器端口", ge=1, le=65535)
        http_callback_path: str = Field(default="/callback", description="HTTP回调路径")
        connect_timeout: float = Field(default=10.0, description="连接超时时间（秒）", gt=0)
        reconnect_interval: float = Field(default=5.0, description="重连间隔时间（秒）", gt=0)

        # Decision 超时配置
        decision_timeout: float = Field(default=30.0, description="等待 MaiCore 响应的超时时间（秒）", gt=0)

        # Action 建议配置
        action_suggestions_enabled: bool = Field(default=False, description="是否启用 MaiBot Action 建议")
        action_confidence_threshold: float = Field(
            default=0.6, ge=0.0, le=1.0, description="Action 建议的最低置信度阈值"
        )
        action_cooldown_seconds: float = Field(default=5.0, gt=0.0, description="同一 Action 的最小间隔（秒）")
        max_suggested_actions: int = Field(default=3, ge=1, le=10, description="每条消息最大建议数量")

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """
        获取 Provider 注册信息

        用于显式注册模式，避免模块导入时的自动注册。
        """
        return {"layer": "decision", "name": "maicore", "class": cls, "source": "builtin:maicore"}

    def __init__(self, config: Dict[str, Any]):
        self.provider_name = "maicore"
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
        # 使用 Pydantic Schema 验证配置
        self.typed_config = self.ConfigSchema(**config)
        self.logger = get_logger("MaiCoreDecisionProvider")

        # WebSocket 配置
        self.host = self.typed_config.host
        self.port = self.typed_config.port
        self.platform = self.typed_config.platform
        self.ws_url = f"ws://{self.host}:{self.port}/ws"

        # HTTP 配置
        self.http_host = self.typed_config.http_host
        self.http_port = self.typed_config.http_port
        self.http_callback_path = self.typed_config.http_callback_path

        # Decision 超时配置
        self._decision_timeout = self.typed_config.decision_timeout

        # Router
        self._router: Optional[Router] = None
        self._router_task: Optional[asyncio.Task] = None

        # 组件
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

        # 初始化 IntentParser（需要LLMManager）
        llm_service = dependencies.get("llm_service") if dependencies else None
        if llm_service:
            from src.domains.decision.intent_parser import IntentParser

            self._intent_parser = IntentParser(llm_service)
            await self._intent_parser.setup()
            self.logger.info("IntentParser初始化成功")
        else:
            self.logger.warning("LLMManager未找到，IntentParser功能将不可用")

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
        """启动 WebSocket 连接 - 直接使用 Router"""
        if self._router:
            # 创建 Router 运行任务
            self._router_task = asyncio.create_task(self._router.run())
            self.logger.info("MaiCore Router 运行任务已创建")

            # 等待连接建立
            await asyncio.sleep(1)

            # 检查连接状态
            if self._router.check_connection(self.platform):
                self.logger.info("MaiCore WebSocket 连接初步建立")
            else:
                self.logger.warning("MaiCore WebSocket 连接尚未建立，Router 将在后台继续重试")

    async def disconnect(self):
        """断开 WebSocket 连接 - 直接使用 Router"""
        if self._router:
            # 停止 Router（会断开所有连接）
            await self._router.stop()
            self.logger.info("MaiCore Router 已停止")

        # 取消运行任务
        if self._router_task is not None and not self._router_task.done():
            self._router_task.cancel()
            try:
                await self._router_task
            except asyncio.CancelledError:
                pass

    def _normalized_to_message_base(self, normalized: "NormalizedMessage") -> Optional["MessageBase"]:
        """
        转换 NormalizedMessage 为 MessageBase

        将 NormalizedMessage 转换为 MaiCore 需要的 MessageBase 格式。

        Args:
            normalized: 标准化消息

        Returns:
            MessageBase 实例，如果转换失败返回 None
        """
        try:
            from maim_message import BaseMessageInfo, MessageBase, Seg, UserInfo

            # 构建UserInfo
            user_id = normalized.user_id or "unknown"
            nickname = normalized.metadata.get("user_nickname", normalized.source)
            user_info = UserInfo(user_id=user_id, user_nickname=nickname)

            # 构建Seg（文本片段）
            seg = Seg(type="text", data=normalized.text)

            # 构建MessageBase
            message = MessageBase(
                message_info=BaseMessageInfo(
                    message_id=f"normalized_{int(normalized.timestamp)}",
                    platform=normalized.source,
                    user_info=user_info,
                    time=normalized.timestamp,
                ),
                message_segment=seg,
            )

            return message
        except Exception as e:
            self.logger.error(f"转换为 MessageBase 失败: {e}", exc_info=True)
            return None

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
        if not self._is_router_connected:
            raise RuntimeError("MaiCore 未连接，无法发送消息")

        # 转换 NormalizedMessage 为 MessageBase
        message = self._normalized_to_message_base(normalized_message)
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

        # 注册 Future（被动清理：清理超时的旧 Future）
        async with self._futures_lock:
            # 被动清理：检查是否有同名 message_id 的旧 Future
            old_future = self._pending_futures.get(message_id)
            if old_future and not old_future.done():
                old_future.cancel()
                self.logger.debug(f"取消同名消息的旧 Future: {message_id}")

            self._pending_futures[message_id] = future

        try:
            self.logger.debug(f"准备发送消息到 MaiCore: {message_id}")
            await self._router_adapter.send(message)
            self.logger.info(f"消息 {message_id} 已发送至 MaiCore，等待 Intent 解析...")

            # 等待响应（使用配置的超时时间）
            intent = await asyncio.wait_for(future, timeout=self._decision_timeout)
            self.logger.info(f"消息 {message_id} 的 Intent 解析完成")

            # 异步发送 Action 建议（不阻塞主流程）
            if self.typed_config.action_suggestions_enabled and intent.suggested_actions and self._router_adapter:
                asyncio.create_task(self._safe_send_suggestion(intent))

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
                    from src.modules.events.names import CoreEvents
                    from src.modules.events.payloads.decision import DecisionResponsePayload

                    await self._event_bus.emit(
                        CoreEvents.DECISION_RESPONSE_GENERATED,
                        DecisionResponsePayload(
                            response=message.model_dump(),
                            provider=self.provider_name,
                        ),
                        source=self.provider_name,
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
        from src.domains.decision.intent import Intent
        from src.modules.types import ActionType, EmotionType, IntentAction

        return Intent(
            original_text=text,
            response_text=text,
            emotion=EmotionType.NEUTRAL,
            actions=[IntentAction(type=ActionType.BLINK, params={}, priority=30)],
            metadata={"fallback_reason": reason, "parser": "fallback"},
        )

    async def _safe_send_suggestion(self, intent: Intent) -> None:
        """
        安全发送 Action 建议（捕获异常）

        异步发送 MaiBot Action 建议，任何错误都不会影响主流程。

        Args:
            intent: 包含建议动作的 Intent 对象
        """
        try:
            if not self._router_adapter:
                self.logger.warning("RouterAdapter 未初始化，无法发送 Action 建议")
                return

            await self._router_adapter.send_action_suggestion(intent)
            self.logger.debug(f"Action 建议发送成功: {len(intent.suggested_actions)} 个动作")
        except Exception as e:
            self.logger.warning(f"发送 Action 建议失败: {e}", exc_info=True)

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
        return self._is_router_connected

    @property
    def _is_router_connected(self) -> bool:
        """检查 Router 是否已连接"""
        if self._router:
            return self._router.check_connection(self.platform)
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

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取运行时统计信息

        Returns:
            统计信息字典
        """
        return {
            "pending_futures_count": len(self._pending_futures),
            "is_connected": self.is_connected,
            "router_running": self._router_task is not None and not self._router_task.done(),
        }
