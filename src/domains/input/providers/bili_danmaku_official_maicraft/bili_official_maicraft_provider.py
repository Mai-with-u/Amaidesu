"""
Bilibili 官方弹幕+Minecraft转发 InputProvider

从 Bilibili 官方开放平台 WebSocket API 采集弹幕数据并转发到Minecraft服务器。
"""

import asyncio
import contextlib
from typing import Any, AsyncIterator, Dict, Literal, Optional

from pydantic import Field, field_validator

from src.domains.input.shared.bili_messages import (
    BiliBaseMessage,
    BiliMessageType,
    BiliMessageTypeConfig,
    DanmakuMessage,
    EnterMessage,
    GiftMessage,
    GuardMessage,
    SuperChatMessage,
)
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.types.base.input_provider import InputProvider
from src.modules.types.base.normalized_message import NormalizedMessage

from .client.websocket_client import BiliWebSocketClient


class ForwardWebSocketClient:
    """简易的外发 WebSocket 客户端，支持自动重连与发送JSON。"""

    def __init__(self, url: str, logger, reconnect_delay: int = 5):
        self.url = url
        self.logger = logger
        self.reconnect_delay = reconnect_delay
        self._ws = None
        self._task = None
        self._stop = asyncio.Event()

    async def run(self):
        try:
            import websockets
        except Exception as e:
            self.logger.error(f"缺少 websockets 依赖，无法转发消息: {e}")
            return

        while not self._stop.is_set():
            try:
                self.logger.info(f"尝试连接外发 WebSocket: {self.url}")
                async with websockets.connect(self.url) as ws:
                    self._ws = ws
                    self.logger.info("外发 WebSocket 已连接")
                    # 等待直到停止事件或连接断开
                    while not self._stop.is_set():
                        await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.warning(f"外发 WebSocket 连接失败/已断开: {e}")
            finally:
                self._ws = None
                if not self._stop.is_set():
                    self.logger.info(f"{self.reconnect_delay} 秒后重试连接外发 WebSocket...")
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=self.reconnect_delay)
                    except asyncio.TimeoutError:
                        pass

    async def send_json(self, data: Dict[str, Any]) -> bool:
        if self._ws is None:
            self.logger.debug("外发 WebSocket 未连接，丢弃消息")
            return False
        try:
            import json

            await self._ws.send(json.dumps(data, ensure_ascii=False))
            return True
        except Exception as e:
            self.logger.error(f"外发 WebSocket 发送失败: {e}")
            return False

    async def start(self):
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self.run(), name="ForwardWebSocketClient")

    async def close(self):
        self._stop.set()
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None


class BiliDanmakuOfficialMaiCraftInputProvider(InputProvider):
    """
    Bilibili 官方弹幕+Minecraft转发 InputProvider

    使用官方WebSocket API获取实时弹幕，并转发到Minecraft服务器。
    """

    class ConfigSchema(BaseProviderConfig):
        """Bilibili官方弹幕+Minecraft转发输入Provider配置"""

        type: Literal["bili_danmaku_official_maicraft"] = "bili_danmaku_official_maicraft"
        platform: str = Field(default="bili_live", description="平台标识")
        id_code: str = Field(..., description="直播间ID代码")
        app_id: str = Field(..., description="应用ID")
        access_key: str = Field(..., description="访问密钥")
        access_key_secret: str = Field(..., description="访问密钥Secret")
        api_host: str = Field(default="https://live-open.biliapi.com", description="API主机地址")
        context_tags: Optional[list] = Field(default=None, description="Prompt上下文标签")
        forward_ws_url: Optional[str] = Field(default=None, description="转发目标WebSocket URL")
        forward_enabled: bool = Field(default=True, description="启用WebSocket转发")

        # 消息类型过滤配置
        handle_enter_messages: bool = Field(default=True, description="处理进场消息")
        handle_gift_messages: bool = Field(default=True, description="处理礼物消息")
        handle_guard_messages: bool = Field(default=True, description="处理大航海消息")
        handle_superchat_messages: bool = Field(default=True, description="处理醒目留言消息")

        @field_validator("forward_ws_url")
        @classmethod
        def validate_forward_ws_url(cls, v: Optional[str]) -> Optional[str]:
            """验证WebSocket URL格式"""
            if v is not None and not v.startswith(("ws://", "wss://")):
                raise ValueError("forward_ws_url必须以ws://或wss://开头")
            return v

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)

        # 配置
        self.typed_config = self.ConfigSchema(**config)
        self.platform = self.typed_config.platform
        self.id_code = self.typed_config.id_code
        self.app_id = self.typed_config.app_id
        self.access_key = self.typed_config.access_key
        self.access_key_secret = self.typed_config.access_key_secret
        self.api_host = self.typed_config.api_host
        self.forward_ws_url = self.typed_config.forward_ws_url
        self.forward_enabled = self.typed_config.forward_enabled

        # 消息类型配置
        self.message_type_config = BiliMessageTypeConfig(config)

        # Prompt Context Tags
        self.context_tags: Optional[list] = self.typed_config.context_tags
        if not isinstance(self.context_tags, list):
            if self.context_tags is not None:
                self.logger.warning(f"配置 'context_tags' 不是列表类型 ({type(self.context_tags)}), 将获取所有上下文。")
            self.context_tags = None
        elif not self.context_tags:
            self.logger.info("'context_tags' 为空，将获取所有上下文。")
            self.context_tags = None
        else:
            self.logger.info(f"将获取具有以下标签的上下文: {self.context_tags}")

        # 状态变量
        self.websocket_client = None
        self.forward_client: Optional[ForwardWebSocketClient] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> AsyncIterator[NormalizedMessage]:
        """采集弹幕数据"""
        await self._setup_internal()
        self.is_running = True

        # 初始化WebSocket客户端
        self.websocket_client = BiliWebSocketClient(
            id_code=self.id_code,
            app_id=self.app_id,
            access_key=self.access_key,
            access_key_secret=self.access_key_secret,
            api_host=self.api_host,
        )

        # 初始化外发WebSocket客户端
        if self.forward_enabled and self.forward_ws_url:
            self.forward_client = ForwardWebSocketClient(self.forward_ws_url, self.logger)
            await self.forward_client.start()
            self.logger.info(f"已启用外发 WebSocket 转发: {self.forward_ws_url}")
        else:
            if not self.forward_enabled:
                self.logger.info("已禁用外发 WebSocket 转发")
            else:
                self.logger.warning("未配置 forward_ws_url，外发 WebSocket 转发不可用")

        self.logger.info("开始采集 Bilibili 官方弹幕数据...")

        # 运行WebSocket连接
        try:
            await self.websocket_client.run(self._handle_message_from_bili)
        except Exception as e:
            self.logger.error(f"WebSocket运行出错: {e}", exc_info=True)
        finally:
            self.is_running = False
            await self._cleanup_internal()
            self.logger.info("Bilibili 官方弹幕采集已停止")

    async def _handle_message_from_bili(self, message_data: Dict[str, Any]):
        """处理从 Bilibili 接收到的消息"""
        try:
            cmd = message_data.get("cmd", "")

            # 使用消息类型配置进行过滤
            if not self.message_type_config.should_handle(cmd):
                return

            # 从字典创建消息对象
            bili_message = self._create_message_from_dict(message_data)
            if not bili_message:
                self.logger.debug(f"无法解析消息类型: {cmd}")
                return

            # 构造 NormalizedMessage
            normalized_msg = self._create_normalized_message(bili_message)

            # 外发到指定 WebSocket
            # BREAKING CHANGE: 载荷格式从 MessageBase 变更为 NormalizedMessage
            if self.forward_client and self.forward_enabled and self.forward_ws_url:
                try:
                    payload = normalized_msg.model_dump(exclude={"raw"})
                    # 将 raw 转换为 raw_data 字典
                    if normalized_msg.raw is not None:
                        payload["raw_data"] = normalized_msg.raw.model_dump()
                    sent = await self.forward_client.send_json(payload)
                    if not sent:
                        self.logger.debug("外发失败，消息未送达")
                except Exception as e:
                    self.logger.error(f"外发消息序列化或发送失败: {e}")

            yield normalized_msg

        except Exception as e:
            self.logger.error(f"处理消息时出错: {e}", exc_info=True)

    def _create_message_from_dict(self, data: Dict[str, Any]) -> Optional[BiliBaseMessage]:
        """从字典创建对应的消息对象"""
        cmd = data.get("cmd", "")

        if cmd == BiliMessageType.DANMAKU.value:
            return DanmakuMessage.from_dict(data)
        elif cmd == BiliMessageType.ENTER.value:
            return EnterMessage.from_dict(data)
        elif cmd == BiliMessageType.GIFT.value:
            return GiftMessage.from_dict(data)
        elif cmd == BiliMessageType.GUARD.value:
            return GuardMessage.from_dict(data)
        elif cmd == BiliMessageType.SUPER_CHAT.value:
            return SuperChatMessage.from_dict(data)

        return None

    def _create_normalized_message(self, bili_message: BiliBaseMessage) -> NormalizedMessage:
        """从 Bili 消息创建 NormalizedMessage"""
        if isinstance(bili_message, DanmakuMessage):
            return self._create_danmaku_message(bili_message)
        elif isinstance(bili_message, EnterMessage):
            return self._create_enter_message(bili_message)
        elif isinstance(bili_message, GiftMessage):
            return self._create_gift_message(bili_message)
        elif isinstance(bili_message, GuardMessage):
            return self._create_guard_message(bili_message)
        elif isinstance(bili_message, SuperChatMessage):
            return self._create_superchat_message(bili_message)

        # 默认消息
        return NormalizedMessage(
            text=f"[B站] 未知消息类型: {bili_message.cmd}",
            source="bili_danmaku_official_maicraft",
            data_type="unknown",
            importance=0.1,
            raw=bili_message,
        )

    def _create_danmaku_message(self, msg: DanmakuMessage) -> NormalizedMessage:
        """创建弹幕消息"""
        text = f"[弹幕] {msg.uname}: {msg.msg}"
        importance = self._calculate_danmaku_importance(msg)

        return NormalizedMessage(
            text=text,
            source="bili_danmaku_official_maicraft",
            data_type="text",
            importance=importance,
            raw=msg,
        )

    def _create_enter_message(self, msg: EnterMessage) -> NormalizedMessage:
        """创建进场消息"""
        text = f"[进场] {msg.uname} 进入了直播间"

        return NormalizedMessage(
            text=text,
            source="bili_danmaku_official_maicraft",
            data_type="enter",
            importance=0.2,
            raw=msg,
        )

    def _create_gift_message(self, msg: GiftMessage) -> NormalizedMessage:
        """创建礼物消息"""
        text = f"[礼物] {msg.uname} 送出了 {msg.gift_name} x{msg.gift_num}"
        importance = self._calculate_gift_importance(msg)

        return NormalizedMessage(
            text=text,
            source="bili_danmaku_official_maicraft",
            data_type="gift",
            importance=importance,
            raw=msg,
        )

    def _create_guard_message(self, msg: GuardMessage) -> NormalizedMessage:
        """创建大航海消息"""
        guard_level_names = {1: "总督", 2: "提督", 3: "舰长"}
        guard_name = guard_level_names.get(msg.guard_level, "大航海")
        text = f"[大航海] {msg.uname} 开通了 {guard_name}"

        # 大航海重要性较高
        importance = 0.9 if msg.guard_level == 1 else (0.8 if msg.guard_level == 2 else 0.7)

        return NormalizedMessage(
            text=text,
            source="bili_danmaku_official_maicraft",
            data_type="guard",
            importance=importance,
            raw=msg,
        )

    def _create_superchat_message(self, msg: SuperChatMessage) -> NormalizedMessage:
        """创建醒目留言消息"""
        text = f"[SC] {msg.uname} (¥{msg.rmb}): {msg.message}"

        # SC 重要性基于金额
        importance = min(0.95, 0.5 + msg.rmb / 1000)

        return NormalizedMessage(
            text=text,
            source="bili_danmaku_official_maicraft",
            data_type="super_chat",
            importance=importance,
            raw=msg,
        )

    def _calculate_danmaku_importance(self, msg: DanmakuMessage) -> float:
        """计算弹幕消息的重要性"""
        importance = 0.3  # 基础重要性

        # 粉丝牌等级加成
        if msg.fans_medal_level > 0:
            importance += min(0.2, msg.fans_medal_level / 50)

        # 大航海等级加成
        if msg.guard_level > 0:
            importance += 0.3 if msg.guard_level == 1 else (0.2 if msg.guard_level == 2 else 0.1)

        # 房管加成
        if msg.is_admin:
            importance += 0.1

        return min(1.0, importance)

    def _calculate_gift_importance(self, msg: GiftMessage) -> float:
        """计算礼物消息的重要性"""
        # 基于礼物价值计算
        total_value = msg.price * msg.gift_num

        if msg.paid:
            # 付费礼物
            importance = min(0.9, 0.4 + total_value / 1000)
        else:
            # 免费礼物
            importance = min(0.5, 0.2 + total_value / 10000)

        return importance

    async def _cleanup_internal(self):
        """清理资源"""
        # 关闭WebSocket客户端
        if self.websocket_client:
            try:
                self.logger.info("关闭WebSocket客户端...")
                await self.websocket_client.close()
                self.logger.info("WebSocket客户端已成功关闭")
            except Exception as e:
                self.logger.error(f"关闭WebSocket客户端时发生异常: {e}")
            finally:
                self.websocket_client = None

        # 关闭外发客户端
        if self.forward_client:
            self.logger.info("关闭外发 WebSocket 客户端...")
            try:
                await self.forward_client.close()
            except Exception as e:
                self.logger.warning(f"关闭外发客户端时出错: {e}")
            finally:
                self.forward_client = None

        self.logger.info("BiliDanmakuOfficialMaiCraftInputProvider 已清理")
