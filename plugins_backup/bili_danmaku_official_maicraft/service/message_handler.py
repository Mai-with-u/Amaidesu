# src/plugins/bili_danmaku_official/message_handler.py

import json
from typing import Any, Dict, List, Optional

from maim_message import MessageBase
from src.core.amaidesu_core import AmaidesuCore
from src.utils.logger import get_logger

from ..message.base import BiliBaseMessage, BiliMessageType
from ..message.danmaku import DanmakuMessage
from ..message.enter import EnterMessage
from ..message.gift import GiftMessage
from ..message.guard import GuardMessage
from ..message.superchat import SuperChatMessage
from .message_cache import MessageCacheService


class BiliMessageHandler:
    """消息处理器，负责将官方WebSocket消息转换为MessageBase对象"""

    def __init__(
        self,
        core: AmaidesuCore,
        config: Dict[str, Any],
        context_tags: Optional[List[str]],
        message_cache_service: MessageCacheService,
    ):
        self.core = core
        self.config = config
        self.context_tags = context_tags
        self.message_cache_service = message_cache_service
        self.logger = get_logger("BiliMessageHandler")

        # 消息类型配置映射
        self.message_type_config = {
            "LIVE_OPEN_PLATFORM_DM": True,  # 弹幕消息始终处理
            "LIVE_OPEN_PLATFORM_LIVE_ROOM_ENTER": self.config.get("handle_enter_messages", True),
            "LIVE_OPEN_PLATFORM_SEND_GIFT": self.config.get("handle_gift_messages", True),
            "LIVE_OPEN_PLATFORM_GUARD": self.config.get("handle_guard_messages", True),
            "LIVE_OPEN_PLATFORM_SUPER_CHAT": self.config.get("handle_superchat_messages", True),
        }

    async def create_message_base(self, message_data: Dict[str, Any]) -> Optional[MessageBase]:
        """
        根据官方WebSocket消息数据创建 MessageBase 对象

        Args:
            message_data: 从WebSocket接收到的消息数据

        Returns:
            MessageBase对象，如果无法处理则返回None
        """
        try:
            cmd = message_data.get("cmd", "")

            # 检查是否需要处理此类型消息
            if not self.message_type_config.get(cmd, False):
                self.logger.debug(f"消息类型 {cmd} 已被配置为不处理")
                return None

            # 消息解析
            bili_message = self.create_message_from_dict(message_data)
            if not bili_message:
                self.logger.debug(f"无法解析消息类型: {cmd}")
                return None

            self.logger.debug(
                f"处理bilibili直播间消息【{cmd}】: {json.dumps(message_data, ensure_ascii=False, indent=2)}"
            )

            # 调用消息类自身的to_message_base方法（已移除模板参数）
            return await bili_message.to_message_base(self.core, self.config, self.context_tags)

        except Exception as e:
            self.logger.exception(f"处理消息时发生错误: {e}", exc_info=True)
            return None

    @staticmethod
    def create_message_from_dict(data: Dict[str, Any]) -> Optional[BiliBaseMessage]:
        """从字典数据创建对应的消息对象"""
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
        else:
            return None
