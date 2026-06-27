"""MaiBotDecider - 通过 WebSocket 与 MaiBot 通信的纯文本转发决策器。"""

import asyncio
from typing import Any, Dict, Literal, Optional

from maim_message import MessageBase, RouteConfig, Router, TargetConfig
from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import IntentPayload
from src.modules.logging import get_logger
from src.modules.types import Intent, IntentMetadata
from src.modules.types.base.normalized_message import NormalizedMessage
from src.stages.decision.registry import decider

from .router_adapter import RouterAdapter
from src.modules.time_utils import now_ms


@decider("maibot")
class MaiBotDecider:
    """通过 WebSocket 与 MaiBot 通信，将 NormalizedMessage 转发给 MaiBot，并从 MaiBot 的回复中提取文本作为 Intent。"""

    class ConfigSchema(BaseConfig):
        type: Literal["maibot"] = "maibot"
        host: str = Field(default="localhost", description="MaiBot WebSocket服务器主机地址")
        port: int = Field(default=8000, description="MaiBot WebSocket服务器端口", ge=1, le=65535)
        platform: str = Field(default="amaidesu", description="平台标识符")
        token: Optional[str] = Field(default=None, description="可选的 token 鉴权")
        connect_timeout: float = Field(default=10.0, description="连接超时时间（秒）", gt=0)
        reconnect_interval: float = Field(default=5.0, description="重连间隔时间（秒）", gt=0)

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        return {"layer": "decision", "name": "maibot", "class": cls, "source": "builtin:maibot"}

    def __init__(self, config: Dict[str, Any], event_bus: EventBus, **kwargs):
        self.name = "maibot"
        self.typed_config = self.ConfigSchema.from_dict(config)
        self.logger = get_logger("MaiBotDecider")

        self._event_bus = event_bus

        self.host = self.typed_config.host
        self.port = self.typed_config.port
        self.platform = self.typed_config.platform
        self.token = self.typed_config.token
        self.ws_url = f"ws://{self.host}:{self.port}/ws"

        self._router: Optional[Router] = None
        self._router_task: Optional[asyncio.Task] = None
        self._router_adapter: Optional[RouterAdapter] = None
        self._is_initialized = False

    async def setup(self) -> None:
        self.logger.info("初始化 MaiBotDecider...")

        self._setup_router()

        if not self._router:
            self.logger.error("Router 初始化失败")
            raise RuntimeError("Router 初始化失败")

        self._router_adapter = RouterAdapter(self._router, self._event_bus)
        self._router_adapter.register_handler(self._handle_maibot_message)

        await self.connect()

        self._is_initialized = True
        self.logger.info("MaiBotDecider 初始化完成")

    def _setup_router(self):
        route_config = RouteConfig(
            route_config={
                self.platform: TargetConfig(
                    url=self.ws_url,
                    token=self.token,
                )
            }
        )
        self._router = Router(route_config)
        self.logger.info(f"Router 配置完成，目标 MaiBot: {self.ws_url}")

    async def connect(self):
        if self._router:
            self._router_task = asyncio.create_task(self._router.run())
            self.logger.info("MaiBot Router 运行任务已创建")

            await asyncio.sleep(1)

            if self._router.check_connection(self.platform):
                self.logger.info("MaiBot WebSocket 连接初步建立")
            else:
                self.logger.warning("MaiBot WebSocket 连接尚未建立，Router 将在后台继续重试")

    async def disconnect(self):
        if self._router:
            await self._router.stop()
            self.logger.info("MaiBot Router 已停止")

        if self._router_task is not None and not self._router_task.done():
            self._router_task.cancel()
            try:
                await self._router_task
            except asyncio.CancelledError:
                pass

    def _normalized_to_message_base(self, normalized: "NormalizedMessage") -> Optional["MessageBase"]:
        try:
            from maim_message import BaseMessageInfo, FormatInfo, GroupInfo, MessageBase, Seg, UserInfo

            user_id = normalized.user_id or "unknown"
            nickname = normalized.user_nickname or normalized.source
            user_info = UserInfo(platform=self.platform, user_id=user_id, user_nickname=nickname)

            format_info = FormatInfo(
                content_format=["text"],
                accept_format=["text"],
            )

            seg = Seg(type="text", data=normalized.text)

            message_id = f"normalized_{normalized.timestamp_ms}"
            additional_config = {
                "source": "amaidesu",
                "original_platform": normalized.source,
                "original_message_id": message_id,
                "account_id": "amaidesu",
                "scope": "live_room",
            }

            group_info = GroupInfo(
                platform=self.platform,
                group_id="live_room",
                group_name="直播间",
            )

            return MessageBase(
                message_info=BaseMessageInfo(
                    message_id=message_id,
                    platform=self.platform,
                    user_info=user_info,
                    time=normalized.timestamp_ms,
                    format_info=format_info,
                    additional_config=additional_config,
                    group_info=group_info,
                ),
                message_segment=seg,
                raw_message=normalized.text,
            )
        except Exception as e:
            self.logger.error(f"转换为 MessageBase 失败: {e}", exc_info=True)
            return None

    async def decide(self, normalized_message: "NormalizedMessage") -> None:
        if not self._is_router_connected:
            self.logger.warning("MaiBot 未连接，跳过消息发送")
            return

        message = self._normalized_to_message_base(normalized_message)
        if not message:
            self.logger.error("转换为 MessageBase 失败，无法发送消息")
            return

        if not self._router_adapter:
            self.logger.error("RouterAdapter 未初始化，无法发送消息")
            return

        try:
            await self._router_adapter.send(message)
            self.logger.debug(f"消息已发送至 MaiBot (id: {message.message_info.message_id})")
        except Exception as e:
            self.logger.error(f"发送消息到 MaiBot 时发生错误: {e}", exc_info=True)

    def _handle_maibot_message(self, message_data: Dict[str, Any]):
        self.logger.debug(f"收到 MaiBot 原始消息: {message_data}")
        asyncio.create_task(self._process_maibot_message(message_data))

    async def _process_maibot_message(self, message_data: Dict[str, Any]):
        try:
            message = MessageBase.from_dict(message_data)
        except Exception as e:
            self.logger.error(f"从 MaiBot 接收到的消息无法解析为 MessageBase 对象: {e}", exc_info=True)
            self.logger.debug(f"原始消息数据: {message_data}")
            return

        message_id = message.message_info.message_id

        seg = message.message_segment
        if not seg or not seg.data:
            self.logger.debug(f"收到无内容消息，忽略（message_id={message_id}）")
            return

        if seg.type == "text" and isinstance(seg.data, str):
            speech = seg.data
        else:
            speech = f"[{seg.type}] {seg.data}"

        self.logger.debug(f"收到 MaiBot 消息: message_id={message_id}, speech={speech[:50]}")

        intent = Intent(
            emotion=None,
            action=None,
            speech=speech,
            context=None,
            metadata=IntentMetadata(
                source_id="maibot",
                decision_time_ms=now_ms(),
                parser_type="text_forward",
            ),
        )

        await self._publish_intent(intent)

    async def _publish_intent(self, intent: Intent) -> None:
        if not self._event_bus:
            self.logger.error("EventBus 未初始化，无法发布事件")
            return

        name = self.get_info().get("name", "maibot")

        await self._event_bus.emit(
            CoreEvents.DECISION_INTENT_GENERATED,
            IntentPayload.from_intent(intent, name),
            source="MaiBotDecider",
        )

        self.logger.debug("已发布 decision.intent.generated 事件")

    async def cleanup(self) -> None:
        self.logger.info("清理 MaiBotDecider...")
        await self.disconnect()
        self._is_initialized = False
        self.logger.info("MaiBotDecider 已清理")

    @property
    def is_connected(self) -> bool:
        return self._is_router_connected

    @property
    def _is_router_connected(self) -> bool:
        if self._router:
            return self._router.check_connection(self.platform)
        return False

    @property
    def router(self) -> Optional[Router]:
        return self._router

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "MaiBotDecider",
            "version": "1.0.0",
            "host": self.host,
            "port": self.port,
            "platform": self.platform,
            "is_connected": self.is_connected,
        }

    def get_statistics(self) -> Dict[str, Any]:
        return {
            "is_connected": self.is_connected,
            "router_running": self._router_task is not None and not self._router_task.done(),
        }
