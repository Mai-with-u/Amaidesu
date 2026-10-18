"""BiliDanmakuLaplaceCollector —— LAPLACE Event Bridge 弹幕采集器

LAPLACE Chat（https://laplace.live/chat）是浏览器中的 B 站直播聊天面板，
把弹幕、SC、礼物分栏显示，并提供 OBS 覆盖层、TTS、翻译等能力。
LAPLACE Event Bridge（https://laplace.live/chat/event-bridge）是它的
WebSocket 事件桥：把 LAPLACE Chat 收到的实时 B 站直播事件转给本地客户端。

本采集器以 client 角色连接桥接服务器（默认 ``ws://localhost:9696``），把
事件映射为现有 ``room.message.*`` 语义事件。使用前需按 Event Bridge 文档
启动桥接服务器（官方 ``leb-server``，或 Event Fetcher v2.2.0+ 的 bridge
mode）；使用 ``leb-server`` 时 LAPLACE Chat 控制台以 server 角色连入同一
bridge，控制台页面必须保持开启。bridge 为实时广播、断线期间不补播。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from src.modules.collectors.base import BaseCollector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.logging import get_logger

from .client import LaplaceBridgeClient
from .events import LaplaceEventMapper

# message_type → 语义事件名（EventBus 会校验事件名末段 == message_type）
_EVENT_MAP: Dict[str, str] = {
    "danmaku": CoreEvents.ROOM_MESSAGE_DANMAKU,
    "gift": CoreEvents.ROOM_MESSAGE_GIFT,
    "super_chat": CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
    "guard": CoreEvents.ROOM_MESSAGE_GUARD,
    "enter": CoreEvents.ROOM_MESSAGE_ENTER,
}


class BiliDanmakuLaplaceCollector(BaseCollector):
    """通过 LAPLACE Event Bridge 接收 B 站弹幕/礼物/SC/上舰/进房。

    LAPLACE Chat 是 B 站直播聊天面板（https://laplace.live/chat），
    Event Bridge 是它向本地客户端广播事件的 WebSocket 桥
    （https://laplace.live/chat/event-bridge）。采集器只发布数据事件，
    不订阅下游结果；``platform`` 统一为 ``bilibili``，场次归属由事件
    总线的场次盖章拦截器注入。
    """

    name = "bili_danmaku_laplace"
    description = "通过 LAPLACE Event Bridge 接收 B 站弹幕/礼物/SC/上舰/进房（client 角色）"

    class ConfigSchema(BaseConfig):
        """LAPLACE Event Bridge 客户端配置"""

        url: str = Field(
            default="ws://localhost:9696",
            description=(
                "LAPLACE Event Bridge 地址；leb-server 默认 ws://localhost:9696。"
                "前置要求见 https://laplace.live/chat/event-bridge"
            ),
        )
        token: str = Field(
            default="",
            description="桥接认证 token；须与 leb-server --auth 一致，空=无认证",
        )
        room_id: int = Field(
            default=0,
            ge=0,
            description="只接收该房间事件（origin 为规范长房间号）；0=不过滤",
        )
        emit_semantic_events: bool = Field(default=True, description="emit room.message.* 语义事件")
        handle_enter_messages: bool = Field(default=True, description="处理进房事件（interaction action=1）")
        handle_gift_messages: bool = Field(default=True, description="处理礼物事件")
        handle_superchat_messages: bool = Field(default=True, description="处理醒目留言事件")
        handle_guard_messages: bool = Field(default=True, description="处理上舰/续费事件（toast）")
        dedupe_cache_size: int = Field(
            default=2000,
            ge=0,
            le=65536,
            description="去重缓存上限；0=关闭去重",
        )
        reconnect_base_ms: int = Field(default=3000, ge=50, description="重连基础间隔（毫秒）")
        reconnect_max_ms: int = Field(default=60000, ge=1000, description="重连最大间隔（毫秒）")

        @field_validator("url")
        @classmethod
        def _validate_url(cls, value: str) -> str:
            value = value.strip()
            parsed = urlsplit(value)
            if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
                raise ValueError("url 必须是 ws:// 或 wss:// 地址")
            if parsed.fragment:
                raise ValueError("url 不能包含 fragment")
            return value

        @model_validator(mode="after")
        def _validate_reconnect_range(self) -> Self:
            if self.reconnect_base_ms > self.reconnect_max_ms:
                raise ValueError("reconnect_base_ms 不能大于 reconnect_max_ms")
            return self

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        super().__init__(event_bus=event_bus)
        self.logger = get_logger(self.__class__.__name__)
        self.typed_config = self.ConfigSchema.from_dict(config or {})
        self.is_started = False
        self._emit_semantic_events = self.typed_config.emit_semantic_events
        # 连接与去重状态都只在 __init__ 构造一次；重连由 client.events() 内部处理，
        # mapper 跨重连保留，避免 bridge 重发历史事件时重复落库
        self._client = LaplaceBridgeClient(
            url=self.typed_config.url,
            token=self.typed_config.token,
            reconnect_base_ms=self.typed_config.reconnect_base_ms,
            reconnect_max_ms=self.typed_config.reconnect_max_ms,
        )
        self._mapper = LaplaceEventMapper(
            room_id=self.typed_config.room_id,
            handle_enter=self.typed_config.handle_enter_messages,
            handle_gift=self.typed_config.handle_gift_messages,
            handle_superchat=self.typed_config.handle_superchat_messages,
            handle_guard=self.typed_config.handle_guard_messages,
            dedupe_cache_size=self.typed_config.dedupe_cache_size,
        )

    # ===== 生命周期 =====

    async def start(self) -> None:
        """启动后台采集任务（连接在 collect() 内建立，不阻塞启动）。"""
        if not self.is_started:
            self.is_started = True
            await self._start_collect_task()

    async def stop(self) -> None:
        """停止后台采集任务（无条件取消，兼容任务已异常结束的情形）。"""
        self.is_started = False
        await self._stop_collect_task()

    async def cleanup(self) -> None:
        """资源清理兜底：确保后台任务与 WebSocket 连接已关闭。"""
        self.is_started = False
        await self._stop_collect_task()

    # ===== 采集 =====

    async def collect(self) -> AsyncIterator[RoomMessagePayload]:
        """连接 bridge 并逐条发布语义事件；由基类后台任务迭代驱动。"""
        self.is_started = True
        try:
            async for raw in self._client.events():
                payload = self._mapper.to_payload(raw)
                if payload is None:
                    continue
                if self._emit_semantic_events:
                    await self._emit_semantic_event(payload)
                yield payload
        finally:
            self.is_started = False

    async def _emit_semantic_event(self, payload: RoomMessagePayload) -> None:
        """按 message_type 发对应 ``room.message.*`` 事件。"""
        event_name = _EVENT_MAP.get(payload.message_type)
        if event_name is None:
            self.logger.warning(f"未知 message_type，跳过 emit: {payload.message_type}")
            return
        try:
            await self.emit_event(event_name, payload)
        except Exception as exc:  # noqa: BLE001 - emit 边界，失败不影响后续采集
            self.logger.warning(
                f"emit 语义事件失败: {event_name}（{type(exc).__name__}: {exc}）",
                exc=exc,
            )


__all__ = ["BiliDanmakuLaplaceCollector"]
